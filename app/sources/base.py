"""Shared pieces for every source: the spec, the per-run context (HTTP, cache, credit cap, rate limit) and helpers.

Interfaces (each optional, a source offers the ones it can):
  discover(ctx, target, limit)                -> {"rows": [campaign rows], "requests": n}
  find_people(ctx, domain, company, titles, limit) -> [Person]      (domain + titles)
  find_email(ctx, first, last, domain, company)    -> [Person]      (name + domain)
  verify(ctx, email)                               -> Verdict
  test(ctx)                                        -> {"ok": bool, "message": str}   (costs nothing where the API allows)

Person  = {email, phone, name, title, linkedin_url, confidence (0-100 or None), verified (True/False/None),
           source (source id), source_url}
Verdict = {status: valid|invalid|risky|unknown|plausible, reason, provider, credits}
"""
import re
import time
import urllib.parse
from datetime import datetime, timezone

from ..channels import http as channel_http
from ..channels.netguard import BLOCKED_HOSTS, BLOCKED_SUFFIXES
from ..hygiene import business_domain, normalise_email, normalise_phone, safe_http_url

CAPS = ("discover_companies", "discover_people", "find_email", "verify_email", "hiring_signal")
SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
CACHE_DAYS = 7
CACHE_MAX = 800
DEFAULT_CREDIT_CAP = 100


from ..campaign_sources import KeyRejected, SourceError   # one error family for the old and the new sources


class RateLimited(SourceError):
    """The provider said slow down (HTTP 429); the source is not called again this run."""


class CreditCapReached(SourceError):
    """The per-run credit cap is used up."""


class SourceSpec:
    def __init__(self, id, title, caps, group, why, docs_url, terms, steps, fields=(), keyless=False, cost=0.0,
                 cost_note="", rate_per_min=30, free_note="", integration=None, honest_status="coded from documentation; not run against the live service",
                 discover=None, find_people=None, find_email=None, verify=None, test=None, needs_domain=False, input_note="", legacy=False):
        self.id, self.title, self.caps, self.group, self.why = id, title, tuple(caps), group, why
        self.docs_url, self.terms, self.steps, self.fields = docs_url, terms, list(steps), list(fields)
        self.keyless, self.cost, self.cost_note, self.rate_per_min = keyless, cost, cost_note, rate_per_min
        self.free_note, self.integration, self.honest_status = free_note, integration or id, honest_status
        self.discover, self.find_people, self.find_email, self.verify, self.test = discover, find_people, find_email, verify, test
        self.needs_domain, self.input_note, self.legacy = needs_domain, input_note, legacy
        self.pages = None
        assert all(c in CAPS for c in self.caps), self.id

    @property
    def free(self) -> bool:
        return self.cost == 0

    def required(self) -> list:
        return [f["key"] for f in self.fields if f.get("required")]


def field(key, label, required=False, secret=False, placeholder="", kind="text"):
    return {"key": key, "label": label, "required": required, "secret": secret, "placeholder": placeholder, "kind": kind}


KEY_FIELD = lambda label: [field("api_key", label, required=True, secret=True)]


# ---------- per-run context ----------
class Budget:
    """Credits one run may spend across every source. Cache hits and free sources cost nothing."""

    def __init__(self, cap=DEFAULT_CREDIT_CAP):
        self.cap, self.spent = cap, 0.0

    def charge(self, amount: float) -> None:
        if amount and self.cap is not None and self.spent + amount > self.cap:
            raise CreditCapReached(f"Credit cap of {self.cap} reached for this run; paid lookups stopped.")
        self.spent += amount


class Cache:
    """Responses by (source, key) kept 7 days in the `source_cache` collection, newest 800 only. Loaded once, written once."""

    def __init__(self, store=None, now=None):
        self.store, self.now = store, now or datetime.now(timezone.utc)
        self.rows = {r["k"]: r for r in (store.read("source_cache") if store else [])}
        self.new, self.hits = {}, 0

    def get(self, key: str):
        row = self.new.get(key) or self.rows.get(key)
        if row is None:
            return None
        try:
            age = self.now - datetime.fromisoformat(row["at"])
        except (ValueError, KeyError):
            return None
        if age.days >= CACHE_DAYS:
            return None
        self.hits += 1
        return {"v": row["v"]}

    def put(self, key: str, value) -> None:
        self.new[key] = {"k": key, "at": self.now.isoformat(timespec="seconds"), "v": value}

    def flush(self) -> None:
        if not (self.store and self.new):
            return
        fresh = self.new

        def apply(rows: list) -> list:
            kept = [r for r in rows if r.get("k") not in fresh]
            return [*kept, *fresh.values()][-CACHE_MAX:]
        self.store.update("source_cache", apply)
        self.new = {}


class Ctx:
    def __init__(self, http=None, fetch=None, cache=None, budget=None, sleep=time.sleep, clock=time.monotonic, now=None):
        self.http = http or default_http
        self.fetch, self.cache, self.budget = fetch, cache or Cache(), budget or Budget()
        self.sleep, self.clock = sleep, clock
        self.now = now or datetime.now(timezone.utc)
        self.values, self.params, self.spec, self.requests = {}, {}, None, 0
        self._last = {}

    def bind(self, spec, values=None, params=None) -> "Ctx":
        other = Ctx.__new__(Ctx)
        other.__dict__.update(self.__dict__)
        other.spec, other.values, other.params = spec, values or {}, params or {}
        return other

    def _throttle(self) -> None:
        if not self.spec or not self.spec.rate_per_min:
            return
        gap = 60.0 / self.spec.rate_per_min
        wait = self._last.get(self.spec.id, -1e9) + gap - self.clock()
        if wait > 0:
            self.sleep(min(wait, 5))
        self._last[self.spec.id] = self.clock()

    def call(self, method: str, url: str, headers=None, body=None, form=None, params=None) -> dict:
        """One API call through the guarded HTTP helper. 401/403 -> KeyRejected, 429 -> RateLimited, other errors OSError."""
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        self._throttle()
        self.requests += 1
        try:
            return self.http(method, url, headers or {}, body, form)
        except PermissionError as exc:
            raise KeyRejected(f"{self.spec.title if self.spec else 'The provider'} rejected the key or refused access.") from exc
        except OSError as exc:
            if getattr(exc, "code", None) == 429:
                raise RateLimited(f"{self.spec.title if self.spec else 'The provider'} says too many requests; stopped for this run.") from exc
            raise

    def get(self, url, headers=None, params=None):
        return self.call("GET", url, headers, params=params)

    def post(self, url, body=None, headers=None, form=None, params=None):
        return self.call("POST", url, headers, body=body, form=form, params=params)

    def cached(self, key: str, fn, cost: float = 0):
        """The stored answer for (source, key) or the result of fn(), charging `cost` credits only on a real call."""
        full = f"{self.spec.id}|{key}"
        hit = self.cache.get(full)
        if hit is not None:
            return hit["v"]
        self.budget.charge(cost)
        value = fn()
        self.cache.put(full, value)
        return value


def default_http(method, url, headers, body, form):
    return channel_http.request(url, headers, body=body, method=method, form=form)


# ---------- helpers ----------
def slugs(raw) -> list:
    """Company identifiers typed by the owner: a list or text split on commas/newlines. Each item is 'slug' or
    'slug|domain'. Only [A-Za-z0-9._-] slugs are kept, so a typed value can never change the host or path of a request."""
    items = raw if isinstance(raw, list) else re.split(r"[,\n]", str(raw or ""))
    out = []
    for item in items:
        slug, _, domain = str(item).strip().partition("|")
        slug, domain = slug.strip(), domain.strip().lower()
        if SLUG_RE.match(slug) and ".." not in slug and (not domain or valid_host(domain) == domain):
            out.append((slug, domain))
    return list(dict.fromkeys(out))


def valid_host(value) -> str:
    """A bare company domain, or ''. Anything with a path, port, user info or an IP literal is refused."""
    text = str(value or "").strip().lower()
    text = re.sub(r"^https?://", "", text).split("/")[0]
    text = text[4:] if text.startswith("www.") else text
    if text in BLOCKED_HOSTS or text.endswith(BLOCKED_SUFFIXES):       # the fetcher refuses these too; refuse early
        return ""
    return text if DOMAIN_RE.match(text) else ""


def pretty_name(slug: str) -> str:
    return " ".join(w.capitalize() for w in re.split(r"[-_.]+", slug) if w)


def person(source: str, email="", phone="", name="", title="", linkedin_url="", confidence=None, verified=None, source_url="") -> dict:
    mail = normalise_email(email)
    return {"email": mail or "", "phone": normalise_phone(phone) or "", "name": str(name or "").strip(), "title": str(title or "").strip(),
            "linkedin_url": safe_http_url(linkedin_url), "confidence": confidence, "verified": verified,
            "source": source, "source_url": source_url or source}


def verdict(provider: str, status: str, reason: str = "", credits: float = 0) -> dict:
    return {"provider": provider, "status": status, "reason": str(reason or "")[:120], "credits": credits}


def split_name(full: str) -> tuple:
    first, _, last = str(full or "").strip().partition(" ")
    return first, last.strip()


def city_match(location: str, cities: list) -> bool:
    text = str(location or "").lower()
    if cities:
        return any(c.lower() in text for c in cities)
    return "india" in text


def company_row(source: str, company: str, domain="", website="", city="", source_url="", **extra) -> dict:
    site = f"https://{domain}" if domain and not website else website
    row = {"company": company.strip(), "website": site, "domain": business_domain(site) or "", "city": city, "country": "IN",
           "source": source, "source_url": source_url, **extra}
    return {k: v for k, v in row.items() if v not in (None, "")}
