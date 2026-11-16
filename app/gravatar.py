"""Gravatar public profiles: a free way to put a name, photo and links to an email address.

Gravatar's public profile for an address is at https://gravatar.com/<md5 of the trimmed, lower-cased address>.json
(404 = no public profile). Only the hash leaves the app, never the address itself. Results are kept in the
`gravatar_cache` collection and asked again after RECHECK_DAYS. lookup() never raises: a timeout or any other failure
is reported as {"status": "error"} and is not cached, so the next run tries again.

Reply shape (the long-standing public JSON, also read defensively for the newer v3 field names):
  {"entry": [{"profileUrl", "thumbnailUrl", "displayName", "name": {"givenName", "familyName", "formatted"},
              "photos": [{"value"}], "accounts": [{"shortname", "url"}], "urls": [{"value", "title"}]}]}
  v3: {"display_name", "avatar_url", "profile_url", "verified_accounts": [{"service_type", "url"}], "links": [{"url"}]}
"""
import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from .hygiene import normalise_email, safe_http_url

PROFILE_URL = "https://gravatar.com/{hash}.json"
TIMEOUT_SECONDS = 4
RECHECK_DAYS = 30
CACHE_MAX = 5000
COLLECTION = "gravatar_cache"
USER_AGENT = "OutreachOS-Bot/0.1 (+public profile lookup)"
LIVE = True                     # tests switch this off: no real network there (tests/conftest.py)


class NoProfile(Exception):
    """Gravatar answered 404: there is no public profile for this address."""


def email_hash(email: str) -> str:
    return hashlib.md5((email or "").strip().lower().encode("utf-8")).hexdigest()


def http_fetch(url: str) -> dict:
    """GET the profile JSON. 404 raises NoProfile; anything else that goes wrong raises OSError."""
    if not LIVE:
        raise OSError("Gravatar lookups are off here")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read(200_000).decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NoProfile() from exc
        raise OSError(f"HTTP {exc.code}") from exc
    except (ValueError, TimeoutError) as exc:
        raise OSError(str(exc)[:80]) from exc


def _links(entry: dict) -> list:
    out = []
    for row in entry.get("accounts") or entry.get("verified_accounts") or []:
        if isinstance(row, dict) and safe_http_url(row.get("url")):
            out.append({"kind": str(row.get("shortname") or row.get("service_type") or row.get("domain") or "link"), "url": row["url"]})
    for row in entry.get("urls") or entry.get("links") or []:
        url = row.get("value") or row.get("url") if isinstance(row, dict) else ""
        if safe_http_url(url):
            out.append({"kind": str(row.get("title") or row.get("label") or "link"), "url": url})
    return out[:10]


def parse_profile(reply) -> Optional[dict]:
    """{"name", "photo_url", "profile_url", "linkedin_url", "links"} from either reply shape, or None when it holds no profile."""
    if not isinstance(reply, dict):
        return None
    entry = reply.get("entry")
    entry = entry[0] if isinstance(entry, list) and entry and isinstance(entry[0], dict) else reply
    names = entry.get("name") if isinstance(entry.get("name"), dict) else {}
    given = " ".join(str(names.get(k) or "").strip() for k in ("givenName", "familyName")).strip()
    name = str(names.get("formatted") or given or entry.get("displayName") or entry.get("display_name") or "").strip()
    photos = [p.get("value") for p in entry.get("photos") or [] if isinstance(p, dict)]
    photo = safe_http_url(entry.get("thumbnailUrl") or entry.get("avatar_url") or (photos[0] if photos else ""))
    profile = safe_http_url(entry.get("profileUrl") or entry.get("profile_url"))
    links = _links(entry)
    linkedin = next((l["url"] for l in links if "linkedin.com/in/" in l["url"].lower()), "")
    if not (name or photo or profile or links):
        return None
    return {"name": name[:120], "photo_url": photo, "profile_url": profile, "linkedin_url": linkedin, "links": links}


def _fresh(row: dict, now: datetime) -> bool:
    try:
        at = datetime.fromisoformat(row["at"])
    except (KeyError, TypeError, ValueError):
        return False
    return now - (at if at.tzinfo else at.replace(tzinfo=timezone.utc)) < timedelta(days=RECHECK_DAYS)


def lookup(store, email: str, fetch: Optional[Callable] = None, now: Optional[datetime] = None) -> dict:
    """{"status": "found"|"none"|"error", "profile"?, "cached": bool}. Never raises."""
    now = now or datetime.now(timezone.utc)
    addr = normalise_email(email)
    if not addr:
        return {"status": "none", "cached": False}
    digest = email_hash(addr)
    try:
        row = next((r for r in store.read(COLLECTION) if r.get("h") == digest), None)
    except Exception:                                   # noqa: BLE001 - a broken cache never stops enrichment
        row = None
    if row and _fresh(row, now):
        return {"status": "found" if row.get("profile") else "none", "profile": row.get("profile"), "cached": True}
    try:
        profile = parse_profile((fetch or http_fetch)(PROFILE_URL.format(hash=digest)))
    except NoProfile:
        profile = None
    except Exception:                                   # noqa: BLE001 - timeouts, network, odd replies: try again next run
        return {"status": "error", "cached": False}
    entry = {"h": digest, "at": now.isoformat(timespec="seconds"), "profile": profile}
    try:
        store.update(COLLECTION, lambda rows: [*(r for r in rows if r.get("h") != digest), entry][-CACHE_MAX:])
    except Exception:                                   # noqa: BLE001
        pass
    return {"status": "found" if profile else "none", "profile": profile, "cached": False}
