"""Lusha (owner's key, paid plan): https://docs.lusha.com/
GET https://api.lusha.com/v2/person?firstName=&lastName=&companyDomain=&revealEmails=true  header api_key
-> {contact: {data: {emailAddresses: [{email, emailType, emailConfidence}], phoneNumbers: [{number, phoneType}], jobTitle: {title}, linkedinUrl}}}
GET https://api.lusha.com/account/usage  header api_key -> {usage: {used, remaining, total}} (free check).
API access is a paid Lusha feature. Only work emails and work/direct numbers are kept; personal and mobile numbers are dropped.
Field names come from Lusha's docs/examples and were not run against the live API."""
from .base import SourceSpec, field, person

BASE = "https://api.lusha.com/"


def find_email(ctx, first, last, domain, company):
    params = {"firstName": first, "lastName": last, "companyDomain": domain, "revealEmails": "true"}
    data = ctx.cached(f"ef:{domain}|{first}|{last}".lower(), lambda: (((ctx.get(BASE + "v2/person", headers={"api_key": ctx.values.get("api_key", "")}, params=params)
                      .get("contact") or {}).get("data")) or {}), cost=1)
    emails = [e for e in data.get("emailAddresses") or [] if isinstance(e, dict) and str(e.get("emailType", "work")).lower() in ("work", "professional", "business")]
    phones = [p for p in data.get("phoneNumbers") or [] if isinstance(p, dict) and str(p.get("phoneType", "")).lower() in ("work", "direct", "office")]
    title = (data.get("jobTitle") or {}).get("title") if isinstance(data.get("jobTitle"), dict) else data.get("jobTitle")
    return [person("lusha", e.get("email"), phones[0].get("number") if phones else "", f"{first} {last}".strip(), title, data.get("linkedinUrl"),
                   source_url="https://www.lusha.com") for e in emails[:1]]


def test(ctx):
    reply = ctx.get(BASE + "account/usage", headers={"api_key": ctx.values.get("api_key", "")})
    usage = reply.get("usage") or {}
    return {"ok": True, "message": f"Lusha accepted the key (usage check, no credit used). Remaining: {usage.get('remaining', '?')}."}


SPEC = SourceSpec(
    "lusha", "Lusha", ("find_email",), "People and email", "Work email and office phone for a named person at a company domain.",
    "https://docs.lusha.com/", "API access needs a paid Lusha plan. Only work emails and work/direct numbers are kept. Check Lusha's terms and India's DPDP rules.",
    ["Ask Lusha to enable API access on your plan.", "Open Settings > API and copy your key.", "Paste it here and save."],
    fields=[field("api_key", "Lusha API key", True, True)], cost=1, cost_note="1 credit per revealed contact (your plan decides the price).", rate_per_min=20,
    free_note="No free API tier is published.", find_email=find_email, test=test, needs_domain=True)
