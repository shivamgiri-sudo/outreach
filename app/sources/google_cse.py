"""Google Programmable Search (Custom Search JSON API), owner's key + search engine id (cx):
https://developers.google.com/custom-search/v1/overview ; GET https://www.googleapis.com/customsearch/v1?key=&cx=&q=&num=10&gl=in
-> {items: [{title, link, snippet}]}. 100 free queries a day, then billed (see Google's page).
IMPORTANT: Google has closed this API to NEW customers and will shut it down on 1 January 2027 (Google's overview page).
It works only for projects that already had it. The Brave Search source is the replacement."""
from .base import SourceSpec, field
from .search_common import discover_from_search, pages_for_domain

URL = "https://www.googleapis.com/customsearch/v1"


def search(ctx, q: str, num: int = 10) -> list:
    reply = ctx.get(URL, params={"key": ctx.values.get("api_key", ""), "cx": ctx.values.get("cx", ""), "q": q, "num": num, "gl": "in"})
    return [{"title": i.get("title", ""), "url": i.get("link", ""), "snippet": i.get("snippet", "")} for i in reply.get("items") or [] if isinstance(i, dict)]


def test(ctx):
    search(ctx, "careers", 1)
    return {"ok": True, "message": "Google accepted the key and search engine id (1 query of your 100 free a day)."}


SPEC = SourceSpec(
    "google_cse", "Google Programmable Search", ("discover_companies",), "Company discovery",
    "Finds company websites and careers pages with Google's official search API. Only works for accounts that already had the API.",
    "https://developers.google.com/custom-search/v1/overview",
    "Official API under Google's terms; results are used only as links to a company's own site. Closed to new customers; ends 1 Jan 2027.",
    ["Only for existing users: in programmablesearchengine.google.com open your search engine and copy its Search engine ID.",
     "In Google Cloud console create an API key with Custom Search API enabled.", "Paste the key and the engine id here and save.",
     "New accounts cannot get access any more: use Brave Search instead."],
    fields=[field("api_key", "API key", True, True), field("cx", "Search engine ID", True)],
    cost=1, cost_note="1 credit per query. 100 free queries a day, then billed by Google.", rate_per_min=60,
    free_note="100 free queries a day for existing customers (per Google's overview).",
    discover=lambda ctx, target, limit: discover_from_search(ctx, target, limit, search, "google_cse"), test=test)
SPEC.pages = lambda ctx, domain: pages_for_domain(ctx, domain, search)
