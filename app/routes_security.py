"""Two-step login and the Security tab.

Public (no token yet; the ticket from /api/login is the key, and the same login limits apply):
  POST /api/login/code            {ticket, code}  the authenticator code (or a recovery code) -> token
  POST /api/login/setup/start     {ticket}        two-step login is required and not set up yet: a new secret
  POST /api/login/setup/confirm   {ticket, code}  switch it on with a first code -> token + recovery codes
The signed-in person's own two-step login: GET /api/me/two-step, POST /api/me/two-step/{start,confirm,off,recovery}.
Owners: GET/PUT /api/security/settings (require for everyone), POST /api/security/two-step/{email}/reset.
Owners and workspace admins: GET /api/security/logins (admins see only their own workspaces' logins).
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, auth, login_log, super_admin, two_step
from .hygiene import normalise_email
from .web_security import client_ip

PUBLIC_PATHS = ("/api/login/code", "/api/login/setup/start", "/api/login/setup/confirm")
TOO_MANY = "Too many wrong attempts. Wait a minute and try again."
TOO_MANY_CODES = f"Too many wrong codes for this login. Wait {login_log.WINDOW_MINUTES} minutes, or use a recovery code later."


class TicketBody(BaseModel):
    ticket: str = Field(default="", max_length=600)


class TicketCodeBody(TicketBody):
    code: str = Field(default="", max_length=40)


class CodeBody(BaseModel):
    code: str = Field(default="", max_length=40)


class OffBody(BaseModel):
    password: str = Field(default="", max_length=auth.MAX_PASSWORD_CHARS)
    code: str = Field(default="", max_length=40)


class SecurityBody(BaseModel):
    require_two_step: bool


def after_password(store, user: dict) -> Optional[dict]:
    """What /api/login answers instead of a token when the login needs a second step, else None."""
    if two_step.is_on(user):
        return {"two_step": "code", "ticket": two_step.make_ticket(store, user, "code")}
    if two_step.required(store) or super_admin.two_step_mandatory(user):       # owners and super admins always
        return {"two_step": "setup", "ticket": two_step.make_ticket(store, user, "setup")}
    return None


def make_router(store, admin_password: str, limiter: auth.LoginLimiter) -> APIRouter:
    router = APIRouter()

    def me(request: Request) -> dict:
        return getattr(request.state, "user", None) or {}

    def own_row(request: Request) -> dict:
        user = me(request)
        row = auth.find_user(store, user.get("email", "")) if str(user.get("sub", "")).startswith("user:") else None
        if not row:
            raise HTTPException(400, "Two-step login is for team logins. The server admin password sign-in is not covered: "
                                     "keep OUTREACH_ADMIN_PASSWORD long and private on the server.")
        return row

    def gate(request: Request) -> str:
        caller = client_ip(request)
        if limiter.locked(caller):
            raise HTTPException(429, TOO_MANY)
        return caller

    def signed_in(user: dict, request: Request, method: str, caller: str) -> dict:
        limiter.reset(caller)
        login_log.record(store, request, "login", user["email"], "success", two_step=True, method=method)
        principal = auth.subject_principal(store, admin_password, "user:" + user["email"])
        return {"token": auth.issue_token(store, principal), "role": principal["role"], "name": principal["name"]}

    def wrong(user: dict, request: Request, caller: str, method: str, message: str):
        limiter.record_failure(caller)
        login_log.record(store, request, "login", user["email"], "failure", "wrong two-step code", two_step=True, method=method)
        raise HTTPException(401, message)

    def ticket_user(body: TicketBody, kind: str, request: Request) -> dict:
        user = two_step.read_ticket(store, body.ticket, kind)
        if not user:
            raise HTTPException(401, "This sign-in has expired. Enter your email and password again.")
        if login_log.wrong_codes(store, user["email"]) >= login_log.FAILS_FOR_ALERT:
            raise HTTPException(429, TOO_MANY_CODES)
        return user

    # ---------- the second step of signing in ----------
    @router.post("/api/login/code")
    def login_code(body: TicketCodeBody, request: Request):
        caller = gate(request)
        user = ticket_user(body, "code", request)
        method = two_step.check(store, user, body.code)
        if not method:
            wrong(user, request, caller, "code", "That code is not right. Type the newest code from your authenticator app.")
        return signed_in(user, request, method, caller)

    @router.post("/api/login/setup/start")
    def login_setup_start(body: TicketBody, request: Request):
        gate(request)
        user = ticket_user(body, "setup", request)
        try:
            return two_step.start(store, user)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/login/setup/confirm")
    def login_setup_confirm(body: TicketCodeBody, request: Request):
        caller = gate(request)
        user = ticket_user(body, "setup", request)
        try:
            codes = two_step.confirm(store, user, body.code)
        except ValueError as exc:
            wrong(user, request, caller, "setup", str(exc))
        audit.record(store, {"name": user.get("name") or user["email"], "email": user["email"], "role": user.get("role")},
                     "user.two_step_on", user["email"], "set up while signing in (required)")
        return {**signed_in(user, request, "setup", caller), "recovery_codes": codes}

    # ---------- the signed-in person's own two-step login ----------
    @router.get("/api/me/two-step")
    def my_status(request: Request):
        user = me(request)
        row = auth.find_user(store, user.get("email", "")) if str(user.get("sub", "")).startswith("user:") else None
        return two_step.status(store, row)

    @router.post("/api/me/two-step/start")
    def my_start(request: Request):
        try:
            return two_step.start(store, own_row(request))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/me/two-step/confirm")
    def my_confirm(body: CodeBody, request: Request):
        caller = gate(request)
        row = own_row(request)
        try:
            codes = two_step.confirm(store, row, body.code)
        except ValueError as exc:
            limiter.record_failure(caller)
            raise HTTPException(400, str(exc)) from None
        limiter.reset(caller)
        audit.record(store, me(request), "user.two_step_on", row["email"], "turned on by the person")
        return {**two_step.status(store, auth.find_user(store, row["email"])), "recovery_codes": codes}

    @router.post("/api/me/two-step/off")
    def my_off(body: OffBody, request: Request):
        caller = gate(request)
        row = own_row(request)
        if two_step.required(store):
            raise HTTPException(400, "The owner requires two-step login for everyone, so it cannot be turned off.")
        if super_admin.two_step_mandatory(row):
            raise HTTPException(400, "Owners and super admins must keep two-step login on.")
        if not auth.check_password(row.get("password_hash", ""), body.password) or not two_step.check(store, row, body.code):
            limiter.record_failure(caller)
            raise HTTPException(400, "Your password or code is not right.")
        limiter.reset(caller)
        two_step.turn_off(store, row["email"])
        audit.record(store, me(request), "user.two_step_off", row["email"], "turned off by the person")
        return two_step.status(store, auth.find_user(store, row["email"]))

    @router.post("/api/me/two-step/recovery")
    def my_recovery(body: CodeBody, request: Request):
        caller = gate(request)
        row = own_row(request)
        if two_step.check(store, row, body.code) != "code":
            limiter.record_failure(caller)
            raise HTTPException(400, "Type the newest code from your authenticator app.")
        limiter.reset(caller)
        codes = two_step.new_recovery_codes(store, auth.find_user(store, row["email"]))
        audit.record(store, me(request), "user.two_step_codes", row["email"], "new recovery codes made")
        return {"recovery_codes": codes}

    # ---------- owners ----------
    @router.get("/api/security/settings")
    def security_settings():
        people = [{"email": u["email"], "name": u.get("name") or u["email"], "role": u.get("role"), "two_step_on": two_step.is_on(u)}
                  for u in auth.users(store)]
        return {"require_two_step": two_step.required(store), "people": people, "admin_password_login": bool(admin_password)}

    @router.put("/api/security/settings")
    def save_security(body: SecurityBody, request: Request):
        if body.require_two_step != two_step.required(store):
            two_step.set_required(store, body.require_two_step)
            audit.record(store, me(request), "security.require_two_step", "everyone", "on" if body.require_two_step else "off")
        return security_settings()

    @router.post("/api/security/two-step/{email}/reset")
    def reset_two_step(email: str, request: Request):
        target = auth.find_user(store, normalise_email(email))
        if not target:
            raise HTTPException(404, "No login with that email.")
        if target.get("role") == "owner" and not auth.is_super_admin(me(request)):
            raise HTTPException(403, "Only the super admin can reset an owner's two-step login.")
        if super_admin.is_super_email(target["email"]):
            if not auth.is_super_admin(me(request)):
                raise HTTPException(403, "Only another super admin can reset a super admin's two-step login.")
            if normalise_email(me(request).get("email")) == target["email"]:
                raise HTTPException(403, "You cannot reset your own two-step login here. Use your super admin recovery codes "
                                         "with a password reset link, or ask another super admin.")
        two_step.turn_off(store, target["email"])
        audit.record(store, me(request), "user.two_step_reset", target["email"], "two-step login reset by an owner")
        if super_admin.two_step_mandatory(target):
            super_admin.owner_two_step_reset(store, me(request), target["email"], "from Setup > Security")
        return {"ok": True}

    # ---------- login history ----------
    @router.get("/api/security/logins")
    def logins(request: Request, person: str = "", result: str = "", event: str = "", date_from: str = "", date_to: str = "",
               limit: int = 200, offset: int = 0):
        viewer = me(request) or {"role": "owner"}
        if viewer.get("role") not in ("owner", "admin"):
            raise HTTPException(403, "Only owners and workspace admins can see login history.")
        return login_log.history(store, viewer, person[:120], result[:20], event[:40], date_from[:10], date_to[:10], limit, offset)

    return router
