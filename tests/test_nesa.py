import datetime

import httpx
from fastapi.testclient import TestClient

from app import db, nesa
from app.main import app

TODAY = datetime.date(2026, 11, 2)

LOGIN_HTML = """<html><body><form action="index.php?pageid=1" method="post" name="login">
<input type="text" name="login" value=""><input type="password" name="passwort" value="">
<input type="hidden" name="loginhash" value="abc123"><input type="submit" value="Login">
</form></body></html>"""

START_HTML = """<html><body>
<h3>Ihre nächsten Tests</h3>
<table class="mdl-data-table">
<tr><th>Datum</th><th>Kurs</th><th>Thema</th></tr>
<tr><td>Mo, 10.11.2026</td><td>Mathematik-INP1a-26</td><td>Mengenlehre und Logik</td></tr>
<tr><td>13.11.2026</td><td>M114-INP1a</td><td>Codierung</td></tr>
<tr><td>01.10.2026</td><td>Englisch-INP1a</td><td>Vocabulary</td></tr>
</table>
<table><tr><th>Kurs</th><th>Note</th></tr><tr><td>Mathematik</td><td>5.5</td></tr></table>
</body></html>"""


def fake_schulnetz(start_html=START_HTML, password="geheim"):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("loginto.php"):
            return httpx.Response(200, html=LOGIN_HTML)
        if request.method == "POST":
            form = dict(httpx.QueryParams(request.content.decode()))
            seen["form"] = form
            if form.get("passwort") != password:
                return httpx.Response(200, html=LOGIN_HTML)
            return httpx.Response(200, html=start_html)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True), seen


def setup_credentials(password="geheim"):
    nesa.save_credentials("schulnetz.bbbaden.ch/loginto.php?mode=0", "sam.muster", password)


def test_base_url_is_normalized():
    assert nesa.normalize_base("schulnetz.bbbaden.ch") == "https://schulnetz.bbbaden.ch/"
    assert nesa.normalize_base("https://www.schul-netz.com/kszug/index.php?pageid=1") == "https://www.schul-netz.com/kszug/"


def test_password_is_not_stored_in_plain_text():
    setup_credentials()
    assert "geheim" not in db.get_setting("nesa_password")
    assert nesa.credentials() == ("https://schulnetz.bbbaden.ch/", "sam.muster", "geheim")


def test_find_exams_reads_upcoming_tests_only():
    assert nesa.find_exams(START_HTML, TODAY) == [
        {"date": "2026-11-10", "course": "Mathematik-INP1a-26", "title": "Mengenlehre und Logik"},
        {"date": "2026-11-13", "course": "M114-INP1a", "title": "Codierung"}]
    assert nesa.find_exams("<table><tr><td>nichts</td></tr></table>", TODAY) is None


def test_sync_logs_in_and_adds_exams_once(monkeypatch):
    db.add_subject("Mathe")
    setup_credentials()
    client, seen = fake_schulnetz()
    assert nesa.sync(client, TODAY) == {"found": 2, "added": 2, "moved": 0}
    assert seen["form"] == {"login": "sam.muster", "passwort": "geheim", "loginhash": "abc123"}
    exams = {e["title"]: e for e in db.list_exams()}
    assert exams["Mengenlehre und Logik"]["subject"] == "Mathe"  # Kurs passt zum vorhandenen Fach
    assert exams["Codierung"]["subject"] == "M114"

    # Nochmals holen: nichts doppelt, verschobene Prüfung bekommt das neue Datum
    moved = START_HTML.replace("13.11.2026", "20.11.2026")
    client, _ = fake_schulnetz(moved)
    assert nesa.sync(client, TODAY) == {"found": 2, "added": 0, "moved": 1}
    assert len(db.list_exams()) == 2
    assert {e["title"]: e["date"] for e in db.list_exams()}["Codierung"] == "2026-11-20"


def test_wrong_password_gives_clear_error():
    setup_credentials("falsch")
    client, _ = fake_schulnetz()
    try:
        nesa.sync(client, TODAY)
    except nesa.NesaError as e:
        assert "Benutzername und Passwort" in str(e)
    else:
        raise AssertionError("Fehler erwartet")
    assert db.list_exams() == []


def test_changed_subject_is_remembered_for_the_course():
    setup_credentials()
    client, _ = fake_schulnetz()
    nesa.sync(client, TODAY)
    exam = next(e for e in db.list_exams() if e["title"] == "Codierung")
    with TestClient(app) as c:
        assert c.patch(f"/api/exams/{exam['id']}", json={"subject": "Informatik"}).status_code == 200
    assert nesa.subject_for_course("M114-INP1a", []) == "Informatik"


def test_api_never_returns_the_password():
    with TestClient(app) as c:
        r = c.post("/api/nesa", json={"url": "schulnetz.bbbaden.ch", "user": "sam", "password": "geheim"})
        assert r.status_code == 200 and "geheim" not in r.text
        assert r.json()["configured"] is True
        r = c.post("/api/nesa", json={"user": "", "password": ""})
        assert r.json()["configured"] is False
        assert c.post("/api/nesa/sync").status_code == 400


def test_backup_leaves_out_credentials(tmp_path):
    setup_credentials()
    import sqlite3
    target = db.backup(tmp_path / "sicherung")
    keys = {r[0] for r in sqlite3.connect(target).execute("SELECT key FROM settings")}
    assert "nesa_password" not in keys and "nesa_user" not in keys
