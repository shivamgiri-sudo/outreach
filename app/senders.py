"""Real email delivery through Resend, only when connected AND explicitly switched on."""
from datetime import datetime, timezone

from .growth_core import public_base_url
from .hygiene import normalise_email
from .http_util import post_json

RESEND_URL = "https://api.resend.com/emails"


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
        unsub = f"{public_base_url(store, base_url)}/u/{message['lead_id']}"
        body = {
            "from": f"{cfg['from_name']} <{cfg['from_email']}>", "to": [message["to"]],
            "subject": message["subject"], "text": message["body"],
            "headers": {"List-Unsubscribe": f"<{unsub}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
        }
        try:
            reply = transport(RESEND_URL, {"Authorization": f"Bearer {cfg['api_key']}"}, body)
        except (OSError, ValueError, PermissionError) as exc:
            return {"status": "failed", "error": str(exc)}
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
    body = {"from": f"{cfg['from_name']} <{cfg['from_email']}>", "to": [address],
            "subject": "Outreach OS test email", "text": "This is a test from Outreach OS. If you can read this, sending works."}
    try:
        transport(RESEND_URL, {"Authorization": f"Bearer {cfg['api_key']}"}, body)
    except PermissionError:
        return {"ok": False, "message": "The email provider rejected the API key. Check it and the sending domain."}
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": f"Could not reach the email provider: {exc}"}
    store.write("settings", {**store.read("settings"), "test_email_ok_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return {"ok": True, "message": f"Test email sent to {address}. Check the inbox and spam folder."}
