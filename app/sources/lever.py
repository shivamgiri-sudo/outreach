"""Lever Postings API (public, no key): https://github.com/lever/postings-api
GET api.lever.co/v0/postings/{site}?mode=json&limit=N&skip=N -> [{id, text, categories: {location, team, commitment},
hostedUrl, createdAt (ms)}]. EU-hosted accounts use api.eu.lever.co (write the site as eu:{site})."""
from datetime import datetime, timezone

from .ats_common import SLUG_STEP, SLUGS_FIELD, discover_boards, test_first_board
from .base import SourceSpec, pretty_name

PAGE = 100


def fetch_board(ctx, slug: str) -> dict:
    host = "api.lever.co"
    site = slug
    if slug.lower().startswith("eu-"):          # 'eu-acme' means the EU host and site 'acme'
        host, site = "api.eu.lever.co", slug[3:]
    reply = ctx.get(f"https://{host}/v0/postings/{site}", params={"mode": "json", "limit": PAGE})
    postings = reply.get("_list") or []
    jobs = []
    for p in postings:
        if not isinstance(p, dict):
            continue
        ms = p.get("createdAt")
        posted = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat() if isinstance(ms, (int, float)) else ""
        jobs.append({"title": p.get("text", ""), "location": (p.get("categories") or {}).get("location", "") or p.get("country", ""),
                     "posted": posted, "url": p.get("hostedUrl", "")})
    return {"name": pretty_name(site), "jobs": jobs}


def discover(ctx, target, limit):
    return discover_boards(ctx, target, limit, fetch_board, "lever", lambda s: f"https://jobs.lever.co/{s}")


SPEC = SourceSpec(
    "lever", "Lever job postings", ("discover_companies", "hiring_signal"), "Company discovery",
    "Open roles at companies that publish a Lever careers page.", "https://github.com/lever/postings-api",
    "Public Postings API meant for careers sites. No key. First 100 postings per company are read.",
    [SLUG_STEP.format(example="jobs.lever.co/acme"), "For an EU-hosted account write the identifier as eu-acme.", "Paste and save. No key is needed."],
    fields=[SLUGS_FIELD], keyless=True, rate_per_min=60, free_note="Free, no key.", cost_note="Free. 1 request per company.",
    discover=discover, test=lambda ctx: test_first_board(ctx, fetch_board, "Lever"))
