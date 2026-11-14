"""Routes: lead ageing rules, the re-engage list, and the read-only existing-duplicates report."""
from typing import List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import accounts, audit, duplicates, lead_access, lead_ageing, merge_flow
from . import workspaces as workspaces_mod


class ReEngageBody(BaseModel):
    lead_ids: List[str] = Field(default_factory=list, max_length=20000)
    kind: str = Field(default="", max_length=20)


class MergeBody(BaseModel):
    keep_id: str = Field(max_length=60)
    merge_ids: List[str] = Field(default_factory=list, max_length=50)     # the limit (5) is checked in words by app/merge_flow.py
    dry_run: bool = True              # safe by default: nothing is written unless the caller says dry_run=false


def make_router(store) -> APIRouter:
    router = APIRouter()

    def user_of(request: Request):
        return getattr(request.state, "user", None)

    @router.get("/api/lead-ageing")
    def get_ageing():
        return {**lead_ageing.config(store), "defaults": lead_ageing.DEFAULTS}

    @router.put("/api/lead-ageing")
    def put_ageing(body: dict, request: Request):
        try:
            saved = lead_ageing.save_config(store, body)
        except lead_ageing.AgeingRefused as exc:
            raise HTTPException(400, str(exc)) from exc
        audit.record(store, user_of(request), "lead_ageing.settings", "Lead ageing rules", ", ".join(f"{k} {v}" for k, v in saved.items()))
        return {**saved, "defaults": lead_ageing.DEFAULTS}

    @router.post("/api/lead-ageing/re-engage")
    def re_engage(body: ReEngageBody, request: Request):
        try:
            return lead_ageing.re_engage_list(store, user_of(request), audit.actor_of(user_of(request)), body.lead_ids, body.kind)
        except lead_ageing.AgeingRefused as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/api/lead-duplicates")
    def lead_duplicates(request: Request):
        """Leads already in the app that share an email, phone or LinkedIn profile. Owners and admins only; read-only."""
        user = user_of(request)
        if user is not None and (user.get("base_role") or user.get("role")) not in ("owner", "admin"):
            raise HTTPException(403, "Only an owner or admin can look for duplicate leads.")
        seen = lead_access.visible_leads(store, user, workspaces_mod.filter_rows(store.read("leads")))
        amap = accounts.ws_account_map(store)
        return duplicates.existing_groups(seen, lambda l: accounts.account_of_lead(amap, l))

    @router.post("/api/lead-duplicates/merge")
    def merge_duplicates(body: MergeBody, request: Request):
        """Fold duplicate leads into one (owner / admin of the workspace). dry_run (default) only reports what would change."""
        user = user_of(request)
        try:
            return merge_flow.merge(store, user, audit.actor_of(user), body.keep_id, body.merge_ids, body.dry_run)
        except merge_flow.MergeRefused as exc:
            raise HTTPException(exc.status, exc.message) from exc

    @router.post("/api/lead-duplicates/merge-undo/{undo_id}")
    def merge_undo(undo_id: str, request: Request):
        user = user_of(request)
        try:
            return merge_flow.undo(store, undo_id, user, audit.actor_of(user))
        except merge_flow.MergeRefused as exc:
            raise HTTPException(exc.status, exc.message) from exc

    return router
