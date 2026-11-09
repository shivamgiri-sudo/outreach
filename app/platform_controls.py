"""Platform-wide controls held by the super admins, and the one mail helper the sign-up / console emails share.

Stop all sending (settings["platform_stop"] = {on, reason, by, at}): while on, nothing goes out for ANY account,
Mas Callnet included -- email (sequence runs, send-now, flows, manual emails: the email sender answers "held"),
WhatsApp (accounts.hold_reason), flows (the flow tick does nothing) and LinkedIn (the agent / runner work routes and
starting a LinkedIn sequence answer 423, app/account_guard.py). Every login sees a banner while it is on
(GET /api/platform/status). Transactional mail (password reset, sign-up verification, alerts) still goes: it is how
people sign in and how the super admins are told.

Mail: send_mail() sends one transactional email and logs it in transactional_outbox. A body carrying a one-time link
is logged without the link. OUTREACH_DEV_OUTBOX=<file> (local test rigs only) also appends every such email, link
included, as one JSON line to that file, so a browser test can follow a verification link without a mail server.
"""
import json
import os
from typing import Optional

from .lead_model import now_iso

STOP_KEY = "platform_stop"
STOP_TEXT = "Sending is stopped for every account by the platform admin."


def stop_state(store) -> Optional[dict]:
    row = (store.read("settings") or {}).get(STOP_KEY)
    return row if isinstance(row, dict) and row.get("on") else None


def stop_reason(store) -> Optional[str]:
    """The plain reason nothing may be sent right now, or None."""
    row = stop_state(store)
    if not row:
        return None
    return STOP_TEXT + (f" Reason: {row['reason']}" if row.get("reason") else "")


def set_stop(store, on: bool, by: str, reason: str = "") -> dict:
    row = {"on": bool(on), "reason": " ".join(str(reason or "").split())[:300], "by": str(by or "")[:120], "at": now_iso()}
    store.update("settings", lambda s: {**s, STOP_KEY: row})
    return row


def public_stop(store) -> Optional[dict]:
    row = stop_state(store)
    return {k: row.get(k) for k in ("reason", "by", "at")} if row else None


# ---------------------------------------------------------------- mail
def _dev_outbox(message: dict) -> None:
    path = os.environ.get("OUTREACH_DEV_OUTBOX", "").strip()
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": now_iso(), **{k: message.get(k) for k in ("to", "subject", "body", "kind")}}) + "\n")
    except OSError:
        pass


def send_mail(store, sender, to: str, subject: str, body: str, kind: str, has_link: bool = False) -> dict:
    """Send (or dry-run without a provider) and log one transactional email. Never raises."""
    message = {"to": to, "subject": subject[:300], "body": body, "kind": "internal" if kind == "internal" else kind}
    try:
        result = sender(message) if sender else {"status": "dry_run"}
    except Exception as exc:                  # a mail failure must not break the request that caused it
        result = {"status": "failed", "error": str(exc)[:200]}
    _dev_outbox(message)
    from .provenance import internal
    from .reminders import _log_transactional
    try:
        _log_transactional(store, to, subject, "(one-time link, not kept)" if has_link else body, kind, None, result,
                           extra={"origin": internal("password_reset" if has_link else "alert", live=result.get("status") != "dry_run",
                                                     rule={"kind": kind})})
    except Exception:
        pass
    return result


SIGNUP_KEY = "public_signup_open"


def signup_open(store) -> bool:
    """Public sign-up ("Create an account") is closed until a super admin opens it (legal pages, terms, capacity)."""
    return bool(store.read("settings").get(SIGNUP_KEY))


def set_signup(store, on: bool) -> bool:
    store.update("settings", lambda s: {**s, SIGNUP_KEY: bool(on)})
    return bool(on)
