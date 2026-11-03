"""Shared grouping for job-search APIs: many postings -> one hiring-signal row per employer."""
from ..campaign_sources import hiring_signals
from .base import company_row

MAX_QUERIES = 6


def queries(target: dict) -> list:
    """(what, where) pairs: each role (or keyword/industry) in each city, at most MAX_QUERIES."""
    whats = list(target.get("roles_hiring") or []) or list(target.get("keywords") or []) or list(target.get("industries") or []) or [""]
    cities = list(target.get("cities") or []) or [""]
    return [(w, c) for w in whats for c in cities][:MAX_QUERIES]


def group_postings(source: str, postings: list, wanted: list, now, limit: int) -> list:
    """postings: [{company, title, location, posted, url}] -> rows with open_roles, role and job age per employer."""
    by_company = {}
    for p in postings:
        name = str(p.get("company") or "").strip()
        if name and p.get("title"):
            by_company.setdefault(name.lower(), {"name": name, "jobs": []})["jobs"].append(p)
    rows = []
    for item in list(by_company.values())[:limit]:
        jobs = item["jobs"]
        signals = hiring_signals([{"title": j["title"], "date_posted": j.get("posted", "")} for j in jobs], wanted, now)
        rows.append(company_row(source, item["name"], city=next((j["location"] for j in jobs if j.get("location")), ""),
                                source_url=next((j["url"] for j in jobs if j.get("url")), ""), **signals))
    return rows
