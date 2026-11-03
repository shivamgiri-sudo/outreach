"""Send the next due email step for every enrolled lead. Used by the API and the background timer.

Safety rules, in order: one run at a time; a send is claimed before it goes out (a crash never repeats it);
cold steps stop once a lead is warm or closed; at least two days between steps; a few sends per run so the
day's volume is spread out; one email per company domain per day; a warm-up ramp for live sending; failed
sends back off and stop after three attempts; nothing goes out with an unfilled {{placeholder}}.
"""
import threading
from datetime import date, datetime, timedelta
from typing import Optional

from . import sequences
from .growth_core import ensure_demo, get_offer, offer_line
from .hygiene import business_domain
from .sequences import DEFAULT_DAILY_CAP, IST, due_steps, in_business_hours, lead_variables, render

DEFAULT_SENDER = "Priya"
DEFAULT_PER_RUN_CAP = 5
MIN_GAP = timedelta(days=2)
RETRY_AFTER = timedelta(hours=1)
MAX_ATTEMPTS = 3
WARM_UP = ((7, 10), (14, 20), (21, 35))   # (days since first live send, daily limit)
# Leads in these stages must never get another cold email.
NO_EMAIL_STAGES = ("unsubscribed", "disqualified", "not_now", "customer", "advocate")
# Leads in these stages are warm: a person talks to them now, so the cold sequence stops.
WARM_STAGES = ("interested", "whatsapp_active", "call_done", "trial")
EARLY_STAGES = ("new", "verified", "enriched")
_RUN_LOCK = threading.Lock()


def get_settings(store) -> dict:
    return {"daily_cap": DEFAULT_DAILY_CAP, "per_run_cap": DEFAULT_PER_RUN_CAP, "sender_name": DEFAULT_SENDER,
            "live_sending": False, **store.read("settings")}


def effective_daily_cap(daily_cap: int, live: bool, first_send: Optional[date], today: date) -> int:
    """New mailboxes ramp up: 10 a day in week 1, 20 in week 2, 35 in week 3, then the configured cap."""
    if not live:
        return daily_cap
    age = (today - (first_send or today)).days
    for days, limit in WARM_UP:
        if age < days:
            return min(daily_cap, limit)
    return daily_cap


def run_due(store, now: datetime, base_url: str, sender, ignore_hours: bool = False) -> dict:
    if not ignore_hours and not in_business_hours(now):
        return {"sent": 0, "skipped": "outside business hours (Mon–Fri 9–18 IST)"}
    if not _RUN_LOCK.acquire(blocking=False):
        return {"sent": 0, "skipped": "another send run is in progress"}
    try:
        return _run(store, now, base_url, sender)
    finally:
        _RUN_LOCK.release()


def _run(store, now: datetime, base_url: str, sender) -> dict:
    cfg = get_settings(store)
    offer = get_offer(store)
    today = now.astimezone(IST).date()
    sent_ok = [m for m in store.read("outbox") if m.get("status") != "failed"]
    live_dates = [date.fromisoformat(m["sent_at_ist_date"]) for m in sent_ok if m.get("status") == "sent"]
    cap = effective_daily_cap(cfg["daily_cap"], cfg["live_sending"], min(live_dates, default=None), today)
    todays = [m for m in sent_ok if m["sent_at_ist_date"] == today.isoformat()]
    domains_today = {business_domain(m["to"]) for m in todays} - {None}
    suppressed = set(store.read("suppressed"))
    counts = {"sent": 0, "failed": 0, "needs_check": 0, "blocked": 0}

    for lead in sorted(store.read("leads"), key=lambda l: (l.get("sequence") or {}).get("enrolled_at", "")):
        seq = lead.get("sequence")
        if not seq or seq["stopped"] or lead["stage"] in NO_EMAIL_STAGES + WARM_STAGES:
            continue
        if not lead.get("email") or lead["email"] in suppressed:
            continue
        if seq.get("in_flight"):
            counts["needs_check"] += 1
            continue
        if len(todays) + counts["sent"] >= cap or counts["sent"] >= cfg["per_run_cap"]:
            break
        if not _ready(seq, now):
            continue
        steps = due_steps(datetime.fromisoformat(seq["enrolled_at"]), seq["sent_days"], now)
        domain = business_domain(lead["email"])
        if not steps or (domain and domain in domains_today):
            continue
        composed = _compose(lead, steps[0], cfg, offer, base_url, now, today)
        if composed is None:
            counts["blocked"] += 1
            _set_sequence(store, lead["id"], blocked="a detail needed by the email is missing")
            continue
        message, demo = composed
        _set_sequence(store, lead["id"], in_flight={"day": steps[0]["day"], "at": now.isoformat(timespec="seconds")})
        message.update(sender(message))
        store.update("outbox", lambda rows: [*rows, message])
        if message["status"] == "failed":
            counts["failed"] += 1
            store.update("leads", lambda rows: [_record_failure(r, now) if r["id"] == lead["id"] else r for r in rows])
            continue
        day = steps[0]["day"]
        store.update("leads", lambda rows: [_record_send(r, day, message, demo, now) if r["id"] == lead["id"] else r for r in rows])
        counts["sent"] += 1
        if domain:
            domains_today.add(domain)
    return counts


def _ready(seq: dict, now: datetime) -> bool:
    """Respect the minimum gap between steps and the back-off after a failed attempt."""
    last_sent = seq.get("last_sent_at")
    if last_sent and now - datetime.fromisoformat(last_sent) < MIN_GAP:
        return False
    last_failed = seq.get("last_failed_at")
    return not (last_failed and now - datetime.fromisoformat(last_failed) < RETRY_AFTER)


def _compose(lead: dict, step: dict, cfg: dict, offer: dict, base_url: str, now: datetime, today: date):
    """Return (message, demo fields), or None when a {{placeholder}} would be left unfilled."""
    lead = ensure_demo(lead, now)
    extra = {"offer_line": offer_line(offer), "demo_link": f"{base_url}/demo/{lead['demo_token']}",
             "include_link": bool(cfg.get(getattr(sequences, "INCLUDE_LINK_SETTING", "email1_include_link")))}
    variables = lead_variables(lead, cfg["sender_name"], base_url, extra)
    pick = getattr(sequences, "pick_variant", None)
    step = pick(step, variables) if pick else step
    subject, missing_subject = render(step["subject"], variables)
    body_text, missing_body = render(step["body"], variables)
    if missing_subject or missing_body:
        return None
    message = {"lead_id": lead["id"], "to": lead["email"], "subject": subject, "body": body_text, "step": step["name"],
               "sent_at": now.isoformat(timespec="seconds"), "sent_at_ist_date": today.isoformat()}
    return message, {k: lead[k] for k in ("demo_token", "referral_code", "activation")}


def _set_sequence(store, lead_id: str, **changes) -> None:
    store.update("leads", lambda rows: [{**r, "sequence": {**r["sequence"], **changes}} if r["id"] == lead_id else r for r in rows])


def _record_failure(lead: dict, now: datetime) -> dict:
    seq = {k: v for k, v in lead["sequence"].items() if k != "in_flight"}
    attempts = seq.get("failed_attempts", 0) + 1
    seq = {**seq, "failed_attempts": attempts, "last_failed_at": now.isoformat(timespec="seconds")}
    if attempts >= MAX_ATTEMPTS:
        seq = {**seq, "stopped": "send_failed"}
    return {**lead, "sequence": seq}


def _record_send(lead: dict, day: int, message: dict, demo: dict, now: datetime) -> dict:
    """Mark a sent step on the latest saved copy of the lead (a reply saved meanwhile is kept)."""
    seq = {k: v for k, v in lead["sequence"].items() if k not in ("in_flight", "failed_attempts", "last_failed_at", "blocked")}
    sent_days = seq.get("sent_days", [])
    event = {"at": now.isoformat(timespec="seconds"), "kind": "email", "text": f"{message['step']} — {message['status']}"}
    return {**{k: v for k, v in demo.items() if not lead.get(k)}, **lead,
            "stage": "contacted" if lead["stage"] in EARLY_STAGES else lead["stage"],
            "sequence": {**seq, "sent_days": sent_days if day in sent_days else [*sent_days, day],
                         "last_sent_at": now.isoformat(timespec="seconds")},
            "timeline": [*lead["timeline"], event]}
