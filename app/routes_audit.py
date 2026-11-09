"""Change-log check, audit pack and a lead's consent record.

GET /api/audit/verify               owner (routes_auth /api/audit prefix): walk the hash chain (app/audit_chain.py)
GET /api/audit/pack                 owner: zip of CSVs for ?lead_id= or ?from=&to= (IST days); the download is logged
GET /api/leads/{lead_id}/consent    anyone who can see the lead (the lead scope guard answers 404 outside it)
"""
import re

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from . import audit, audit_chain, audit_pack, consent_view, workspaces

DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def make_router(store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/audit/verify")
    def verify():
        return audit_chain.verify(store.read("audit"))

    @router.get("/api/audit/pack")
    def pack(request: Request, lead_id: str = "", date_from: str = Query("", alias="from"), date_to: str = Query("", alias="to")):
        lead_id, date_from, date_to = lead_id.strip()[:40], date_from.strip()[:10], date_to.strip()[:10]
        if any(d and not DAY.match(d) for d in (date_from, date_to)):
            raise HTTPException(400, "Dates must look like 2026-11-01.")
        if not lead_id and not (date_from or date_to):
            raise HTTPException(400, "Pick one lead or a date range.")
        try:
            data, counts = audit_pack.build(store, lead_id, date_from, date_to)
        except LookupError:
            raise HTTPException(404, "Lead not found") from None
        what = f"lead {lead_id}" if lead_id else f"{date_from or 'start'} to {date_to or 'today'}"
        audit.record(store, getattr(request.state, "user", None), "audit.pack", what,
                     ", ".join(f"{k} {v}" for k, v in counts.items()), {"lead_id": lead_id} if lead_id else None)
        name = f"audit-pack-{lead_id or (date_from or 'start') + '_' + (date_to or 'today')}.zip"
        return Response(data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @router.get("/api/leads/{lead_id}/consent")
    def consent(lead_id: str):
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None or not workspaces.visible(lead):
            raise HTTPException(404, "Lead not found")
        return consent_view.build(store, lead)

    return router
