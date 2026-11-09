"""Two-step login for team logins (settings.users): an authenticator-app code after the password.

Kept on the login's own row in settings.users (never returned by any API; auth.public_user picks named fields):
  two_step_secret     the base32 secret, encrypted with OUTREACH_SECRET_KEY when it is set (app/secretbox.py),
                      in plain text otherwise (like every other key in store.json without that key)
  two_step_pending    a secret shown but not yet confirmed with a code (same storage)
  two_step_on         true once confirmed
  two_step_recovery   HMAC-SHA256 hashes of the unused one-time recovery codes
  two_step_last_step  the newest 30-second step accepted, so one code works only once
settings.require_two_step (owner switch): every team login must use it; one without it sets it up at its next sign-in.

The server admin password login (OUTREACH_ADMIN_PASSWORD) is not covered: it is the server's break-glass key, kept
in the server environment, and has no row to hold a secret. The Security tab says so.

Between the password and the code the browser holds a ticket: signed with the server secret, valid TICKET_MINUTES,
bound to the login and to its password fingerprint (a password change voids it), and of one kind ("code" or "setup").
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

from . import auth, totp
from .channels.security import server_secret
from .secretbox import SecretBox

TICKET_MINUTES = 5
FIELDS = ("two_step_secret", "two_step_pending", "two_step_on", "two_step_recovery", "two_step_last_step")


def _box() -> SecretBox:
    return SecretBox()


def _seal(secret: str) -> str:
    return _box().seal_value(secret)


def _open(value: str) -> str:
    return _box().open_value(value or "")


def required(store) -> bool:
    return store.read("settings").get("require_two_step") is True


def is_on(user: Optional[dict]) -> bool:
    return bool(user and user.get("two_step_on") and user.get("two_step_secret"))


def status(store, user: Optional[dict]) -> dict:
    return {"on": is_on(user), "pending": bool(user and user.get("two_step_pending") and not is_on(user)),
            "recovery_left": len((user or {}).get("two_step_recovery") or []) if is_on(user) else 0,
            "required": required(store), "available": user is not None}


def _save(store, email: str, changes: dict) -> None:
    def apply(s):
        rows = []
        for u in s.get("users") or []:
            if isinstance(u, dict) and u.get("email") == email:
                u = {k: v for k, v in {**u, **changes}.items() if v is not None}
            rows.append(u)
        return {**s, "users": rows}
    store.update("settings", apply)


def issuer(store) -> str:
    try:
        from .brand import get_brand
        return str(get_brand(store).get("app_name") or "Outreach OS")[:40]
    except Exception:
        return "Outreach OS"


# ---------- setting it up ----------
def start(store, user: dict) -> dict:
    """A new secret to type or scan into the app. Nothing changes until it is confirmed with a code."""
    if is_on(user):
        raise ValueError("Two-step login is already on. Turn it off first to move it to a new phone.")
    secret = totp.new_secret()
    _save(store, user["email"], {"two_step_pending": _seal(secret)})
    return {"secret": secret, "uri": totp.otpauth_uri(secret, user["email"], issuer(store))}


def _pepper(store) -> str:
    return server_secret(store)


def confirm(store, user: dict, code: str, now: Optional[float] = None) -> list:
    """Switch it on with a first right code. Returns the recovery codes (shown once)."""
    pending = _open(user.get("two_step_pending", ""))
    step = totp.verify(pending, code, now) if pending else None
    if step is None:
        raise ValueError("That code is not right. Check the phone's clock and type the newest code.")
    codes = totp.new_recovery_codes()
    _save(store, user["email"], {"two_step_secret": user["two_step_pending"], "two_step_on": True, "two_step_last_step": step,
                                 "two_step_recovery": [totp.hash_recovery(_pepper(store), c) for c in codes],
                                 "two_step_pending": None})
    return codes


def check(store, user: dict, code: str, now: Optional[float] = None) -> Optional[str]:
    """'code' or 'recovery' when the code is right (a recovery code is used up), else None."""
    if not is_on(user):
        return None
    step = totp.verify(_open(user["two_step_secret"]), code, now, int(user.get("two_step_last_step", -1)))
    if step is not None:
        _save(store, user["email"], {"two_step_last_step": step})
        return "code"
    wanted = totp.hash_recovery(_pepper(store), code)
    hashes = list(user.get("two_step_recovery") or [])
    if totp.normalise_recovery(code) and any(hmac.compare_digest(h, wanted) for h in hashes):
        _save(store, user["email"], {"two_step_recovery": [h for h in hashes if not hmac.compare_digest(h, wanted)]})
        return "recovery"
    return None


def new_recovery_codes(store, user: dict) -> list:
    codes = totp.new_recovery_codes()
    _save(store, user["email"], {"two_step_recovery": [totp.hash_recovery(_pepper(store), c) for c in codes]})
    return codes


def turn_off(store, email: str) -> None:
    """Off and forgotten (also the owner's reset)."""
    def apply(s):
        return {**s, "users": [{k: v for k, v in u.items() if k not in FIELDS} if isinstance(u, dict) and u.get("email") == email else u
                               for u in s.get("users") or []]}
    store.update("settings", apply)


def set_required(store, on: bool) -> None:
    store.update("settings", lambda s: {**s, "require_two_step": bool(on)})


# ---------- the ticket between the password and the code ----------
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _sign(store, body: str) -> str:
    return _b64(hmac.new(server_secret(store).encode(), f"two-step.{body}".encode(), hashlib.sha256).digest())


def _pf(store, user: dict) -> str:
    return auth._fingerprint(store, "user:" + str(user.get("password_hash")))


def make_ticket(store, user: dict, kind: str, now: Optional[float] = None) -> str:
    moment = int(time.time() if now is None else now)
    claims = {"e": user["email"], "k": kind, "exp": moment + TICKET_MINUTES * 60, "pf": _pf(store, user), "n": secrets.token_hex(6)}
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_sign(store, body)}"


def read_ticket(store, ticket: str, kind: str, now: Optional[float] = None) -> Optional[dict]:
    """The login the ticket is for (fresh from the store), or None when it is forged, expired, of another kind or stale."""
    try:
        body, signature = str(ticket or "").split(".")
        if not hmac.compare_digest(signature.encode(), _sign(store, body).encode()):
            return None
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if claims.get("k") != kind or int(claims["exp"]) <= (time.time() if now is None else now):
            return None
    except (ValueError, TypeError, KeyError):
        return None
    user = auth.find_user(store, str(claims.get("e", "")))
    if not user or not hmac.compare_digest(_pf(store, user), str(claims.get("pf", ""))):
        return None
    return user
