"""Lernfunktionen: Fragen an die Notizen, Quiz und Karteikarten."""
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
{{"karten": [{{"vorne": "...", "hinten": "..."}}]}}
Verwende nur Inhalte aus dem Text."""

MAX_STUDY_WORDS = 5000


def search(query: str, k: int = 6) -> list[dict]:
    rows = db.all_chunks()
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


def answer(question: str) -> dict:
    hits = search(question)
    if not hits:
        return {"answer": "Ich habe noch keine Notizen, in denen ich nachschauen kann. "
                          "Lade zuerst PDFs oder Aufnahmen hoch.", "sources": []}
    context = "\n\n".join(f"[{i}] ({h['title']})\n{h['text']}" for i, h in enumerate(hits, start=1))
    text = llm.ask(ANSWER_PROMPT, f"Auszüge:\n{context}\n\nFrage: {question}", num_ctx=12288)
    return {"answer": text, "sources": [{"n": i, **h} for i, h in enumerate(hits, start=1)]}


def study_text(doc: dict) -> str:
    text = doc["clean_text"] or doc["raw_text"]
    words = text.split()
    return " ".join(words[:MAX_STUDY_WORDS])


def make_quiz(doc: dict, n: int = 5) -> list[dict]:
    data = llm.parse_json(llm.ask(QUIZ_PROMPT.format(n=n), study_text(doc), json_mode=True, num_ctx=12288))
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


def make_flashcards(doc: dict, n: int = 10) -> int:
    data = llm.parse_json(llm.ask(CARDS_PROMPT.format(n=n), study_text(doc), json_mode=True, num_ctx=12288))
    cards = [(c["vorne"].strip(), c["hinten"].strip())
             for c in (data.get("karten", []) if isinstance(data, dict) else [])
             if isinstance(c, dict) and isinstance(c.get("vorne"), str) and isinstance(c.get("hinten"), str)
             and c["vorne"].strip() and c["hinten"].strip()]
    if not cards:
        raise llm.LLMError("Die KI hat keine brauchbaren Karteikarten erstellt. Bitte nochmals versuchen.")
    return db.add_flashcards(doc["id"], cards)
