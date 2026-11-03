"""Prospeo (owner's key): https://prospeo.io/api-docs
POST https://api.prospeo.io/enrich-person  header X-KEY  body {"only_verified_email": true, "data": {"first_name", "last_name", "company_website"}}
-> {error: false, person: {full_name, current_job_title, linkedin_url, email: {status: VERIFIED, email}}}
GET  https://api.prospeo.io/account-information  header X-KEY -> {error: false, response: {remaining_credits, ...}} (free check).
The docs overview was readable; the exact field names above come from Prospeo's published examples. Confirm with one live call."""
from .base import SourceSpec, field, person

BASE = "https://api.prospeo.io/"


def find_email(ctx, first, last, domain, company):
    body = {"only_verified_email": True, "data": {"first_name": first, "last_name": last, "company_website": domain}}
    reply = ctx.cached(f"ef:{domain}|{first}|{last}".lower(), lambda: ctx.post(BASE + "enrich-person", body=body, headers={"X-KEY": ctx.values.get("api_key", "")}), cost=1)
    p = reply.get("person") or {}
    mail = p.get("email") or {}
    if reply.get("error") or not mail.get("email"):
        return []
    return [person("prospeo", mail["email"], name=p.get("full_name") or f"{first} {last}", title=p.get("current_job_title"), linkedin_url=p.get("linkedin_url"),
                   verified=str(mail.get("status", "")).upper() == "VERIFIED", source_url="https://prospeo.io")]


def test(ctx):
    reply = ctx.get(BASE + "account-information", headers={"X-KEY": ctx.values.get("api_key", "")})
    left = (reply.get("response") or {}).get("remaining_credits", "?")
    return {"ok": not reply.get("error"), "message": f"Prospeo accepted the key (account check, no credit used). Credits left: {left}."}


SPEC = SourceSpec(
    "prospeo", "Prospeo", ("find_email",), "People and email", "Finds a verified work email for a named person at a company website.",
    "https://prospeo.io/api-docs", "Official API with your key; B2B work emails only. Follow Prospeo's terms.",
    ["Sign up at prospeo.io (check their site for free credits).", "Open the API section of your dashboard and copy your API key.", "Paste it here and save."],
    fields=[field("api_key", "Prospeo API key", True, True)], cost=1, cost_note="About 1 credit per verified email found; the key check is free.",
    rate_per_min=30, free_note="Prospeo lists a free allowance on its pricing page.", find_email=find_email, test=test, needs_domain=True)
