"""Fake HTTP for the source adapters. Every name, domain and number is made up; payloads follow the providers' documented shapes."""
from datetime import datetime, timezone

from app.sources import registry
from app.sources.base import Budget, Cache, Ctx

NOW = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)


class FakeHttp:
    """Callable like the guarded transport: (method, url, headers, body, form) -> dict. Routes match by URL part, first match wins.
    A reply may be a dict, an Exception (raised), a callable(call) or a list consumed one item per call (the last one repeats)."""

    def __init__(self, routes: dict = None):
        self.routes, self.calls = dict(routes or {}), []

    def __call__(self, method, url, headers, body, form):
        call = {"method": method, "url": url, "headers": dict(headers or {}), "body": body, "form": form}
        self.calls.append(call)
        for part, reply in self.routes.items():
            if part in url:
                if isinstance(reply, list):
                    reply = reply.pop(0) if len(reply) > 1 else reply[0]
                if isinstance(reply, Exception):
                    raise reply
                return reply(call) if callable(reply) else reply
        raise OSError(f"no fake route for {url}")

    def urls(self, part: str = "") -> list:
        return [c["url"] for c in self.calls if part in c["url"]]


def ctx_for(source_id: str, http: FakeHttp, values: dict = None, params: dict = None, fetch=None, cap=100, store=None):
    sleeps = []
    base = Ctx(http=http, fetch=fetch, cache=Cache(store, NOW), budget=Budget(cap), sleep=sleeps.append, clock=lambda: 0.0, now=NOW)
    base.sleeps = sleeps
    bound = base.bind(registry.get(source_id), values or {}, params or {})
    bound.sleeps = sleeps
    return bound


class HttpError(OSError):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code
