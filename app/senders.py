"""Real email delivery through Resend, only when connected AND explicitly switched on."""
from datetime import datetime, timezone

from .growth_core import public_base_url
from .hygiene import normalise_email
from .http_util import HttpStatusError, post_json

RESEND_URL = "https://api.resend.com/emails"


def email_ready(store) -> dict:
    """The sending-domain settings when complete (direct API mode, key, from address and name), else {}."""
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


def make_email_sender(store, transport=post_json, base_url: str = ""):
    """Returns send(message) -> {"status": "dry_run" | "sent" | "failed", ...}."""

    def send(message: dict) -> dict:
        cfg = _email_config(store)
        if not (store.read("settings").get("live_sending") and cfg):
            return {"status": "dry_run"}
        unsub = f"{public_base_url(store, base_url)}/u/{message['lead_id']}"
        headers = {"List-Unsubscribe": f"<{unsub}>, <mailto:{cfg['from_email']}?subject=unsubscribe>",
                   "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}
        if message.get("message_id"):
            headers["Message-ID"] = message["message_id"]
        body = {"from": _from_header(cfg["from_name"], cfg["from_email"]), "to": [message["to"]],
                "subject": message["subject"], "text": message["body"], "headers": headers}
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
