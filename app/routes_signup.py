"""Public sign-up, Google / Microsoft sign-in, magic links, the pending page and the platform status banner.

Public (no token; each checks its own ticket / link / state, and /api/signup*, /api/oidc/*, /api/magic-link* are rate
limited per address in app/web_security.py):
  GET  /api/signup/config            which sign-in options exist (buttons only for configured providers), terms versions
  GET  /api/signup/form              a form ticket (minimum fill time)
  POST /api/signup                   ask for an account (always the same answer) -> verification email
  POST /api/signup/verify            {token} -> the pending account + its admin login, signed in
  POST /api/signup/oidc              {ticket, company, phone, accept}: sign-up after Google / Microsoft (email verified)
  GET  /api/oidc/{provider}/start    ?intent=login|signup -> {url, state}
  POST /api/oidc/callback            {code, state} -> token | two-step ticket | {signup: {ticket, email, name}}
  POST /api/magic-link               {email} (always the same answer)
  POST /api/magic-link/verify        {token} -> token | two-step ticket
Signed in: GET /api/signup/pending (the waiting page), GET /api/platform/status (banners: stop-all, view-as, pending).
"""
import os
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import accounts, auth, login_log, magic_link, oidc, platform_controls, signup, super_admin, two_step, view_as
from .web_security import client_ip

PUBLIC_PATHS = {("GET", "/api/signup/config"), ("GET", "/api/signup/form"), ("POST", "/api/signup"), ("POST", "/api/signup/verify"),
                ("POST", "/api/signup/oidc"), ("POST", "/api/oidc/callback"), ("POST", "/api/magic-link"), ("POST", "/api/magic-link/verify")}
OIDC_START = re.compile(r"^/api/oidc/(google|microsoft)/start$")


def is_public(method: str, path: str) -> bool:
    return (method, path) in PUBLIC_PATHS or (method == "GET" and bool(OIDC_START.match(path)))


class SignupBody(BaseModel):
    company: str = Field(default="", max_length=200)
    name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=320)
    password: str = Field(default="", max_length=auth.MAX_PASSWORD_CHARS)
    phone: str = Field(default="", max_length=40)
    accept: bool = False
    ticket: str = Field(default="", max_length=400)
    website: str = Field(default="", max_length=200)          # the honeypot
    challenge_token: str = Field(default="", max_length=400)
    challenge_answer: str = Field(default="", max_length=10)


class TokenBody(BaseModel):
    token: str = Field(min_length=10, max_length=400)


class OidcSignupBody(BaseModel):
    ticket: str = Field(min_length=10, max_length=2000)
    company: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=40)
    accept: bool = False


class CallbackBody(BaseModel):
    code: str = Field(min_length=1, max_length=4000)
    state: str = Field(min_length=10, max_length=200)


class EmailBody(BaseModel):
    email: str = Field(default="", max_length=320)


def make_router(store, admin_password: str, base_url_of, sender) -> APIRouter:
    router = APIRouter()

    def signed_in(user: dict, request: Request, method: str, force_two_step: bool = False) -> dict:
        """Token, or the two-step step for logins that need it (owners / super admins always; admins after OIDC)."""
        principal = auth.subject_principal(store, admin_password, "user:" + user["email"])
        if principal is None:
            login_log.record(store, request, "login", user["email"], "failure", f"{method}: account not active", method=method)
            raise HTTPException(403, "Your company's account is suspended. Ask the platform admin.")
        from .routes_security import after_password
        second = after_password(store, user)
        if not second and force_two_step and not principal.get("account_pending"):
            second = {"two_step": "setup", "ticket": two_step.make_ticket(store, user, "setup")}
        if second:
            login_log.record(store, request, "login", user["email"], "pending", f"{method}: waiting for the two-step code", method=method)
            return {**second, "name": principal["name"]}
        login_log.record(store, request, "login", user["email"], "success", method=method)
        return {"token": auth.issue_token(store, principal), "role": principal["role"], "name": principal["name"]}

    @router.get("/api/signup/config")
    def config():
        return {"providers": oidc.public_list(), "magic_link": True, "signup": True,
                "terms": {"terms": signup.TERMS_VERSION, "privacy": signup.PRIVACY_VERSION, "dpa": signup.DPA_VERSION}}

    @router.get("/api/signup/form")
    def form():
        return {"ticket": signup.form_ticket(store), "min_seconds": signup.MIN_FILL_SECONDS}

    @router.post("/api/signup")
    def create(body: SignupBody, request: Request):
        ip = client_ip(request)
        try:
            result = signup.request_signup(store, body.model_dump(), ip, base_url_of(), sender)
        except signup.Challenge as exc:
            raise HTTPException(400, {"message": str(exc), "challenge": {"question": exc.question, "token": exc.token}}) from None
        except OverflowError as exc:
            raise HTTPException(429, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        login_log.record(store, request, "signup_request", body.email[:120], "success" if result["status"] != "dropped" else "failure",
                         result["status"], method="signup")
        return {"message": result["message"]}

    @router.post("/api/signup/verify")
    def verify(body: TokenBody, request: Request):
        try:
            made = signup.verify(store, body.token)
        except ValueError as exc:
            login_log.record(store, request, "signup_verify", "(link)", "failure", str(exc), method="signup")
            raise HTTPException(400, str(exc)) from None
        return {**signed_in(made["user"], request, "signup"), "account": accounts.public(made["account"])}

    @router.post("/api/signup/oidc")
    def oidc_signup(body: OidcSignupBody, request: Request):
        claims = signup.unsign(store, "oidc-signup", body.ticket)
        if not claims:
            raise HTTPException(400, "This sign-up has expired. Start again with Google or Microsoft.")
        try:
            clean = signup.clean_form({"company": body.company, "name": claims.get("name") or claims["email"], "email": claims["email"],
                                       "phone": body.phone, "accept": body.accept})
            consent = {"terms": signup.TERMS_VERSION, "privacy": signup.PRIVACY_VERSION, "dpa": signup.DPA_VERSION,
                       "at": auth.now_iso(), "ip": client_ip(request)}
            made = signup.create_pending(store, clean["company"], clean["name"], clean["email"], clean["phone"], consent,
                                         via=claims["p"], oidc={claims["p"]: claims["s"]})
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {**signed_in(made["user"], request, claims["p"]), "account": accounts.public(made["account"])}

    @router.get("/api/oidc/{provider}/start")
    def oidc_start(provider: str, intent: str = "login"):
        try:
            return oidc.start(store, provider, intent, base_url_of())
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    @router.post("/api/oidc/callback")
    def oidc_callback(body: CallbackBody, request: Request):
        try:
            who = oidc.finish(store, body.code, body.state, base_url_of())
        except ValueError as exc:
            login_log.record(store, request, "login", "(oidc)", "failure", str(exc), method="oidc")
            raise HTTPException(400, str(exc)) from None
        user = auth.find_user(store, who["email"])
        if not user:
            if who["intent"] != "signup":
                login_log.record(store, request, "login", who["email"], "failure", f"{who['provider']}: no login with this email", method=who["provider"])
                raise HTTPException(404, "No login uses this email address. Create an account first, or ask your admin to add you.")
            ticket = signup.sign(store, "oidc-signup", {"email": who["email"], "name": who["name"], "p": who["provider"], "s": who["sub"],
                                                         "exp": int(auth._now()) + 1800})
            return {"signup": {"ticket": ticket, "email": who["email"], "name": who["name"], "provider": who["provider"]}}
        linked = (user.get("oidc") or {}).get(who["provider"])
        if linked and linked != who["sub"]:
            login_log.record(store, request, "login", user["email"], "failure", f"{who['provider']}: another account of the provider", method=who["provider"])
            raise HTTPException(403, "This login is linked to a different account at the provider. Sign in with your password.")
        if not linked:
            store.update("settings", lambda s: {**s, "users": [{**u, "oidc": {**(u.get("oidc") or {}), who["provider"]: who["sub"]}}
                                                               if isinstance(u, dict) and u.get("email") == user["email"] else u
                                                               for u in s.get("users") or []]})
        strong = user.get("role") in ("owner", "admin") or super_admin.is_super_email(user["email"])
        return signed_in(auth.find_user(store, user["email"]), request, who["provider"], force_two_step=strong)

    @router.post("/api/magic-link")
    def magic_request(body: EmailBody, request: Request):
        result = magic_link.request_link(store, body.email, base_url_of(), sender)
        if result["status"] not in ("none",):
            login_log.record(store, request, "magic_link_request", body.email[:120], "success" if result["status"] not in ("withheld", "limited") else "failure",
                             result["status"], method="magic_link")
        return {"message": result["message"]}

    @router.post("/api/magic-link/verify")
    def magic_verify(body: TokenBody, request: Request):
        try:
            user = magic_link.use_link(store, body.token)
        except ValueError as exc:
            login_log.record(store, request, "login", "(magic link)", "failure", str(exc), method="magic_link")
            raise HTTPException(400, str(exc)) from None
        return signed_in(user, request, "magic_link")

    # ---------- signed in ----------
    @router.get("/api/signup/pending")
    def pending(request: Request):
        user = getattr(request.state, "user", None) or {}
        if not user.get("account_pending"):
            return {"pending": False}
        return {"pending": True, **signup.pending_info(store, user)}

    @router.get("/api/platform/status")
    def platform_status(request: Request):
        user = getattr(request.state, "user", None) or {}
        acct = accounts.find(store, accounts.of_user(user)) or {}
        return {"stop_all_sending": platform_controls.public_stop(store), "view_as": view_as.public(user),
                "account_status": "pending" if user.get("account_pending") else acct.get("status", "active"),
                "super_admin": auth.is_super_admin(user) and not user.get("view_as"),
                "base_url": os.environ.get("OUTREACH_BASE_URL", "")}

    return router
