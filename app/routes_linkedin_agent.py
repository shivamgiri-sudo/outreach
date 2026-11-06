"""LinkedIn agent: the portal switch and score (owner), and the calls the agent program makes with its pairing token."""
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import linkedin_agent as agent


class ConfigBody(BaseModel):
    enabled: Optional[bool] = None
    weekly_connections: Optional[int] = None
    daily_messages: Optional[int] = None
    start_hour: Optional[int] = None
    end_hour: Optional[int] = None
    weekdays_only: Optional[bool] = None
    gap_minutes: Optional[int] = None
    check_replies: Optional[bool] = None
    resume_account: Optional[str] = None
    merge_from: Optional[str] = None
    merge_into: Optional[str] = None


class EventBody(BaseModel):
    kind: str
    lead_id: str = ""
    note: str = ""
    state: str = ""
    fatal: bool = False


def make_router(store, notify_sender=None) -> APIRouter:
    router = APIRouter()

    # ----- the portal (owner only: see routes_auth OWNER_PREFIXES) -----
    @router.get("/api/linkedin-agent")
    def get_status():
        return agent.status(store)

    @router.put("/api/linkedin-agent")
    def put_config(body: ConfigBody):
        if body.resume_account:
            agent.resume(store, body.resume_account)
        try:
            if body.merge_from or body.merge_into:
                agent.merge(store, body.merge_from or "", body.merge_into or "")
            agent.save_config(store, body.model_dump(exclude={"resume_account", "merge_from", "merge_into"}))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return agent.status(store)

    # ----- the agent program (pairing token; see extension_api.ALLOWED) -----
    @router.get("/api/extension/agent")
    def control(request: Request):
        account = request.headers.get("x-linkedin-account", "")
        agent.heartbeat(store, "asking", account=account, name=unquote(request.headers.get("x-linkedin-name", ""))[:80])
        return agent.control(store, account=account)

    @router.post("/api/extension/agent/event")
    def event(body: EventBody, request: Request):
        try:
            return agent.record(store, {**body.model_dump(), "account": request.headers.get("x-linkedin-account", "")}, notify_sender=notify_sender)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    return router
