"""Contact enrichment from a company's own public web pages (robots.txt respected, polite delay between pages).

The usual pages are the home, contact, about and careers pages. The wider read (`wide=True`, Find & enrich's "Team and
press pages" step) also looks at team / leadership / press / investor / partner / event pages: first the ones the home
page links to, then a few common addresses, at most MAX_PAGES_PER_DOMAIN pages per company in all. Addresses meant for
complaints or legal matters (privacy@, security@, abuse@, dpo@, legal@, postmaster@, noreply@ ...) are skipped for sales
use, and a name is paired with an email when both sit on the same card (app/site_people.py).
"""
import re
import time
from urllib.parse import urljoin, urlparse

from .scraper import extract_contacts, fetch_page, outreach_ok
from .site_people import pairs

PAGE_PATHS = ("/", "/contact", "/contact-us", "/about", "/careers")
WIDE_PATHS = ("/team", "/our-team", "/leadership", "/management", "/people", "/press", "/media", "/news", "/investors",
              "/investor-relations", "/partners", "/events", "/speakers")
LINK_WORDS = ("team", "leadership", "management", "people", "press", "media", "news", "investor", "partners", "events", "speakers")
MAX_PAGES_PER_DOMAIN = 10
HREF_RE = re.compile(r'href=["\']([^"\'#>\s]+)', re.I)
HIRING_LOCAL_PARTS = ("hr", "careers", "jobs", "recruitment", "recruiter", "talent", "hiring")
GENERIC_LOCAL_PARTS = ("info", "contact", "hello", "admin", "support", "sales", "office", "mail", "enquiry")
POLITE_DELAY_SECONDS = 1.0


def needs(lead: dict) -> list:
    gaps = []
    if not lead.get("email"):
        gaps.append("email")
    if not lead.get("phone"):
        gaps.append("phone")
    if not lead.get("contact_name"):
        gaps.append("decision_maker")
    return gaps


def candidate_urls(domain: str) -> list:
    return [f"https://{domain}{path}" for path in PAGE_PATHS]


def _same_domain(email: str, domain: str) -> bool:
    host = email.rsplit("@", 1)[-1]
    return host == domain or host.endswith("." + domain)


def pick_best_email(emails: list, domain: str):
    """Prefer hiring mailboxes, then personal ones, then generic; only addresses on the company's own domain."""
    own = [e for e in emails if _same_domain(e, domain)]

    def rank(email: str) -> tuple:
        local = email.split("@", 1)[0]
        if local in HIRING_LOCAL_PARTS:
            return (0, HIRING_LOCAL_PARTS.index(local))
        return (2, 0) if local in GENERIC_LOCAL_PARTS else (1, 0)

    return min(own, key=rank) if own else None


def polite_fetch(url: str) -> str:
    time.sleep(POLITE_DELAY_SECONDS)
    return fetch_page(url)


def linked_pages(html: str, base: str, domain: str) -> list:
    """Same-site links on a page whose path names a team / press / investor / partner / event page."""
    out = []
    for href in HREF_RE.findall(html or ""):
        url = urljoin(base, href)
        parts = urlparse(url)
        host = (parts.hostname or "").lower().removeprefix("www.")
        if parts.scheme in ("http", "https") and host == domain and any(w in parts.path.lower() for w in LINK_WORDS):
            url = f"https://{domain}{parts.path.rstrip('/') or '/'}"
            if url not in out:
                out.append(url)
    return out


def find_public_contacts(domain: str, fetch=polite_fetch, wide: bool = False) -> dict:
    """Visit the company's own pages; keep same-domain emails and published phone numbers with their source URL.

    Also returns `people` ([{"name", "email", "title", "source"}] where a name and email share a card) and `skipped`
    (addresses not for sales use)."""
    emails, phones, people, skipped, checked = {}, {}, {}, set(), 0
    queue, done = candidate_urls(domain), set()
    while queue and len(done) < MAX_PAGES_PER_DOMAIN:
        url = queue.pop(0)
        if url in done:
            continue
        done.add(url)
        try:
            html = fetch(url)
        except (OSError, ValueError, PermissionError):
            continue
        checked += 1
        if wide and url == f"https://{domain}/":
            extra = [u for u in linked_pages(html, url, domain) if u not in queue and u not in done]
            queue += extra + [f"https://{domain}{p}" for p in WIDE_PATHS if f"https://{domain}{p}" not in extra]
        found = extract_contacts(html, url)
        for email in found["emails"]:
            if not _same_domain(email, domain):
                continue
            if outreach_ok(email):
                emails.setdefault(email, url)
            else:
                skipped.add(email)
        for phone in found["phones"]:
            phones.setdefault(phone, url)
        for person in pairs(html, url):
            if _same_domain(person["email"], domain):
                people.setdefault(person["email"], person)
                emails.setdefault(person["email"], url)
    return {"emails": emails, "phones": phones, "pages_checked": checked, "people": list(people.values()), "skipped": sorted(skipped)}
