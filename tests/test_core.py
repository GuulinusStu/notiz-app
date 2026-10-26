import json

import pytest

from app import db, ingest, llm, srs, study


def test_split_words_overlap_covers_everything():
    text = " ".join(str(i) for i in range(500))
    parts = ingest.split_words(text, max_words=220, overlap=40)
    assert parts[0].split()[0] == "0"
    assert parts[-1].split()[-1] == "499"
    assert parts[1].split()[0] == "180"
    assert ingest.split_words("   ", 10) == []


def test_split_lines_keeps_lines_whole():
    text = "\n".join(f"[00:{i:02d}] eins zwei drei" for i in range(10))
    parts = ingest.split_lines(text, max_words=9)
    assert all(len(p.splitlines()) <= 2 for p in parts)
    assert "\n".join(parts) == text


def test_fmt_time():
    assert ingest.fmt_time(65) == "01:05"
    assert ingest.fmt_time(3725) == "1:02:05"


def test_parse_json_with_surrounding_text():
    assert llm.parse_json('Klar! {"a": 1} fertig') == {"a": 1}
    with pytest.raises(llm.LLMError):
        llm.parse_json("kein json")


def test_srs_schedule():
    card = {"ease": 2.5, "interval_days": 0, "reps": 0}
    first = srs.schedule(card, srs.GOOD, now=0)
    assert first["interval_days"] == 1 and first["due"] == srs.DAY
    second = srs.schedule(first, srs.GOOD, now=0)
    assert second["interval_days"] == 3
    third = srs.schedule(second, srs.EASY, now=0)
    assert third["interval_days"] > 3 * 2.5 and third["ease"] > 2.5
    again = srs.schedule(third, srs.AGAIN, now=0)
    assert again["reps"] == 0 and again["due"] == 600 and again["ease"] >= 1.3
    with pytest.raises(ValueError):
        srs.schedule(card, 7)


def test_text_note_is_indexed_and_searchable(fake_llm):
    a = db.create_document("Bio", "text", raw_text="Die Mitose ist die Zellteilung bei Körperzellen.")
    b = db.create_document("Geo", "text", raw_text="Bern ist die Bundesstadt der Schweiz.")
    ingest.process(a)
    ingest.process(b)
    assert db.get_document(a)["status"] == "ready"
    hits = study.search("Was ist Mitose?", k=1)
    assert hits[0]["doc_id"] == a
    result = study.answer("Was ist Mitose?")
    assert result["answer"] == "Antwort [1]" and result["sources"][0]["n"] == 1


def test_lesson_pipeline_filters_off_topic(fake_llm):
    doc_id = db.create_document("Mathe", "lesson", raw_text="[00:00] Heute lernen wir Brüche")
    ingest.process(doc_id)
    doc = db.get_document(doc_id)
    assert doc["status"] == "ready", doc["error"]
    assert doc["clean_text"].startswith("Bereinigt:")
    assert doc["summary"]

    off_topic = db.create_document("Pause", "lesson", raw_text="[00:00] Pause und Gelaber")
    ingest.process(off_topic)
    assert db.get_document(off_topic)["clean_text"] == ""


def test_pdf_import(fake_llm, tmp_path):
    import pymupdf as fitz
    path = tmp_path / "notiz.pdf"
    with fitz.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Photosynthese braucht Licht.")
        pdf.save(path)
    doc_id = db.create_document("Bio", "pdf", source_path=str(path))
    ingest.process(doc_id)
    doc = db.get_document(doc_id)
    assert doc["status"] == "ready"
    assert "Photosynthese" in doc["raw_text"]


def test_processing_error_is_stored(fake_llm, tmp_path):
    doc_id = db.create_document("leer", "pdf", source_path=str(tmp_path / "fehlt.pdf"))
    ingest.process(doc_id)
    assert db.get_document(doc_id)["status"] == "error"


def test_quiz_and_flashcards_drop_invalid_items(fake_llm):
    doc_id = db.create_document("X", "text", raw_text="Inhalt")
    ingest.process(doc_id)
    doc = db.get_document(doc_id)
    quiz = study.make_quiz(doc)
    assert len(quiz) == 1 and quiz[0]["richtig"] == 1
    assert study.make_flashcards(doc) == 1
    assert db.flashcard_counts() == {"total": 1, "due": 1}
