"""Pipeline funnel: where every lead stands right now, worked out from facts already stored (lead timelines, logged
activities, open tasks). Steps: all leads -> enriched -> attempted -> connected, and what the connected ones said
(interested / not interested / follow-up / no answer yet). Also call outcomes, channels tried, stages, a daily trend,
and the same funnel per owner and per source. Only leads in the caller's workspace scope count.

Each lead lands in exactly one outcome bucket: interested beats not interested beats follow-up.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import workspaces
from .analytics_range import INTERESTED, IST, _in, _t, milestones, public_range
from .lists import is_enriched
from .replies import is_person_reply
from .source_stats import real

STEPS = ("total", "enriched", "attempted", "connected")
OUTCOMES = ("interested", "not_interested", "follow_up", "awaiting")
CHANNELS = ("email", "call", "linkedin", "whatsapp", "sms")
CONNECT_OUTCOMES = {"connected", "interested", "not_interested", "meeting_booked", "call_back"}
BAD_NUMBER = {"wrong_number", "inactive_number"}
NOT_INTERESTED_STAGES = {"disqualified", "unsubscribed"}
FOLLOW_UP_TASKS = {"follow_up", "call", "meeting_follow_up", "workflow", "question", "review"}
ENRICH_KINDS = {"enrichment", "enriched"}
MAX_TREND_DAYS = 92
DRILL_LIMIT = 300


def _calls_by_lead(store) -> dict:
    out = defaultdict(list)
    for a in store.read("activities"):
        if a.get("lead_id") and a.get("type") == "call":
            out[a["lead_id"]].append(a)
    for rows in out.values():
        rows.sort(key=lambda a: a.get("at") or "")
    return out


def _open_follow_ups(store) -> set:
    return {t.get("lead_id") for t in store.read("tasks")
            if t.get("lead_id") and (t.get("status") or ("done" if t.get("done") else "open")) == "open" and t.get("kind") in FOLLOW_UP_TASKS}


def classify(lead: dict, calls: list, follow_up_open: bool) -> dict:
    """Facts for one lead: which steps it reached (with the first time), its outcome bucket and channels tried."""
    timeline = lead.get("timeline") or []
    seq, li = lead.get("sequence") or {}, lead.get("linkedin_seq") or {}
    channels = {e.get("kind") for e in timeline if e.get("kind") in CHANNELS}
    if seq.get("sent_days"):
        channels.add("email")
    if li.get("done"):
        channels.add("linkedin")
    if calls:
        channels.add("call")
    marks = milestones(lead)
    attempted_at = marks["contacted"] or (_t(calls[0].get("at")) if calls else None)
    connect_times = [_t(a.get("at")) for a in calls if a.get("outcome") in CONNECT_OUTCOMES]
    connect_times += [_t(e.get("at")) for e in timeline if is_person_reply(e)]
    if li.get("accepted_at"):
        connect_times.append(_t(li["accepted_at"]))
    connect_times = [t for t in connect_times if t]
    connected_at = min(connect_times) if connect_times else None
    last_call = calls[-1].get("outcome") if calls else ""
    stage = lead.get("stage") or "new"
    attempted = bool(channels) or attempted_at is not None
    if stage in INTERESTED or last_call in ("interested", "meeting_booked"):
        outcome = "interested"
    elif stage in NOT_INTERESTED_STAGES or last_call == "not_interested":
        outcome = "not_interested"
    elif stage == "not_now" or last_call == "call_back" or (follow_up_open and attempted):
        outcome = "follow_up"
    else:
        outcome = "awaiting"
    connected = connected_at is not None or (attempted and outcome in ("interested", "not_interested"))
    enriched = is_enriched(lead) or stage == "enriched" or any(e.get("kind") in ENRICH_KINDS for e in timeline)
    return {"enriched": enriched, "attempted": attempted, "attempted_at": attempted_at, "connected": connected,
            "connected_at": connected_at or (marks["interested"] if connected else None), "interested_at": marks["interested"],
            "outcome": outcome if connected or outcome == "follow_up" else "not_connected", "channels": channels,
            "bad_number": last_call in BAD_NUMBER or (lead.get("phone_status") or {}).get("status") in BAD_NUMBER,
            "contactable": bool(lead.get("email") or lead.get("phone") or lead.get("linkedin_url"))}


def _row(name: str, facts: list) -> dict:
    n = len(facts)
    row = {"name": name, "total": n, "contactable": sum(f["contactable"] for f in facts),
           "enriched": sum(f["enriched"] for f in facts), "attempted": sum(f["attempted"] for f in facts),
           "connected": sum(f["connected"] for f in facts), "bad_number": sum(f["bad_number"] for f in facts),
           "not_attempted": sum(not f["attempted"] for f in facts),
           "no_connect": sum(f["attempted"] and not f["connected"] for f in facts)}
    for key in OUTCOMES:
        row[key] = sum(f["connected"] and f["outcome"] == key for f in facts)
    row["follow_up"] += sum(not f["connected"] and f["outcome"] == "follow_up" for f in facts)
    pct = lambda a, b: round(100 * a / b, 1) if b else 0.0
    row["rates"] = {"enriched": pct(row["enriched"], n), "attempted": pct(row["attempted"], n),
                    "connected": pct(row["connected"], row["attempted"]), "interested": pct(row["interested"], row["connected"]),
                    "not_interested": pct(row["not_interested"], row["connected"]), "follow_up": pct(row["follow_up"], row["attempted"])}
    return row


def _trend(leads: list, facts: dict, start: datetime, end: datetime) -> list:
    last = (end - timedelta(seconds=1)).astimezone(IST).date()
    days = min(MAX_TREND_DAYS, max(1, (last - start.astimezone(IST).date()).days + 1))
    first = last - timedelta(days=days - 1)
    keys = [(first + timedelta(days=i)).isoformat() for i in range(days)]
    out = {k: Counter() for k in keys}
    bump = lambda at, what: at and (d := at.astimezone(IST).date().isoformat()) in out and out[d].update([what])
    for lead in leads:
        f = facts[lead["id"]]
        bump(_t(lead.get("created_at")), "new")
        bump(f["attempted_at"], "attempted")
        bump(f["connected_at"], "connected")
        bump(f["interested_at"], "interested")
    return [{"day": k, **{w: out[k].get(w, 0) for w in ("new", "attempted", "connected", "interested")}} for k in keys]


def _scope(store, rng: Optional[dict], added_only: bool) -> tuple:
    leads = real(workspaces.filter_rows(store.read("leads")))
    if rng and added_only:
        leads = [l for l in leads if _in(_t(l.get("created_at")), rng["start"], rng["end"])]
    calls, follow = _calls_by_lead(store), _open_follow_ups(store)
    return leads, calls, {l["id"]: classify(l, calls.get(l["id"], []), l["id"] in follow) for l in leads}


def build(store, rng: Optional[dict] = None, added_only: bool = False, now: Optional[datetime] = None) -> dict:
    leads, calls, facts = _scope(store, rng, added_only)
    now = now or datetime.now(timezone.utc)
    start, end = (rng["start"], rng["end"]) if rng else (now - timedelta(days=30), now)
    ids = {l["id"] for l in leads}
    call_rows = [a for lid, rows in calls.items() if lid in ids for a in rows if not rng or _in(_t(a.get("at")), start, end)]
    by = lambda key: sorted((_row(k, [facts[l["id"]] for l in v]) for k, v in _group(leads, key).items()), key=lambda r: (-r["total"], r["name"]))
    return {"range": public_range(rng) if rng else None, "added_only": bool(rng and added_only),
            "overall": _row("All", list(facts.values())), "steps": list(STEPS), "outcomes": list(OUTCOMES),
            "calls": {"total": len(call_rows), "leads": len({a["lead_id"] for a in call_rows}),
                      "by_outcome": dict(Counter(a.get("outcome") or "no_outcome" for a in call_rows).most_common())},
            "channels": {c: sum(c in f["channels"] for f in facts.values()) for c in CHANNELS},
            "stages": dict(Counter(l.get("stage") or "new" for l in leads).most_common()),
            "trend": _trend(leads, facts, start, end), "by_owner": by("owner"), "by_source": by("source")}


def _group(leads: list, key: str) -> dict:
    out = defaultdict(list)
    for l in leads:
        out[str(l.get(key) or ("Unassigned" if key == "owner" else "unknown"))].append(l)
    return out


DRILL_STEPS = set(STEPS) | set(OUTCOMES) | {"not_attempted", "no_connect", "bad_number", "contactable"}


def drill(store, step: str, rng: Optional[dict] = None, added_only: bool = False, owner: str = "", source: str = "") -> dict:
    """The leads behind one number on the funnel (newest first, at most DRILL_LIMIT)."""
    leads, _calls, facts = _scope(store, rng, added_only)
    def hit(l):
        f = facts[l["id"]]
        if owner and str(l.get("owner") or "Unassigned") != owner or source and str(l.get("source") or "unknown") != source:
            return False
        if step == "total":
            return True
        if step in ("enriched", "attempted", "connected", "bad_number", "contactable"):
            return bool(f[step])
        if step == "not_attempted":
            return not f["attempted"]
        if step == "no_connect":
            return f["attempted"] and not f["connected"]
        return f["outcome"] == step and (f["connected"] or step == "follow_up")
    rows = sorted((l for l in leads if hit(l)), key=lambda l: l.get("created_at") or "", reverse=True)
    return {"step": step, "count": len(rows), "leads": [
        {"id": l["id"], "name": l.get("name") or "", "company": l.get("company") or "", "phone": l.get("phone") or "",
         "email": l.get("email") or "", "stage": l.get("stage") or "new", "owner": l.get("owner") or "",
         "outcome": facts[l["id"]]["outcome"]} for l in rows[:DRILL_LIMIT]]}
