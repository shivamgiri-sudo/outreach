"""Hunter (owner's key): https://hunter.io/api-documentation/v2
domain-search  GET /v2/domain-search?domain=&department=&seniority=&limit=  -> data.emails[{value, confidence, first_name, last_name, position, linkedin, phone_number, verification: {status}}]
email-finder   GET /v2/email-finder?domain=&first_name=&last_name=          -> data{email, score, verification: {status}}
email-verifier GET /v2/email-verifier?email=                                 -> data{status: valid|invalid|accept_all|webmail|disposable|unknown, result: deliverable|undeliverable|risky|unknown}
account        GET /v2/account                                               -> free check of the key (no request used).
The key is the one saved in the existing Hunter card. Credits per Hunter's pricing page (domain search and finder 1 per call with results; verifier 0.5-1)."""
from ..providers import HUNTER_URL
from .base import SourceSpec, person, verdict

BASE = "https://api.hunter.io/v2/"


def _status_ok(s):
    return True if s == "valid" else (False if s in ("invalid", "disposable") else None)


def find_people(ctx, domain, company, titles, limit):
    reply = ctx.cached(f"ds:{domain}|{limit}", lambda: [
        e for e in ctx.get(BASE + "domain-search", params={"domain": domain, "api_key": ctx.values.get("api_key", ""), "limit": min(limit, 10), "type": "personal"}).get("data", {}).get("emails") or []
        if isinstance(e, dict)], cost=1)
    words = [t.lower() for t in titles or []]
    out = []
    for e in reply:
        pos = str(e.get("position") or "")
        if words and not any(w in pos.lower() or pos.lower() in w for w in words if pos):
            continue
        out.append(person("hunter", e.get("value"), e.get("phone_number"), " ".join(p for p in (e.get("first_name"), e.get("last_name")) if p),
                          pos, e.get("linkedin"), e.get("confidence"), _status_ok((e.get("verification") or {}).get("status")), "https://hunter.io"))
    return out


def find_email(ctx, first, last, domain, company):
    d = ctx.cached(f"ef:{domain}|{first}|{last}".lower(), lambda: ctx.get(BASE + "email-finder", params={
        "domain": domain, "first_name": first, "last_name": last, "api_key": ctx.values.get("api_key", "")}).get("data") or {}, cost=1)
    return [person("hunter", d["email"], name=f"{first} {last}".strip(), confidence=d.get("score"),
                   verified=_status_ok((d.get("verification") or {}).get("status")), source_url="https://hunter.io")] if d.get("email") else []


def verify(ctx, email):
    d = ctx.cached(f"v:{email}", lambda: ctx.get(BASE + "email-verifier", params={"email": email, "api_key": ctx.values.get("api_key", "")}).get("data") or {}, cost=1)
    status, result = d.get("status"), d.get("result")
    mapped = "valid" if status == "valid" else "invalid" if status in ("invalid", "disposable") or result == "undeliverable" else \
        "risky" if status in ("accept_all", "webmail") or result == "risky" else "unknown"
    return verdict("hunter", mapped, status or result or "", 1)


def test(ctx):
    d = ctx.get(BASE + "account", params={"api_key": ctx.values.get("api_key", "")}).get("data") or {}
    left = (d.get("requests") or {}).get("searches", {})
    return {"ok": True, "message": f"Hunter accepted the key (account check, no credit used). Searches used: {left.get('used', '?')} of {left.get('available', '?')}."}


SPEC = SourceSpec(
    "hunter", "Hunter (people, email finder, verifier)", ("discover_people", "find_email", "verify_email"), "People and email",
    "Public work emails for a company domain, an email finder for a named person, and an email verifier. Uses the key from the Hunter card.",
    "https://hunter.io/api-documentation/v2", "Hunter returns publicly sourced work emails with their source pages. Follow Hunter's terms; B2B work addresses only.",
    ["Use the same Hunter key as in Integrations > Lead data > Hunter.io."], cost=1, cost_note="About 1 credit per call (see Hunter's pricing page); the key check is free.",
    rate_per_min=30, free_note="Hunter has a free plan with a small monthly allowance; check hunter.io.", integration="hunter", legacy=True,
    find_people=find_people, find_email=find_email, verify=verify, test=test, needs_domain=True)
