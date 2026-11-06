"""Real email delivery through Resend, only when connected AND explicitly switched on."""
import base64
from datetime import datetime, timezone

from .hygiene import normalise_email
from .http_util import HttpStatusError, post_json
from .unsubscribe import unsub_url

RESEND_URL = "https://api.resend.com/emails"


def email_ready(store) -> dict:
    """The active email provider's settings when complete, else {}.

    Resend through the classic "sending domain" card, or any provider chosen on the Integrations page (SendGrid,
    Postmark, SMTP, Gmail, custom HTTP)."""
    if _hub_email_provider(store):
        from .channels.config import config_for
        cfg = config_for(store, "email_send")
        return dict(cfg["values"]) if cfg else {}
    return _email_config(store)


def classify_send_error(exc: Exception) -> dict:
    """Turn a provider error into a send result.

    A bad key, a rate limit, an outage or no network is the provider's problem, not the lead's: it pauses all
    sending (`provider_error`) instead of counting as a failed attempt for every lead. A rejected request (for
    example an invalid recipient) fails only that message.
    """
    if isinstance(exc, PermissionError):
        return {"status": "provider_error", "retryable": False,
                "error": "The email provider rejected the API key. Fix it under Integrations, then resume sending."}
    if isinstance(exc, HttpStatusError) and exc.code not in (408, 409, 429) and exc.code < 500:
        return {"status": "failed", "error": str(exc)}
    return {"status": "provider_error", "retryable": True, "error": f"Email provider unavailable ({exc}). Retrying in an hour."}


def _from_header(name: str, email: str) -> str:
    safe = (name or "").replace('"', "'").replace("\r", " ").replace("\n", " ").strip()
    return f'"{safe}" <{email}>' if safe else email


def _email_config(store) -> dict:
    saved = store.read("integrations").get("sending_domain", {})
    values = saved.get("values", {})
    ready = saved.get("mode") == "api" and all(values.get(k) for k in ("api_key", "from_email", "from_name"))
    return values if ready else {}


def _hub_email_provider(store):
    """The active Integrations-hub email provider when it is not Resend (SendGrid, Postmark, SMTP, custom), else None."""
    from .channels.config import active_provider, entry_of
    provider = active_provider("email_send", entry_of(store.read("integrations"), "email_send"))
    return provider if provider and provider != "resend" else None


def make_email_sender(store, transport=post_json, base_url: str = "", channel_transport=None, smtp_factory=None):
    """Returns send(message) -> {"status": "dry_run" | "sent" | "failed", ...}."""

    def send(message: dict) -> dict:
        if _hub_email_provider(store):
            from .channels.email_send import send_email
            from .channels.http import request
            return send_email(store, message, channel_transport or request, smtp_factory, base_url)
        cfg = _email_config(store)
        if not (store.read("settings").get("live_sending") and cfg):
            return {"status": "dry_run"}
        unsub = unsub_url(store, base_url, message["lead_id"])
        headers = {"List-Unsubscribe": f"<{unsub}>, <mailto:{cfg['from_email']}?subject=unsubscribe>",
                   "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
        if message.get("message_id"):
            headers["Message-ID"] = message["message_id"]
        body = {"from": _from_header(cfg["from_name"], cfg["from_email"]), "to": [message["to"]],
                "subject": message["subject"], "text": message["body"], "headers": headers}
        from .channels.email_send import designed
        html = designed(store, message, base_url)
        if html:
            body["html"] = html
        if cfg.get("reply_to"):
            body["reply_to"] = cfg["reply_to"]
        request_headers = {"Authorization": f"Bearer {cfg['api_key']}"}
        if message.get("idempotency_key"):
            request_headers["Idempotency-Key"] = message["idempotency_key"][:256]
        try:
            reply = transport(RESEND_URL, request_headers, body)
        except (OSError, ValueError, PermissionError) as exc:
            return classify_send_error(exc)
        return {"status": "sent", "provider_id": reply.get("id", "")}

    return send


def send_test(store, transport, to: str) -> dict:
    """Send one real test email to the given address, ignoring the 'send for real' switch."""
    address = normalise_email(to)
    if not address:
        return {"ok": False, "message": "Enter a valid email address to send the test to."}
    hub_provider = _hub_email_provider(store)
    if hub_provider:
        from .channels.email_send import test_email
        return test_email(store, hub_provider, address)
    cfg = _email_config(store)
    if not cfg:
        return {"ok": False, "message": "Connect the sending domain first (all fields, direct API mode)."}
    body = {"from": _from_header(cfg["from_name"], cfg["from_email"]), "to": [address],
            "subject": "Outreach OS test email", "text": "This is a test from Outreach OS. If you can read this, sending works."}
    try:
        transport(RESEND_URL, {"Authorization": f"Bearer {cfg['api_key']}"}, body)
    except PermissionError:
        return {"ok": False, "message": "The email provider rejected the API key. Check it and the sending domain."}
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": f"Could not reach the email provider: {exc}"}
    store.write("settings", {**store.read("settings"), "test_email_ok_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return {"ok": True, "message": f"Test email sent to {address}. Check the inbox and spam folder."}


def make_transactional_sender(store, transport=post_json, channel_transport=None, smtp_factory=None):
    """One-to-one emails that aren't cold outreach: meeting invites and reminders to a lead, reminders to your team.

    Returns send({to, subject, body, kind, ics?, ics_method?}) -> {"status": "sent" | "failed" | "dry_run" |
    "provider_error", ...}. A message with `ics` carries it as a calendar invite attachment.
    Needs the sending domain connected; the caller decides whether real sending is allowed for that kind.
    """
    def send(message: dict) -> dict:
        from .channels.email_send import invite_attachments, send_transactional
        if _hub_email_provider(store):
            from .channels.http import request
            return send_transactional(store, message, channel_transport or request, smtp_factory)
        cfg = _email_config(store)
        to = normalise_email(message.get("to"))
        if not cfg or not to:
            return {"status": "dry_run"}
        body = {"from": _from_header(cfg["from_name"], cfg["from_email"]), "to": [to],
                "subject": message["subject"][:300], "text": message["body"]}
        if cfg.get("reply_to"):
            body["reply_to"] = cfg["reply_to"]
        files = invite_attachments(message)
        if files:
            body["attachments"] = [{"filename": f["filename"], "content": base64.b64encode(f["content"]).decode("ascii"),
                                    "content_type": f["content_type"]} for f in files]
        try:
            reply = transport(RESEND_URL, {"Authorization": f"Bearer {cfg['api_key']}"}, body)
        except (OSError, ValueError, PermissionError) as exc:
            return classify_send_error(exc)
        return {"status": "sent", "provider_id": reply.get("id", "")}

    return send
