"""Import von PDFs, Texten und Unterrichtsaufnahmen: Text holen, bereinigen, indexieren."""
import threading
from pathlib import Path
from typing import Callable

import numpy as np

from . import config, db, llm

CLEAN_PROMPT = """Du bekommst einen Ausschnitt aus dem automatischen Transkript einer Schullektion.
Gesprochen wird meist Hochdeutsch, manchmal kurz Schweizerdeutsch. Die Spracherkennung macht Fehler.

Deine Aufgabe:
- Gib den fachlichen Unterrichtsinhalt vollständig in sauberem Hochdeutsch wieder.
- Übersetze schweizerdeutsche Stellen ins Hochdeutsche.
- Lass Smalltalk, Pausengespräche, Durcheinanderreden bei Gruppenarbeiten und Unverständliches weg.
- Behalte Hausaufgaben, Prüfungstermine und wichtige organisatorische Hinweise.
- Korrigiere offensichtliche Erkennungsfehler, aber erfinde nichts dazu.
- Wenn der Ausschnitt keinen Unterrichtsinhalt enthält, antworte nur mit: (kein Inhalt)

Antworte nur mit dem bereinigten Text, ohne Einleitung."""

SUMMARY_PROMPT = """Du fasst Lernstoff für eine Schülerin oder einen Schüler zusammen.
Schreibe auf Deutsch eine übersichtliche Zusammenfassung in Markdown:
- ## Thema (ein Satz)
- ## Wichtigste Punkte (Stichpunkte)
- ## Begriffe (Begriff: kurze Erklärung)
- ## Hausaufgaben / Termine (nur falls erwähnt)
Verwende nur Informationen aus dem Text."""

PART_SUMMARY_PROMPT = "Fasse diesen Teil eines Lernstoffs auf Deutsch in Stichpunkten zusammen. Nur Inhalte aus dem Text."

NO_CONTENT = "(kein Inhalt)"

ProgressFn = Callable[[str], None]


# --- Text in Stücke teilen -------------------------------------------------

def split_words(text: str, max_words: int, overlap: int = 0) -> list[str]:
    words = text.split()
    if not words:
        return []
    step = max(1, max_words - overlap)
    parts = []
    for start in range(0, len(words), step):
        parts.append(" ".join(words[start:start + max_words]))
        if start + max_words >= len(words):
            break
    return parts


def split_lines(text: str, max_words: int) -> list[str]:
    """Teilt zeilenweise (z. B. Transkript-Segmente), ohne Zeilen zu zerschneiden."""
    parts, current, count = [], [], 0
    for line in text.splitlines():
        n = len(line.split())
        if current and count + n > max_words:
            parts.append("\n".join(current))
            current, count = [], 0
        current.append(line)
        count += n
    if current:
        parts.append("\n".join(current))
    return [p for p in parts if p.strip()]


# --- Quellen lesen ---------------------------------------------------------

MIN_PAGE_WORDS = 25  # weniger Text auf einer Seite = vermutlich Bild, Scan oder Handschrift


class PdfText(str):
    """Text eines PDFs; images_missing zählt Bildseiten, die die KI (noch) nicht lesen konnte."""
    images_missing = 0


def extract_pdf_text(path: Path, progress: ProgressFn = lambda _: None, read_images: bool = False) -> PdfText:
    """Liest den Text eines PDFs. Seiten fast ohne Text werden auf Wunsch von der KI abgeschrieben.

    Abgeschriebene Seiten werden gespeichert. Ist das Tageslimit erreicht, wird der Rest
    übersprungen und beim nächsten Mal dort weitergemacht.
    """
    import hashlib

    import pymupdf

    pages, missing, limit_hit = [], 0, False
    model = llm.vision_model() if read_images else ""
    with pymupdf.open(path) as pdf:
        for i, page in enumerate(pdf, start=1):
            text = page.get_text().strip()
            if read_images and len(text.split()) < MIN_PAGE_WORDS and _has_visual_content(page):
                png = page.get_pixmap(dpi=130).tobytes("png")
                key = hashlib.sha1(png).hexdigest() + ":" + model
                seen = db.cached_page(key)
                if seen is None and not limit_hit:
                    progress(f"Erkenne Bild/Handschrift auf Seite {i} von {pdf.page_count}…")
                    seen = _read_page(png)
                    if seen is LIMIT:
                        limit_hit, seen = True, None
                    elif seen is not None:
                        db.cache_page(key, seen)
                if seen is None:
                    missing += 1
                else:
                    text = (text + "\n" + seen).strip()
            if text:
                pages.append(f"--- Seite {i} ---\n{text}")
    result = PdfText("\n\n".join(pages))
    result.images_missing = missing
    return result


LIMIT = object()


def _image_budget_left() -> bool:
    """Zählt die Bild-Anfragen pro Tag (UTC, wie OpenRouter) und hält sich an IMAGE_DAILY_BUDGET."""
    import datetime

    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    day, _, count = db.get_setting("image_requests", "").partition(":")
    used = int(count) if day == today and count.isdigit() else 0
    if used >= config.IMAGE_DAILY_BUDGET:
        return False
    db.set_setting("image_requests", f"{today}:{used + 1}")
    return True


def _read_page(png: bytes):
    """Eine Bildseite abschreiben. None = ging nicht (später nochmals), LIMIT = Tageslimit erreicht."""
    for _attempt in range(2):
        if not _image_budget_left():
            return LIMIT  # Rest des Tageslimits bleibt fürs Lernen frei
        try:
            return llm.read_image(png)
        except llm.LimitError:
            return LIMIT
        except llm.LLMError:
            continue  # z. B. Zeitüberschreitung: einmal nochmals versuchen
    return None


def _has_visual_content(page) -> bool:
    try:
        return bool(page.get_images()) or len(page.get_drawings()) > 3
    except Exception:
        return True


_whisper_model = None
_whisper_lock = threading.Lock()


def _get_whisper():
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is None:
            from faster_whisper import WhisperModel

            device = config.WHISPER_DEVICE
            if device == "auto":
                try:
                    import ctranslate2
                    device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
                except Exception:
                    device = "cpu"
            compute = "float16" if device == "cuda" else "int8"
            _whisper_model = WhisperModel(config.WHISPER_MODEL, device=device, compute_type=compute)
        return _whisper_model


def fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


SAMPLE_RATE = 16000  # was Whisper erwartet


def load_audio(path: Path) -> np.ndarray:
    """Liest eine Aufnahme als 16-kHz-Mono für Whisper.

    Eigene Version statt faster_whisper.decode_audio, weil diese PyAV ein Argument
    (metadata_errors) übergibt, das neuere PyAV-Versionen nicht mehr kennen.
    """
    import av

    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    parts = []
    with av.open(str(path), mode="r") as container:
        if not container.streams.audio:
            raise ValueError("Die Datei enthält keine Tonspur.")
        for frame in container.decode(audio=0):
            frame.pts = None  # Browser-Aufnahmen (webm) haben lückenhafte Zeitstempel
            for out in resampler.resample(frame):
                parts.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            parts.append(out.to_ndarray().reshape(-1))
    if not parts:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(parts).astype(np.float32) / 32768.0


def transcribe(path: Path, progress: ProgressFn) -> str:
    progress("Lade Spracherkennung (beim ersten Mal wird das Modell heruntergeladen)…")
    model = _get_whisper()
    progress("Lese Aufnahme…")
    audio = load_audio(path)
    segments, info = model.transcribe(
        audio,
        language=config.WHISPER_LANGUAGE,
        vad_filter=True,  # überspringt Stille und Pausen
        vad_parameters={"min_silence_duration_ms": 1000},
        beam_size=5,
    )
    lines = []
    total = info.duration or 0
    for seg in segments:
        lines.append(f"[{fmt_time(seg.start)}] {seg.text.strip()}")
        if total:
            progress(f"Transkribiere… {min(100, int(seg.end / total * 100))} %")
    return "\n".join(lines)


# --- KI-Verarbeitung -------------------------------------------------------

def clean_transcript(raw: str, progress: ProgressFn) -> str:
    parts = split_lines(raw, max_words=900)
    cleaned = []
    for i, part in enumerate(parts, start=1):
        progress(f"Bereinige Transkript… Teil {i}/{len(parts)}")
        text = llm.ask(CLEAN_PROMPT, part)
        if text and NO_CONTENT not in text[:40]:
            cleaned.append(text)
    return "\n\n".join(cleaned)


def summarize(text: str, progress: ProgressFn = lambda _: None) -> str:
    parts = split_words(text, max_words=2500)
    if not parts:
        return ""
    if len(parts) > 1:
        notes = []
        for i, part in enumerate(parts, start=1):
            progress(f"Fasse zusammen… Teil {i}/{len(parts)}")
            notes.append(llm.ask(PART_SUMMARY_PROMPT, part))
        text = "\n\n".join(notes)
    progress("Schreibe Zusammenfassung…")
    return llm.ask(SUMMARY_PROMPT, text)


def index(doc_id: int, text: str, progress: ProgressFn) -> None:
    chunks = split_words(text, max_words=220, overlap=40)
    if not chunks:
        db.replace_chunks(doc_id, [])
        return
    progress(f"Mache durchsuchbar… ({len(chunks)} Abschnitte)")
    vectors = llm.embed(chunks)
    db.replace_chunks(doc_id, [(c, np.asarray(v, dtype=np.float32).tobytes())
                               for c, v in zip(chunks, vectors)])


def reindex(doc_id: int) -> None:
    doc = db.get_document(doc_id)
    if doc and doc["status"] == "ready":
        index(doc_id, doc["clean_text"] or doc["raw_text"], lambda _msg: None)


def process(doc_id: int) -> None:
    doc = db.get_document(doc_id)
    if not doc:
        return

    def progress(msg: str) -> None:
        db.update_document(doc_id, progress=msg)

    db.update_document(doc_id, status="processing", error="")
    try:
        if doc["kind"] == "pdf":
            progress("Lese PDF…")
            raw = extract_pdf_text(Path(doc["source_path"]), progress, read_images=bool(llm.vision_model()))
            if not raw.strip():
                if raw.images_missing:
                    raise llm.LimitError("Die Seiten sind Bilder oder Handschrift, und die KI konnte sie noch nicht lesen "
                                         "(Tageslimit). Wird automatisch später nochmals versucht.")
                raise ValueError("Im PDF wurde kein Text gefunden (evtl. nur Bilder oder Handschrift).")
            db.update_document(doc_id, raw_text=str(raw), clean_text=str(raw), images_missing=raw.images_missing)
            index(doc_id, raw, progress)
        elif doc["kind"] == "text":
            db.update_document(doc_id, clean_text=doc["raw_text"])
            index(doc_id, doc["raw_text"], progress)
        elif doc["kind"] == "lesson":
            raw = doc["raw_text"] or transcribe(Path(doc["source_path"]), progress)
            db.update_document(doc_id, raw_text=raw)
            if not raw.strip():
                raise ValueError("In der Aufnahme wurde keine Sprache erkannt.")
            clean = clean_transcript(raw, progress)
            db.update_document(doc_id, clean_text=clean)
            db.update_document(doc_id, summary=summarize(clean, progress))
            index(doc_id, clean, progress)
            if clean.strip():
                progress("Erstelle Karteikarten…")
                try:
                    from . import study
                    study.make_flashcards(db.get_document(doc_id), n=8)
                except llm.LLMError:
                    pass  # Karteikarten sind ein Bonus; die Lektion ist trotzdem fertig
        else:
            raise ValueError(f"Unbekannte Art: {doc['kind']}")
        db.update_document(doc_id, status="ready", progress="")
    except Exception as e:  # Fehler wird in der App angezeigt
        db.update_document(doc_id, status="error", progress="", error=str(e))
