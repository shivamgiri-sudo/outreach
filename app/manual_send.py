"""One email a person writes for one lead, outside the sequence (moved out of app/sequence_runner.py).

A lead of another client account uses that account's sender identity and switch, and is held when the account has
neither its own sending keys nor a grant (app/account_sending.py)."""
import re
import uuid
from datetime import datetime

from . import email_domain, provenance, sequences
from .channels.security import server_secret
from .privacy import is_blocked
from .senders import email_ready
from .sequences import IST, lead_variables, render

MAX_MANUAL_SUBJECT, MAX_MANUAL_BODY = 200, 8000


def send_manual(store, lead_id: str, subject: str, body: str, now: datetime, base_url: str, sender, by: str = "",
                by_email: str = "") -> dict:
    """One email a person wrote (or pasted from a template) for ONE lead, outside the sequence. Not part of the sequence
    and does not change it. The same safety rules apply: unsubscribed / suppressed / bounced / erased / failed-check
    addresses are refused, an unsubscribe line is always added, practice mode until 'Send for real' is on."""
    from .unsubscribe import unsub_url
    subject, body = " ".join(str(subject or "").split())[:MAX_MANUAL_SUBJECT], str(body or "").strip()[:MAX_MANUAL_BODY]
    if not subject or not body:
        raise ValueError("Write a subject and a message.")
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None:
        raise LookupError("Lead not found")
    email = lead.get("email")
    if not email:
        raise ValueError("This lead has no email address.")
    if lead["stage"] in ("unsubscribed", "disqualified") or is_blocked(store, email) or email in set(store.read("suppressed")):
        raise ValueError("This address may not be emailed (unsubscribed, bounced, erased or on the do-not-contact list).")
    if (lead.get("email_check") or {}).get("status") == "invalid":
        raise ValueError("A mailbox check said this address does not exist.")
    if (bad := email_domain.problem(email)):
        raise ValueError(f"Not sent: {bad}.")
    from .sequence_runner import EARLY_STAGES, _message_domain, get_settings, sending_paused
    from . import account_sending
    cfg = get_settings(store)
    live = bool(cfg["live_sending"]) and bool(email_ready(store))
    acct = account_sending.for_lead(store, lead, cfg, live)
    if acct["hold"]:
        raise ValueError(acct["hold"])
    cfg, live = acct["cfg"], acct["live"]
    if live and (pause := sending_paused(store, now)):
        raise ValueError(f"Sending is paused: {pause['reason']}")
    variables = lead_variables(lead, cfg["sender_name"], base_url, {}, secret=server_secret(store),
                               brand=store if acct["account"] == "mcn" else cfg)
    subject, _ = render(subject, variables)
    body, missing = render(body, variables)
    if missing or re.search(r"\{\{|\}\}", subject + body):
        raise ValueError("Fill in or remove the {{placeholders}} first: " + ", ".join(sorted(set(missing))) if missing else "Remove the {{ }} left in the text.")
    from . import ai_marks
    ai_draft = ai_marks.match(store, lead_id, body, now)         # a person sending (an edit of) an AI draft
    if "/u/" not in body:
        body += f"\n\nNot relevant? Unsubscribe: {unsub_url(store, base_url, lead_id)}"
    footer = sequences.identity_footer(cfg)
    if footer:
        body += f"\n{footer}"
    key = f"{lead_id}:manual:{uuid.uuid4().hex[:8]}"
    message = {"lead_id": lead_id, "to": email, "subject": subject, "body": body, "step": "One-off email", "variant": "manual",
               "manual": True, "by": by, "sent_at": now.isoformat(timespec="seconds"), "sent_at_ist_date": now.astimezone(IST).date().isoformat(),
                "idempotency_key": key, "message_id": f"<{uuid.uuid5(uuid.NAMESPACE_URL, key).hex}.{lead_id}@{_message_domain(store)}>",
                "origin": provenance.build(store, "one_off", live, lead, provenance.person(by, by_email), cfg=cfg, hours=provenance.HOURS_PERSON)}
    if ai_draft:
        ai_marks.mark(message, by, ai_draft.get("source") or "reply draft", ai_draft.get("id", ""))
    account_sending.stamp(message, acct)
    message.update(sender(message))
    if message["status"] == "held":
        raise ValueError(message.get("error") or "Sending is held for this account.")
    store.update("outbox", lambda rows: [*rows, message])
    if message["status"] in ("provider_error", "failed"):
        raise ValueError(f"The email provider did not take it: {message.get('error') or message['status']}")
    sent = message["status"] == "sent"
    store.update_lead(lead_id, lambda l: {**l, "last_activity_at": now.isoformat(timespec="seconds"),
                                          "stage": "contacted" if sent and l["stage"] in EARLY_STAGES else l["stage"],
                                          "timeline": [*l["timeline"], {"at": now.isoformat(timespec="seconds"), "kind": "email" if sent else "practice",
                                                                        "text": f"One-off email: {subject} — {message['status'] if sent else 'practice only, not sent'}", "by": by,
                                                                        **({"ai_drafted": True} if ai_draft else {})}]})
    if ai_draft:
        ai_marks.log_sent(store, by, lead_id, "email", by, ai_draft.get("source") or "reply draft")
    return {"status": message["status"], "to": email, "subject": subject, "mode": "live" if live else "practice"}
