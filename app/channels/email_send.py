"""Email delivery through the active email provider: Resend, SendGrid, Postmark, SMTP (SES, Gmail, any) or custom.

`send_email` only sends when the provider is complete AND settings.live_sending is on; otherwise "dry_run".
`test_email` sends one test message regardless of the switch (like the old Setup test button).
"""
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from ..growth_core import public_base_url
from ..hygiene import normalise_email
from .config import config_for
from .custom_http import call_custom
from .http import request

RESEND_URL = "https://api.resend.com/emails"
SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"
POSTMARK_URL = "https://api.postmarkapp.com/email"
GMAIL_HOST, GMAIL_PORT = "smtp.gmail.com", 587
SMTP_TIMEOUT = 20
SMTP_PROVIDERS = ("smtp", "ses_smtp", "gmail_smtp")


def _from(values: dict) -> str:
    return f"{values['from_name']} <{values['from_email']}>"


def default_smtp_factory(host: str, port: int, use_ssl: bool, timeout: int):
    if use_ssl:
        return smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context())
    return smtplib.SMTP(host, port, timeout=timeout)


def smtp_settings(pid: str, values: dict) -> dict:
    if pid == "gmail_smtp":
        return {"host": GMAIL_HOST, "port": GMAIL_PORT, "ssl": False, "username": values["smtp_username"],
                "password": values["app_password"]}
    port = int(values.get("smtp_port") or 587)
    return {"host": values["smtp_host"], "port": port, "ssl": values.get("smtp_security") == "ssl" or port == 465,
            "username": values["smtp_username"], "password": values["smtp_password"]}


def build_message(values: dict, to: str, subject: str, text: str, headers: dict) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = _from(values)
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    domain = values["from_email"].split("@")[-1]
    msg["Message-ID"] = make_msgid(domain=domain)
    for key, value in headers.items():
        msg[key] = value
    msg.set_content(text)
    return msg


def smtp_send(pid: str, values: dict, msg: EmailMessage, smtp_factory=None) -> str:
    """Send through SMTP; returns the Message-ID. Bad login → PermissionError, other failures → OSError."""
    cfg = smtp_settings(pid, values)
    try:
        client = (smtp_factory or default_smtp_factory)(cfg["host"], cfg["port"], cfg["ssl"], SMTP_TIMEOUT)
    except (OSError, smtplib.SMTPException) as exc:
        hint = " Port 25 is blocked on most clouds; use 587 or 465." if cfg["port"] == 25 else ""
        raise OSError(f"Could not reach {cfg['host']}:{cfg['port']} ({exc}).{hint}") from exc
    try:
        if not cfg["ssl"]:
            client.ehlo()
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        client.login(cfg["username"], cfg["password"])
        client.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise PermissionError("The SMTP server rejected the username or password.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise OSError(f"SMTP error: {exc}") from exc
    finally:
        try:
            client.quit()
        except (smtplib.SMTPException, OSError):
            pass
    return msg["Message-ID"]


def http_request_for(pid: str, values: dict, to: str, subject: str, text: str, headers: dict):
    """(url, headers, json body) for the HTTP email providers."""
    if pid == "resend":
        return RESEND_URL, {"Authorization": f"Bearer {values['api_key']}"}, {
            "from": _from(values), "to": [to], "subject": subject, "text": text, "headers": headers}
    if pid == "sendgrid":
        return SENDGRID_URL, {"Authorization": f"Bearer {values['api_key']}"}, {
            "personalizations": [{"to": [{"email": to}]}], "from": {"email": values["from_email"], "name": values["from_name"]},
            "subject": subject, "content": [{"type": "text/plain", "value": text}], **({"headers": headers} if headers else {})}
    if pid == "postmark":
        return POSTMARK_URL, {"X-Postmark-Server-Token": values["server_token"], "Accept": "application/json"}, {
            "From": _from(values), "To": to, "Subject": subject, "TextBody": text,
            "MessageStream": values.get("message_stream") or "outbound",
            "Headers": [{"Name": k, "Value": v} for k, v in headers.items()]}
    raise ValueError(f"{pid} is not an HTTP email provider")


def _http_id(pid: str, reply: dict) -> str:
    if pid == "sendgrid":
        return (reply.get("_headers") or {}).get("x-message-id", "")
    if pid == "postmark":
        return str(reply.get("MessageID", ""))
    return str(reply.get("id", ""))


def deliver(cfg: dict, to: str, subject: str, text: str, headers: dict, transport=request, smtp_factory=None,
            resolver=None, extra: dict = None) -> dict:
    """Send one email with a provider config from config_for(). Returns {status: sent|failed, provider_id?, error?}."""
    pid, values = cfg["provider"], cfg["values"]
    try:
        if pid in SMTP_PROVIDERS:
            return {"status": "sent", "provider_id": smtp_send(pid, values, build_message(values, to, subject, text, headers), smtp_factory)}
        if pid == "custom_http":
            result = call_custom(values, {"to": to, "email": to, "subject": subject, "text": text, **(extra or {})},
                                 transport, resolver)
            return ({"status": "sent", "provider_id": result.get("provider_id", "")} if result["ok"]
                    else {"status": "failed", "error": result.get("error", "failed")})
        url, auth, body = http_request_for(pid, values, to, subject, text, headers)
        reply = transport(url, auth, body)
        if pid == "postmark" and reply.get("ErrorCode") not in (None, 0):
            return {"status": "failed", "error": f"Postmark error {reply.get('ErrorCode')}: {reply.get('Message', '')}"}
        return {"status": "sent", "provider_id": _http_id(pid, reply)}
    except PermissionError as exc:
        return {"status": "failed", "error": str(exc) or "The provider rejected the key."}
    except (OSError, ValueError, KeyError) as exc:
        return {"status": "failed", "error": str(exc)[:300]}


def unsubscribe_headers(store, lead_id: str, base_url: str) -> dict:
    unsub = f"{public_base_url(store, base_url)}/u/{lead_id}"
    return {"List-Unsubscribe": f"<{unsub}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


def send_email(store, message: dict, transport=request, smtp_factory=None, base_url: str = "", resolver=None) -> dict:
    """Sequence sender for non-Resend providers: dry_run unless connected AND 'Send for real' is on."""
    cfg = config_for(store, "email_send")
    if not (store.read("settings").get("live_sending") and cfg):
        return {"status": "dry_run"}
    headers = unsubscribe_headers(store, message["lead_id"], base_url)
    return deliver(cfg, message["to"], message["subject"], message["body"], headers, transport, smtp_factory, resolver)


def test_email(store, pid: str, to: str, transport=request, smtp_factory=None, resolver=None) -> dict:
    address = normalise_email(to)
    if not address:
        return {"ok": False, "message": "Enter a valid email address to send the test to."}
    cfg = config_for(store, "email_send", pid)
    if not cfg:
        return {"ok": False, "message": "Save all the required details first."}
    result = deliver(cfg, address, "Outreach OS test email",
                     "This is a test from Outreach OS. If you can read this, sending works.", {}, transport, smtp_factory, resolver)
    if result["status"] != "sent":
        return {"ok": False, "message": f"Sending failed: {result.get('error', 'unknown error')}"}
    store.update("settings", lambda s: {**s, "test_email_ok_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return {"ok": True, "message": f"Test email sent to {address}. Check the inbox and the spam folder."}
