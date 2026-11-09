"""Self-service password reset: POST /api/password/forgot (always the same answer), POST /api/password/reset/info
(does this link work, and is a code asked too) and POST /api/password/reset.

A super admin's plain "forgot" sends nothing (same answer); with recovery=true a link is sent that also needs the
authenticator code or a sealed super admin recovery code (app/super_admin.py)."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, login_log, password_reset, super_admin

PUBLIC_PATHS = ("/api/password/forgot", "/api/password/reset", "/api/password/reset/info")


class ForgotBody(BaseModel):
    email: str = Field(default="", max_length=200)
    recovery: bool = False


class InfoBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class ResetBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(max_length=1000)
    code: str = Field(default="", max_length=40)


def make_router(store, base_url_of, sender) -> APIRouter:
    router = APIRouter()

    @router.post("/api/password/forgot")
    def forgot(body: ForgotBody, request: Request):
        result = password_reset.request_reset(store, body.email, base_url_of(), sender, recovery=body.recovery)
        if result.get("status") == "withheld":
            audit.record(store, body.email[:120] or "Unknown", "user.reset_withheld", body.email[:120],
                         "super admin: no email-only reset link; use the recovery reset")
            login_log.record(store, request, "password_reset_request", body.email[:120], "failure",
                             "super admin: plain reset link not sent (recovery reset needed)")
        elif result.get("status"):
            audit.record(store, body.email[:120] or "Unknown", "user.reset_requested", body.email[:120],
                         f"{result['status']}{' (super admin recovery)' if body.recovery else ''}")
            login_log.record(store, request, "password_reset_request", body.email[:120], "success", f"link email: {result['status']}")
        return {"message": result["message"]}

    @router.post("/api/password/reset/info")
    def info(body: InfoBody):
        return password_reset.link_info(store, body.token)

    @router.post("/api/password/reset")
    def reset(body: ResetBody, request: Request):
        try:
            user = password_reset.reset(store, body.token, body.password, code=body.code)
        except password_reset.CodeNeeded as exc:
            raise HTTPException(400, str(exc)) from None
        except ValueError as exc:
            login_log.record(store, request, "password_reset", "(reset link)", "failure", str(exc))
            raise HTTPException(400, str(exc)) from None
        how = {"code": "with the authenticator code", "recovery": "with a two-step recovery code",
               "super_recovery": "with a super admin recovery code (two-step login reset too)"}.get(user.get("second_factor"), "")
        audit.record(store, user["email"], "user.change", user["email"], f"password reset by email link {how}".strip())
        login_log.record(store, request, "password_reset", user["email"], "success", f"new password set with the email link {how}".strip())
        if user.get("second_factor") == "super_recovery":
            super_admin.record(store, user["email"], "super_admin.recovery_used", user["email"], "password and two-step login reset",
                               f"Super admin recovery code used: {user['email']}",
                               f"A super admin recovery code was used with a reset link for {user['email']}: the password and "
                               "two-step login were reset. If this was not them, act now.")
        return {"ok": True, "email": user["email"]}

    return router
