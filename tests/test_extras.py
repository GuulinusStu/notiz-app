import datetime

import pytest

from fastapi.testclient import TestClient

from app import db, exams, ingest, llm
from app.main import app
from test_api import wait_ready


def test_mistakes_collect_resolve_and_become_cards(fake_llm):
    with TestClient(app) as client:
        doc = client.post("/api/notes", json={"title": "Binär", "subject": "Info", "text": "Die Basis des Binärsystems ist zwei."}).json()["id"]
        wait_ready(client, doc)
        q = {"subject": "Info", "question": "Welche Basis hat das Binärsystem?", "options": ["10", "2", "16"], "right": 1, "explanation": "Nur 0 und 1."}
        first = client.post("/api/mistakes", json=q).json()["id"]
        assert client.post("/api/mistakes", json=q).json()["id"] == first  # nicht doppelt
        assert client.post("/api/mistakes", json={**q, "right": 7}).status_code == 400
        assert len(client.get("/api/mistakes?subject=Info").json()) == 1
        assert client.get("/api/stats?subject=Info").json()["mistakes_open"] == 1

        client.post(f"/api/mistakes/{first}/card")
        cards = client.get("/api/flashcards?subject=Info").json()
        assert any(c["front"] == q["question"] and c["back"].startswith("2") for c in cards)
        assert client.get("/api/mistakes").json() == []

        other = client.post("/api/mistakes", json={**q, "question": "Was ist ein Bit?"}).json()["id"]
        client.post(f"/api/mistakes/{other}/resolve")
        assert client.get("/api/mistakes").json() == []


def test_focus_minutes_count_for_streak_and_today():
    with TestClient(app) as client:
        client.post("/api/focus", json={"subject": "Mathe", "minutes": 25})
        s = client.get("/api/stats").json()
        assert s["focus_today"] == 25 and s["streak"] == 1
        assert client.post("/api/focus", json={"minutes": 0}).status_code == 422


def test_today_lists_plan_tasks():
    today = datetime.date(2026, 11, 2)
    soon = {"id": 1, "title": "Brüche", "subject": "Mathe", "date": "2026-11-04", "goals": [{"text": "Kürzen", "done": False}]}
    now = {"id": 2, "title": "Vokabeln", "subject": "Englisch", "date": "2026-11-02", "goals": []}
    past = {"id": 3, "title": "Alt", "subject": "Mathe", "date": "2026-10-01", "goals": []}
    tasks = exams.today_tasks([soon, now, past], today)
    assert [t["exam_id"] for t in tasks] == [2, 1]
    assert tasks[0]["task"].startswith("Heute ist die Prüfung")
    assert tasks[1]["goals"] == ["Kürzen"]


def test_explain_mode_compares_with_notes(fake_llm, monkeypatch):
    with TestClient(app) as client:
        doc = client.post("/api/notes", json={"title": "Binär", "subject": "Info", "text": "Das Binärsystem hat die Basis zwei."}).json()["id"]
        wait_ready(client, doc)
        seen = {}
        monkeypatch.setattr(llm, "ask", lambda system, user, **k: seen.update(user=user) or "## Bewertung\nSitzt")
        r = client.post("/api/explain", json={"goal": "Binärsystem erklären", "explanation": "Das Binärsystem hat die Basis zwei.", "subject": "Info"}).json()
        assert r["feedback"].startswith("## Bewertung") and r["sources"][0]["doc_id"] == doc
        assert "Lernziel: Binärsystem erklären" in seen["user"]


def test_marked_passages_from_transcript():
    raw = "[00:05] Begrüssung\n[01:00] Das kommt an die Prüfung\n[01:08] Die Basis ist zwei\n[03:00] Pause"
    assert ingest.marked_passages(raw, "70") == ["[01:10] Das kommt an die Prüfung Die Basis ist zwei"]
    assert ingest.marked_passages(raw, "") == []
    assert ingest.marked_passages(raw, "abc,") == []


def test_audio_upload_keeps_marks(tmp_path, monkeypatch):
    from app import worker
    monkeypatch.setattr(worker, "enqueue", lambda doc_id: None)
    monkeypatch.setattr(ingest, "audio_readable", lambda path: True)
    with TestClient(app) as client:
        r = client.post("/api/upload/audio", files={"file": ("a.webm", b"x", "audio/webm")},
                        data={"title": "Lektion", "subject": "Info", "marks": "12.5,70"})
        assert db.get_document(r.json()["id"])["marks"] == "12.5,70"


def test_broken_audio_upload_is_refused(monkeypatch):
    pytest.importorskip("av")
    from app import worker
    monkeypatch.setattr(worker, "enqueue", lambda doc_id: None)
    with TestClient(app) as client:
        # Mitten aus einer webm-Datei, ohne Dateikopf (so wie ein verlorenes erstes Stück)
        r = client.post("/api/upload/audio", files={"file": ("a.webm", bytes.fromhex("411481 04ec807b83") * 50, "audio/webm")})
        assert r.status_code == 400 and "beschädigt" in r.json()["detail"]
        assert not db.list_documents()


def test_blank_pdf_pages_are_counted(tmp_path):
    import pymupdf
    path = tmp_path / "leer.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), " ".join(["Wort"] * 40))
        pdf.new_page()
        pdf.new_page()
        pdf.save(path)
    text = ingest.extract_pdf_text(path)
    assert text.blank_pages == 2 and "Seite 1" in text


def test_backup_keeps_last_copies(tmp_path, monkeypatch):
    import app.db as dbmod
    folder = tmp_path / "sicherung"
    for day in range(1, 10):
        (folder).mkdir(exist_ok=True)
        (folder / f"notizapp-2026-01-0{day}.db").write_bytes(b"alt")
    target = dbmod.backup(folder, keep=3)
    names = sorted(p.name for p in folder.iterdir())
    assert len(names) == 3 and target.name in names
    import sqlite3
    with sqlite3.connect(target) as c:
        assert c.execute("SELECT count(*) FROM documents").fetchone()[0] == 0


def test_backup_leaves_out_the_api_key(tmp_path):
    import sqlite3
    db.set_setting("openrouter_key", "sk-or-geheim")
    db.set_setting("chat_model", "x")
    target = db.backup(tmp_path / "sicherung")
    with sqlite3.connect(target) as c:
        keys = {r[0] for r in c.execute("SELECT key FROM settings")}
    assert "openrouter_key" not in keys and "chat_model" in keys
    assert db.get_setting("openrouter_key") == "sk-or-geheim"  # Original unverändert


def test_backup_uses_only_private_onedrive(tmp_path, monkeypatch):
    from app import main
    school = tmp_path / "OneDrive - Schule"
    school.mkdir()
    monkeypatch.setenv("OneDrive", str(school))
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    assert "OneDrive" not in str(main.backup_dir())
    private = tmp_path / "OneDrive"
    private.mkdir()
    monkeypatch.setenv("OneDriveConsumer", str(private))
    assert main.backup_dir() == private / "Notiz-App-Sicherung"


def test_quota_counts_own_requests_without_key(monkeypatch):
    monkeypatch.setattr(llm, "_quota_cache", {})
    monkeypatch.setattr(llm, "api_key", lambda: "")
    llm._count_request()
    llm._count_request()
    q = llm.quota()
    assert q["used"] == 2 and q["remaining"] == 48 and q["source"] == "app"
