"""Signed unsubscribe links: /u/<lead_id>.<signature>.

The signature is a 22-character HMAC-SHA256 of the lead id with the server secret, so nobody can unsubscribe a
lead whose link they never received. It is checked in constant time.

Older emails carry the unsigned form /u/<lead_id>. Those keep working for 60 days after that email was sent, but
only for an email that really went out before signing started (settings.unsub_signed_since) and only when the
lead still exists. Every use of an unsigned link is logged on the lead's timeline and in the server log.
"""
import base64
import hashlib
import hmac
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .channels.security import server_secret
from .growth_core import public_base_url

SIG_BYTES = 16                       # 22 characters in base64url
LEGACY_DAYS = 60
CUTOVER_KEY = "unsub_signed_since"
log = logging.getLogger("outreach.unsubscribe")


def signature(secret: str, lead_id: str) -> str:
    digest = hmac.new(secret.encode(), f"unsub:{lead_id}".encode("utf-8"), hashlib.sha256).digest()[:SIG_BYTES]
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def unsub_token(store, lead_id: str) -> str:
    return f"{lead_id}.{signature(server_secret(store), str(lead_id))}"


def unsub_path(store, lead_id: str) -> str:
    return f"/u/{unsub_token(store, lead_id)}"


def unsub_url(store, base_url: str, lead_id: str) -> str:
    """The full link for emails: the public address set in Setup (or the environment default) plus the signed path."""
    return f"{public_base_url(store, base_url)}{unsub_path(store, lead_id)}"


def ensure_cutover(store, now: Optional[datetime] = None) -> str:
    """Record when signed links started (once), so the 60-day grace for older unsigned links has a fixed start."""
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return store.update("settings", lambda s: s if s.get(CUTOVER_KEY) else {**s, CUTOVER_KEY: stamp})[CUTOVER_KEY]


def parse(store, token: str) -> tuple:
    """(lead_id, signed) where signed is True (valid signature), False (wrong signature) or None (unsigned link)."""
    lead_id, dot, sig = str(token or "").partition(".")
    if not dot:
        return lead_id, None
    expected = signature(server_secret(store), lead_id)
    return lead_id, hmac.compare_digest(sig.encode("utf-8", "replace"), expected.encode())


def legacy_allowed(store, lead_id: str, now: Optional[datetime] = None) -> bool:
    """An unsigned link works only for a real email sent to this lead before signing started, for 60 days after it."""
    now = now or datetime.now(timezone.utc)
    cutover = _aware(store.read("settings").get(CUTOVER_KEY))
    if not cutover:
        return False
    for row in store.read("outbox"):
        if row.get("lead_id") != lead_id or row.get("status") != "sent":
            continue
        sent = _aware(row.get("sent_at"))
        if sent and sent < cutover and now - sent <= timedelta(days=LEGACY_DAYS):
            return True
    return False


def _aware(text) -> Optional[datetime]:
    try:
        value = datetime.fromisoformat(str(text or ""))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def log_legacy_use(lead_id: str) -> None:
    log.warning("unsigned unsubscribe link used for lead %s (legacy grace period)", lead_id)
