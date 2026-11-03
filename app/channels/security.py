"""Secrets used by the hub: the server secret, constant-time comparisons, signed OAuth state, masking."""
import base64
import hashlib
import hmac
import json
import secrets
import time

SECRET_TAIL = 4
STATE_TTL_SECONDS = 600


def mask(value) -> str:
    text = str(value or "")
    return "••••" + text[-SECRET_TAIL:] if len(text) > SECRET_TAIL else "••••"


def is_masked(value) -> bool:
    return isinstance(value, str) and value.startswith("••••")


def new_token() -> str:
    return secrets.token_urlsafe(24)


def _bytes(value) -> bytes:
    return value if isinstance(value, bytes) else str(value).encode("utf-8", "replace")


def safe_equal(given, expected) -> bool:
    """Constant-time compare that never raises, even for non-ASCII header values."""
    if not given or not expected:
        return False
    return hmac.compare_digest(_bytes(given), _bytes(expected))


def server_secret(store) -> str:
    """A random secret created on first use and kept in settings. No API ever returns it."""
    existing = store.read("settings").get("server_secret")
    if existing:
        return existing

    def add(settings: dict) -> dict:
        return settings if settings.get("server_secret") else {**settings, "server_secret": secrets.token_hex(32)}
    return store.update("settings", add)["server_secret"]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_state(store, payload: dict, ttl: int = STATE_TTL_SECONDS, now: float = None) -> str:
    body = {**payload, "exp": int((now or time.time()) + ttl), "n": secrets.token_hex(8)}
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(server_secret(store).encode(), raw, hashlib.sha256).digest()
    return f"{_b64(raw)}.{_b64(sig)}"


def verify_state(store, token: str, now: float = None):
    """The payload of a valid, unexpired state; None otherwise."""
    try:
        raw_part, sig_part = (token or "").split(".", 1)
        raw, sig = _unb64(raw_part), _unb64(sig_part)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(server_secret(store).encode(), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("exp", 0) < (now or time.time()):
        return None
    return payload
