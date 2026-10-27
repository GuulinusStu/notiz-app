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


def test_stats_search_and_card_management(fake_llm):
    with TestClient(app) as client:
        doc_id = client.post("/api/notes", json={"title": "Zahlensysteme", "text": "Das Binärsystem hat die Basis zwei.", "subject": "Mathe"}).json()["id"]
        wait_ready(client, doc_id)
        found = client.post("/api/search", json={"query": "Zahlen", "subject": "Mathe"}).json()
        assert found[0]["doc_id"] == doc_id
        client.post(f"/api/documents/{doc_id}/flashcards")
        card = client.get("/api/flashcards?subject=Mathe").json()[0]
        assert client.patch(f"/api/flashcards/{card['id']}", json={"front": "Basis?", "back": "2"}).json() == {"ok": True}
        client.post(f"/api/flashcards/{card['id']}/review", json={"grade": 2})
        client.post("/api/quiz/result", json={"subject": "Mathe", "score": 3, "total": 4})
        s = client.get("/api/stats?subject=Mathe").json()
        assert s["notes"] == 1 and s["reviews_today"] == 1 and s["streak"] == 1 and s["quiz_percent"] == 75
        csv = client.get("/api/flashcards/export.csv").text
        assert csv == "Basis?\t2\tMathe\n"
        client.delete(f"/api/flashcards/{card['id']}")
        assert client.get("/api/flashcards").json() == []


def test_streak_counts_consecutive_days():
    day = 86400
    now = 1_800_000_000
    assert db.streak_days([now, now - day, now - 2 * day], now=now) == 3
    assert db.streak_days([now - day, now - 2 * day], now=now) == 2  # heute noch nicht gelernt
    assert db.streak_days([now - 3 * day], now=now) == 0


def test_manage_subjects_and_move_notes(fake_llm):
    with TestClient(app) as client:
        a = client.post("/api/notes", json={"title": "A", "text": "eins", "subject": "Mathe"}).json()["id"]
        b = client.post("/api/notes", json={"title": "B", "text": "zwei", "subject": "Mathe"}).json()["id"]
        client.post("/api/subjects", json={"name": "Physik"})
        names = lambda: [s["subject"] for s in client.get("/api/subjects").json()]
        assert names() == ["Mathe", "Physik"]
        client.post("/api/subjects/order", json={"order": ["Physik", "Mathe"]})
        assert names() == ["Physik", "Mathe"]

        assert client.post("/api/documents/move", json={"ids": [a], "subject": "Physik"}).json() == {"moved": 1}
        assert db.get_document(a)["subject"] == "Physik" and db.get_document(b)["subject"] == "Mathe"
        assert client.delete("/api/subjects", params={"name": "Physik"}).status_code == 400  # nicht leer

        client.post("/api/subjects/rename", json={"old": "Physik", "new": "Naturlehre"})
        assert names() == ["Naturlehre", "Mathe"]
        client.post("/api/subjects/rename", json={"old": "Naturlehre", "new": "Mathe"})  # zusammenlegen
        assert names() == ["Mathe"]

        client.post("/api/subjects", json={"name": "Leer"})
        assert client.delete("/api/subjects", params={"name": "Leer"}).status_code == 200
        assert names() == ["Mathe"]


def test_renamed_onenote_title_survives_sync():
    from app import onenote
    sections = lambda m: [{"id": "{A}", "name": "Brüche", "notebook": "Mathe", "path": "Mathe / Brüche", "lastModified": m}]

    def export(section_id, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-1.4")

    onenote.sync(lister=lambda: sections("1"), exporter=export)
    doc = db.list_documents()[0]
    with TestClient(app) as client:
        client.patch(f"/api/documents/{doc['id']}", json={"title": "Brüche (Test 3)"})
    onenote.sync(lister=lambda: sections("2"), exporter=export)
    assert db.get_document(doc["id"])["title"] == "Brüche (Test 3)"
