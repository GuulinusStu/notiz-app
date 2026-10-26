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

def extract_pdf_text(path: Path) -> str:
    import pymupdf

    pages = []
    with pymupdf.open(path) as pdf:
        for i, page in enumerate(pdf, start=1):
            text = page.get_text().strip()
            if text:
                pages.append(f"--- Seite {i} ---\n{text}")
    return "\n\n".join(pages)


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


def transcribe(path: Path, progress: ProgressFn) -> str:
    progress("Lade Spracherkennung (beim ersten Mal wird das Modell heruntergeladen)…")
    model = _get_whisper()
    segments, info = model.transcribe(
        str(path),
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
            raw = extract_pdf_text(Path(doc["source_path"]))
            if not raw.strip():
                raise ValueError("Im PDF wurde kein Text gefunden (evtl. nur Bilder oder Handschrift).")
            db.update_document(doc_id, raw_text=raw, clean_text=raw)
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
        else:
            raise ValueError(f"Unbekannte Art: {doc['kind']}")
        db.update_document(doc_id, status="ready", progress="")
    except Exception as e:  # Fehler wird in der App angezeigt
        db.update_document(doc_id, status="error", progress="", error=str(e))
