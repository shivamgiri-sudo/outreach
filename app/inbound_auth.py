"""Who may post a lead: shared by the login middleware (to let a call through) and by the routes (to check again).

Public capture addresses, and what proves a call is genuine:
  POST /api/inbound/{id}                 the connector's capture token (X-Capture-Token, ?token= or Bearer), or, for a
                                         website form, its public form key (which can only submit a form)
  GET|POST /api/inbound/meta/{id}        Meta: verify token on the handshake; a valid X-Hub-Signature-256 (app secret)
                                         or the capture token on a lead notification
  POST /api/inbound/google/{id}          Google Ads: google_key in the body equal to the connector key
  GET /api/inbound/{id}/embed.js         the website-form script (public by design, holds no secret)
Everything else under /api/inbound/ needs a login. Comparisons are constant time and never raise.
"""
import re

from . import capture_connectors as connectors
from .channels.inbound_parse import parse_body
from .channels.security import safe_equal
from .inbound_platforms import meta_signature_ok

MAX_BODY = 256_000
ID = r"(cap[0-9a-f]{8})"
ROUTES = (("meta", re.compile(rf"^/api/inbound/meta/{ID}$"), ("GET", "POST")),
          ("google", re.compile(rf"^/api/inbound/google/{ID}$"), ("POST",)),
          ("embed", re.compile(rf"^/api/inbound/{ID}/embed\.js$"), ("GET",)),
          ("generic", re.compile(rf"^/api/inbound/{ID}$"), ("POST",)))


def classify(method: str, path: str):
    for kind, pattern, methods in ROUTES:
        match = pattern.match(path)
        if match and method in methods:
            return kind, match.group(1)
    return None


def given_token(headers, query) -> str:
    bearer = headers.get("authorization", "")
    return (headers.get("x-capture-token") or query.get("token") or (bearer[7:].strip() if bearer.lower().startswith("bearer ") else "")).strip()


def payload_of(content_type: str, body: bytes):
    try:
        data = parse_body(content_type, body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def authorize(store, method: str, path: str, headers, query, body: bytes):
    """(connector, kind, mode) when the call proves itself, else None. mode is "token", "signature", "verify" or "form"."""
    found = classify(method, path)
    if found is None:
        return None
    kind, cid = found
    connector = connectors.get(store, cid)
    headers = {str(k).lower(): str(v) for k, v in dict(headers).items()}
    query = {str(k): str(v) for k, v in dict(query).items()}
    if connector is None or len(body) > MAX_BODY:
        return None
    mode_of_type = connectors.BY_ID.get(connector["type"], {}).get("mode")
    token = given_token(headers, query)
    if kind == "embed":
        return (connector, kind, "public") if mode_of_type == "form" else None
    if kind == "meta":
        if mode_of_type != "meta":
            return None
        if method == "GET":
            ok = query.get("hub.mode") == "subscribe" and safe_equal(query.get("hub.verify_token", ""), (connector.get("tokens") or {}).get("verify"))
            return (connector, kind, "verify") if ok else None
        if meta_signature_ok((connector.get("tokens") or {}).get("app_secret", ""), headers.get("x-hub-signature-256", ""), body):
            return connector, kind, "signature"
        return (connector, kind, "token") if connectors.token_ok(connector, token) else None
    if kind == "google":
        if mode_of_type != "google":
            return None
        if connectors.token_ok(connector, token):
            return connector, kind, "token"
        payload = payload_of(headers.get("content-type", ""), body) or {}
        return (connector, kind, "token") if connectors.token_ok(connector, str(payload.get("google_key") or "")) else None
    if connectors.token_ok(connector, token):
        return connector, kind, "token"
    if mode_of_type == "form":
        payload = payload_of(headers.get("content-type", ""), body) or {}
        key = str(payload.get("_form_key") or headers.get("x-form-key") or query.get("form_key") or "")
        if safe_equal(key, connector.get("form_key")):
            return connector, kind, "form"
    return None


async def gate_allows(store, request) -> bool:
    """For the login middleware: True only for a public capture address whose call proves itself."""
    if not request.url.path.startswith("/api/inbound/") or classify(request.method, request.url.path) is None:
        return False
    try:
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return False
    except ValueError:
        return False
    body = await request.body() if request.method == "POST" else b""
    return authorize(store, request.method, request.url.path, request.headers, request.query_params, body) is not None
