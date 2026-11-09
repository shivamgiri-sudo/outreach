"""Self-service password reset by email.

"Forgot password" sends a one-time link (valid TOKEN_MINUTES) to a login's own address. The answer is always the same,
so it never tells anyone whether an address has a login. Only a hash of the token is kept; using it sets the new
password, signs out the login's old sessions (the password fingerprint changes) and is written to the audit trail.
At most MAX_PER_HOUR links per address per hour.

Second factor (app/super_admin.py): a login with two-step on, and every super admin, must also give a code with the
link: the authenticator code, a two-step recovery code or (super admins) a sealed super admin recovery code; the
sealed code also resets the login's own two-step login. A plain "forgot password" for a super admin sends nothing
(same answer); the super admin asks with recovery=True. MAX_CODE_TRIES wrong codes void the link.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import auth
from .hygiene import normalise_email

TOKEN_MINUTES = 30
MAX_PER_HOUR = 3
MAX_CODE_TRIES = 5


class CodeNeeded(ValueError):
    """The link is right but this login must also give a code."""
GENERIC = "If that address has a login, a reset link is on its way. It works for 30 minutes."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def request_reset(store, email: str, base_url: str, send, now: Optional[datetime] = None, recovery: bool = False) -> dict:
    """Make and send a link when the address has a login. Returns {message, sent} (sent is for logs/tests only)."""
    from . import super_admin
    now = now or _now()
    address = normalise_email(email or "")
    user = auth.find_user(store, address) if address else None
    if not user:
        return {"message": GENERIC, "sent": False}
    is_super = super_admin.is_super_email(user["email"])
    if is_super and not recovery:            # no email-only reset for a super admin; the answer stays the same
        return {"message": GENERIC, "sent": False, "status": "withheld"}
    hour_ago = (now - timedelta(hours=1)).isoformat()
    rows = [r for r in store.read("settings").get("password_resets") or [] if r.get("expires_at", "") > now.isoformat() or r.get("at", "") > hour_ago]
    if sum(1 for r in rows if r.get("email") == user["email"] and r.get("at", "") > hour_ago) >= MAX_PER_HOUR:
        return {"message": GENERIC, "sent": False}
    token = secrets.token_urlsafe(32)
    row = {"hash": _hash(token), "email": user["email"], "at": now.isoformat(timespec="seconds"),
           "expires_at": (now + timedelta(minutes=TOKEN_MINUTES)).isoformat(timespec="seconds"), "used": False}
    store.update("settings", lambda s: {**s, "password_resets": [*rows, row][-200:]})
    link = f"{base_url.rstrip('/')}/#reset?token={token}"
    name = user.get("name") or user["email"]
    extra = ("You will also need the code from your authenticator app or one of your super admin recovery codes.\n\n"
             if is_super else "If you use two-step login, you will also need the code from your authenticator app.\n\n")
    body = (f"Hello {name},\n\nSomeone (hopefully you) asked to reset the password for your MCN Outreach login.\n\n"
            f"Set a new password here (the link works for {TOKEN_MINUTES} minutes and only once):\n{link}\n\n{extra}"
            "If you did not ask for this, ignore this email: your password stays as it is.")
    html = (f"<div style=\"font-family:Arial,sans-serif;max-width:520px;margin:auto;padding:20px;color:#0f172a;\">"
            f"<h2 style=\"font-size:18px;\">Reset your password</h2><p>Hello {name},</p>"
            f"<p>Someone (hopefully you) asked to reset the password for your MCN Outreach login.</p>"
            f"<p style=\"margin:20px 0;\"><a href=\"{link}\" style=\"background:#0369a1;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:600;\">Set a new password</a></p>"
            f"<p style=\"font-size:13px;color:#475569;\">The link works for {TOKEN_MINUTES} minutes and only once. If you did not ask for this, ignore this email.</p></div>")
    result = send({"to": user["email"], "subject": "Reset your MCN Outreach password", "body": body, "html": html, "kind": "password_reset"})
    from .provenance import internal
    from .reminders import _log_transactional
    # Logged for the audit without the link itself (the one-time link is a password; it is never stored in clear).
    _log_transactional(store, user["email"], "Reset your MCN Outreach password", "(password reset link, not kept)", "password_reset", None,
                       result, extra={"origin": internal("password_reset", live=result.get("status") != "dry_run",
                                                         rule={"requested": "Forgot password on the sign-in page"})})
    return {"message": GENERIC, "sent": result.get("status") == "sent", "status": result.get("status")}


def _live_row(store, token: str, now: datetime) -> Optional[dict]:
    h = _hash(token or "")
    rows = store.read("settings").get("password_resets") or []
    row = next((r for r in rows if secrets.compare_digest(r.get("hash", ""), h)), None)
    if not row or row.get("used") or row.get("expires_at", "") <= now.isoformat():
        return None
    return row


def link_info(store, token: str, now: Optional[datetime] = None) -> dict:
    """What the reset page needs to show: whether the link works and whether a code is asked too."""
    from . import super_admin
    row = _live_row(store, token, now or _now())
    user = auth.find_user(store, row["email"]) if row else None
    if not user:
        return {"valid": False, "needs_code": False, "super_admin": False}
    return {"valid": True, "needs_code": super_admin.reset_needs_code(user), "super_admin": super_admin.is_super_email(user["email"])}


def _second_factor(store, user: dict, h: str, code: str) -> Optional[str]:
    from . import super_admin, two_step
    if not super_admin.reset_needs_code(user):
        return None
    if super_admin.is_super_email(user["email"]) and not two_step.is_on(user) and not super_admin.codes_left(user):
        raise ValueError("This super admin login has neither two-step login nor recovery codes, so a link alone cannot reset it. "
                         "Ask another super admin, or sign in with the server admin password through an SSH tunnel.")
    if not str(code or "").strip():
        raise CodeNeeded("Also type the code from your authenticator app, or a recovery code.")
    method = super_admin.check_reset_code(store, user, code)
    if method:
        return method
    def bump(s):
        out = []
        for r in s.get("password_resets") or []:
            if r.get("hash") == h:
                tries = int(r.get("code_tries", 0)) + 1
                r = {**r, "code_tries": tries, "used": r.get("used") or tries >= MAX_CODE_TRIES}
            out.append(r)
        return {**s, "password_resets": out}
    store.update("settings", bump)
    raise ValueError("That code is not right. Type the newest code from your authenticator app, or a recovery code.")


def reset(store, token: str, new_password: str, now: Optional[datetime] = None, code: str = "") -> dict:
    """Use a link: returns the login's public row (with `second_factor`: how the code was given, or None).
    Raises ValueError (CodeNeeded when only the code is missing) with a plain message."""
    now = now or _now()
    h = _hash(token or "")
    row = _live_row(store, token, now)
    if not row:
        raise ValueError("This reset link has expired or was already used. Ask for a new one.")
    problem = auth.password_problem(new_password)
    if problem:
        raise ValueError(problem)
    user = auth.find_user(store, row["email"])
    if not user:
        raise ValueError("This login no longer exists.")
    method = _second_factor(store, user, h, code)
    if method == "super_recovery":            # the sealed code also resets the login's own two-step login
        from . import two_step
        two_step.turn_off(store, user["email"])
        user = auth.find_user(store, user["email"]) or user
    changes = {"password_hash": auth.hash_password(new_password), "password_changed_at": now.isoformat(timespec="seconds")}
    store.update("settings", lambda s: {**s,
        "users": [{**u, **changes} if isinstance(u, dict) and u.get("email") == user["email"] else u for u in s.get("users") or []],
        "password_resets": [{**r, "used": True} if r.get("hash") == h or r.get("email") == user["email"] else r
                            for r in s.get("password_resets") or []]})
    return {**auth.public_user({**user, **changes}), "second_factor": method}
