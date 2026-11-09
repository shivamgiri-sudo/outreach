"""Self-service password reset: POST /api/password/forgot (always the same answer) and POST /api/password/reset."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, login_log, password_reset

PUBLIC_PATHS = ("/api/password/forgot", "/api/password/reset")


class ForgotBody(BaseModel):
    email: str = Field(default="", max_length=200)


class ResetBody(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(max_length=1000)


def make_router(store, base_url_of, sender) -> APIRouter:
    router = APIRouter()

    @router.post("/api/password/forgot")
    def forgot(body: ForgotBody, request: Request):
        result = password_reset.request_reset(store, body.email, base_url_of(), sender)
        if result.get("status"):
            audit.record(store, body.email[:120] or "Unknown", "user.reset_requested", body.email[:120], result["status"])
            login_log.record(store, request, "password_reset_request", body.email[:120], "success", f"link email: {result['status']}")
        return {"message": result["message"]}

    @router.post("/api/password/reset")
    def reset(body: ResetBody, request: Request):
        try:
            user = password_reset.reset(store, body.token, body.password)
        except ValueError as exc:
            login_log.record(store, request, "password_reset", "(reset link)", "failure", str(exc))
            raise HTTPException(400, str(exc)) from None
        audit.record(store, user["email"], "user.change", user["email"], "password reset by email link")
        login_log.record(store, request, "password_reset", user["email"], "success", "new password set with the email link")
        return {"ok": True, "email": user["email"]}

    return router
