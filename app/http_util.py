"""One small JSON-over-HTTPS helper shared by the email, AI and Apollo adapters."""
import json
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 20


class HttpStatusError(OSError):
    """The provider answered with an error status. `code` tells a rate limit (429) or outage (5xx) from a bad request."""

    def __init__(self, code: int, detail: str = ""):
        super().__init__(f"HTTP {code}" + (f": {detail}" if detail else ""))
        self.code = code


def post_json(url: str, headers: dict, body: dict) -> dict:
    """POST JSON, return the parsed reply. 401/403 raise PermissionError; other failures raise OSError."""
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise PermissionError(f"HTTP {exc.code}") from exc
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except OSError:
            pass
        raise HttpStatusError(exc.code, detail) from exc
