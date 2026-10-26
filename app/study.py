"""Lernfunktionen: Fragen an die Notizen, Quiz und Karteikarten."""
import random

import numpy as np

from . import db, llm

ANSWER_PROMPT = """Du bist ein geduldiger Lernhelfer. Beantworte die Frage auf Deutsch
nur mit Hilfe der nummerierten Auszüge aus den Notizen und Unterrichtsaufnahmen.
- Gib bei jeder Aussage die Quelle als [1], [2] usw. an.
- Wenn die Antwort nicht in den Auszügen steht, sag das ehrlich und rate nicht.
- Erkläre verständlich, wie für eine Prüfungsvorbereitung."""

QUIZ_PROMPT = """Erstelle aus dem Lernstoff {n} Multiple-Choice-Fragen auf Deutsch, die das Verständnis prüfen.
Antworte nur mit JSON in genau diesem Format:
{{"fragen": [{{"frage": "...", "optionen": ["A", "B", "C", "D"], "richtig": 0, "erklaerung": "..."}}]}}
"richtig" ist die Position (0 bis 3) der richtigen Option. Verwende nur Inhalte aus dem Text."""

CARDS_PROMPT = """Erstelle aus dem Lernstoff {n} Karteikarten auf Deutsch für die Prüfungsvorbereitung.
Vorderseite: eine klare Frage oder ein Begriff. Rückseite: eine kurze, korrekte Antwort.
Antworte nur mit JSON in genau diesem Format:
{{"karten": [{{"vorne": "...", "hinten": "...", "quelle": 1}}]}}
"quelle" ist die Nummer des Auszugs [n], aus dem die Karte stammt (nur falls der Text nummeriert ist).
Verwende nur Inhalte aus dem Text."""

MAX_STUDY_WORDS = 5000


def search(query: str, k: int = 6, subject: str | None = None) -> list[dict]:
    rows = db.all_chunks(subject)
    if not rows:
        return []
    q = llm.embed([query])[0]
    vectors = [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
    keep = [i for i, v in enumerate(vectors) if v.shape == q.shape]
    if not keep:
        return []
    mat = np.vstack([vectors[i] for i in keep])
    sims = mat @ q / (np.linalg.norm(mat, axis=1) * np.linalg.norm(q) + 1e-9)
    order = np.argsort(-sims)[:k]
    return [{**{key: rows[keep[i]][key] for key in ("doc_id", "title", "text")},
             "score": float(sims[i])} for i in order]


def answer(question: str, subject: str | None = None) -> dict:
    hits = search(question, subject=subject)
    if not hits:
        where = f"im Fach „{subject}“" if subject else ""
        return {"answer": f"Ich habe {where} noch keine Notizen, in denen ich nachschauen kann. "
                          "Lade zuerst Notizen hoch oder nimm eine Lektion auf.", "sources": []}
    context = "\n\n".join(f"[{i}] ({h['title']})\n{h['text']}" for i, h in enumerate(hits, start=1))
    text = llm.ask(ANSWER_PROMPT, f"Auszüge:\n{context}\n\nFrage: {question}")
    return {"answer": text, "sources": [{"n": i, **h} for i, h in enumerate(hits, start=1)]}


def study_text(doc: dict) -> str:
    text = doc["clean_text"] or doc["raw_text"]
    return " ".join(text.split()[:MAX_STUDY_WORDS])


def subject_excerpts(subject: str, max_words: int = MAX_STUDY_WORDS) -> list[dict]:
    """Zufällige Auszüge aus allen Notizen eines Fachs, damit ein Quiz das ganze Fach abdeckt."""
    chunks = db.all_chunks(subject)
    random.shuffle(chunks)
    picked, words = [], 0
    for chunk in chunks:
        n = len(chunk["text"].split())
        if picked and words + n > max_words:
            break
        picked.append(chunk)
        words += n
    return picked


def _numbered(excerpts: list[dict]) -> str:
    return "\n\n".join(f"[{i}] ({e['title']})\n{e['text']}" for i, e in enumerate(excerpts, start=1))


def _source_text(doc: dict | None, subject: str | None) -> tuple[str, list[dict]]:
    if doc is not None:
        return study_text(doc), []
    excerpts = subject_excerpts(subject or "")
    if not excerpts:
        raise llm.LLMError("In diesem Fach gibt es noch keine fertig verarbeiteten Notizen.")
    return _numbered(excerpts), excerpts


def make_quiz(doc: dict | None = None, n: int = 5, subject: str | None = None) -> list[dict]:
    text, _ = _source_text(doc, subject)
    data = llm.parse_json(llm.ask(QUIZ_PROMPT.format(n=n), text, json_mode=True))
    questions = []
    for q in data.get("fragen", []) if isinstance(data, dict) else []:
        options = q.get("optionen")
        right = q.get("richtig")
        if (isinstance(q.get("frage"), str) and isinstance(options, list) and len(options) >= 2
                and isinstance(right, int) and 0 <= right < len(options)):
            questions.append({"frage": q["frage"], "optionen": [str(o) for o in options],
                              "richtig": right, "erklaerung": str(q.get("erklaerung", ""))})
    if not questions:
        raise llm.LLMError("Die KI hat keine brauchbaren Fragen erstellt. Bitte nochmals versuchen.")
    return questions


def make_flashcards(doc: dict | None = None, n: int = 10, subject: str | None = None) -> int:
    text, excerpts = _source_text(doc, subject)
    data = llm.parse_json(llm.ask(CARDS_PROMPT.format(n=n), text, json_mode=True))
    created = 0
    for c in data.get("karten", []) if isinstance(data, dict) else []:
        if not (isinstance(c, dict) and isinstance(c.get("vorne"), str) and isinstance(c.get("hinten"), str)):
            continue
        front, back = c["vorne"].strip(), c["hinten"].strip()
        if not front or not back:
            continue
        if doc is not None:
            doc_id = doc["id"]
        else:
            src = c.get("quelle")
            ok = isinstance(src, int) and 1 <= src <= len(excerpts)
            doc_id = excerpts[src - 1]["doc_id"] if ok else excerpts[0]["doc_id"]
        created += db.add_flashcards(doc_id, [(front, back)])
    if not created:
        raise llm.LLMError("Die KI hat keine brauchbaren Karteikarten erstellt. Bitte nochmals versuchen.")
    return created
