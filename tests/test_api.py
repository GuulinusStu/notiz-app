import time

from fastapi.testclient import TestClient

from app import db
from app.main import app


def wait_ready(client, doc_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        doc = client.get(f"/api/documents/{doc_id}").json()
        if doc["status"] in ("ready", "error"):
            return doc
        time.sleep(0.05)
    raise AssertionError("Verarbeitung dauerte zu lange")


def test_note_flow(fake_llm):
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        r = client.post("/api/notes", json={"title": "Geo", "text": "Bern ist die Bundesstadt."})
        doc = wait_ready(client, r.json()["id"])
        assert doc["status"] == "ready"
        assert client.post("/api/ask", json={"question": "Bern?"}).json()["sources"]

        assert client.post(f"/api/documents/{doc['id']}/flashcards").json()["created"] == 1
        cards = client.get("/api/flashcards/due").json()
        r = client.post(f"/api/flashcards/{cards[0]['id']}/review", json={"grade": 2})
        assert r.json()["interval_days"] == 1
        assert client.get("/api/flashcards/due").json() == []

        assert client.delete(f"/api/documents/{doc['id']}").json() == {"ok": True}
        assert client.get(f"/api/documents/{doc['id']}").status_code == 404
        assert db.flashcard_counts()["total"] == 0


def test_rejects_wrong_file_type(fake_llm):
    with TestClient(app) as client:
        r = client.post("/api/upload/pdf", files={"files": ("virus.exe", b"x")})
        assert r.status_code == 400


def test_reprocess_only_errors(fake_llm):
    ok = db.create_document("ok", "text", raw_text="x")
    bad = db.create_document("bad", "pdf", source_path="/fehlt.pdf")
    db.update_document(ok, status="ready")
    db.update_document(bad, status="error", error="kaputt")
    with TestClient(app) as client:
        assert client.post("/api/documents/reprocess", json={"only_errors": True}).json() == {"queued": 1}
        assert db.get_document(ok)["status"] == "ready"
