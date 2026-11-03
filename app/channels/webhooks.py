"""Webhook and OAuth-callback verification, used both by the login middleware and by the routes themselves.

A webhook call is accepted only for a provider that is saved, and only when it proves it knows the secret:
  * a valid provider signature: Meta X-Hub-Signature-256 (app secret), Resend/Svix (whsec_ secret),
    Cal.com X-Cal-Signature-256 and Calendly-Webhook-Signature (the per-provider secret this app generated);
  * otherwise the per-provider secret as ?token=…, an X-Webhook-Secret header or a basic-auth password
    (for providers that cannot sign or send headers). When a signing secret is configured, only a valid
    signature is accepted.
Every comparison is constant time and never raises on non-ASCII input. Rate limiting is out of scope.
"""
import base64
import hashlib
import hmac
import re
import time

from .config import effective, entry_of, is_saved, provider_values
from .registry import get_provider, webhook_pairs
from .security import safe_equal, verify_state

WEBHOOK_PATH = re.compile(r"^/api/webhooks/([a-z_]+)/([a-z0-9_]+)$")
OAUTH_CALLBACK = re.compile(r"^/api/oauth/(google|microsoft)/callback$")
MAX_BODY_BYTES = 5_000_000
SIGNATURE_TOLERANCE_SECONDS = 300


def _hmac_hex(key: str, data: bytes) -> str:
    return hmac.new(str(key).encode("utf-8"), data, hashlib.sha256).hexdigest()


def _basic_password(header: str) -> str:
    if not header.lower().startswith("basic "):
        return ""
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""
    return decoded.split(":", 1)[1] if ":" in decoded else ""


def _token_ok(token: str, headers: dict, query: dict) -> bool:
    candidates = (query.get("token", ""), headers.get("x-webhook-secret", ""), _basic_password(headers.get("authorization", "")))
    return any(safe_equal(c, token) for c in candidates if c)


def _fresh(timestamp: str, now: float) -> bool:
    try:
        return abs(now - int(timestamp)) <= SIGNATURE_TOLERANCE_SECONDS
    except (TypeError, ValueError):
        return False


def _meta(values, headers, body, token, now):
    secret = values.get("app_secret")
    if not secret:
        return None
    given = headers.get("x-hub-signature-256", "")
    return safe_equal(given, "sha256=" + _hmac_hex(secret, body))


def _svix(values, headers, body, token, now):
    secret = values.get("signing_secret")
    if not secret:
        return None
    msg_id, stamp, sigs = headers.get("svix-id", ""), headers.get("svix-timestamp", ""), headers.get("svix-signature", "")
    if not (msg_id and stamp and sigs and _fresh(stamp, now)):
        return False
    try:
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except (ValueError, TypeError):
        return False
    expected = base64.b64encode(hmac.new(key, f"{msg_id}.{stamp}.".encode() + body, hashlib.sha256).digest()).decode()
    return any(safe_equal(part.split(",", 1)[-1], expected) for part in sigs.split())


def _calcom(values, headers, body, token, now):
    given = headers.get("x-cal-signature-256", "")
    return safe_equal(given, _hmac_hex(token, body)) if given else None


def _calendly(values, headers, body, token, now):
    given = headers.get("calendly-webhook-signature", "")
    if not given:
        return None
    parts = dict(p.split("=", 1) for p in given.split(",") if "=" in p)
    stamp, sig = parts.get("t", ""), parts.get("v1", "")
    return bool(_fresh(stamp, now) and safe_equal(sig, _hmac_hex(token, f"{stamp}.".encode() + body)))


SIGNATURES = {"meta": _meta, "svix": _svix, "calcom": _calcom, "calendly": _calendly}


def verify(store, ch_id: str, pid: str, method: str, headers, query, body: bytes, now: float = None) -> bool:
    if (ch_id, pid) not in webhook_pairs():
        return False
    entry = entry_of(store.read("integrations"), ch_id)
    token = (entry.get("webhook_tokens") or {}).get(pid)
    if not token or not is_saved(ch_id, entry, pid):
        return False
    provider = get_provider(ch_id, pid)
    headers = {str(k).lower(): str(v) for k, v in dict(headers or {}).items()}
    query = {str(k): str(v) for k, v in dict(query or {}).items()}
    if method == "GET":
        return pid == "meta_cloud" and query.get("hub.mode") == "subscribe" and safe_equal(query.get("hub.verify_token", ""), token)
    check = SIGNATURES.get(provider["webhook"]["auth"])
    values = effective(provider, provider_values(ch_id, entry, pid))
    signed = check(values, headers, body or b"", token, now or time.time()) if check else None
    if signed is not None:
        return signed
    return _token_ok(token, headers, query)


async def gate_allows(store, request) -> bool:
    """For the login middleware: True only for an exact webhook / OAuth-callback route whose secret checks out."""
    path, method = request.url.path, request.method
    match = WEBHOOK_PATH.match(path)
    if match and method in ("GET", "POST") and (match.group(1), match.group(2)) in webhook_pairs():
        if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
            return False
        body = await request.body() if method == "POST" else b""
        return len(body) <= MAX_BODY_BYTES and verify(store, match.group(1), match.group(2), method, request.headers,
                                                     request.query_params, body)
    match = OAUTH_CALLBACK.match(path)
    if match and method == "GET":
        payload = verify_state(store, request.query_params.get("state", ""))
        return bool(payload and payload.get("provider") == match.group(1))
    return False
