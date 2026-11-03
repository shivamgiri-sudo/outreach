"""Paid email verifiers behind the same `verify(ctx, email)` interface. Each maps its own answer to valid / invalid / risky / unknown.
ZeroBounce  GET https://api.zerobounce.net/v2/validate?api_key=&email=  -> {status: valid|invalid|catch-all|unknown|spamtrap|abuse|do_not_mail}; GET /v2/getcredits is free   https://www.zerobounce.net/docs/
NeverBounce GET https://api.neverbounce.com/v4/single/check?key=&email= -> {status: success, result: valid|invalid|disposable|catchall|unknown}; GET /v4/account/info is free   https://developers.neverbounce.com/
Kickbox     GET https://api.kickbox.com/v2/verify?email=&apikey= -> {result: deliverable|undeliverable|risky|unknown, reason}; keys that start with test_ are the free sandbox   https://docs.kickbox.com/
MillionVerifier GET https://api.millionverifier.com/api/v3/?api=&email= -> {result: ok|catch_all|unknown|disposable|invalid|error}; GET /api/v3/credits is free   https://developer.millionverifier.com/
Bouncer     GET https://api.usebouncer.com/v1.1/email/verify?email=  header x-api-key -> {status: deliverable|risky|undeliverable|unknown, reason}; GET /v1.1/credits is free   https://docs.usebouncer.com/
(Hunter's verifier lives in hunter_src.py.)"""
from .. import campaign_sources as old
from .base import SourceSpec, field, verdict

KEY = [field("api_key", "API key", True, True)]


# ---------- ZeroBounce / NeverBounce: reuse the existing, tested calls ----------
def _legacy_verify(provider: str):
    def run(ctx, email):
        key = ctx.values.get("api_key", "")
        status = ctx.cached(f"v:{email}", lambda: old.verify_email(email, {"provider": provider, "api_key": key}, lambda url, headers: ctx.get(url, headers)), cost=1)
        return verdict(provider, status, "", 1)
    return run


def _zb_test(ctx):
    reply = ctx.get("https://api.zerobounce.net/v2/getcredits", params={"api_key": ctx.values.get("api_key", "")})
    if str(reply.get("Credits", "")) == "-1":
        return {"ok": False, "message": "ZeroBounce did not accept the key."}
    return {"ok": True, "message": f"ZeroBounce accepted the key (credit check, free). Credits: {reply.get('Credits', '?')}."}


def _nb_test(ctx):
    reply = ctx.get("https://api.neverbounce.com/v4/account/info", params={"key": ctx.values.get("api_key", "")})
    if reply.get("status") != "success":
        return {"ok": False, "message": f"NeverBounce: {str(reply.get('message') or reply.get('status'))[:100]}"}
    return {"ok": True, "message": "NeverBounce accepted the key (account info, free)."}


# ---------- Kickbox ----------
def _kickbox(ctx, email):
    def fetch():
        r = ctx.get("https://api.kickbox.com/v2/verify", params={"email": email, "apikey": ctx.values.get("api_key", "")})
        if r.get("success") is False:
            raise old.KeyRejected(f"Kickbox: {str(r.get('message') or 'request refused')[:100]}")
        return {"result": r.get("result"), "reason": r.get("reason", "")}
    r = ctx.cached(f"v:{email}", fetch, cost=1)
    status = {"deliverable": "valid", "undeliverable": "invalid", "risky": "risky"}.get(r["result"], "unknown")
    return verdict("kickbox", status, r.get("reason"), 1)


def _kickbox_test(ctx):
    if str(ctx.values.get("api_key", "")).startswith("test_"):
        return {"ok": True, "message": "Sandbox key (test_...): Kickbox verifications with it are free and return sample answers."}
    return {"ok": False, "message": "Kickbox has no free key check. The key is saved but not verified: the first real verification (1 credit) confirms it."}


# ---------- MillionVerifier ----------
def _million(ctx, email):
    r = ctx.cached(f"v:{email}", lambda: {k: v for k, v in ctx.get("https://api.millionverifier.com/api/v3/", params={
        "api": ctx.values.get("api_key", ""), "email": email, "timeout": 20}).items() if k in ("result", "subresult", "error")}, cost=1)
    if r.get("error"):
        raise old.KeyRejected(f"MillionVerifier: {str(r['error'])[:100]}")
    status = {"ok": "valid", "catch_all": "risky", "disposable": "invalid", "invalid": "invalid"}.get(r.get("result"), "unknown")
    return verdict("millionverifier", status, r.get("subresult") or r.get("result"), 1)


def _million_test(ctx):
    r = ctx.get("https://api.millionverifier.com/api/v3/credits", params={"api": ctx.values.get("api_key", "")})
    if r.get("error"):
        return {"ok": False, "message": f"MillionVerifier: {str(r['error'])[:100]}"}
    return {"ok": True, "message": f"MillionVerifier accepted the key (credit check, free). Credits: {r.get('credits', '?')}."}


# ---------- Bouncer ----------
def _bouncer(ctx, email):
    r = ctx.cached(f"v:{email}", lambda: {k: v for k, v in ctx.get("https://api.usebouncer.com/v1.1/email/verify", headers={"x-api-key": ctx.values.get("api_key", "")},
                                                                 params={"email": email, "timeout": 10}).items() if k in ("status", "reason")}, cost=1)
    status = {"deliverable": "valid", "undeliverable": "invalid", "risky": "risky"}.get(r.get("status"), "unknown")
    return verdict("bouncer", status, r.get("reason"), 1)


def _bouncer_test(ctx):
    r = ctx.get("https://api.usebouncer.com/v1.1/credits", headers={"x-api-key": ctx.values.get("api_key", "")})
    return {"ok": True, "message": f"Bouncer accepted the key (credit check, free). Credits: {r.get('credits', '?')}."}


def _spec(sid, title, docs, verify, test, steps, free_note, terms, legacy=False):
    return SourceSpec(sid, title, ("verify_email",), "Email verification", f"Checks that an address can receive mail before you send (uses your {title} account).",
                      docs, terms, steps, fields=[] if legacy else KEY, cost=1, cost_note="1 credit per address (see their pricing page).", rate_per_min=60,
                      free_note=free_note, integration="email_verifier" if legacy else sid, legacy=legacy, verify=verify, test=test,
                      honest_status="existing call (fake-tested)" if legacy else "coded from documentation; not run against the live service")


SPECS = [
    _spec("zerobounce", "ZeroBounce", "https://www.zerobounce.net/docs/", _legacy_verify("zerobounce"), _zb_test,
          ["Use the existing 'Email verifier' card: choose provider zerobounce and paste your key."], "ZeroBounce lists a small free allowance on its site.",
          "Official API with your key; follow ZeroBounce's terms.", legacy=True),
    _spec("neverbounce", "NeverBounce", "https://developers.neverbounce.com/", _legacy_verify("neverbounce"), _nb_test,
          ["Use the existing 'Email verifier' card: choose provider neverbounce and paste your key."], "NeverBounce lists a free trial on its site.",
          "Official API with your key; follow NeverBounce's terms.", legacy=True),
    _spec("kickbox", "Kickbox", "https://docs.kickbox.com/", _kickbox, _kickbox_test,
          ["Sign up at kickbox.com (see their site for the free credits).", "Create an API key under API Settings. Keys starting with test_ are a free sandbox.", "Paste it here and save."],
          "Kickbox gives a sandbox and a free trial; see their pricing page.", "Official API with your key; follow Kickbox's terms."),
    _spec("millionverifier", "MillionVerifier", "https://developer.millionverifier.com/", _million, _million_test,
          ["Sign up at millionverifier.com (see their site for free credits).", "Open API and copy your key.", "Paste it here and save."],
          "MillionVerifier lists a free trial; check their site.", "Official API with your key; follow MillionVerifier's terms."),
    _spec("bouncer", "Bouncer", "https://docs.usebouncer.com/", _bouncer, _bouncer_test,
          ["Sign up at usebouncer.com (see their site for the free credits).", "Open API in your account and create a key.", "Paste it here and save."],
          "Bouncer lists free credits on signup; check their site.", "Official API with your key; follow Bouncer's terms."),
]
