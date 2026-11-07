"""Email open and click tracking for outreach emails (the HTML version only; the plain-text part is never changed).

- Opens: a 1x1 image at /api/track/o/<token>.gif. Many mail apps load images by themselves (Apple Mail privacy, some
  scanners), so opens are a rough signal; clicks are the strong one.
- Clicks: every http(s) link (not the unsubscribe link) goes through /api/track/c/<token>?u=<link>&s=<signature>; the
  signature makes it impossible to use the redirect for any other address.
Tokens are signed with the install's server secret and name the outbox row (its idempotency key). Events in the first
IGNORE_SECONDS after sending are ignored (security scanners open and click at once). Owners can switch tracking off
(settings.track_email = false).
"""
import base64
import hashlib
import hmac
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

from .channels.security import server_secret

IGNORE_SECONDS = 20
PIXEL = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
_HREF = re.compile(r'href="(https?://[^"]+)"', re.I)


def _sig(store, text: str) -> str:
    return hmac.new(server_secret(store).encode("utf-8"), text.encode("utf-8"), hashlib.sha256).hexdigest()[:20]


def make_token(store, key: str) -> str:
    raw = base64.urlsafe_b64encode(key.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{raw}.{_sig(store, 'k:' + key)}"


def read_token(store, token: str) -> Optional[str]:
    raw, _, sig = (token or "").partition(".")
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return key if sig and hmac.compare_digest(sig, _sig(store, "k:" + key)) else None


def link_ok(store, url: str, sig: str) -> bool:
    return bool(sig) and url.lower().startswith(("http://", "https://")) and hmac.compare_digest(sig, _sig(store, "u:" + url))


def decorate(store, html: str, message: dict, base_url: str) -> str:
    """Add the open pixel and wrap the links of one outreach email's HTML."""
    key, base = message.get("idempotency_key") or "", (base_url or "").rstrip("/")
    if not html or not key or not message.get("lead_id") or not base or store.read("settings").get("track_email") is False:
        return html
    token = make_token(store, key)

    def wrap(m):
        url = m.group(1).replace("&amp;", "&")
        if "/u/" in url or url.startswith(base + "/api/track/"):
            return m.group(0)                          # the unsubscribe link and our own links stay as they are
        return f'href="{base}/api/track/c/{token}?u={quote(url, safe="")}&amp;s={_sig(store, "u:" + url)}"'
    out = _HREF.sub(wrap, html)
    pixel = f'<img src="{base}/api/track/o/{token}.gif" width="1" height="1" alt="" style="display:block;border:0;width:1px;height:1px">'
    return out.replace("</body>", pixel + "</body>") if "</body>" in out else out + pixel


def record(store, key: str, kind: str, url: str = "", now: Optional[datetime] = None) -> Optional[dict]:
    """Count one open / click on the outbox row and its lead. Returns what was recorded, or None (unknown / too soon)."""
    now = now or datetime.now(timezone.utc)
    row = next((m for m in store.read("outbox") if m.get("idempotency_key") == key), None)
    if row is None or row.get("status") not in ("sent", "delivered"):
        return None
    try:
        sent = datetime.fromisoformat(str(row.get("sent_at")))
        sent = sent if sent.tzinfo else sent.replace(tzinfo=timezone.utc)
        if (now - sent).total_seconds() < IGNORE_SECONDS:
            return None
    except ValueError:
        pass
    stamp = now.isoformat(timespec="seconds")
    first = not row.get("opened_at" if kind == "open" else "clicked_at")

    def bump(rows):
        out = []
        for m in rows:
            if m.get("idempotency_key") == key:
                m = {**m, f"{kind}s": int(m.get(f"{kind}s") or 0) + 1, f"last_{kind}_at": stamp}
                m.setdefault("opened_at" if kind == "open" else "clicked_at", stamp)
                if kind == "open" and not m.get("opened_at"):
                    m["opened_at"] = stamp
                if kind == "click":
                    m["opened_at"] = m.get("opened_at") or stamp           # a click means it was opened
                    m["clicked_urls"] = list(dict.fromkeys([*(m.get("clicked_urls") or []), url]))[:20]
            out.append(m)
        return out
    store.update("outbox", bump)
    lead_id = row.get("lead_id")
    subject = (row.get("subject") or "")[:120]
    text = f"Opened the email “{subject}”" if kind == "open" else f"Clicked {url[:200]} in “{subject}”"

    def on_lead(l):
        eng = dict(l.get("engagement") or {})
        eng[f"{kind}s"] = int(eng.get(f"{kind}s") or 0) + 1
        if kind == "click" and not int(eng.get("opens") or 0):
            eng["opens"] = 1
        events = list(l.get("timeline") or [])
        if first or kind == "click":
            events.append({"at": stamp, "kind": "email_open" if kind == "open" else "email_click", "text": text})
        return {**l, "engagement": eng, "timeline": events}
    if lead_id:
        store.update_lead(lead_id, on_lead)
        if kind == "click" and first:
            from . import alerts
            alerts.safe(store, "click", lead_id, text + ". A good moment to call.")
    return {"kind": kind, "lead_id": lead_id, "first": first}
