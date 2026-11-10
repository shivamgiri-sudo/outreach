"""Assignment rules API (teams, ordered rules, round-robin). Owner or admin of THE workspace in the request scope only.

  GET  /api/assignment              this workspace's settings
  PUT  /api/assignment              replace them (validated)
  POST /api/assignment/run-unowned  give the workspace's unowned leads an owner by the rules
  GET  /api/assignment/stats        per member: leads assigned, average first touch, reassigned away; unassigned count
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from . import assignment, audit
from . import workspaces as workspaces_mod


def make_router(store) -> APIRouter:
    router = APIRouter()

    def workspace_for(request: Request) -> str:
        """The one workspace this admin manages now: the picked one, the login's only one, or main for an owner."""
        user = getattr(request.state, "user", None) or {"role": "owner"}
        if user.get("role") not in ("owner", "admin"):
            raise HTTPException(403, "Only the owner or a workspace admin manages lead assignment.")
        selected, allowed = getattr(request.state, "workspace_scope", (None, None))
        ws = selected or (sorted(allowed)[0] if allowed is not None and len(allowed) == 1 else None) \
            or (workspaces_mod.MAIN if allowed is None else None)
        if not ws:
            raise HTTPException(400, "Pick a workspace first.")
        if allowed is not None and ws not in allowed:
            raise HTTPException(403, "That workspace is not yours to manage.")
        return ws

    @router.get("/api/assignment")
    def get_config(request: Request):
        return assignment.config(store, workspace_for(request))

    @router.put("/api/assignment")
    def put_config(body: dict, request: Request):
        ws = workspace_for(request)
        try:
            saved = assignment.save_config(store, ws, body)
        except assignment.AssignmentRefused as exc:
            raise HTTPException(400, str(exc)) from None
        audit.record(store, getattr(request.state, "user", None), "assignment.save", ws,
                     f"Assignment {'on' if saved['on'] else 'off'}: {len(saved['teams'])} teams, {len(saved['rules'])} rules")
        return saved

    @router.post("/api/assignment/run-unowned")
    def run_unowned(request: Request):
        ws = workspace_for(request)
        out = assignment.run_unowned(store, ws)
        audit.record(store, getattr(request.state, "user", None), "assignment.run", ws, f"Assigned {out['assigned']} unowned leads, {out['left']} left")
        return out

    @router.get("/api/assignment/stats")
    def get_stats(request: Request):
        return assignment.stats(store, workspace_for(request))

    return router
