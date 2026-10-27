"""Aufnahmen direkt auf dem Laptop, ohne Browser, damit der Stundenplan Lektionen von selbst aufnehmen kann.

Das Mikrofon wird mit 48 kHz gelesen und laufend als Opus (.ogg) gespeichert: gut verständliche Sprache,
etwa 15 MB pro Stunde und kaum Rechenlast. Weil die Datei laufend geschrieben wird, bleibt bei einem
Absturz alles bis dahin Aufgenommene erhalten und wird beim nächsten Start verarbeitet.
"""
import json
import queue
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta

from . import config, db, worker

RATE = 48000
BITRATE = 32000
SILENCE_PEAK = 30  # lauter als das wird jedes eingeschaltete Mikrofon in 10 s einmal
CHECK_EVERY = 20  # Sekunden zwischen zwei Blicken auf den Stundenplan
MIN_MINUTES_LEFT = 5  # später als 5 Minuten vor Schluss lohnt sich das Starten nicht mehr
LEAD = timedelta(minutes=1)  # etwas früher beginnen und später aufhören, damit nichts fehlt
TAIL = timedelta(minutes=2)
DAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

_lock = threading.Lock()
_current: "Recording | None" = None
_last_error = ""


def _state_file():
    return config.DATA_DIR / "aufnahme-laeuft.json"


def microphone_source(callback):
    """Mikrofon über sounddevice; in den Tests wird eine Attrappe eingesetzt."""
    import sounddevice as sd

    return sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=RATE // 10,
                          callback=lambda data, frames, t, status: callback(data[:, 0].copy()))


source_factory = microphone_source


def _keep_awake(on: bool) -> None:
    """Verhindert, dass Windows während der Aufnahme in den Energiesparmodus geht."""
    if sys.platform == "win32":
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


class Recording:
    def __init__(self, subject: str, title: str, until: datetime | None = None, entry_id: str = ""):
        self.subject, self.title, self.until, self.entry_id = subject, title, until, entry_id
        self.started = time.time()
        self.marks: list[int] = []
        self.path = config.UPLOAD_DIR / f"{uuid.uuid4().hex}.ogg"
        self._audio: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.error = ""
        self.silent = False  # nach 10 s nur Stille: Mikrofon stumm oder falsches Gerät
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self._save_state()
        self._thread.start()
        self._ready.wait(10)
        if self.error:
            raise RuntimeError(self.error)

    def _save_state(self) -> None:
        _state_file().write_text(json.dumps({
            "path": str(self.path), "subject": self.subject, "title": self.title,
            "started": self.started, "marks": self.marks}), encoding="utf-8")

    def _run(self) -> None:
        import av
        import numpy as np

        _keep_awake(True)
        container = None
        try:
            container = av.open(str(self.path), "w", format="ogg")
            stream = container.add_stream("libopus", rate=RATE)
            stream.layout = "mono"
            stream.bit_rate = BITRATE
            mic = source_factory(self._audio.put)
            mic.start()
        except Exception as e:  # kein Mikrofon, Treiber fehlt …
            self.error = f"Das Mikrofon lässt sich nicht öffnen: {e}"
            if container:
                container.close()
            self.path.unlink(missing_ok=True)
            _keep_awake(False)
            self._ready.set()
            return
        self._ready.set()
        samples, peak = 0, 0
        try:
            while not (self._stop.is_set() and self._audio.empty()):
                try:
                    block = self._audio.get(timeout=0.5)
                except queue.Empty:
                    continue
                if samples < RATE * 10:
                    samples += len(block)
                    peak = max(peak, int(np.abs(np.asarray(block, dtype=np.int32)).max(initial=0)))
                    if samples >= RATE * 10:
                        self.silent = peak < SILENCE_PEAK
                frame = av.AudioFrame.from_ndarray(np.asarray(block, dtype=np.int16).reshape(1, -1),
                                                   format="s16", layout="mono")
                frame.sample_rate = RATE
                for packet in stream.encode(frame):
                    container.mux(packet)
        finally:
            mic.stop()
            mic.close()
            for packet in stream.encode(None):
                container.mux(packet)
            container.close()
            _keep_awake(False)

    def mark(self) -> int:
        self.marks.append(int(time.time() - self.started))
        self._save_state()
        return len(self.marks)

    def finish(self) -> int:
        self._stop.set()
        self._thread.join(30)
        _state_file().unlink(missing_ok=True)
        return _save_document(str(self.path), self.subject, self.title, self.marks)

    def info(self) -> dict:
        return {"subject": self.subject, "title": self.title, "started": self.started,
                "seconds": int(time.time() - self.started), "marks": len(self.marks),
                "until": self.until.strftime("%H:%M") if self.until else None, "scheduled": bool(self.entry_id),
                "silent": self.silent}


def _save_document(path: str, subject: str, title: str, marks: list[int]) -> int:
    doc_id = db.create_document(title, "lesson", subject, path)
    if marks:
        db.update_document(doc_id, marks=",".join(str(m) for m in marks)[:2000])
    worker.enqueue(doc_id)
    return doc_id


def recover() -> int | None:
    """Aufnahme, die bei einem Absturz oder beim Herunterfahren noch lief, trotzdem verarbeiten."""
    f = _state_file()
    if not f.exists():
        return None
    try:
        state = json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        f.unlink(missing_ok=True)
        return None
    f.unlink(missing_ok=True)
    from pathlib import Path
    if not Path(state["path"]).exists() or Path(state["path"]).stat().st_size < 1000:
        return None
    return _save_document(state["path"], state.get("subject", ""), state.get("title") or "Aufnahme",
                          state.get("marks", []))


# --- Steuerung ----------------------------------------------------------------

def start(subject: str, title: str, until: datetime | None = None, entry_id: str = "") -> dict:
    global _current, _last_error
    with _lock:
        if _current:
            raise RuntimeError("Es läuft schon eine Aufnahme.")
        rec = Recording(subject, title, until, entry_id)
        try:
            rec.start()
        except RuntimeError as e:
            _last_error = str(e)
            raise
        _current, _last_error = rec, ""
        return rec.info()


def stop() -> int | None:
    global _current
    with _lock:
        rec, _current = _current, None
    return rec.finish() if rec else None


def mark() -> int:
    with _lock:
        if not _current:
            raise RuntimeError("Es läuft keine Aufnahme.")
        return _current.mark()


def status() -> dict:
    rec = _current
    return {"recording": rec.info() if rec else None, "error": _last_error}


# --- Stundenplan --------------------------------------------------------------

def timetable() -> dict:
    return {
        "entries": json.loads(db.get_setting("timetable", "[]")),
        "enabled": db.get_setting("timetable_enabled", "1") == "1",
        "paused_until": db.get_setting("timetable_paused_until", ""),
    }


def _clean_time(value: str) -> str:
    h, m = (int(p) for p in str(value).strip().split(":")[:2])
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(value)
    return f"{h:02d}:{m:02d}"


def save_timetable(entries: list[dict], enabled: bool, paused_until: str = "") -> dict:
    clean = []
    for e in entries:
        start_, end = _clean_time(e["start"]), _clean_time(e["end"])
        if end <= start_:
            raise ValueError(f"Am {DAYS[int(e['day'])]} endet die Lektion ({end}) vor dem Start ({start_}).")
        clean.append({"id": e.get("id") or uuid.uuid4().hex[:8], "day": int(e["day"]) % 7,
                      "start": start_, "end": end, "subject": str(e.get("subject", "")).strip(),
                      "title": str(e.get("title", "")).strip(), "active": bool(e.get("active", True))})
    clean.sort(key=lambda e: (e["day"], e["start"]))
    db.set_setting("timetable", json.dumps(clean, ensure_ascii=False))
    db.set_setting("timetable_enabled", "1" if enabled else "0")
    db.set_setting("timetable_paused_until", paused_until or "")
    return timetable()


def _done_today(today: str) -> set[str]:
    data = json.loads(db.get_setting("timetable_done", "{}"))
    return set(data.get("ids", [])) if data.get("date") == today else set()


def _mark_done(today: str, entry_id: str) -> None:
    ids = _done_today(today) | {entry_id}
    db.set_setting("timetable_done", json.dumps({"date": today, "ids": sorted(ids)}))


def _window(day, e: dict) -> tuple[datetime, datetime]:
    at = lambda hm: datetime.combine(day, datetime.strptime(hm, "%H:%M").time())
    return at(e["start"]) - LEAD, at(e["end"]) + TAIL


def due_entry(now: datetime, plan: dict, done: set[str]) -> dict | None:
    """Die Lektion, die jetzt laufen sollte und noch nicht aufgenommen wurde."""
    if not plan["enabled"] or (plan["paused_until"] and now.date().isoformat() <= plan["paused_until"]):
        return None
    for e in plan["entries"]:
        if not e.get("active", True) or e["day"] != now.weekday() or e["id"] in done:
            continue
        begin, end = _window(now.date(), e)
        if begin <= now < end and end - now >= timedelta(minutes=MIN_MINUTES_LEFT):
            return e
    return None


def skip_today(entry_id: str, now: datetime | None = None) -> None:
    _mark_done((now or datetime.now()).date().isoformat(), entry_id)


def tick(now: datetime | None = None) -> None:
    """Startet und beendet Aufnahmen nach Stundenplan. Läuft alle paar Sekunden."""
    global _last_error
    now = now or datetime.now()
    rec = _current
    if rec and rec.until and now >= rec.until:
        stop()
        return
    if rec:
        return
    today = now.date().isoformat()
    entry = due_entry(now, timetable(), _done_today(today))
    if not entry:
        return
    _mark_done(today, entry["id"])  # auch wenn du sie von Hand stoppst, startet sie heute nicht nochmals
    until = _window(now.date(), entry)[1]
    title = f"{entry['title'] or entry['subject'] or 'Lektion'} {now.strftime('%d.%m.%Y')}"
    try:
        start(entry["subject"], title, until, entry["id"])
    except RuntimeError:
        pass  # Fehler steht in status() und wird in der App angezeigt


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception as e:  # der Stundenplan darf die App nie zum Absturz bringen
            print("Stundenplan:", e)
        time.sleep(CHECK_EVERY)


def start_scheduler() -> None:
    threading.Thread(target=_loop, daemon=True).start()
