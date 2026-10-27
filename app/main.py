"""Web-Server der Notiz-App. Läuft nur lokal auf dem eigenen Laptop."""
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, exams, ingest, llm, onenote, srs, study, worker

PDF_TYPES = {".pdf"}
AUDIO_TYPES = {".webm", ".ogg", ".mp3", ".m4a", ".wav", ".mp4", ".aac", ".flac", ".opus"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    worker.start()
    if onenote.available() and config.ONENOTE_AUTO_SYNC:
        onenote.start_sync()
    yield


app = FastAPI(title="Notiz-App", lifespan=lifespan)


@app.exception_handler(llm.LLMError)
async def llm_error(_request, exc: llm.LLMError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})


def save_upload(upload: UploadFile, allowed: set[str]) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(400, f"Dateityp {suffix or '(ohne Endung)'} wird nicht unterstützt.")
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    target = config.UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    with target.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    return target


def require_doc(doc_id: int) -> dict:
    doc = db.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Notiz nicht gefunden.")
    return doc


# --- Status ------------------------------------------------------------------

@app.get("/api/status")
def status():
    return {**llm.status(), "flashcards": db.flashcard_counts()}


# --- Dokumente ---------------------------------------------------------------

@app.get("/api/documents")
def list_documents():
    return db.list_documents()


@app.get("/api/documents/{doc_id}")
def get_document(doc_id: int):
    return require_doc(doc_id)


class DocUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    subject: str | None = Field(default=None, max_length=100)


@app.patch("/api/documents/{doc_id}")
def update_document(doc_id: int, upd: DocUpdate):
    require_doc(doc_id)
    fields = {k: v.strip() for k, v in upd.model_dump().items() if v is not None}
    if "title" in fields:
        fields["title_custom"] = 1
    db.update_document(doc_id, **fields)
    return db.get_document(doc_id)


@app.get("/api/documents/{doc_id}/file")
def document_file(doc_id: int):
    doc = require_doc(doc_id)
    path = Path(doc["source_path"] or "")
    if not doc["source_path"] or not path.is_file():
        raise HTTPException(404, "Zu dieser Notiz gibt es keine Datei.")
    return FileResponse(path, filename=f"{doc['title']}{path.suffix}".replace("/", "-"),
                        content_disposition_type="inline")


# --- Fächer ------------------------------------------------------------------

@app.get("/api/subjects")
def subjects():
    return db.list_subjects()


class SubjectRename(BaseModel):
    old: str
    new: str = Field(min_length=1, max_length=100)


class SubjectNew(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class SubjectOrder(BaseModel):
    order: list[str]


class MoveIn(BaseModel):
    ids: list[int] = Field(min_length=1)
    subject: str = Field(max_length=100)


@app.post("/api/subjects")
def add_subject(s: SubjectNew):
    db.add_subject(s.name.strip())
    return db.list_subjects()


@app.post("/api/subjects/order")
def subject_order(s: SubjectOrder):
    db.set_subject_order(s.order)
    return db.list_subjects()


@app.delete("/api/subjects")
def remove_subject(name: str):
    if not db.remove_subject(name):
        raise HTTPException(400, "Das Fach ist nicht leer. Verschiebe zuerst die Notizen und Prüfungen in ein anderes Fach.")
    return db.list_subjects()


@app.post("/api/documents/move")
def move_documents(m: MoveIn):
    return {"moved": db.move_documents(m.ids, m.subject.strip())}


@app.post("/api/subjects/rename")
def rename_subject(r: SubjectRename):
    return {"changed": db.rename_subject(r.old, r.new.strip())}


# --- Einstellungen -----------------------------------------------------------

class SettingsIn(BaseModel):
    openrouter_key: str | None = None
    chat_model: str | None = None
    vision_model: str | None = None


@app.get("/api/settings")
def get_settings():
    key = llm.api_key()
    return {"key_set": bool(key), "key_hint": f"…{key[-4:]}" if len(key) > 8 else "",
            "chat_model": llm.chat_model(), "vision_model": llm.vision_model()}


@app.post("/api/settings")
def save_settings(s: SettingsIn):
    if s.openrouter_key is not None:
        db.set_setting("openrouter_key", s.openrouter_key.strip())
    if s.chat_model is not None:
        db.set_setting("chat_model", s.chat_model.strip())
    if s.vision_model is not None:
        db.set_setting("vision_model", s.vision_model.strip())
    return get_settings()


@app.get("/api/models")
def models():
    found = llm.list_models()
    return {"models": found, "suggested": llm.pick_default_model(found),
            "suggested_vision": llm.pick_default_model(found, vision=True)}


@app.post("/api/settings/test")
def test_settings():
    reply = llm.ask("Antworte kurz auf Deutsch.", "Sag in einem Satz Hallo.")
    return {"reply": reply}


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: int):
    doc = require_doc(doc_id)
    db.delete_document(doc_id)
    if doc["source_path"]:
        Path(doc["source_path"]).unlink(missing_ok=True)
    return {"ok": True}


@app.post("/api/documents/{doc_id}/retry")
def retry_document(doc_id: int):
    require_doc(doc_id)
    db.update_document(doc_id, status="pending", error="")
    worker.enqueue(doc_id)
    return {"ok": True}


class Reprocess(BaseModel):
    only_errors: bool = False


@app.post("/api/documents/reprocess")
def reprocess(r: Reprocess):
    """PDFs neu einlesen (z. B. nachdem ein Bild-Modell gewählt wurde) oder Fehlgeschlagene nochmals versuchen."""
    ids = [d["id"] for d in db.list_documents()
           if (d["status"] == "error" if r.only_errors else d["kind"] == "pdf" and d["status"] in ("ready", "error"))]
    for doc_id in ids:
        db.update_document(doc_id, status="pending", error="")
        worker.enqueue(doc_id)
    return {"queued": len(ids)}


@app.post("/api/upload/pdf")
def upload_pdf(files: list[UploadFile] = File(...), subject: str = Form("")):
    ids = []
    for upload in files:
        path = save_upload(upload, PDF_TYPES)
        title = Path(upload.filename).stem
        doc_id = db.create_document(title, "pdf", subject.strip(), str(path))
        worker.enqueue(doc_id)
        ids.append(doc_id)
    return {"ids": ids}


@app.post("/api/upload/audio")
def upload_audio(file: UploadFile = File(...), title: str = Form(""), subject: str = Form("")):
    path = save_upload(file, AUDIO_TYPES)
    title = title.strip() or Path(file.filename).stem
    doc_id = db.create_document(title, "lesson", subject.strip(), str(path))
    worker.enqueue(doc_id)
    return {"id": doc_id}


@app.get("/api/onenote")
def onenote_status():
    return onenote.status()


class SyncIn(BaseModel):
    force: bool = False  # alle Abschnitte neu exportieren, nicht nur geänderte


@app.post("/api/onenote/sync")
def onenote_sync(body: SyncIn | None = None):
    if not onenote.available():
        raise HTTPException(400, "Der automatische OneNote-Export funktioniert nur unter Windows.")
    onenote.start_sync(force=bool(body and body.force))
    return onenote.status()


class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)
    subject: str = ""


@app.post("/api/notes")
def create_note(note: NoteIn):
    doc_id = db.create_document(note.title.strip(), "text", note.subject.strip(), raw_text=note.text)
    worker.enqueue(doc_id)
    return {"id": doc_id}


# --- Lernen ------------------------------------------------------------------

class Question(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    subject: str | None = None
    history: list[dict] = Field(default_factory=list, max_length=10)


@app.post("/api/ask")
def ask(q: Question):
    return study.answer(q.question.strip(), subject=q.subject, history=q.history)


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    subject: str | None = None


@app.post("/api/search")
def search(s: SearchIn):
    return study.find(s.query.strip(), subject=s.subject)


@app.get("/api/stats")
def stats(subject: str | None = None):
    return db.stats(subject)


class QuizResult(BaseModel):
    subject: str | None = None
    score: int = Field(ge=0)
    total: int = Field(ge=1)


@app.post("/api/quiz/result")
def quiz_result(r: QuizResult):
    db.log_quiz(r.subject or "", min(r.score, r.total), r.total)
    return {"ok": True}


class SubjectIn(BaseModel):
    subject: str


# --- Prüfungen ---------------------------------------------------------------

class ExamIn(BaseModel):
    subject: str = ""
    title: str = Field(min_length=1, max_length=200)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    goals_text: str = ""


class ExamUpdate(BaseModel):
    subject: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    goals_text: str | None = None
    done: list[bool] | None = None  # abgehakte Lernziele, in derselben Reihenfolge


def check_date(date: str) -> str:
    import datetime
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "Ungültiges Datum.")
    return date


def require_exam(exam_id: int) -> dict:
    exam = db.get_exam(exam_id)
    if not exam:
        raise HTTPException(404, "Prüfung nicht gefunden.")
    return exam


@app.get("/api/exams")
def list_exams(subject: str | None = None):
    return [exams.summary(e) for e in db.list_exams(subject)]


@app.post("/api/exams")
def create_exam(e: ExamIn):
    goals = exams.merge_goals([], exams.parse_goals(e.goals_text))
    exam_id = db.create_exam(e.subject.strip(), e.title.strip(), check_date(e.date), goals)
    return exams.summary(db.get_exam(exam_id))


@app.get("/api/exams.ics")
def exams_ics():
    return Response(exams.ics(db.list_exams()), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="pruefungen.ics"'})


@app.get("/api/exams/{exam_id}")
def get_exam(exam_id: int):
    exam = require_exam(exam_id)
    try:
        goals = exams.materials(exam)
    except llm.LLMError:
        goals = [{**g, "docs": []} for g in exam["goals"]]
    return {**exams.summary(exam), "goals": goals, "plan": exams.plan(exam)}


@app.patch("/api/exams/{exam_id}")
def update_exam(exam_id: int, u: ExamUpdate):
    exam = require_exam(exam_id)
    fields = {k: v.strip() for k, v in (("subject", u.subject), ("title", u.title)) if v is not None}
    if u.date is not None:
        fields["date"] = check_date(u.date)
    goals = exam["goals"]
    if u.goals_text is not None:
        goals = exams.merge_goals(goals, exams.parse_goals(u.goals_text))
    if u.done is not None:
        goals = [{**g, "done": bool(d)} for g, d in zip(goals, u.done)] + goals[len(u.done):]
    fields["goals"] = goals
    db.update_exam(exam_id, **fields)
    return exams.summary(db.get_exam(exam_id))


@app.delete("/api/exams/{exam_id}")
def delete_exam(exam_id: int):
    require_exam(exam_id)
    db.delete_exam(exam_id)
    return {"ok": True}


@app.post("/api/exams/{exam_id}/quiz")
def exam_quiz(exam_id: int, n: int = 8, only_open: bool = True):
    exam = require_exam(exam_id)
    return {"fragen": study.make_quiz(n=max(1, min(n, 15)), excerpts=exams.excerpts(exam, only_open))}


@app.post("/api/exams/{exam_id}/flashcards")
def exam_flashcards(exam_id: int, n: int = 15):
    exam = require_exam(exam_id)
    return {"created": study.make_flashcards(n=max(1, min(n, 30)), excerpts=exams.excerpts(exam))}


@app.post("/api/exams/goals-from-file")
def goals_from_file(file: UploadFile = File(...)):
    """Lernziele aus einem PDF (z. B. Lernzielblatt) übernehmen. Nur Text, ohne KI."""
    import pymupdf
    data = file.file.read()
    try:
        with pymupdf.open(stream=data, filetype="pdf") as pdf:
            text = "\n".join(page.get_text() for page in pdf)
    except Exception:
        raise HTTPException(400, "Die Datei konnte nicht gelesen werden. Bitte ein PDF wählen.")
    goals = exams.parse_goals(text)
    if not goals:
        raise HTTPException(400, "Im PDF wurde kein Text gefunden. Tippe die Lernziele bitte ab.")
    return {"goals_text": "\n".join(goals)}


@app.post("/api/subjects/quiz")
def subject_quiz(s: SubjectIn, n: int = 8):
    return {"fragen": study.make_quiz(subject=s.subject, n=max(1, min(n, 15)))}


@app.post("/api/subjects/flashcards")
def subject_flashcards(s: SubjectIn, n: int = 15):
    return {"created": study.make_flashcards(subject=s.subject, n=max(1, min(n, 30)))}


@app.post("/api/documents/{doc_id}/summary")
def summarize(doc_id: int):
    doc = require_doc(doc_id)
    summary = ingest.summarize(doc["clean_text"] or doc["raw_text"])
    db.update_document(doc_id, summary=summary)
    return {"summary": summary}


@app.post("/api/documents/{doc_id}/quiz")
def quiz(doc_id: int, n: int = 5):
    return {"fragen": study.make_quiz(require_doc(doc_id), n=max(1, min(n, 15)))}


@app.post("/api/documents/{doc_id}/flashcards")
def flashcards(doc_id: int, n: int = 10):
    return {"created": study.make_flashcards(require_doc(doc_id), n=max(1, min(n, 30)))}


@app.get("/api/flashcards")
def all_cards(subject: str | None = None):
    return db.list_flashcards(subject)


@app.get("/api/flashcards/export.csv")
def export_cards(subject: str | None = None):
    """Karteikarten als CSV (Tabulator-getrennt), z. B. für den Import in Anki."""
    def clean(text: str) -> str:
        return " ".join(text.split())
    lines = [f"{clean(c['front'])}\t{clean(c['back'])}\t{clean(c['subject'] or '')}" for c in db.list_flashcards(subject)]
    return Response("\n".join(lines) + "\n", media_type="text/tab-separated-values; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="karteikarten.txt"'})


class CardEdit(BaseModel):
    front: str = Field(min_length=1, max_length=2000)
    back: str = Field(min_length=1, max_length=4000)


@app.patch("/api/flashcards/{card_id}")
def edit_card(card_id: int, c: CardEdit):
    if not db.get_flashcard(card_id):
        raise HTTPException(404, "Karte nicht gefunden.")
    db.edit_flashcard(card_id, c.front.strip(), c.back.strip())
    return {"ok": True}


@app.delete("/api/flashcards/{card_id}")
def delete_card(card_id: int):
    db.delete_flashcard(card_id)
    return {"ok": True}


@app.get("/api/flashcards/due")
def due_cards(subject: str | None = None):
    return db.due_flashcards(subject=subject)


class Review(BaseModel):
    grade: int = Field(ge=0, le=3)


@app.post("/api/flashcards/{card_id}/review")
def review(card_id: int, r: Review):
    card = db.get_flashcard(card_id)
    if not card:
        raise HTTPException(404, "Karte nicht gefunden.")
    new = srs.schedule(card, r.grade)
    db.update_flashcard(card_id, **new)
    doc = db.get_document(card["doc_id"]) if card["doc_id"] else None
    db.log_review(card_id, doc["subject"] if doc else "", r.grade)
    return new


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
