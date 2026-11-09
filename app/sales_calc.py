"""Sales funnel, part 3: every dashboard number (18 components), worked out in one pass over a list of FLead objects.

Funnel stages use the cohort basis (created in range, stage reached by the To Date); closed revenue uses the Closed
Date. A lead counts in every stage it reached; a later stage reached implies the earlier ones (no stage can ever be
larger than the one before it, even with odd hand-entered dates). The result carries `ids` lists (the leads behind
each number); app/routes_funnel.py turns them into drill-down keys before sending.
"""
from math import floor
from typing import Optional

from .sales_model import DEFAULTS, ENGAGEMENTS, FITS, LOST, PROBABILITY, RECYCLED, STAGES, WON, ist_day
from .sales_range import previous_range, resolve_range

DAY = 86400.0
LOST_BUCKETS = ("0-30 days", "31-90 days", "91-180 days", "180+ days")


class FLead:
    """One lead as the funnel sees it (built from the stored record by app/sales_view.py)."""
    __slots__ = ("id", "created", "company", "contact", "industry", "service", "source", "owner", "fit", "engagement", "status",
                 "first_response", "dates", "closed", "recycled", "lost_stage", "lost_reason", "est", "prob", "last_activity",
                 "closed_value", "tenure", "tcv", "backfilled", "idx")

    def __init__(self, **kw):
        self.id = kw.get("id", "")
        self.created = kw.get("created")
        self.company = kw.get("company", "")
        self.contact = kw.get("contact", "")
        self.industry = kw.get("industry", "")
        self.service = kw.get("service", "")
        self.source = kw.get("source", "")
        self.owner = kw.get("owner", "")
        self.fit = kw.get("fit", "")
        self.engagement = kw.get("engagement", "")
        self.status = kw.get("status", STAGES[0]["status"])
        self.first_response = kw.get("first_response")
        self.dates = kw.get("dates") or {}                    # stage key -> aware datetime (mql ... won)
        self.closed = kw.get("closed")
        self.recycled = kw.get("recycled")
        self.lost_stage = kw.get("lost_stage", "")
        self.lost_reason = kw.get("lost_reason", "")
        self.est = kw.get("est", 0) or 0
        self.prob = kw.get("prob", 0) or 0
        self.last_activity = kw.get("last_activity")
        self.closed_value = kw.get("closed_value", 0) or 0
        self.tenure = kw.get("tenure", 0) or 0
        self.tcv = kw.get("tcv", 0) or 0
        self.backfilled = kw.get("backfilled") or []
        self.idx = _status_index(self.status)


def _status_index(status: str) -> int:
    for i, s in enumerate(STAGES):
        if s["status"] == status:
            return i
    return -1


# ---------------------------------------------------------------- small maths (JS rounding: halves go up)
def rnd(x: float) -> float:
    return floor(x + 0.5)


def median(xs: list) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def avg(xs: list) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def pct(a, b) -> Optional[float]:
    """a as a % of b with 1 decimal; None (shown as a dash) when b is 0."""
    return rnd(1000 * a / b) / 10 if b else None


def change(now, before) -> Optional[float]:
    return rnd(1000 * (now - before) / before) / 10 if before else None


def r1(x) -> Optional[float]:
    return None if x is None else rnd(x * 10) / 10


def total(rows, attr: str):
    return sum((getattr(r, attr) or 0) for r in rows)


def passes(lead: FLead, f: dict) -> bool:
    for attr, key in (("source", "sources"), ("industry", "industries"), ("service", "services"), ("owner", "owners")):
        want = f.get(key)
        if want and getattr(lead, attr) not in want:
            return False
    return True


def reached_index(lead: FLead, end) -> int:
    """Index of the furthest stage the lead reached by `end` (-1 when not created by then)."""
    if not lead.created or lead.created > end:
        return -1
    dates = lead.dates
    for i in range(len(STAGES) - 1, 0, -1):
        d = dates.get(STAGES[i]["key"])
        if d and d <= end:
            return i
    return 0


def is_open(l: FLead) -> bool:
    return l.status != WON and l.status != LOST


def current_index(l: FLead) -> int:
    return l.idx if l.idx >= 0 else 0


def brief(l: FLead, **extra) -> dict:
    value = l.closed_value if l.status == WON and l.closed_value else l.est
    return {"id": l.id, "company": l.company, "owner": l.owner, "stage": l.status, "value": value, **extra}


def funnel(cohort: list, end) -> list:
    n = len(STAGES)
    ids = [[] for _ in range(n)]
    for l in cohort:
        r = reached_index(l, end)
        for i in range(r + 1):
            ids[i].append(l.id)
    counts = [len(x) for x in ids]
    return [{"key": s["key"], "label": s["status"], "count": counts[i], "ids": ids[i], "ofInquiry": pct(counts[i], counts[0]),
             "conv": None if i == 0 else pct(counts[i], counts[i - 1])} for i, s in enumerate(STAGES)]


def closed_won(leads: list, start, end) -> list:
    return [l for l in leads if l.status == WON and l.closed and start <= l.closed <= end]


def _group(rows: list, attr: str) -> dict:
    g = {}
    for l in rows:
        g.setdefault(getattr(l, attr) or "Not set", []).append(l)
    return g


def _tile(label, v, p, money=False, ids=None) -> dict:
    return {"label": label, "value": v, "previous": p, "change": change(v, p), "money": bool(money), "ids": ids}


def compute(all_leads: list, now, from_date: str = "", to_date: str = "", filters: Optional[dict] = None, sla_hours: Optional[int] = None,
            stalled_days: Optional[int] = None, targets: Optional[list] = None) -> dict:
    f = filters or {}
    sla = sla_hours or DEFAULTS["sla_hours"]
    stalled_n = stalled_days or DEFAULTS["stalled_days"]
    leads = [l for l in all_leads if l.id and passes(l, f)]
    earliest = min((l.created for l in all_leads if l.created), default=None)
    rng = resolve_range(from_date or "", to_date or "", earliest, now)
    if rng.get("error"):
        return {"error": rng["error"]}
    prev = previous_range(rng)
    start, end = rng["start"], rng["end"]
    cohort = [l for l in leads if l.created and start <= l.created <= end]
    cohort_prev = [l for l in leads if l.created and prev["start"] <= l.created <= prev["end"]]
    stages, stages_prev = funnel(cohort, end), funnel(cohort_prev, prev["end"])
    count = lambda st, key: next(s["count"] for s in st if s["key"] == key)

    # 2 Revenue funnel snapshot
    won, won_prev = closed_won(leads, start, end), closed_won(leads, prev["start"], prev["end"])
    snapshot = [
        _tile("Inquiries", count(stages, "inquiry"), count(stages_prev, "inquiry"), 0, stages[0]["ids"]),
        _tile("MQLs", count(stages, "mql"), count(stages_prev, "mql"), 0, stages[1]["ids"]),
        _tile("SQLs", count(stages, "sql"), count(stages_prev, "sql"), 0, stages[3]["ids"]),
        _tile("Opportunities (Meeting Held)", count(stages, "meeting"), count(stages_prev, "meeting"), 0, stages[4]["ids"]),
        _tile("Won (from these leads)", count(stages, "won"), count(stages_prev, "won"), 0, stages[7]["ids"]),
        _tile("Deal Value Closed (₹)", total(won, "closed_value"), total(won_prev, "closed_value"), 1, [l.id for l in won]),
    ]

    # 5 Stage conversion rates
    conversions = [{"from": stages[i]["label"], "to": s["label"], "rate": s["conv"], "fromCount": stages[i]["count"], "toCount": s["count"]}
                   for i, s in enumerate(stages[1:])]
    conversions.append({"from": stages[0]["label"], "to": "Closed Won (overall)", "rate": pct(stages[7]["count"], stages[0]["count"]),
                        "fromCount": stages[0]["count"], "toCount": stages[7]["count"]})

    # 6 Funnel leakage and lost reasons (cohort leads lost by the To Date)
    lost = [l for l in cohort if l.status == LOST and l.closed and l.closed <= end]
    leakage, cells = {}, {}
    for l in lost:
        st, rs = l.lost_stage or "Unknown", l.lost_reason or "Not given"
        leakage.setdefault(st, {})
        leakage[st][rs] = leakage[st].get(rs, 0) + 1
        cells.setdefault((st, rs), []).append(l.id)
    leakage_cells = [{"stage": st, "reason": rs, "count": len(ids), "ids": ids} for (st, rs), ids in sorted(cells.items())]

    # 7 / 8 Speed to lead and SLA
    with_response = [l for l in cohort if l.first_response and l.created and l.first_response <= end]
    hrs = lambda l: max(0.0, (l.first_response - l.created).total_seconds() / 3600)
    by_owner_speed = {}
    for l in with_response:
        by_owner_speed.setdefault(l.owner or "Unassigned", []).append(hrs(l))
    all_hrs = [hrs(l) for l in with_response]
    speed = {"avgHours": r1(avg(all_hrs)), "medianHours": r1(median(all_hrs)), "measured": len(with_response),
             "byOwner": [{"owner": o, "avgHours": r1(avg(by_owner_speed[o])), "medianHours": r1(median(by_owner_speed[o])),
                          "measured": len(by_owner_speed[o])} for o in sorted(by_owner_speed)]}
    within = [l for l in with_response if hrs(l) <= sla]
    sla_out = {"hours": sla, "pct": pct(len(within), len(with_response)), "within": len(within), "measured": len(with_response)}

    # 9 Lead aging and stalled leads (open now, idle more than N days)
    stalled = sorted((brief(l, daysIdle=int((now - l.last_activity).total_seconds() // DAY)) for l in leads
                      if is_open(l) and l.last_activity and (now - l.last_activity).total_seconds() / DAY > stalled_n),
                     key=lambda b: -b["value"])

    # 10 Pipeline value by stage (open leads, current stage)
    open_leads = [l for l in leads if is_open(l)]
    pipeline = []
    for i, s in enumerate(STAGES[:7]):
        at = [l for l in open_leads if current_index(l) == i and l.status != RECYCLED]
        pipeline.append({"key": s["key"], "label": s["status"], "value": total(at, "est"),
                         "weighted": rnd(sum(l.est * (PROBABILITY.get(s["key"], 0)) / 100 for l in at)), "count": len(at),
                         "ids": [l.id for l in at]})
    open_value = sum(p["value"] for p in pipeline)

    # 11 Pipeline coverage ratio (targets: [{month: "yyyy-mm", owner, target}])
    owners_f = f.get("owners") or []
    target = sum(float(t.get("target") or 0) for t in (targets or [])
                 if rng["from"][:7] <= str(t.get("month")) <= rng["to"][:7] and (not owners_f or t.get("owner") in owners_f))
    coverage = {"pipeline": open_value, "target": target, "ratio": round(100 * open_value / target) / 100 if target else None}

    # 12 Sales velocity and 13 average sales cycle (closed in range)
    closed_in = [l for l in leads if (l.status == WON or l.status == LOST) and l.closed and start <= l.closed <= end]
    won_in = [l for l in closed_in if l.status == WON]
    win_rate = len(won_in) / len(closed_in) if closed_in else None
    cycle_days = [(l.closed - l.created).total_seconds() / DAY for l in won_in if l.created]
    open_opps = [l for l in open_leads if current_index(l) >= 4 and l.status != RECYCLED]
    avg_won = avg([l.closed_value for l in won_in])
    avg_cycle = avg(cycle_days)
    velocity = {"openOpportunities": len(open_opps), "winRate": None if win_rate is None else r1(100 * win_rate),
                "avgWonValue": None if avg_won is None else rnd(avg_won), "avgCycleDays": r1(avg_cycle),
                "perDay": rnd(len(open_opps) * win_rate * avg_won / max(avg_cycle, 1)) if (win_rate is not None and avg_won and avg_cycle) else None,
                "ids": [l.id for l in open_opps]}
    cycle = []
    for i, s in enumerate(STAGES[1:]):
        prev_key = STAGES[i]["key"]
        xs = []
        for l in cohort:
            a = l.created if prev_key == "inquiry" else l.dates.get(prev_key)
            b = l.dates.get(s["key"])
            if a and b and b <= end and s["short"] not in l.backfilled and STAGES[i]["short"] not in l.backfilled:
                xs.append((b - a).total_seconds() / DAY)
        cycle.append({"from": STAGES[i]["short"], "to": s["short"], "avgDays": r1(avg(xs)), "measured": len(xs)})
    cycle.append({"from": "Inquiry", "to": "Won (total)", "avgDays": r1(avg_cycle), "measured": len(cycle_days)})

    # 14 Source-wise funnel and 16 owner leaderboard
    won_by = lambda attr, name: closed_won([l for l in leads if (getattr(l, attr) or "Not set") == name], start, end)
    source_rows = []
    for k, rows in sorted(_group(cohort, "source").items()):
        st = funnel(rows, end)
        source_rows.append({"source": k, "counts": [s["count"] for s in st], "ids": st[0]["ids"], "toSql": pct(st[3]["count"], st[0]["count"]),
                            "toWon": pct(st[7]["count"], st[0]["count"]), "wonValue": total(won_by("source", k), "closed_value")})
    owner_rows = []
    for k, rows in sorted(_group(cohort, "owner").items()):
        st = funnel(rows, end)
        closed_k = [l for l in closed_in if (l.owner or "Not set") == k]
        owner_rows.append({"owner": k, "leads": st[0]["count"], "sqls": st[3]["count"], "meetings": st[4]["count"],
                           "proposals": st[5]["count"], "won": st[7]["count"],
                           "winPct": pct(sum(1 for l in closed_k if l.status == WON), len(closed_k)),
                           "wonValue": total(won_by("owner", k), "closed_value"),
                           "avgSpeedHours": r1(avg(by_owner_speed.get(k, []))), "ids": st[0]["ids"]})
    owner_rows.sort(key=lambda r: (-r["wonValue"], -r["won"]))

    # 15 Lead score distribution (open leads)
    grid = {fit: {e: {"count": 0, "ids": []} for e in ENGAGEMENTS} for fit in FITS}
    for l in open_leads:
        cell = grid.get(l.fit, {}).get(l.engagement)
        if cell is not None:
            cell["count"] += 1
            cell["ids"].append(l.id)

    # 17 Recycled and nurture pool
    recycled = [l for l in leads if l.status == RECYCLED]
    ages, reasons = {b: 0 for b in LOST_BUCKETS}, {}
    for l in recycled:
        d = (now - l.recycled).total_seconds() / DAY if l.recycled else 0
        ages[LOST_BUCKETS[0] if d <= 30 else LOST_BUCKETS[1] if d <= 90 else LOST_BUCKETS[2] if d <= 180 else LOST_BUCKETS[3]] += 1
        r = l.lost_reason or "Not given"
        reasons[r] = reasons.get(r, 0) + 1

    # 18 Deal value closed (Closed Date basis)
    mv, mv_prev = total(won, "closed_value"), total(won_prev, "closed_value")
    tcv, tcv_prev = total(won, "tcv"), total(won_prev, "tcv")
    by_month = {}
    for l in won:
        m = ist_day(l.closed)[:7]
        by_month[m] = by_month.get(m, 0) + l.closed_value

    def split(attr):
        out = []
        for k, rows in sorted(_group(won, attr).items()):
            est, act = total(rows, "est"), total(rows, "closed_value")
            out.append({"name": k, "deals": len(rows), "closedValue": act, "tcv": total(rows, "tcv"), "estValue": est,
                        "variancePct": r1(100 * (act - est) / est) if est else None, "ids": [l.id for l in rows]})
        return out
    revenue = {
        "tiles": [_tile("Monthly Value Closed (₹)", mv, mv_prev, 1, [l.id for l in won]),
                  _tile("Total Contract Value Closed (₹)", tcv, tcv_prev, 1, [l.id for l in won]),
                  _tile("Deals Closed", len(won), len(won_prev), 0, [l.id for l in won]),
                  _tile("Average Deal Size (₹)", rnd(mv / len(won)) if won else 0, rnd(mv_prev / len(won_prev)) if won_prev else 0, 1,
                        [l.id for l in won])],
        "byMonth": [{"month": m, "value": by_month[m]} for m in sorted(by_month)],
        "bySource": split("source"), "byIndustry": split("industry"), "byService": split("service"), "byOwner": split("owner"),
    }

    def distinct(attr):
        return sorted({getattr(l, attr) for l in all_leads if getattr(l, attr)})
    today = ist_day(now)
    return {
        "range": {"from": rng["from"], "to": rng["to"], "label": rng["label"], "previous": {"from": prev["from"], "to": prev["to"]}},
        "filters": {"sources": distinct("source"), "industries": distinct("industry"), "services": distinct("service"), "owners": distinct("owner")},
        "status": {"total": len(leads), "updatedToday": sum(1 for l in leads if l.last_activity and ist_day(l.last_activity) == today)},
        "snapshot": snapshot, "funnel": stages, "conversions": conversions, "leakage": leakage, "leakageCells": leakage_cells, "lost": {"count": len(lost), "ids": [l.id for l in lost]},
        "speed": speed, "sla": sla_out, "stalled": {"total": len(stalled), "ids": [b["id"] for b in stalled], "rows": stalled[:200]},
        "pipeline": pipeline, "coverage": coverage, "velocity": velocity, "cycle": cycle, "sources": source_rows, "scoreGrid": grid,
        "owners": owner_rows, "recycled": {"count": len(recycled), "byReason": reasons, "byAge": ages, "ids": [l.id for l in recycled]},
        "revenue": revenue, "probabilities": PROBABILITY,
    }


def drill(all_leads: list, ids) -> list:
    """The leads behind a number, for the drill-down list (one row per id, in the order of ids)."""
    by_id = {l.id: l for l in all_leads}
    return [brief(by_id[i]) for i in ids if i in by_id]


def drill_by_id(by_id: dict, ids) -> list:
    """Same as drill(), from an {id: FLead} map (the server keeps one per request scope)."""
    return [brief(by_id[i]) for i in ids if i in by_id]
