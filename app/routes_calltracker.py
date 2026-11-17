"""DialHalo links per workspace (Integrations -> DialHalo) and their webhook receiver.

A workspace can be linked to several DialHalo workspaces (app/calltracker_links.py). Routes that act on one link take
`link` (its id); without it they act on the workspace's only link, or its primary one (a change with several links and
no `link` is refused, so nothing is changed on the wrong DialHalo workspace).

  GET    /api/calltracker?workspace=ws            the card: links (secrets masked, receiver, pairing, backfill), queue, mapping
  PUT    /api/calltracker                         manual connect / change: address, key, secret, on/off, mapping, lead_sync, primary
  DELETE /api/calltracker?workspace=ws&link=id    disconnect one link (DialHalo is told first when it can be)
  POST   /api/calltracker/test                    which DialHalo workspace, members (people pairing), do-not-call list
  POST   /api/calltracker/retry                   failed pushes back into the queue
  POST   /api/calltracker/backfill                match past calls (7 / 30 / 90 days) of one link
  POST   /api/calltracker/connect/start           one-click connect: a pending link and the DialHalo address to open
  GET    /api/calltracker/connect/callback        DialHalo sends the browser here (no login): redirect to the SPA only
  POST   /api/calltracker/connect/finish          the SPA, signed in, finishes: code exchange, link saved and tested
  GET    /api/calltracker/funnel                  called / connected / meetings booked from real calls (leads in scope)
  POST   /api/calltracker/hook/{conn_id}          receiver for DialHalo's signed webhooks (no login: the signature is the key)

Owners manage any workspace; a workspace admin only its own workspaces (never main). Connect / change / disconnect
are written to the change log with key names only, never values.
"""
import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import audit, calltracker as ct, calltracker_backfill as backfill, calltracker_connect as connect, calltracker_events as events
from . import calltracker_links as links, workspaces
from .activities import CALL_OUTCOMES
from .stages import ALL_STAGES

HOOK_PATH = "/api/calltracker/hook/"
MAX_BODY = 1_000_000
DEPLOY_PORT = 8000                # deploy/outreach.service runs uvicorn on 127.0.0.1:8000 (OUTREACH_PORT overrides)


class ConnectionBody(BaseModel):
    workspace: str = ""
    link: str = ""
    new: bool = False
    base_url: Optional[str] = None
    public_url: Optional[str] = None
    api_key: Optional[str] = None
    webhook_secret: Optional[str] = None
    enabled: Optional[bool] = None
    mapping: Optional[dict] = None
    reset_mapping: bool = False
    lead_sync: Optional[str] = None
    primary: Optional[bool] = None


class WorkspaceBody(BaseModel):
    workspace: str = ""
    link: str = ""


class BackfillBody(BaseModel):
    workspace: str = ""
    link: str = ""
    days: int = 30


class StartBody(BaseModel):
    workspace: str = ""
    dialhalo_url: str = ""


class FinishBody(BaseModel):
    state: str = ""
    code: str = ""
    error: str = ""


def _user(request: Request) -> dict:
    return getattr(request.state, "user", None) or {"role": "owner", "name": "This computer", "email": "", "sub": "local"}


def internal_port(request: Optional[Request]) -> int:
    """The port this app listens on, for DialHalo on the same server: OUTREACH_PORT, else the port uvicorn bound on the
    loopback address (the deploy), else the deploy's 8000."""
    env = os.environ.get("OUTREACH_PORT", "").strip()
    if env.isdigit() and 0 < int(env) < 65536:
        return int(env)
    server = (request.scope.get("server") if request is not None else None) or ()
    if len(server) == 2 and str(server[0]).startswith("127.") and server[1]:
        return int(server[1])
    return DEPLOY_PORT


def make_router(store, base_url: str) -> APIRouter:
    router = APIRouter()

    def public_base() -> str:
        from .growth_core import public_base_url
        return public_base_url(store, base_url)

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

    def pick(ws: str, link_id: str, change: bool = False) -> Optional[dict]:
        """The link a request means: `link_id`, else the only link, else (reads / tests) the primary one."""
        if link_id:
            link = links.get(store, link_id)
            if not link or link.get("workspace") != ws:
                raise HTTPException(404, "This DialHalo link was not found in this workspace.")
            return link
        mine = links.links_of(store, ws)
        if change and len(mine) > 1:
            raise HTTPException(400, "This workspace is linked to several DialHalo workspaces: say which one.")
        return links.primary_of(mine)

    def receiver(request: Optional[Request], conn: Optional[dict]) -> dict:
        if not conn:
            return {"public": "", "internal": ""}
        return {"public": f"{public_base()}{HOOK_PATH}{conn['id']}",
                "internal": f"http://127.0.0.1:{internal_port(request)}{HOOK_PATH}{conn['id']}"}

    def card(request: Request, ws: str) -> dict:
        conn = ct.connection(store, ws)
        rows = [{**links.public_view(store, l), "receiver": receiver(request, l)} for l in links.links_of(store, ws)]
        return {"workspace": ws, "connection": ct.public_view(store, ws), "receiver": receiver(request, conn), "links": rows,
                "queue": ct.queue_summary(store, ws), "default_mapping": ct.DEFAULT_MAPPING,
                "outcomes": list(CALL_OUTCOMES), "stages": list(ALL_STAGES), "lead_sync_modes": list(links.LEAD_SYNC),
                "events": list(events.CALL_EVENTS) + ["dnc.added", "dnc.removed"],
                "connect": {"dialhalo_url": connect.default_base(store), "callback": f"{public_base()}{connect.CALLBACK_PATH}"},
                "match": {"backfill": backfill.public(backfill.state(store, conn["id"])) if conn else {}, "days": list(backfill.DAYS),
                          "max_calls": backfill.MAX_CALLS, "waiting": len((store.read("ct_unmatched") or {}).get(ws) or {})}}

    @router.get("/api/calltracker")
    def get_card(request: Request, workspace: str = ""):
        return card(request, workspace_for(request, workspace))

    @router.put("/api/calltracker")
    def save(body: ConnectionBody, request: Request):
        ws = workspace_for(request, body.workspace)
        existing = None if body.new else pick(ws, body.link, change=True)
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
                if "•" in value or "…" in value:
                    raise HTTPException(400, f"The {key.replace('_', ' ')} is the masked version (it has • dots). In DialHalo use Show or Copy to get the full value.")
                if len(value) > 300 or any(ch.isspace() for ch in value):
                    raise HTTPException(400, f"The {key.replace('_', ' ')} looks wrong (spaces or too long).")
                changes[key] = value
        if body.api_key and not body.api_key.strip().startswith("ctk_"):
            raise HTTPException(400, "A DialHalo API key starts with ctk_. Create one in DialHalo: Settings → Integrations → API keys.")
        if body.enabled is not None:
            changes["enabled"] = bool(body.enabled)
        if body.lead_sync is not None:
            if body.lead_sync not in links.LEAD_SYNC:
                raise HTTPException(400, "Choose how leads are sent: off, call tasks, owners who are members, or every lead with a phone.")
            changes["lead_sync"] = body.lead_sync
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
        link = links.save(store, ws, existing["id"] if existing else None, changes, who)
        if body.primary:
            links.set_primary(store, link["id"])
            changed.append("primary")
        if changes.get("lead_sync") in links.SWEEP_MODES and links.is_active(link):
            from . import calltracker_sync
            if changes["lead_sync"] == "owners_members":
                links.members_of(store, links.get(store, link["id"]), refresh=True)
            calltracker_sync.sweep(store, ws)
        audit.record(store, _user(request), "calltracker.connect" if not existing else "calltracker.update", f"workspace {ws}",
                     f"link {link['id']}; changed: {', '.join(changed) or 'nothing'}")          # key names only, never values
        return card(request, ws)

    @router.delete("/api/calltracker")
    def disconnect(request: Request, workspace: str = "", link: str = ""):
        ws = workspace_for(request, workspace)
        target = pick(ws, link, change=True)
        if not target:
            raise HTTPException(404, "DialHalo is not connected for this workspace.")
        note = connect.disconnect(store, target)
        name = (target.get("dh_workspace") or {}).get("name") or target.get("public_url") or target.get("base_url") or ""
        audit.record(store, _user(request), "calltracker.disconnect", f"workspace {ws}", f"link {target['id']} ({name[:80]}): API key and webhook secret removed")
        return {"ok": True, "message": f"Disconnected{f' from {name}' if name else ''}. {note}", "card": card(request, ws)}

    @router.post("/api/calltracker/test")
    def test(body: WorkspaceBody, request: Request):
        ws = workspace_for(request, body.workspace)
        conn = pick(ws, body.link)
        if not conn or not conn.get("api_key"):
            raise HTTPException(400, "Save the address and API key first.")
        return {**connect.run_test(store, conn), "link": conn["id"], "card": card(request, ws)}

    @router.post("/api/calltracker/retry")
    def retry(body: WorkspaceBody, request: Request):
        ws = workspace_for(request, body.workspace)
        if body.link:
            pick(ws, body.link)
        return {"requeued": ct.retry_failed(store, ws, body.link or None), "card": card(request, ws)}

    @router.post("/api/calltracker/backfill")
    def start_backfill(body: BackfillBody, request: Request):
        ws = workspace_for(request, body.workspace)
        conn = pick(ws, body.link)
        try:
            row = backfill.start(store, conn["id"] if conn else ws, body.days, audit.actor_of(_user(request))["name"])
        except ValueError as exc:
            raise HTTPException(409 if "already" in str(exc) else 400, str(exc))
        audit.record(store, _user(request), "calltracker.backfill", f"workspace {ws}", f"link {conn['id']}: match past calls: {body.days} days")
        return {"ok": True, "message": "Matching past calls. This page shows the progress.", "backfill": row, "card": card(request, ws)}

    @router.post("/api/calltracker/connect/start")
    def connect_start(body: StartBody, request: Request):
        ws = workspace_for(request, body.workspace)
        name = next((w.get("name") for w in workspaces.all_workspaces(store) if w["id"] == ws), ws)
        try:
            out = connect.start(store, ws, _user(request), body.dialhalo_url or connect.default_base(store), public_base(), name)
        except connect.ConnectError as exc:
            raise HTTPException(exc.status, str(exc))
        audit.record(store, _user(request), "calltracker.connect_start", f"workspace {ws}", f"one-click connect started ({out['link_id']})")
        return out

    @router.get(connect.CALLBACK_PATH)
    def connect_callback(code: str = "", state: str = "", error: str = ""):
        return RedirectResponse(connect.spa_redirect(code, state, error), status_code=303)

    @router.post("/api/calltracker/connect/finish")
    def connect_finish(body: FinishBody, request: Request):
        try:
            row = connect.take_pending(store, body.state, _user(request))
            ws = workspace_for(request, row["workspace"])          # still allowed (role or workspaces may have changed)
            out = connect.finish(store, row, body.code, body.error, audit.actor_of(_user(request))["name"])
        except connect.ConnectError as exc:
            raise HTTPException(exc.status, str(exc))
        name = (out["link"].get("dh_workspace") or {}).get("name") or ""
        audit.record(store, _user(request), "calltracker.connect", f"workspace {ws}",
                     f"one-click: link {out['link']['id']} to DialHalo workspace {name[:80]}; api_key, webhook_secret saved")
        return {"ok": True, "message": out["message"], "link": out["link"]["id"], "test": {k: v for k, v in out["test"].items() if k != "owners"},
                "card": card(request, ws)}

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
                "connected_workspaces": len({l["workspace"] for l in links.all_links(store) if l.get("enabled", True)})}

    class SendBody(BaseModel):
        workspace: str = ""
        lead_ids: list = []
        campaign_id: str = ""
        owner: str = ""
        stage: str = ""
        unsent_only: bool = False

    @router.post("/api/calltracker/send")
    def send(body: SendBody, request: Request):
        """Bulk 'Send to DialHalo' for chosen leads or a campaign / owner / stage filter of THIS workspace."""
        from . import calltracker_kit as kit
        ws = workspace_for(request, body.workspace)
        leads = kit.select_leads(store, ws, body.lead_ids, body.campaign_id, body.owner, body.stage, body.unsent_only)
        if not leads and body.unsent_only:
            return {"requested": 0, "sent": 0, "created": 0, "updated": 0, "skipped": [], "errors": [], "owner_unmatched": [],
                    "message": "Every lead is already in DialHalo (opted-out leads are never sent)."}
        if not leads:
            raise HTTPException(400, "No leads match. Opted-out leads are never sent.")
        try:
            result = kit.send_leads(store, ws, leads, base_url)
        except ct.CallTrackerError as exc:
            raise HTTPException(400 if not exc.retry else 502, str(exc))
        audit.record(store, _user(request), "calltracker.bulk_send", f"workspace {ws}", f"{result['sent']} sent, {len(result['errors'])} errors")
        return result

    @router.get("/api/calltracker/people")
    def people(request: Request, workspace: str = ""):
        from . import calltracker_kit as kit
        ws = workspace_for(request, workspace)
        try:
            return kit.people(store, ws)
        except ct.CallTrackerError as exc:
            raise HTTPException(400 if not exc.retry else 502, str(exc))

    @router.get("/api/calltracker/people-report")
    def people_report(request: Request, workspace: str = "", date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to"), fmt: str = Query("", alias="format")):
        from . import calltracker_kit as kit
        ws = workspace_for(request, workspace)
        try:
            start = datetime.fromisoformat(date_from[:10]).replace(tzinfo=events.IST) if date_from else None
            end = (datetime.fromisoformat(date_to[:10]).replace(tzinfo=events.IST) + timedelta(days=1)) if date_to else None
        except ValueError:
            raise HTTPException(400, "Dates must look like 2026-11-13")
        conn = ct.active_connection(store, ws)
        report = kit.people_report(store, ws, start, end, kit.dialhalo_stats(conn, start, end) if conn else None)
        if fmt == "csv":
            from fastapi.responses import Response
            return Response(kit.report_csv(report), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="calling-by-person.csv"'})
        return {"from": date_from[:10], "to": date_to[:10], **report}

    @router.post("/api/calltracker/reconcile")
    def reconcile_now(body: WorkspaceBody, request: Request):
        from . import calltracker_kit as kit
        ws = workspace_for(request, body.workspace)
        try:
            return kit.reconcile(store, ws)
        except ct.CallTrackerError as exc:
            raise HTTPException(400 if not exc.retry else 502, str(exc))

    @router.post(HOOK_PATH + "{conn_id}")
    async def hook(conn_id: str, request: Request):
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return JSONResponse({"detail": "Too large"}, status_code=413)
        from starlette.concurrency import run_in_threadpool
        status, payload = await run_in_threadpool(events.handle, store, conn_id, request.headers.get("x-dialhalo-signature") or request.headers.get("x-calltracker-signature", ""), raw)
        return JSONResponse(payload, status_code=status)

    return router
