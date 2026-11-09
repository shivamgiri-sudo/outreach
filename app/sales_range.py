"""Sales funnel, part 2: the From Date / To Date rules (cohort basis) and the quick presets.

Leads created between From 00:00 and To 23:59 (India time) form the cohort; a lead counts in a stage only if that stage's
date is on or before To 23:59. No dates means "As on <today>" (every lead to date).
"""
import re
from datetime import datetime, timedelta
from typing import Optional

from .sales_model import day_end, day_start, ist_day

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
PRESETS = ("today", "week", "month", "lastMonth", "quarter", "fy")


def label(ymd: str) -> str:
    """'2026-11-09' -> '09-Nov-2026'"""
    y, m, d = ymd.split("-")
    return f"{d}-{MONTHS[int(m) - 1]}-{y}"


def add_days(ymd: str, n: int) -> str:
    return (datetime.strptime(ymd, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")


def days_between(a: str, b: str) -> int:
    return (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days


def resolve_range(date_from: str, date_to: str, earliest: Optional[datetime], now: datetime) -> dict:
    """{"error"} or {from, to, label, start, end, chosen_from, chosen_to}. `earliest`: the earliest Created Date."""
    today = ist_day(now)
    valid = lambda v: bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v or ""))
    for v in (date_from, date_to):
        if v:
            try:
                datetime.strptime(v, "%Y-%m-%d")
            except ValueError:
                return {"error": "Pick dates with the date pickers."}
    if (date_from and not valid(date_from)) or (date_to and not valid(date_to)):
        return {"error": "Pick dates with the date pickers."}
    if (date_from and date_from > today) or (date_to and date_to > today):
        return {"error": "Future dates cannot be picked."}
    if date_from and date_to and date_from > date_to:
        return {"error": "From Date cannot be after To Date"}
    first = ist_day(earliest) if earliest else today
    f, t = date_from or first, date_to or today
    if not date_from and not date_to:
        text = "As on " + label(today)
    elif date_from and not date_to:
        text = "From " + label(date_from) + " to today"
    elif date_to and not date_from:
        text = "As on " + label(date_to)
    else:
        text = label(date_from) + " to " + label(date_to)
    if f > t:
        f = t                                              # earliest lead created after the chosen To Date: an empty range
    return {"from": f, "to": t, "label": text, "start": day_start(f), "end": day_end(t),
            "chosen_from": bool(date_from), "chosen_to": bool(date_to)}


def previous_range(r: dict) -> dict:
    """The period of equal length that ends the day before From (1-30 Nov -> 2-31 Oct)."""
    length = days_between(r["from"], r["to"]) + 1
    to = add_days(r["from"], -1)
    start = add_days(to, -(length - 1))
    return {"from": start, "to": to, "start": day_start(start), "end": day_end(to)}


def preset(name: str, now: datetime) -> dict:
    """{from, to} for a quick preset; the financial year runs April to March; the week starts on Monday."""
    today = ist_day(now)
    y, m = int(today[:4]), int(today[5:7])
    dow = datetime.strptime(today, "%Y-%m-%d").weekday()          # Monday = 0
    if name == "today":
        return {"from": today, "to": today}
    if name == "week":
        return {"from": add_days(today, -dow), "to": today}
    if name == "month":
        return {"from": today[:8] + "01", "to": today}
    if name == "lastMonth":
        ly, lm = (y - 1, 12) if m == 1 else (y, m - 1)
        return {"from": f"{ly}-{lm:02d}-01", "to": add_days(f"{y}-{m:02d}-01", -1)}
    if name == "quarter":
        qm = (m - 1) // 3 * 3 + 1
        return {"from": f"{y}-{qm:02d}-01", "to": today}
    if name == "fy":
        return {"from": f"{y if m >= 4 else y - 1}-04-01", "to": today}
    return {"from": "", "to": ""}
