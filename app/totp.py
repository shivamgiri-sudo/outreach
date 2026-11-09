"""Time-based one-time codes (RFC 6238 on top of RFC 4226 HOTP), standard library only.

Settings match every phone authenticator app (Google Authenticator, Microsoft Authenticator, Authy, 1Password):
SHA1, 6 digits, 30-second steps. A code from one step before or after the current one is accepted (clock drift),
and the caller passes the last step it accepted so the same code can never be used twice.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
DRIFT_STEPS = 1
SECRET_BYTES = 20                  # 160 bits, as RFC 4226 recommends
RECOVERY_CODES = 8


def new_secret() -> str:
    """A random base32 secret without padding (32 characters), as authenticator apps expect it typed in."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode().rstrip("=")


def _key(secret_b32: str) -> bytes:
    text = "".join(str(secret_b32 or "").split()).upper()
    return base64.b32decode(text + "=" * (-len(text) % 8))


def hotp(key: bytes, counter: int, digits: int = DIGITS, digest=hashlib.sha1) -> str:
    """RFC 4226: HMAC of the 8-byte counter, dynamic truncation, last `digits` decimal digits."""
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    offset = mac[-1] & 0x0F
    number = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10 ** digits)).zfill(digits)


def step_of(now: Optional[float] = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def code_at(secret_b32: str, now: Optional[float] = None, digits: int = DIGITS) -> str:
    return hotp(_key(secret_b32), step_of(now), digits)


def verify(secret_b32: str, code: str, now: Optional[float] = None, last_step: int = -1) -> Optional[int]:
    """The step the code belongs to when it is right and newer than `last_step`, else None."""
    code = "".join(str(code or "").split())
    if len(code) != DIGITS or not code.isdigit():
        return None
    try:
        key = _key(secret_b32)
    except (ValueError, TypeError):
        return None
    if not key:
        return None
    current = step_of(now)
    for step in range(current - DRIFT_STEPS, current + DRIFT_STEPS + 1):
        if step > last_step and hmac.compare_digest(hotp(key, step), code):
            return step
    return None


def otpauth_uri(secret_b32: str, account: str, issuer: str) -> str:
    """The otpauth:// link an authenticator app reads (from a QR code or pasted)."""
    label = quote(f"{issuer}:{account}", safe="@:")
    return f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"


# ---------- recovery codes ----------
def new_recovery_codes(count: int = RECOVERY_CODES) -> list:
    """Codes like 'k3f9q-7xw2m' (50 bits each), shown once."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    make = lambda: "".join(secrets.choice(alphabet) for _ in range(10))
    return [f"{c[:5]}-{c[5:]}" for c in (make() for _ in range(count))]


def normalise_recovery(code: str) -> str:
    return "".join(ch for ch in str(code or "").lower() if ch.isalnum())


def hash_recovery(pepper: str, code: str) -> str:
    return hmac.new(pepper.encode(), ("recovery:" + normalise_recovery(code)).encode(), hashlib.sha256).hexdigest()
