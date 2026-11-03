"""Outreach routes: sequence enrolment/run, outbox, replies, unsubscribe, scraper, settings."""
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from html import escape
from urllib.parse import quote, urlparse

from .auth import PRIVATE_SETTINGS
from .growth_core import public_base_url
from .inbound import _rates, apply_reply, suppress as _suppress_email
from .scraper import extract_contacts, fetch_page
from .channels.netguard import is_public_host
from .privacy import is_blocked, record_consent
from .senders import email_ready
from .unsubscribe import legacy_allowed, log_legacy_use, parse as parse_unsub
from .sequence_runner import NO_EMAIL_STAGES, clear_practice, get_settings, resume_lead, run_due, sending_paused

BASE_URL = os.environ.get("OUTREACH_BASE_URL", "http://localhost:8000")
IGNORE_BUSINESS_HOURS = os.environ.get("OUTREACH_IGNORE_BUSINESS_HOURS") == "1"  # local testing only
# Only tests may set the clock; otherwise a caller could skip business hours or the daily cap.
ALLOW_NOW_OVERRIDE = os.environ.get("OUTREACH_TEST_MODE") == "1"
HIDDEN_SETTINGS = PRIVATE_SETTINGS          # the server secret and password hashes never leave the server
NOT_PUBLIC_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
CAP_LIMITS = {"daily_cap": (1, 500), "per_run_cap": (1, 50), "retention_days_cold": (30, 3650)}


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
    sender_company: Optional[str] = None
    sender_address: Optional[str] = None
    sender_website: Optional[str] = None
    sender_contact_email: Optional[str] = None
    retention_days_cold: Optional[int] = None
    privacy_url: Optional[str] = None
    whatsapp_template_params: Optional[dict] = None       # {template: how many values it takes, or their names in order}


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
    _suppress_email(store, email)


def live_blockers(store, base_url: str) -> list:
    """Why real sending can't be switched on yet (empty when it can)."""
    settings = store.read("settings")
    url = public_base_url(store, base_url)
    parsed = urlparse(url)
    host = parsed.hostname or ""
    blockers = []
    if parsed.scheme != "https" or not host or host in NOT_PUBLIC_HOSTS or not is_public_host(host):
        blockers.append("Set a public https web address under Setup (unsubscribe links must open for recipients).")
    if not email_ready(store):
        blockers.append("Connect the cold-email sending domain under Integrations.")
    if not (settings.get("sender_company") or "").strip():
        blockers.append("Add your company name (it goes in every email footer, as anti-spam rules require).")
    elif not (settings.get("sender_address") or settings.get("sender_website") or settings.get("sender_contact_email") or "").strip():
        blockers.append("Add a postal address, or your website and a contact email, for the email footer.")
    return blockers


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _event(kind: str, text: str) -> dict:
    return {"at": _now_iso(), "kind": kind, "text": text}


ALREADY_ENROLLED = "This lead is already in the email sequence; it is never restarted, so no one gets Email 1 twice"


class EnrollRefused(ValueError):
    """The lead may not be put into the email sequence; the message says why in plain words."""


def enroll_lead(store, lead_id: str, note: str = "Enrolled in email sequence") -> dict:
    """Start one lead's email sequence. The only way in: the Enrol button, bulk enrol and campaigns all use it.

    Refuses leads without an email, leads that may not be emailed (unsubscribed, disqualified, not now, customers),
    suppressed addresses, and leads already in the sequence (it is never restarted). The checks run again on the
    latest saved copy, so a reply or unsubscribe saved a moment ago still wins. Sending itself stays with the
    sequence engine and its caps, warm-up, business hours and practice mode. Raises LookupError or EnrollRefused.
    """
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None:
        raise LookupError("Lead not found")
    if not lead.get("email"):
        raise EnrollRefused("This lead has no valid email")
    if lead["stage"] in NO_EMAIL_STAGES:
        raise EnrollRefused(f"Lead is {lead['stage']} and cannot be emailed")
    if lead.get("sequence"):
        raise EnrollRefused(ALREADY_ENROLLED)
    if is_blocked(store, lead["email"]):
        raise EnrollRefused("This address is on the suppression list (unsubscribed or bounced)")
    sequence = {"enrolled_at": _now_iso(), "sent_days": [], "stopped": None}
    applied = {}

    def start(l: dict):
        if l.get("sequence") or l["stage"] in NO_EMAIL_STAGES or not l.get("email"):
            return None
        if is_blocked(store, l["email"]):             # an unsubscribe / bounce / erasure saved since the checks above
            applied["blocked"] = True
            return None
        applied["ok"] = True
        return {**l, "sequence": sequence, "timeline": [*l["timeline"], _event("sequence", note)]}
    saved = store.update_lead(lead_id, start)
    if applied.get("blocked"):
        raise EnrollRefused("This address is on the suppression list (unsubscribed or bounced)")
    if not applied:
        raise EnrollRefused(ALREADY_ENROLLED)
    return saved


def make_router(store, build_lead, present, email_sender, ai_transport=None) -> APIRouter:
    router = APIRouter()

    def settings() -> dict:
        shown = {k: v for k, v in get_settings(store).items() if k not in HIDDEN_SETTINGS}   # server_secret never leaves the server
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
        if "whatsapp_template_params" in changes:
            from .whatsapp import PARAM_ORDER
            ok = lambda v: (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= len(PARAM_ORDER)) or (
                isinstance(v, list) and all(n in PARAM_ORDER for n in v))
            if not all(isinstance(k, str) and ok(v) for k, v in (changes["whatsapp_template_params"] or {}).items()):
                raise HTTPException(400, "WhatsApp template values must be a count (0-4) or a list of: " + ", ".join(PARAM_ORDER))
        if "privacy_url" in changes:
            changes["privacy_url"] = changes["privacy_url"].strip()
            parsed = urlparse(changes["privacy_url"])
            if changes["privacy_url"] and (parsed.scheme != "https" or not parsed.hostname or len(changes["privacy_url"]) > 500):
                raise HTTPException(400, "The privacy notice link must be a full https:// address.")
        for key, (low, high) in CAP_LIMITS.items():
            if key in changes and not low <= changes[key] <= high:
                raise HTTPException(400, f"{key.replace('_', ' ').capitalize()} must be between {low} and {high}.")
        store.update("settings", lambda current: {**current, **changes})
        if changes.get("live_sending") and (blockers := live_blockers(store, BASE_URL)):
            store.update("settings", lambda current: {**current, "live_sending": False})
            raise HTTPException(400, "Real sending stays off until: " + " ".join(blockers))
        return settings()

    @router.get("/api/sending/status")
    def sending_status():
        now = datetime.now(timezone.utc)
        live = bool(get_settings(store).get("live_sending")) and bool(email_ready(store))
        return {"mode": "live" if live else "practice", "paused": sending_paused(store, now), "rates": _rates(store, now),
                "live_blockers": live_blockers(store, BASE_URL),
                "stuck": [l["id"] for l in store.read("leads") if (l.get("sequence") or {}).get("in_flight")],
                "send_failed": [l["id"] for l in store.read("leads") if (l.get("sequence") or {}).get("stopped") == "send_failed"]}

    @router.post("/api/sending/resume")
    def resume_sending():
        store.update("settings", lambda current: {k: v for k, v in current.items() if k != "sending_paused"})
        return sending_status()

    @router.post("/api/sequences/resume-failed")
    def resume_failed():
        ids = [l["id"] for l in store.read("leads") if (l.get("sequence") or {}).get("stopped") == "send_failed"]
        for lead_id in ids:
            resume_lead(store, lead_id)
        return {"resumed": len(ids)}

    @router.post("/api/leads/{lead_id}/sequence/resume")
    def resume_one(lead_id: str):
        find(lead_id)
        lead = resume_lead(store, lead_id)
        if lead is None:
            raise HTTPException(400, "Only a sequence stopped by send failures can be resumed. Replies and unsubscribes stay stopped.")
        return present(lead)

    @router.post("/api/leads/{lead_id}/sequence/clear-stuck")
    def clear_stuck(lead_id: str):
        """A send was interrupted (crash). Check the provider's log first; the retry reuses the same idempotency key."""
        find(lead_id)
        lead = store.update_lead(lead_id, lambda l: {**l, "sequence": {k: v for k, v in (l.get("sequence") or {}).items() if k != "in_flight"},
                                                     "timeline": [*l["timeline"], _event("sequence", "Interrupted send cleared by a person")]}
                                 if (l.get("sequence") or {}).get("in_flight") else None)
        return present(lead)

    @router.post("/api/practice/clear")
    def practice_clear():
        return clear_practice(store)

    @router.get("/api/inbox")
    def inbox():
        return list(reversed(store.read("inbox")))[:500]

    @router.post("/api/leads/{lead_id}/enroll")
    def enroll(lead_id: str):
        try:
            return present(enroll_lead(store, lead_id))
        except LookupError:
            raise HTTPException(404, "Lead not found")
        except EnrollRefused as exc:
            raise HTTPException(400, str(exc))

    @router.post("/api/sequences/run")
    def run_sequences(body: RunBody):
        now = resolve_now(body.now)
        return run_due(store, now, public_base_url(store, BASE_URL), email_sender, ignore_hours=IGNORE_BUSINESS_HOURS)

    @router.get("/api/outbox")
    def get_outbox():
        return list(reversed(store.read("outbox")))

    @router.post("/api/leads/{lead_id}/reply")
    def log_reply(lead_id: str, body: ReplyBody):
        find(lead_id)
        result = apply_reply(store, lead_id, body.text, "email", ai_transport, source="pasted")
        refreshed, j = find(lead_id)
        return {**result, "lead": present(refreshed[j])}

    def unsubscribe_target(token: str) -> tuple:
        """(lead_id, legacy) for a valid link; 404 for a wrong signature, an unknown lead or an expired unsigned link."""
        lead_id, signed = parse_unsub(store, token)
        if signed is False or not any(l["id"] == lead_id for l in store.read("leads")):
            raise HTTPException(404, "This link is not valid")
        if signed is None:
            if not legacy_allowed(store, lead_id):
                raise HTTPException(404, "This link is not valid")
            log_legacy_use(lead_id)
        return lead_id, signed is None

    @router.get("/u/{token}", response_class=HTMLResponse)
    def unsubscribe_page(token: str):
        unsubscribe_target(token)
        action = escape(f"/u/{quote(token, safe='.')}", quote=True)
        return ("<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<div style='font-family:sans-serif;max-width:420px;margin:15vh auto;text-align:center'>"
                "<p>Stop all emails from BookMyInterview outreach?</p>"
                f"<form method='post' action='{action}'><button style='padding:10px 18px;font-size:16px'>Yes, unsubscribe me</button></form>"
                "</div>")

    @router.post("/u/{token}", response_class=HTMLResponse)
    def unsubscribe(token: str):
        lead_id, legacy = unsubscribe_target(token)
        how = "Clicked unsubscribe link" + (" (older unsigned link)" if legacy else "")

        def unsubscribe_lead(lead: dict):
            if lead["stage"] == "unsubscribed":
                return None
            seq = lead.get("sequence")
            return {**lead, "stage": "unsubscribed", "consent": None, "consent_withdrawn_at": _now_iso(),
                    "timeline": with_event(lead, "unsubscribe", how),
                    **({"sequence": {**seq, "stopped": "unsubscribed"}} if seq else {})}
        was = next((l["stage"] for l in store.read("leads") if l["id"] == lead_id), None)
        lead = store.update_lead(lead_id, unsubscribe_lead)
        _suppress(store, lead.get("email"))
        if was != "unsubscribed":
            record_consent(store, lead_id, "withdrawn", "email", "", how.lower())
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
            built = [build_lead({"company": body.company, "email": email, "website": body.url}, f"web_scraper:{body.url}")
                     for email in found["emails"]]
            added = len(store.add_leads([lead for lead in built if lead]))
        return {**found, "added": added}

    return router
