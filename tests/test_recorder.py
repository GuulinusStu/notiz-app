import threading
import time
from datetime import datetime

import numpy as np
import pytest

pytest.importorskip("av")

from app import db, ingest, recorder, worker


class FakeMic:
    """Liefert einen Ton, solange die Aufnahme läuft, statt ein echtes Mikrofon zu öffnen."""

    def __init__(self, callback):
        self.callback, self.running = callback, False

    def start(self):
        self.running = True
        threading.Thread(target=self._feed, daemon=True).start()

    def _feed(self):
        t = np.arange(recorder.RATE // 10) / recorder.RATE
        block = (np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16)
        while self.running:
            self.callback(block)
            time.sleep(0.01)

    def stop(self):
        self.running = False

    def close(self):
        pass


@pytest.fixture
def fake_mic(monkeypatch):
    monkeypatch.setattr(recorder, "source_factory", FakeMic)
    queued = []
    monkeypatch.setattr(worker, "enqueue", queued.append)
    db.init()
    yield queued
    recorder.stop()


def test_record_mark_and_stop(fake_mic):
    recorder.start("Mathe", "Brüche")
    time.sleep(0.5)
    assert recorder.mark() == 1
    assert recorder.status()["recording"]["subject"] == "Mathe"
    doc_id = recorder.stop()
    doc = db.get_document(doc_id)
    assert doc["kind"] == "lesson" and doc["subject"] == "Mathe" and doc["marks"] == "0"
    assert fake_mic == [doc_id]
    assert len(ingest.load_audio(ingest.Path(doc["source_path"]))) > 16000 * 0.5
    assert recorder.status()["recording"] is None


def test_only_one_recording(fake_mic):
    recorder.start("Mathe", "A")
    with pytest.raises(RuntimeError):
        recorder.start("Deutsch", "B")


def test_broken_microphone(monkeypatch, fake_mic):
    def broken(_cb):
        raise OSError("kein Gerät")
    monkeypatch.setattr(recorder, "source_factory", broken)
    with pytest.raises(RuntimeError, match="Mikrofon"):
        recorder.start("Mathe", "A")
    assert "kein Gerät" in recorder.status()["error"]
    assert recorder.status()["recording"] is None


def test_recover_after_crash(fake_mic):
    recorder.start("Bio", "Zellen")
    time.sleep(0.3)
    recorder.mark()
    rec = recorder._current
    rec._stop.set()  # Absturz nachspielen: Datei ist geschrieben, aber kein Dokument erstellt
    rec._thread.join(5)
    recorder._current = None
    doc_id = recorder.recover()
    assert db.get_document(doc_id)["title"] == "Zellen"
    assert recorder.recover() is None


def plan(**kw):
    entry = {"id": "m1", "day": 0, "start": "08:15", "end": "09:00", "subject": "Mathe", "title": "", "active": True}
    entry.update(kw)
    return {"entries": [entry], "enabled": True, "paused_until": ""}


MONDAY = datetime(2026, 10, 26)


def test_due_entry_window():
    assert recorder.due_entry(MONDAY.replace(hour=8, minute=13), plan(), set()) is None
    assert recorder.due_entry(MONDAY.replace(hour=8, minute=14), plan(), set())["id"] == "m1"  # 1 Min. früher
    assert recorder.due_entry(MONDAY.replace(hour=8, minute=58), plan(), set()) is None  # zu spät zum Starten
    assert recorder.due_entry(MONDAY.replace(hour=8, minute=30), plan(), {"m1"}) is None
    assert recorder.due_entry(MONDAY.replace(hour=8, minute=30), plan(day=1), set()) is None
    assert recorder.due_entry(MONDAY.replace(hour=8, minute=30), plan(active=False), set()) is None
    paused = {**plan(), "paused_until": "2026-10-30"}
    assert recorder.due_entry(MONDAY.replace(hour=8, minute=30), paused, set()) is None


def test_tick_starts_and_stops(fake_mic):
    recorder.save_timetable(plan()["entries"], True)
    recorder.tick(MONDAY.replace(hour=8, minute=15))
    rec = recorder.status()["recording"]
    assert rec["scheduled"] and rec["until"] == "09:02" and rec["title"] == "Mathe 26.10.2026"
    time.sleep(0.3)
    recorder.tick(MONDAY.replace(hour=9, minute=2))
    assert recorder.status()["recording"] is None
    assert len(fake_mic) == 1
    recorder.tick(MONDAY.replace(hour=8, minute=40))  # schon erledigt: startet nicht nochmals
    assert recorder.status()["recording"] is None


def test_save_timetable_validates():
    db.init()
    with pytest.raises(ValueError):
        recorder.save_timetable([{"day": 0, "start": "09:00", "end": "08:00"}], True)
    saved = recorder.save_timetable([{"day": 2, "start": "8:5", "end": "9:00", "subject": "Mathe"}], False, "2026-12-31")
    assert saved["entries"][0]["start"] == "08:05" and saved["entries"][0]["id"]
    assert saved["enabled"] is False and saved["paused_until"] == "2026-12-31"


class SilentMic(FakeMic):
    def _feed(self):
        block = np.zeros(recorder.RATE // 10, dtype=np.int16)
        while self.running:
            self.callback(block)
            time.sleep(0.001)


def test_warns_about_silence(fake_mic, monkeypatch):
    monkeypatch.setattr(recorder, "source_factory", SilentMic)
    recorder.start("Mathe", "Stumm")
    for _ in range(50):
        if recorder.status()["recording"]["silent"]:
            break
        time.sleep(0.05)
    assert recorder.status()["recording"]["silent"]


def test_sound_is_not_silence(fake_mic):
    recorder.start("Mathe", "Laut")
    time.sleep(1.5)  # FakeMic liefert 10 s Ton in etwa 1 s
    assert recorder.status()["recording"]["silent"] is False
