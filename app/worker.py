"""Hintergrund-Warteschlange: verarbeitet Importe nacheinander, damit der Laptop nicht überlastet wird."""
import queue
import threading

from . import db, ingest

_jobs: "queue.Queue[int]" = queue.Queue()
_started = False


def enqueue(doc_id: int) -> None:
    _jobs.put(doc_id)


def _run() -> None:
    while True:
        doc_id = _jobs.get()
        try:
            ingest.process(doc_id)
        finally:
            _jobs.task_done()


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_run, name="notizapp-worker", daemon=True).start()
    # Was beim letzten Mal nicht fertig wurde, wird erneut verarbeitet.
    for doc_id in db.documents_with_status("pending", "processing"):
        enqueue(doc_id)
