"""Outreach routes: sequence enrolment/run, outbox, replies, unsubscribe, scraper, settings."""
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .growth_core import public_base_url
from .ai import ai_classify
from .replies import classify_reply
from .workflows import fire
from .scraper import extract_contacts, fetch_page
from .sequence_runner import NO_EMAIL_STAGES, get_settings, run_due

BASE_URL = os.environ.get("OUTREACH_BASE_URL", "http://localhost:8000")
IGNORE_BUSINESS_HOURS = os.environ.get("OUTREACH_IGNORE_BUSINESS_HOURS") == "1"  # local testing only
# Only tests may set the clock; otherwise a caller could skip business hours or the daily cap.
ALLOW_NOW_OVERRIDE = os.environ.get("OUTREACH_TEST_MODE") == "1"
EARLY_STAGES = ("new", "verified", "enriched")
# A reply may only move a lead that is still being prospected. Leads further on (or closed) keep their stage;
# the reply is logged and a person is asked to look.
REPLY_CAN_MOVE = EARLY_STAGES + ("contacted", "engaged", "interested", "not_now")
NOT_A_REPLY = ("auto_reply", "bounce")


class RunBody(BaseModel):
    now: Optional[str] = None


class ReplyBody(BaseModel):
    text: str


class SettingsBody(BaseModel):
    daily_cap: Optional[int] = None
    sender_name: Optional[str] = None
    live_sending: Optional[bool] = None
    public_base_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    confirm_bmi_limits: Optional[bool] = None
    email1_include_link: Optional[bool] = None
    per_run_cap: Optional[int] = None


class ScrapeBody(BaseModel):
    url: str = ""
    html: str = ""
    company: str = ""
    add_leads: bool = False


def resolve_now(raw: Optional[str]) -> datetime:
    if raw and ALLOW_NOW_OVERRIDE:
        return datetime.fromisoformat(raw)
    return datetime.now(timezone.utc)


def _suppress(store, email: Optional[str]) -> None:
    if email:
        store.update("suppressed", lambda rows: rows if email in rows else [*rows, email])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event(kind: str, text: str) -> dict:
    return {"at": _now_iso(), "kind": kind, "text": text}


def make_router(store, build_lead, present, email_sender, ai_transport=None) -> APIRouter:
    router = APIRouter()

    def settings() -> dict:
        shown = dict(get_settings(store))
        secret = shown.get("webhook_secret")
        if secret:
            shown["webhook_secret"] = "••••" + secret[-4:]
        return shown

    def find(lead_id: str) -> tuple:
        leads = store.read("leads")
        for i, lead in enumerate(leads):
            if lead["id"] == lead_id:
                return leads, i
        raise HTTPException(404, "Lead not found")

    def update(lead_id: str, **changes) -> dict:
        find(lead_id)
        saved = store.update("leads", lambda leads: [{**lead, **changes} if lead["id"] == lead_id else lead for lead in leads])
        return next(lead for lead in saved if lead["id"] == lead_id)

    def with_event(lead: dict, kind: str, text: str) -> list:
        return [*lead["timeline"], _event(kind, text)]

    @router.get("/api/settings")
    def read_settings():
        return settings()

    @router.put("/api/settings")
    def put_settings(body: SettingsBody):
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        if str(changes.get("webhook_secret", "")).startswith("••••"):
            del changes["webhook_secret"]
        if "public_base_url" in changes:
            changes["public_base_url"] = changes["public_base_url"].strip().rstrip("/")
        store.write("settings", {**store.read("settings"), **changes})
        return settings()

    @router.post("/api/leads/{lead_id}/enroll")
    def enroll(lead_id: str):
        leads, i = find(lead_id)
        lead = leads[i]
        if not lead.get("email"):
            raise HTTPException(400, "This lead has no valid email")
        if lead["stage"] in NO_EMAIL_STAGES:
            raise HTTPException(400, f"Lead is {lead['stage']} and cannot be emailed")
        if lead.get("sequence"):
            raise HTTPException(400, "This lead is already in the email sequence; it is never restarted, so no one gets Email 1 twice")
        sequence = {"enrolled_at": _now_iso(), "sent_days": [], "stopped": None}
        return present(update(lead_id, sequence=sequence, timeline=with_event(lead, "sequence", "Enrolled in email sequence")))

    @router.post("/api/sequences/run")
    def run_sequences(body: RunBody):
        now = resolve_now(body.now)
        return run_due(store, now, public_base_url(store, BASE_URL), email_sender, ignore_hours=IGNORE_BUSINESS_HOURS)

    @router.get("/api/outbox")
    def get_outbox():
        return list(reversed(store.read("outbox")))

    @router.post("/api/leads/{lead_id}/reply")
    def log_reply(lead_id: str, body: ReplyBody):
        leads, i = find(lead_id)
        lead = leads[i]
        label, ai_label = classify_reply(body.text), None
        if label == "other":
            ai_label = ai_classify(store, body.text, ai_transport)
            if ai_label and ai_label != "interested":
                label = ai_label
        changes, effect = reply_effect(lead, label, body.text)
        if ai_label == "interested":
            effect = "review"
        update(lead_id, timeline=with_event(lead, "reply", f"[{label}] {body.text}"), **changes)
        if label in ("unsubscribe", "bounce"):
            _suppress(store, lead.get("email"))
        if effect == "review" or label in ("question", "wrong_person", "other"):
            task = {"lead_id": lead_id, "kind": label if effect != "review" else "review", "text": body.text, "at": _now_iso()}
            store.update("tasks", lambda rows: [*rows, task])
        if effect == "applied":
            fire(store, "reply", label, lead_id)
        refreshed, j = find(lead_id)
        return {"label": label, "effect": effect, "ai_label": ai_label, "lead": present(refreshed[j])}

    def reply_effect(lead: dict, label: str, text: str) -> tuple:
        """What a reply changes. Returns (changes, effect) where effect is applied / logged / review."""
        seq = lead.get("sequence")
        stop = lambda why: {"sequence": {**seq, "stopped": why}} if seq else {}
        if label == "auto_reply":
            return {}, "logged"
        if label == "bounce":
            moved = {"stage": "disqualified"} if lead["stage"] != "unsubscribed" else {}
            return {**stop("bounced"), **moved}, "applied"
        engagement = {**lead["engagement"], "replies": lead["engagement"]["replies"] + 1}
        changes = {"engagement": engagement, **stop("replied")}
        if label == "unsubscribe":
            return {**changes, **stop("unsubscribed"), "stage": "unsubscribed"}, "applied"
        if lead["stage"] not in REPLY_CAN_MOVE:
            return changes, ("review" if label in ("interested", "not_interested", "not_now") else "logged")
        if label == "interested":
            return {**changes, "consent": {"text": text, "at": _now_iso(), "source": "email reply"}, "stage": "interested"}, "applied"
        if label == "not_interested":
            return {**changes, "stage": "disqualified"}, "applied"
        if label == "not_now":
            return {**changes, "stage": "not_now"}, "applied"
        if lead["stage"] in EARLY_STAGES + ("contacted",):
            return {**changes, "stage": "engaged"}, "applied"
        return changes, "applied"

    @router.get("/u/{lead_id}", response_class=HTMLResponse)
    def unsubscribe_page(lead_id: str):
        find(lead_id)
        return ("<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<div style='font-family:sans-serif;max-width:420px;margin:15vh auto;text-align:center'>"
                "<p>Stop all emails from BookMyInterview outreach?</p>"
                f"<form method='post' action='/u/{lead_id}'><button style='padding:10px 18px;font-size:16px'>Yes, unsubscribe me</button></form>"
                "</div>")

    @router.post("/u/{lead_id}", response_class=HTMLResponse)
    def unsubscribe(lead_id: str):
        leads, i = find(lead_id)
        lead = leads[i]
        if lead["stage"] != "unsubscribed":
            seq = lead.get("sequence")
            update(lead_id, stage="unsubscribed", timeline=with_event(lead, "unsubscribe", "Clicked unsubscribe link"),
                   **({"sequence": {**seq, "stopped": "unsubscribed"}} if seq else {}))
            _suppress(store, lead.get("email"))
        return "<p style='font-family:sans-serif'>You are unsubscribed and will not hear from us again.</p>"

    @router.post("/api/scrape/extract")
    def scrape(body: ScrapeBody):
        try:
            html = body.html or fetch_page(body.url)
        except (ValueError, PermissionError, OSError) as exc:
            raise HTTPException(400, str(exc))
        found = extract_contacts(html, body.url)
        added = 0
        if body.add_leads:
            leads = store.read("leads")
            seen = {lead["dedupe_key"] for lead in leads}
            for email in found["emails"]:
                lead = build_lead({"company": body.company, "email": email, "website": body.url}, f"web_scraper:{body.url}")
                if lead and lead["dedupe_key"] not in seen:
                    seen.add(lead["dedupe_key"])
                    leads.append(lead)
                    added += 1
            store.write("leads", leads)
        return {**found, "added": added}

    return router
