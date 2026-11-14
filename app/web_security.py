"""HTTP hardening that wraps every request: the caller's real address, rate limits and security headers.

- `client_ip`: the TCP peer, except when the peer is the trusted local proxy (Caddy on 127.0.0.1); then the last
  X-Forwarded-For hop (the address that connected to the proxy). A client cannot spoof it by sending its own header,
  because the proxy appends the real address last.
- Rate limits: a token bucket per address and route group, kept in memory with a hard cap on how many addresses
  are remembered (the least recently seen are dropped first).
- Headers: nosniff, referrer policy, no framing, a Content-Security-Policy, and HSTS when the request came in over
  HTTPS. Public HTML pages (/u/*, /demo/*) get a page policy; a route can set its own (the demo page adds a nonce
  for its one script).
"""
import ipaddress
import threading
import time
from collections import OrderedDict

from fastapi.responses import PlainTextResponse

TRUSTED_PROXIES = ("127.0.0.1", "::1")
MAX_TRACKED = 10_000
# (path, exact match?, requests per minute)
RATE_LIMITS = (
    ("/api/login", True, 5),
    ("/api/login/", False, 10),                     # the two-step code and setup during sign-in
    ("/u/", False, 30),
    ("/demo/", False, 60),
    ("/api/webhooks/", False, 120),
    ("/api/inbound/", False, 120),
    ("/api/calltracker/hook/", False, 240),          # DialHalo webhooks (signature checked in app/calltracker_events.py)
    ("/api/activation/event", True, 120),
    ("/api/linkedin-runner/pair", True, 10),        # office runner pairing codes: wrong guesses are also capped per address and code
    ("/api/linkedin-runner/install", False, 20),
    ("/api/linkedin-runner/bundle", False, 20),
    ("/api/signup", False, 20),                     # self sign-up (app/signup.py also limits per address and email domain)
    ("/api/oidc/", False, 30),                      # Google / Microsoft sign-in
    ("/api/magic-link", False, 10),                 # emailed sign-in links
)
TOO_MANY = "Too many requests. Wait a minute and try again."
PAGE_CSP = ("default-src 'self'; script-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'")
API_CSP = "default-src 'none'; frame-ancestors 'none'"
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")
HSTS = "max-age=31536000; includeSubDomains"


def page_csp(nonce: str = "") -> str:
    """The policy for a public HTML page; with a nonce, only the script carrying it may run."""
    return PAGE_CSP.replace("script-src 'none'", f"script-src 'nonce-{nonce}'") if nonce else PAGE_CSP


def _valid_ip(text: str) -> str:
    try:
        return str(ipaddress.ip_address(text.strip().strip("[]")))
    except ValueError:
        return ""


def client_ip(request) -> str:
    peer = request.client.host if request.client else ""
    if peer in TRUSTED_PROXIES:
        hops = [h for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
        if hops and (real := _valid_ip(hops[-1])):
            return real
    return peer


def is_https(request) -> bool:
    if request.url.scheme == "https":
        return True
    peer = request.client.host if request.client else ""
    return peer in TRUSTED_PROXIES and request.headers.get("x-forwarded-proto", "").split(",")[-1].strip() == "https"


_clock = time.monotonic


class RateLimiter:
    """Token bucket per key: `per_minute` requests, refilled smoothly. Remembers at most `max_keys` keys."""

    def __init__(self, per_minute: int, max_keys: int = MAX_TRACKED, clock=None):
        self.capacity = float(per_minute)
        self.refill = per_minute / 60.0
        self.max_keys = max_keys
        self.clock = clock or (lambda: _clock())             # looked up per call, so tests can freeze it
        self._buckets = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        with self._lock:
            now = self.clock()
            tokens, last = self._buckets.pop(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill)
            allowed = tokens >= 1
            self._buckets[key] = (tokens - 1 if allowed else tokens, now)
            while len(self._buckets) > self.max_keys:
                self._buckets.popitem(last=False)
            return allowed

    def __len__(self) -> int:
        return len(self._buckets)


def make_limiters(limits=RATE_LIMITS) -> list:
    return [(path, exact, RateLimiter(per_minute)) for path, exact, per_minute in limits]


def limiter_for(limiters: list, path: str):
    for prefix, exact, limiter in limiters:
        if (path == prefix) if exact else path.startswith(prefix):
            return prefix, limiter
    return None, None


def rate_limit_middleware(limiters: list):
    async def middleware(request, call_next):
        group, limiter = limiter_for(limiters, request.url.path)
        if limiter is not None and request.method != "OPTIONS" and not limiter.allow(client_ip(request)):
            return PlainTextResponse(TOO_MANY, status_code=429, headers={"Retry-After": "60"})
        return await call_next(request)
    return middleware


def security_headers_middleware(docs_enabled: bool = False):
    async def middleware(request, call_next):
        response = await call_next(request)
        path = request.url.path
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("X-Frame-Options", "DENY")
        if not (docs_enabled and path.startswith(DOCS_PATHS)):
            headers.setdefault("Content-Security-Policy", PAGE_CSP if path.startswith(("/u/", "/demo/")) else API_CSP)
        if path.startswith("/api/"):
            headers.setdefault("Cache-Control", "no-store")
        if is_https(request):
            headers.setdefault("Strict-Transport-Security", HSTS)
        return response
    return middleware
