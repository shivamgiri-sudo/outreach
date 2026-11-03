"""HTTP for channel adapters: JSON, form or raw bodies, any method, and redirects are never followed.

`request(url, headers, body)` has the same shape as `http_util.post_json`, so either can be injected where a
provider only needs a JSON POST. Errors: 401/403 raise PermissionError, everything else raises OSError.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 20
MAX_RESPONSE_BYTES = 1_000_000
ERROR_DETAIL_CHARS = 300


from ..http_util import HttpStatusError   # a subclass of OSError: callers that catch OSError still work


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        return None  # a 3xx then surfaces as an HTTPError instead of being followed


_OPENER = urllib.request.build_opener(_NoRedirect)


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
            timeout: int = TIMEOUT_SECONDS) -> dict:
    headers = dict(headers or {})
    data = _encode(headers, body, form, raw)
    req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with _OPENER.open(req, timeout=timeout) as response:
            text = response.read(MAX_RESPONSE_BYTES).decode("utf-8", "replace")
            meta = {"_status": response.status, "_headers": {k.lower(): v for k, v in response.headers.items()}}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(2000).decode("utf-8", "replace")
        except (OSError, ValueError, AttributeError):
            pass
        if exc.code in (401, 403):
            raise PermissionError(f"HTTP {exc.code}") from exc
        raise HttpStatusError(exc.code, detail[:ERROR_DETAIL_CHARS]) from exc
    except urllib.error.URLError as exc:
        raise OSError(f"Could not connect ({exc.reason})") from exc
    return parse_response(text, meta)
