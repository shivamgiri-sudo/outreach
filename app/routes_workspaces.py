"""Workspaces API: list (with lead counts), create, remove, move leads."""
from typing import List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, workspaces as ws


class WsBody(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class MoveBody(BaseModel):
    lead_ids: List[str] = Field(default_factory=list, max_length=5000)


def make_router(store) -> APIRouter:
    router = APIRouter()

    def who(request: Request) -> str:
        return audit.actor_of(getattr(request.state, "user", None))["name"]

    @router.get("/api/workspaces")
    def list_all(request: Request):
        allowed = ws.allowed_of(getattr(request.state, "user", None))
        n = ws.counts(store)
        return [{**w, "leads": n.get(w["id"], 0)} for w in ws.all_workspaces(store) if allowed is None or w["id"] in allowed]

    @router.post("/api/workspaces")
    def create(body: WsBody, request: Request):
        try:
            return ws.create(store, body.name, who(request))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.delete("/api/workspaces/{ws_id}")
    def remove(ws_id: str):
        try:
            ws.delete(store, ws_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True}

    @router.post("/api/workspaces/{ws_id}/move")
    def move(ws_id: str, body: MoveBody, request: Request):
        allowed = ws.allowed_of(getattr(request.state, "user", None))
        if allowed is not None and ws_id not in allowed:
            raise HTTPException(403, "You do not have access to that workspace.")
        try:
            return {"moved": ws.move(store, body.lead_ids, ws_id, who(request))}
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None

    return router
