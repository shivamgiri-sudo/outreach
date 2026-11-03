"""RocketReach (owner's key): https://rocketreach.co/api
GET https://api.rocketreach.co/api/v2/person/lookup?name=&current_employer=  header Api-Key
-> {id, status: complete|searching|waiting|failed, name, current_title, linkedin_url, emails: [{email, smtp_valid: valid|invalid|inconclusive, type, grade}]}
GET /api/v2/person/checkStatus?ids=ID polls an unfinished lookup. GET /api/v2/account/ -> account and usage (free check).
Only professional-type emails are kept. API access is a paid plan feature. Coded from the published reference; not run live."""
from .base import SourceSpec, field, person

BASE = "https://api.rocketreach.co/api/v2/"
POLLS = 3


def find_email(ctx, first, last, domain, company):
    headers = {"Api-Key": ctx.values.get("api_key", "")}

    def fetch():
        rec = ctx.get(BASE + "person/lookup", headers=headers, params={"name": f"{first} {last}".strip(), "current_employer": company or domain})
        for _ in range(POLLS):
            if rec.get("status") in ("complete", "failed") or not rec.get("id"):
                break
            ctx.sleep(3)
            polled = ctx.get(BASE + "person/checkStatus", headers=headers, params={"ids": rec["id"]}).get("_list") or []
            rec = polled[0] if polled and isinstance(polled[0], dict) else rec
        if rec.get("status") != "complete":
            return []
        return [{"email": e["email"], "ok": e.get("smtp_valid"), "grade": e.get("grade"), "title": rec.get("current_title", ""), "li": rec.get("linkedin_url", "")}
                for e in rec.get("emails") or [] if isinstance(e, dict) and e.get("email") and str(e.get("type", "professional")).lower() == "professional"]
    return [person("rocketreach", r["email"], name=f"{first} {last}".strip(), title=r["title"], linkedin_url=r["li"],
                   confidence={"A": 95, "A-": 90, "B": 75}.get(r["grade"]), verified=True if r["ok"] == "valid" else (False if r["ok"] == "invalid" else None),
                   source_url="https://rocketreach.co") for r in ctx.cached(f"ef:{domain}|{first}|{last}".lower(), fetch, cost=1)]


def test(ctx):
    reply = ctx.get(BASE + "account/", headers={"Api-Key": ctx.values.get("api_key", "")})
    return {"ok": True, "message": f"RocketReach accepted the key (account check, no lookup used). Plan: {reply.get('plan_name') or reply.get('plan') or 'unknown'}."}


SPEC = SourceSpec(
    "rocketreach", "RocketReach", ("find_email",), "People and email", "Professional email for a named person at a company.",
    "https://rocketreach.co/api", "Paid API. Only professional emails are kept. Follow RocketReach's terms and India's DPDP rules.",
    ["Choose a RocketReach plan that includes API access.", "Open Account > API and generate a key.", "Paste it here and save."],
    fields=[field("api_key", "RocketReach API key", True, True)], cost=1, cost_note="1 lookup credit per completed contact.", rate_per_min=20,
    free_note="No free API tier is published.", find_email=find_email, test=test, needs_domain=True)
