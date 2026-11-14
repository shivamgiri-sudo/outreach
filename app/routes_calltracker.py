"""DialHalo connection per workspace (Integrations -> DialHalo) and its webhook receiver.

  GET    /api/calltracker?workspace=ws        the card: connection (secrets masked), receiver address, queue, mapping
  PUT    /api/calltracker                     save address, API key, webhook secret, on/off, outcome mapping
  DELETE /api/calltracker?workspace=ws        disconnect (secrets and pending jobs removed)
  POST   /api/calltracker/test                check the key against DialHalo and preview owner matching
  POST   /api/calltracker/retry               failed pushes back into the queue
  GET    /api/calltracker/funnel              called / connected / meetings booked from real calls (leads in scope)
  POST   /api/calltracker/hook/{conn_id}      receiver for DialHalo's signed webhooks (no login: the signature is the key)

Owners manage any workspace; a workspace admin only its own workspaces (never main). Connect / change / disconnect
are written to the change log with key names only, never values.
"""
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import audit, calltracker as ct, calltracker_events as events, workspaces
from .activities import CALL_OUTCOMES
from .stages import ALL_STAGES

HOOK_PATH = "/api/calltracker/hook/"
MAX_BODY = 1_000_000


class ConnectionBody(BaseModel):
    workspace: str = ""
    base_url: Optional[str] = None
    public_url: Optional[str] = None
    api_key: Optional[str] = None
    webhook_secret: Optional[str] = None
    enabled: Optional[bool] = None
    mapping: Optional[dict] = None
    reset_mapping: bool = False


class WorkspaceBody(BaseModel):
    workspace: str = ""


def _user(request: Request) -> dict:
    return getattr(request.state, "user", None) or {"role": "owner", "name": "This computer", "email": ""}


def make_router(store, base_url: str) -> APIRouter:
    router = APIRouter()

    def workspace_for(request: Request, raw: str) -> str:
        """The workspace this admin may manage: owners any existing one, a workspace admin only its own (not main)."""
        user = _user(request)
        selected, _ = getattr(request.state, "workspace_scope", (None, None))
        ws = (raw or selected or workspaces.MAIN).strip()[:40]
        if ws not in {w["id"] for w in workspaces.all_workspaces(store)}:
            raise HTTPException(404, "Workspace not found")
        allowed = workspaces.allowed_of(user)
        if user.get("role") == "owner" and allowed is None:
            return ws
        if user.get("role") == "admin" and allowed is not None and ws in allowed and ws != workspaces.MAIN:
            return ws
        raise HTTPException(403, "Only the owner or this workspace's admin can connect DialHalo.")

    def receiver(request: Request, conn: Optional[dict]) -> dict:
        if not conn:
            return {"public": "", "internal": ""}
        from .growth_core import public_base_url
        return {"public": f"{public_base_url(store, base_url)}{HOOK_PATH}{conn['id']}",
                "internal": f"http://127.0.0.1:8000{HOOK_PATH}{conn['id']}"}

    def card(request: Request, ws: str) -> dict:
        conn = ct.connection(store, ws)
        return {"workspace": ws, "connection": ct.public_view(store, ws), "receiver": receiver(request, conn),
                "queue": ct.queue_summary(store, ws), "default_mapping": ct.DEFAULT_MAPPING,
                "outcomes": list(CALL_OUTCOMES), "stages": list(ALL_STAGES),
                "events": list(events.CALL_EVENTS) + ["dnc.added", "dnc.removed"]}

    @router.get("/api/calltracker")
    def get_card(request: Request, workspace: str = ""):
        return card(request, workspace_for(request, workspace))

    @router.put("/api/calltracker")
    def save(body: ConnectionBody, request: Request):
        ws = workspace_for(request, body.workspace)
        existing = ct.connection(store, ws)
        changes, changed = {}, []
        try:
            if body.base_url is not None:
                changes["base_url"] = ct.check_base_url(body.base_url)
            if body.public_url is not None:
                changes["public_url"] = ct.check_public_url(body.public_url)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        for key in ("api_key", "webhook_secret"):
            value = (getattr(body, key) or "").strip()
            if value:
                if len(value) > 300 or any(ch.isspace() for ch in value):
                    raise HTTPException(400, f"The {key.replace('_', ' ')} looks wrong (spaces or too long).")
                changes[key] = value
        if body.api_key and not body.api_key.strip().startswith("ctk_"):
            raise HTTPException(400, "A DialHalo API key starts with ctk_. Create one in DialHalo: Settings → Integrations → API keys.")
        if body.enabled is not None:
            changes["enabled"] = bool(body.enabled)
        if body.reset_mapping:
            changes["mapping"] = {}
        elif body.mapping is not None:
            changes["mapping"] = ct.clean_mapping(body.mapping)
        if not existing and not changes.get("base_url"):
            raise HTTPException(400, "Enter the DialHalo address first.")
        for key in changes:
            if key in ("api_key", "webhook_secret") or (existing or {}).get(key) != changes[key]:
                changed.append(key)
        who = audit.actor_of(_user(request))["name"]
        ct.save_connection(store, ws, changes, who)
        audit.record(store, _user(request), "calltracker.connect" if not existing else "calltracker.update", f"workspace {ws}",
                     f"changed: {', '.join(changed) or 'nothing'}")                       # key names only, never values
        return card(request, ws)

    @router.delete("/api/calltracker")
    def disconnect(request: Request, workspace: str = ""):
        ws = workspace_for(request, workspace)
        if not ct.delete_connection(store, ws):
            raise HTTPException(404, "DialHalo is not connected for this workspace.")
        audit.record(store, _user(request), "calltracker.disconnect", f"workspace {ws}", "connection, API key and webhook secret removed")
        return card(request, ws)

    @router.post("/api/calltracker/test")
    def test(body: WorkspaceBody, request: Request):
        ws = workspace_for(request, body.workspace)
        conn = ct.connection(store, ws)
        if not conn or not conn.get("api_key"):
            raise HTTPException(400, "Save the address and API key first.")
        try:
            members = ct.ct_request(conn, "GET", "/api/v1/members")
            ct.ct_request(conn, "GET", "/api/v1/dnc?limit=1")
        except ct.CallTrackerError as exc:
            ct.note_status(store, ws, "last_test", False, str(exc))
            return {"ok": False, "message": str(exc), "card": card(request, ws)}
        all_members = [m for m in members if isinstance(m, dict)] if isinstance(members, list) else []
        members = [m for m in all_members if m.get("active", 1)]
        preview = ct.owner_preview(store, ws, members, all_members)
        msg = f"Connected. {len(members)} DialHalo member(s); {sum(p['matched'] for p in preview)} of {len(preview)} Outreach people match by email."
        if not conn.get("webhook_secret"):
            msg += " Add the webhook secret so calls come back."
        ct.note_status(store, ws, "last_test", True, msg)
        unmatched = [{"name": p["name"], "email": p["email"], "reason": p["reason"]} for p in preview if not p["matched"]]
        return {"ok": True, "message": msg, "members": len(members), "owners": preview, "unmatched": unmatched, "card": card(request, ws)}

    @router.post("/api/calltracker/retry")
    def retry(body: WorkspaceBody, request: Request):
        ws = workspace_for(request, body.workspace)
        return {"requeued": ct.retry_failed(store, ws), "card": card(request, ws)}

    @router.get("/api/calltracker/funnel")
    def funnel(date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to")):
        try:
            start = datetime.fromisoformat(date_from[:10]).replace(tzinfo=events.IST) if date_from else None
            end = (datetime.fromisoformat(date_to[:10]).replace(tzinfo=events.IST) + timedelta(days=1)) if date_to else None
        except ValueError:
            raise HTTPException(400, "Dates must look like 2026-11-13")
        from .source_stats import real
        leads = real(workspaces.filter_rows(store.read("leads")))
        return {"from": date_from[:10], "to": date_to[:10], **events.funnel(store, leads, start, end),
                "connected_workspaces": len([c for c in ct.connections(store).values() if isinstance(c, dict) and c.get("enabled", True)])}

    @router.post(HOOK_PATH + "{conn_id}")
    async def hook(conn_id: str, request: Request):
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return JSONResponse({"detail": "Too large"}, status_code=413)
        from starlette.concurrency import run_in_threadpool
        status, payload = await run_in_threadpool(events.handle, store, conn_id, request.headers.get("x-dialhalo-signature") or request.headers.get("x-calltracker-signature", ""), raw)
        return JSONResponse(payload, status_code=status)

    return router
