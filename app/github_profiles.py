"""Public GitHub contacts for a company domain: a free step in Find & enrich ("public_profiles", OFF by default).

For a lead whose company domain is known:
  1. find the GitHub organisation whose public website (the org's "blog" field) is on that domain: the org named like the
     domain (acme.in -> "acme") plus the top results of GitHub's public user search for that name (type:org); each
     candidate's profile is read and kept only when its website is on the domain. Two or more matches = ambiguous:
     skipped. None = nothing.
  2. read only PUBLIC profile fields: the org's public email, and the public email of up to MAX_MEMBERS public members,
     kept only when the address is on the company domain. Never noreply / privacy / security-type addresses (users.noreply.github.com), never
     commit metadata, never private data.
Found people become other contacts with source "github" and their profile URL (app/find_enrich.save_outcome).

Limits: the public REST API without a token allows 60 requests an hour per server. Every reply's X-RateLimit-Remaining
is read; under RESERVE left, or on HTTP 403 / 429, the step stops and backs off until the reset time (at least
BACKOFF_SECONDS), kept in settings.github_backoff_until, so later leads in the run and later runs skip GitHub until then.
At most MAX_DOMAINS domains per run, and each domain is looked up at most once per CACHE_DAYS (the "github_profile_cache"
collection, at most MAX_CACHED rows). An optional token in the server environment (OUTREACH_GITHUB_TOKEN) raises the
limit; it is never stored in the data file.
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from urllib.parse import quote

from .hygiene import business_domain, normalise_email
from .scraper import NOT_FOR_OUTREACH

API = "https://api.github.com"
UA = "MCN-Outreach/1.0 (public profiles)"
CACHE_KEY = "github_profile_cache"
BACKOFF_KEY = "github_backoff_until"
CACHE_DAYS = 30
MAX_CACHED = 1000
MAX_DOMAINS = 20
MAX_CANDIDATES = 3
MAX_MEMBERS = 8
RESERVE = 3
BACKOFF_SECONDS = 3600
LIVE = True
NOREPLY = re.compile(r"(^|[.@])(users\.)?noreply\.github\.com$|^noreply@|no-reply@", re.I)


class RateLimited(Exception):
    def __init__(self, until: datetime):
        super().__init__(f"GitHub rate limit; paused until {until.isoformat(timespec='minutes')}")
        self.until = until


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def http_get(url: str) -> tuple:
    """(status, headers, body text) through the SSRF-guarded client."""
    if not LIVE:
        raise OSError("network lookups are off")
    from .channels.netguard import guarded_request
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("OUTREACH_GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = guarded_request(url, "GET", headers, timeout=10, max_bytes=600_000, max_redirects=2)
    return resp.status, resp.headers, resp.body.decode("utf-8", errors="replace")


class Client:
    """GitHub GETs for one run: stops for good on a rate limit (RateLimited), counts requests."""

    def __init__(self, get: Callable, now: Optional[datetime] = None):
        self.get, self.now, self.requests = get, now or now_utc(), 0

    def __call__(self, path: str):
        status, headers, body = self.get(API + path)
        self.requests += 1
        headers = {str(k).lower(): v for k, v in (headers or {}).items()}
        if status in (403, 429):
            raise RateLimited(self._reset(headers))
        if status == 404:
            return None
        if status >= 400:
            raise OSError(f"HTTP {status}")
        data = json.loads(body or "null")
        try:
            remaining = int(headers.get("x-ratelimit-remaining", RESERVE + 1))
        except (TypeError, ValueError):
            remaining = RESERVE + 1
        if remaining < RESERVE:
            raise RateLimited(self._reset(headers))
        return data

    def _reset(self, headers: dict) -> datetime:
        floor = self.now + timedelta(seconds=BACKOFF_SECONDS)
        try:
            reset = datetime.fromtimestamp(int(headers.get("x-ratelimit-reset")), timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            return floor
        return max(reset, floor) if reset > self.now else floor


def site_domain(blog) -> Optional[str]:
    text = str(blog or "").strip()
    return business_domain(text) if text else None


def public_email(value, domain: str) -> str:
    """A public profile email only when it is a real address on the company domain (never a noreply address)."""
    email = normalise_email(value)
    if not email or NOREPLY.search(email.split("@", 1)[1]) or NOREPLY.search(email) or email.split("@")[0] in NOT_FOR_OUTREACH:
        return ""
    host = email.split("@", 1)[1]
    return email if host == domain or host.endswith("." + domain) else ""


def find_org(domain: str, call: Client) -> dict:
    """{"status": "found"|"none"|"ambiguous", "org"?}: the one organisation whose website is on the domain."""
    label = domain.split(".")[0]
    logins = [label]
    found = call(f"/search/users?q={quote(label)}+type:org&per_page={MAX_CANDIDATES}") or {}
    for item in (found.get("items") or [])[:MAX_CANDIDATES]:
        login = str(item.get("login") or "")
        if login and login.lower() not in {l.lower() for l in logins}:
            logins.append(login)
    matches = []
    for login in logins[:MAX_CANDIDATES + 1]:
        org = call(f"/orgs/{quote(login)}")
        if org and site_domain(org.get("blog")) == domain:
            matches.append(org)
    if len(matches) > 1:
        return {"status": "ambiguous", "candidates": [m.get("login") for m in matches]}
    return {"status": "found", "org": matches[0]} if matches else {"status": "none"}


def contacts(domain: str, call: Client, company: str = "") -> dict:
    """{"status", "org_url"?, "people": [other-contact rows]} for one domain."""
    org_hit = find_org(domain, call)
    if org_hit["status"] != "found":
        return {"status": org_hit["status"], "people": []}
    org = org_hit["org"]
    login, org_url = org.get("login") or "", org.get("html_url") or f"https://github.com/{org.get('login', '')}"
    people = []
    if (email := public_email(org.get("email"), domain)):
        people.append(_row(org.get("name") or login, "Organisation address on GitHub", email, org_url, company))
    for member in (call(f"/orgs/{quote(login)}/public_members?per_page={MAX_MEMBERS}") or [])[:MAX_MEMBERS]:
        user = call(f"/users/{quote(str(member.get('login') or ''))}") if member.get("login") else None
        if user and (email := public_email(user.get("email"), domain)):
            url = user.get("html_url") or f"https://github.com/{member['login']}"
            people.append(_row(user.get("name") or member["login"], "Public GitHub profile", email, url, company))
    return {"status": "found", "org_url": org_url, "people": people}


def _row(name: str, title: str, email: str, url: str, company: str) -> dict:
    return {"contact_name": str(name)[:120], "title": title, "email": email, "linkedin_url": "", "profile_url": url,
            "source": "github", "found_at": "github", "company": company}


# ------------------------------------------------------------------ cache + back-off
def _rows(store) -> list:
    try:
        return store.read(CACHE_KEY)
    except KeyError:
        return []


def cached(store, domain: str, now: datetime) -> Optional[dict]:
    row = next((r for r in _rows(store) if r.get("d") == domain), None)
    try:
        return row["r"] if now - datetime.fromisoformat(row["at"]) < timedelta(days=CACHE_DAYS) else None
    except (TypeError, KeyError, ValueError):
        return None


def _remember(store, domain: str, result: dict, now: datetime) -> None:
    row = {"d": domain, "at": now.isoformat(timespec="seconds"), "r": result}
    try:
        store.update(CACHE_KEY, lambda rows: [*(r for r in rows if r.get("d") != domain), row][-MAX_CACHED:])
    except KeyError:
        store.write(CACHE_KEY, [row])


def paused_until(store, now: datetime) -> Optional[datetime]:
    try:
        until = datetime.fromisoformat((store.read("settings") or {}).get(BACKOFF_KEY) or "")
    except ValueError:
        return None
    return until if until > now else None


def lookup(store, domain: str, get: Optional[Callable] = None, now: Optional[datetime] = None, company: str = "") -> dict:
    """Cache first; then GitHub unless paused. {"status": found|none|ambiguous|paused|error, "people", "cached"}."""
    now = now or now_utc()
    hit = cached(store, domain, now)
    if hit is not None:
        return {**hit, "cached": True}
    if (until := paused_until(store, now)):
        return {"status": "paused", "people": [], "until": until.isoformat(timespec="minutes"), "cached": False}
    try:
        result = contacts(domain, Client(get or http_get, now), company)
    except RateLimited as exc:
        store.update("settings", lambda s: {**s, BACKOFF_KEY: exc.until.isoformat(timespec="seconds")})
        return {"status": "paused", "people": [], "until": exc.until.isoformat(timespec="minutes"), "cached": False}
    except Exception as exc:                            # noqa: BLE001 - network / odd replies: try again next run
        return {"status": "error", "people": [], "error": str(exc)[:120], "cached": False}
    _remember(store, domain, result, now)
    return {**result, "cached": False}
