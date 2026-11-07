"""Response times (SLA): how fast new leads get a first attempt, and how fast real replies get an answer.

- First touch: hours from a lead being added to the first email / call / WhatsApp / LinkedIn step.
- Reply answered: hours from a person's reply (interested, question, other, wrong person) to the next thing we did
  for that lead (a call, an email or WhatsApp sent, a LinkedIn step, the reply marked handled).
Targets are settings.sla_first_touch_hours (default 24) and settings.sla_reply_hours (default 4), in clock hours.
check() runs with the reminders job: each new breach raises one alert (owner + managers) per lead and kind.
Only leads in the request's workspace scope count (the job runs without a scope, so it sees every lead).
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Optional

from . import workspaces
from .analytics_range import _in, _t, milestones
from .replies import is_person_reply
from .source_stats import real

FIRST_TOUCH_HOURS = 24
REPLY_HOURS = 4
NEEDS_ANSWER = ("interested", "question", "other", "wrong_person", "review")
CLOSED = ("unsubscribed", "disqualified", "customer", "advocate")
OUTBOUND = ("email", "call", "whatsapp", "linkedin", "sms", "note_logged")
LIST_LIMIT = 200


def targets(store) -> dict:
    s = store.read("settings")
    num = lambda k, d: float(s.get(k)) if isinstance(s.get(k), (int, float)) and s.get(k) > 0 else d
    return {"first_touch_hours": num("sla_first_touch_hours", FIRST_TOUCH_HOURS), "reply_hours": num("sla_reply_hours", REPLY_HOURS)}


def _label(event: dict) -> str:
    text = str(event.get("text") or "")
    return text[1:text.index("]")] if text.startswith("[") and "]" in text else "other"


def _answers(store) -> dict:
    """lead_id -> sorted times of things we did that answer a reply."""
    out = defaultdict(list)
    for a in store.read("activities"):
        if a.get("direction", "out") == "out" and a.get("type") in ("call", "email", "whatsapp", "sms", "linkedin", "meeting"):
            out[a.get("lead_id")].append(_t(a.get("at")))
    for m in store.read("outbox"):
        if m.get("status") in ("sent", "delivered"):
            out[m.get("lead_id")].append(_t(m.get("sent_at")))
    for m in store.read("whatsapp_outbox"):
        out[m.get("lead_id")].append(_t(m.get("sent_at") or m.get("at")))
    for key, st in (store.read("settings").get("reply_state") or {}).items():
        if isinstance(st, dict) and st.get("handled_at"):
            out[key.split("|", 1)[0]].append(_t(st["handled_at"]))
    return {k: sorted(t for t in v if t) for k, v in out.items()}


def measure(store, rng: Optional[dict] = None, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    tg = targets(store)
    leads = real(workspaces.filter_rows(store.read("leads")))
    answers = _answers(store)
    calls = defaultdict(list)
    for a in store.read("activities"):
        if a.get("type") == "call":
            calls[a.get("lead_id")].append(_t(a.get("at")))
    in_range = (lambda at: _in(at, rng["start"], rng["end"])) if rng else (lambda at: at is not None and at >= now - timedelta(days=30))
    touch_hours, reply_hours = [], []
    untouched, waiting = [], []
    per_owner = defaultdict(lambda: {"first_touch": [], "reply": [], "untouched": 0, "waiting": 0})
    for lead in leads:
        owner = lead.get("owner") or "Unassigned"
        created = _t(lead.get("created_at"))
        first = min([t for t in [milestones(lead)["contacted"], *calls.get(lead["id"], [])] if t], default=None)
        if created and in_range(created):
            if first and first >= created:
                h = (first - created).total_seconds() / 3600
                touch_hours.append(h)
                per_owner[owner]["first_touch"].append(h)
            elif not first and lead.get("stage") not in CLOSED:
                age = (now - created).total_seconds() / 3600
                if age > tg["first_touch_hours"]:
                    untouched.append({"lead_id": lead["id"], "company": lead.get("company") or "", "owner": owner, "hours": round(age, 1),
                                      "since": created.isoformat(timespec="seconds")})
                    per_owner[owner]["untouched"] += 1
        for e in lead.get("timeline") or []:
            at = _t(e.get("at"))
            if not at or not in_range(at) or not is_person_reply(e) or _label(e) not in NEEDS_ANSWER:
                continue
            nxt = next((t for t in answers.get(lead["id"], []) if t > at), None)
            if nxt:
                h = (nxt - at).total_seconds() / 3600
                reply_hours.append(h)
                per_owner[owner]["reply"].append(h)
            elif lead.get("stage") not in CLOSED:
                age = (now - at).total_seconds() / 3600
                if age > tg["reply_hours"]:
                    waiting.append({"lead_id": lead["id"], "company": lead.get("company") or "", "owner": owner, "hours": round(age, 1),
                                    "since": at.isoformat(timespec="seconds"), "reply": str(e.get("text") or "")[:200]})
                    per_owner[owner]["waiting"] += 1
    med = lambda xs: round(median(xs), 1) if xs else None
    within = lambda xs, lim: round(100 * sum(1 for x in xs if x <= lim) / len(xs)) if xs else None
    owners = sorted(({"owner": o, "median_first_touch_hours": med(v["first_touch"]), "median_reply_hours": med(v["reply"]),
                      "first_touch_within_pct": within(v["first_touch"], tg["first_touch_hours"]),
                      "reply_within_pct": within(v["reply"], tg["reply_hours"]), "untouched": v["untouched"], "waiting": v["waiting"]}
                     for o, v in per_owner.items() if v["first_touch"] or v["reply"] or v["untouched"] or v["waiting"]),
                    key=lambda r: -(r["untouched"] + r["waiting"]))
    return {"targets": tg,
            "first_touch": {"measured": len(touch_hours), "median_hours": med(touch_hours), "within_pct": within(touch_hours, tg["first_touch_hours"]),
                            "breaches": len(untouched)},
            "reply": {"measured": len(reply_hours), "median_hours": med(reply_hours), "within_pct": within(reply_hours, tg["reply_hours"]),
                      "breaches": len(waiting)},
            "untouched": sorted(untouched, key=lambda r: -r["hours"])[:LIST_LIMIT],
            "waiting": sorted(waiting, key=lambda r: -r["hours"])[:LIST_LIMIT], "owners": owners}


def check(store, now: Optional[datetime] = None) -> int:
    """Alert once per lead and kind for each new breach (newer than 7 days). Returns the number of alerts raised."""
    from . import alerts
    now = now or datetime.now(timezone.utc)
    data = measure(store, {"start": now - timedelta(days=7), "end": now + timedelta(seconds=1)}, now)
    done = dict(store.read("settings").get("sla_alerted") or {})
    raised = 0
    for kind, rows, what in (("untouched", data["untouched"], "No attempt yet, {h} hours after the lead was added (target {t} h)."),
                             ("waiting", data["waiting"], "Their reply has waited {h} hours for an answer (target {t} h).")):
        limit = data["targets"]["first_touch_hours" if kind == "untouched" else "reply_hours"]
        for r in rows:
            key = f"{r['lead_id']}:{kind}:{r['since']}"
            if key in done:
                continue
            alerts.safe(store, "sla", r["lead_id"], what.format(h=r["hours"], t=int(limit)), managers=True)
            done[key] = now.isoformat(timespec="seconds")
            raised += 1
    if raised:
        cut = (now - timedelta(days=14)).isoformat()
        store.update("settings", lambda s: {**s, "sla_alerted": {k: v for k, v in {**(s.get("sla_alerted") or {}), **done}.items() if v >= cut}})
    return raised
