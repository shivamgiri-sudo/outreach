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
            row = ws.create(store, body.name, who(request))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        audit.record(store, getattr(request.state, "user", None), "workspace.create", row["name"], f"id {row['id']}")
        return row

    @router.delete("/api/workspaces/{ws_id}")
    def remove(ws_id: str, request: Request):
        names = {w["id"]: w["name"] for w in ws.all_workspaces(store)}
        try:
            ws.delete(store, ws_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        audit.record(store, getattr(request.state, "user", None), "workspace.delete", names.get(ws_id, ws_id), f"id {ws_id}")
        return {"ok": True}

    @router.post("/api/workspaces/{ws_id}/move")
    def move(ws_id: str, body: MoveBody, request: Request):
        allowed = ws.allowed_of(getattr(request.state, "user", None))
        if allowed is not None and ws_id not in allowed:
            raise HTTPException(403, "You do not have access to that workspace.")
        try:
            moved = ws.move(store, body.lead_ids, ws_id, who(request))
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        if moved:
            audit.record(store, getattr(request.state, "user", None), "bulk.workspace_move", ws_id, f"{moved} of {len(body.lead_ids)} leads moved")
        return {"moved": moved}

    return router
