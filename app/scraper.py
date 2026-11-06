"""Public contact extraction from web pages. Respects robots.txt; stores the source URL."""
import ipaddress
import re
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from .channels.netguard import (BLOCKED_HOSTS, BLOCKED_SUFFIXES, GENERIC_FETCH_ERROR, BlockedAddress, FetchFailed,
                                guarded_request, is_public_ip)
from .hygiene import normalise_email, normalise_phone

USER_AGENT = "OutreachOS-Bot/0.1 (+business contacts, honours robots.txt)"
FETCH_TIMEOUT_SECONDS = 10
MAX_PAGE_BYTES = 1_000_000
MAX_REDIRECTS = 3
FILE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
OBFUSCATED_RE = re.compile(r"([A-Za-z0-9._%+\-]+)\s*[\[\(]\s*at\s*[\]\)]\s*([A-Za-z0-9\-]+)\s*[\[\(]\s*dot\s*[\]\)]\s*([A-Za-z]{2,})", re.I)
PHONE_RE = re.compile(r"(?:\+91[\s\-]?)?(?<!\d)[6-9]\d{4}[\s\-]?\d{5}(?!\d)")


def extract_contacts(html: str, url: str) -> dict:
    text = OBFUSCATED_RE.sub(lambda m: f"{m.group(1)}@{m.group(2)}.{m.group(3)}", html)
    emails = {
        e for e in (normalise_email(m) for m in EMAIL_RE.findall(text))
        if e and not e.endswith(FILE_SUFFIXES)
    }
    phones = {p for p in (normalise_phone(m) for m in PHONE_RE.findall(html)) if p}
    return {"emails": sorted(emails), "phones": sorted(phones), "source_url": url}


def robots_allows(robots_txt: str, url: str) -> bool:
    parser = RobotFileParser()
    parser.parse(robots_txt.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def fetch_page(url: str, resolver=None, max_bytes: int = MAX_PAGE_BYTES) -> str:
    """Fetch a page only if robots.txt allows it, through the SSRF guard (public addresses only, IP pinned,
    redirects re-checked). Failures say only "Couldn't fetch that page.", so the tool can't probe internal hosts."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("Only http/https URLs are allowed")
    _refuse_typed_private(parts.hostname or "")
    _refuse_linkedin(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        robots = _get(robots_url, resolver)
    except OSError:
        robots = ""
    if robots and not robots_allows(robots, url):
        raise PermissionError("robots.txt does not allow fetching this page")
    return _get(url, resolver, max_bytes)


def _refuse_linkedin(url: str) -> None:
    host = (urlparse(url).hostname or "").rstrip(".").lower()
    if host == "linkedin.com" or host.endswith(".linkedin.com") or host in ("lnkd.in", "linked.in"):
        raise PermissionError("LinkedIn pages are never fetched: import a Sales Navigator export instead")


def _refuse_typed_private(host: str) -> None:
    """An address the person typed that is plainly private (an IP literal or localhost) gets a clear answer.
    Names that only resolve to a private address get the generic fetch error instead (no DNS oracle)."""
    host = host.rstrip(".").lower()
    if host in BLOCKED_HOSTS or host.endswith(BLOCKED_SUFFIXES):
        raise PermissionError("That address is not a public website")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return
    if not is_public_ip(literal):
        raise PermissionError("That address is not a public website")


def _get(url: str, resolver=None, max_bytes: int = MAX_PAGE_BYTES) -> str:
    try:
        resp = guarded_request(url, "GET", {"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
                               timeout=FETCH_TIMEOUT_SECONDS, max_bytes=max_bytes, resolver=resolver,
                               allow_http=True, max_redirects=MAX_REDIRECTS, check_hop=_refuse_linkedin)
    except (BlockedAddress, FetchFailed) as exc:
        raise FetchFailed(GENERIC_FETCH_ERROR) from exc
    if resp.status in (404, 410):            # only public hosts get here, so saying why is safe
        raise FetchFailed(f"That page was not found (HTTP {resp.status}). Check the address.")
    if resp.status in (401, 403, 429):
        raise FetchFailed(f"That website refused automated reading (HTTP {resp.status}). Open the page in your browser "
                          "and paste its HTML (or a CSV) instead.")
    if resp.status >= 300:
        raise FetchFailed(GENERIC_FETCH_ERROR)
    return resp.body.decode("utf-8", errors="replace")
