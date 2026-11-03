"""Snov.io API v2 (owner's client id + secret): https://snov.io/api
POST https://api.snov.io/v1/oauth/access_token {grant_type: client_credentials, client_id, client_secret} -> {access_token, expires_in}
Email finder: POST /v2/emails-by-domain-by-name/start {rows: [{first_name, last_name, domain}]} -> {data: {task_hash}}; then
GET /v2/emails-by-domain-by-name/result?task_hash= -> {status: completed|in_progress|not_enough_credits, data: [{people, result: [{email, smtp_status, is_valid_format, is_disposable}]}]}
Verifier: POST /v2/email-verification/start {emails: [..]}; GET /v2/email-verification/result?task_hash= -> data[{email, result: {smtp_status: valid|not_valid|unknown}}].
Results are asynchronous: the adapter polls a few times (a pause between each) and gives up quietly, without charging twice."""
from .base import SourceError, SourceSpec, field, person, verdict

BASE = "https://api.snov.io/"
POLLS = 5


def _token(ctx) -> dict:
    reply = ctx.post(BASE + "v1/oauth/access_token", body={"grant_type": "client_credentials", "client_id": ctx.values.get("client_id", ""),
                                                          "client_secret": ctx.values.get("client_secret", "")})
    if not reply.get("access_token"):
        raise SourceError("Snov.io did not return an access token. Check the client id and secret.")
    return {"Authorization": f"Bearer {reply['access_token']}"}


def _run(ctx, start_path: str, result_path: str, body: dict):
    headers = _token(ctx)
    task = ((ctx.post(BASE + start_path, body=body, headers=headers).get("data")) or {}).get("task_hash")
    if not task:
        return []
    for _ in range(POLLS):
        reply = ctx.get(BASE + result_path, headers=headers, params={"task_hash": task})
        if reply.get("status") == "completed":
            return reply.get("data") or []
        if reply.get("status") == "not_enough_credits":
            raise SourceError("Snov.io says you are out of credits.")
        ctx.sleep(2)
    return []


def find_email(ctx, first, last, domain, company):
    def fetch():
        out = []
        for item in _run(ctx, "v2/emails-by-domain-by-name/start", "v2/emails-by-domain-by-name/result", {"rows": [{"first_name": first, "last_name": last, "domain": domain}]}):
            for r in item.get("result") or []:
                if isinstance(r, dict) and r.get("email") and r.get("is_valid_format", True) and not r.get("is_disposable"):
                    out.append({"email": r["email"], "smtp": r.get("smtp_status")})
        return out
    return [person("snov", r["email"], name=f"{first} {last}".strip(), verified=True if r["smtp"] == "valid" else None, source_url="https://snov.io")
            for r in ctx.cached(f"ef:{domain}|{first}|{last}".lower(), fetch, cost=1)]


def verify(ctx, email):
    def fetch():
        for item in _run(ctx, "v2/email-verification/start", "v2/email-verification/result", {"emails": [email]}):
            if isinstance(item, dict) and item.get("email") == email:
                return (item.get("result") or {}).get("smtp_status") or "unknown"
        return "unknown"
    status = ctx.cached(f"v:{email}", fetch, cost=1)
    return verdict("snov", {"valid": "valid", "not_valid": "invalid"}.get(status, "unknown"), status, 1)


def test(ctx):
    _token(ctx)
    return {"ok": True, "message": "Snov.io accepted the client id and secret (token request, no credit used)."}


SPEC = SourceSpec(
    "snov", "Snov.io", ("find_email", "verify_email"), "People and email",
    "Finds the work email of a named person at a company domain and verifies addresses.", "https://snov.io/api",
    "Official API with your credentials; B2B work emails only. Follow Snov.io's terms.",
    ["Sign up at snov.io (check the plan page for the free credits).", "Open Account settings > API and copy the client ID and client secret.", "Paste both here and save."],
    fields=[field("client_id", "API user ID (client id)", True, True), field("client_secret", "API secret", True, True)],
    cost=1, cost_note="About 1 credit per email found or verified (see Snov.io pricing). The key check is free.", rate_per_min=20,
    free_note="Snov.io has a trial allowance; check their pricing page.", find_email=find_email, verify=verify, test=test, needs_domain=True)
