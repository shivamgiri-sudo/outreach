"""How clean is the lead data? Invalid / consultant rates per source and per caller, and why leads were rejected.

Built from the `lead_quality` mark a caller sets on a call (rep_workflow.record_call_outcome). The rate is marked leads divided by
the leads that came from that source (or, per caller, by the leads that caller marked: their own share of invalid vs consultant)."""
from __future__ import annotations

from collections import defaultdict


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
