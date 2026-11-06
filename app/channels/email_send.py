"""Email delivery through the active email provider: Resend, SendGrid, Postmark, SMTP (SES, Gmail, any) or custom.

`send_email` only sends when the provider is complete AND settings.live_sending is on; otherwise "dry_run".
`test_email` sends one test message regardless of the switch (like the old Setup test button).
Attachments ({filename, content: bytes, content_type}) are supported for SMTP and the Resend / SendGrid / Postmark
APIs; a calendar invite (text/calendar) also goes in as an alternative part over SMTP, which mail apps show as an
invitation. The custom HTTP connector cannot carry attachments; only the text goes out.
"""
import base64
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from ..unsubscribe import unsub_url
from ..hygiene import normalise_email
from ..senders import classify_send_error
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


class RecipientRejected(ValueError):
    """This one address was refused; the provider itself is fine."""


def build_message(values: dict, to: str, subject: str, text: str, headers: dict, attachments=None, html: str = "") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = _from(values)
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    domain = values["from_email"].split("@")[-1]
    # Use the Message-ID the sequence engine chose (it's how replies are matched to the thread), else make one.
    msg["Message-ID"] = headers.get("Message-ID") or make_msgid(domain=domain)
    for key, value in headers.items():
        if key not in ("Message-ID", "X-Idempotency-Key"):    # the idempotency key is for HTTP APIs, not an email header
            msg[key] = value
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")          # the designed version; text stays for plain-text readers
    for item in attachments or []:
        maintype, _, rest = item["content_type"].partition("/")
        subtype, *raw_params = [p.strip() for p in rest.split(";")]
        params = dict(p.split("=", 1) for p in raw_params if "=" in p and not p.lower().startswith("charset"))
        if (maintype, subtype) == ("text", "calendar"):
            msg.add_alternative(item["content"].decode("utf-8"), subtype="calendar", params=params)
        msg.add_attachment(item["content"], maintype=maintype, subtype=subtype, filename=item["filename"], params=params)
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
    except smtplib.SMTPRecipientsRefused as exc:
        raise RecipientRejected(f"The server refused the recipient: {exc}") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise OSError(f"SMTP error: {exc}") from exc
    finally:
        try:
            client.quit()
        except (smtplib.SMTPException, OSError):
            pass
    return msg["Message-ID"]


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def http_request_for(pid: str, values: dict, to: str, subject: str, text: str, headers: dict, attachments=None, html: str = ""):
    """(url, headers, json body) for the HTTP email providers."""
    files = attachments or []
    if pid == "resend":
        auth = {"Authorization": f"Bearer {values['api_key']}"}
        if headers.get("X-Idempotency-Key"):
            auth["Idempotency-Key"] = headers["X-Idempotency-Key"]
        body = {"from": _from(values), "to": [to], "subject": subject, "text": text,
                "headers": {k: v for k, v in headers.items() if k != "X-Idempotency-Key"}, **({"html": html} if html else {})}
        if files:
            body["attachments"] = [{"filename": f["filename"], "content": _b64(f["content"]), "content_type": f["content_type"]}
                                   for f in files]
        return RESEND_URL, auth, body
    if pid == "sendgrid":
        body = {"personalizations": [{"to": [{"email": to}]}], "from": {"email": values["from_email"], "name": values["from_name"]},
                "subject": subject, "content": [{"type": "text/plain", "value": text}, *([{"type": "text/html", "value": html}] if html else [])],
                **({"headers": headers} if headers else {})}
        if files:
            body["attachments"] = [{"content": _b64(f["content"]), "type": f["content_type"], "filename": f["filename"],
                                    "disposition": "attachment"} for f in files]
        return SENDGRID_URL, {"Authorization": f"Bearer {values['api_key']}"}, body
    if pid == "postmark":
        body = {"From": _from(values), "To": to, "Subject": subject, "TextBody": text, **({"HtmlBody": html} if html else {}),
                "MessageStream": values.get("message_stream") or "outbound",
                "Headers": [{"Name": k, "Value": v} for k, v in headers.items()]}
        if files:
            body["Attachments"] = [{"Name": f["filename"], "Content": _b64(f["content"]), "ContentType": f["content_type"]}
                                   for f in files]
        return POSTMARK_URL, {"X-Postmark-Server-Token": values["server_token"], "Accept": "application/json"}, body
    raise ValueError(f"{pid} is not an HTTP email provider")


def _http_id(pid: str, reply: dict) -> str:
    if pid == "sendgrid":
        return (reply.get("_headers") or {}).get("x-message-id", "")
    if pid == "postmark":
        return str(reply.get("MessageID", ""))
    return str(reply.get("id", ""))


def deliver(cfg: dict, to: str, subject: str, text: str, headers: dict, transport=request, smtp_factory=None,
            resolver=None, extra: dict = None, attachments=None, html: str = "") -> dict:
    """Send one email with a provider config from config_for(). Returns {status: sent|failed, provider_id?, error?}."""
    pid, values = cfg["provider"], cfg["values"]
    try:
        if pid in SMTP_PROVIDERS:
            msg = build_message(values, to, subject, text, headers, attachments, html)
            return {"status": "sent", "provider_id": smtp_send(pid, values, msg, smtp_factory)}
        if pid == "custom_http":
            result = call_custom(values, {"to": to, "email": to, "subject": subject, "text": text, **({"html": html} if html else {}), **(extra or {})},
                                 transport, resolver)
            return ({"status": "sent", "provider_id": result.get("provider_id", "")} if result["ok"]
                    else {"status": "failed", "error": result.get("error", "failed")})
        headers = {k: v for k, v in headers.items() if k != "X-Idempotency-Key" or pid == "resend"}
        url, auth, body = http_request_for(pid, values, to, subject, text, headers, attachments, html)
        reply = transport(url, auth, body)
        if pid == "postmark" and reply.get("ErrorCode") not in (None, 0):
            return {"status": "failed", "error": f"Postmark error {reply.get('ErrorCode')}: {reply.get('Message', '')}"}
        return {"status": "sent", "provider_id": _http_id(pid, reply)}
    except RecipientRejected as exc:
        return {"status": "failed", "error": str(exc)[:300]}
    except (PermissionError, OSError) as exc:
        # bad key, rate limit, outage or no network: the provider's problem, so sending pauses (see senders.classify_send_error)
        return classify_send_error(exc)
    except (ValueError, KeyError) as exc:
        return {"status": "failed", "error": str(exc)[:300]}


def unsubscribe_headers(store, lead_id: str, base_url: str, from_email: str = "") -> dict:
    unsub = unsub_url(store, base_url, lead_id)
    mailto = f", <mailto:{from_email}?subject=unsubscribe>" if from_email else ""
    return {"List-Unsubscribe": f"<{unsub}>{mailto}", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


def send_email(store, message: dict, transport=request, smtp_factory=None, base_url: str = "", resolver=None) -> dict:
    """Sequence sender for non-Resend providers: dry_run unless connected AND 'Send for real' is on."""
    cfg = config_for(store, "email_send")
    if not (store.read("settings").get("live_sending") and cfg):
        return {"status": "dry_run"}
    headers = unsubscribe_headers(store, message["lead_id"], base_url, cfg["values"].get("from_email", ""))
    if message.get("message_id"):
        headers["Message-ID"] = message["message_id"]
    if message.get("idempotency_key"):
        headers["X-Idempotency-Key"] = message["idempotency_key"][:256]
    return deliver(cfg, message["to"], message["subject"], message["body"], headers, transport, smtp_factory, resolver,
                   html=designed(store, message, base_url))


def designed(store, message: dict, base_url: str) -> str:
    """The HTML version of a sequence email (same words, with the logo), unless the owner switched designed emails off."""
    if store.read("settings").get("html_email") is False or not message.get("body"):
        return ""
    from ..email_html import render
    return render(message["body"], base_url)


def invite_attachments(message: dict) -> list:
    """The .ics calendar invite of a message ({ics, ics_method}) as an attachment, or nothing."""
    if not message.get("ics"):
        return []
    method = message.get("ics_method") or "REQUEST"
    return [{"filename": "invite.ics" if method != "CANCEL" else "cancel.ics", "content": message["ics"].encode("utf-8"),
             "content_type": f"text/calendar; method={method}; charset=UTF-8"}]


def send_transactional(store, message: dict, transport=request, smtp_factory=None, resolver=None) -> dict:
    """A one-to-one email (meeting invite or reminder, team reminder) through the active hub provider."""
    cfg = config_for(store, "email_send")
    to = normalise_email(message.get("to"))
    if not cfg or not to:
        return {"status": "dry_run"}
    return deliver(cfg, to, message["subject"][:300], message["body"], {}, transport, smtp_factory, resolver,
                   attachments=invite_attachments(message))


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
