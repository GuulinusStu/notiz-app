import datetime

from fastapi.testclient import TestClient

from app import db, exams
from app.main import app
from test_api import wait_ready


def test_goals_are_parsed_from_lists():
    text = "Lernziele\n- Das Binärsystem erklären\n2. Hexadezimal umrechnen\n• ok\n\n[x] Zweierkomplement bilden\n- Das Binärsystem erklären"
    assert exams.parse_goals(text) == ["Lernziele", "Das Binärsystem erklären", "Hexadezimal umrechnen",
                                       "Zweierkomplement bilden"]


def test_editing_goals_keeps_checked_ones():
    old = [{"text": "A lernen", "done": True}, {"text": "B lernen", "done": False}]
    assert exams.merge_goals(old, ["B lernen", "A lernen", "C lernen"]) == [
        {"text": "B lernen", "done": False}, {"text": "A lernen", "done": True}, {"text": "C lernen", "done": False}]


def test_plan_spreads_open_goals_and_ends_with_review():
    today = datetime.date(2026, 11, 2)
    exam = {"date": "2026-11-07", "goals": [{"text": "A", "done": False}, {"text": "B", "done": True},
                                            {"text": "C", "done": False}]}
    plan = exams.plan(exam, today)
    assert [p["date"] for p in plan] == ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]
    assert plan[-1]["task"].startswith("Gesamtwiederholung")
    learned = [g for p in plan[:-1] for g in p["goals"]]
    assert "B" not in learned and learned.count("A") == 2 and learned.count("C") == 2
    assert exams.plan(exam, datetime.date(2026, 11, 7)) == []  # Prüfungstag


def test_plan_with_one_day_left_still_learns():
    plan = exams.plan({"date": "2026-11-03", "goals": [{"text": "A", "done": False}]}, datetime.date(2026, 11, 2))
    assert plan == [{"date": "2026-11-02", "goals": ["A"], "task": "Lernziele lernen, dann Karteikarten"}]


def test_exam_finds_matching_notes_and_makes_quiz(fake_llm):
    with TestClient(app) as client:
        binary = client.post("/api/notes", json={"title": "Binär", "subject": "Informatik",
                                                 "text": "Das Binärsystem hat die Basis zwei und nur die Ziffern null und eins."}).json()["id"]
        other = client.post("/api/notes", json={"title": "Netzwerke", "subject": "Informatik",
                                                "text": "Ein Router verbindet verschiedene Netzwerke miteinander."}).json()["id"]
        wait_ready(client, binary)
        wait_ready(client, other)
        exam = client.post("/api/exams", json={"subject": "Informatik", "title": "Zahlensysteme", "date": "2030-05-10",
                                               "goals_text": "- Das Binärsystem mit Basis zwei erklären\n- Quantenphysik"}).json()
        assert exam["goals_total"] == 2 and exam["days_left"] > 0
        detail = client.get(f"/api/exams/{exam['id']}").json()
        assert [d["doc_id"] for d in detail["goals"][0]["docs"]] == [binary]
        assert detail["goals"][1]["docs"] == []  # nicht in den Notizen
        assert detail["plan"][-1]["task"].startswith("Gesamtwiederholung")

        quiz = client.post(f"/api/exams/{exam['id']}/quiz?n=3").json()
        assert quiz["fragen"][0]["frage"] == "Was ist 1+1?"
        assert client.post(f"/api/exams/{exam['id']}/flashcards").json()["created"] == 1
        assert db.list_flashcards("Informatik")[0]["doc_id"] == binary

        client.patch(f"/api/exams/{exam['id']}", json={"done": [True, False]})
        updated = client.patch(f"/api/exams/{exam['id']}", json={"goals_text": "Quantenphysik\nDas Binärsystem mit Basis zwei erklären"}).json()
        assert updated["goals"][1] == {"text": "Das Binärsystem mit Basis zwei erklären", "done": True}

        client.post("/api/subjects/rename", json={"old": "Informatik", "new": "Info"})
        assert client.get("/api/exams?subject=Info").json()[0]["title"] == "Zahlensysteme"

        ics = client.get("/api/exams.ics").text
        assert "DTSTART;VALUE=DATE:20300510" in ics and "SUMMARY:Prüfung Info: Zahlensysteme" in ics

        assert client.post("/api/exams", json={"title": "x", "date": "2030-02-31"}).status_code == 400
        client.delete(f"/api/exams/{exam['id']}")
        assert client.get("/api/exams").json() == []


def test_goals_from_pdf(tmp_path):
    import pymupdf
    path = tmp_path / "lernziele.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "1. Brueche addieren\n2. Brueche kuerzen")
        pdf.save(path)
    with TestClient(app) as client:
        r = client.post("/api/exams/goals-from-file", files={"file": ("l.pdf", path.read_bytes(), "application/pdf")})
        assert r.json()["goals_text"] == "Brueche addieren\nBrueche kuerzen"
