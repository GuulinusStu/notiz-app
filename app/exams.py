"""Prüfungen: Lernziele, passende Notizen dazu und ein Lernplan bis zum Prüfungstag."""
import datetime
import re

from . import db, study

MIN_SCORE = 0.3  # ab dieser Ähnlichkeit gilt eine Notiz als passend zu einem Lernziel
DOCS_PER_GOAL = 3
WORDS_PER_GOAL = 900

_BULLET = re.compile(r"^\s*(?:[-*•–·▪]|\d+[.)]|[a-zA-Z][.)]|\[[ xX]?\])\s*")


def parse_goals(text: str) -> list[str]:
    """Eine Zeile pro Lernziel; Aufzählungszeichen und Nummern werden entfernt."""
    goals = []
    for line in (text or "").splitlines():
        line = _BULLET.sub("", line).strip()
        if len(line) >= 3 and line not in goals:
            goals.append(line)
    return goals


def merge_goals(old: list[dict], texts: list[str]) -> list[dict]:
    """Neue Lernziel-Liste; schon abgehakte Ziele bleiben abgehakt."""
    done = {g["text"]: g.get("done", False) for g in old}
    return [{"text": t, "done": done.get(t, False)} for t in texts]


def days_left(exam: dict, today: datetime.date | None = None) -> int:
    today = today or datetime.date.today()
    return (datetime.date.fromisoformat(exam["date"]) - today).days


def summary(exam: dict, today: datetime.date | None = None) -> dict:
    goals = exam["goals"]
    return {**exam, "days_left": days_left(exam, today),
            "goals_total": len(goals), "goals_done": sum(1 for g in goals if g.get("done"))}


def materials(exam: dict) -> list[dict]:
    """Für jedes Lernziel die passenden Notizen (beste Stelle pro Notiz) aus dem Fach der Prüfung."""
    result = []
    for goal in exam["goals"]:
        best: dict[int, dict] = {}
        for hit in study.search(goal["text"], k=12, subject=exam["subject"]):
            if hit["score"] >= MIN_SCORE and hit["doc_id"] not in best:
                best[hit["doc_id"]] = hit
        docs = [{"doc_id": h["doc_id"], "title": h["title"], "score": round(h["score"], 2),
                 "snippet": h["text"][:200]} for h in list(best.values())[:DOCS_PER_GOAL]]
        result.append({**goal, "docs": docs})
    return result


def excerpts(exam: dict, only_open: bool = False, max_words: int = study.MAX_STUDY_WORDS) -> list[dict]:
    """Die zu den Lernzielen passenden Stellen, für Quiz und Karteikarten. Ohne Lernziele: das ganze Fach."""
    goals = [g for g in exam["goals"] if not (only_open and g.get("done"))] or exam["goals"]
    if not goals:
        return study.subject_excerpts(exam["subject"], max_words)
    per_goal = max(150, min(WORDS_PER_GOAL, max_words // len(goals)))
    picked, seen, words = [], set(), 0
    for goal in goals:
        used = 0
        for hit in study.search(goal["text"], k=8, subject=exam["subject"]):
            key = (hit["doc_id"], hit["text"][:80])
            if hit["score"] < MIN_SCORE or key in seen:
                continue
            n = len(hit["text"].split())
            if used + n > per_goal or words + n > max_words:
                break
            seen.add(key)
            picked.append({**hit, "goal": goal["text"]})
            used += n
            words += n
    return picked


def plan(exam: dict, today: datetime.date | None = None) -> list[dict]:
    """Verteilt die offenen Lernziele auf die Tage bis zur Prüfung. Der letzte Tag ist für die Wiederholung."""
    today = today or datetime.date.today()
    exam_day = datetime.date.fromisoformat(exam["date"])
    days = (exam_day - today).days
    if days <= 0:
        return []
    open_goals = [g["text"] for g in exam["goals"] if not g.get("done")]
    study_days = [today + datetime.timedelta(days=i) for i in range(days)]
    review_day = study_days.pop() if len(study_days) > 1 or not open_goals else None
    entries = {d: [] for d in study_days}
    if open_goals and study_days:
        # Ziele gleichmässig verteilen; bei mehr Tagen als Zielen wird wiederholt.
        rounds = max(1, len(study_days) // len(open_goals))
        sequence = open_goals * rounds
        for i, goal in enumerate(sequence):
            day = study_days[min(len(study_days) - 1, i * len(study_days) // len(sequence))]
            if goal not in entries[day]:
                entries[day].append(goal)
    result = [{"date": d.isoformat(), "goals": g, "task": "Lernziele lernen, dann Karteikarten" if g
               else "Karteikarten wiederholen"} for d, g in entries.items()]
    if review_day:
        result.append({"date": review_day.isoformat(), "goals": [], "task": "Gesamtwiederholung: Quiz über alle Lernziele"})
    return result


def ics(exams: list[dict]) -> str:
    """Kalenderdatei, damit die Prüfungen auch im Handy-Kalender erscheinen."""
    def esc(s: str) -> str:
        for a, b in (("\\", "\\\\"), (";", "\\;"), (",", "\\,"), ("\n", "\\n")):
            s = s.replace(a, b)
        return s

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Notiz-App//Pruefungen//DE"]
    for e in exams:
        day = datetime.date.fromisoformat(e["date"])
        goals = "\n".join("- " + g["text"] for g in e["goals"])
        title = f"Prüfung {e['subject']}: {e['title']}" if e["subject"] else f"Prüfung: {e['title']}"
        lines += ["BEGIN:VEVENT", f"UID:pruefung-{e['id']}@notiz-app", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{day + datetime.timedelta(days=1):%Y%m%d}",
                  "SUMMARY:" + esc(title)]
        if goals:
            lines.append("DESCRIPTION:" + esc("Lernziele:\n" + goals))
        lines += ["BEGIN:VALARM", "TRIGGER:-P2D", "ACTION:DISPLAY", "DESCRIPTION:Prüfung in 2 Tagen", "END:VALARM",
                  "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
