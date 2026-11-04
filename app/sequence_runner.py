"""Send the next due email step for every enrolled lead. Used by the API and the background timer.

Safety rules, in order: one run at a time; a send is claimed before it goes out (a crash never repeats it);
cold steps stop once a lead is warm or closed; at least two days between steps; a few sends per run so the
day's volume is spread out; one email per company domain per day; a warm-up ramp for live sending; failed
sends back off and stop after three attempts; nothing goes out with an unfilled {{placeholder}}.

Practice runs (nothing actually sent) are recorded separately (`practice_days`), so rehearsing never uses up a
lead's real Email 1. A provider-wide problem (bad key, rate limit, outage) pauses all sending instead of
counting as a failure against every lead; each message carries an idempotency key so a retry after a timeout
can't deliver it twice.
"""
import re
import threading
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from . import sequences
from .channels.security import server_secret
from .growth_core import ensure_demo, get_offer, offer_line
from .hygiene import business_domain
from .privacy import is_blocked
from .senders import email_ready
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
PROVIDER_PAUSE = timedelta(hours=1)
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


def sending_paused(store, now: datetime):
    """The active provider pause, or None. A pause with `resume_at` lifts itself once that time has passed."""
    pause = store.read("settings").get("sending_paused")
    if not pause:
        return None
    resume_at = pause.get("resume_at")
    if resume_at and now >= datetime.fromisoformat(resume_at):
        store.update("settings", lambda s: {k: v for k, v in s.items() if k != "sending_paused"})
        return None
    return pause


def _pause_sending(store, now: datetime, reason: str, auto_resume: bool) -> None:
    pause = {"reason": reason, "at": now.isoformat(timespec="seconds"),
             "resume_at": (now + PROVIDER_PAUSE).isoformat(timespec="seconds") if auto_resume else None}
    store.update("settings", lambda s: {**s, "sending_paused": pause})


def _mode_keys(live: bool) -> tuple:
    return ("sent_days", "last_sent_at") if live else ("practice_days", "practice_last_at")


def _run(store, now: datetime, base_url: str, sender) -> dict:
    server_secret(store)                      # unsubscribe links are signed with it; make sure it exists
    cfg = get_settings(store)
    live = bool(cfg["live_sending"]) and bool(email_ready(store))
    if live and (pause := sending_paused(store, now)):
        return {"sent": 0, "skipped": f"sending is paused: {pause['reason']}"}
    days_key, last_key = _mode_keys(live)
    mode_status = "sent" if live else "dry_run"
    offer = get_offer(store)
    today = now.astimezone(IST).date()
    sent_ok = [m for m in store.read("outbox") if m.get("status") == mode_status]
    live_dates = [date.fromisoformat(m["sent_at_ist_date"]) for m in sent_ok if live]
    cap = effective_daily_cap(cfg["daily_cap"], live, min(live_dates, default=None), today)
    todays = [m for m in sent_ok if m["sent_at_ist_date"] == today.isoformat()]
    domains_today = {business_domain(m["to"]) for m in todays} - {None}
    counts = {"sent": 0, "failed": 0, "needs_check": 0, "blocked": 0, "mode": "live" if live else "practice"}

    for lead in sorted(store.read("leads"), key=lambda l: (l.get("sequence") or {}).get("enrolled_at", "")):
        seq = lead.get("sequence")
        if not seq or seq["stopped"] or lead["stage"] in NO_EMAIL_STAGES + WARM_STAGES or not lead.get("email"):
            continue
        if seq.get("in_flight"):
            counts["needs_check"] += 1
            continue
        if len(todays) + counts["sent"] >= cap or counts["sent"] >= cfg["per_run_cap"]:
            break
        if not _ready(seq, now, last_key):
            continue
        if live and sequences.needs_postal_address(lead) and not cfg.get("sender_address"):
            counts["blocked"] += 1          # US/Canada law requires a postal address in the footer; none is set
            continue
        if (lead.get("email_check") or {}).get("status") == "invalid":
            counts["blocked"] += 1          # a mailbox check said this address does not exist: never send to it
            _set_sequence(store, lead["id"], blocked="the email address failed a mailbox check")
            continue
        last = seq.get(last_key)
        steps = due_steps(datetime.fromisoformat(seq["enrolled_at"]), seq.get(days_key, []), now,
                          last_sent_at=datetime.fromisoformat(last) if last else None)
        domain = business_domain(lead["email"])
        if not steps or (domain and domain in domains_today):
            continue
        composed = _compose(lead, steps[0], cfg, offer, base_url, now, today)
        if composed is None:
            counts["blocked"] += 1
            _set_sequence(store, lead["id"], blocked="a detail needed by the email is missing")
            continue
        message, demo = composed
        day = steps[0]["day"]
        key = f"{lead['id']}:{day}:{counts['mode']}"
        # The Message-ID comes from the key: a retry must be the same request, or Resend refuses it (409).
        message.update({"idempotency_key": key,
                        "message_id": f"<{uuid.uuid5(uuid.NAMESPACE_URL, key).hex}.{lead['id']}@{_message_domain(store)}>"})
        if not _claim(store, lead["id"], day, now, lead["email"], demo):
            continue                                   # stopped, unsubscribed or suppressed a moment ago
        message.update(sender(message))
        store.update("outbox", lambda rows: [*rows, message])
        if message["status"] == "provider_error":
            counts["failed"] += 1
            _release(store, lead["id"])
            _pause_sending(store, now, message.get("error", "the email provider is not accepting messages"),
                           auto_resume=message.get("retryable", True))
            counts["paused"] = message.get("error", "")
            break
        if message["status"] == "failed":
            counts["failed"] += 1
            store.update_lead(lead["id"], lambda r: _record_failure(r, now))
            continue
        store.update_lead(lead["id"], lambda r: _record_send(r, day, message, demo, now, days_key, last_key))
        counts["sent"] += 1
        if domain:
            domains_today.add(domain)
    return counts


def _message_domain(store) -> str:
    cfg = email_ready(store) or {}
    return (cfg.get("from_email", "").rpartition("@")[2] or cfg.get("domain") or "outreach.local").strip() or "outreach.local"


def _claim(store, lead_id: str, day: int, now: datetime, email: str, demo: dict = None) -> bool:
    """Mark the send as in progress on the latest saved lead, but only if it may still be emailed right now.

    The demo link fields are saved now too, so a retry after a provider error sends the very same text."""
    if is_blocked(store, email):                 # unsubscribed, bounced, or erased on request (hashed list)
        return False
    suppressed = set(store.read("suppressed"))
    claimed = {}

    def claim(lead: dict):
        seq = lead.get("sequence") or {}
        if (not seq or seq.get("stopped") or seq.get("in_flight") or lead["stage"] in NO_EMAIL_STAGES + WARM_STAGES
                or lead.get("email") != email or email in suppressed):
            return None
        claimed["ok"] = True
        return {**{k: v for k, v in (demo or {}).items() if not lead.get(k)}, **lead,
                "sequence": {**seq, "in_flight": {"day": day, "at": now.isoformat(timespec="seconds")}}}

    store.update_lead(lead_id, claim)
    return bool(claimed)


def _release(store, lead_id: str) -> None:
    store.update_lead(lead_id, lambda r: {**r, "sequence": {k: v for k, v in r["sequence"].items() if k != "in_flight"}})


def _ready(seq: dict, now: datetime, last_key: str = "last_sent_at") -> bool:
    """Respect the minimum gap between steps and the back-off after a failed attempt."""
    last_sent = seq.get(last_key)
    if last_sent and now - datetime.fromisoformat(last_sent) < MIN_GAP:
        return False
    last_failed = seq.get("last_failed_at")
    return not (last_failed and now - datetime.fromisoformat(last_failed) < RETRY_AFTER)


def _compose(lead: dict, step: dict, cfg: dict, offer: dict, base_url: str, now: datetime, today: date):
    """Return (message, demo fields), or None when a {{placeholder}} would be left unfilled."""
    lead = ensure_demo(lead, now)
    extra = {"offer_line": offer_line(offer), "demo_link": f"{base_url}/demo/{lead['demo_token']}",
             "include_link": bool(cfg.get(getattr(sequences, "INCLUDE_LINK_SETTING", "email1_include_link")))}
    variables = lead_variables(lead, cfg["sender_name"], base_url, extra, secret=cfg.get("server_secret", ""))
    pick = getattr(sequences, "pick_variant", None)
    step = pick(step, variables) if pick else step
    subject, missing_subject = render(step["subject"], variables)
    body_text, missing_body = render(step["body"], variables)
    if missing_subject or missing_body:
        return None
    if re.search(r"\{\{|\}\}", str(lead.get("company") or "")):
        return None                              # a mail-merge leftover as the company name: never email that lead
    footer = sequences.identity_footer(cfg)
    message = {"lead_id": lead["id"], "to": lead["email"], "subject": subject,
               "body": body_text + (f"\n{footer}" if footer else ""), "step": step["name"],
               "variant": step.get("variant", "default"),
               "sent_at": now.isoformat(timespec="seconds"), "sent_at_ist_date": today.isoformat()}
    return message, {k: lead[k] for k in ("demo_token", "referral_code", "activation")}


def _set_sequence(store, lead_id: str, **changes) -> None:
    store.update_lead(lead_id, lambda r: {**r, "sequence": {**r["sequence"], **changes}})


def _record_failure(lead: dict, now: datetime) -> dict:
    seq = {k: v for k, v in lead["sequence"].items() if k != "in_flight"}
    attempts = seq.get("failed_attempts", 0) + 1
    seq = {**seq, "failed_attempts": attempts, "last_failed_at": now.isoformat(timespec="seconds")}
    if attempts >= MAX_ATTEMPTS:
        seq = {**seq, "stopped": "send_failed"}
    return {**lead, "sequence": seq}


def _record_send(lead: dict, day: int, message: dict, demo: dict, now: datetime,
                 days_key: str = "sent_days", last_key: str = "last_sent_at") -> dict:
    """Mark a sent (or practised) step on the latest saved copy of the lead (a reply saved meanwhile is kept)."""
    live = days_key == "sent_days"
    seq = {k: v for k, v in lead["sequence"].items() if k not in ("in_flight", "failed_attempts", "last_failed_at", "blocked")}
    done = seq.get(days_key, [])
    label = message["status"] if live else "practice only, not sent"
    event = {"at": now.isoformat(timespec="seconds"), "kind": "email" if live else "practice", "text": f"{message['step']} — {label}"}
    return {**{k: v for k, v in demo.items() if not lead.get(k)}, **lead,
            "stage": "contacted" if live and lead["stage"] in EARLY_STAGES else lead["stage"],
            "sequence": {**seq, days_key: done if day in done else [*done, day], last_key: now.isoformat(timespec="seconds")},
            "timeline": [*lead["timeline"], event]}


def resume_lead(store, lead_id: str):
    """Restart a sequence that stopped after repeated send failures, keeping what was already sent.

    Returns None when the lead is missing or was not stopped by send failures (replies and unsubscribes stay stopped)."""
    resumed = {}

    def resume(lead: dict):
        seq = lead.get("sequence") or {}
        if seq.get("stopped") != "send_failed":
            return None
        resumed["ok"] = True
        cleaned = {k: v for k, v in seq.items() if k not in ("failed_attempts", "last_failed_at", "in_flight")}
        return {**lead, "sequence": {**cleaned, "stopped": None},
                "timeline": [*lead["timeline"], {"at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                                 "kind": "sequence", "text": "Sequence resumed after send failures"}]}
    lead = store.update_lead(lead_id, resume)
    return lead if resumed else None


def clear_practice(store) -> dict:
    """Forget practice runs: remove practice outbox rows and practice progress, so live sending starts fresh."""
    removed = {"outbox": 0}

    def drop(rows):
        kept = [m for m in rows if m.get("status") != "dry_run"]
        removed["outbox"] = len(rows) - len(kept)
        return kept
    store.update("outbox", drop)
    store.update("leads", lambda rows: [{**r, "sequence": {k: v for k, v in r["sequence"].items()
                                                           if k not in ("practice_days", "practice_last_at")}}
                                        if r.get("sequence") else r for r in rows])
    return removed
