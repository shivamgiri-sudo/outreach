"""Custom lead fields and industry presets.

GET  /api/lead-fields               everyone signed in (the Leads page, drawer and import need the definitions)
PUT  /api/lead-fields               owner only: replace the field list
PATCH /api/leads/{lead_id}/custom   change custom values on a lead (same permission as correcting contact details; audited)
GET  /api/presets                   the industry presets, with a short description each
POST /api/presets/{preset_id}/apply owner only: {overwrite?: bool}; returns what changed
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, auth, industry_presets, lead_fields


class FieldsBody(BaseModel):
    fields: list = Field(default_factory=list)


class CustomBody(BaseModel):
    values: dict = Field(default_factory=dict)
    reason: str = Field(default="", max_length=300)


class ApplyBody(BaseModel):
    overwrite: bool = False


def make_router(store, present) -> APIRouter:
    router = APIRouter()

    def actor(request: Request) -> dict:
        return audit.actor_of(getattr(request.state, "user", None))

    @router.get("/api/lead-fields")
    def get_fields():
        return {"fields": lead_fields.list_fields(store), "types": list(lead_fields.FIELD_TYPES),
                "lead_term": (store.read("settings").get("lead_terms") or {}).get("open_roles") or "Open roles"}

    @router.put("/api/lead-fields")
    def put_fields(body: FieldsBody, request: Request):
        try:
            fields = lead_fields.save_fields(store, body.fields, actor(request)["name"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        audit.record(store, getattr(request.state, "user", None), "settings.save", "Custom lead fields",
                     ", ".join(f["key"] for f in fields) or "(none)")
        return {"fields": fields}

    @router.patch("/api/leads/{lead_id}/custom")
    def edit_custom(lead_id: str, body: CustomBody, request: Request):
        user = getattr(request.state, "user", None)
        if user and not auth.can_edit_contacts(user):
            raise HTTPException(403, "Your login is not allowed to edit lead details. Ask an owner.")
        try:
            result = lead_fields.edit_custom(store, lead_id, body.values, actor(request), body.reason.strip())
        except LookupError:
            raise HTTPException(404, "Lead not found") from None
        except audit.EditRefused as exc:
            raise HTTPException(400, str(exc)) from None
        return {**present(result["lead"]), "changed": result["changed"]}

    @router.get("/api/presets")
    def list_presets():
        return {"presets": industry_presets.summaries(), "applied": store.read("settings").get("industry_preset") or None}

    @router.get("/api/presets/{preset_id}")
    def read_preset(preset_id: str):
        preset = industry_presets.get(preset_id)
        if preset is None:
            raise HTTPException(404, "Unknown preset")
        return preset

    @router.post("/api/presets/{preset_id}/apply")
    def apply(preset_id: str, request: Request, body: Optional[ApplyBody] = None):
        body = body or ApplyBody()
        try:
            result = industry_presets.apply(store, preset_id, actor(request)["name"], overwrite=body.overwrite)
        except LookupError:
            raise HTTPException(404, "Unknown preset") from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        audit.record(store, getattr(request.state, "user", None), "settings.save", f"Industry preset: {preset_id}",
                     "changed: " + (", ".join(result["changed"]) or "nothing") + ("; overwrite" if body.overwrite else ""))
        return result

    return router
