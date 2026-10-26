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
              "raw_text", "clean_text", "summary", "error", "external_modified"}

# Spalten, die nach der ersten Version dazugekommen sind
MIGRATIONS = {"external_id": "TEXT", "external_modified": "TEXT"}


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
            "SELECT id, title, kind, subject, status, progress, error, created_at, external_id,"
            " source_path IS NOT NULL AS has_file,"
            " length(summary) > 0 AS has_summary FROM documents ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def documents_by_external_prefix(prefix: str) -> dict[str, dict]:
    with session() as c:
        rows = c.execute(
            "SELECT id, external_id, external_modified, status FROM documents WHERE external_id LIKE ?",
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


def delete_document(doc_id: int) -> dict | None:
    doc = get_document(doc_id)
    if doc:
        with session() as c:
            c.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    return doc


# --- Fächer -----------------------------------------------------------------

def list_subjects() -> list[dict]:
    with session() as c:
        rows = c.execute(
            "SELECT subject, count(*) AS count FROM documents GROUP BY subject ORDER BY subject = '', lower(subject)"
        ).fetchall()
        return [dict(r) for r in rows]


def rename_subject(old: str, new: str) -> int:
    """Benennt ein Fach um (oder legt zwei zusammen) und merkt sich das für künftige OneNote-Importe."""
    with session() as c:
        n = c.execute("UPDATE documents SET subject = ? WHERE subject = ?", (new, old)).rowcount
        c.execute("UPDATE subject_alias SET subject = ? WHERE subject = ?", (new, old))
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
