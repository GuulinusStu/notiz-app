"""Web-Server der Notiz-App. Läuft nur lokal auf dem eigenen Laptop."""
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, ingest, llm, srs, study, worker

PDF_TYPES = {".pdf"}
AUDIO_TYPES = {".webm", ".ogg", ".mp3", ".m4a", ".wav", ".mp4", ".aac", ".flac", ".opus"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    worker.start()
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


@app.post("/api/ask")
def ask(q: Question):
    return study.answer(q.question.strip())


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


@app.get("/api/flashcards/due")
def due_cards():
    return db.due_flashcards()


class Review(BaseModel):
    grade: int = Field(ge=0, le=3)


@app.post("/api/flashcards/{card_id}/review")
def review(card_id: int, r: Review):
    card = db.get_flashcard(card_id)
    if not card:
        raise HTTPException(404, "Karte nicht gefunden.")
    new = srs.schedule(card, r.grade)
    db.update_flashcard(card_id, **new)
    return new


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
