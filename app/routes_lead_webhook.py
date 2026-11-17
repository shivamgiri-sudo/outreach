"""Lead webhook setting (Integrations -> Lead webhook), owner only (app/routes_auth.py OWNER_PREFIXES).

  GET    /api/lead-webhook?workspace=ws    the card: setting (secret never returned), events, fields, delivery log, queue
  PUT    /api/lead-webhook                 save url / secret / mode / events / fields / on-off
  DELETE /api/lead-webhook?workspace=ws    remove it (waiting deliveries dropped)
  POST   /api/lead-webhook/test            send one sample lead.created now and say what came back
  POST   /api/lead-webhook/retry           failed deliveries back into the queue
Changes are written to the change log with names only, never the address or secret.
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, lead_webhook as hooks, lead_webhook_events, workspaces
from .retry_queue import JobError, iso, now_utc

APPS_SCRIPT = """function doPost(e) {
  var data = JSON.parse(e.postData.contents);
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Leads')
           || SpreadsheetApp.getActiveSpreadsheet().insertSheet('Leads');
  if (sheet.getLastRow() === 0) sheet.appendRow(data.columns);
  sheet.appendRow(data.row);
  return ContentService.createTextOutput('ok');
}"""


class HookBody(BaseModel):
    workspace: str = ""
    url: Optional[str] = Field(default=None, max_length=2000)
    secret: Optional[str] = Field(default=None, max_length=200)
    mode: Optional[str] = None
    events: Optional[List[str]] = Field(default=None, max_length=10)
    fields: Optional[List[str]] = Field(default=None, max_length=30)
    enabled: Optional[bool] = None


class WorkspaceBody(BaseModel):
    workspace: str = ""


def _user(request: Request) -> dict:
    return getattr(request.state, "user", None) or {"role": "owner", "name": "This computer", "email": ""}


def make_router(store) -> APIRouter:
    router = APIRouter()
    lead_webhook_events.install(store)
    if any(j.get("status") == "pending" for j in store.read(hooks.OUTBOX)):
        hooks.QUEUE.kick(store)                       # deliveries waiting from before a restart

    def workspace_for(request: Request, raw: str) -> str:
        user = _user(request)
        if user.get("role") != "owner":
            raise HTTPException(403, "Only the owner can set up the lead webhook.")
        selected, allowed = getattr(request.state, "workspace_scope", (None, None))
        ws = hooks.clean_ws(raw) or selected or workspaces.MAIN
        if ws not in {w["id"] for w in workspaces.all_workspaces(store)} or (allowed is not None and ws not in allowed):
            raise HTTPException(404, "Workspace not found")
        return ws

    def card(ws: str) -> dict:
        return {"workspace": ws, "hook": hooks.public_view(store, ws), "events": [{"id": e, "text": hooks.EVENT_TEXT[e]} for e in hooks.EVENTS],
                "watchable": list(hooks.WATCHABLE), "log": hooks.delivery_log(store, ws), "queue": hooks.queue_summary(store, ws),
                "sheet_columns": list(hooks.SHEET_COLUMNS), "apps_script": APPS_SCRIPT}

    def who(request: Request) -> str:
        u = _user(request)
        return u.get("name") or u.get("email") or "owner"

    @router.get("/api/lead-webhook")
    def get_card(request: Request, workspace: str = ""):
        return card(workspace_for(request, workspace))

    @router.put("/api/lead-webhook")
    def put_hook(body: HookBody, request: Request):
        ws = workspace_for(request, body.workspace)
        changes = body.model_dump(exclude={"workspace"}) if hasattr(body, "model_dump") else body.dict(exclude={"workspace"})
        try:
            hook = hooks.save(store, ws, changes, who(request))
        except hooks.HookError as exc:
            raise HTTPException(400, str(exc)) from None
        changed = [k for k, v in changes.items() if v is not None]
        audit.record(store, _user(request), "lead_webhook.save", f"workspace {ws}",
                     f"changed: {', '.join(changed) or 'nothing'}; {'on' if hook['enabled'] else 'off'}; mode {hook['mode']}")
        return card(ws)

    @router.delete("/api/lead-webhook")
    def delete_hook(request: Request, workspace: str = ""):
        ws = workspace_for(request, workspace)
        if not hooks.remove(store, ws):
            raise HTTPException(404, "No lead webhook in this workspace.")
        audit.record(store, _user(request), "lead_webhook.remove", f"workspace {ws}", "removed")
        return card(ws)

    @router.post("/api/lead-webhook/test")
    def test_hook(body: WorkspaceBody, request: Request):
        ws = workspace_for(request, body.workspace)
        hook = hooks.hook_of(store, ws)
        if not hook or not hook.get("url"):
            raise HTTPException(400, "Add the address first.")
        sample = hooks.payload("lead.created", hooks.sample_lead(ws))
        sample["test"] = True
        entry = {"at": iso(now_utc()), "event": "test", "lead_id": "test-lead", "company": "Example Traders", "ws": ws, "attempts": 1}
        try:
            result = hooks.send(hook, sample)
            hooks.log(store, {**entry, "status": "done", "result": result})
            return {"ok": True, "result": result}
        except JobError as exc:
            hooks.log(store, {**entry, "status": "failed", "result": str(exc)})
            return {"ok": False, "result": str(exc)}

    @router.post("/api/lead-webhook/retry")
    def retry(body: WorkspaceBody, request: Request):
        ws = workspace_for(request, body.workspace)
        return {"requeued": hooks.QUEUE.retry_failed(store, lambda job: job.get("ws") == ws)}

    return router
