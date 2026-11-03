"""Adzuna Jobs API (owner's own app id + key), India = country code 'in':
docs https://developer.adzuna.com/docs/search ; GET https://api.adzuna.com/v1/api/jobs/in/search/1?app_id=&app_key=
&results_per_page=&what=&where= -> {count, results: [{id, title, company: {display_name}, location: {display_name},
created, redirect_url}]}. Only the employer name, role title, city and posting date are kept (a hiring signal);
Adzuna's terms ask you to link back via redirect_url, which is stored as the source link."""
from .base import SourceSpec, field
from .job_apis import group_postings, queries

BASE = "https://api.adzuna.com/v1/api/jobs/in/"


def _auth(ctx) -> dict:
    return {"app_id": ctx.values.get("app_id", ""), "app_key": ctx.values.get("app_key", "")}


def discover(ctx, target, limit):
    postings = []
    for what, where in queries(target):
        params = {**_auth(ctx), "results_per_page": min(50, max(10, limit)), "content-type": "application/json"}
        if what:
            params["what"] = what
        if where:
            params["where"] = where
        key = f"search:{what}|{where}|{params['results_per_page']}"
        found = ctx.cached(key, lambda p=params: [
            {"company": (r.get("company") or {}).get("display_name", ""), "title": r.get("title", ""),
             "location": (r.get("location") or {}).get("display_name", ""), "posted": r.get("created", ""), "url": r.get("redirect_url", "")}
            for r in ctx.get(BASE + "search/1", params=p).get("results") or [] if isinstance(r, dict)], cost=1)
        postings += found
    return {"rows": group_postings("adzuna", postings, target.get("roles_hiring") or [], ctx.now, limit), "requests": ctx.requests}


def test(ctx):
    ctx.get(BASE + "categories", params=_auth(ctx))
    return {"ok": True, "message": "Adzuna accepted the app id and key (categories lookup, no search used)."}


SPEC = SourceSpec(
    "adzuna", "Adzuna (India jobs)", ("discover_companies", "hiring_signal"), "Company discovery",
    "Employers with live job ads in your cities, from Adzuna's India index. Gives company name, roles and ad age (no website or contacts).",
    "https://developer.adzuna.com/docs/search",
    "Official API with your own key. Keep Adzuna's attribution and link back if you show their ads. Check their terms before storing ad text; only employer, title, city and date are kept here.",
    ["Register a free developer account at developer.adzuna.com.", "Copy the Application ID and Application Key from your dashboard.",
     "Paste both here and save. Check the plan page for your allowance."],
    fields=[field("app_id", "Application ID", True, True), field("app_key", "Application Key", True, True)],
    cost=1, cost_note="1 credit per search (roles x cities, at most 6 searches per run).", rate_per_min=20,
    free_note="Adzuna offers a developer plan with rate limits; see their site for the current allowance.",
    discover=discover, test=test)
