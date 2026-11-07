"""WhatsApp routes (practice mode)."""
import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .audit import actor_of
from .growth_core import public_base_url
from .whatsapp import TEMPLATES, WhatsAppError, send

BASE_URL = os.environ.get("OUTREACH_BASE_URL", "http://localhost:8000")


class WhatsAppBody(BaseModel):
    template: str
    text: str = ""


def make_router(store, present) -> APIRouter:
    router = APIRouter()

    @router.post("/api/leads/{lead_id}/whatsapp")
    def send_whatsapp(lead_id: str, body: WhatsAppBody, request: Request):
        who = actor_of(getattr(request.state, "user", None))
        me = who["name"] if who["email"] else ""          # no login (local use): fall back to the team name in Settings
        try:
            return present(send(store, lead_id, body.template, body.text, datetime.now(timezone.utc), public_base_url(store, BASE_URL), sender=me))
        except LookupError:
            raise HTTPException(404, "Lead not found")
        except WhatsAppError as exc:
            raise HTTPException(400, str(exc))

    @router.get("/api/whatsapp/outbox")
    def whatsapp_outbox():
        from . import workspaces
        return list(reversed(workspaces.filter_by_lead(store, store.read("whatsapp_outbox"))))

    @router.get("/api/whatsapp/status")
    def whatsapp_status():
        """Practice or real: real only when a WhatsApp provider is connected AND "WhatsApp for real" is on."""
        from .channels.config import config_for
        cfg = config_for(store, "whatsapp")
        live = bool(cfg and store.read("settings").get("live_whatsapp"))
        return {"mode": "live" if live else "practice", "connected": bool(cfg), "provider": (cfg or {}).get("provider") or "",
                "live_whatsapp": bool(store.read("settings").get("live_whatsapp"))}

    @router.get("/api/whatsapp/templates")
    def templates():
        return TEMPLATES

    return router
