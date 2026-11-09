"""Super admins: the logins named on the server in OUTREACH_SUPER_ADMIN (comma-separated emails).

Rules kept here (wired in from routes_auth, routes_security, routes_password, login_log and main):
  - Two-step login is mandatory for every super admin and every owner: one without it is sent into setup at sign-in
    before it gets a token (routes_security.after_password) and cannot switch it off.
  - Sealed recovery codes: a super admin makes RECOVERY_CODES one-time codes (shown once, kept as HMAC hashes on
    the login's row as `super_recovery`). Together with an emailed reset link, one code resets the password AND the
    login's own two-step login. Making new codes voids the old ones.
  - The server admin password (OUTREACH_ADMIN_PASSWORD) sign-in is off unless OUTREACH_ADMIN_LOGIN=1, and then only
    for a request made on the server itself (127.0.0.1 / ::1 with a local Host and no proxy headers: an SSH tunnel),
    never through the public proxy (Caddy adds X-Forwarded-For). Each use is logged and alerts the super admins.
    create_app(admin_password=...) with an explicit password (tests, the agent simulator) keeps the old behaviour
    ("any") unless it also passes admin_login.
  - Alerts (bell + email to every super admin): failed super-admin sign-ins (FAILS_FOR_ALERT within WINDOW_MINUTES),
    admin-password sign-in, an owner's two-step login reset, recovery codes made or used, owner logins added /
    removed / role changed. Every one is also written to the hash-chained change log (app/audit.py).

Routes (super admins only; the route checks it): GET /api/super-admin/status, POST /api/super-admin/recovery-codes.
"""
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, auth
from .auth import is_super_admin, super_admins           # noqa: F401  (one import point for the rules)
from .hygiene import normalise_email

RECOVERY_CODES = 10
FAILS_FOR_ALERT = 3
WINDOW_MINUTES = 15
ALERT_KIND = "security:super_admin"
LOCAL_PEERS = ("127.0.0.1", "::1")
LOCAL_HOST_NAMES = ("127.0.0.1", "::1", "localhost", "testserver")
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip")
MODES = ("off", "local", "any")


# ---------- who ----------
def is_super_email(email: str) -> bool:
    return bool(email) and normalise_email(email) in super_admins()


def two_step_mandatory(user: Optional[dict]) -> bool:
    """Owners and super admins must use two-step login."""
    user = user or {}
    return user.get("role") == "owner" or is_super_email(user.get("email", ""))


# ---------- the server admin password sign-in ----------
def admin_login_mode(env_value: str) -> str:
    """OUTREACH_ADMIN_LOGIN: 1 / on / local = allowed from the server itself only; anything else = off."""
    return "local" if str(env_value or "").strip().lower() in ("1", "true", "on", "yes", "local") else "off"


def is_local_request(request) -> bool:
    """Made on the server itself (an SSH tunnel to 127.0.0.1), not through the public proxy."""
    peer = request.client.host if request.client else ""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
    return peer in LOCAL_PEERS and host in LOCAL_HOST_NAMES and not any(h in request.headers for h in PROXY_HEADERS)


def admin_login_allowed(request, mode: str) -> bool:
    return mode == "any" or (mode == "local" and is_local_request(request))


def admin_login_refusal(mode: str) -> str:
    if mode == "off":
        return ("The server admin password sign-in is switched off. Sign in with your own email, or ask the server "
                "operator to set OUTREACH_ADMIN_LOGIN=1 and use it through an SSH tunnel.")
    return "The server admin password sign-in works only on the server itself (through an SSH tunnel), not over the internet."


# ---------- alerts ----------
def _recipients(store) -> list:
    """(bell name, email) for every super admin; every owner when no super admin is set (old rules)."""
    supers = super_admins()
    rows = auth.users(store)
    if not supers:
        return [(u.get("name") or u["email"], u["email"]) for u in rows if u.get("role") == "owner"]
    by_email = {u["email"]: u for u in rows}
    return [((by_email.get(e) or {}).get("name") or e, e) for e in sorted(supers)]


def alert(store, title: str, text: str) -> int:
    """Bell entry plus email to every super admin. Never raises into the action that caused it."""
    from . import alerts
    from .reminders import _log_transactional, notify
    sent = 0
    try:
        people = _recipients(store)
        if not people:
            notify(store, None, title, text, kind=ALERT_KIND)
            return 0
        email_on = store.read("settings").get("alerts_email", True) is not False
        for name, email in people:
            notify(store, name, title, text, kind=ALERT_KIND)
            sent += 1
            if alerts.SENDER and email and email_on:
                subject = f"[Outreach] {title}"
                try:
                    result = alerts.SENDER({"to": email, "subject": subject, "body": text, "kind": "internal"})
                except Exception as exc:
                    result = {"status": "failed", "error": str(exc)[:200]}
                _log_transactional(store, email, subject, text, "internal", None, result)
    except Exception:
        import logging
        logging.getLogger("outreach.super_admin").exception("super admin alert not sent")
    return sent


def record(store, actor, action: str, target: str, detail: str, title: str, text: str) -> None:
    """Change log row (hash-chained) plus the super-admin alert."""
    audit.record(store, actor, action, target, detail)
    alert(store, title, text)


def _parse(at: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(at).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def maybe_alert_failures(store, row: dict, now: Optional[datetime] = None) -> None:
    """Called by login_log for every failed sign-in: FAILS_FOR_ALERT in a row for a super admin within the window
    raise one alert (the row that raised it is marked, so one burst alerts once)."""
    login = row.get("email") or ""
    if not is_super_email(login):
        return
    now = now or datetime.now(timezone.utc)
    from .login_log import _streak
    streak = _streak(store.read("login_history"), login, now - timedelta(minutes=WINDOW_MINUTES))
    if len(streak) < FAILS_FOR_ALERT or any(r.get("super_alerted") for r in streak):
        return
    store.update("login_history", lambda rows: [{**r, "super_alerted": True} if r.get("id") == row.get("id") else r for r in rows])
    ips = sorted({r.get("ip") for r in streak if r.get("ip")})
    text = (f"{len(streak)} failed sign-ins in a row for the super admin login {login} within {WINDOW_MINUTES} minutes"
            f"{(' from ' + ', '.join(ips[:5])) if ips else ''}. If this was not you, change the password and check "
            "Setup > Security > Login history.")
    audit.record(store, "System", "super_admin.failed_logins", login, f"{len(streak)} failed sign-ins")
    alert(store, f"Failed super admin sign-ins: {login}", text)


def admin_login_used(store, request) -> None:
    from .web_security import client_ip
    ip = client_ip(request)
    record(store, {"name": "Admin", "role": "owner"}, "super_admin.admin_login", "server admin password",
           f"signed in from {ip or 'unknown'}", "Server admin password used",
           f"Someone signed in with the server admin password (OUTREACH_ADMIN_PASSWORD) from {ip or 'an unknown address'}. "
           "If this was not you, change the password on the server and switch OUTREACH_ADMIN_LOGIN off.")


def owner_changed(store, actor, action: str, email: str, detail: str) -> None:
    words = {"user.add": "added", "user.remove": "removed", "user.change": "changed"}.get(action, "changed")
    who = (actor or {}).get("email") or (actor or {}).get("name") or "Someone"
    alert(store, f"Owner login {words}: {email}", f"{who} {words} the owner login {email} ({detail}).")


def owner_two_step_reset(store, actor, email: str, how: str) -> None:
    who = (actor or {}).get("email") or (actor or {}).get("name") or "Someone"
    alert(store, f"Two-step login reset: {email}", f"The two-step login of the owner {email} was reset ({how}) by {who}. "
          "They set it up again at their next sign-in.")


# ---------- sealed recovery codes ----------
def _new_code() -> str:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(15))
    return f"{raw[:5]}-{raw[5:10]}-{raw[10:]}"


def _normal(code: str) -> str:
    return "".join(ch for ch in str(code or "").lower() if ch.isalnum())


def _hash(store, code: str) -> str:
    from .channels.security import server_secret
    return hmac.new(server_secret(store).encode(), ("super-recovery:" + _normal(code)).encode(), "sha256").hexdigest()


def _save_row(store, email: str, changes: dict) -> None:
    store.update("settings", lambda s: {**s, "users": [{**u, **changes} if isinstance(u, dict) and u.get("email") == email else u
                                                       for u in s.get("users") or []]})


def codes_left(user: Optional[dict]) -> int:
    return len((user or {}).get("super_recovery") or [])


def new_codes(store, email: str) -> list:
    """RECOVERY_CODES fresh codes (shown once); the old ones stop working."""
    codes = [_new_code() for _ in range(RECOVERY_CODES)]
    _save_row(store, email, {"super_recovery": [_hash(store, c) for c in codes], "super_recovery_at": auth.now_iso()})
    return codes


def use_code(store, user: dict, code: str) -> bool:
    """True (and the code is used up) when it is one of this super admin's unused sealed codes."""
    if not is_super_email(user.get("email", "")) or len(_normal(code)) != 15:
        return False
    wanted = _hash(store, code)
    hashes = list(user.get("super_recovery") or [])
    if not any(hmac.compare_digest(h, wanted) for h in hashes):
        return False
    _save_row(store, user["email"], {"super_recovery": [h for h in hashes if not hmac.compare_digest(h, wanted)]})
    return True


# ---------- password reset: the second factor ----------
def reset_needs_code(user: dict) -> bool:
    """Owners and super admins (and anyone with two-step on) give a code with the emailed link. An owner without
    two-step yet may use the link alone (setup is then forced at sign-in); a super admin never may."""
    from . import two_step
    return two_step.is_on(user) or is_super_email(user.get("email", ""))


def check_reset_code(store, user: dict, code: str) -> Optional[str]:
    """'code' / 'recovery' (two-step) or 'super_recovery' (sealed code) when right, else None."""
    from . import two_step
    method = two_step.check(store, user, code) if code else None
    if method:
        return method
    return "super_recovery" if code and use_code(store, user, code) else None


# ---------- routes ----------
class NewCodesBody(BaseModel):
    password: str = Field(default="", max_length=auth.MAX_PASSWORD_CHARS)
    code: str = Field(default="", max_length=40)


def status(store, mode: str, admin_password: str, user: Optional[dict]) -> dict:
    from . import two_step
    row = auth.find_user(store, (user or {}).get("email", "")) if str((user or {}).get("sub", "")).startswith("user:") else None
    owners = [{"email": u["email"], "name": u.get("name") or u["email"], "super_admin": is_super_email(u["email"]),
               "two_step_on": two_step.is_on(u)} for u in auth.users(store) if two_step_mandatory(u)]
    return {"super_admins": sorted(super_admins()), "owners": owners,
            "recovery": {"left": codes_left(row), "made_at": (row or {}).get("super_recovery_at"), "available": row is not None},
            "admin_login": {"password_set": bool(admin_password), "mode": mode,
                            "on": bool(admin_password) and mode != "off", "local_only": mode == "local"}}


def make_router(store, mode: str, admin_password: str, limiter: auth.LoginLimiter) -> APIRouter:
    router = APIRouter()

    def super_only(request: Request) -> dict:
        user = getattr(request.state, "user", None) or {}
        if not is_super_admin(user):
            raise HTTPException(403, "Only a super admin can do this.")
        return user

    @router.get("/api/super-admin/status")
    def get_status(request: Request):
        return status(store, mode, admin_password, super_only(request))

    @router.post("/api/super-admin/recovery-codes")
    def make_codes(body: NewCodesBody, request: Request):
        from . import two_step
        from .web_security import client_ip
        user = super_only(request)
        row = auth.find_user(store, user.get("email", "")) if str(user.get("sub", "")).startswith("user:") else None
        if not row or not is_super_email(row["email"]):
            raise HTTPException(400, "Recovery codes belong to a super admin's own login (named in OUTREACH_SUPER_ADMIN).")
        caller = client_ip(request)
        if limiter.locked(caller):
            raise HTTPException(429, "Too many wrong attempts. Wait a minute and try again.")
        right = auth.check_password(row.get("password_hash", ""), body.password)
        if right and two_step.is_on(row):
            right = two_step.check(store, row, body.code) == "code"
        if not right:
            limiter.record_failure(caller)
            raise HTTPException(400, "Your password or authenticator code is not right.")
        limiter.reset(caller)
        had = codes_left(row)
        codes = new_codes(store, row["email"])
        record(store, user, "super_admin.recovery_codes", row["email"], "made again (old codes void)" if had else "made",
               f"Super admin recovery codes made: {row['email']}",
               f"{row['email']} made {'new ' if had else ''}super admin recovery codes"
               f"{' (the old ones no longer work)' if had else ''}. If this was not them, act now.")
        return {"recovery_codes": codes, **status(store, mode, admin_password, user)}

    return router
