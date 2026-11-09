"""Login: signed, expiring, revocable tokens, optional users with roles, and a bounded login lockout.

Tokens are signed with the per-install server secret (settings.server_secret, never returned by any API), not with
anything derived from a password, so a leaked token cannot be used to guess the password offline.
A token carries {sub, sid, jti, iat, exp, pf}:
  - sub: "admin" (the OUTREACH_ADMIN_PASSWORD login, role owner) or "user:<email>" (settings.users)
  - sid: the sign-in session, kept when the token is renewed, so signing out ends every renewed copy too
  - pf:  a fingerprint of the password, so changing the password signs out every old token
Tokens last 12 hours and are re-issued (header X-Renewed-Token) once more than half of that has passed.
Signing out stores the jti and sid in `revoked_tokens` until they would have expired anyway.
"""
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Optional

from .channels.security import server_secret
from .hygiene import normalise_email

TOKEN_SECONDS = 12 * 3600
RENEW_AFTER = 0.5                      # re-issue once more than half the lifetime has passed
CLOCK_SKEW_SECONDS = 60
TOKEN_PREFIX = "v2"
ADMIN_SUBJECT = "admin"
ROLES = ("owner", "admin", "caller", "viewer")      # admin = workspace admin: a caller who also manages its workspaces' logins
MIN_PASSWORD_CHARS = 10
MAX_PASSWORD_CHARS = 1000
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60
MAX_TRACKED_CALLERS = 10_000
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
# settings keys no API may return (secrets and password hashes)
PRIVATE_SETTINGS = ("server_secret", "users", "extension_tokens")


def _now(now: Optional[float] = None) -> float:
    return time.time() if now is None else now


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---------- passwords ----------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, maxmem=64 * 1024 * 1024, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${_b64(salt)}${_b64(digest)}"


_DUMMY_HASH = None


def check_password(stored: str, given: str) -> bool:
    """Constant-time check of a scrypt hash. An unknown user is checked against a dummy hash (same timing)."""
    global _DUMMY_HASH
    if not stored:
        _DUMMY_HASH = _DUMMY_HASH or hash_password("not-a-real-password")
        stored, real = _DUMMY_HASH, False
    else:
        real = True
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt((given or "").encode("utf-8"), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32,
                             maxmem=64 * 1024 * 1024)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, _unb64(digest)) and real


def password_matches(expected: str, given: str) -> bool:
    return hmac.compare_digest(expected.encode("utf-8"), (given or "").encode("utf-8"))


def password_problem(password: str) -> Optional[str]:
    if len(password or "") < MIN_PASSWORD_CHARS:
        return f"Use at least {MIN_PASSWORD_CHARS} characters."
    if len(password) > MAX_PASSWORD_CHARS:
        return "That password is too long."
    return None


# ---------- users ----------
def users(store) -> list:
    return [u for u in store.read("settings").get("users") or [] if isinstance(u, dict) and u.get("email")]


def find_user(store, email: str) -> Optional[dict]:
    wanted = normalise_email(email)
    return next((u for u in users(store) if u["email"] == wanted), None) if wanted else None


def super_admins() -> set:
    """Super admin email(s) from the server setting OUTREACH_SUPER_ADMIN (comma-separated). Set on the server, so nobody
    inside the app can make themselves one or take it away."""
    import os
    return {normalise_email(e) for e in os.environ.get("OUTREACH_SUPER_ADMIN", "").split(",") if normalise_email(e)}


def is_super_admin(user: Optional[dict]) -> bool:
    """A super admin, or the server admin password login. With no super admin set, every owner counts as one (old rules)."""
    user = user or {}
    if user.get("sub") in (ADMIN_SUBJECT, "local"):      # the server admin password, or no sign-in at all (this computer)
        return True
    supers = super_admins()
    if not supers:
        return user.get("role") == "owner"
    return normalise_email(user.get("email")) in supers


def can_edit_contacts(user: Optional[dict]) -> bool:
    """Owners always may; callers may unless an owner switched it off for them; viewers never."""
    user = user or {}
    if user.get("role") == "owner":
        return True
    if user.get("role") == "admin":
        return True
    return user.get("role") == "caller" and user.get("can_edit_contacts", True) is not False


def public_user(user: dict) -> dict:
    return {**{k: user.get(k) for k in ("email", "name", "role", "created_at", "password_changed_at")},
            "can_edit_contacts": can_edit_contacts(user), "workspaces": user.get("workspaces") or [],
            "super_admin": bool(super_admins()) and normalise_email(user.get("email")) in super_admins()}


def auth_on(store, admin_password: str) -> bool:
    return bool(admin_password) or bool(users(store))


def _fingerprint(store, material: str) -> str:
    return hmac.new(server_secret(store).encode(), ("pw:" + material).encode("utf-8"), hashlib.sha256).hexdigest()[:20]


def subject_principal(store, admin_password: str, sub: str) -> Optional[dict]:
    """Who a token subject is right now, with the password fingerprint that its tokens must carry."""
    if sub == ADMIN_SUBJECT:
        if not admin_password:
            return None
        return {"sub": sub, "email": "", "name": "Admin", "role": "owner", "pf": _fingerprint(store, "admin:" + admin_password)}
    if sub.startswith("user:"):
        user = find_user(store, sub[5:])
        if not user or user.get("role") not in ROLES:
            return None
        return {"sub": sub, "email": user["email"], "name": user.get("name") or user["email"], "role": user["role"],
                "can_edit_contacts": can_edit_contacts(user), "workspaces": user.get("workspaces") or [],
                "no_workspace": bool(user.get("no_workspace")), "pf": _fingerprint(store, "user:" + str(user.get("password_hash")))}
    return None


# ---------- tokens ----------
def _sign(store, body: str) -> str:
    return _b64(hmac.new(server_secret(store).encode(), f"{TOKEN_PREFIX}.{body}".encode(), hashlib.sha256).digest())


def issue_token(store, principal: dict, now: Optional[float] = None, sid: Optional[str] = None) -> str:
    issued = int(_now(now))
    claims = {"sub": principal["sub"], "sid": sid or secrets.token_hex(8), "jti": secrets.token_hex(8),
              "iat": issued, "exp": issued + TOKEN_SECONDS, "pf": principal["pf"]}
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    return f"{TOKEN_PREFIX}.{body}.{_sign(store, body)}"


def _claims(store, token: str, now: float) -> Optional[dict]:
    try:
        prefix, body, signature = (token or "").split(".")
    except ValueError:
        return None
    if prefix != TOKEN_PREFIX or not hmac.compare_digest(signature.encode(), _sign(store, body).encode()):
        return None
    try:
        claims = json.loads(_unb64(body))
        if not isinstance(claims, dict) or not now < int(claims["exp"]) or int(claims["iat"]) > now + CLOCK_SKEW_SECONDS:
            return None
        if not all(isinstance(claims.get(k), str) for k in ("sub", "sid", "jti", "pf")):
            return None
    except (ValueError, TypeError, KeyError):
        return None
    return claims


def _revoked(store, claims: dict) -> bool:
    ids = {row.get("id") for row in store.read("revoked_tokens")}
    return f"jti:{claims['jti']}" in ids or f"sid:{claims['sid']}" in ids


def authenticate(store, admin_password: str, token: str, now: Optional[float] = None) -> Optional[dict]:
    """The signed-in principal {sub, email, name, role, claims}, or None (bad, expired, revoked or stale token)."""
    moment = _now(now)
    claims = _claims(store, token, moment)
    if not claims or _revoked(store, claims):
        return None
    principal = subject_principal(store, admin_password, claims["sub"])
    if not principal or not hmac.compare_digest(principal["pf"], claims["pf"]):
        return None
    return {**principal, "claims": claims}


def renewal(store, principal: dict, now: Optional[float] = None) -> Optional[str]:
    """A fresh token for the same session once more than half of the current one's life has passed."""
    claims = principal.get("claims") or {}
    life = claims.get("exp", 0) - claims.get("iat", 0)
    if not claims or _now(now) - claims["iat"] < life * RENEW_AFTER:
        return None
    return issue_token(store, principal, now, sid=claims["sid"])


def revoke(store, claims: dict, now: Optional[float] = None) -> None:
    """Sign out: the token and every renewed copy of its session stop working. Expired entries are pruned."""
    moment = int(_now(now))
    until = max(int(claims.get("exp", 0)), moment) + TOKEN_SECONDS      # a renewed copy lives at most this long
    rows = [{"id": f"jti:{claims['jti']}", "exp": until}, {"id": f"sid:{claims['sid']}", "exp": until}]
    store.update("revoked_tokens", lambda current: [r for r in current if r.get("exp", 0) > moment] + rows)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LoginLimiter:
    """Blocks a caller for a minute after five wrong passwords; other callers are not affected.

    Remembers at most MAX_TRACKED_CALLERS callers (the least recently seen are forgotten), so a flood of addresses
    cannot grow memory without bound."""

    def __init__(self, max_callers: int = MAX_TRACKED_CALLERS):
        self._failures = OrderedDict()
        self._max = max_callers
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list:
        cutoff = time.monotonic() - LOCKOUT_SECONDS
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        if recent:
            self._failures[key] = recent
            self._failures.move_to_end(key)
        else:
            self._failures.pop(key, None)
        return recent

    def locked(self, key: str = "") -> bool:
        with self._lock:
            return len(self._recent(key)) >= MAX_FAILURES

    def record_failure(self, key: str = "") -> None:
        with self._lock:
            self._failures[key] = [*self._recent(key), time.monotonic()]
            self._failures.move_to_end(key)
            while len(self._failures) > self._max:
                self._failures.popitem(last=False)

    def reset(self, key: str = "") -> None:
        with self._lock:
            self._failures.pop(key, None)

    def __len__(self) -> int:
        return len(self._failures)
