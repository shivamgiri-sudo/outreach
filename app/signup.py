"""Self sign-up: a company asks for an account, confirms its email, and waits for a super admin's approval.

  1. GET  /api/signup/form   a form ticket (signed, holds the time the form was shown).
  2. POST /api/signup         company, name, work email, password, phone (optional), accept (Terms + Privacy + Data
                              Processing Agreement), the ticket, a honeypot field. The answer is always the same
                              (GENERIC), whether or not the address already has a login, so nobody learns who is a user.
                              A verification link (single use, 24 h) goes to the address; an address that already has a
                              login gets a "you already have a login" email instead.
  3. POST /api/signup/verify  {token}: the account is made with status "pending", its first login (role admin) and one
                              workspace waiting for approval ("requested"); the super admins get a bell + email alert.
Bot protection without any third-party service: the honeypot (filled = silently dropped), a minimum fill time,
per-address and per-email-domain limits (plus the per-IP rate limit middleware on /api/signup/), and a small sum to
solve only after a suspicious signal (too fast, or a second sign-up from the same address / domain within the hour).
Free-mail addresses may sign up; the account is flagged "free email" for the super admin. Nothing of the request is
kept in plain text except what the account needs: the password is hashed at once, the link is kept as a hash.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import accounts, auth
from .channels.security import server_secret
from .hygiene import FREE_MAIL_DOMAINS, normalise_email
from .lead_model import now_iso

GENERIC = "Thanks. If the address can receive email, a confirmation link is on its way. It works for 24 hours."
LINK_HOURS = 24
MIN_FILL_SECONDS = 4
FORM_HOURS = 2
PER_IP_HOUR = 5
PER_DOMAIN_HOUR = 5
SUSPICIOUS_AFTER = 1          # a second sign-up from the same address or domain within the hour asks for the sum
MAX_ROWS = 500
TERMS_VERSION = os.environ.get("OUTREACH_TERMS_VERSION", "2026-11-01")
PRIVACY_VERSION = os.environ.get("OUTREACH_PRIVACY_VERSION", "2026-11-01")
DPA_VERSION = os.environ.get("OUTREACH_DPA_VERSION", "2026-11-01")
KEY = "signup_requests"


class Challenge(ValueError):
    """A suspicious signal: the form must be sent again with the answer to `question` (and `token`)."""

    def __init__(self, question: str, token: str, message: str = "Please answer the small sum below so we know you are a person."):
        super().__init__(message)
        self.question, self.token = question, token


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sig(store, kind: str, body: str) -> str:
    return _b64(hmac.new(server_secret(store).encode(), f"{kind}.{body}".encode(), hashlib.sha256).digest())[:32]


def sign(store, kind: str, claims: dict) -> str:
    body = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_sig(store, kind, body)}"


def unsign(store, kind: str, token: str, now: Optional[float] = None) -> Optional[dict]:
    try:
        body, sig = str(token or "").split(".")
        if not hmac.compare_digest(sig.encode(), _sig(store, kind, body).encode()):
            return None
        claims = json.loads(_unb64(body))
        if int(claims.get("exp", 0)) <= (time.time() if now is None else now):
            return None
        return claims
    except (ValueError, TypeError):
        return None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def domain_of(email: str) -> str:
    return (email or "").rsplit("@", 1)[-1].lower()


def free_email(email: str) -> bool:
    return domain_of(email) in FREE_MAIL_DOMAINS


# ---------------------------------------------------------------- the form ticket and the sum
def form_ticket(store, now: Optional[float] = None) -> str:
    moment = int(time.time() if now is None else now)
    return sign(store, "signup-form", {"t": moment, "exp": moment + FORM_HOURS * 3600, "n": secrets.token_hex(4)})


def new_challenge(store, now: Optional[float] = None) -> tuple:
    a, b = secrets.randbelow(9) + 2, secrets.randbelow(9) + 2
    moment = int(time.time() if now is None else now)
    token = sign(store, "signup-sum", {"a": _hash(f"{a + b}:{moment}"), "t": moment, "exp": moment + 1800})
    return f"What is {a} + {b}?", token


def challenge_ok(store, token: str, answer: str) -> bool:
    claims = unsign(store, "signup-sum", token)
    text = re.sub(r"\D", "", str(answer or ""))[:3]
    return bool(claims and text and hmac.compare_digest(claims["a"], _hash(f"{int(text)}:{claims['t']}")))


# ---------------------------------------------------------------- requests
def _live(settings: dict, now: datetime) -> list:
    return [r for r in (settings.get(KEY) or []) if isinstance(r, dict) and r.get("expires_at", "") > now.isoformat()]


def _rows(store, now: datetime) -> list:
    return _live(store.read("settings"), now)


def recent_count(store, field: str, value: str, now: datetime) -> int:
    hour_ago = (now - timedelta(hours=1)).isoformat()
    return sum(1 for r in _rows(store, now) if r.get(field) == value and r.get("at", "") > hour_ago)


def clean_form(form: dict) -> dict:
    company = " ".join(str(form.get("company") or "").split())[:80]
    name = " ".join(str(form.get("name") or "").split())[:80]
    email = normalise_email(str(form.get("email") or "")[:200])
    phone = re.sub(r"[^\d+ ()-]", "", str(form.get("phone") or ""))[:30].strip()
    if not company:
        raise ValueError("Enter your company name.")
    if not name:
        raise ValueError("Enter your name.")
    if not email:
        raise ValueError("Enter a valid work email address.")
    if not form.get("accept"):
        raise ValueError("Please accept the Terms, the Privacy Policy and the Data Processing Agreement.")
    return {"company": company, "name": name, "email": email, "phone": phone}


def request_signup(store, form: dict, ip: str, base_url: str, send, now: Optional[datetime] = None) -> dict:
    """Validate, check for bots, keep the request (hashes only) and email the link. Returns {message, status}.
    Raises ValueError (plain message), Challenge (ask the sum) or OverflowError (too many: 429)."""
    now = now or datetime.now(timezone.utc)
    if str(form.get("website") or "").strip():           # the honeypot: people never see this field
        return {"message": GENERIC, "status": "dropped"}
    clean = clean_form(form)
    problem = auth.password_problem(str(form.get("password") or ""))
    if problem:
        raise ValueError(problem)
    from .email_domain import problem as domain_problem
    bad = domain_problem(clean["email"])
    if bad:
        raise ValueError(f"We cannot send to this address: {bad}.")
    domain = domain_of(clean["email"])
    company_domain = not free_email(clean["email"])        # gmail.com etc. are shared by everyone: the address limit covers them
    if recent_count(store, "ip", ip, now) >= PER_IP_HOUR or (company_domain and recent_count(store, "domain", domain, now) >= PER_DOMAIN_HOUR):
        raise OverflowError("Too many sign-ups from here in the last hour. Try again later.")
    ticket = unsign(store, "signup-form", form.get("ticket") or "", now.timestamp())
    suspicious = (not ticket or now.timestamp() - int(ticket["t"]) < MIN_FILL_SECONDS
                  or recent_count(store, "ip", ip, now) >= SUSPICIOUS_AFTER
                  or (company_domain and recent_count(store, "domain", domain, now) >= SUSPICIOUS_AFTER))
    if suspicious and not challenge_ok(store, form.get("challenge_token") or "", form.get("challenge_answer") or ""):
        question, token = new_challenge(store, now.timestamp())
        raise Challenge(question, token)
    token = secrets.token_urlsafe(32)
    signed = f"{token}.{_sig(store, 'signup-link', token)}"
    existing = auth.find_user(store, clean["email"])
    row = {"hash": _hash(token), "email": clean["email"], "domain": domain, "ip": ip, "at": now.isoformat(timespec="seconds"),
           "expires_at": (now + timedelta(hours=LINK_HOURS)).isoformat(timespec="seconds"), "used": False,
           "company": clean["company"], "name": clean["name"], "phone": clean["phone"], "existing": bool(existing),
           "password_hash": "" if existing else auth.hash_password(str(form["password"])),
           "consent": {"terms": TERMS_VERSION, "privacy": PRIVACY_VERSION, "dpa": DPA_VERSION, "at": now.isoformat(timespec="seconds"), "ip": ip}}
    store.update("settings", lambda s: {**s, KEY: [*_live(s, now), row][-MAX_ROWS:]})
    from .platform_controls import send_mail
    if existing:
        send_mail(store, send, clean["email"], "You already have a login",
                  f"Hello,\n\nSomeone (hopefully you) asked to create an account with this address, but it already has a login.\n\n"
                  f"Sign in here: {base_url.rstrip('/')}/\nForgot the password? Use \"Forgot password?\" on the sign-in page.\n\n"
                  "If this was not you, ignore this email.", "signup")
        return {"message": GENERIC, "status": "existing"}
    link = f"{base_url.rstrip('/')}/#verify?token={signed}"
    send_mail(store, send, clean["email"], "Confirm your email to create your account",
              f"Hello {clean['name']},\n\nConfirm your email to create the account for {clean['company']}:\n{link}\n\n"
              f"The link works for {LINK_HOURS} hours and only once. After that, our team reviews new accounts "
              "(usually within one working day) and emails you when yours is approved.\n\n"
              "If you did not ask for this, ignore this email: nothing is created.", "signup", has_link=True)
    return {"message": GENERIC, "status": "sent"}


def _unique_name(store, company: str) -> str:
    names = {a["name"].lower() for a in accounts.all_accounts(store)}
    if company.lower() not in names:
        return company
    n = 2
    while f"{company} ({n})".lower() in names:
        n += 1
    return f"{company} ({n})"[:80]


def verify(store, token: str, now: Optional[datetime] = None) -> dict:
    """Use a verification link: makes the pending account, its admin login and its requested workspace.
    Returns {account, user}. Raises ValueError when the link is wrong, used or expired."""
    now = now or datetime.now(timezone.utc)
    raw, _, sig = str(token or "").rpartition(".")
    if not raw or not hmac.compare_digest(sig.encode(), _sig(store, "signup-link", raw).encode()):
        raise ValueError("This link is not valid. Sign up again to get a new one.")
    h = _hash(raw)
    row = next((r for r in _rows(store, now) if hmac.compare_digest(r.get("hash", ""), h)), None)
    if not row or row.get("used") or row.get("existing") or auth.find_user(store, row["email"]):
        raise ValueError("This link has expired or was already used. Sign in, or sign up again.")
    store.update("settings", lambda s: {**s, KEY: [{**r, "used": True, "password_hash": ""} if r.get("hash") == h else r
                                                   for r in s.get(KEY) or []]})
    return create_pending(store, row["company"], row["name"], row["email"], row.get("phone", ""), row["consent"],
                          password_hash=row["password_hash"], via="email")


def create_pending(store, company: str, name: str, email: str, phone: str, consent: dict, password_hash: str = "",
                   via: str = "email", oidc: Optional[dict] = None) -> dict:
    """The pending account, its first login (admin) and one workspace waiting for approval; alerts the super admins."""
    from . import workspaces as ws
    if auth.find_user(store, email):
        raise ValueError("This address already has a login. Sign in instead.")
    row = accounts.create(store, _unique_name(store, company), f"sign-up: {email}", status="pending",
                          notes=f"Self sign-up ({via})")
    flags = ["free email"] if free_email(email) else []
    extra = {"signup": {"email": email, "name": name, "phone": phone, "via": via, "at": now_iso(), "consent": consent},
             "flags": flags}
    store.update("settings", lambda s: {**s, "accounts": [{**a, **extra} if isinstance(a, dict) and a.get("id") == row["id"] else a
                                                          for a in s.get("accounts") or []]})
    space = ws.create(store, f"{row['name']} workspace"[:60], email, row["id"], "requested", email)
    user = {"email": email, "name": name, "role": "admin", "password_hash": password_hash, "created_at": now_iso(),
            "account_id": row["id"], "workspaces": [space["id"]], "phone": phone, "consent": consent,
            **({"oidc": oidc} if oidc else {})}
    store.update("settings", lambda s: {**s, "users": [*(s.get("users") or []), user]})
    from . import audit, super_admin
    audit.record(store, {"name": name, "email": email, "role": "admin"}, "signup.verified", row["name"],
                 f"id {row['id']}; {email}; via {via}" + (f"; flags: {', '.join(flags)}" if flags else ""),
                 extra={"account_id": row["id"]})
    from .growth_core import public_base_url
    base = public_base_url(store, os.environ.get("OUTREACH_BASE_URL", "http://localhost:8000"))
    super_admin.alert(store, f"New sign-up: {row['name']}",
                      f"{name} <{email}> signed up for {row['name']} ({'free email address' if flags else 'company address'}"
                      f"{', phone ' + phone if phone else ''}). It waits for approval: {base}/#setup/accounts "
                      "(sign in as a super admin to approve, reject or ask for details).")
    try:
        from . import abuse_alerts
        abuse_alerts.check_signups(store)           # a surge of sign-ups alerts the super admins
    except Exception:
        import logging
        logging.getLogger("outreach.signup").exception("sign-up surge check failed")
    return {"account": accounts.find(store, row["id"]), "user": auth.find_user(store, email)}


def pending_info(store, user: dict) -> dict:
    """What the "waiting for approval" page shows a pending account's login."""
    row = accounts.find(store, accounts.of_user(user)) or {}
    asked = [a for a in row.get("asked") or [] if isinstance(a, dict)]
    return {"account": row.get("name", ""), "status": row.get("status", ""), "submitted_at": (row.get("signup") or {}).get("at") or row.get("created_at"),
            "email": user.get("email"), "questions": asked[-5:],
            "steps": [{"label": "Email confirmed", "done": True},
                      {"label": "Platform team reviews your company", "done": row.get("status") == "active"},
                      {"label": "Your workspace is opened and you can import leads and send", "done": row.get("status") == "active"}]}
