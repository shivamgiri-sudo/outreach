"""Official web-search APIs -> company websites and careers/contact pages. LinkedIn, job boards and directories that
forbid scraping are filtered out of results; a search result is only ever used as a link to the company's own site."""
import re
from urllib.parse import urlparse

from ..campaign_sources import own_site
from .base import company_row, valid_host

PAGE_WORDS = ("careers", "career", "jobs", "contact", "about", "team", "leadership")
SKIP_HOSTS = ("linkedin.com", "naukri.com", "indeed.com", "glassdoor", "justdial.com", "indiamart.com", "wikipedia.org", "ambitionbox.com")


def clean_results(results: list) -> list:
    """[{title, url, snippet}] without social, job-board and directory hosts."""
    out = []
    for r in results:
        url = str(r.get("url") or "")
        host = (urlparse(url).hostname or "").lower()
        if url.startswith("http") and host and not any(s in host for s in SKIP_HOSTS):
            out.append({"title": str(r.get("title") or "")[:200], "url": url, "snippet": str(r.get("snippet") or "")[:300]})
    return out


def discover_from_search(ctx, target: dict, limit: int, search, source_id: str) -> dict:
    """One query per industry/keyword and city: '<kind> company <city> careers'. Each hit on a company's own site
    becomes a company row (no contacts: those come from the enrichers)."""
    kinds = list(target.get("industries") or []) or list(target.get("keywords") or []) or ["company"]
    cities = list(target.get("cities") or []) or ["India"]
    rows, seen = [], set()
    for kind in kinds:
        for city in cities:
            if len(rows) >= limit:
                break
            q = f"{kind} company {city} careers"
            for r in ctx.cached(f"q:{q}", lambda q=q: clean_results(search(ctx, q)), cost=1):
                site, domain = own_site(r["url"])
                if not domain or domain in seen:
                    continue
                seen.add(domain)
                name = re.split(r"\s[-|–:]\s", r["title"])[0].strip() or domain
                rows.append(company_row(source_id, name, domain=domain, website=site, city="" if city == "India" else city,
                                        source_url=r["url"]))
    return {"rows": rows[:limit], "requests": ctx.requests}


def pages_for_domain(ctx, domain: str, search, limit: int = 5) -> list:
    """Careers / contact page links on one company's own domain, found by 'site:<domain> careers contact'."""
    host = valid_host(domain)
    if not host:
        return []
    results = ctx.cached(f"pages:{host}", lambda: clean_results(search(ctx, f"site:{host} careers OR contact OR about")), cost=1)
    out = []
    for r in results:
        h = (urlparse(r["url"]).hostname or "").lower()
        if (h == host or h.endswith("." + host)) and any(w in r["url"].lower() for w in PAGE_WORDS):
            out.append(r["url"])
    return out[:limit]
