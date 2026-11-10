"""Magic-link sign-in: an emailed link (MINUTES, single use) instead of a password, for callers and viewers only.

Owners, workspace admins and super admins never get one (they sign in with password + two-step, or Google/Microsoft
+ two-step); the answer is the same either way, so the form tells nobody who has a login. At most MAX_PER_HOUR links
per login per hour (and the per-address rate limit on /api/magic-link). A login with two-step on still types its code
after the link. Only a hash of the link is kept.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import accounts, auth
from .hygiene import normalise_email

MINUTES = 15
MAX_PER_HOUR = 3
KEY = "magic_links"
ROLES = ("caller", "viewer")
GENERIC = (f"If that address has a login that can use email links, a sign-in link is on its way. It works for {MINUTES} minutes. "
           "Owners, admins and super admins never get one: they sign in with their password and authenticator code.")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def eligible(store, user: Optional[dict]) -> bool:
    from . import super_admin
    if not user or user.get("role") not in ROLES or super_admin.is_super_email(user.get("email", "")):
        return False
    aid = accounts.of_user(user)
    return accounts.account_active(store, aid)


def request_link(store, email: str, base_url: str, send, now: Optional[datetime] = None) -> dict:
    now = now or datetime.now(timezone.utc)
    user = auth.find_user(store, normalise_email(email or ""))
    if not eligible(store, user):
        return {"message": GENERIC, "status": "withheld" if user else "none"}
    hour_ago = (now - timedelta(hours=1)).isoformat()
    rows = [r for r in store.read("settings").get(KEY) or [] if r.get("at", "") > hour_ago or r.get("expires_at", "") > now.isoformat()]
    if sum(1 for r in rows if r.get("email") == user["email"] and r.get("at", "") > hour_ago) >= MAX_PER_HOUR:
        return {"message": GENERIC, "status": "limited"}
    token = secrets.token_urlsafe(32)
    row = {"hash": _hash(token), "email": user["email"], "at": now.isoformat(timespec="seconds"),
           "expires_at": (now + timedelta(minutes=MINUTES)).isoformat(timespec="seconds"), "used": False}
    store.update("settings", lambda s: {**s, KEY: [*rows, row][-300:]})
    link = f"{base_url.rstrip('/')}/#magic?token={token}"
    from .platform_controls import send_mail
    result = send_mail(store, send, user["email"], "Your sign-in link",
                       f"Hello {user.get('name') or user['email']},\n\nSign in with this link (it works for {MINUTES} minutes and only once):\n"
                       f"{link}\n\nIf you did not ask for it, ignore this email.", "magic_link", has_link=True)
    status = result.get("status", "dry_run")
    if status != "sent":
        _tell_admins(store, user["email"], status, result.get("error", ""))
    return {"message": GENERIC, "status": status}


def _tell_admins(store, email: str, status: str, error: str) -> None:
    """A sign-in link that could not be sent (no mail service connected, or the provider refused): tell the super admins,
    at most once an hour, so the person who never got the email is not the only one who knows. Never raises."""
    try:
        from . import super_admin
        from .reminders import notify
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        if any(n.get("kind") == "alert:magic_link_failed" and datetime.fromisoformat(n["at"]) > recent for n in store.read("notifications")[-200:]):
            return
        why = "no email service is connected for portal mail" if status == "dry_run" else f"the mail provider said: {error or status}"
        for who in sorted(super_admin.super_admins()):
            notify(store, who, "Sign-in link could not be sent",
                   f"A sign-in link for {email} was not delivered: {why}. Connect or fix the sending domain under Setup, then ask them to try again.",
                   kind="alert:magic_link_failed")
    except Exception as exc:
        print(f"magic link admin alert failed: {exc}")


def use_link(store, token: str, now: Optional[datetime] = None) -> dict:
    """The login the link is for (the link is used up). Raises ValueError."""
    now = now or datetime.now(timezone.utc)
    h = _hash(token or "")
    rows = store.read("settings").get(KEY) or []
    row = next((r for r in rows if hmac.compare_digest(r.get("hash", ""), h)), None)
    if not row or row.get("used") or row.get("expires_at", "") <= now.isoformat():
        raise ValueError("This sign-in link has expired or was already used. Ask for a new one.")
    store.update("settings", lambda s: {**s, KEY: [{**r, "used": True} if r.get("hash") == h else r for r in s.get(KEY) or []]})
    user = auth.find_user(store, row["email"])
    if not eligible(store, user):
        raise ValueError("This login cannot use email links. Sign in with your password.")
    return user
