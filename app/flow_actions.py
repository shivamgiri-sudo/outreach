"""What each flow step does for one lead: conditions (if / split), wait timing, and the actions.

Every message goes through the code the rest of the app already uses, so a flow can never get round a rule:
- email: the sequence engine's composer (`sequence_runner._compose`), the same sender, the same outbox, daily cap,
  warm-up, per-run cap, one email per company domain per day, Mon–Fri 09:00–18:00 IST, provider pause, postal-address
  rule, practice vs live, idempotency key and Message-ID, and the sequence engine's send lock (one send run at a time);
- WhatsApp: `app.whatsapp.send` (consent, phone, closed stages, templates only, 24-hour window for free text);
- SMS: `send_sms` (dry run unless connected and "SMS for real" is on), and only with recorded consent;
- stage changes: `stages.can_move`, and closed leads stay closed;
- webhooks: the SSRF guard (`netguard.check_public_url`) and the no-redirect HTTP helper.
Handlers return an outcome: ok / refused (follow the error path or stop) / defer (try again later) / idle.
"""
import hashlib
import uuid
from datetime import date, datetime, time, timedelta, timezone

from . import linkedin_seq, sequence_runner, sequences
from .activities import add_task, caller_name, next_call_slot, on_became_interested
from .channels.http import request
from .channels.netguard import check_public_url
from .channels.sms_send import send_sms
from .flows import IF_FIELDS, title_of
from .growth_core import ensure_demo, get_offer
from .hygiene import business_domain
from .privacy import is_blocked
from .reminders import notify
from .scoring import score_lead
from .senders import email_ready
from .sequence_runner import (EARLY_STAGES, MAX_ATTEMPTS, NO_EMAIL_STAGES, RETRY_AFTER, effective_daily_cap,
                              get_settings, sending_paused)
from .sequences import BUSINESS_START_HOUR, IST, in_business_hours, lead_variables, render
from .stages import CONSENT_REQUIRED, can_move
from .whatsapp import WhatsAppError, send as send_whatsapp_message

PROVIDER_RETRY = timedelta(hours=1)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def ok(result: str, note: bool = True) -> dict:
    return {"kind": "ok", "result": result, "note": note}


def refused(reason: str) -> dict:
    return {"kind": "refused", "result": reason}


def defer(until: datetime, reason: str) -> dict:
    return {"kind": "defer", "until": until, "result": reason}


def idle(reason: str = "") -> dict:
    return {"kind": "idle", "result": reason}


# ---------------------------------------------------------------- time
def next_business_time(when: datetime) -> datetime:
    """`when` if it is inside Mon–Fri 09:00–18:00 IST, else the next weekday at 09:00 IST."""
    if in_business_hours(when):
        return when
    local = when.astimezone(IST)
    day = local.date()
    if local.weekday() < 5 and local.hour < BUSINESS_START_HOUR:
        return datetime.combine(day, time(BUSINESS_START_HOUR), tzinfo=IST).astimezone(timezone.utc)
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, time(BUSINESS_START_HOUR), tzinfo=IST).astimezone(timezone.utc)


def wait_until(config: dict, now: datetime) -> datetime:
    target = now + timedelta(days=int(config.get("days") or 0), hours=int(config.get("hours") or 0))
    return next_business_time(target) if config.get("business_hours") else target


def ist_text(when: datetime) -> str:
    return when.astimezone(IST).strftime("%a %d %b, %I:%M %p IST").replace(" 0", " ")


def _next_morning(now: datetime) -> datetime:
    local = now.astimezone(IST)
    tomorrow = datetime.combine(local.date() + timedelta(days=1), time(BUSINESS_START_HOUR), tzinfo=IST)
    return next_business_time(tomorrow.astimezone(timezone.utc))


# ---------------------------------------------------------------- conditions
def lead_facts(lead: dict) -> dict:
    """The values an If step can check, worked out from the lead the same way the Leads page shows them."""
    scored = score_lead({**lead, "consent_yes": bool(lead.get("consent"))})
    replied = (lead.get("engagement") or {}).get("replies", 0) > 0 or any(e.get("kind") == "reply" for e in lead.get("timeline", []))
    return {"score": scored["score"], "band": scored["band"], "stage": lead.get("stage", ""), "city": lead.get("city") or "",
            "country": lead.get("country") or "", "title": lead.get("title") or "",
            "persona": linkedin_seq.persona(lead.get("title", ""))[0], "source": lead.get("source") or "",
            "campaign_id": lead.get("campaign_id") or "", "tag": list(lead.get("tags") or []),
            "open_roles": lead.get("open_roles") or 0, "has_email": bool(lead.get("email")), "has_phone": bool(lead.get("phone")),
            "has_consent": bool(lead.get("consent")), "has_replied": replied,
            "has_meeting": any(m.get("status") in ("scheduled", "done") for m in lead.get("meetings") or [])}


def _items(value) -> list:
    raw = value if isinstance(value, list) else str(value or "").split(",")
    return [str(v).strip().lower() for v in raw if str(v).strip()]


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def evaluate(config: dict, facts: dict) -> tuple:
    """(True/False, plain explanation) for an If step."""
    field, op, value = config.get("field"), config.get("op"), config.get("value")
    kind, actual = IF_FIELDS.get(field, "text"), facts.get(field)
    shown = ", ".join(actual) if isinstance(actual, list) else actual
    if kind == "bool":
        want = str(value).lower() in ("true", "yes", "1")
        result = bool(actual) if op == "exists" else (bool(actual) == want) if op == "eq" else (bool(actual) != want)
        return result, f"{field.replace('_', ' ')} is {'yes' if actual else 'no'}"
    if kind == "tags":
        tags = {str(t).strip().lower() for t in actual or []}
        wanted = _items(value)
        result = {"eq": bool(wanted) and wanted[0] in tags, "contains": bool(wanted) and wanted[0] in tags,
                  "ne": not wanted or wanted[0] not in tags, "in": any(w in tags for w in wanted),
                  "exists": bool(tags)}.get(op, False)
        return result, f"tags are {shown or 'none'}"
    if kind == "number":
        a = _num(actual)
        result = {"gt": a > _num(value), "lt": a < _num(value), "eq": a == _num(value), "ne": a != _num(value),
                  "in": any(a == _num(v) for v in _items(value)), "contains": str(value) in str(actual),
                  "exists": a != 0}.get(op, False)
        return result, f"{field.replace('_', ' ')} is {actual}"
    a, v = str(actual or "").strip().lower(), str(value or "").strip().lower()
    result = {"eq": a == v, "ne": a != v, "contains": bool(v) and v in a, "in": a in _items(value), "exists": bool(a)}.get(op, False)
    return result, f"{field.replace('_', ' ')} is {actual or 'empty'}"


def split_variant(lead_id: str, node_id: str, percent_a) -> str:
    """Sticky A/B: the same lead always gets the same variant of the same split step."""
    bucket = int(hashlib.sha256(f"{lead_id}:{node_id}".encode()).hexdigest()[:8], 16) % 100
    return "a" if bucket < int(percent_a or 50) else "b"


# ---------------------------------------------------------------- message checks shared with the simulation
def blocked_reason(store, lead: dict):
    """Why no message of any kind may go to this lead, or None."""
    if lead.get("stage") in ("unsubscribed", "disqualified"):
        return f"lead is {lead['stage']}"
    suppressed = set(store.read("suppressed"))
    if lead.get("email") and lead["email"] in suppressed:
        return "their email address is on the do-not-contact list"
    if lead.get("phone") and lead["phone"] in suppressed:
        return "their phone number is on the do-not-contact list"
    if is_blocked(store, lead.get("email"), lead.get("phone")):          # erased on request (hashed list)
        return "their details were erased on request (do-not-contact list)"
    if (lead.get("sequence") or {}).get("stopped") in ("bounced", "unsubscribed", "complained"):
        return f"their email {lead['sequence']['stopped']}"
    return None


def email_step(flow: dict, node: dict) -> dict:
    c = node.get("config") or {}
    step = {"name": f"Flow {flow['name']}: {title_of(node)}", "subject": c.get("subject", ""), "body": c.get("body", "")}
    for key in ("subject_no_role", "body_no_role"):
        if c.get(key):
            step[key] = c[key]
    return step


def compose_email(store, flow: dict, node: dict, lead: dict, now: datetime, base_url: str):
    """(message, demo fields) exactly as the sequence engine would build it, or None if a placeholder is unfilled."""
    cfg = get_settings(store)
    return sequence_runner._compose(lead, email_step(flow, node), cfg, get_offer(store), base_url, now, now.astimezone(IST).date())


def text_for(store, lead: dict, text: str, base_url: str, now: datetime) -> tuple:
    lead = ensure_demo(lead, now)
    variables = lead_variables(lead, get_settings(store)["sender_name"], base_url, {"demo_link": f"{base_url}/demo/{lead['demo_token']}"})
    return render(text, variables)


def _owner_or_caller(store, name) -> str:
    return str(name or "").strip() or caller_name(store)


# ---------------------------------------------------------------- email
def do_email(store, flow, run, node, lead, now, ctx, claim, release) -> dict:
    """One flow email through the cold-email engine's rules. Defers (no claim) while a cap or hour rule says wait."""
    cfg = get_settings(store)
    live = bool(cfg["live_sending"]) and bool(email_ready(store))
    if lead["stage"] in NO_EMAIL_STAGES:
        return refused(f"lead is {lead['stage']} and cannot be emailed")
    if not lead.get("email"):
        return refused("this lead has no email address")
    if not ctx.ignore_hours and not in_business_hours(now):
        return defer(next_business_time(now), "waiting for business hours (Mon–Fri 9–18 IST)")
    if live and (pause := sending_paused(store, now)):
        return defer(now + PROVIDER_RETRY, f"sending is paused: {pause['reason']}")
    if live and sequences.needs_postal_address(lead) and not cfg.get("sender_address"):
        return defer(now + PROVIDER_RETRY, "a postal address is needed in the footer for this country (Setup)")
    attempt = (run.get("attempts") or {}).get(str(run["step"])) or {}
    if attempt.get("last_failed_at") and now - datetime.fromisoformat(attempt["last_failed_at"]) < RETRY_AFTER:
        return defer(datetime.fromisoformat(attempt["last_failed_at"]) + RETRY_AFTER, "retrying a failed send after an hour")
    mode_status = "sent" if live else "dry_run"
    today = now.astimezone(IST).date()
    sent_ok = [m for m in store.read("outbox") if m.get("status") == mode_status]
    first_live = min((date.fromisoformat(m["sent_at_ist_date"]) for m in sent_ok if live), default=None)
    cap = effective_daily_cap(cfg["daily_cap"], live, first_live, today)
    todays = [m for m in sent_ok if m.get("sent_at_ist_date") == today.isoformat()]
    if len(todays) >= cap:
        return defer(_next_morning(now), f"today's email limit ({cap}) is reached")
    if ctx.emails_sent >= cfg["per_run_cap"]:
        return idle("a few sends per run; the next tick continues")
    domain = business_domain(lead["email"])
    if domain and domain in {business_domain(m["to"]) for m in todays}:
        return defer(_next_morning(now), f"one email per company a day ({domain} already had one today)")
    composed = compose_email(store, flow, node, lead, now, ctx.base_url)
    if composed is None:
        return refused("a detail the email needs is missing (an unfilled {{placeholder}}), so nothing was sent")
    message, demo = composed
    mode = "live" if live else "practice"
    message.update({"idempotency_key": f"flow:{run['id']}:{run['step']}:{mode}",
                    "message_id": f"<{uuid.uuid4().hex}.{lead['id']}@{sequence_runner._message_domain(store)}>",
                    "flow_id": flow["id"], "flow_run_id": run["id"], "flow_node": node["id"], "flow_variants": run.get("variants") or {}})
    if not sequence_runner._RUN_LOCK.acquire(blocking=False):
        return idle("another send run is in progress")
    try:
        latest = next((l for l in store.read("leads") if l["id"] == lead["id"]), None)
        if latest is None or blocked_reason(store, latest) or latest.get("email") != lead["email"] or latest["stage"] in NO_EMAIL_STAGES:
            return {"kind": "recheck", "result": "the lead changed a moment ago"}
        if not claim():
            return idle("another tick took this step")
        message.update(ctx.sender(message))
        store.update("outbox", lambda rows: [*rows, message])
    finally:
        sequence_runner._RUN_LOCK.release()
    if message["status"] == "provider_error":
        release()
        sequence_runner._pause_sending(store, now, message.get("error", "the email provider is not accepting messages"),
                                       auto_resume=message.get("retryable", True))
        return defer(now + PROVIDER_RETRY, f"the email provider had a problem: {message.get('error', '')}")
    if message["status"] == "failed":
        count = int(attempt.get("count", 0)) + 1
        release({"attempts": {**(run.get("attempts") or {}), str(run["step"]): {"count": count, "last_failed_at": iso(now)}}})
        if count >= MAX_ATTEMPTS:
            return refused(f"the email failed {count} times ({message.get('error', 'unknown error')})")
        return defer(now + RETRY_AFTER, f"send failed ({message.get('error', 'unknown error')}); retrying in an hour")
    ctx.emails_sent += 1
    label = "sent" if live else "practice only, not sent"
    event = {"at": iso(now), "kind": "flow", "text": f"Flow {flow['name']}: email “{message['subject']}” — {label}"}

    def record(latest: dict) -> dict:
        stage = "contacted" if live and latest["stage"] in EARLY_STAGES else latest["stage"]
        return {**latest, **{k: v for k, v in demo.items() if not latest.get(k)}, "stage": stage,
                "timeline": [*latest["timeline"], event]}
    store.update_lead(lead["id"], record)
    return ok(f"email “{message['subject']}” — {label}", note=False)


# ---------------------------------------------------------------- other actions (claimed by the engine first)
def do_whatsapp(store, flow, node, lead, now, ctx) -> dict:
    template = (node.get("config") or {}).get("template")
    try:
        saved = send_whatsapp_message(store, lead["id"], template, "", now, ctx.base_url, ctx.transport)
    except WhatsAppError as exc:
        return refused(f"WhatsApp not sent: {exc}")
    status = next((m["status"] for m in reversed(store.read("whatsapp_outbox")) if m.get("lead_id") == lead["id"]), "dry_run")
    shown = "practice only, not sent" if status == "dry_run" else status
    return ok(f"WhatsApp {template} — {shown}" + (f" (stage now {saved['stage']})" if saved and saved["stage"] != lead["stage"] else ""))


def do_sms(store, flow, node, lead, now, ctx) -> dict:
    if not lead.get("consent"):
        return refused("SMS not sent: no recorded consent. Ask for their yes first.")
    if not lead.get("phone"):
        return refused("SMS not sent: this lead has no phone number")
    text, missing = text_for(store, lead, (node.get("config") or {}).get("text", ""), ctx.base_url, now)
    if missing:
        return refused(f"SMS not sent: details missing ({', '.join(missing)})")
    result = send_sms(store, lead["phone"], text, (node.get("config") or {}).get("template_id") or None, None,
                      ctx.transport or request, ctx.resolver)
    row = {"id": uuid.uuid4().hex[:12], "lead_id": lead["id"], "type": "sms", "direction": "out", "subject": f"Flow {flow['name']}",
           "body": text, "outcome": result.get("status", "dry_run"), "by": "flow", "at": iso(now), "created_at": iso(now)}
    store.update("activities", lambda rows: [*rows, row])
    if result.get("status") == "failed":
        return refused(f"SMS not sent: {result.get('error') or 'the provider refused it'}")
    return ok("SMS — " + ("practice only, not sent (connect SMS and switch it on to send)" if result.get("status") == "dry_run" else result["status"]))


def do_linkedin(store, flow, node, lead, now, ctx) -> dict:
    c = node.get("config") or {}
    if c.get("mode", "task") == "start_sequence":
        try:
            linkedin_seq.start(store, lead["id"], now)
        except (ValueError, LookupError) as exc:
            return refused(f"LinkedIn sequence not started: {exc}")
        return ok("LinkedIn sequence started (step 1 task for the person who sends it)")
    text, missing = text_for(store, lead, c.get("message", ""), ctx.base_url, now)
    if missing:
        return refused(f"LinkedIn task not created: details missing ({', '.join(missing)})")
    owner = store.read("settings").get("linkedin_owner") or caller_name(store)
    add_task(store, lead["id"], "linkedin", text, due_at=now, owner=owner, title="LinkedIn: send this message by hand", source="flow")
    return ok(f"LinkedIn task for {owner} (sent by hand)")


def do_call_task(store, flow, node, lead, now, ctx) -> dict:
    c = node.get("config") or {}
    owner = _owner_or_caller(store, c.get("owner"))
    company = lead.get("company") or lead.get("domain") or "this lead"
    note = str(c.get("note") or "").strip() or f"Call {company} through PD only, before 18:55."
    due = next_call_slot(now)
    add_task(store, lead["id"], "call", f"Flow {flow['name']}: {note}", due_at=due, owner=owner, priority="high",
             source="flow", dedupe_open=True)
    return ok(f"call task for {owner}, due {ist_text(due)}")


def do_meeting_link(store, flow, node, lead, now, ctx) -> dict:
    from .channels import meetings
    from .channels.calendar import LINK_PROVIDERS
    from .channels.config import active_provider, config_for, entry_of
    c = node.get("config") or {}
    owner = _owner_or_caller(store, c.get("owner"))
    pid = active_provider("calendar", entry_of(store.read("integrations"), "calendar"))
    name = lead.get("contact_name") or lead.get("company") or "them"
    if pid in LINK_PROVIDERS and config_for(store, "calendar", pid):
        try:
            booked = meetings.book(store, lead["id"], {}, ctx.transport or request, ctx.resolver, now=now)
        except ValueError as exc:
            return refused(f"booking link not made: {exc}")
        add_task(store, lead["id"], "meeting", f"Send this booking link to {name}: {booked['url']}\n{c.get('note') or ''}".strip(),
                 due_at=now, owner=owner, title="Send the booking link", source="flow")
        return ok(f"booking link ready ({pid}); task for {owner} to send it")
    why = "the connected calendar needs a time picked by a person" if pid else "no calendar is connected"
    add_task(store, lead["id"], "meeting", f"Book a meeting with {name} ({why}). {c.get('note') or ''}".strip(),
             due_at=next_call_slot(now), owner=owner, title="Book a meeting", source="flow")
    return ok(f"meeting task for {owner} ({why})")


def do_set_stage(store, flow, node, lead, now, ctx, on_stage=None) -> dict:
    target = (node.get("config") or {}).get("stage")
    moved = {}

    def move(latest: dict):
        current = latest["stage"]
        if current == target:
            moved["same"] = True
            return None
        closed = current in NO_EMAIL_STAGES and not (current == "not_now" and target == "new")
        if closed or not can_move(current, target, has_consent=bool(latest.get("consent"))) or (target in CONSENT_REQUIRED and not latest.get("consent")):
            moved["refused"] = current
            return None
        moved["from"] = current
        event = {"at": iso(now), "kind": "stage", "text": f"{current} → {target} (flow {flow['name']})"}
        return {**latest, "stage": target, "timeline": [*latest["timeline"], event]}
    store.update_lead(lead["id"], move)
    if moved.get("same"):
        return ok(f"stage already {target}")
    if "refused" in moved:
        return refused(f"stage not changed: {moved['refused']} → {target} is not allowed")
    if target == "interested":
        on_became_interested(store, lead["id"], f"Moved to Interested by flow {flow['name']}")
    if on_stage:
        on_stage(target)
    return ok(f"stage {moved['from']} → {target}")


def do_add_tag(store, flow, node, lead, now, ctx) -> dict:
    tag = str((node.get("config") or {}).get("tag") or "").strip()

    def add(latest: dict):
        tags = list(latest.get("tags") or [])
        if tag.lower() in {t.lower() for t in tags}:
            return None
        return {**latest, "tags": [*tags, tag]}
    store.update_lead(lead["id"], add)
    return ok(f"tag “{tag}” added")


def do_notify(store, flow, node, lead, now, ctx) -> dict:
    c = node.get("config") or {}
    to = _owner_or_caller(store, c.get("to"))
    company = lead.get("company") or lead.get("domain") or "a lead"
    notify(store, to, f"Flow {flow['name']}: {company}", str(c.get("message") or ""), lead["id"], kind="flow", sender=ctx.notify_sender)
    return ok(f"{to} notified")


def webhook_payload(flow: dict, node: dict, lead: dict, now: datetime) -> dict:
    """Only what the receiver needs; contact details only when the step says so (DPDP: least data)."""
    base = {k: lead.get(k) for k in ("id", "company", "domain", "title", "city", "country", "stage", "source", "campaign_id")}
    if (node.get("config") or {}).get("include_contact"):
        base.update({k: lead.get(k) for k in ("contact_name", "email", "phone")})
    return {"event": "flow.step", "at": iso(now), "flow": {"id": flow["id"], "name": flow["name"]},
            "step": {"id": node["id"], "title": title_of(node)}, "lead": base}


def do_webhook(store, flow, node, lead, now, ctx) -> dict:
    url = str((node.get("config") or {}).get("url") or "")
    try:
        clean = check_public_url(url, ctx.resolver)
        (ctx.transport or request)(clean, {"User-Agent": "OutreachOS-Flows/1"}, webhook_payload(flow, node, lead, now))
    except (ValueError, OSError, PermissionError) as exc:
        return refused(f"webhook failed: {exc}")
    return ok(f"webhook sent to {clean.split('/')[2]}")


HANDLERS = {"whatsapp": do_whatsapp, "sms": do_sms, "linkedin_task": do_linkedin, "call_task": do_call_task,
            "meeting_link": do_meeting_link, "add_tag": do_add_tag, "notify": do_notify, "webhook": do_webhook}
