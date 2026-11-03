"""Chrome assistant API: pairing tokens (web app, signed-in people) and the few routes the extension itself may call."""
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import extension_api as ext, linkedin_seq as li


class PairBody(BaseModel):
    label: str = Field(default="", max_length=60)


class SentBody(BaseModel):
    step: int = Field(ge=1, le=7)


class ReplyBody(BaseModel):
    text: str = Field(default="", max_length=2000)


def make_router(store) -> APIRouter:
    router = APIRouter()

    def person(request: Request) -> dict:
        from .routes_auth import LOCAL_OWNER
        user = getattr(request.state, "user", None) or LOCAL_OWNER
        if user.get("kind") == "extension":
            raise HTTPException(403, "A pairing token cannot manage pairing tokens.")
        return user

    def lead_or_404(lead_id: str) -> dict:
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None:
            raise HTTPException(404, "Lead not found")
        return lead

    # ----- pairing (web app) -----
    @router.post("/api/extension/pair")
    def pair(body: PairBody, request: Request):
        try:
            raw, row = ext.create_token(store, person(request), body.label)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {**row, "token": raw, "note": "Copy this now. It is shown only once."}

    @router.get("/api/extension/tokens")
    def list_tokens(request: Request):
        user = person(request)
        return [ext.public_token(t) for t in ext.tokens(store) if user.get("role") == "owner" or t.get("sub") == user.get("sub")]

    @router.delete("/api/extension/tokens/{token_id}")
    def revoke(token_id: str, request: Request):
        if not ext.revoke_token(store, token_id, person(request)):
            raise HTTPException(404, "No such pairing token.")
        return {"ok": True}

    # ----- the extension's own routes -----
    @router.get("/api/extension/match")
    def match(profile_url: str = Query("", max_length=2000), name: str = Query("", max_length=200), company: str = Query("", max_length=200)):
        lead = ext.find_lead(store, profile_url, name, company)
        return ext.describe(store, lead) if lead else ext.no_match()

    @router.get("/api/extension/queue")
    def queue():
        return ext.queue(store)

    def refuse(info: dict, why: str = ""):
        raise HTTPException(409, why or info["reason"] or "That is not possible right now.")

    @router.post("/api/extension/leads/{lead_id}/sent")
    def sent(lead_id: str, body: SentBody):
        info = ext.describe(store, lead_or_404(lead_id))
        if not info["can_send"]:
            refuse(info)
        if info["message"]["step"] != body.step:
            refuse(info, f"The step to send now is {info['message']['step']}, not {body.step}.")
        li.mark_sent(store, lead_id, body.step)
        return {"ok": True, "match": ext.describe(store, lead_or_404(lead_id))}

    @router.post("/api/extension/leads/{lead_id}/accepted")
    def accepted(lead_id: str):
        lead = lead_or_404(lead_id)
        if (lead.get("linkedin_seq") or {}).get("status") != "waiting_for_acceptance":
            refuse(ext.describe(store, lead), "This person is not waiting to accept a connection request.")
        li.mark_accepted(store, lead_id)
        return {"ok": True, "match": ext.describe(store, lead_or_404(lead_id))}

    @router.post("/api/extension/leads/{lead_id}/replied")
    def replied(lead_id: str, body: ReplyBody):
        lead = lead_or_404(lead_id)
        if not lead.get("linkedin_seq"):
            refuse(ext.describe(store, lead), "This person is not on the LinkedIn sequence.")
        li.mark_replied(store, lead_id, body.text.strip())
        return {"ok": True, "match": ext.describe(store, lead_or_404(lead_id))}

    return router
