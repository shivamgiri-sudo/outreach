"""Everything a lead sends back, in one place: replies (email, WhatsApp, SMS), bounces and complaints.

Pasted replies, the IMAP poller and provider webhooks all end up here, so the rules are applied the same way
whatever the source:
- A reply is matched to its lead by the email thread (In-Reply-To / References = our Message-ID), then by sender
  address (main or additional contacts), then by phone for WhatsApp/SMS.
- Any real reply stops the cold sequence at once, before anything else, so "Please stop" or "No." can never be
  followed by another cold email even if the label is unclear.
- Quoted history is removed before the reply is read.
- A bounce or complaint suppresses the address; too many of them pause live sending (sender reputation).
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from .activities import add_task, on_became_interested
from .ai import ai_classify
from .hygiene import normalise_email, normalise_phone
from .privacy import asks_for_erasure, record_consent
from .replies import classify_reply, strip_quoted
from .workflows import fire

EARLY_STAGES = ("new", "verified", "enriched")
# A reply may only move a lead that is still being prospected. Leads further on (or closed) keep their stage;
# the reply is logged and a person is asked to look.
REPLY_CAN_MOVE = EARLY_STAGES + ("contacted", "engaged", "interested", "not_now")
CLOSED_STAGES = ("unsubscribed", "customer", "advocate")
BOUNCE_PAUSE_RATE = 0.02        # pause live sending above 2% hard bounces …
COMPLAINT_PAUSE_RATE = 0.003    # … or 0.3% spam complaints,
MIN_SENDS_FOR_RATE = 50         # once there are enough sends for the rate to mean something
RATE_WINDOW = timedelta(days=7)
TEXT_KEPT = 4000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event(kind: str, text: str) -> dict:
    return {"at": _now(), "kind": kind, "text": text}


def suppress(store, email: Optional[str]) -> None:
    if email:
        store.update("suppressed", lambda rows: rows if email in rows else [*rows, email])


def reply_effect(lead: dict, label: str, text: str, channel: str = "email") -> tuple:
    """What a reply changes. Returns (changes, effect) where effect is applied / logged / review."""
    seq = lead.get("sequence")
    stop = lambda why: {"sequence": {**seq, "stopped": seq.get("stopped") or why}} if seq else {}
    if label == "auto_reply":
        return {}, "logged"
    if label == "bounce":
        moved = {"stage": "disqualified"} if lead["stage"] not in CLOSED_STAGES else {}
        return {**stop("bounced"), **moved}, "applied"
    engagement = {**lead["engagement"], "replies": lead["engagement"].get("replies", 0) + 1}
    changes = {"engagement": engagement, **stop("replied")}
    if label == "unsubscribe":
        withdrawn = {**changes, "stage": "unsubscribed", "consent": None, "consent_withdrawn_at": _now()}
        if seq:
            withdrawn["sequence"] = {**seq, "stopped": "unsubscribed"}
        return withdrawn, "applied"
    if lead["stage"] not in REPLY_CAN_MOVE:
        return changes, ("review" if label in ("interested", "not_interested", "not_now") else "logged")
    if label == "interested":
        consent = {"text": text[:500], "at": _now(), "source": f"{channel} reply"}
        return {**changes, "consent": consent, "stage": "interested"}, "applied"
    if label == "not_interested":
        return {**changes, "stage": "disqualified"}, "applied"
    if label == "not_now":
        return {**changes, "stage": "not_now"}, "applied"
    if lead["stage"] in EARLY_STAGES + ("contacted",):
        return {**changes, "stage": "engaged"}, "applied"
    return changes, "applied"


def apply_reply(store, lead_id: str, text: str, channel: str = "email", ai_transport=None, source: str = "pasted",
                received_at: Optional[str] = None, message_id: str = "") -> Optional[dict]:
    """Read a reply and act on it, on the latest saved copy of the lead. Returns None when the lead doesn't exist."""
    clean = strip_quoted(text) or (text or "").strip()
    label, ai_label = classify_reply(clean), None
    erasure = asks_for_erasure(clean)
    if erasure and label not in ("unsubscribe", "bounce", "auto_reply"):
        label = "unsubscribe"                      # "delete my data" also withdraws consent (DPDP)
    if label == "other" and ai_transport is not None:
        ai_label = ai_classify(store, clean, ai_transport)
        if ai_label and ai_label != "interested":
            label = ai_label                       # an AI "yes" is never treated as consent; a person checks it
    outcome = {}

    def change(lead: dict) -> dict:
        changes, effect = reply_effect(lead, label, clean, channel)
        if ai_label == "interested":
            effect = "review"
        outcome.update(effect=effect, email=lead.get("email"), was_unsubscribed=lead["stage"] == "unsubscribed",
                       consent_given=bool(changes.get("consent")))
        extra = {"whatsapp_last_inbound_at": _window_start(received_at)} if channel == "whatsapp" else {}
        return {**lead, **changes, **extra,
                "timeline": [*lead["timeline"], _event("reply", f"[{label}] ({channel}, {source}) {clean[:TEXT_KEPT]}")]}

    if store.update_lead(lead_id, change) is None:
        return None
    effect = outcome["effect"]
    if label not in ("auto_reply", "bounce") and channel != "linkedin":
        _stop_linkedin(store, lead_id, f"replied by {channel}")       # one real reply stops every cold channel
    if label in ("unsubscribe", "bounce") and channel == "email":
        suppress(store, outcome["email"])
    if label == "bounce" and channel == "email":
        _count_reply_bounce(store, outcome["email"], message_id)
    if label == "unsubscribe" and not outcome["was_unsubscribed"]:
        record_consent(store, lead_id, "withdrawn", channel, clean, f"{channel} reply ({source})")
    if outcome["consent_given"]:
        record_consent(store, lead_id, "given", channel, clean, f"{channel} reply ({source})")
    if erasure:
        add_task(store, lead_id, "erasure_request", clean[:TEXT_KEPT], due_at=datetime.now(timezone.utc) + timedelta(hours=24),
                 priority="high", title="Erasure request", source=f"{channel} reply", dedupe_open=True)
    if effect == "review" or label in ("question", "wrong_person", "other"):
        add_task(store, lead_id, label if effect != "review" else "review", clean[:TEXT_KEPT],
                 due_at=datetime.now(timezone.utc) + timedelta(hours=2), priority="high" if effect == "review" else "normal",
                 source=f"{channel} reply")
    if effect == "applied":
        if label == "interested":
            on_became_interested(store, lead_id, f"Said yes by {channel}")
        fire(store, "reply", label, lead_id)
    return {"label": label, "effect": effect, "ai_label": ai_label}


def _window_start(received_at: Optional[str]) -> str:
    """The 24-hour window opens when they sent the message, not when a (possibly retried) webhook arrived."""
    now = _now()
    try:
        sent = datetime.fromisoformat(str(received_at))
        return sent.isoformat(timespec="seconds") if sent.tzinfo and sent < datetime.fromisoformat(now) else now
    except ValueError:
        return now


def _stop_linkedin(store, lead_id: str, why: str) -> None:
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), {})
    if (lead.get("linkedin_seq") or {}).get("status") in ("waiting_to_connect", "waiting_for_acceptance", "messaging"):
        from .linkedin_seq import stop
        stop(store, lead_id, why)


def _looks_like_reply(msg: dict) -> bool:
    subject = (msg.get("subject") or "").strip().lower()
    return bool(msg.get("in_reply_to") or msg.get("references")) or subject.startswith(("re:", "re :", "aw:", "fwd: re:"))


def _match_lead(store, msg: dict) -> tuple:
    """(lead_id, matched_by) for an inbound message, or (None, None)."""
    thread_ids = {i.strip() for i in [msg.get("in_reply_to") or "", *(msg.get("references") or [])] if i and i.strip()}
    if thread_ids:
        for row in reversed(store.read("outbox")):
            if row.get("message_id") in thread_ids:
                return row["lead_id"], "thread"
    leads = store.read("leads")
    email = normalise_email(msg.get("from_email"))
    if email:
        for lead in leads:
            others = {normalise_email(c.get("email")) for c in lead.get("other_contacts") or [] if isinstance(c, dict)}
            if lead.get("email") == email or email in others:
                return lead["id"], "email"
    phone = normalise_phone(msg.get("from_phone"))
    if phone:
        for lead in leads:
            phones = {lead.get("phone"), *(normalise_phone(p) for p in lead.get("phones") or [])}
            if phone in phones:
                return lead["id"], "phone"
    return None, None


def _leads_sharing_phone(store, raw_phone, skip_id: str) -> list:
    phone = normalise_phone(raw_phone)
    if not phone:
        return []
    return [l["id"] for l in store.read("leads") if l["id"] != skip_id and l["stage"] != "unsubscribed"
            and phone in {l.get("phone"), *(normalise_phone(p) for p in l.get("phones") or [])}]


def process_inbound_message(store, msg: dict, ai_transport=None) -> dict:
    """Store an inbound message (once, by message id) and act on it if it belongs to a lead."""
    message_id = (msg.get("message_id") or msg.get("raw_id") or "").strip()
    if message_id and any(row.get("message_id") == message_id for row in store.read("inbox")):
        return {"matched": False, "duplicate": True}
    channel = msg.get("channel") or "email"
    lead_id, matched_by = _match_lead(store, msg)
    if not lead_id and channel == "whatsapp":
        from .inbound_leads import capture_whatsapp         # optional: an unknown sender's first message becomes a lead
        lead_id = capture_whatsapp(store, msg)
        matched_by = "whatsapp_click" if lead_id else None
    if not lead_id and channel == "email" and not _looks_like_reply(msg):
        # A shared mailbox also gets newsletters, alerts and login codes: not ours to store or act on.
        return {"matched": False, "ignored": "not a reply to our outreach"}
    text = (msg.get("text") or "").strip() or (msg.get("subject") or "").strip()
    row = {"id": uuid.uuid4().hex[:12], "message_id": message_id, "channel": channel, "provider": msg.get("provider", ""),
           "from": msg.get("from_email") or msg.get("from_phone") or "", "subject": (msg.get("subject") or "")[:300],
           "text": strip_quoted(text)[:TEXT_KEPT], "received_at": msg.get("received_at") or _now(),
           "lead_id": lead_id, "matched_by": matched_by}
    result = apply_reply(store, lead_id, text, channel, ai_transport, source=msg.get("provider") or "inbound",
                         received_at=msg.get("received_at"), message_id=message_id) if lead_id else None
    row["label"] = (result or {}).get("label")
    if result and result.get("label") == "unsubscribe" and channel in ("whatsapp", "sms"):
        for other in _leads_sharing_phone(store, msg.get("from_phone"), lead_id):       # a number's STOP covers everybody on it
            apply_reply(store, other, text, channel, ai_transport, source=msg.get("provider") or "inbound",
                        received_at=msg.get("received_at"))
    store.update("inbox", lambda rows: [*rows, row])
    if not lead_id:
        add_task(store, None, "unmatched_reply", f"From {row['from']}: {row['text'][:500]}", source=f"{channel} inbound")
        return {"matched": False, "inbox_id": row["id"]}
    return {"matched": True, "lead_id": lead_id, "matched_by": matched_by, "inbox_id": row["id"], **(result or {})}


def _rates(store, now: datetime) -> dict:
    since = (now - RATE_WINDOW).isoformat(timespec="seconds")
    sent = sum(1 for m in store.read("outbox") if m.get("status") == "sent" and m.get("sent_at", "") >= since)
    events = [e for e in store.read("delivery_events") if e.get("at", "") >= since]
    bounces = sum(1 for e in events if e["type"] == "bounce")
    complaints = sum(1 for e in events if e["type"] == "complaint")
    return {"sent": sent, "bounces": bounces, "complaints": complaints,
            "bounce_rate": bounces / sent if sent else 0.0, "complaint_rate": complaints / sent if sent else 0.0}


def process_delivery_event(store, event: dict, now: Optional[datetime] = None) -> dict:
    """Bounce / complaint / delivered from the email provider. Duplicates (same provider id and type) are ignored."""
    now = now or datetime.now(timezone.utc)
    kind = event.get("type")
    if kind not in ("bounce", "complaint", "delivered"):
        return {"ignored": True}
    email = normalise_email(event.get("email"))
    provider_id = event.get("provider_id") or ""
    key = f"{kind}:{provider_id or email}"
    if any(e.get("key") == key for e in store.read("delivery_events")):
        return {"duplicate": True}
    record = {"key": key, "type": kind, "email": email, "provider_id": provider_id, "at": event.get("at") or now.isoformat(timespec="seconds")}
    store.update("delivery_events", lambda rows: [*rows, record])
    if kind == "delivered":
        store.update("outbox", lambda rows: [{**m, "delivered_at": record["at"]} if provider_id and m.get("provider_id") == provider_id else m
                                             for m in rows])
        return {"ok": True}
    suppress(store, email)
    reason = "bounced" if kind == "bounce" else "complained"
    for lead in store.read("leads"):
        if email and lead.get("email") == email:
            if kind == "complaint" and lead["stage"] != "unsubscribed":
                record_consent(store, lead["id"], "withdrawn", "email", "", "spam complaint reported by the email provider")

            def change(l, kind=kind, reason=reason):
                seq = l.get("sequence")
                stage = l["stage"]
                if kind == "complaint":
                    stage = "unsubscribed"
                elif stage not in CLOSED_STAGES:
                    stage = "disqualified"
                return {**l, "stage": stage, **({"sequence": {**seq, "stopped": seq.get("stopped") or reason}} if seq else {}),
                        "timeline": [*l["timeline"], _event(kind, f"Email provider reported a {kind} for {email}")]}
            store.update_lead(lead["id"], change)
    rates, paused = _pause_if_unhealthy(store, now)
    return {"ok": True, "suppressed": email, "paused": paused, "rates": rates}


def _pause_if_unhealthy(store, now: datetime) -> tuple:
    rates = _rates(store, now)
    paused = None
    if rates["sent"] >= MIN_SENDS_FOR_RATE and (rates["bounce_rate"] > BOUNCE_PAUSE_RATE or rates["complaint_rate"] > COMPLAINT_PAUSE_RATE):
        paused = (f"{rates['bounces']} bounces and {rates['complaints']} complaints from {rates['sent']} emails in 7 days. "
                  "Check the list quality, then resume sending on the Outbox page.")
        store.update("settings", lambda s: {**s, "sending_paused": {"reason": paused, "at": now.isoformat(timespec="seconds"), "resume_at": None}})
    return rates, paused


def _count_reply_bounce(store, email: Optional[str], message_id: str = "") -> None:
    """A bounce notice found by the reply poller (IMAP / inbound mail) counts toward the bounce-rate pause like a provider event."""
    email = normalise_email(email)
    if not email:
        return
    key = f"bounce:{message_id or email}"
    now = datetime.now(timezone.utc)
    since = (now - RATE_WINDOW).isoformat(timespec="seconds")
    if any(e.get("key") == key or (e.get("type") == "bounce" and e.get("email") == email and e.get("at", "") >= since)
           for e in store.read("delivery_events")):
        return                     # the provider webhook (or an earlier notice) already counted this bounce
    record = {"key": key, "type": "bounce", "email": email, "provider_id": message_id, "at": now.isoformat(timespec="seconds"), "via": "reply"}
    store.update("delivery_events", lambda rows: [*rows, record])
    _pause_if_unhealthy(store, now)
