"""Apollo (owner's key): https://docs.apollo.io/ . People API Search by organization domain and titles (no email is returned,
no credit is used) and People Match for the email (about 1 credit). The key is the one in the Apollo card.
The campaign's own Apollo checkboxes (search, hiring companies, email lookup) keep working unchanged."""
from ..apollo import APOLLO_MATCH_URL, check_usage
from ..campaign_sources import APOLLO_PEOPLE_SEARCH_URL, _apollo_headers, map_apollo_person
from ..hygiene import normalise_email
from .base import SourceSpec, person


def find_people(ctx, domain, company, titles, limit):
    body = {"q_organization_domains_list": [domain], "person_titles": list(titles or []), "page": 1, "per_page": min(limit, 25)}
    rows = ctx.cached(f"ps:{domain}|{'/'.join(titles or [])}", lambda: [map_apollo_person(p) for p in
                      ctx.post(APOLLO_PEOPLE_SEARCH_URL, body=body, headers=_apollo_headers(ctx.values.get("api_key", ""))).get("people") or [] if isinstance(p, dict)])
    return [person("apollo", r["email"], name=r["contact_name"], title=r["title"], linkedin_url=r["linkedin_url"], source_url="https://app.apollo.io") for r in rows]


def find_email(ctx, first, last, domain, company):
    body = {"first_name": first, "last_name": last, "domain": domain or "", "organization_name": company or ""}
    p = ctx.cached(f"pm:{domain}|{first}|{last}".lower(), lambda: ctx.post(APOLLO_MATCH_URL, body=body, headers=_apollo_headers(ctx.values.get("api_key", ""))).get("person") or {}, cost=1)
    email = normalise_email(p.get("email"))
    if not email or email.startswith("email_not_unlocked"):
        return []
    return [person("apollo", email, name=p.get("name") or f"{first} {last}", title=p.get("title"), linkedin_url=p.get("linkedin_url"), source_url="https://app.apollo.io")]


def test(ctx):
    check_usage(ctx.values.get("api_key", ""), lambda url, headers, body: ctx.post(url, body=body, headers=headers))
    return {"ok": True, "message": "Apollo accepted the key (usage check, no credit used)."}


SPEC = SourceSpec(
    "apollo", "Apollo", ("discover_people", "find_email"), "People and email",
    "Decision makers by company domain and title; email lookup for a named person.", "https://docs.apollo.io/",
    "Official API with your key; follow Apollo's terms. Business emails only.", ["Use the same Apollo key as in Integrations > Lead data > Apollo."],
    cost=1, cost_note="People search uses no credit; an email reveal is about 1 credit.", rate_per_min=40, integration="apollo", legacy=True,
    honest_status="existing adapter (coded from docs, fake-tested); domain filter q_organization_domains_list coded from docs",
    find_people=find_people, find_email=find_email, test=test, needs_domain=True)
