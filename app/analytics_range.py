"""Analytics over a chosen period (IST days): KPI tiles with the change against the period just before, the outreach
funnel (contacted -> replied -> interested -> meeting -> customer) by campaign / sequence step / template variant /
source, how long each stage takes, and what each person did. Worked out from facts already stored (lead timelines,
the outbox, campaigns), so nothing extra has to be kept in sync. Only leads in the caller's workspace scope count.
"""
import re
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from typing import Optional

from . import workspaces
from .replies import is_person_reply
from .source_stats import real

IST = timezone(timedelta(hours=5, minutes=30))
MAX_DAYS = 731
DEFAULT_DAYS = 30
FUNNEL = ("contacted", "replied", "interested", "meeting", "customer")
CONTACT_KINDS = ("email", "linkedin", "whatsapp", "call")
INTERESTED = {"interested", "whatsapp_active", "call_done", "trial", "customer", "advocate", "meeting"}
MEETING = {"call_done", "trial", "customer", "advocate", "meeting"}
CUSTOMER = {"customer", "advocate"}
SENT = ("sent",)
GROUPS = ("campaign", "step", "variant", "source")
ARROW = re.compile(r"(?:→|->)\s*([a-z_]+)")


class RangeError(ValueError):
    """The dates asked for can't be used; the message says why in plain words."""


def _t(value) -> Optional[datetime]:
    try:
        out = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def _day(text: str, name: str) -> date:
    try:
        return date.fromisoformat(str(text)[:10])
    except ValueError:
        raise RangeError(f"'{name}' must be a date like 2026-11-06.") from None


def _start(d: date) -> datetime:
    return datetime.combine(d, time(0), IST)


def parse_range(date_from: str = "", date_to: str = "", days: Optional[int] = None, now: Optional[datetime] = None) -> dict:
    """{from, to, days, prev_from, prev_to} as IST dates plus the matching UTC instants (end exclusive)."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(IST).date()
    if date_from or date_to:
        end = _day(date_to, "to") if date_to else today
        start = _day(date_from, "from") if date_from else end - timedelta(days=DEFAULT_DAYS - 1)
        if end < start:
            raise RangeError("'from' must be on or before 'to'.")
    else:
        n = DEFAULT_DAYS if days is None else int(days)
        if not 1 <= n <= MAX_DAYS:
            raise RangeError(f"Days must be between 1 and {MAX_DAYS}.")
        end, start = today, today - timedelta(days=n - 1)
    n = (end - start).days + 1
    if n > MAX_DAYS:
        raise RangeError(f"Pick at most {MAX_DAYS} days.")
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=n - 1)
    return {"from": start.isoformat(), "to": end.isoformat(), "days": n, "prev_from": prev_start.isoformat(), "prev_to": prev_end.isoformat(),
            "start": _start(start), "end": _start(end + timedelta(days=1)), "prev_start": _start(prev_start), "prev_end": _start(start)}


def public_range(rng: dict) -> dict:
    return {k: rng[k] for k in ("from", "to", "days", "prev_from", "prev_to")}


def _in(at: Optional[datetime], start: datetime, end: datetime) -> bool:
    return at is not None and start <= at < end


def _events(lead: dict) -> list:
    out = [(_t(e.get("at")), e) for e in lead.get("timeline") or []]
    return sorted(((at, e) for at, e in out if at), key=lambda x: x[0])


def _stage_target(event: dict) -> Optional[str]:
    if event.get("kind") != "stage":
        return None
    match = ARROW.search(str(event.get("text") or ""))
    return match.group(1) if match else None


def milestones(lead: dict) -> dict:
    """When this lead first reached each funnel step (None when it never did)."""
    events = _events(lead)
    first = lambda test: next((at for at, e in events if test(e)), None)
    out = {"contacted": first(lambda e: e.get("kind") in CONTACT_KINDS),
           "replied": first(is_person_reply),
           "interested": first(lambda e: _stage_target(e) in INTERESTED),
           "meeting": first(lambda e: _stage_target(e) in MEETING or (e.get("kind") == "meeting" and "booked" in str(e.get("text", "")).lower())),
           "customer": first(lambda e: _stage_target(e) in CUSTOMER)}
    last = events[-1][0] if events else _t(lead.get("created_at"))
    stage = lead.get("stage")
    # A stage set without a stage event (a reply read as interested, a flow, an import) still counts, at the best time known.
    if stage in INTERESTED and out["interested"] is None:
        out["interested"] = out["replied"] or last
    if stage in MEETING and out["meeting"] is None:
        out["meeting"] = out["interested"] or last
    if stage in CUSTOMER and out["customer"] is None:
        out["customer"] = last
    return out


def _sent_rows(store) -> dict:
    by_lead = defaultdict(list)
    for m in store.read("outbox"):
        if m.get("status") in SENT and m.get("lead_id"):
            by_lead[m["lead_id"]].append(m)
    for rows in by_lead.values():
        rows.sort(key=lambda m: m.get("sent_at") or "")
    return by_lead


def _deciding_email(rows: list, replied: Optional[datetime]) -> Optional[dict]:
    """The email that got the reply (the last one sent before it), else the last one sent."""
    if not rows:
        return None
    if replied:
        before = [m for m in rows if (_t(m.get("sent_at")) or replied) <= replied]
        if before:
            return before[-1]
    return rows[-1]


def _scoped_leads(store) -> list:
    return real(workspaces.filter_rows(store.read("leads")))


def _pct(part, whole) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def _delta(value, previous) -> dict:
    change = value - previous
    return {"value": value, "previous": previous, "delta": round(change, 1),
            "delta_pct": round(100 * change / previous, 1) if previous else None}


def _period_numbers(leads: list, marks: dict, sent: list, start: datetime, end: datetime) -> dict:
    created = sum(1 for l in leads if _in(_t(l.get("created_at")), start, end))
    reach = {step: sum(1 for l in leads if _in(marks[l["id"]][step], start, end)) for step in FUNNEL}
    replies = sum(1 for l in leads if any(is_person_reply(e) and _in(at, start, end) for at, e in _events(l)))
    unsub = sum(1 for l in leads if any(e.get("kind") == "unsubscribe" and _in(at, start, end) for at, e in _events(l)))
    emails = [m for m in sent if _in(_t(m.get("sent_at")), start, end)]
    emailed = {m["lead_id"] for m in emails}
    by_id = {l["id"]: l for l in leads}
    opened = sum(1 for i in emailed if ((by_id[i].get("engagement") or {}).get("opens") or 0) > 0)
    return {"new_leads": created, "contacted": reach["contacted"], "emails_sent": len(emails), "leads_emailed": len(emailed),
            "opened": opened, "replied": replies, "interested": reach["interested"], "meetings": reach["meeting"],
            "customers": reach["customer"], "unsubscribed": unsub,
            "reply_rate": _pct(replies, reach["contacted"] or len(emailed)), "open_rate": _pct(opened, len(emailed))}


def overview(store, rng: dict, leads: Optional[list] = None) -> dict:
    """KPI tiles: this period, the period before it, and the change."""
    leads = _scoped_leads(store) if leads is None else leads
    ids = {l["id"] for l in leads}
    marks = {l["id"]: milestones(l) for l in leads}
    sent = [m for rows in _sent_rows(store).values() for m in rows if m["lead_id"] in ids]
    now_n = _period_numbers(leads, marks, sent, rng["start"], rng["end"])
    prev_n = _period_numbers(leads, marks, sent, rng["prev_start"], rng["prev_end"])
    return {"range": public_range(rng), "kpis": {k: _delta(now_n[k], prev_n[k]) for k in now_n}}


def _group_key(group: str, lead: dict, email: Optional[dict], campaign_names: dict) -> str:
    if group == "campaign":
        cid = lead.get("campaign_id")
        return campaign_names.get(cid, f"Deleted campaign ({cid})") if cid else "No campaign"
    if group == "source":
        return lead.get("source_id") or str(lead.get("source") or "unknown").split(":")[0] or "unknown"
    if email is None:
        return "No email sent"
    if group == "step":
        return email.get("step") or "Unknown step"
    return email.get("variant") or "Default"


def _funnel_row(name: str, members: list, marks: dict) -> dict:
    counts = {step: sum(1 for l in members if marks[l["id"]][step]) for step in FUNNEL}
    base = counts["contacted"] or len(members)
    return {"name": name, **counts, "rates": {step: _pct(counts[step], base) for step in FUNNEL[1:]}}


def funnel(store, rng: dict, leads: Optional[list] = None) -> dict:
    """Leads first contacted in the period (the cohort) and how far each got by now, overall and per group."""
    leads = _scoped_leads(store) if leads is None else leads
    marks = {l["id"]: milestones(l) for l in leads}
    cohort = [l for l in leads if _in(marks[l["id"]]["contacted"], rng["start"], rng["end"])]
    sent = _sent_rows(store)
    names = {c["id"]: c.get("name") or c["id"] for c in store.read("campaigns")}
    deciding = {l["id"]: _deciding_email(sent.get(l["id"], []), marks[l["id"]]["replied"]) for l in cohort}
    groups = {}
    for group in GROUPS:
        buckets = defaultdict(list)
        for l in cohort:
            buckets[_group_key(group, l, deciding[l["id"]], names)].append(l)
        groups[group] = sorted((_funnel_row(k, v, marks) for k, v in buckets.items()), key=lambda r: (-r["contacted"], r["name"]))
    return {"range": public_range(rng), "steps": list(FUNNEL), "overall": _funnel_row("All", cohort, marks), "by": groups,
            "velocity": velocity(leads, marks, rng)}


def _median_days(gaps: list) -> Optional[float]:
    return round(median(gaps), 1) if gaps else None


def velocity(leads: list, marks: dict, rng: dict) -> dict:
    """Median days between funnel steps (for leads that reached the later step in the period) and median days spent in
    each pipeline stage (from the timeline's stage events, for stays that ended in the period)."""
    steps = []
    for a, b in zip(FUNNEL, FUNNEL[1:]):
        gaps = [(m[b] - m[a]).total_seconds() / 86400 for m in marks.values()
                if m[a] and m[b] and m[b] >= m[a] and _in(m[b], rng["start"], rng["end"])]
        steps.append({"from": a, "to": b, "median_days": _median_days(gaps), "samples": len(gaps)})
    dwell = defaultdict(list)
    for l in leads:
        changes = [(at, _stage_target(e)) for at, e in _events(l) if _stage_target(e)]
        for (at, stage), (nxt, _) in zip(changes, changes[1:]):
            if _in(nxt, rng["start"], rng["end"]):
                dwell[stage].append((nxt - at).total_seconds() / 86400)
    stages = [{"stage": s, "median_days": _median_days(g), "samples": len(g)} for s, g in sorted(dwell.items(), key=lambda x: -len(x[1]))]
    return {"steps": steps, "stages": stages}


def team(store, rng: dict, leads: Optional[list] = None) -> list:
    """What each person did in the period (timeline events with a name, plus emails they sent by hand)."""
    leads = _scoped_leads(store) if leads is None else leads
    ids = {l["id"] for l in leads}
    out = defaultdict(Counter)
    for l in leads:
        for at, e in _events(l):
            if e.get("by") and _in(at, rng["start"], rng["end"]):
                out[e["by"]][e.get("kind") or "other"] += 1
    for m in store.read("outbox"):
        if m.get("by") and m.get("lead_id") in ids and m.get("status") in SENT and _in(_t(m.get("sent_at")), rng["start"], rng["end"]):
            out[m["by"]]["emails_by_hand"] += 1
    rows = [{"who": w, "total": sum(c.values()), "by_kind": dict(c), "stage_changes": c.get("stage", 0), "calls": c.get("call", 0),
             "notes": c.get("note", 0)} for w, c in out.items()]
    return sorted(rows, key=lambda r: -r["total"])


def active_in(leads: list, rng: dict) -> list:
    """Leads created in the period or with anything on their timeline in it."""
    return [l for l in leads if _in(_t(l.get("created_at")), rng["start"], rng["end"])
            or any(_in(at, rng["start"], rng["end"]) for at, _ in _events(l))]


def build(store, rng: dict) -> dict:
    leads = _scoped_leads(store)
    return {"range": public_range(rng), "overview": overview(store, rng, leads)["kpis"], "funnel": funnel(store, rng, leads),
            "team": team(store, rng, leads)}
