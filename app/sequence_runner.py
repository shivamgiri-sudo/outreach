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

from .template_vars import brand_variables
from . import email_domain, email_gate, provenance
from . import sequence_library, sequences
from .channels.security import server_secret
from .growth_core import ensure_demo, get_offer, offer_line
from .hygiene import business_domain
from .icp import gate_reason as icp_gate_reason, get_icp
from .privacy import is_blocked
from .senders import email_ready
from .sequences import DEFAULT_DAILY_CAP, IST, due_steps, in_business_hours, lead_variables, render

DEFAULT_SENDER = "BookMyInterview team"     # kept for older imports; the brand (app/brand.py) decides the sender
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
MISSING_SEQUENCE = "the sequence this lead was started on no longer exists"
_RUN_LOCK = threading.Lock()


def get_settings(store) -> dict:
    from .brand import sender_name
    settings = store.read("settings")
    return {"daily_cap": DEFAULT_DAILY_CAP, "per_run_cap": DEFAULT_PER_RUN_CAP, "live_sending": False, **settings,
            "sender_name": sender_name(settings)}


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
    from .platform_controls import stop_reason
    if (stopped := stop_reason(store)):
        return {"sent": 0, "skipped": stopped}
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

    gate_live = email_gate.for_run(store, True)       # "verify before the first email" (app/email_gate.py); None = off
    icp_rules = get_icp(store)
    _decide_ab_winners(store)
    from . import account_limits, account_sending
    amap, outbox_all, other = account_sending.accounts.ws_account_map(store), store.read("outbox"), {}
    mcn_vars = (cfg, live, days_key, last_key, mode_status, counts["mode"])
    for lead in sorted(store.read("leads"), key=lambda l: (l.get("sequence") or {}).get("enrolled_at", "")):
        seq = lead.get("sequence")
        if not seq or seq["stopped"] or lead["stage"] in NO_EMAIL_STAGES + WARM_STAGES or not lead.get("email"):
            continue
        if seq.get("in_flight"):
            counts["needs_check"] += 1
            continue
        if len(todays) + counts["sent"] >= cap or counts["sent"] >= mcn_vars[0]["per_run_cap"]:
            break
        acct = account_sending.for_lead(store, lead, mcn_vars[0], mcn_vars[1], amap)     # another client's lead: its own
        if acct["account"] == "mcn":                                                      # identity, switch and cap
            cfg, live, days_key, last_key, mode_status, mode_name = mcn_vars
        else:
            if acct["hold"]:
                counts["blocked"] += 1          # no sending identity of its own and no grant: held, with the reason
                if seq.get("blocked") != acct["hold"]:
                    _set_sequence(store, lead["id"], blocked=acct["hold"])
                continue
            if account_limits.room(store, acct["account"], "daily_sends"):
                continue                        # the account's daily sends limit is used up (message on its Usage card)
            cfg, live = acct["cfg"], acct["live"]
            (days_key, last_key), mode_status, mode_name = _mode_keys(live), "sent" if live else "dry_run", "live" if live else "practice"
            done = other.setdefault(acct["account"], account_sending.sent_today(outbox_all, acct["account"], mode_status, today.isoformat()))
            if done >= cfg["daily_cap"]:
                continue
        if not _ready(seq, now, last_key):
            continue
        if live and sequences.needs_postal_address(lead) and not cfg.get("sender_address"):
            counts["blocked"] += 1          # US/Canada law requires a postal address in the footer; none is set
            continue
        if (lead.get("email_check") or {}).get("status") == "invalid":
            counts["blocked"] += 1          # a mailbox check said this address does not exist: never send to it
            _set_sequence(store, lead["id"], blocked="the email address failed a mailbox check")
            continue
        if (bad := email_domain.problem(lead["email"])):
            counts["blocked"] += 1          # the domain does not exist / takes no mail (e.g. a typo): it would only bounce
            if seq.get("blocked") != bad:
                _set_sequence(store, lead["id"], blocked=bad)
            continue
        if live and gate_live and email_gate.is_first_email(lead):
            what, why = email_gate.decision(lead, gate_live["ready"], gate_live["approve_risky"])
            if what == "block":
                counts["blocked"] += 1
                _set_sequence(store, lead["id"], blocked=why)
                continue
            if what in ("check", "hold"):
                counts["held"] = counts.get("held", 0) + 1      # waits for a mailbox check; looked at again next run
                if seq.get("blocked") != why:
                    _set_sequence(store, lead["id"], blocked=why)
                continue
            email_gate.clear_stale_note(store, lead)
        held = icp_gate_reason(lead, icp_rules)
        if held:
            counts["blocked"] += 1          # outside the ideal-customer score the owner set: held, never deleted
            if seq.get("blocked") != held:
                _set_sequence(store, lead["id"], blocked=held)
            continue
        plan = sequence_library.steps_for(store, seq)
        if plan is None:
            counts["blocked"] += 1          # the saved sequence (or its version) is gone: hold, never guess another
            if seq.get("blocked") != MISSING_SEQUENCE:
                _set_sequence(store, lead["id"], blocked=MISSING_SEQUENCE)
            continue
        last = seq.get(last_key)
        steps = due_steps(datetime.fromisoformat(seq["enrolled_at"]), seq.get(days_key, []), now, sequence=plan,
                          last_sent_at=datetime.fromisoformat(last) if last else None)
        domain = business_domain(lead["email"])
        if not steps or (domain and domain in domains_today):
            continue
        step = _lead_step(steps[0], lead)
        composed = _compose(lead, step, cfg, offer, base_url, now, today)
        if composed is None:
            counts["blocked"] += 1
            _set_sequence(store, lead["id"], blocked=_blocked_text(missing_details(lead, step, cfg, offer, base_url, now)))
            continue
        message, demo = composed
        _tag(message, step, seq)
        day = steps[0]["day"]
        message["origin"] = provenance.for_sequence(store, lead, seq, step, day, live, cfg)
        key = f"{lead['id']}:{day}:{mode_name}"
        account_sending.stamp(message, acct)
        # The Message-ID comes from the key: a retry must be the same request, or Resend refuses it (409).
        message.update({"idempotency_key": key,
                        "message_id": f"<{uuid.uuid5(uuid.NAMESPACE_URL, key).hex}.{lead['id']}@{_message_domain(store)}>"})
        if not _claim(store, lead["id"], day, now, lead["email"], demo):
            continue                                   # stopped, unsubscribed or suppressed a moment ago
        message.update(sender(message))
        if message["status"] == "held" or (acct["account"] != "mcn" and message["status"] == "provider_error"):
            _release(store, lead["id"])        # another account's keys or grant: never pauses Mas Callnet's sending
            counts["blocked" if message["status"] == "held" else "failed"] += 1
            _set_sequence(store, lead["id"], blocked=message.get("error") or "the account's email provider refused it")
            continue
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
        if acct["account"] != "mcn":
            account_limits.record(store, acct["account"], "daily_sends")
            other[acct["account"]] = other.get(acct["account"], 0) + 1
            counts["other_accounts_sent"] = counts.get("other_accounts_sent", 0) + 1
            continue                           # Mas Callnet's daily cap and company spacing are its own
        counts["sent"] += 1
        if domain:
            domains_today.add(domain)
    return counts


def _decide_ab_winners(store) -> None:
    """Save any A/B winner that is now clear, before this run picks variants. Never stops sending if it fails."""
    try:
        sequence_library.evaluate_winners(store)
    except Exception:                                     # noqa: BLE001 - statistics must not block the sender
        pass


def _lead_step(step: dict, lead: dict) -> dict:
    """The step as this lead gets it: saved sequences get their A/B variant and the unsubscribe guarantee.
    The built-in default sequence is passed through untouched (old leads behave exactly as before)."""
    seq_id = (lead.get("sequence") or {}).get("sequence_id") or sequence_library.DEFAULT_ID
    if seq_id == sequence_library.DEFAULT_ID:
        return step
    return sequence_library.prepare_step(step, lead["id"], seq_id)


def _tag(message: dict, step: dict, seq: dict) -> None:
    """Saved-sequence sends carry which sequence/version/step/variant they were (for the A/B counts)."""
    seq_id = seq.get("sequence_id") or sequence_library.DEFAULT_ID
    if seq_id == sequence_library.DEFAULT_ID:
        return
    message.update({"sequence_id": seq_id, "sequence_version": seq.get("version", 1), "step_day": step["day"],
                    "variant": step.get("ab_variant", "A")})


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


def _prepare(lead: dict, step: dict, cfg: dict, offer: dict, base_url: str, now: datetime) -> tuple:
    lead = ensure_demo(lead, now)
    extra = {**brand_variables(cfg), "offer_line": offer_line(offer), "demo_link": f"{base_url}/demo/{lead['demo_token']}",
             "include_link": bool(cfg.get(getattr(sequences, "INCLUDE_LINK_SETTING", "email1_include_link")))}
    variables = lead_variables(lead, cfg["sender_name"], base_url, extra, secret=cfg.get("server_secret", ""), brand=cfg)
    pick = getattr(sequences, "pick_variant", None)
    return lead, (pick(step, variables) if pick else step), variables


def missing_details(lead: dict, step: dict, cfg: dict, offer: dict, base_url: str, now: datetime) -> list:
    """The {{names}} this step needs that the lead (or Brand settings) cannot fill, e.g. ["project"]."""
    lead, step, variables = _prepare(lead, step, cfg, offer, base_url, now)
    names = render(step["subject"], variables)[1] + render(step["body"], variables)[1]
    return list(dict.fromkeys(names))


def _blocked_text(missing: list) -> str:
    # Name the detail: "a detail is missing" alone left the owner guessing which one to fill in.
    return "a detail needed by the email is missing" + (f": {', '.join('{{' + m + '}}' for m in missing[:5])}" if missing else "")


def _compose(lead: dict, step: dict, cfg: dict, offer: dict, base_url: str, now: datetime, today: date):
    """Return (message, demo fields), or None when a {{placeholder}} would be left unfilled."""
    lead, step, variables = _prepare(lead, step, cfg, offer, base_url, now)
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


def send_now(store, lead_id: str, now: datetime, base_url: str, sender, by: str = "", by_email: str = "") -> dict:
    """A person presses "Send email now" for ONE lead: its next due sequence email goes at once.

    Same compose / claim / record path as the scheduled run, so the sequence carries on normally afterwards. A person chose
    it, so the daily cap and the one-email-per-company-a-day spacing do not apply; unsubscribed, suppressed, bounced,
    erased, failed-mailbox-check and warm/closed leads are still refused, and only a step that is due now can go."""
    server_secret(store)
    if not _RUN_LOCK.acquire(timeout=30):
        raise ValueError("Another send run is in progress. Try again in a minute.")
    try:
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None:
            raise LookupError("Lead not found")
        seq = lead.get("sequence")
        if not seq:
            raise ValueError("Start the email sequence for this lead first.")
        if seq.get("stopped"):
            raise ValueError(f"The sequence is stopped ({seq['stopped']}).")
        if lead["stage"] in NO_EMAIL_STAGES + WARM_STAGES:
            raise ValueError(f"This lead is {lead['stage']}; no sequence emails.")
        if not lead.get("email") or is_blocked(store, lead["email"]) or lead["email"] in set(store.read("suppressed")):
            raise ValueError("This address may not be emailed (unsubscribed, bounced, erased or on the do-not-contact list).")
        if (lead.get("email_check") or {}).get("status") == "invalid":
            raise ValueError("A mailbox check said this address does not exist.")
        if (bad := email_domain.problem(lead["email"])):
            raise ValueError(f"Not sent: {bad}.")
        if seq.get("in_flight"):
            raise ValueError("An email to this lead is being sent right now.")
        cfg = get_settings(store)
        live = bool(cfg["live_sending"]) and bool(email_ready(store))
        from . import account_sending
        acct = account_sending.for_lead(store, lead, cfg, live)         # another client's lead: its own identity
        if acct["hold"]:
            raise ValueError(acct["hold"])
        from . import account_limits
        if (full := account_limits.room(store, acct["account"], "daily_sends")):
            raise ValueError(full)
        cfg, live = acct["cfg"], acct["live"]
        if live and (pause := sending_paused(store, now)):
            raise ValueError(f"Sending is paused: {pause['reason']}")
        if live and sequences.needs_postal_address(lead) and not cfg.get("sender_address"):
            raise ValueError("US/Canada addresses need a postal address in the footer; add it in Outbox settings.")
        days_key, last_key = _mode_keys(live)
        plan = sequence_library.steps_for(store, seq)
        if plan is None:
            raise ValueError(f"Not sent: {MISSING_SEQUENCE}.")
        last = seq.get(last_key)
        steps = due_steps(datetime.fromisoformat(seq["enrolled_at"]), seq.get(days_key, []), now, sequence=plan,
                          last_sent_at=datetime.fromisoformat(last) if last else None)
        if not steps:
            raise ValueError("No email is due for this lead right now (the next one has its own day in the sequence).")
        today = now.astimezone(IST).date()
        step = _lead_step(steps[0], lead)
        composed = _compose(lead, step, cfg, get_offer(store), base_url, now, today)
        if composed is None:
            why = _blocked_text(missing_details(lead, step, cfg, get_offer(store), base_url, now))
            raise ValueError(why[0].upper() + why[1:] + " for this lead.")
        message, demo = composed
        _tag(message, step, seq)
        day = steps[0]["day"]
        key = f"{lead['id']}:{day}:{'live' if live else 'practice'}"
        message["origin"] = provenance.for_sequence(store, lead, seq, step, day, live, cfg, "send_now", provenance.person(by, by_email))
        message.update({"idempotency_key": key, "by": by, "manual": True,
                        "message_id": f"<{uuid.uuid5(uuid.NAMESPACE_URL, key).hex}.{lead['id']}@{_message_domain(store)}>"})
        account_sending.stamp(message, acct)
        if not _claim(store, lead["id"], day, now, lead["email"], demo):
            raise ValueError("This lead may not be emailed right now.")
        message.update(sender(message))
        if message["status"] == "held":
            _release(store, lead["id"])
            raise ValueError(message.get("error") or "Sending is held for this account.")
        store.update("outbox", lambda rows: [*rows, message])
        if message["status"] in ("provider_error", "failed"):
            _release(store, lead["id"])
            raise ValueError(f"The email provider did not take it: {message.get('error') or message['status']}")
        account_limits.record(store, acct["account"], "daily_sends")
        store.update_lead(lead["id"], lambda r: (lambda saved: {**saved, "timeline": [*saved["timeline"][:-1], {**saved["timeline"][-1], "by": by}]})(
            _record_send(r, day, message, demo, now, days_key, last_key)))
        return {"status": message["status"], "step": message["step"], "to": message["to"], "mode": "live" if live else "practice"}
    finally:
        _RUN_LOCK.release()


# One-off emails a person writes live in app/manual_send.py (kept here as names other modules import).
from .manual_send import MAX_MANUAL_BODY, MAX_MANUAL_SUBJECT, send_manual  # noqa: E402,F401
