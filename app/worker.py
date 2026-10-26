"""Hintergrund-Warteschlange: verarbeitet Importe nacheinander, damit der Laptop nicht überlastet wird."""
import queue
import threading
import time

from . import config, db, ingest

_jobs: "queue.Queue[tuple[str, object]]" = queue.Queue()
_started = False


def enqueue(doc_id: int) -> None:
    _jobs.put(("process", doc_id))


def enqueue_reindex(doc_id: int) -> None:
    _jobs.put(("reindex", doc_id))


def _run() -> None:
    while True:
        kind, arg = _jobs.get()
        try:
            if kind == "process":
                ingest.process(arg)
            elif kind == "reindex":
                ingest.reindex(arg)
            elif kind == "indexed":
                db.set_setting("indexed_with", arg)
        except Exception as e:  # der Worker darf nie sterben, sonst bleibt alles in der Warteschlange
            print(f"Fehler bei Auftrag {kind} {arg}: {e}")
        finally:
            _jobs.task_done()


RETRY_EVERY = 2 * 3600  # Sekunden; so oft werden ungelesene Bildseiten nochmals versucht


def retry_unread_images() -> int:
    """Stellt Notizen an, bei denen Bildseiten wegen des Tageslimits noch fehlen."""
    ids = db.documents_with_missing_images()
    for doc_id in db.documents_with_status("error"):
        doc = db.get_document(doc_id)
        if "Limit" in doc["error"] or "Guthaben" in doc["error"]:
            db.update_document(doc_id, status="pending")
            ids.append(doc_id)
    for doc_id in ids:
        enqueue(doc_id)
    return len(ids)


def _retry_loop() -> None:
    time.sleep(120)
    while True:
        if _jobs.empty():
            try:
                retry_unread_images()
            except Exception as e:
                print(f"Fehler beim erneuten Versuch: {e}")
        time.sleep(RETRY_EVERY)


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_run, name="notizapp-worker", daemon=True).start()
    if config.AUTO_RETRY:
        threading.Thread(target=_retry_loop, name="notizapp-retry", daemon=True).start()
    # Was beim letzten Mal nicht fertig wurde, wird erneut verarbeitet.
    for doc_id in db.documents_with_status("pending", "processing"):
        enqueue(doc_id)
    # Neues Suchmodell: bestehende Notizen neu durchsuchbar machen (ohne die KI erneut zu fragen).
    if db.get_setting("indexed_with") != config.EMBED_MODEL:
        for doc_id in db.ready_documents():
            enqueue_reindex(doc_id)
        _jobs.put(("indexed", config.EMBED_MODEL))
