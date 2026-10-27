import json
import os
import tempfile
import zlib

# Vor dem Import der App: Hintergrund-Threads, die einen Test überleben, dürfen nie die echten Daten anfassen.
os.environ["NOTIZAPP_DATA"] = tempfile.mkdtemp(prefix="notizapp-test-")
os.environ["NOTIZAPP_ONENOTE_AUTO_SYNC"] = "0"
os.environ["NOTIZAPP_AUTO_RETRY"] = "0"
os.environ["NOTIZAPP_AUTO_BACKUP"] = "0"
os.environ["NOTIZAPP_AUTO_RECORD"] = "0"

import numpy as np
import pytest

from app import config, llm


@pytest.fixture(autouse=True)
def temp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "ONENOTE_AUTO_SYNC", False)
    from app import db, worker
    db.init()
    yield
    # Aufträge aus diesem Test nicht in den nächsten Test mitnehmen
    while not worker._jobs.empty():
        worker._jobs.get_nowait()
        worker._jobs.task_done()
    # und einen Auftrag, der gerade läuft, fertig werden lassen (sonst trifft er die Daten des nächsten Tests)
    import time
    deadline = time.time() + 10
    while worker._jobs.unfinished_tasks and time.time() < deadline:
        time.sleep(0.02)


def fake_embed(texts, batch_size=16):
    # Deterministischer "Bag of words"-Vektor, damit ähnliche Texte ähnliche Vektoren haben.
    vecs = np.zeros((len(texts), 64), dtype=np.float32)
    for i, t in enumerate(texts):
        for w in t.lower().split():
            vecs[i, zlib.crc32(w.strip(".,?!").encode()) % 64] += 1
    return vecs


def fake_ask(system, user, **kwargs):
    if "Multiple-Choice" in system:
        return json.dumps({"fragen": [{"frage": "Was ist 1+1?", "optionen": ["1", "2", "3", "4"],
                                       "richtig": 1, "erklaerung": "Einfach."},
                                      {"frage": "kaputt", "optionen": ["a"], "richtig": 5}]})
    if "Karteikarten" in system:
        return 'Hier: {"karten": [{"vorne": "Hauptstadt der Schweiz?", "hinten": "Bern"}, {"vorne": "", "hinten": "x"}]}'
    if "Schullektion" in system:
        return "(kein Inhalt)" if "Pause" in user else "Bereinigt: " + user
    return "Antwort [1]"


@pytest.fixture
def fake_llm(monkeypatch):
    monkeypatch.setattr(llm, "embed", fake_embed)
    monkeypatch.setattr(llm, "ask", fake_ask)
    monkeypatch.setattr(llm, "chat", lambda messages, **k: fake_ask(messages[0]["content"], messages[-1]["content"]))
