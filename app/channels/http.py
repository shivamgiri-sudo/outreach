"""HTTP for channel adapters: JSON, form or raw bodies, any method, and redirects are never followed.

`request(url, headers, body)` has the same shape as `http_util.post_json`, so either can be injected where a
provider only needs a JSON POST. Errors: 401/403 raise PermissionError, everything else raises OSError.
Every call goes through the SSRF guard (channels/netguard.py): the host is resolved once, must be public, and the
connection is pinned to that IP.
"""
import json
import urllib.parse

from ..http_util import HttpStatusError   # a subclass of OSError: callers that catch OSError still work
from .netguard import PRIVATE_MESSAGE, BlockedAddress, FetchFailed, guarded_request

TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 1_000_000
ERROR_DETAIL_CHARS = 300
USER_AGENT = "OutreachOS/1.0"


def _encode(headers: dict, body, form, raw):
    if raw is not None:
        return raw if isinstance(raw, bytes) else str(raw).encode("utf-8")
    if form is not None:
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        return urllib.parse.urlencode(form, doseq=True).encode("utf-8")
    if body is not None:
        headers.setdefault("Content-Type", "application/json")
        return json.dumps(body).encode("utf-8")
    return None


def parse_response(text: str, meta: dict) -> dict:
    """JSON object replies come back as a dict; lists go under `_list`, anything else under `_raw`."""
    try:
        parsed = json.loads(text) if text.strip() else {}
    except ValueError:
        parsed = {"_raw": text}
    if not isinstance(parsed, dict):
        parsed = {"_list": parsed}
    return {**parsed, **meta}


def request(url: str, headers: dict = None, body=None, method: str = "POST", form: dict = None, raw=None,
            timeout: int = TIMEOUT_SECONDS, resolver=None) -> dict:
    headers = dict(headers or {})
    if not any(k.lower() == "user-agent" for k in headers):
        headers["User-Agent"] = USER_AGENT
    data = _encode(headers, body, form, raw)
    try:
        resp = guarded_request(url, method.upper(), headers, data, timeout=timeout, max_bytes=MAX_RESPONSE_BYTES,
                               resolver=resolver)
    except BlockedAddress as exc:
        raise OSError(PRIVATE_MESSAGE) from exc
    except FetchFailed as exc:
        raise OSError("Could not connect") from exc
    except ValueError as exc:
        raise OSError(str(exc)) from exc
    if resp.status in (401, 403):
        raise PermissionError(f"HTTP {resp.status}")
    if resp.status >= 300:            # a redirect is never followed: it surfaces as an error like any other status
        detail = resp.body[:2000].decode("utf-8", "replace")
        raise HttpStatusError(resp.status, detail[:ERROR_DETAIL_CHARS])
    return parse_response(resp.body.decode("utf-8", "replace"), {"_status": resp.status, "_headers": resp.headers})
