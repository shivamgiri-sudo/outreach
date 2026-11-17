"""Email checks before the first send: status, switch, "check now" and a manager's approval of one risky address, plus
the fresh-check rule (re-check addresses whose last check is older than N days) and its "Re-check now (max 500)" run.
Responses hold counts only (no addresses or names)."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, email_gate, email_recheck, lead_access, workspaces

CHECK_NOW_LIMIT = 100


class GateBody(BaseModel):
    enabled: bool
    approve_risky: bool = False
    builtin: Optional[bool] = None             # None = keep what was saved
    recheck_enabled: Optional[bool] = None     # None = keep what was saved (default on)
    recheck_days: Optional[int] = Field(default=None, ge=email_gate.RECHECK_MIN_DAYS, le=email_gate.RECHECK_MAX_DAYS)


BACKGROUND = True                              # tests set False: "Re-check now" then finishes inside the request


def make_router(store, transports: dict) -> APIRouter:
    router = APIRouter()
    recheck = email_recheck.Recheck(store, transports)

    def my_leads(request: Request) -> list:
        user = getattr(request.state, "user", None)
        hider = lead_access.hidden_test(store, user)
        return [l for l in workspaces.filter_rows(store.read("leads")) if not (hider and hider(l))]

    def view(request: Request) -> dict:
        cfg, info = email_gate.settings_of(store), email_gate.verifier(store)
        status = (store.read("settings") or {}).get("email_gate_status") or {}
        return {"enabled": cfg["enabled"], "approve_risky": cfg["approve_risky"],
                "recheck_enabled": cfg["recheck_enabled"], "recheck_days": cfg["recheck_days"],
                "verifier": {"connected": info["connected"], "provider": info["provider"], "supported": info["supported"],
                             **({"builtin": True} if info.get("builtin") else {})},
                "builtin": email_gate.builtin_on(store),
                "counts": email_gate.counts(store, my_leads(request)),
                "last_run": status.get("at"), "error": status.get("error") or ""}

    @router.get("/api/email-gate")
    def get_gate(request: Request):
        return view(request)

    @router.put("/api/email-gate")
    def put_gate(body: GateBody, request: Request):
        keep = email_gate.builtin_on(store) if body.builtin is None else body.builtin
        cfg = email_gate.settings_of(store)
        fresh = cfg["recheck_enabled"] if body.recheck_enabled is None else body.recheck_enabled
        days = cfg["recheck_days"] if body.recheck_days is None else email_gate.recheck_days(body.recheck_days)
        store.update("settings", lambda s: {**s, "email_gate": {"enabled": body.enabled, "approve_risky": body.approve_risky, "builtin": keep,
                                                                "recheck_enabled": fresh, "recheck_days": days}})
        audit.record(store, getattr(request.state, "user", None), "email_gate.settings",
                     "email checks before the first send", f"{'on' if body.enabled else 'off'}; risky addresses {'allowed' if body.approve_risky else 'held'}; built-in checker {'on' if keep else 'off'}"
                     f"; fresh check after {days} days {'on' if fresh else 'off'}")
        return view(request)

    @router.post("/api/email-gate/check-now")
    def check_now(request: Request):
        ids = {l["id"] for l in my_leads(request)}
        out = email_gate.check_pending(store, transports, CHECK_NOW_LIMIT, lead_ids=ids)
        return out

    @router.post("/api/email-gate/recheck-stale")
    def recheck_now(request: Request):
        ids = {l["id"] for l in my_leads(request)}
        try:
            state = recheck.start(ids, background=BACKGROUND)
        except email_recheck.Busy as exc:
            raise HTTPException(409, str(exc)) from None
        audit.record(store, getattr(request.state, "user", None), "email_gate.recheck", "re-check old email checks",
                     f"up to {email_gate.RECHECK_MAX} address(es) not checked in {state['days']} days")
        return state

    @router.get("/api/email-gate/recheck-stale")
    def recheck_progress():
        return recheck.view()

    @router.post("/api/leads/{lead_id}/email-approve")
    def approve(lead_id: str, request: Request):
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None or not workspaces.filter_rows([lead]):
            raise HTTPException(404, "Lead not found")
        lead_access.check_http(store, request, lead)
        try:
            return email_gate.approve(store, lead_id, getattr(request.state, "user", None))
        except LookupError:
            raise HTTPException(404, "Lead not found") from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    return router
