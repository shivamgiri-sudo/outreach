"""How clean is the lead data? Invalid / consultant rates per source and per caller, and why leads were rejected.

Built from the `lead_quality` mark a caller sets on a call (rep_workflow.record_call_outcome). The rate is marked leads divided by
the leads that came from that source (or, per caller, by the leads that caller marked: their own share of invalid vs consultant)."""
from __future__ import annotations

from collections import defaultdict


OUTLIER_MIN_CALLS = 20       # a caller needs this many calls before their rate means anything
OUTLIER_FACTOR = 2.0         # flagged when their marked-per-call rate is this many times the team's ...
OUTLIER_FLOOR = 10.0         # ... and at least this percent


def review(leads: list, days: int = 7, now=None) -> list:
    """Leads marked invalid / consultant in the last `days` days, newest first: the list a manager checks each week."""
    from datetime import datetime, timedelta, timezone
    from .rep_workflow import parse_at
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days)
    rows = []
    for lead in leads:
        q = lead.get("lead_quality") or {}
        at = parse_at(q.get("at"))
        if q.get("kind") in ("invalid", "consultant") and at and at >= cutoff:
            rows.append({"lead_id": lead["id"], "company": lead.get("company") or lead.get("domain") or "", "contact_name": lead.get("contact_name") or "",
                         "kind": q["kind"], "reason": q.get("reason") or "", "by": q.get("by") or "Unknown", "at": q.get("at"), "note": q.get("note") or "",
                         "stage": lead.get("stage"), "source": lead.get("source") or ""})
    rows.sort(key=lambda r: parse_at(r["at"]), reverse=True)
    return rows


def flag_outliers(callers: list, calls_by_caller: dict) -> list:
    """Add `calls`, `rate` (percent of that caller's calls that ended in a mark) and `outlier` to each caller row."""
    total_calls = sum(calls_by_caller.values())
    team = 100.0 * sum(c["marked"] for c in callers) / total_calls if total_calls else 0.0
    for c in callers:
        calls = calls_by_caller.get(c["caller"], 0)
        c["calls"] = calls
        c["rate"] = round(100.0 * c["marked"] / calls, 1) if calls else 0.0
        c["outlier"] = bool(calls >= OUTLIER_MIN_CALLS and c["rate"] >= OUTLIER_FLOOR and c["rate"] >= OUTLIER_FACTOR * team)
    return callers


def report(leads: list) -> dict:
    by_source = defaultdict(lambda: {"leads": 0, "invalid": 0, "consultant": 0})
    by_caller = defaultdict(lambda: {"invalid": 0, "consultant": 0})
    reasons = defaultdict(int)
    total = {"leads": 0, "invalid": 0, "consultant": 0}
    for lead in leads:
        src = str(lead.get("source") or "unknown")
        row, q = by_source[src], lead.get("lead_quality") or {}
        row["leads"] += 1
        total["leads"] += 1
        kind = q.get("kind")
        if kind in ("invalid", "consultant"):
            row[kind] += 1
            total[kind] += 1
            by_caller[str(q.get("by") or "Unknown")][kind] += 1
            if kind == "invalid":
                reasons[str(q.get("reason") or "not recorded")] += 1
    pct = lambda n, d: round(100.0 * n / d, 1) if d else 0.0
    sources = [{"source": s, **v, "marked": v["invalid"] + v["consultant"], "invalid_rate": pct(v["invalid"], v["leads"]),
                "consultant_rate": pct(v["consultant"], v["leads"])} for s, v in by_source.items()]
    sources.sort(key=lambda r: (-r["invalid_rate"], -r["leads"]))
    callers = [{"caller": c, **v, "marked": v["invalid"] + v["consultant"]} for c, v in by_caller.items()]
    callers.sort(key=lambda r: -r["marked"])
    return {"total": {**total, "invalid_rate": pct(total["invalid"], total["leads"]), "consultant_rate": pct(total["consultant"], total["leads"])},
            "sources": sources, "callers": callers, "reasons": [{"reason": r, "count": n} for r, n in sorted(reasons.items(), key=lambda kv: -kv[1])]}
