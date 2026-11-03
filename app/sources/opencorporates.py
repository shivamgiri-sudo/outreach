"""OpenCorporates API (owner's token): https://api.opencorporates.com/documentation/API-Reference
GET https://api.opencorporates.com/v0.4/companies/search?q=&jurisdiction_code=in&current_status=Active&api_token=
-> {results: {companies: [{company: {name, company_number (CIN), incorporation_date, current_status, registered_address_in_full,
opencorporates_url}}]}}. GET /v0.4/account_status?api_token= reports usage and does not use a call.
What it gives: legal entity names, CIN and registered address. No website, no contacts. Free access is for public-benefit
and open-data use; commercial use needs a paid plan, so check your licence before using results in sales outreach."""
from .base import SourceSpec, field
from .job_apis import MAX_QUERIES

BASE = "https://api.opencorporates.com/v0.4/"


def discover(ctx, target, limit):
    words = list(target.get("keywords") or []) + list(target.get("industries") or [])
    if not words:
        raise ValueError("Add an industry or keyword: OpenCorporates searches by company name.")
    rows = []
    for word in words[:MAX_QUERIES]:
        params = {"q": word, "jurisdiction_code": "in", "current_status": "Active", "per_page": min(30, limit), "api_token": ctx.values.get("api_key", "")}
        found = ctx.cached(f"q:{word}|{params['per_page']}", lambda p=params: [
            c["company"] for c in ((ctx.get(BASE + "companies/search", params=p).get("results") or {}).get("companies") or [])
            if isinstance(c, dict) and isinstance(c.get("company"), dict)], cost=1)
        rows += [{"company": c.get("name", ""), "address": str(c.get("registered_address_in_full") or "")[:300], "country": "IN",
                  "source": "opencorporates", "source_url": c.get("opencorporates_url", "https://opencorporates.com")} for c in found if c.get("name")]
    return {"rows": rows[:limit], "requests": ctx.requests}


def test(ctx):
    ctx.get(BASE + "account_status", params={"api_token": ctx.values.get("api_key", "")})
    return {"ok": True, "message": "OpenCorporates accepted the token (account status, no call used)."}


SPEC = SourceSpec(
    "opencorporates", "OpenCorporates (company registry)", ("discover_companies",), "Company discovery",
    "Registered Indian companies by name (legal name, CIN, registered address). No website or contacts.",
    "https://api.opencorporates.com/documentation/API-Reference",
    "Needs your API token. Free tier is for public-benefit/open-data use; commercial outreach needs a paid licence. Check before relying on it.",
    ["Create an account at opencorporates.com and request an API token (see their API page for plans).", "Paste the token here and save."],
    fields=[field("api_key", "OpenCorporates API token", True, True)], cost=1, cost_note="1 call per keyword search.", rate_per_min=20,
    free_note="Free access is limited to open-data/public-benefit use; see their plans.", discover=discover, test=test)
