"""Outreach OS API."""
import csv
import hmac
import io
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import integrations
from .lead_model import build_lead as _build_lead, contactless_match as _contactless_match, event as _event, merge_contact as _merge_contact, now_iso as _now
from .prompt_gen import build_prompt
from .scoring import score_lead
from .stages import ALL_STAGES, STAGES, can_move
from .activation import next_nudge
from .enrich import polite_fetch
from .routes_enrich import make_router as make_enrich_router
from fastapi import Request
from fastapi.responses import JSONResponse

from .auth import LoginLimiter, make_token, password_matches, verify_token
from .autorun import start_background
from .http_util import post_json
from . import routes_growth
from .growth_core import webhook_secret
from .routes_setup import make_router as make_setup_router
from .senders import make_email_sender
from .routes_workflows import make_router as make_workflows_router
from .workflows import fire
from .routes_insights import make_router as make_insights_router
from .routes_today import make_router as make_today_router
from .routes_providers import make_router as make_providers_router
from .routes_activities import make_router as make_activities_router
from .routes_linkedin import make_router as make_linkedin_router
from .activities import close_call_tasks, on_became_interested
from .routes_whatsapp import make_router as make_whatsapp_router
from .routes_growth import make_router as make_growth_router
from .routes_jobs import make_router as make_jobs_router
from .routes_channels import make_router as make_channels_router
from .channels.http import request as channel_request
from .channels.poller import start_imap_poller
from .channels.webhooks import gate_allows
from .routes_outreach import make_router
from .store import Store
from .suggest import suggest_next

BASE_URL = os.environ.get("OUTREACH_BASE_URL", "http://localhost:8000")
PUBLIC_API_PATHS = ("/api/login", "/api/auth/status")
DEFAULT_DATA_FILE = Path(os.environ.get("OUTREACH_DATA_FILE") or Path(__file__).resolve().parent.parent / "data" / "store.json")


class ImportBody(BaseModel):
    csv: str
    source: str = "manual"


class LoginBody(BaseModel):
    password: str = ""


class StageBody(BaseModel):
    stage: str


class ConsentBody(BaseModel):
    text: str
    source: str = ""
    channel: str = ""


class NoteBody(BaseModel):
    text: str
    kind: str = "note"


class IntegrationBody(BaseModel):
    mode: str
    values: dict = {}


class StoryBody(BaseModel):
    title: str
    industry: str = ""
    result: str = ""
    quote: str = ""
    permission: bool = False


class PromptBody(BaseModel):
    channel: str = "email"
    industry: str = "any"
    role: str = ""
    tone: str = "warm"
    language: str = "English"
    goal: str = ""
    story: str = ""
    dos: str = ""
    donts: str = ""


LOCAL_HOSTS = ("127.0.0.1", "::1", "localhost", "testclient")
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip")


def _caller(request) -> str:
    return request.client.host if request.client else ""


def _is_local(request) -> bool:
    """A request from this computer that did not come through a proxy or tunnel.

    The Host header must be a local name too: a reverse proxy on the same machine that forwards a public domain
    (even without X-Forwarded-For) is refused, so a missing password can never expose the API to the internet.
    """
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
    return (_caller(request) in LOCAL_HOSTS and host in LOCAL_HOSTS + ("testserver",)
            and not any(h in request.headers for h in PROXY_HEADERS))


def _present(lead: dict) -> dict:
    scored = score_lead({**lead, "consent_yes": bool(lead.get("consent"))})
    activation = lead.get("activation")
    return {**lead, "score": scored["score"], "band": scored["band"], "signals": scored["signals"],
            "suggestion": suggest_next(lead["stage"], scored["band"]),
            "nudge": next_nudge(activation, datetime.now(timezone.utc)) if activation else None}


def create_app(data_file: Path = DEFAULT_DATA_FILE, fetcher=polite_fetch, admin_password: Optional[str] = None,
               email_transport=post_json, ai_transport=post_json, apollo_transport=post_json,
               channel_transport=None, smtp_factory=None, imap_factory=None, resolver=None,
               inbound_processor=None, delivery_processor=None) -> FastAPI:
    password = admin_password if admin_password is not None else os.environ.get("OUTREACH_ADMIN_PASSWORD", "")
    limiter = LoginLimiter()
    app = FastAPI(title="Outreach OS")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])
    store = Store(data_file)
    app.state.store = store

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        open_path = (not path.startswith("/api/") or path in PUBLIC_API_PATHS or request.method == "OPTIONS"
                     or (path == "/api/activation/event" and webhook_secret(store, routes_growth.WEBHOOK_SECRET)
                         and hmac.compare_digest(request.headers.get("x-webhook-secret", "").encode("utf-8", "replace"),
                                                 webhook_secret(store, routes_growth.WEBHOOK_SECRET).encode("utf-8")))
                     # Integrations hub: only the exact webhook / OAuth-callback routes, and only when their secret checks out.
                     or (path.startswith(("/api/webhooks/", "/api/oauth/")) and await gate_allows(store, request)))
        if open_path:
            return await call_next(request)
        if not password:
            if not _is_local(request):
                return JSONResponse({"detail": "This app has no password yet, so it only answers on this computer. "
                                               "Set OUTREACH_ADMIN_PASSWORD before opening it to anyone else."}, status_code=403)
            return await call_next(request)
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not verify_token(password, token):
            return JSONResponse({"detail": "Login required"}, status_code=401)
        return await call_next(request)

    @app.get("/api/auth/status")
    def auth_status():
        return {"auth_required": bool(password)}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request):
        if not password:
            return {"token": ""}
        caller = _caller(request)
        if limiter.locked(caller):
            raise HTTPException(429, "Too many wrong passwords. Wait a minute and try again.")
        if not password_matches(password, body.password):
            limiter.record_failure(caller)
            raise HTTPException(401, "Wrong password")
        limiter.reset(caller)
        return {"token": make_token(password)}

    email_sender = make_email_sender(store, email_transport, BASE_URL, channel_transport, smtp_factory)

    def find(lead_id: str) -> tuple:
        leads = store.read("leads")
        for i, lead in enumerate(leads):
            if lead["id"] == lead_id:
                return leads, i
        raise HTTPException(404, "Lead not found")

    def change_lead(lead_id: str, change) -> dict:
        """Apply `change` to the latest saved copy of one lead (atomic) and return it presented."""
        saved = store.update_lead(lead_id, change)
        if saved is None:
            raise HTTPException(404, "Lead not found")
        return _present(saved)

    @app.get("/api/dashboard")
    def dashboard():
        leads = [_present(lead) for lead in store.read("leads")]
        by_stage = {s: 0 for s in ALL_STAGES}
        for lead in leads:
            by_stage[lead["stage"]] += 1
        setup = integrations.describe(store.read("integrations"))
        return {
            "total_leads": len(leads),
            "hot": sum(1 for lead in leads if lead["band"] == "hot"),
            "by_stage": by_stage,
            "funnel": [{"stage": s, "count": by_stage[s]} for s in STAGES],
            "setup_done": sum(1 for i in setup if i["status"] == "configured"),
            "setup_total": len(setup),
            "reminders": [
                {"lead_id": lead["id"], "company": lead["company"], "text": lead["suggestion"]["action"]}
                for lead in leads if lead["band"] == "hot" and lead["stage"] not in ("customer", "advocate", "unsubscribed")
            ][:10],
        }

    @app.get("/api/leads")
    def list_leads():
        return sorted((_present(lead) for lead in store.read("leads")), key=lambda x: -x["score"])

    @app.get("/api/leads/{lead_id}")
    def get_lead(lead_id: str):
        leads, i = find(lead_id)
        return _present(leads[i])

    @app.post("/api/leads/import")
    def import_leads(body: ImportBody):
        rows = list(csv.DictReader(io.StringIO(body.csv)))
        stats = {}

        def merge_in(leads: list) -> list:
            seen = {lead["dedupe_key"] for lead in leads}
            created = duplicates = rejected = merged = 0
            for row in rows:
                lead = _build_lead(row, body.source)
                if lead is None:
                    rejected += 1
                elif lead["dedupe_key"] in seen:
                    duplicates += 1
                elif (target := _contactless_match(leads, lead)) is not None:
                    leads[target] = _merge_contact(leads[target], lead)
                    seen.add(lead["dedupe_key"])
                    merged += 1
                else:
                    seen.add(lead["dedupe_key"])
                    leads.append(lead)
                    created += 1
            stats.update(created=created, duplicates=duplicates, rejected=rejected, merged=merged)
            return leads
        store.update("leads", merge_in)
        return stats

    @app.post("/api/leads/{lead_id}/stage")
    def move_stage(lead_id: str, body: StageBody):
        find(lead_id)
        refused = {}

        def move(lead: dict):
            if not can_move(lead["stage"], body.stage, has_consent=bool(lead.get("consent"))):
                refused["from"] = lead["stage"]
                return None
            return {**lead, "stage": body.stage, "timeline": [*lead["timeline"], _event("stage", f"{lead['stage']} → {body.stage}")]}
        store.update_lead(lead_id, move)
        if refused:
            raise HTTPException(400, f"Cannot move from {refused['from']} to {body.stage}")
        if body.stage == "interested":
            on_became_interested(store, lead_id, "Moved to Interested")
        fire(store, "stage_change", body.stage, lead_id)
        return _present(next(l for l in store.read("leads") if l["id"] == lead_id))

    @app.post("/api/leads/{lead_id}/consent")
    def record_consent(lead_id: str, body: ConsentBody):
        text = body.text.strip()
        if len(text) < 3:
            raise HTTPException(400, "Write what they said and how (for example: 'Yes on a call with Priya, 3 Nov').")
        return change_lead(lead_id, lambda lead: {
            **lead, "consent": {"text": text[:500], "at": _now(), "source": body.source or "recorded by a person",
                                "channel": body.channel or "any"},
            "consent_withdrawn_at": None,
            "timeline": [*lead["timeline"], _event("consent", f"Said yes: {text[:500]}")]})

    @app.post("/api/leads/{lead_id}/note")
    def add_note(lead_id: str, body: NoteBody):
        saved = change_lead(lead_id, lambda lead: {**lead, "timeline": [*lead["timeline"], _event(body.kind, body.text)]})
        if body.kind == "call":
            close_call_tasks(store, lead_id, body.text)
            saved = _present(next(l for l in store.read("leads") if l["id"] == lead_id))
        return saved

    @app.get("/api/integrations")
    def list_integrations():
        return integrations.describe(store.read("integrations"))

    @app.put("/api/integrations/{integration_id}")
    def save_integration(integration_id: str, body: IntegrationBody):
        if integration_id not in integrations.known_ids():
            raise HTTPException(404, "Unknown integration")
        saved = store.read("integrations")
        existing = saved.get(integration_id, {}).get("values", {})
        merged = {**existing, **{k: v for k, v in body.values.items() if v and not str(v).startswith("••••")}}
        # keep hub data (other providers, last test, tokens) stored next to mode / values
        store.write("integrations", {**saved, integration_id: {**saved.get(integration_id, {}), "mode": body.mode, "values": merged}})
        return next(i for i in integrations.describe(store.read("integrations")) if i["id"] == integration_id)

    @app.get("/api/stories")
    def list_stories():
        return store.read("stories")

    @app.post("/api/stories")
    def add_story(body: StoryBody):
        story = {"id": uuid.uuid4().hex[:8], **body.model_dump(), "created_at": _now()}
        store.write("stories", [*store.read("stories"), story])
        return story

    @app.post("/api/prompts/generate")
    def generate_prompt(body: PromptBody):
        return {"prompt": build_prompt(body.model_dump())}

    app.include_router(make_router(store, _build_lead, _present, email_sender, ai_transport))
    app.include_router(make_jobs_router(store, _build_lead, _present))
    app.include_router(make_growth_router(store, _build_lead, _present))
    app.include_router(make_enrich_router(store, fetcher, apollo_transport))
    app.include_router(make_workflows_router(store, ai_transport))
    app.include_router(make_setup_router(store, email_transport, ai_transport, apollo_transport, BASE_URL, routes_growth.WEBHOOK_SECRET, lambda: bool(password)))
    app.include_router(make_whatsapp_router(store, _present))
    app.include_router(make_insights_router(store, _present))
    app.include_router(make_today_router(store, _present))
    app.include_router(make_providers_router(store, fetcher))
    app.include_router(make_activities_router(store))
    app.include_router(make_linkedin_router(store))
    app.include_router(make_channels_router(store, channel_transport or channel_request, smtp_factory, imap_factory, resolver,
                                            BASE_URL, _present, inbound_processor, delivery_processor))
    if os.environ.get("OUTREACH_AUTORUN") == "1":
        start_background(store, email_sender)
        start_imap_poller(store, imap_factory)
    return app


app = create_app()
