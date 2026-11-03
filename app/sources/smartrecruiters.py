"""SmartRecruiters Posting API, public postings of a company (no key): https://developers.smartrecruiters.com/docs/posting-api
GET https://api.smartrecruiters.com/v1/companies/{companyIdentifier}/postings?limit=100&country=in
-> {totalFound, content: [{id, name, releasedDate, company: {name}, location: {city, country}}]}.
Only the unauthenticated public postings are read, never the internal ones that need a key."""
from .ats_common import SLUG_STEP, SLUGS_FIELD, discover_boards, test_first_board
from .base import SourceSpec, pretty_name


def fetch_board(ctx, slug: str) -> dict:
    reply = ctx.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 100, "country": "in"})
    items = [p for p in reply.get("content") or [] if isinstance(p, dict)]
    name = next((p["company"]["name"] for p in items if isinstance(p.get("company"), dict) and p["company"].get("name")), "")
    jobs = [{"title": p.get("name", ""), "location": ", ".join(x for x in ((p.get("location") or {}).get("city"), (p.get("location") or {}).get("country")) if x),
             "posted": p.get("releasedDate", ""), "url": f"https://jobs.smartrecruiters.com/{slug}/{p.get('id', '')}"} for p in items]
    return {"name": name or pretty_name(slug), "jobs": jobs}


def discover(ctx, target, limit):
    return discover_boards(ctx, target, limit, fetch_board, "smartrecruiters", lambda s: f"https://jobs.smartrecruiters.com/{s}")


SPEC = SourceSpec(
    "smartrecruiters", "SmartRecruiters job boards", ("discover_companies", "hiring_signal"), "Company discovery",
    "Open public roles (India) at companies that use SmartRecruiters.", "https://developers.smartrecruiters.com/docs/posting-api",
    "Public postings endpoint, no key. India postings only (country=in). Do not use the internal-postings scope.",
    [SLUG_STEP.format(example="jobs.smartrecruiters.com/Acme"), "Paste and save. No key is needed."],
    fields=[SLUGS_FIELD], keyless=True, rate_per_min=60, free_note="Free, no key.", cost_note="Free. 1 request per company.",
    discover=discover, test=lambda ctx: test_first_board(ctx, fetch_board, "SmartRecruiters"))
