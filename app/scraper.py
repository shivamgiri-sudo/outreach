"""Public contact extraction from web pages. Respects robots.txt; stores the source URL."""
import ipaddress
import re
import socket
import urllib.request
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from .hygiene import normalise_email, normalise_phone

USER_AGENT = "OutreachOS-Bot/0.1 (+business contacts, honours robots.txt)"
FETCH_TIMEOUT_SECONDS = 10
MAX_PAGE_BYTES = 1_000_000
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


def fetch_page(url: str) -> str:
    """Fetch a page only if robots.txt allows it."""
    parts = urlparse(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("Only http/https URLs are allowed")
    _assert_public_host(parts.hostname or "")
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        robots = _get(robots_url)
    except OSError:
        robots = ""
    if robots and not robots_allows(robots, url):
        raise PermissionError("robots.txt does not allow fetching this page")
    return _get(url)


def _assert_public_host(host: str) -> None:
    """Block loopback/private targets so the tool cannot be pointed at internal services."""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror as exc:
        raise ValueError(f"Cannot resolve {host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise PermissionError("That address is not a public website")


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_SECONDS) as resp:
        return resp.read(MAX_PAGE_BYTES).decode("utf-8", errors="replace")
