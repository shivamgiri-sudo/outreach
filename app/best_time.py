"""Best time to call: reach rate by IST hour of day (and weekday) from logged calls. Read-only."""
from datetime import datetime, timezone
from typing import Optional

from .sequences import IST

REACHED = ("connected", "interested", "meeting_booked")
MARKERS = ("invalid_lead", "consultant_lead")
MIN_TOTAL, MIN_BUCKET = 30, 5
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _local(raw) -> Optional[datetime]:
    try:
        w = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (w if w.tzinfo else w.replace(tzinfo=timezone.utc)).astimezone(IST)


def _rate(a: int, r: int) -> float:
    return round(r / a, 3) if a else 0.0


def compute(leads: list, activities: list) -> dict:
    ids = {l.get("id") for l in leads}
    hours = {h: [0, 0] for h in range(24)}
    days = {d: [0, 0] for d in range(7)}
    total = 0
    for a in activities:
        if a.get("type") != "call" or a.get("lead_id") not in ids or a.get("outcome") in MARKERS:
            continue
        when = _local(a.get("at"))
        if when is None:
            continue
        reached = a.get("outcome") in REACHED
        total += 1
        for bucket, key in ((hours, when.hour), (days, when.weekday())):
            bucket[key][0] += 1
            bucket[key][1] += reached
    enough = total >= MIN_TOTAL
    rows = [{"hour": h, "attempts": a, "reached": r, "rate": _rate(a, r)} for h, (a, r) in hours.items() if a]
    best, window = [], None
    ok = [row for row in rows if row["attempts"] >= MIN_BUCKET]
    top = max((row["rate"] for row in ok), default=0)
    if enough and top > 0:
        best = sorted(row["hour"] for row in ok if row["rate"] >= 0.9 * top)
        run, runs = [], []
        for h in best:
            if run and h == run[-1] + 1:
                run.append(h)
            else:
                run = [h]
                runs.append(run)
        longest = max(runs, key=len)
        window = {"from": longest[0], "to": longest[-1] + 1}
    week = [{"weekday": WEEKDAYS[d], "attempts": a, "reached": r, "rate": _rate(a, r)} for d, (a, r) in days.items() if a]
    return {"hours": rows, "weekdays": week, "best": best, "window": window, "sample": total, "enough": enough,
            "min_total": MIN_TOTAL, "min_per_hour": MIN_BUCKET}
