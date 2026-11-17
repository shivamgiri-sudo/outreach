"""GET /api/leads/{lead_id}/sources: "Where this came from" for one lead (app/lead_sources_view.py), read only.

Same visibility as GET /api/leads/{lead_id}: the lead must be in the caller's workspaces and allowed by lead ownership,
else 404 (the global lead scope guard in main.py also answers 404 for a limited login outside its workspaces)."""
from fastapi import APIRouter, HTTPException, Request

from . import lead_access, lead_sources_view, workspaces


def make_router(store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/leads/{lead_id}/sources")
    def lead_sources(lead_id: str, request: Request):
        lead = next((l for l in store.read("leads") if l.get("id") == lead_id), None)
        if lead is None or not workspaces.visible(lead) or not lead_access.can_see(store, getattr(request.state, "user", None), lead):
            raise HTTPException(404, "Lead not found")
        return lead_sources_view.build(store, lead)

    return router
