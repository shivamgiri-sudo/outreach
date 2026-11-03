"""Single admin password with signed, expiring tokens and a simple login lockout."""
import base64
import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

TOKEN_HOURS = 12
MAX_FAILURES = 5
LOCKOUT_SECONDS = 60


def _key(password: str) -> bytes:
    return hashlib.sha256(("outreach-token:" + password).encode("utf-8")).digest()


def make_token(password: str, now: Optional[datetime] = None) -> str:
    expires = int(((now or datetime.now(timezone.utc)) + timedelta(hours=TOKEN_HOURS)).timestamp())
    payload = base64.urlsafe_b64encode(str(expires).encode()).decode().rstrip("=")
    signature = hmac.new(_key(password), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_token(password: str, token: str, now: Optional[datetime] = None) -> bool:
    try:
        payload, signature = token.split(".", 1)
        expected = hmac.new(_key(password), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        expires = int(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode())
    except (ValueError, TypeError):
        return False
    return (now or datetime.now(timezone.utc)).timestamp() < expires


def password_matches(expected: str, given: str) -> bool:
    return hmac.compare_digest(expected.encode("utf-8"), (given or "").encode("utf-8"))


class LoginLimiter:
    """Blocks a caller for a minute after five wrong passwords; other callers are not affected."""

    def __init__(self):
        self._failures = {}

    def _recent(self, key: str) -> list:
        cutoff = time.monotonic() - LOCKOUT_SECONDS
        self._failures[key] = [t for t in self._failures.get(key, []) if t > cutoff]
        return self._failures[key]

    def locked(self, key: str = "") -> bool:
        return len(self._recent(key)) >= MAX_FAILURES

    def record_failure(self, key: str = "") -> None:
        self._recent(key).append(time.monotonic())

    def reset(self, key: str = "") -> None:
        self._failures.pop(key, None)
