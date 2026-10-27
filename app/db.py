"""SQLite-Speicher für Dokumente, Textabschnitte (mit Embeddings) und Karteikarten."""
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  kind TEXT NOT NULL,                       -- pdf | lesson | text
  subject TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | processing | ready | error
  progress TEXT NOT NULL DEFAULT '',
  source_path TEXT,
  raw_text TEXT NOT NULL DEFAULT '',
  clean_text TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  external_id TEXT,                         -- z. B. onenote:<Abschnitt-ID>
  external_modified TEXT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  text TEXT NOT NULL,
  embedding BLOB
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS reviews (        -- Lernverlauf für Statistik und Lernserie
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  card_id INTEGER,
  subject TEXT NOT NULL DEFAULT '',
  grade INTEGER NOT NULL,
  ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS quiz_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subject TEXT NOT NULL DEFAULT '',
  score INTEGER NOT NULL,
  total INTEGER NOT NULL,
  ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subject_alias (  -- OneNote-Notizbuch -> Fach
  source TEXT PRIMARY KEY,
  subject TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_state (     -- z. B. leere OneNote-Abschnitte, die nichts zu importieren haben
  external_id TEXT PRIMARY KEY,
  modified TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exams (          -- Prüfungen im Kalender
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subject TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  date TEXT NOT NULL,                       -- JJJJ-MM-TT
  goals TEXT NOT NULL DEFAULT '[]',         -- JSON: [{"text": ..., "done": false}]
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS page_cache (     -- schon von der KI abgeschriebene Bildseiten
  key TEXT PRIMARY KEY,
  text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flashcards (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER REFERENCES documents(id) ON DELETE CASCADE,
  front TEXT NOT NULL,
  back TEXT NOT NULL,
  due REAL NOT NULL,
  interval_days REAL NOT NULL DEFAULT 0,
  ease REAL NOT NULL DEFAULT 2.5,
  reps INTEGER NOT NULL DEFAULT 0
);
"""

DOC_FIELDS = {"title", "subject", "status", "progress", "source_path",
              "raw_text", "clean_text", "summary", "error", "external_modified", "images_missing", "title_custom"}

# Spalten, die nach der ersten Version dazugekommen sind
MIGRATIONS = {"external_id": "TEXT", "external_modified": "TEXT",
              "images_missing": "INTEGER NOT NULL DEFAULT 0",
              "title_custom": "INTEGER NOT NULL DEFAULT 0"}  # 1 = selbst umbenannt, OneNote überschreibt den Titel nicht


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with session() as c:
        c.executescript(SCHEMA)
        existing = {r["name"] for r in c.execute("PRAGMA table_info(documents)")}
        for column, kind in MIGRATIONS.items():
            if column not in existing:
                c.execute(f"ALTER TABLE documents ADD COLUMN {column} {kind}")


# --- Dokumente ---------------------------------------------------------------

def create_document(title: str, kind: str, subject: str = "", source_path: str | None = None,
                    raw_text: str = "", external_id: str | None = None,
                    external_modified: str | None = None) -> int:
    with session() as c:
        cur = c.execute(
            "INSERT INTO documents (title, kind, subject, source_path, raw_text, external_id,"
            " external_modified, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title, kind, subject, source_path, raw_text, external_id, external_modified, time.time()),
        )
        return cur.lastrowid


def update_document(doc_id: int, **fields) -> None:
    unknown = set(fields) - DOC_FIELDS
    if unknown:
        raise ValueError(f"Unbekannte Felder: {unknown}")
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with session() as c:
        c.execute(f"UPDATE documents SET {cols} WHERE id = ?", (*fields.values(), doc_id))


def get_document(doc_id: int) -> dict | None:
    with session() as c:
        row = c.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return dict(row) if row else None


def list_documents() -> list[dict]:
    with session() as c:
        rows = c.execute(
            "SELECT id, title, kind, subject, status, progress, error, created_at, external_id, images_missing,"
            " source_path IS NOT NULL AS has_file,"
            " length(summary) > 0 AS has_summary FROM documents ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def documents_by_external_prefix(prefix: str) -> dict[str, dict]:
    with session() as c:
        rows = c.execute(
            "SELECT id, external_id, external_modified, status, title_custom FROM documents WHERE external_id LIKE ?",
            (prefix.replace("%", "") + "%",),
        )
        return {r["external_id"]: dict(r) for r in rows}


def sync_states(prefix: str) -> dict[str, str]:
    with session() as c:
        rows = c.execute("SELECT external_id, modified FROM sync_state WHERE external_id LIKE ?",
                         (prefix.replace("%", "") + "%",))
        return {r["external_id"]: r["modified"] for r in rows}


def set_sync_state(external_id: str, modified: str) -> None:
    with session() as c:
        c.execute("INSERT OR REPLACE INTO sync_state (external_id, modified) VALUES (?, ?)", (external_id, modified))


def ready_documents() -> list[int]:
    with session() as c:
        return [r["id"] for r in c.execute("SELECT id FROM documents WHERE status = 'ready' ORDER BY id")]


def documents_with_status(*statuses: str) -> list[int]:
    marks = ",".join("?" * len(statuses))
    with session() as c:
        rows = c.execute(f"SELECT id FROM documents WHERE status IN ({marks}) ORDER BY id", statuses)
        return [r["id"] for r in rows]


def documents_with_missing_images() -> list[int]:
    with session() as c:
        rows = c.execute("SELECT id FROM documents WHERE status = 'ready' AND images_missing > 0 ORDER BY id")
        return [r["id"] for r in rows]


def cached_page(key: str) -> str | None:
    with session() as c:
        row = c.execute("SELECT text FROM page_cache WHERE key = ?", (key,)).fetchone()
        return row["text"] if row else None


def cache_page(key: str, text: str) -> None:
    with session() as c:
        c.execute("INSERT OR REPLACE INTO page_cache (key, text) VALUES (?, ?)", (key, text))


def delete_document(doc_id: int) -> dict | None:
    doc = get_document(doc_id)
    if doc:
        with session() as c:
            c.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    return doc


# --- Fächer -----------------------------------------------------------------

def _json_setting(key: str) -> list:
    import json
    try:
        value = json.loads(get_setting(key, "[]"))
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _set_json_setting(key: str, value: list) -> None:
    import json
    set_setting(key, json.dumps(value, ensure_ascii=False))


def list_subjects() -> list[dict]:
    """Alle Fächer: aus Notizen, Prüfungen und selbst angelegte (auch leere), in der eigenen Reihenfolge."""
    with session() as c:
        counts = {r["subject"]: r["count"] for r in c.execute(
            "SELECT subject, count(*) AS count FROM documents GROUP BY subject")}
        for r in c.execute("SELECT DISTINCT subject FROM exams WHERE subject != ''"):
            counts.setdefault(r["subject"], 0)
    for name in _json_setting("extra_subjects"):
        counts.setdefault(name, 0)
    order = {name: i for i, name in enumerate(_json_setting("subject_order"))}
    names = sorted(counts, key=lambda n: (n == "", order.get(n, len(order)), n.lower()))
    return [{"subject": n, "count": counts[n]} for n in names]


def add_subject(name: str) -> None:
    extra = _json_setting("extra_subjects")
    if name not in extra:
        _set_json_setting("extra_subjects", extra + [name])


def remove_subject(name: str) -> bool:
    """Entfernt ein leeres Fach. False, wenn noch Notizen oder Prüfungen drin sind."""
    with session() as c:
        used = c.execute("SELECT (SELECT count(*) FROM documents WHERE subject = ?)"
                         " + (SELECT count(*) FROM exams WHERE subject = ?)", (name, name)).fetchone()[0]
    if used:
        return False
    _set_json_setting("extra_subjects", [n for n in _json_setting("extra_subjects") if n != name])
    _set_json_setting("subject_order", [n for n in _json_setting("subject_order") if n != name])
    return True


def set_subject_order(names: list[str]) -> None:
    _set_json_setting("subject_order", names)


def move_documents(doc_ids: list[int], subject: str) -> int:
    if not doc_ids:
        return 0
    with session() as c:
        marks = ",".join("?" * len(doc_ids))
        return c.execute(f"UPDATE documents SET subject = ? WHERE id IN ({marks})", (subject, *doc_ids)).rowcount


def rename_subject(old: str, new: str) -> int:
    """Benennt ein Fach um (oder legt zwei zusammen) und merkt sich das für künftige OneNote-Importe."""
    with session() as c:
        n = c.execute("UPDATE documents SET subject = ? WHERE subject = ?", (new, old)).rowcount
        c.execute("UPDATE subject_alias SET subject = ? WHERE subject = ?", (new, old))
        c.execute("UPDATE exams SET subject = ? WHERE subject = ?", (new, old))
    for key in ("extra_subjects", "subject_order"):
        names = [new if n == old else n for n in _json_setting(key)]
        _set_json_setting(key, list(dict.fromkeys(names)))  # doppelte (zusammengelegt) entfernen
    with session() as c:
        c.execute("INSERT OR REPLACE INTO subject_alias (source, subject) VALUES (?, ?)", (old, new))
        c.execute("DELETE FROM subject_alias WHERE source = subject")
        return n


def subject_for(source: str) -> str:
    with session() as c:
        row = c.execute("SELECT subject FROM subject_alias WHERE source = ?", (source,)).fetchone()
        return row["subject"] if row else source


# --- Einstellungen -----------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    with session() as c:
        row = c.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with session() as c:
        c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


# --- Textabschnitte ----------------------------------------------------------

def replace_chunks(doc_id: int, chunks: list[tuple[str, bytes | None]]) -> None:
    with session() as c:
        c.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
        c.executemany(
            "INSERT INTO chunks (doc_id, idx, text, embedding) VALUES (?, ?, ?, ?)",
            [(doc_id, i, text, emb) for i, (text, emb) in enumerate(chunks)],
        )


def all_chunks(subject: str | None = None) -> list[dict]:
    sql = ("SELECT chunks.id, chunks.doc_id, chunks.text, chunks.embedding, documents.title, documents.subject"
           " FROM chunks JOIN documents ON documents.id = chunks.doc_id WHERE chunks.embedding IS NOT NULL")
    args: tuple = ()
    if subject is not None:
        sql += " AND documents.subject = ?"
        args = (subject,)
    with session() as c:
        rows = c.execute(sql, args).fetchall()
        return [dict(r) for r in rows]


# --- Karteikarten ------------------------------------------------------------

def add_flashcards(doc_id: int | None, cards: list[tuple[str, str]]) -> int:
    now = time.time()
    with session() as c:
        c.executemany(
            "INSERT INTO flashcards (doc_id, front, back, due) VALUES (?, ?, ?, ?)",
            [(doc_id, f, b, now) for f, b in cards],
        )
    return len(cards)


def due_flashcards(limit: int = 50, subject: str | None = None) -> list[dict]:
    sql = ("SELECT flashcards.*, documents.title AS doc_title, documents.subject AS subject FROM flashcards"
           " LEFT JOIN documents ON documents.id = flashcards.doc_id WHERE due <= ?")
    args: list = [time.time()]
    if subject is not None:
        sql += " AND documents.subject = ?"
        args.append(subject)
    with session() as c:
        rows = c.execute(sql + " ORDER BY due LIMIT ?", (*args, limit)).fetchall()
        return [dict(r) for r in rows]


def flashcard_counts() -> dict:
    with session() as c:
        total = c.execute("SELECT count(*) FROM flashcards").fetchone()[0]
        due = c.execute("SELECT count(*) FROM flashcards WHERE due <= ?", (time.time(),)).fetchone()[0]
        per_subject = c.execute(
            "SELECT documents.subject, count(*) FROM flashcards JOIN documents ON documents.id = flashcards.doc_id"
            " WHERE due <= ? GROUP BY documents.subject", (time.time(),)).fetchall()
        return {"total": total, "due": due, "due_by_subject": {r[0]: r[1] for r in per_subject}}


def get_flashcard(card_id: int) -> dict | None:
    with session() as c:
        row = c.execute("SELECT * FROM flashcards WHERE id = ?", (card_id,)).fetchone()
        return dict(row) if row else None


def update_flashcard(card_id: int, due: float, interval_days: float, ease: float, reps: int) -> None:
    with session() as c:
        c.execute(
            "UPDATE flashcards SET due = ?, interval_days = ?, ease = ?, reps = ? WHERE id = ?",
            (due, interval_days, ease, reps, card_id),
        )


def list_flashcards(subject: str | None = None) -> list[dict]:
    sql = ("SELECT flashcards.*, documents.title AS doc_title, documents.subject AS subject FROM flashcards"
           " LEFT JOIN documents ON documents.id = flashcards.doc_id")
    args: tuple = ()
    if subject is not None:
        sql += " WHERE documents.subject = ?"
        args = (subject,)
    with session() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY flashcards.id DESC", args)]


def edit_flashcard(card_id: int, front: str, back: str) -> None:
    with session() as c:
        c.execute("UPDATE flashcards SET front = ?, back = ? WHERE id = ?", (front, back, card_id))


def delete_flashcard(card_id: int) -> None:
    with session() as c:
        c.execute("DELETE FROM flashcards WHERE id = ?", (card_id,))


# --- Lernverlauf -------------------------------------------------------------

def log_review(card_id: int, subject: str, grade: int) -> None:
    with session() as c:
        c.execute("INSERT INTO reviews (card_id, subject, grade, ts) VALUES (?, ?, ?, ?)",
                  (card_id, subject or "", grade, time.time()))


def log_quiz(subject: str, score: int, total: int) -> None:
    with session() as c:
        c.execute("INSERT INTO quiz_results (subject, score, total, ts) VALUES (?, ?, ?, ?)",
                  (subject or "", score, total, time.time()))


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def streak_days(timestamps: list[float], now: float | None = None) -> int:
    """Wie viele Tage in Folge (bis heute oder gestern) gelernt wurde."""
    now = time.time() if now is None else now
    days = {_day(t) for t in timestamps}
    day = now if _day(now) in days else now - 86400
    count = 0
    while _day(day) in days:
        count += 1
        day -= 86400
    return count


def stats(subject: str | None = None) -> dict:
    where, args = ("", ()) if subject is None else (" WHERE subject = ?", (subject,))
    today_start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    with session() as c:
        docs = c.execute(f"SELECT kind, status, count(*) AS n FROM documents{where} GROUP BY kind, status", args).fetchall()
        learn_ts = [r[0] for r in c.execute(f"SELECT ts FROM reviews{where}", args)]
        learn_ts += [r[0] for r in c.execute(f"SELECT ts FROM quiz_results{where}", args)]
        reviews_today = c.execute(
            f"SELECT count(*) FROM reviews{where}{' AND' if where else ' WHERE'} ts >= ?", (*args, today_start)).fetchone()[0]
        quiz = c.execute(f"SELECT sum(score), sum(total) FROM quiz_results{where}", args).fetchone()
        recent = c.execute(
            f"SELECT id, title, kind, subject, status, created_at FROM documents{where}"
            " ORDER BY created_at DESC LIMIT 6", args).fetchall()
    due = due_flashcards(limit=10000, subject=subject)
    cards_total = len(list_flashcards(subject))
    by_kind = {}
    for r in docs:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + r["n"]
    return {
        "notes": sum(by_kind.values()),
        "lessons": by_kind.get("lesson", 0),
        "processing": sum(r["n"] for r in docs if r["status"] in ("pending", "processing")),
        "errors": sum(r["n"] for r in docs if r["status"] == "error"),
        "cards_total": cards_total,
        "cards_due": len(due),
        "reviews_today": reviews_today,
        "streak": streak_days(learn_ts),
        "quiz_percent": round(100 * quiz[0] / quiz[1]) if quiz[1] else None,
        "recent": [dict(r) for r in recent],
    }


# --- Prüfungen ---------------------------------------------------------------

def _exam(row) -> dict:
    import json
    return {**dict(row), "goals": json.loads(row["goals"] or "[]")}


def create_exam(subject: str, title: str, date: str, goals: list[dict]) -> int:
    import json
    with session() as c:
        cur = c.execute("INSERT INTO exams (subject, title, date, goals, created_at) VALUES (?, ?, ?, ?, ?)",
                        (subject, title, date, json.dumps(goals, ensure_ascii=False), time.time()))
        return cur.lastrowid


def update_exam(exam_id: int, **fields) -> None:
    import json
    if "goals" in fields:
        fields["goals"] = json.dumps(fields["goals"], ensure_ascii=False)
    fields = {k: v for k, v in fields.items() if k in {"subject", "title", "date", "goals"}}
    if not fields:
        return
    with session() as c:
        c.execute(f"UPDATE exams SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), exam_id))


def get_exam(exam_id: int) -> dict | None:
    with session() as c:
        row = c.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone()
        return _exam(row) if row else None


def list_exams(subject: str | None = None) -> list[dict]:
    with session() as c:
        if subject is None:
            rows = c.execute("SELECT * FROM exams ORDER BY date, id").fetchall()
        else:
            rows = c.execute("SELECT * FROM exams WHERE subject = ? ORDER BY date, id", (subject,)).fetchall()
        return [_exam(r) for r in rows]


def delete_exam(exam_id: int) -> None:
    with session() as c:
        c.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
