"""Prüfungen aus schulNetz (NESA) holen und im Prüfungskalender eintragen.

Die App meldet sich mit deinen Zugangsdaten bei schulNetz an, liest auf der Startseite die Tabelle
mit den nächsten Prüfungen und trägt neue Prüfungen ein. Verschobene Prüfungen bekommen das neue
Datum. Benutzername und Passwort bleiben auf diesem Laptop: Unter Windows wird das Passwort mit
dem Windows-Konto verschlüsselt (DPAPI) und es kommt nie in eine Sicherung.
"""
import base64
import datetime
import re
import sys
import threading
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from . import config, db

PREFIX = "nesa:"
LOGIN_PAGE = "loginto.php?mode=0&lang="
TIMEOUT = 30

_lock = threading.Lock()
_state = {"running": False, "message": "", "error": "", "result": None, "last": 0.0}


class NesaError(RuntimeError):
    pass


# --- Passwort verschlüsselt speichern ------------------------------------------------

def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in, blob_out = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise NesaError("Das Passwort konnte nicht ver- oder entschlüsselt werden. Bitte nochmals eintragen.")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def protect(password: str) -> str:
    data = password.encode()
    if sys.platform == "win32":
        return "dpapi:" + base64.b64encode(_dpapi(data, True)).decode()
    return "plain:" + base64.b64encode(data).decode()


def unprotect(stored: str) -> str:
    kind, _, value = stored.partition(":")
    raw = base64.b64decode(value)
    if kind == "dpapi":
        return _dpapi(raw, False).decode()
    return raw.decode()


def save_credentials(base_url: str | None, user: str | None, password: str | None) -> None:
    if base_url is not None:
        db.set_setting("nesa_url", normalize_base(base_url) if base_url.strip() else "")
    if user is not None:
        db.set_setting("nesa_user", user.strip())
    if password is not None:
        db.set_setting("nesa_password", protect(password) if password else "")


def credentials() -> tuple[str, str, str]:
    stored = db.get_setting("nesa_password")
    return db.get_setting("nesa_url"), db.get_setting("nesa_user"), unprotect(stored) if stored else ""


def configured() -> bool:
    return bool(db.get_setting("nesa_url") and db.get_setting("nesa_user") and db.get_setting("nesa_password"))


def normalize_base(url: str) -> str:
    """„schulnetz.bbbaden.ch/loginto.php?…“ → „https://schulnetz.bbbaden.ch/“"""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    parts = urlsplit(url)
    path = parts.path
    if path.endswith(".php"):
        path = path.rsplit("/", 1)[0]
    if not path.endswith("/"):
        path += "/"
    return urlunsplit(("https", parts.netloc, path, "", ""))


# --- HTML lesen (ohne zusätzliche Bibliothek) ----------------------------------------

class _PageParser(HTMLParser):
    """Sammelt Formulare (mit Eingabefeldern) und Tabellen (als Zeilen mit Zelltexten)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self.tables: list[list[list[str]]] = []
        self._form = None
        self._tables: list[list[list[str]]] = []  # offene (auch verschachtelte) Tabellen
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "form":
            self._form = {"action": a.get("action", ""), "method": a.get("method", "get").lower(), "inputs": []}
            self.forms.append(self._form)
        elif tag == "input" and self._form is not None:
            self._form["inputs"].append({"name": a.get("name", ""), "type": a.get("type", "text").lower(),
                                         "value": a.get("value", "")})
        elif tag == "table":
            self._tables.append([])
        elif tag == "tr" and self._tables:
            self._tables[-1].append([])
        elif tag in ("td", "th") and self._tables:
            if not self._tables[-1]:
                self._tables[-1].append([])
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        elif tag in ("td", "th") and self._cell is not None and self._tables:
            self._tables[-1][-1].append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "table" and self._tables:
            self.tables.append(self._tables.pop())

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def parse(html: str) -> _PageParser:
    p = _PageParser()
    p.feed(html)
    p.close()
    return p


def _login_form(page: _PageParser) -> dict | None:
    return next((f for f in page.forms if any(i["type"] == "password" for i in f["inputs"])), None)


# --- Prüfungen auf der Startseite finden ---------------------------------------------

_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})")
DATE_WORDS = ("datum", "date")
COURSE_WORDS = ("kurs", "fach", "modul", "lektion")
TOPIC_WORDS = ("thema", "bezeichnung", "titel", "prüfung", "pruefung", "test", "inhalt", "beschreibung")


def _column(header: list[str], words: tuple[str, ...], skip: set[int]) -> int | None:
    for i, h in enumerate(header):
        if i not in skip and any(w in h.lower() for w in words):
            return i
    return None


def parse_date(text: str) -> datetime.date | None:
    m = _DATE.search(text or "")
    if not m:
        return None
    day, month, year = (int(x) for x in m.groups())
    if year < 100:
        year += 2000
    try:
        return datetime.date(year, month, day)
    except ValueError:
        return None


def find_exams(html: str, today: datetime.date | None = None) -> list[dict] | None:
    """Kommende Prüfungen aus den Tabellen einer schulNetz-Seite. None, wenn es keine passende Tabelle gibt."""
    today = today or datetime.date.today()
    found, any_table = [], False
    for rows in parse(html).tables:
        rows = [r for r in rows if any(c.strip() for c in r)]
        for h, header in enumerate(rows[:3]):
            date_col = _column(header, DATE_WORDS, set())
            if date_col is None:
                continue
            topic_col = _column(header, TOPIC_WORDS, {date_col})
            if topic_col is None:
                continue
            course_col = _column(header, COURSE_WORDS, {date_col, topic_col})
            any_table = True
            for row in rows[h + 1:]:
                if len(row) <= max(date_col, topic_col):
                    continue
                day = parse_date(row[date_col])
                if not day or day < today:
                    continue
                course = row[course_col].strip() if course_col is not None and course_col < len(row) else ""
                title = row[topic_col].strip() or "Prüfung"
                item = {"date": day.isoformat(), "course": course, "title": title}
                if item not in found:
                    found.append(item)
            break
    return found if any_table else None


# --- Kurs → Fach ---------------------------------------------------------------------

def _norm(s: str) -> str:
    s = s.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("é", "e"), ("è", "e")):
        s = s.replace(a, b)
    return s


def subject_for_course(course: str, subjects: list[str]) -> str:
    """Passt den schulNetz-Kurs (z. B. „Mathematik-INP1a-26“) einem vorhandenen Fach zu."""
    if not course:
        return ""
    alias = db.subject_for(PREFIX + course)
    if alias != PREFIX + course:
        return alias
    tokens = [t for t in re.split(r"[^a-z0-9]+", _norm(course)) if t]
    best, best_len = "", 0
    for name in subjects:
        words = [w for w in re.split(r"[^a-z0-9]+", _norm(name)) if len(w) >= 3]
        if not words:
            continue
        word = words[0]
        for t in tokens:
            if t.startswith(word) or (len(t) >= 4 and word.startswith(t)):
                if len(word) > best_len:
                    best, best_len = name, len(word)
    if best:
        return best
    first = re.split(r"[-_ ]+", course.strip())[0] or course.strip()
    return db.subject_for(first)


def external_id(course: str, title: str) -> str:
    return f"{PREFIX}{course}|{title}"


def course_of(exam: dict) -> str | None:
    ext = exam.get("external_id") or ""
    return ext[len(PREFIX):].split("|", 1)[0] if ext.startswith(PREFIX) else None


def remember_subject(exam: dict, subject: str) -> None:
    """Wenn du das Fach einer geholten Prüfung änderst, kommen künftige Prüfungen dieses Kurses auch dorthin."""
    course = course_of(exam)
    if course and subject and subject != exam.get("subject"):
        db.set_subject_alias(PREFIX + course, subject)


def store(items: list[dict]) -> dict:
    """Trägt neue Prüfungen ein und verschiebt bekannte auf das neue Datum."""
    subjects = [s["subject"] for s in db.list_subjects() if s["subject"]]
    added, moved = [], []
    for item in items:
        ext = external_id(item["course"], item["title"])
        exam = db.exam_by_external_id(ext)
        if exam is None:
            subject = subject_for_course(item["course"], subjects)
            db.create_exam(subject, item["title"], item["date"], [], external_id=ext)
            added.append(item)
            if subject and subject not in subjects:
                subjects.append(subject)
        elif exam["date"] != item["date"]:
            db.update_exam(exam["id"], date=item["date"])
            moved.append(item)
    return {"found": len(items), "added": len(added), "moved": len(moved)}


# --- Anmelden und holen ------------------------------------------------------------------

def fetch_start_page(base: str, user: str, password: str, client: httpx.Client | None = None) -> str:
    own = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=TIMEOUT,
                                    headers={"User-Agent": "Mozilla/5.0 (Notiz-App)"})
    try:
        try:
            r = client.get(urljoin(base, LOGIN_PAGE))
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise NesaError(f"schulNetz ist nicht erreichbar ({base}). Stimmt die Adresse? ({e})")
        form = _login_form(parse(r.text))
        if form is None:
            raise NesaError("Auf der schulNetz-Seite wurde kein Anmeldeformular mit Passwort gefunden. "
                            "Falls deine Schule die Anmeldung über Microsoft macht, geht der automatische Import nicht.")
        data, user_set = {}, False
        for i in form["inputs"]:
            if not i["name"] or i["type"] in ("submit", "button", "image", "checkbox", "radio"):
                continue
            if i["type"] == "password":
                data[i["name"]] = password
            elif i["type"] in ("text", "email") and not user_set:
                data[i["name"]] = user
                user_set = True
            else:
                data[i["name"]] = i["value"]
        target = urljoin(str(r.url), form["action"] or str(r.url))
        try:
            r = client.post(target, data=data) if form["method"] == "post" else client.get(target, params=data)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise NesaError(f"Anmeldung bei schulNetz fehlgeschlagen ({e}).")
        if _login_form(parse(r.text)) is not None:
            raise NesaError("Anmeldung bei schulNetz fehlgeschlagen. Stimmen Benutzername und Passwort?")
        return r.text
    finally:
        if own:
            client.close()


def sync(client: httpx.Client | None = None, today: datetime.date | None = None) -> dict:
    base, user, password = credentials()
    if not (base and user and password):
        raise NesaError("schulNetz ist noch nicht eingerichtet. Trag Adresse, Benutzername und Passwort in den Einstellungen ein.")
    html = fetch_start_page(base, user, password, client)
    items = find_exams(html, today)
    if items is None:
        # Zum Nachbessern: die Startseite lokal ablegen (bleibt auf dem Laptop, ohne Passwort)
        (config.DATA_DIR / "schulnetz-startseite.html").write_text(html, encoding="utf-8")
        raise NesaError("Angemeldet, aber auf der schulNetz-Startseite keine Prüfungstabelle gefunden. "
                        "Die Seite liegt zum Prüfen unter data/schulnetz-startseite.html.")
    return store(items)


def _message(result: dict) -> str:
    if not result["found"]:
        return "Keine kommenden Prüfungen in schulNetz."
    parts = [f"{result['found']} Prüfung{'en' if result['found'] != 1 else ''} in schulNetz"]
    if result["added"]:
        parts.append(f"{result['added']} neu eingetragen")
    if result["moved"]:
        parts.append(f"{result['moved']} verschoben")
    if not result["added"] and not result["moved"]:
        parts.append("alles schon eingetragen")
    return ", ".join(parts) + "."


def run_sync() -> dict:
    with _lock:
        if _state["running"]:
            return status()
        _state.update(running=True, error="", message="Hole Prüfungen aus schulNetz…")
    try:
        result = sync()
        _state.update(result=result, message=_message(result), error="")
        db.set_setting("nesa_last", f"{time.time():.0f}")
    except NesaError as e:
        _state.update(error=str(e), message="")
    except Exception as e:  # unerwartet, aber die App soll weiterlaufen
        _state.update(error=f"Unerwarteter Fehler beim schulNetz-Import: {e}", message="")
    finally:
        _state["running"] = False
    return status()


def start_sync() -> None:
    threading.Thread(target=run_sync, name="notizapp-nesa", daemon=True).start()


def start_scheduler(interval: float = 6 * 3600) -> None:
    """Holt die Prüfungen gleich und dann alle paar Stunden, solange schulNetz eingerichtet ist."""
    def loop():
        while True:
            if configured():
                run_sync()
            time.sleep(interval)

    threading.Thread(target=loop, name="notizapp-nesa-auto", daemon=True).start()


def status() -> dict:
    last = db.get_setting("nesa_last")
    return {"configured": configured(), "url": db.get_setting("nesa_url"), "user": db.get_setting("nesa_user"),
            "password_set": bool(db.get_setting("nesa_password")), "running": _state["running"],
            "message": _state["message"], "error": _state["error"], "last": float(last) if last else None}
