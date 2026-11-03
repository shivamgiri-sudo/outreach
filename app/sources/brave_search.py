"""Brave Search API, owner's key: https://api-dashboard.search.brave.com/app/documentation/web-search/get-started
GET https://api.search.brave.com/res/v1/web/search?q=&count=&country=IN with header X-Subscription-Token ->
{web: {results: [{title, url, description}]}}. Plans and quotas change: read the dashboard for what your key includes."""
from .base import SourceSpec, field
from .search_common import discover_from_search, pages_for_domain

URL = "https://api.search.brave.com/res/v1/web/search"


def search(ctx, q: str, count: int = 20) -> list:
    reply = ctx.get(URL, headers={"X-Subscription-Token": ctx.values.get("api_key", ""), "Accept": "application/json"},
                    params={"q": q, "count": count, "country": "IN"})
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")}
            for r in (reply.get("web") or {}).get("results") or [] if isinstance(r, dict)]


def test(ctx):
    search(ctx, "careers", 1)
    return {"ok": True, "message": "Brave accepted the key (1 query)."}


SPEC = SourceSpec(
    "brave_search", "Brave Search API", ("discover_companies",), "Company discovery",
    "Finds company websites and careers or contact pages with Brave's official search API.",
    "https://api-dashboard.search.brave.com/app/documentation/web-search/get-started",
    "Official API under Brave's terms; check that your plan allows storing results. Results are used only as links to a company's own site.",
    ["Create an account at api-dashboard.search.brave.com and subscribe to a Search plan (check the dashboard for the free allowance).",
     "Create an API key under API Keys.", "Paste it here and save."],
    fields=[field("api_key", "Brave Search API key", True, True)], cost=1, cost_note="1 credit per query.", rate_per_min=60,
    free_note="Brave publishes the current free allowance on its dashboard; it has changed over time.",
    discover=lambda ctx, target, limit: discover_from_search(ctx, target, limit, search, "brave_search"), test=test)
SPEC.pages = lambda ctx, domain: pages_for_domain(ctx, domain, search)
