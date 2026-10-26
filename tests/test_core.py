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
    counts = db.flashcard_counts()
    assert counts["total"] == 1 and counts["due"] == 1


def test_subjects_scope_search_and_rename(fake_llm):
    bio = db.create_document("Zellen", "text", subject="Bio", raw_text="Die Mitose ist die Zellteilung.")
    geo = db.create_document("Schweiz", "text", subject="Geo", raw_text="Bern ist die Bundesstadt.")
    ingest.process(bio)
    ingest.process(geo)
    assert {h["doc_id"] for h in study.search("Mitose Zellteilung", subject="Geo")} == {geo}
    assert db.rename_subject("Bio", "Biologie") == 1
    assert {s["subject"] for s in db.list_subjects()} == {"Biologie", "Geo"}
    assert db.subject_for("Bio") == "Biologie"
    assert db.subject_for("Unbekannt") == "Unbekannt"


def test_subject_wide_flashcards_link_to_source_doc(fake_llm, monkeypatch):
    a = db.create_document("A", "text", subject="Mathe", raw_text="Brüche haben Zähler und Nenner.")
    ingest.process(a)
    monkeypatch.setattr(llm, "ask", lambda *a, **k: '{"karten": [{"vorne": "Was hat ein Bruch?", "hinten": "Zähler und Nenner", "quelle": 1}]}')
    assert study.make_flashcards(subject="Mathe") == 1
    assert db.due_flashcards(subject="Mathe")[0]["doc_id"] == a
    assert db.due_flashcards(subject="Geo") == []
    with pytest.raises(llm.LLMError):
        study.make_quiz(subject="Leer")


def test_reindex_keeps_text(fake_llm):
    doc_id = db.create_document("A", "text", raw_text="Inhalt hier")
    ingest.process(doc_id)
    db.replace_chunks(doc_id, [])
    ingest.reindex(doc_id)
    assert study.search("Inhalt")[0]["doc_id"] == doc_id


def handwritten_pdf(path, handwritten_pages=1):
    import pymupdf
    with pymupdf.open() as pdf:
        for n in range(handwritten_pages):
            page = pdf.new_page()
            page.insert_text((72, 40), f"Zahlensysteme Seite {n + 1}")
            for i in range(6):  # "Handschrift" als Striche
                page.draw_line((72, 100 + i * 20), (300 + n, 110 + i * 20))
        pdf.new_page().insert_text((72, 72), " ".join(["Wort"] * 40))
        pdf.save(path)
    return path


def test_pdf_pages_without_text_are_read_by_vision_model(tmp_path, monkeypatch):
    path = handwritten_pdf(tmp_path / "hand.pdf")
    monkeypatch.setattr(llm, "vision_model", lambda: "bild-modell")
    calls = []
    monkeypatch.setattr(llm, "read_image", lambda png, **kw: calls.append(png) or "Das Binärsystem hat die Basis 2.")
    text = ingest.extract_pdf_text(path, read_images=True)
    assert len(calls) == 1 and calls[0].startswith(b"\x89PNG")
    assert "Basis 2" in text and "Seite 2" in text
    assert "Basis 2" not in ingest.extract_pdf_text(path)  # ohne Bild-Modell nur Text


def test_read_pages_are_cached_and_limit_resumes_later(tmp_path, monkeypatch):
    path = handwritten_pdf(tmp_path / "hand.pdf", handwritten_pages=3)
    monkeypatch.setattr(llm, "vision_model", lambda: "bild-modell")
    calls = []

    def limited(png, **kw):
        calls.append(png)
        if len(calls) > 1:
            raise llm.LimitError("OpenRouter-Limit erreicht")
        return "Seite gelesen"

    monkeypatch.setattr(llm, "read_image", limited)
    first = ingest.extract_pdf_text(path, read_images=True)
    assert first.count("Seite gelesen") == 1 and first.images_missing == 2
    assert len(calls) == 2  # nach dem Limit nicht weiter versucht

    calls.clear()
    monkeypatch.setattr(llm, "read_image", lambda png, **kw: calls.append(png) or "Seite gelesen")
    second = ingest.extract_pdf_text(path, read_images=True)
    assert second.count("Seite gelesen") == 3 and second.images_missing == 0
    assert len(calls) == 2  # die erste Seite kam aus dem Speicher


def test_timeout_on_a_page_is_retried_then_skipped(tmp_path, monkeypatch):
    path = handwritten_pdf(tmp_path / "hand.pdf", handwritten_pages=2)
    monkeypatch.setattr(llm, "vision_model", lambda: "bild-modell")
    calls = []

    def hangs_on_first(png, **kw):
        calls.append(png)
        if png == calls[0]:
            raise llm.LLMError("OpenRouter ist nicht erreichbar: timed out")
        return "zweite Seite"

    monkeypatch.setattr(llm, "read_image", hangs_on_first)
    text = ingest.extract_pdf_text(path, read_images=True)
    assert "zweite Seite" in text and text.images_missing == 1
    assert len(calls) == 3  # erste Seite zweimal versucht, dann weiter


def test_unread_images_are_retried_automatically(fake_llm, tmp_path, monkeypatch):
    from app import worker
    doc_id = db.create_document("Hand", "pdf", "Mathe", str(tmp_path / "x.pdf"))
    db.update_document(doc_id, status="ready", images_missing=3)
    other = db.create_document("Limit", "pdf", "Mathe", str(tmp_path / "y.pdf"))
    db.update_document(other, status="error", error="OpenRouter-Limit erreicht")
    queued = []
    monkeypatch.setattr(worker, "enqueue", queued.append)
    assert worker.retry_unread_images() == 2
    assert queued == [doc_id, other]
    assert db.get_document(other)["status"] == "pending"
