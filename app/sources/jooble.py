"""Jooble REST API (owner's own key; request one at https://jooble.org/api/about): POST https://jooble.org/api/{key}
body {"keywords", "location", "page", "ResultOnPage"} -> {"totalCount", "jobs": [{title, location, snippet, salary, source,
type, link, company, updated, id}]}. The request/response shape is from Jooble's published sample code; the docs page itself
only describes the access form, so confirm with one small call. Employer, title, city and date are kept."""
from .base import SourceSpec, field
from .job_apis import group_postings, queries

BASE = "https://jooble.org/api/"


def _search(ctx, what: str, where: str, size: int) -> list:
    body = {"keywords": what, "location": where, "page": "1", "ResultOnPage": str(size)}
    reply = ctx.post(BASE + ctx.values.get("api_key", ""), body=body)
    return [{"company": j.get("company", ""), "title": j.get("title", ""), "location": j.get("location", ""),
             "posted": j.get("updated", ""), "url": j.get("link", "")} for j in reply.get("jobs") or [] if isinstance(j, dict)]


def discover(ctx, target, limit):
    postings = []
    for what, where in queries(target):
        size = min(50, max(10, limit))
        postings += ctx.cached(f"search:{what}|{where}|{size}", lambda w=what, c=where: _search(ctx, w, c, size), cost=1)
    return {"rows": group_postings("jooble", postings, target.get("roles_hiring") or [], ctx.now, limit), "requests": ctx.requests}


def test(ctx):
    _search(ctx, "receptionist", "Pune", 1)
    return {"ok": True, "message": "Jooble accepted the key (one search for 1 result)."}


SPEC = SourceSpec(
    "jooble", "Jooble (job search API)", ("discover_companies", "hiring_signal"), "Company discovery",
    "Employers with live job ads in your cities, from Jooble's aggregated index. Gives company name, roles and ad age.",
    "https://jooble.org/api/about",
    "Official API; the key is issued to you on request. Follow https://jooble.org/info/terms. Only employer, title, city, date and link are kept.",
    ["Open jooble.org/api/about and fill in the access form to get an API key by email.", "Paste the key here and save."],
    fields=[field("api_key", "Jooble API key", True, True)], cost=1, cost_note="1 request per search; no price is published for the free key.",
    rate_per_min=20, free_note="The key is free on request; limits are set by Jooble.",
    discover=discover, test=test)
