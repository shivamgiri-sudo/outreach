"""Routes: pick the LinkedIn account per lead / in bulk, owner defaults, and the LinkedIn stage board (+ CSV)."""
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import audit, exports, linkedin_accounts as accts, linkedin_stages as stages, workspaces


class AccountBody(BaseModel):
    account: str = Field(default="", max_length=100)


class AssignBody(BaseModel):
    lead_ids: list[str] = Field(max_length=accts.MAX_LEADS)
    account: str = Field(default="", max_length=100)


class OwnerDefaultsBody(BaseModel):
    defaults: dict[str, str] = Field(default={}, max_length=200)


def make_router(store) -> APIRouter:
    router = APIRouter()

    def user_of(request: Request):
        return getattr(request.state, "user", None)

    @router.get("/api/linkedin/accounts")
    def accounts(request: Request):
        """Known LinkedIn accounts (for the "Send from" pickers) and the owner defaults. Each client account sees only the
        LinkedIn accounts working for it (app/linkedin_scope.py); the owner defaults are Mas Callnet's own."""
        from . import accounts as client_accounts
        user = user_of(request)
        from . import linkedin_scope
        mine = linkedin_scope.my_slugs(store, user) if user else None        # None: an owner sees every profile
        keep = (lambda rows: rows) if mine is None else (lambda rows: [r for r in rows if r["account"] in mine])
        if client_accounts.is_client(user):
            return {"accounts": keep(accts.known(store, client_accounts.of_user(user))), "owner_defaults": {}}
        return {"accounts": keep(accts.known(store, client_accounts.current())),
                "owner_defaults": accts.owner_defaults(store.read("settings"))}

    @router.put("/api/linkedin/owner-accounts")
    def owner_accounts(body: OwnerDefaultsBody, request: Request):
        try:
            return {"owner_defaults": accts.save_owner_defaults(store, body.defaults, user_of(request))}
        except accts.AssignRefused as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.put("/api/leads/{lead_id}/linkedin-account")
    def lead_account(lead_id: str, body: AccountBody, request: Request):
        try:
            r = accts.set_account(store, [lead_id], body.account, user_of(request))
        except accts.AssignRefused as exc:
            raise HTTPException(400, str(exc)) from exc
        res = r["results"][0]
        if res.get("reason") == "not found":
            raise HTTPException(404, "Lead not found")
        if not res["ok"] and res["reason"] != "no change":
            raise HTTPException(409, f"Not changed: {res['reason']}.")
        lead = next(l for l in store.read("leads") if l["id"] == lead_id)
        return {"lead_id": lead_id, "account": accts.assigned(lead, store.read("settings")),
                "assigned_by": accts.how_assigned(lead, store.read("settings")), "changed": res["ok"]}

    @router.post("/api/linkedin/assign")
    def assign(body: AssignBody, request: Request):
        try:
            return accts.set_account(store, body.lead_ids, body.account, user_of(request))
        except accts.AssignRefused as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/api/linkedin/stages")
    def board(account: str = Query("", max_length=100), owner: str = Query("", max_length=100), stage: str = Query("", max_length=40)):
        out = stages.board(store, account, owner)
        if stage:
            out["items"] = stages.rows(store, account, owner, stage)
        return out

    @router.get("/api/linkedin/stages.csv")
    def board_csv(request: Request, stage: str = Query("", max_length=40), account: str = Query("", max_length=100),
                  owner: str = Query("", max_length=100)):
        user = user_of(request) or {"role": "owner"}
        if user.get("role") not in ("owner", "admin"):
            raise HTTPException(403, "Only an owner or a workspace admin can download this list.")
        found = stages.rows(store, account, owner, stage)
        audit.record(store, user, "linkedin.stages_export", stages.label(stage) or "All LinkedIn stages",
                     f"{len(found)} rows; account={account or 'all'}; owner={owner or 'all'}"
                     + ("; workspace scope" if workspaces.scoped() else ""))
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        name = f"linkedin-{re.sub(r'[^a-z0-9]+', '-', stage.lower()) or 'all'}-{day}.csv"
        return StreamingResponse(exports.csv_chunks(stages.CSV_COLUMNS, found), media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})

    return router
