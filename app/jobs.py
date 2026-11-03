"""Job-opening signals from public career pages (schema.org JobPosting) or a CSV of jobs."""
import json
import re
from datetime import datetime, timezone
from typing import Optional

from .hygiene import company_domain

JSONLD_RE = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
JOB_BOARD_DOMAINS = (
    "naukri.com", "indeed.com", "indeed.co.in", "foundit.in", "monsterindia.com", "linkedin.com", "apna.co",
    "shine.com", "workindia.in", "internshala.com", "freshersworld.com", "quikr.com", "glassdoor.com", "timesjobs.com",
)


def is_job_board(domain: Optional[str]) -> bool:
    return bool(domain) and any(domain == b or domain.endswith("." + b) for b in JOB_BOARD_DOMAINS)


def _walk(node):
    if isinstance(node, list):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        yield from _walk(node.get("@graph", []))


def _is_posting(node: dict) -> bool:
    kind = node.get("@type")
    return kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind)


def _location(node: dict) -> str:
    place = node.get("jobLocation")
    place = place[0] if isinstance(place, list) and place else place
    address = place.get("address") if isinstance(place, dict) else None
    if isinstance(address, dict):
        return address.get("addressLocality", "") or ""
    return address if isinstance(address, str) else ""


def parse_job_postings(html: str, url: str) -> list:
    postings = []
    for block in JSONLD_RE.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for node in filter(_is_posting, _walk(data)):
            org = node.get("hiringOrganization")
            org = {"name": org} if isinstance(org, str) else (org or {})
            postings.append({
                "title": (node.get("title") or "").strip(),
                "company": (org.get("name") or "").strip(),
                "company_site": org.get("sameAs") or org.get("url") or "",
                "location": _location(node),
                "date_posted": node.get("datePosted") or "",
                "url": url,
            })
    return [p for p in postings if p["title"] and p["company"]]


def job_age_days(date_posted: str, now: datetime) -> int:
    try:
        posted = datetime.fromisoformat((date_posted or "").replace("Z", "+00:00"))
    except ValueError:
        return 0
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return max(0, (now - posted).days)


def job_key(job: dict, domain: str) -> str:
    parts = (domain or job["company"], job["title"], job.get("location", ""))
    return "|".join(p.strip().lower() for p in parts)


def job_domain(job: dict, page_url: str) -> str:
    """Company website domain: from the posting itself, else the page host unless it is a job board."""
    own = company_domain(job.get("company_site"))
    if own:
        return own
    host = company_domain(page_url)
    return "" if is_job_board(host) else (host or "")
