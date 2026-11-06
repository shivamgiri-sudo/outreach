"""Intelligence: the numbers the owner uses to decide what to change. Everything is derived from facts already stored
(lead timelines, engagement counters, the change log, lists), so there is nothing extra to keep in sync."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Optional

from . import lists as lists_mod
from . import workspaces
from .linkedin_seq import persona
from .source_stats import real

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
STALE_DAYS = 14
IST = timezone(timedelta(hours=5, minutes=30))


def _t(value) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _pct(part, whole) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def _first(lead, kinds) -> Optional[datetime]:
    times = [_t(e.get("at")) for e in lead.get("timeline", []) if e.get("kind") in kinds]
    return min((t for t in times if t), default=None)


def _last_touch(lead) -> Optional[datetime]:
    times = [_t(e.get("at")) for e in lead.get("timeline", [])]
    return max((t for t in times if t), default=None)


def reply_timing(leads) -> dict:
    gaps, weekday, hour = [], Counter(), Counter()
    for l in leads:
        sent = _first(l, ("email", "sent", "linkedin", "whatsapp"))
        for e in l.get("timeline", []):
            if e.get("kind") != "reply":
                continue
            at = _t(e.get("at"))
            if not at:
                continue
            local = at.astimezone(IST)
            weekday[local.weekday()] += 1
            hour[local.hour] += 1
            break
        first_reply = _first(l, ("reply",))
        if sent and first_reply and first_reply >= sent:
            gaps.append((first_reply - sent).total_seconds() / 3600)
    best_day = weekday.most_common(1)
    best_hour = hour.most_common(1)
    return {"replies_measured": sum(weekday.values()), "median_hours_to_reply": round(median(gaps), 1) if gaps else None,
            "by_weekday": [{"day": WEEKDAYS[i], "replies": weekday.get(i, 0)} for i in range(7)],
            "best_day": WEEKDAYS[best_day[0][0]] if best_day else None,
            "best_hour_ist": f"{best_hour[0][0]:02d}:00" if best_hour else None}


def channel_funnels(leads) -> dict:
    email = [l for l in leads if l.get("sequence")]
    sent = [l for l in email if (l["sequence"] or {}).get("sent_days")]
    li = [l for l in leads if l.get("linkedin_seq")]
    li_sent = [l for l in li if l["linkedin_seq"].get("done")]
    li_acc = [l for l in li if l["linkedin_seq"].get("accepted_at")]
    li_rep = [l for l in li if l["linkedin_seq"].get("stopped") == "replied"]
    eng = lambda l, k: (l.get("engagement") or {}).get(k, 0) > 0
    return {
        "email": {"enrolled": len(email), "sent": len(sent), "opened": sum(1 for l in sent if eng(l, "opens")),
                  "clicked": sum(1 for l in sent if eng(l, "clicks")), "replied": sum(1 for l in sent if eng(l, "replies")),
                  "reply_rate": _pct(sum(1 for l in sent if eng(l, "replies")), len(sent))},
        "linkedin": {"variants": {v: {"sent": sum(1 for l in li_sent if l["linkedin_seq"].get("variant") == v),
                                      "accepted": sum(1 for l in li_acc if l["linkedin_seq"].get("variant") == v),
                                      "accept_rate": _pct(sum(1 for l in li_acc if l["linkedin_seq"].get("variant") == v),
                                                          sum(1 for l in li_sent if l["linkedin_seq"].get("variant") == v))} for v in ("A", "B")},
                     "open_profiles": sum(1 for l in leads if (l.get("linkedin_profile") or {}).get("open_profile")),
                     "started": len(li), "connect_sent": len(li_sent), "accepted": len(li_acc), "replied": len(li_rep),
                     "accept_rate": _pct(len(li_acc), len(li_sent)), "reply_rate": _pct(len(li_rep), len(li_acc))}}


def persona_table(leads) -> list:
    rows = defaultdict(lambda: {"leads": 0, "contacted": 0, "replied": 0, "interested": 0})
    for l in leads:
        row = rows[persona(l.get("title", ""))[0]]
        row["leads"] += 1
        row["contacted"] += 1 if (l.get("sequence") or {}).get("sent_days") or (l.get("linkedin_seq") or {}).get("done") else 0
        row["replied"] += 1 if (l.get("engagement") or {}).get("replies", 0) > 0 else 0
        row["interested"] += 1 if l.get("stage") in ("interested", "whatsapp_active", "call_done", "trial", "customer", "advocate") else 0
    return sorted(({"persona": k, **v, "reply_rate": _pct(v["replied"], v["contacted"])} for k, v in rows.items()),
                  key=lambda r: (-r["interested"], -r["replied"], -r["leads"]))


def company_table(leads, limit=15) -> list:
    rows = defaultdict(lambda: {"leads": 0, "contacted": 0, "replied": 0, "interested": 0, "last_touch": None})
    for l in leads:
        name = (l.get("company") or "").strip()
        if not name:
            continue
        row = rows[name]
        row["leads"] += 1
        row["contacted"] += 1 if (l.get("sequence") or {}).get("sent_days") or (l.get("linkedin_seq") or {}).get("done") else 0
        row["replied"] += 1 if (l.get("engagement") or {}).get("replies", 0) > 0 else 0
        row["interested"] += 1 if l.get("stage") in ("interested", "whatsapp_active", "call_done", "trial", "customer") else 0
        touch = _last_touch(l)
        if touch and (row["last_touch"] is None or touch.isoformat() > row["last_touch"]):
            row["last_touch"] = touch.isoformat(timespec="seconds")
    ranked = sorted(({"company": k, **v} for k, v in rows.items()), key=lambda r: (-r["interested"], -r["replied"], -r["leads"]))
    return [r for r in ranked if r["contacted"] or r["replied"]][:limit]


def data_quality(leads, audit_rows) -> dict:
    n = len(leads)
    by_user = Counter(r.get("by") or "Unknown" for r in audit_rows)
    by_field = Counter(r.get("label") for r in audit_rows)
    return {"leads": n, "no_email": sum(1 for l in leads if not l.get("email")), "no_phone": sum(1 for l in leads if not l.get("phone")),
            "no_linkedin": sum(1 for l in leads if not l.get("linkedin_url")), "role_emails": sum(1 for l in leads if l.get("email_is_role")),
            "invalid_email": sum(1 for l in leads if (l.get("email_check") or {}).get("status") == "invalid"),
            "manual_corrections": len(audit_rows), "corrections_by_person": [{"who": k, "count": v} for k, v in by_user.most_common()],
            "corrections_by_field": [{"field": k, "count": v} for k, v in by_field.most_common()]}


def team_activity(leads, days=30, now: Optional[datetime] = None) -> list:
    """What each representative did in the last N days, from the `by` stamped on timeline events."""
    now = now or datetime.now(timezone.utc)
    cut = now - timedelta(days=days)
    out = defaultdict(Counter)
    for l in leads:
        for e in l.get("timeline", []):
            who, at = e.get("by"), _t(e.get("at"))
            if who and at and at >= cut:
                out[who][e.get("kind", "other")] += 1
    return sorted(({"who": w, "total": sum(c.values()), "by_kind": dict(c)} for w, c in out.items()), key=lambda r: -r["total"])


def stale(leads, now: Optional[datetime] = None, limit: Optional[int] = 20) -> dict:
    now = now or datetime.now(timezone.utc)
    cut = now - timedelta(days=STALE_DAYS)
    live = [l for l in leads if l.get("stage") in ("interested", "whatsapp_active", "call_done", "trial", "engaged", "meeting")]
    old = [l for l in live if (_last_touch(l) or now) < cut]
    return {"days": STALE_DAYS, "count": len(old),
            "leads": [{"id": l["id"], "company": l.get("company", ""), "stage": l.get("stage"),
                       "last_touch": (_last_touch(l) or now).isoformat(timespec="seconds")} for l in (old if limit is None else old[:limit])]}


def list_performance(store) -> list:
    return [{"id": r["id"], "name": r["name"], "created_by": r.get("created_by"), **lists_mod.stats(store, r)} for r in reversed(workspaces.filter_rows(store.read("lists")))][:20]


def build(store, now: Optional[datetime] = None, rng: Optional[dict] = None) -> dict:
    """Everything on the Intelligence page. With a period (analytics_range.parse_range) the numbers cover the leads
    created or active in it, and team activity covers exactly those days; without one, all leads and the last 30 days."""
    from . import analytics_range
    from .audit import contact_edits
    leads = real(workspaces.filter_rows(store.read("leads")))
    corrections = contact_edits(store.read("audit"))
    if rng is None:
        return {"channels": channel_funnels(leads), "timing": reply_timing(leads), "personas": persona_table(leads),
                "companies": company_table(leads), "quality": data_quality(leads, corrections),
                "team": team_activity(leads, now=now), "stale": stale(leads, now), "lists": list_performance(store)}
    ids = {l["id"] for l in leads}
    in_range = lambda r: analytics_range._in(analytics_range._t(r.get("at")), rng["start"], rng["end"])
    active = analytics_range.active_in(leads, rng)
    corrections = [r for r in corrections if r.get("lead_id") in ids and in_range(r)]
    return {"channels": channel_funnels(active), "timing": reply_timing(active), "personas": persona_table(active),
            "companies": company_table(active), "quality": data_quality(leads, corrections),
            "team": analytics_range.team(store, rng, leads), "stale": stale(leads, now), "lists": list_performance(store),
            "range": analytics_range.public_range(rng)}
