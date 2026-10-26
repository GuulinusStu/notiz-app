import json
import zlib

import numpy as np
import pytest

from app import config, llm


@pytest.fixture(autouse=True)
def temp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    from app import db
    db.init()


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
