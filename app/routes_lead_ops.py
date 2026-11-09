"""Routes: lead owner, tags, bulk actions, saved Leads views and the owner's scoring settings."""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, lead_ops, scoring
from . import workspaces as workspaces_mod
from .routes_outreach import enroll_lead


class OwnerBody(BaseModel):
    owner: str = Field(default="", max_length=200)


class TagsBody(BaseModel):
    add: List[str] = Field(default_factory=list, max_length=30)
    remove: List[str] = Field(default_factory=list, max_length=30)


class BulkBody(BaseModel):
    lead_ids: List[str] = Field(default_factory=list, max_length=lead_ops.BULK_MAX)
    action: str
    value: Optional[str] = Field(default=None, max_length=200)
    force: bool = False


class ViewBody(BaseModel):
    name: str = Field(max_length=200)
    filters: dict = Field(default_factory=dict)


class ScoringBody(BaseModel):
    weights: Optional[dict] = None
    hot: Optional[int] = None
    warm: Optional[int] = None
    open_roles_full: Optional[int] = None
    fit_size_min: Optional[int] = None


def make_router(store, present) -> APIRouter:
    router = APIRouter()
    scoring.use_store(store)

    def actor(request: Request) -> dict:
        return audit.actor_of(getattr(request.state, "user", None))

    def visible_lead(lead_id: str) -> dict:
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None or not workspaces_mod.visible(lead):
            raise HTTPException(404, "Lead not found")
        return lead

    @router.get("/api/lead-owners")
    def owners(request: Request):
        return {"owners": lead_ops.owner_choices(store, getattr(request.state, "user", None)), "me": actor(request)["name"]}

    @router.post("/api/leads/{lead_id}/owner")
    def set_owner(lead_id: str, body: OwnerBody, request: Request):
        visible_lead(lead_id)
        who = actor(request)["name"]
        try:
            owner = lead_ops.clean_owner(store, body.owner, who, getattr(request.state, "user", None))
        except lead_ops.LeadOpRefused as exc:
            raise HTTPException(400, str(exc)) from exc
        saved = lead_ops.set_owner(store, lead_id, owner, who)
        if saved is None:
            raise HTTPException(404, "Lead not found")
        return present(saved)

    @router.post("/api/leads/{lead_id}/tags")
    def change_tags(lead_id: str, body: TagsBody, request: Request):
        visible_lead(lead_id)
        try:
            saved = lead_ops.change_tags(store, lead_id, body.add, body.remove, actor(request)["name"])
        except lead_ops.LeadOpRefused as exc:
            raise HTTPException(400, str(exc)) from exc
        if saved is None:
            raise HTTPException(404, "Lead not found")
        return present(saved)

    @router.get("/api/lead-tags")
    def tags():
        return lead_ops.all_tags(workspaces_mod.filter_rows(store.read("leads")))

    @router.post("/api/leads/bulk")
    def bulk(body: BulkBody, request: Request):
        try:
            return lead_ops.run_bulk(store, body.lead_ids, body.action, body.value, actor(request), body.force, enroll_lead)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except lead_ops.LeadOpRefused as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/api/lead-views")
    def views(request: Request):
        return lead_ops.list_views(store, getattr(request.state, "user", None))

    @router.post("/api/lead-views")
    def save_view(body: ViewBody, request: Request):
        try:
            return lead_ops.save_view(store, getattr(request.state, "user", None), body.name, body.filters)
        except lead_ops.LeadOpRefused as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.delete("/api/lead-views/{view_id}")
    def delete_view(view_id: str, request: Request):
        if not lead_ops.delete_view(store, getattr(request.state, "user", None), view_id):
            raise HTTPException(404, "View not found")
        audit.record(store, getattr(request.state, "user", None), "view.delete", view_id)
        return {"ok": True}

    @router.get("/api/scoring")
    def get_scoring():
        return {**scoring.active_config(), "defaults": scoring.DEFAULT_CONFIG}

    @router.put("/api/scoring")
    def put_scoring(body: ScoringBody, request: Request):
        saved = store.read("settings").get(scoring.SETTINGS_KEY) or {}
        incoming = {k: v for k, v in body.model_dump().items() if v is not None}
        if "weights" in incoming:
            incoming["weights"] = {**(saved.get("weights") or {}), **incoming["weights"]}
        try:
            config = scoring.clean_config({**saved, **incoming})
        except scoring.ConfigError as exc:
            raise HTTPException(400, str(exc)) from exc
        stamp = {**config, "updated_by": actor(request)["name"], "updated_at": lead_ops.now_iso()}
        from . import audit_settings
        with audit_settings.watch(store, request, "Lead scoring", key=scoring.SETTINGS_KEY):
            store.update("settings", lambda s: {**s, scoring.SETTINGS_KEY: stamp})
        scoring.reset_cache()
        return {**config, "defaults": scoring.DEFAULT_CONFIG}

    return router
