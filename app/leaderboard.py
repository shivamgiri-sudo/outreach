"""Targets and a leaderboard per caller, composed from what already exists: call_log.collect (de-duplicated attempts),
rep_workflow targets (settings.rep_targets, the same ones My day edits) and rep_workflow.CONNECTED.

Definitions
- A caller is the person who LOGGED the call (`by`). Meetings booked and 'interested' outcomes are credited to that person.
- calls = call attempts. Marker outcomes (invalid_lead, consultant_lead) are a quality verdict, not an attempt: they are
  counted apart as `marked` and left out of calls, connected and connect rate.
- connected = outcomes in rep_workflow.CONNECTED (connected, interested, meeting booked: the same list My day, the Call log and the daily report use); unanswered = no answer / busy /
  call disconnected; connect rate = connected / calls.
- avg_per_day = calls / days on which that person made at least one call.
- Attainment = calls / (daily calls target x working days), working days = Mon-Sat (IST) from the period start to today
  (a period that is still running is judged on the days that have passed, not on days still to come). It is None when
  the period has no working day yet or the target is 0. Targets are the person's own daily target, else the team default.
- previous period = the equal number of days straight before the period; `calls_prev` and `calls_delta` compare calls.
- Ranking: by the measure chosen (calls, connected, interested, meetings), highest first, ties by name (A-Z, case-blind).

Visibility rule (decided here, enforced by the route)
- The route reads only the leads the VIEWER may see (workspace, client account, lead ownership), exactly like the call log.
  Every number is a count over those calls. With lead ownership on, a plain caller therefore counts only calls on leads they
  may see; owners and workspace admins see all of their workspace. Viewers read the same board, read-only.
- Everyone who may open the page sees every caller's name, counts and target attainment (a leaderboard is open by design).
- Nobody sees lead names, companies, phones, e-mails or notes here: the output holds caller names and numbers only."""
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from . import auth, rep_workflow as rw
from .call_log import UNANSWERED, collect
from .contact_status import MARKER_OUTCOMES

MAX_DAYS = 92
RANK_BY = ("calls", "connected", "interested", "meetings")
PERIODS = ("today", "yesterday", "week", "month", "custom")


def _day(raw: str, label: str) -> date:
    try:
        return date.fromisoformat(str(raw or "")[:10])
    except ValueError:
        raise ValueError(f"{label} must look like 2026-11-13, got {raw!r}") from None


def resolve(period: str, day_from: str, day_to: str, now: Optional[datetime] = None) -> dict:
    """{period, from, to, days, prev_from, prev_to} with IST dates. Raises ValueError (a plain sentence) on a bad request."""
    today = (now or datetime.now(timezone.utc)).astimezone(rw.IST).date()
    period = (period or "today").strip().lower()
    if period not in PERIODS:
        raise ValueError(f"period must be one of {', '.join(PERIODS)}")
    if period == "today":
        start = end = today
    elif period == "yesterday":
        start = end = today - timedelta(days=1)
    elif period == "week":
        start, end = today - timedelta(days=today.weekday()), today
    elif period == "month":
        start, end = today.replace(day=1), today
    else:
        if not day_from or not day_to:
            raise ValueError("A custom period needs both a from and a to date.")
        start, end = _day(day_from, "The from date"), _day(day_to, "The to date")
        if start > end:
            raise ValueError("The from date is after the to date.")
    days = (end - start).days + 1
    if days > MAX_DAYS:
        raise ValueError(f"A period can be at most {MAX_DAYS} days.")
    prev_to = start - timedelta(days=1)
    return {"period": period, "from": start.isoformat(), "to": end.isoformat(), "days": days,
            "prev_from": (prev_to - timedelta(days=days - 1)).isoformat(), "prev_to": prev_to.isoformat(), "_today": today}


def working_days(start: date, end: date, today: date) -> int:
    """Mon-Sat days in start..end that have already started (nothing after today)."""
    end = min(end, today)
    return sum(1 for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 6)


def _blank(name: str) -> dict:
    return {"caller": name, "calls": 0, "connected": 0, "unanswered": 0, "interested": 0, "meetings": 0, "marked": 0, "_days": set()}


def tally(rows: List[dict], start: str, end: str) -> dict:
    """{caller: counts} over call rows whose IST day is start..end (inclusive)."""
    out = {}
    for r in rows:
        if r["channel"] != "call":
            continue
        day = rw.ist_day(r["at"])
        if not day or not start <= day <= end:
            continue
        s = out.setdefault(r["by"] or "Unknown", _blank(r["by"] or "Unknown"))
        if r["outcome"] in MARKER_OUTCOMES:
            s["marked"] += 1
            continue
        s["calls"] += 1
        s["_days"].add(day)
        s["connected"] += r["outcome"] in rw.CONNECTED
        s["unanswered"] += r["outcome"] in UNANSWERED
        s["interested"] += r["outcome"] == "interested"
        s["meetings"] += r["outcome"] == "meeting_booked"
    return out


def daily_targets(store, caller: str) -> dict:
    """The caller's own daily targets, else the team defaults (the same ones My day shows)."""
    low = caller.strip().lower()
    key = low
    for u in auth.users(store):
        if low in {str(u.get("name") or "").strip().lower(), str(u.get("email") or "").strip().lower()} - {""}:
            key = str(u.get("email") or "").strip().lower() or low
            break
    cfg = store.read("settings").get("rep_targets") or {}
    return {**rw.DEFAULT_TARGETS, **(cfg.get("defaults") or {}), **((cfg.get("users") or {}).get(key) or {})}


def _attain(actual: int, daily: int, wdays: int) -> Optional[int]:
    goal = daily * wdays
    return round(100 * actual / goal) if goal > 0 else None


def build(store, leads: List[dict], activities, window: dict, rank_by: str = "calls", viewer: str = "") -> dict:
    if rank_by not in RANK_BY:
        raise ValueError(f"rank_by must be one of {', '.join(RANK_BY)}")
    rows = collect(leads, activities, "call")
    now_p = tally(rows, window["from"], window["to"])
    prev_p = tally(rows, window["prev_from"], window["prev_to"])
    today = window["_today"]
    wd = working_days(date.fromisoformat(window["from"]), date.fromisoformat(window["to"]), today)
    people = []
    for name, s in now_p.items():
        t = daily_targets(store, name)
        p = {k: v for k, v in s.items() if k != "_days"}
        p["connect_rate"] = round(100 * s["connected"] / s["calls"], 1) if s["calls"] else 0.0
        p["active_days"] = len(s["_days"])
        p["avg_per_day"] = round(s["calls"] / len(s["_days"]), 1) if s["_days"] else 0.0
        before = (prev_p.get(name) or {}).get("calls", 0)
        p.update(calls_prev=before, calls_delta=s["calls"] - before, daily_target=t["calls"], target=t["calls"] * wd,
                 attainment=_attain(s["calls"], t["calls"], wd), connects_attainment=_attain(s["connected"], t["connects"], wd))
        people.append(p)
    people.sort(key=lambda p: (-p[rank_by], p["caller"].lower(), p["caller"]))
    rank, last = 0, None
    for i, p in enumerate(people, 1):                 # equal scores share a rank (1, 1, 3), then name order within them
        if p[rank_by] != last:
            rank, last = i, p[rank_by]
        p["rank"] = rank
        p["you"] = bool(viewer) and p["caller"].strip().lower() == viewer.strip().lower()
    totals = {k: sum(p[k] for p in people) for k in ("calls", "connected", "unanswered", "interested", "meetings", "marked", "calls_prev", "target")}
    totals["connect_rate"] = round(100 * totals["connected"] / totals["calls"], 1) if totals["calls"] else 0.0
    totals["calls_delta"] = totals["calls"] - totals["calls_prev"]
    totals["attainment"] = round(100 * totals["calls"] / totals["target"]) if totals["target"] else None
    prev_marked = sum(s["marked"] for s in prev_p.values())
    return {"period": window["period"], "from": window["from"], "to": window["to"], "days": window["days"], "working_days": wd,
            "previous": {"from": window["prev_from"], "to": window["prev_to"], "calls": totals["calls_prev"], "marked": prev_marked},
            "rank_by": rank_by, "rows": people, "totals": totals}
