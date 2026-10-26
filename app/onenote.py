"""Automatischer Export aus der OneNote-App am PC (Windows).

Jeder Abschnitt wird als PDF exportiert und importiert. Beim nächsten Mal werden
nur Abschnitte neu exportiert, die sich seither geändert haben.
"""
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import config, db, worker

SCRIPT = config.ROOT / "scripts" / "onenote.ps1"
PREFIX = "onenote:"

_lock = threading.Lock()
_state = {"running": False, "message": "", "result": None, "error": ""}


class OneNoteError(RuntimeError):
    pass


class EmptySection(OneNoteError):
    pass


class IncompleteSection(OneNoteError):
    """OneNote hat noch nicht alle Seiten heruntergeladen. Wird beim nächsten Mal nochmals versucht."""


EMPTY_EXIT_CODE = 3
INCOMPLETE_EXIT_CODE = 4
RETRY_DELAY = 10  # Sekunden; OneNote startet manchmal kurz neu
RESYNC_DELAY = 300  # Sekunden bis zum nächsten Versuch, wenn Abschnitte noch nicht fertig geladen waren
RESYNC_MAX = 3


def available() -> bool:
    return sys.platform == "win32"


def _run(*args: str, timeout: float = 300) -> str:
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "RemoteSigned",
           "-File", str(SCRIPT), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise OneNoteError(f"OneNote-Export fehlgeschlagen: {e}") from e
    lines = (proc.stderr or proc.stdout or "").strip().splitlines()
    if proc.returncode == EMPTY_EXIT_CODE:
        raise EmptySection("Abschnitt ist leer")
    if proc.returncode == INCOMPLETE_EXIT_CODE:
        raise IncompleteSection(lines[0] if lines else "Noch nicht vollständig heruntergeladen")
    if proc.returncode != 0:
        raise OneNoteError(lines[0] if lines else f"OneNote-Export fehlgeschlagen (Code {proc.returncode})")
    return proc.stdout or ""


def list_sections() -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "sections.json"
        _run("-Action", "list", "-OutFile", str(out))
        data = json.loads(out.read_text(encoding="utf-8-sig") or "[]")
    return data if isinstance(data, list) else [data]


def export_section(section_id: str, target: Path) -> None:
    """Exportiert in eine Zwischendatei und ersetzt die alte PDF erst, wenn die neue vollständig ist."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.stem + ".neu.pdf")
    try:
        _run("-Action", "export", "-SectionId", section_id, "-OutFile", str(tmp))
        check_pdf(tmp)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)


def check_pdf(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise IncompleteSection("OneNote hat keine PDF-Datei erstellt.")
    import pymupdf
    try:
        with pymupdf.open(path) as pdf:
            pages = pdf.page_count
    except Exception as e:
        raise IncompleteSection(f"Die exportierte PDF ist beschädigt: {e}") from e
    if pages == 0:
        raise IncompleteSection("Die exportierte PDF hat keine Seiten.")


def pdf_name(section: dict) -> str:
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", section["path"]).strip(" ._")[:80] or "abschnitt"
    short = hashlib.sha1(section["id"].encode()).hexdigest()[:8]
    return f"{safe}-{short}.pdf"


def sync(progress=lambda _msg: None, *, lister=None, exporter=None, force: bool = False) -> dict:
    """Exportiert neue und geänderte Abschnitte (mit force alle) und stellt sie zur Verarbeitung an."""
    lister = lister or list_sections
    exporter = exporter or export_section
    progress("Lese Notizbücher aus OneNote…")
    sections = lister()
    known = db.documents_by_external_prefix(PREFIX)
    export_dir = config.DATA_DIR / "onenote"
    empty = db.sync_states(PREFIX)
    result = {"sections": len(sections), "new": 0, "updated": 0, "unchanged": 0, "empty": 0, "incomplete": [], "failed": []}

    for i, section in enumerate(sections, start=1):
        ext_id = PREFIX + section["id"]
        doc = known.get(ext_id)
        if not force and doc and doc["external_modified"] == section["lastModified"]:
            result["unchanged"] += 1
            continue
        if not force and not doc and empty.get(ext_id) == section["lastModified"]:
            result["empty"] += 1
            continue
        progress(f"Exportiere {i}/{len(sections)}: {section['path']}")
        target = export_dir / pdf_name(section)
        try:
            _export_with_retry(exporter, section["id"], target)
        except EmptySection:
            db.set_sync_state(ext_id, section["lastModified"])
            result["empty"] += 1
            continue
        except IncompleteSection as e:
            # Nichts speichern, damit der Abschnitt beim nächsten Mal sicher nochmals drankommt
            result["incomplete"].append({"path": section["path"], "error": str(e)})
            continue
        except OneNoteError as e:
            result["failed"].append({"path": section["path"], "error": str(e)})
            continue
        if doc:
            db.update_document(doc["id"], title=section["path"], source_path=str(target), summary="",
                               status="pending", error="", external_modified=section["lastModified"])
            worker.enqueue(doc["id"])
            result["updated"] += 1
        else:
            subject = db.subject_for(section.get("notebook", ""))
            doc_id = db.create_document(section["path"], "pdf", subject, str(target),
                                        external_id=ext_id, external_modified=section["lastModified"])
            worker.enqueue(doc_id)
            result["new"] += 1
    return result


def _export_with_retry(exporter, section_id: str, target: Path) -> None:
    try:
        exporter(section_id, target)
    except (EmptySection, IncompleteSection):
        raise
    except OneNoteError:
        time.sleep(RETRY_DELAY)
        exporter(section_id, target)


def status() -> dict:
    return {"available": available(), **_state}


def start_sync(attempt: int = 1, force: bool = False) -> bool:
    """Startet die Synchronisierung im Hintergrund. False, wenn sie schon läuft."""
    if not _lock.acquire(blocking=False):
        return False
    _state.update(running=True, message="Starte…", error="")

    def run():
        result = None
        try:
            result = _state["result"] = sync(lambda msg: _state.update(message=msg), force=force)
        except Exception as e:  # wird in der App angezeigt
            _state["error"] = str(e)
        finally:
            _state.update(running=False, message="")
            _lock.release()
        # Abschnitte, die OneNote noch nicht fertig geladen hatte, später nochmals holen
        if result and result["incomplete"] and attempt < RESYNC_MAX:
            timer = threading.Timer(RESYNC_DELAY, start_sync, args=(attempt + 1,))
            timer.daemon = True
            timer.start()

    threading.Thread(target=run, name="onenote-sync", daemon=True).start()
    return True
