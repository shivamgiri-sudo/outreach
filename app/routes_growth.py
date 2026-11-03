"""Free-experience routes: demo links, activation events, referrals, pilots, partners, growth summary."""
import hmac
import os
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .activation import STEPS, apply_event, conversion_due, next_nudge
from .capacity import capacity_plan
from .demo import draft_kit, render_demo_html
from .growth_core import ensure_demo, get_offer, public_base_url, webhook_secret
from .hygiene import normalise_email
from .sequences import DEFAULT_SEQUENCE
from .workflows import fire

BASE_URL = os.environ.get("OUTREACH_BASE_URL", "http://localhost:8000")
WEBHOOK_SECRET = os.environ.get("OUTREACH_WEBHOOK_SECRET", "")
PARTNER_STATUSES = ("to_contact", "contacted", "active", "paused")


class EventBody(BaseModel):
    lead_id: str = ""
    email: str = ""
    event: str
    count: int = Field(default=1, ge=0)


class OfferBody(BaseModel):
    candidate_cap: Optional[int] = Field(default=None, ge=0)
    booking_cap: Optional[int] = Field(default=None, ge=0)
    trial_days: Optional[int] = Field(default=None, ge=1)
    referral_bonus_candidates: Optional[int] = Field(default=None, ge=0)
    mailboxes: Optional[int] = Field(default=None, ge=0)
    per_mailbox_per_day: Optional[int] = Field(default=None, ge=0)
    target_leads: Optional[int] = Field(default=None, ge=0)
    claim_url: Optional[str] = None


class ReferralBody(BaseModel):
    company: str
    contact_name: str = ""
    email: str = ""
    phone: str = ""


class PilotBody(BaseModel):
    applicants: int = Field(default=0, ge=0)


class PartnerBody(BaseModel):
    name: str
    kind: str = "community"
    contact: str = ""


class PartnerStatusBody(BaseModel):
    status: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(kind: str, text: str, now: datetime) -> dict:
    return {"at": now.isoformat(timespec="seconds"), "kind": kind, "text": text}


def make_router(store, build_lead, present) -> APIRouter:
    router = APIRouter()

    def find(lead_id: str) -> tuple:
        leads = store.read("leads")
        for i, lead in enumerate(leads):
            if lead["id"] == lead_id:
                return leads, i
        raise HTTPException(404, "Lead not found")

    def change(lead_id: str, fn) -> dict:
        saved = store.update_lead(lead_id, fn)
        if saved is None:
            raise HTTPException(404, "Lead not found")
        return saved

    def claim_url(lead: dict, offer: dict) -> str:
        query = urlencode({"ref": lead["referral_code"], "utm_source": "outreach", "utm_medium": "demo",
                           "utm_campaign": "free_first_role", "utm_content": lead["id"]})
        return f"{offer['claim_url']}?{query}"

    def record_event(lead: dict, event: str, count: int, now: datetime) -> dict:
        lead = ensure_demo(lead, now)
        activation = apply_event(lead["activation"], event, now, count)
        changes = {"activation": activation, "timeline": [*lead["timeline"], _event("activation", f"{event} → {activation['step']}", now)]}
        if event == "converted" and lead["stage"] not in ("unsubscribed", "disqualified"):
            changes["stage"] = "customer"       # a lead who opted out of messages keeps that stage; usage is still tracked
        return {**lead, **changes}

    # ---- offer / sequence ----
    def shown_offer() -> dict:
        return {k: v for k, v in get_offer(store).items() if k not in ("webhook_secret", "server_secret")}

    @router.get("/api/offer")
    def read_offer():
        return shown_offer()

    @router.put("/api/offer")
    def write_offer(body: OfferBody):
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        store.update("settings", lambda current: {**current, **changes})
        return shown_offer()

    @router.get("/api/sequence")
    def read_sequence():
        return DEFAULT_SEQUENCE

    # ---- demo link and page ----
    @router.post("/api/leads/{lead_id}/demo-link")
    def demo_link(lead_id: str):
        find(lead_id)
        lead = change(lead_id, lambda l: ensure_demo(l, _now()))
        return {"token": lead["demo_token"], "url": f"{public_base_url(store, BASE_URL)}/demo/{lead['demo_token']}"}

    def lead_by_token(token: str) -> dict:
        lead = next((l for l in store.read("leads") if l.get("demo_token") == token), None)
        if lead is None:
            raise HTTPException(404, "This link is not valid")
        return lead

    @router.get("/demo/{token}", response_class=HTMLResponse)
    def demo_page(token: str):
        """Show the sample. Opening is recorded by a script in the page, so email link scanners (which don't run
        scripts) can't fake a "demo opened"."""
        lead = lead_by_token(token)
        offer = get_offer(store)
        html = render_demo_html(lead, draft_kit(lead.get("role", "")), offer, claim_url(ensure_demo(lead, _now()), offer))
        beacon = (f"<script>fetch('/demo/{token}/opened',{{method:'POST'}}).catch(function(){{}});</script>")
        return html.replace("</body>", beacon + "</body>")

    @router.post("/demo/{token}/opened")
    def demo_opened(token: str):
        lead = lead_by_token(token)
        now = _now()
        if lead.get("activation", {}).get("step") in (None, "invited"):
            change(lead["id"], lambda l: record_event(l, "demo_opened", 1, now) if l.get("activation", {}).get("step") in (None, "invited") else None)
            fire(store, "activation", "demo_opened", lead["id"], now)
        return {"ok": True}

    # ---- activation events (called by the BMI product via webhook) ----
    @router.post("/api/activation/event")
    def activation_event(body: EventBody, x_webhook_secret: str = Header(default="")):
        secret = webhook_secret(store, WEBHOOK_SECRET)
        if secret and not hmac.compare_digest(x_webhook_secret.encode("utf-8", "replace"), secret.encode("utf-8")):
            raise HTTPException(401, "Bad webhook secret")
        email = normalise_email(body.email)
        match = next((l for l in store.read("leads")
                      if (body.lead_id and l["id"] == body.lead_id) or (email and l.get("email") == email)), None)
        if match is None:
            raise HTTPException(404, "No matching lead")
        try:
            saved = change(match["id"], lambda l: record_event(l, body.event, body.count, _now()))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        fire(store, "activation", body.event, saved["id"])
        return present(next(l for l in store.read("leads") if l["id"] == saved["id"]))

    # ---- referral ----
    @router.post("/api/leads/{lead_id}/referral")
    def referral(lead_id: str, body: ReferralBody):
        leads, i = find(lead_id)
        now = _now()
        referrer = ensure_demo(leads[i], now)
        new = build_lead({"company": body.company, "contact_name": body.contact_name, "email": body.email, "phone": body.phone},
                         f"referral:{referrer['referral_code']}", allow_company_key=True)
        if new is None:
            raise HTTPException(400, "That company or contact is already in the system")
        bonus = get_offer(store)["referral_bonus_candidates"]
        referred = {**new, "stage": "verified", "referred_by": referrer["id"], "bonus_candidates": bonus}
        if not store.add_leads([referred]):
            raise HTTPException(400, "That company or contact is already in the system")
        change(lead_id, lambda l: {**ensure_demo(l, now), "bonus_candidates": l.get("bonus_candidates", 0) + bonus,
                                   "timeline": [*l["timeline"], _event("referral", f"Referred {body.company}; +{bonus} free candidates", now)]})
        return present(referred)

    # ---- pilot on their real applicants ----
    @router.post("/api/leads/{lead_id}/pilot")
    def pilot(lead_id: str, body: PilotBody):
        leads, i = find(lead_id)
        now = _now()
        company = leads[i].get("company", "")
        store.update("tasks", lambda rows: [*rows, {"id": uuid.uuid4().hex[:10], "lead_id": lead_id, "kind": "pilot",
                                                    "at": now.isoformat(timespec="seconds"),
                                                    "text": f"Run free pilot on {body.applicants} applicants for {company}"}])
        return present(change(lead_id, lambda l: {
            **l, "pilot": {"applicants": body.applicants, "requested_at": now.isoformat(timespec="seconds")},
            "timeline": [*l["timeline"], _event("pilot", f"Free pilot on {body.applicants} existing applicants", now)]}))

    @router.get("/api/tasks")
    def tasks():
        return list(reversed(store.read("tasks")))

    # ---- partners ----
    @router.get("/api/partners")
    def list_partners():
        return store.read("partners")

    @router.post("/api/partners")
    def add_partner(body: PartnerBody):
        partner = {"id": uuid.uuid4().hex[:8], **body.model_dump(), "status": "to_contact", "created_at": _now().isoformat(timespec="seconds")}
        store.update("partners", lambda rows: [*rows, partner])
        return partner

    @router.post("/api/partners/{partner_id}/status")
    def partner_status(partner_id: str, body: PartnerStatusBody):
        if body.status not in PARTNER_STATUSES:
            raise HTTPException(400, "Unknown status")
        partners = store.read("partners")
        if partner_id not in {p["id"] for p in partners}:
            raise HTTPException(404, "Partner not found")
        store.write("partners", [{**p, "status": body.status} if p["id"] == partner_id else p for p in partners])
        return next(p for p in store.read("partners") if p["id"] == partner_id)

    # ---- summary ----
    @router.get("/api/growth")
    def growth():
        now, offer = _now(), get_offer(store)
        leads = [lead for lead in store.read("leads") if lead.get("activation")]
        funnel = {step: 0 for step in STEPS}
        nudges, due = [], []
        for lead in leads:
            funnel[lead["activation"]["step"]] += 1
            nudge = next_nudge(lead["activation"], now)
            if nudge:
                nudges.append({"lead_id": lead["id"], "company": lead["company"], **nudge})
            heads_up = conversion_due(lead["activation"], offer, now, lead.get("bonus_candidates", 0))
            if heads_up:
                due.append({"lead_id": lead["id"], "company": lead["company"], **heads_up})
        return {
            "offer": shown_offer(), "activation_funnel": funnel, "nudges": nudges, "conversion_due": due,
            "capacity": capacity_plan(offer["mailboxes"], offer["per_mailbox_per_day"], offer["target_leads"]),
            "referrals": sum(1 for lead in store.read("leads") if lead.get("referred_by")),
            "partners": len(store.read("partners")),
        }

    return router
