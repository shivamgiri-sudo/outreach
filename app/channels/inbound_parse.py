"""Turn provider webhook payloads and raw emails into one message shape (and one delivery-event shape).

Message: {channel, from_email, from_phone, to, subject, text, html, message_id, in_reply_to, references[],
          received_at, provider, raw_id}  → handed to app.inbound.process_inbound_message.
Event:   {type: bounce|complaint|delivered, email, provider_id, at}  → app.inbound.process_delivery_event.
"""
import json
from datetime import datetime, timezone
from email import message_from_bytes, policy
from email.parser import HeaderParser
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import parse_qs

from .custom_http import get_path
from .phone import e164

MAX_TEXT = 100_000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(value) -> str:
    """ISO time from a unix timestamp, an RFC 2822 date or an ISO string; now when unreadable."""
    if value in (None, ""):
        return now_iso()
    try:
        number = float(value)
        return datetime.fromtimestamp(number, timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat(timespec="seconds")
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text).astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, IndexError):
        return now_iso()


def message(channel: str, provider: str, **fields) -> dict:
    out = {"channel": channel, "from_email": None, "from_phone": None, "to": "", "subject": "", "text": "", "html": "",
           "message_id": "", "in_reply_to": "", "references": [], "received_at": now_iso(), "provider": provider, "raw_id": ""}
    out.update({k: v for k, v in fields.items() if v not in (None,)})
    out["text"], out["html"] = str(out["text"] or "")[:MAX_TEXT], str(out["html"] or "")[:MAX_TEXT]
    return out


def _email_address(value) -> str:
    if isinstance(value, dict):
        value = value.get("email") or value.get("Email") or ""
    if isinstance(value, list):
        value = value[0] if value else ""
    return parseaddr(str(value or ""))[1].strip().lower() or None


def _join(value) -> str:
    return ", ".join(str(v) for v in value) if isinstance(value, list) else str(value or "")


# ---------- request bodies ----------
def parse_multipart(content_type: str, body: bytes) -> dict:
    head = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("latin-1", "replace")
    msg = message_from_bytes(head + body, policy=policy.default)
    raw = {}
    if msg.is_multipart():
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if name and not part.get_filename():
                raw[str(name)] = part.get_payload(decode=True) or b""
    try:
        charsets = json.loads(raw.get("charsets", b"{}").decode("utf-8", "replace") or "{}")
    except ValueError:
        charsets = {}
    out = {}
    for name, data in raw.items():
        try:
            out[name] = data.decode(charsets.get(name) or "utf-8", "replace")
        except LookupError:
            out[name] = data.decode("utf-8", "replace")
    return out


def parse_body(content_type: str, body: bytes):
    kind = (content_type or "").lower()
    if "multipart/form-data" in kind:
        return parse_multipart(content_type, body)
    text = body.decode("utf-8", "replace").strip()
    if "application/x-www-form-urlencoded" in kind:
        return {k: v[0] for k, v in parse_qs(text, keep_blank_values=True).items()}
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        raise ValueError("The request body is not JSON or a form.") from None


# ---------- email ----------
def _content(part) -> str:
    if part is None:
        return ""
    try:
        return part.get_content()
    except (LookupError, KeyError, ValueError):
        return (part.get_payload(decode=True) or b"").decode("utf-8", "replace")


_QMAIL_COPY = "--- Below this line is a copy of the message."


def _bounced_message_id(m, text: str) -> str:
    """A bounce report (DSN/NDR) often has no In-Reply-To; our Message-ID is in the returned message or its headers."""
    for part in m.walk() if m.get_content_type() == "multipart/report" else ():
        kind = part.get_content_type()
        if kind == "message/rfc822":
            inner = part.get_payload()
            inner = inner[0] if isinstance(inner, list) and inner else inner
            if hasattr(inner, "get"):
                return str(inner.get("Message-ID", "")).strip()
        if kind == "text/rfc822-headers":
            return str(HeaderParser().parsestr(_content(part)).get("Message-ID", "")).strip()
    if _QMAIL_COPY in text:                        # qmail (e.g. Rediffmail) pastes the original below this line
        return str(HeaderParser().parsestr(text.split(_QMAIL_COPY, 1)[1].lstrip()).get("Message-ID", "")).strip()
    return ""


def email_from_mime(raw: bytes, provider: str, raw_id: str = "") -> dict:
    m = message_from_bytes(raw, policy=policy.default)
    text = _content(m.get_body(preferencelist=("plain",)))
    in_reply_to = str(m.get("In-Reply-To", "")).strip() or (_bounced_message_id(m, text) if not m.get("References") else "")
    return message("email", provider, from_email=_email_address(str(m.get("From", ""))), to=str(m.get("To", "")),
                   subject=str(m.get("Subject", "")), text=text,
                   html=_content(m.get_body(preferencelist=("html",))), message_id=str(m.get("Message-ID", "")).strip(),
                   in_reply_to=in_reply_to, references=str(m.get("References", "")).split(),
                   received_at=_iso(m.get("Date")), raw_id=raw_id)


def _header_lookup(headers) -> dict:
    if isinstance(headers, dict):
        return {str(k).lower(): str(v) for k, v in headers.items()}
    if isinstance(headers, list):
        return {str(h.get("name") or h.get("Name") or "").lower(): str(h.get("value") or h.get("Value") or "")
                for h in headers if isinstance(h, dict)}
    if isinstance(headers, str):
        parsed = HeaderParser().parsestr(headers)
        return {k.lower(): str(v) for k, v in parsed.items()}
    return {}


def email_webhook(pid: str, payload) -> list:
    if not isinstance(payload, dict):
        return []
    if pid == "resend_inbound":
        if payload.get("type") not in (None, "email.received"):
            return []
        data = payload.get("data") or {}
        h = _header_lookup(data.get("headers"))
        return [message("email", pid, from_email=_email_address(data.get("from")), to=_join(data.get("to")),
                        subject=data.get("subject", ""), text=data.get("text", ""), html=data.get("html", ""),
                        message_id=data.get("message_id") or h.get("message-id", ""), in_reply_to=h.get("in-reply-to", ""),
                        references=h.get("references", "").split(), received_at=_iso(data.get("created_at") or payload.get("created_at")),
                        raw_id=str(data.get("email_id") or data.get("id") or ""))]
    if pid == "sendgrid_inbound":
        if payload.get("email"):
            return [email_from_mime(payload["email"].encode("utf-8", "replace"), pid)]
        h = _header_lookup(payload.get("headers", ""))
        return [message("email", pid, from_email=_email_address(payload.get("from")), to=payload.get("to", ""),
                        subject=payload.get("subject", ""), text=payload.get("text", ""), html=payload.get("html", ""),
                        message_id=h.get("message-id", "").strip(), in_reply_to=h.get("in-reply-to", "").strip(),
                        references=h.get("references", "").split(), received_at=_iso(h.get("date")))]
    if pid == "postmark_inbound":
        h = _header_lookup(payload.get("Headers"))
        return [message("email", pid, from_email=_email_address(payload.get("FromFull") or payload.get("From")),
                        to=payload.get("To", ""), subject=payload.get("Subject", ""), text=payload.get("TextBody", ""),
                        html=payload.get("HtmlBody", ""), message_id=h.get("message-id", "").strip(),
                        in_reply_to=h.get("in-reply-to", "").strip(), references=h.get("references", "").split(),
                        received_at=_iso(payload.get("Date")), raw_id=str(payload.get("MessageID", "")))]
    return []


# ---------- WhatsApp and SMS ----------
def _meta_text(m: dict) -> str:
    kind = m.get("type")
    if kind == "text":
        return (m.get("text") or {}).get("body", "")
    if kind == "button":
        return (m.get("button") or {}).get("text", "")
    if kind == "interactive":
        inter = m.get("interactive") or {}
        return (inter.get("button_reply") or inter.get("list_reply") or {}).get("title", "")
    return f"[{kind or 'message'}]"


def whatsapp_webhook(pid: str, payload) -> list:
    if not isinstance(payload, dict):
        return []
    if pid == "meta_cloud":
        out = []
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                to = (value.get("metadata") or {}).get("display_phone_number", "")
                for m in value.get("messages") or []:
                    out.append(message("whatsapp", pid, from_phone=e164("+" + str(m.get("from", ""))), to=to,
                                       text=_meta_text(m), message_id=m.get("id", ""),
                                       in_reply_to=(m.get("context") or {}).get("id", ""),
                                       received_at=_iso(m.get("timestamp")), raw_id=m.get("id", "")))
        return out
    if pid == "gupshup":
        if payload.get("type") != "message":
            return []
        p = payload.get("payload") or {}
        inner = p.get("payload") or {}
        phone = p.get("source") or (p.get("sender") or {}).get("phone")
        return [message("whatsapp", pid, from_phone=e164("+" + str(phone or "")), text=inner.get("text") or inner.get("caption") or inner.get("title", ""),
                        message_id=p.get("id", ""), received_at=_iso(payload.get("timestamp", 0) / 1000 if isinstance(payload.get("timestamp"), (int, float)) else None),
                        raw_id=p.get("id", ""))]
    if pid == "interakt":
        if payload.get("type") != "message_received":
            return []
        data = payload.get("data") or {}
        customer, msg = data.get("customer") or {}, data.get("message") or {}
        phone = f"{customer.get('country_code', '+91')}{customer.get('phone_number', '')}"
        return [message("whatsapp", pid, from_phone=e164(phone), text=msg.get("message", ""), message_id=msg.get("id", ""),
                        received_at=_iso(msg.get("received_at_utc")), raw_id=msg.get("id", ""))]
    if pid == "wati":
        if payload.get("owner") is True or payload.get("eventType") not in (None, "message") or not payload.get("text"):
            return []
        mid = payload.get("whatsappMessageId") or payload.get("id", "")
        return [message("whatsapp", pid, from_phone=e164("+" + str(payload.get("waId", ""))), text=payload.get("text", ""),
                        message_id=mid, received_at=_iso(payload.get("timestamp") or payload.get("created")), raw_id=str(mid))]
    if pid == "aisensy":
        data = payload.get("data") or payload
        msg = data.get("message") if isinstance(data.get("message"), dict) else data
        phone = msg.get("phone_number") or data.get("phone_number") or payload.get("phone")
        content = msg.get("message_content") if isinstance(msg.get("message_content"), dict) else {}
        text = content.get("text") or msg.get("text") or ""
        if not (phone and text):
            return []
        return [message("whatsapp", pid, from_phone=e164("+" + str(phone).lstrip("+")), text=text,
                        message_id=str(msg.get("id") or msg.get("messageId") or ""), raw_id=str(msg.get("id") or ""))]
    return []


def sms_webhook(pid: str, payload) -> list:
    if not isinstance(payload, dict):
        return []
    if pid == "twilio_sms":
        return [message("sms", pid, from_phone=e164(payload.get("From")), to=payload.get("To", ""), text=payload.get("Body", ""),
                        message_id=payload.get("MessageSid", ""), raw_id=payload.get("MessageSid", ""))]
    if pid == "msg91":
        phone = payload.get("sender") or payload.get("mobile") or payload.get("from")
        text = payload.get("message") or payload.get("content") or payload.get("text") or ""
        mid = str(payload.get("requestId") or payload.get("id") or "")
        return [message("sms", pid, from_phone=e164(phone if str(phone).startswith("+") else f"+{phone}"),
                        to=str(payload.get("inNumber") or ""), text=text, message_id=mid, raw_id=mid)] if phone and text else []
    return []


def custom_messages(channel: str, mapping: str, values: dict, payload) -> list:
    """Read messages from any payload with the owner's dot-path mapping."""
    items = payload if isinstance(payload, list) else [payload]
    out = []
    for item in items:
        text = get_path(item, values.get("text_path"))
        msg_id = get_path(item, values.get("message_id_path")) or ""
        if mapping == "email":
            sender = _email_address(get_path(item, values.get("from_path")))
            if sender and text:
                out.append(message("email", "custom_webhook", from_email=sender, text=str(text),
                                   subject=str(get_path(item, values.get("subject_path")) or ""), message_id=str(msg_id),
                                   in_reply_to=str(get_path(item, values.get("in_reply_to_path")) or ""), raw_id=str(msg_id)))
        else:
            raw_phone = str(get_path(item, values.get("phone_path")) or "")
            phone = e164(raw_phone) or e164("+" + raw_phone.lstrip("+"))
            if phone and text:
                out.append(message(channel, "custom_webhook", from_phone=phone, text=str(text), message_id=str(msg_id),
                                   raw_id=str(msg_id)))
    return out


# ---------- delivery events ----------
RESEND_TYPES = {"email.bounced": "bounce", "email.complained": "complaint", "email.delivered": "delivered"}
SENDGRID_TYPES = {"bounce": "bounce", "spamreport": "complaint", "delivered": "delivered"}


def _event(kind, email, provider_id, at) -> dict:
    return {"type": kind, "email": str(email or "").strip().lower(), "provider_id": str(provider_id or ""), "at": _iso(at)}


def delivery_events(pid: str, payload, values: dict = None) -> list:
    values = values or {}
    if pid == "resend_events" and isinstance(payload, dict) and payload.get("type") in RESEND_TYPES:
        data = payload.get("data") or {}
        to = data.get("to") if isinstance(data.get("to"), list) else [data.get("to")]
        return [_event(RESEND_TYPES[payload["type"]], _email_address(t), data.get("email_id"), payload.get("created_at"))
                for t in to if _email_address(t)]
    if pid == "sendgrid_events":
        items = payload if isinstance(payload, list) else (payload.get("_list") or [payload] if isinstance(payload, dict) else [])
        return [_event(SENDGRID_TYPES[e["event"]], e.get("email"), str(e.get("sg_message_id", "")).split(".")[0], e.get("timestamp"))
                for e in items if isinstance(e, dict) and e.get("event") in SENDGRID_TYPES and e.get("email")
                and not (e["event"] == "bounce" and e.get("type") == "blocked")]
    if pid == "postmark_events" and isinstance(payload, dict):
        kind = payload.get("RecordType")
        if kind == "Bounce" and (payload.get("Type") == "HardBounce" or payload.get("Inactive") is True):
            return [_event("bounce", payload.get("Email"), payload.get("MessageID"), payload.get("BouncedAt"))]
        if kind == "SpamComplaint":
            return [_event("complaint", payload.get("Email"), payload.get("MessageID"), payload.get("BouncedAt"))]
        if kind == "Delivery":
            return [_event("delivered", payload.get("Recipient"), payload.get("MessageID"), payload.get("DeliveredAt"))]
        return []
    if pid == "custom_webhook":
        wanted = {str(values.get("bounce_value") or "bounce").lower(): "bounce",
                  str(values.get("complaint_value") or "complaint").lower(): "complaint",
                  str(values.get("delivered_value") or "delivered").lower(): "delivered"}
        out = []
        for item in payload if isinstance(payload, list) else [payload]:
            kind = wanted.get(str(get_path(item, values.get("type_path")) or "").lower())
            email = _email_address(get_path(item, values.get("email_path")))
            if kind and email:
                out.append(_event(kind, email, get_path(item, values.get("id_path")), None))
        return out
    return []
