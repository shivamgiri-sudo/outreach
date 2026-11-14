"""Email checks before the first send: status, switch, "check now" and a manager's approval of one risky address.
Responses hold counts only (no addresses or names)."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import audit, email_gate, lead_access, workspaces

CHECK_NOW_LIMIT = 100


class GateBody(BaseModel):
    enabled: bool
    approve_risky: bool = False


def make_router(store, transports: dict) -> APIRouter:
    router = APIRouter()

    def my_leads(request: Request) -> list:
        user = getattr(request.state, "user", None)
        hider = lead_access.hidden_test(store, user)
        return [l for l in workspaces.filter_rows(store.read("leads")) if not (hider and hider(l))]

    def view(request: Request) -> dict:
        cfg, info = email_gate.settings_of(store), email_gate.verifier(store)
        status = (store.read("settings") or {}).get("email_gate_status") or {}
        return {"enabled": cfg["enabled"], "approve_risky": cfg["approve_risky"],
                "verifier": {"connected": info["connected"], "provider": info["provider"], "supported": info["supported"]},
                "counts": email_gate.counts(store, my_leads(request)),
                "last_run": status.get("at"), "error": status.get("error") or ""}

    @router.get("/api/email-gate")
    def get_gate(request: Request):
        return view(request)

    @router.put("/api/email-gate")
    def put_gate(body: GateBody, request: Request):
        store.update("settings", lambda s: {**s, "email_gate": {"enabled": body.enabled, "approve_risky": body.approve_risky}})
        audit.record(store, getattr(request.state, "user", None), "email_gate.settings",
                     "email checks before the first send", f"{'on' if body.enabled else 'off'}; risky addresses {'allowed' if body.approve_risky else 'held'}")
        return view(request)

    @router.post("/api/email-gate/check-now")
    def check_now(request: Request):
        ids = {l["id"] for l in my_leads(request)}
        out = email_gate.check_pending(store, transports, CHECK_NOW_LIMIT, lead_ids=ids)
        return out

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
