"""People Data Labs (owner's key): https://docs.peopledatalabs.com/docs/person-enrichment-api
GET https://api.peopledatalabs.com/v5/person/enrich?name=&company=&min_likelihood=6&required=work_email  header X-Api-Key
-> {status: 200, likelihood, data: {full_name, work_email, job_title, linkedin_url, ...}}; HTTP 404 means no match (no credit charged).
Only work_email, title and the LinkedIn URL are kept; personal emails and mobile numbers in the reply are ignored.
PDL holds personal data about individuals: use it for B2B work contacts only and check India's DPDP obligations. There is no free key-check endpoint."""
from .base import SourceSpec, field, person


def find_email(ctx, first, last, domain, company):
    params = {"first_name": first, "last_name": last, "company": domain or company, "min_likelihood": 6, "required": "work_email"}

    def fetch():
        try:
            reply = ctx.get("https://api.peopledatalabs.com/v5/person/enrich", headers={"X-Api-Key": ctx.values.get("api_key", "")}, params=params)
        except OSError as exc:
            if getattr(exc, "code", None) == 404:
                return {}
            raise
        d = reply.get("data") or {}
        return {"email": d.get("work_email"), "title": d.get("job_title", ""), "li": d.get("linkedin_url", ""), "likelihood": reply.get("likelihood")}
    r = ctx.cached(f"ef:{domain}|{first}|{last}".lower(), fetch, cost=1)
    return [person("peopledatalabs", r["email"], name=f"{first} {last}".strip(), title=r["title"], linkedin_url=r["li"],
                   confidence=(r.get("likelihood") or 0) * 10 or None, source_url="https://www.peopledatalabs.com")] if r.get("email") else []


def test(ctx):
    return {"ok": False, "message": "People Data Labs has no free key check. The key is saved but not verified: run one small lookup to confirm it."}


SPEC = SourceSpec(
    "peopledatalabs", "People Data Labs", ("find_email",), "People and email", "Work email for a named person at a company (likelihood-scored).",
    "https://docs.peopledatalabs.com/docs/person-enrichment-api", "Paid API with a small free tier. Work emails only; PDL holds personal data, so check DPDP duties.",
    ["Create an account at peopledatalabs.com (check the free tier on their pricing page).", "Copy the API key from the dashboard.", "Paste it here and save."],
    fields=[field("api_key", "People Data Labs API key", True, True)], cost=1, cost_note="1 credit per matched person; unmatched lookups are free.", rate_per_min=30,
    free_note="PDL lists a free monthly allowance on its pricing page.", find_email=find_email, test=test, needs_domain=True)
