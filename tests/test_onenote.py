from app import db, onenote


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
    assert first == {"sections": 2, "new": 2, "updated": 0, "unchanged": 0, "failed": []}
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
