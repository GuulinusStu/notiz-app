import pytest

from app import db, onenote


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch):
    monkeypatch.setattr(onenote, "RETRY_DELAY", 0)


def make_sections(modified="2026-10-01T10:00:00Z"):
    return [
        {"id": "{A}", "name": "Brüche", "notebook": "Mathe", "path": "Mathe / Brüche", "lastModified": modified},
        {"id": "{B}", "name": "Zellen", "notebook": "Bio", "path": "Bio / Zellen", "lastModified": "2026-10-02T10:00:00Z"},
    ]


def fake_exporter(calls):
    def export(section_id, target):
        calls.append(section_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-1.4")
    return export


def test_sync_only_exports_new_or_changed_sections():
    calls = []
    first = onenote.sync(lister=make_sections, exporter=fake_exporter(calls))
    assert first == {"sections": 2, "new": 2, "updated": 0, "unchanged": 0, "empty": 0, "incomplete": [], "failed": []}
    docs = {d["title"]: d for d in db.list_documents()}
    assert docs["Mathe / Brüche"]["subject"] == "Mathe"

    calls.clear()
    second = onenote.sync(lister=make_sections, exporter=fake_exporter(calls))
    assert second["unchanged"] == 2 and calls == []

    third = onenote.sync(lister=lambda: make_sections("2026-10-20T08:00:00Z"), exporter=fake_exporter(calls))
    assert third["updated"] == 1 and calls == ["{A}"]
    assert len(db.list_documents()) == 2


def test_sync_reports_failed_exports():
    def broken(section_id, target):
        raise onenote.OneNoteError("Abschnitt ist gesperrt")

    result = onenote.sync(lister=make_sections, exporter=broken)
    assert result["new"] == 0 and len(result["failed"]) == 2
    assert result["failed"][0]["error"] == "Abschnitt ist gesperrt"


def test_pdf_name_is_safe_and_unique():
    a = onenote.pdf_name({"id": "{1}", "path": 'Mathe / Brüche: "Teil 1"?'})
    b = onenote.pdf_name({"id": "{2}", "path": 'Mathe / Brüche: "Teil 1"?'})
    assert a != b and a.endswith(".pdf")
    assert not any(ch in a for ch in '<>:"/\\|?*')


def test_old_database_gets_new_columns(tmp_path, monkeypatch):
    import sqlite3
    from app import config
    path = tmp_path / "alt.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL,"
                 " subject TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',"
                 " progress TEXT NOT NULL DEFAULT '', source_path TEXT, raw_text TEXT NOT NULL DEFAULT '',"
                 " clean_text TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',"
                 " error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    db.init()
    doc_id = db.create_document("x", "pdf", external_id="onenote:{X}", external_modified="t")
    assert db.documents_by_external_prefix("onenote:")["onenote:{X}"]["id"] == doc_id


def test_empty_sections_are_remembered_not_failed():
    calls = []

    def empty(section_id, target):
        calls.append(section_id)
        raise onenote.EmptySection("Abschnitt ist leer")

    first = onenote.sync(lister=make_sections, exporter=empty)
    assert first["empty"] == 2 and first["failed"] == [] and len(calls) == 2
    second = onenote.sync(lister=make_sections, exporter=empty)
    assert second["empty"] == 2 and len(calls) == 2  # nicht nochmals versucht
    assert db.list_documents() == []


def test_temporary_failure_is_retried_once():
    attempts = []

    def flaky(section_id, target):
        attempts.append(section_id)
        if attempts.count(section_id) == 1:
            raise onenote.OneNoteError("OneNote startet neu")
        fake_exporter([])(section_id, target)

    result = onenote.sync(lister=make_sections, exporter=flaky)
    assert result["new"] == 2 and result["failed"] == []
    assert attempts == ["{A}", "{A}", "{B}", "{B}"]


def test_incomplete_sections_are_not_imported_and_tried_again():
    calls = []

    def not_loaded(section_id, target):
        calls.append(section_id)
        raise onenote.IncompleteSection("Noch nicht vollständig heruntergeladen: Seite 3")

    first = onenote.sync(lister=make_sections, exporter=not_loaded)
    assert len(first["incomplete"]) == 2 and first["new"] == 0 and first["failed"] == []
    assert db.list_documents() == []
    assert calls == ["{A}", "{B}"]  # kein sofortiger zweiter Versuch

    second = onenote.sync(lister=make_sections, exporter=fake_exporter(calls))
    assert second["new"] == 2


def test_incomplete_update_keeps_the_old_pdf(monkeypatch, tmp_path):
    target = tmp_path / "abschnitt.pdf"
    target.write_bytes(b"alte Version")

    def run(*args, **kwargs):
        out = args[args.index("-OutFile") + 1]
        open(out, "wb").close()  # OneNote liefert eine leere Datei
        return ""

    monkeypatch.setattr(onenote, "_run", run)
    with pytest.raises(onenote.IncompleteSection):
        onenote.export_section("{A}", target)
    assert target.read_bytes() == b"alte Version"
    assert not (tmp_path / "abschnitt.neu.pdf").exists()


def test_complete_export_replaces_the_pdf(monkeypatch, tmp_path):
    import pymupdf

    target = tmp_path / "abschnitt.pdf"
    target.write_bytes(b"alte Version")

    def run(*args, **kwargs):
        pdf = pymupdf.open()
        pdf.new_page()
        pdf.save(args[args.index("-OutFile") + 1])
        return "PAGES=1"

    monkeypatch.setattr(onenote, "_run", run)
    onenote.export_section("{A}", target)
    assert target.read_bytes().startswith(b"%PDF")


def test_force_exports_everything_again():
    calls = []
    onenote.sync(lister=make_sections, exporter=fake_exporter(calls))
    calls.clear()
    result = onenote.sync(lister=make_sections, exporter=fake_exporter(calls), force=True)
    assert result["updated"] == 2 and calls == ["{A}", "{B}"]


def test_stuck_onenote_stops_the_sync_with_a_clear_message():
    calls = []

    def stuck(section_id, target):
        calls.append(section_id)
        raise onenote.OneNoteStuck(onenote.STUCK_MESSAGE)

    with pytest.raises(onenote.OneNoteStuck, match="Drucker"):
        onenote.sync(lister=make_sections, exporter=stuck)
    assert calls == ["{A}"]  # nicht alle Abschnitte einzeln abwarten


def test_export_timeout_becomes_stuck_error(monkeypatch, tmp_path):
    import subprocess

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired("powershell", 180)

    monkeypatch.setattr(onenote.subprocess, "run", hang)
    with pytest.raises(onenote.OneNoteStuck):
        onenote.export_section("{A}", tmp_path / "a.pdf")
