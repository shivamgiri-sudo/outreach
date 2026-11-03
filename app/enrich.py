"""Contact enrichment from a company's own public web pages (robots.txt respected)."""
import time

from .scraper import extract_contacts, fetch_page

PAGE_PATHS = ("/", "/contact", "/contact-us", "/about", "/careers")
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


def find_public_contacts(domain: str, fetch=polite_fetch) -> dict:
    """Visit the company's own pages; keep same-domain emails and published phone numbers with their source URL."""
    emails, phones, checked = {}, {}, 0
    for url in candidate_urls(domain):
        try:
            html = fetch(url)
        except (OSError, ValueError, PermissionError):
            continue
        checked += 1
        found = extract_contacts(html, url)
        for email in found["emails"]:
            if _same_domain(email, domain):
                emails.setdefault(email, url)
        for phone in found["phones"]:
            phones.setdefault(phone, url)
    return {"emails": emails, "phones": phones, "pages_checked": checked}
