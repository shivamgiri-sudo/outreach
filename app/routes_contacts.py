"""Extra phones / emails on a lead and other people at its company (each change logged; see contact_extras)."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, auth, contact_extras
from .audit import EditRefused


class PointBody(BaseModel):
    kind: str = Field(max_length=10)
    value: str = Field(max_length=200)


class PersonBody(BaseModel):
    contact_name: Optional[str] = Field(default=None, max_length=200)
    title: Optional[str] = Field(default=None, max_length=200)
    email: Optional[str] = Field(default=None, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=60)
    linkedin_url: Optional[str] = Field(default=None, max_length=300)


def make_router(store, present) -> APIRouter:
    router = APIRouter()

    def who(request: Request) -> dict:
        user = getattr(request.state, "user", None)
        if user and not auth.can_edit_contacts(user):
            raise HTTPException(403, "Your login is not allowed to edit contact details. Ask an owner.")
        return audit.actor_of(user)

    def run(fn):
        try:
            saved = fn()
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except EditRefused as exc:
            raise HTTPException(400, str(exc)) from None
        lead = saved.get("lead") if isinstance(saved, dict) and "lead" in saved else saved
        return present(lead)

    @router.post("/api/leads/{lead_id}/contact-points")
    def add_point(lead_id: str, body: PointBody, request: Request):
        return run(lambda: contact_extras.add_point(store, lead_id, body.kind, body.value, who(request)))

    @router.post("/api/leads/{lead_id}/contact-points/remove")
    def remove_point(lead_id: str, body: PointBody, request: Request):
        return run(lambda: contact_extras.remove_point(store, lead_id, body.kind, body.value, who(request)))

    @router.post("/api/leads/{lead_id}/contact-points/primary")
    def primary(lead_id: str, body: PointBody, request: Request):
        actor = who(request)
        run(lambda: contact_extras.make_primary(store, lead_id, body.kind, body.value, actor))
        return present(next(l for l in store.read("leads") if l["id"] == lead_id))

    @router.post("/api/leads/{lead_id}/people")
    def add_person(lead_id: str, body: PersonBody, request: Request):
        return run(lambda: contact_extras.add_person(store, lead_id, body.model_dump(), who(request)))

    @router.put("/api/leads/{lead_id}/people/{person_id}")
    def change_person(lead_id: str, person_id: str, body: PersonBody, request: Request):
        return run(lambda: contact_extras.change_person(store, lead_id, person_id[:20], body.model_dump(), who(request)))

    @router.delete("/api/leads/{lead_id}/people/{person_id}")
    def remove_person(lead_id: str, person_id: str, request: Request):
        return run(lambda: contact_extras.remove_person(store, lead_id, person_id[:20], who(request)))

    return router
