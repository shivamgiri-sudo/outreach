"""The Muse public jobs API (no key needed; a free key raises the limit): https://www.themuse.com/developers/api/v2
GET https://www.themuse.com/api/public/jobs?page=1&location=...&category=... -> {page_count, results: [{name, publication_date,
locations: [{name}], company: {name}, refs: {landing_page}}]}. Limit: 500 requests/hour without a key, 3,600 with one.
COVERAGE, honestly: The Muse is US-centred and its listings are mostly US cities and 'Flexible / Remote'. India coverage is
thin; this source mostly finds nothing for Indian cities. Included because it is free and lawful; do not rely on it."""
from .base import SourceSpec, city_match, field
from .job_apis import group_postings, queries


def discover(ctx, target, limit):
    postings = []
    for what, where in queries(target)[:3]:
        location = f"{where}, India" if where else "India"
        key = f"jobs:{location}|1"
        params = {"page": 1, "location": location}
        if ctx.values.get("api_key"):
            params["api_key"] = ctx.values["api_key"]
        postings += ctx.cached(key, lambda p=params: [
            {"company": (r.get("company") or {}).get("name", ""), "title": r.get("name", ""),
             "location": ", ".join(l.get("name", "") for l in r.get("locations") or [] if isinstance(l, dict)),
             "posted": r.get("publication_date", ""), "url": (r.get("refs") or {}).get("landing_page", "")}
            for r in ctx.get("https://www.themuse.com/api/public/jobs", params=p).get("results") or [] if isinstance(r, dict)])
    cities = list(target.get("cities") or [])
    postings = [p for p in postings if city_match(p.get("location"), cities)]       # drop US-only and 'Flexible / Remote' listings
    return {"rows": group_postings("themuse", postings, target.get("roles_hiring") or [], ctx.now, limit), "requests": ctx.requests}


def test(ctx):
    reply = ctx.get("https://www.themuse.com/api/public/jobs", params={"page": 1, "location": "Bengaluru, India"})
    n = len(reply.get("results") or [])
    return {"ok": True, "message": f"The Muse answered ({n} job(s) for Bengaluru on page 1). Expect little India coverage."}


SPEC = SourceSpec(
    "themuse", "The Muse (limited India coverage)", ("discover_companies", "hiring_signal"), "Company discovery",
    "Free job listings API. US-centred: India coverage is thin, so expect few or no results.",
    "https://www.themuse.com/developers/api/v2",
    "Public API under The Muse's terms (https://www.themuse.com/developers/api/v2/terms). Free; 500 requests an hour without a key.",
    ["Nothing is required. Optionally register an app at themuse.com/developers to get a key for a higher limit and paste it here."],
    fields=[field("api_key", "The Muse API key (optional)", False, True)], keyless=True, rate_per_min=30,
    free_note="Free; a key raises the hourly limit.", cost_note="Free.", discover=discover, test=test)
