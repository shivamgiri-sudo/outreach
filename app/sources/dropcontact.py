"""Dropcontact (owner's key): https://developer.dropcontact.com/
POST https://api.dropcontact.com/v1/enrich/all  header X-Access-Token  body {"data": [{"first_name", "last_name", "website"}], "siren": false, "language": "en"}
-> {error: false, request_id, credits_left}; then GET /v1/enrich/all/{request_id} -> {success: true, data: [{email: [{email, qualification}], job, linkedin}]}
or {success: false, reason: "Request not ready yet"}. Dropcontact works asynchronously (often 30 seconds or more): the adapter polls a few times
and returns nothing if it is not ready, so use it for small lists. Coded from the published docs; no free key-check endpoint exists."""
import re

from .base import SourceSpec, field, person

BASE = "https://api.dropcontact.com/v1/enrich/all"
POLLS = 5
ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")


def find_email(ctx, first, last, domain, company):
    headers = {"X-Access-Token": ctx.values.get("api_key", "")}

    def fetch():
        start = ctx.post(BASE, headers=headers, body={"data": [{"first_name": first, "last_name": last, "website": domain}], "siren": False, "language": "en"})
        rid = str(start.get("request_id") or "")
        if start.get("error") or not ID_RE.match(rid):
            return []
        for _ in range(POLLS):
            ctx.sleep(8)
            reply = ctx.get(f"{BASE}/{rid}", headers=headers)
            if reply.get("success"):
                found = []
                for row in reply.get("data") or []:
                    for e in row.get("email") or []:
                        if isinstance(e, dict) and e.get("email"):
                            found.append({"email": e["email"], "q": e.get("qualification", ""), "title": row.get("job", ""), "li": row.get("linkedin", "")})
                return found
        return []
    return [person("dropcontact", r["email"], name=f"{first} {last}".strip(), title=r["title"], linkedin_url=r["li"],
                   verified=True if "nominative@pro" in r["q"] else None, source_url="https://dropcontact.com")
            for r in ctx.cached(f"ef:{domain}|{first}|{last}".lower(), fetch, cost=1)]


def test(ctx):
    return {"ok": False, "message": "Dropcontact has no free key check. The key is saved but not verified: run one small campaign lookup to confirm it."}


SPEC = SourceSpec(
    "dropcontact", "Dropcontact", ("find_email",), "People and email",
    "Finds and qualifies the work email of a named person from their name and company website. Slow (about 30 seconds).",
    "https://developer.dropcontact.com/", "Official API with your key; GDPR-style compliance claims are Dropcontact's, check them for India DPDP use. Work emails only.",
    ["Sign up at dropcontact.com and open the API settings page.", "Copy your access token.", "Paste it here and save."],
    fields=[field("api_key", "Dropcontact access token", True, True)], cost=1, cost_note="About 1 credit per enriched contact (see their pricing).",
    rate_per_min=10, free_note="Dropcontact offers a trial; see their site.", find_email=find_email, test=test, needs_domain=True)
