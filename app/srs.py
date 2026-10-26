"""Wiederholungsplan für Karteikarten (vereinfachtes SM-2, wie bei Anki)."""
import time

DAY = 86400
AGAIN, HARD, GOOD, EASY = 0, 1, 2, 3


def schedule(card: dict, grade: int, now: float | None = None) -> dict:
    if grade not in (AGAIN, HARD, GOOD, EASY):
        raise ValueError("grade muss 0 bis 3 sein")
    now = time.time() if now is None else now
    ease = card["ease"]
    interval = card["interval_days"]
    reps = card["reps"]

    if grade == AGAIN:
        return {"reps": 0, "interval_days": 0.0, "ease": max(1.3, ease - 0.2), "due": now + 10 * 60}

    reps += 1
    if reps == 1:
        interval = 1.0
    elif reps == 2:
        interval = 3.0
    else:
        interval = interval * ease

    if grade == HARD:
        interval = max(1.0, interval * 0.6)
        ease = max(1.3, ease - 0.15)
    elif grade == EASY:
        interval *= 1.3
        ease += 0.15

    return {"reps": reps, "interval_days": round(interval, 2), "ease": round(ease, 2),
            "due": now + interval * DAY}
