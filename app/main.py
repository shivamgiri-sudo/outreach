"""Outreach OS API."""
import hmac
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import integrations
from .lead_model import build_lead as _build_lead, contactless_match as _contactless_match, event as _event, merge_contact as _merge_contact, now_iso as _now
from .prompt_gen import build_prompt, build_prompt_body, with_guardrails
from .ai import ai_write_prompt
from .scoring import score_lead
from .stages import ALL_STAGES, STAGES, can_move
from .activation import next_nudge
from .enrich import polite_fetch
from .routes_enrich import make_router as make_enrich_router
from fastapi import Request
from fastapi.responses import JSONResponse

from . import auth
from .routes_auth import LOCAL_OWNER, make_router as make_auth_router, role_guard
from .web_security import make_limiters, rate_limit_middleware, security_headers_middleware
from .autorun import start_background
from .http_util import post_json
from . import routes_growth
from .growth_core import webhook_secret
from .routes_setup import make_router as make_setup_router
from .senders import make_email_sender, make_transactional_sender
from .routes_workflows import make_router as make_workflows_router
from .routes_flows import make_router as make_flows_router
from .workflows import fire
from .routes_insights import make_router as make_insights_router
from .routes_today import make_router as make_today_router
from .routes_providers import make_router as make_providers_router
from .routes_activities import make_router as make_activities_router
from .routes_linkedin import make_router as make_linkedin_router
from . import extension_api
from .routes_extension import make_router as make_extension_router
from .routes_compliance import make_router as make_compliance_router
from .privacy import lead_hashes, record_consent as ledger_consent
from .activities import close_call_tasks, on_became_interested
from .routes_whatsapp import make_router as make_whatsapp_router
from .routes_growth import make_router as make_growth_router
from .routes_jobs import make_router as make_jobs_router
from .routes_campaigns import make_router as make_campaigns_router
from .sources.routes import make_router as make_sources_router
from .campaigns import run_due_campaigns
from .routes_find_leads import make_router as make_find_leads_router
from .routes_hiring_engine import make_router as make_hiring_engine_router
from .routes_reply_inbox import make_router as make_reply_inbox_router
from .routes_icp import make_router as make_icp_router
from .routes_signals import make_router as make_signals_router
from .routes_linkedin_agent import make_router as make_linkedin_agent_router
from .providers import get_json
from .senders import make_transactional_sender
from .routes_channels import make_router as make_channels_router
from .channels.http import request as channel_request
from .channels.poller import start_imap_poller
from .channels.webhooks import gate_allows
from . import capture_connectors, inbound_auth, source_names, source_stats
from .routes_inbound import make_router as make_inbound_router, run_sheet_syncs
from .routes_capture import make_router as make_capture_router
from .routes_import import make_router as make_import_router
from .routes_exports import make_router as make_exports_router
from .lead_import import quick_rows
from .routes_outreach import make_router
from .store import Store
from .suggest import suggest_next
from .unsubscribe import ensure_cutover

BASE_URL = os.environ.get("OUTREACH_BASE_URL", "http://localhost:8000")
PUBLIC_API_PATHS = ("/api/login", "/api/auth/status")
def open_store(data_file):
    """JSON file by default; MySQL when OUTREACH_DATABASE_URL is set (the data file path is then only used for restore copies)."""
    url = os.environ.get("OUTREACH_DATABASE_URL", "").strip()
    if url:
        from .store_mysql import MySqlStore
        return MySqlStore(data_file, url)
    return Store(data_file)


DEFAULT_DATA_FILE = Path(os.environ.get("OUTREACH_DATA_FILE") or Path(__file__).resolve().parent.parent / "data" / "store.json")


class ImportBody(BaseModel):
    csv: str
    source: str = "manual"


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
    use_ai: bool = False


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
    return {**lead, "source_id": lead.get("source_id") or source_names.source_id(lead.get("source")), "score": scored["score"], "band": scored["band"], "signals": scored["signals"],
            "suggestion": suggest_next(lead["stage"], scored["band"]),
            "nudge": next_nudge(activation, datetime.now(timezone.utc)) if activation else None}


def create_app(data_file: Path = DEFAULT_DATA_FILE, fetcher=polite_fetch, admin_password: Optional[str] = None,
               email_transport=post_json, ai_transport=post_json, apollo_transport=post_json,
               channel_transport=None, smtp_factory=None, imap_factory=None, resolver=None,
               inbound_processor=None, delivery_processor=None, campaign_transports=None,
               capture_graph_transport=None, capture_sheet_fetcher=None, signal_fetch=None) -> FastAPI:
    password = admin_password if admin_password is not None else os.environ.get("OUTREACH_ADMIN_PASSWORD", "")
    limiter = auth.LoginLimiter()
    docs = os.environ.get("OUTREACH_ENABLE_DOCS") == "1"
    app = FastAPI(title="Outreach OS", dependencies=[Depends(role_guard)], docs_url="/docs" if docs else None,
                  redoc_url="/redoc" if docs else None, openapi_url="/openapi.json" if docs else None)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Renewed-Token"])
    store = open_store(data_file)
    app.state.store = store
    ensure_cutover(store)                     # signed unsubscribe links start now; older unsigned ones get 60 days
    source_names.backfill(store)              # adds source_id next to source on older leads (idempotent, never rewrites source)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        open_path = (not path.startswith("/api/") or path in PUBLIC_API_PATHS or request.method == "OPTIONS"
                     or (path == "/api/activation/event" and webhook_secret(store, routes_growth.WEBHOOK_SECRET)
                         and hmac.compare_digest(request.headers.get("x-webhook-secret", "").encode("utf-8", "replace"),
                                                 webhook_secret(store, routes_growth.WEBHOOK_SECRET).encode("utf-8")))
                     # Integrations hub: only the exact webhook / OAuth-callback routes, and only when their secret checks out.
                     or (path.startswith(("/api/webhooks/", "/api/oauth/")) and await gate_allows(store, request))
                     # Lead capture: only the exact public capture addresses, and only when the token / signature / key checks out.
                     or (path.startswith("/api/inbound/") and await inbound_auth.gate_allows(store, request)))
        if open_path:
            return await call_next(request)
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if token.startswith(extension_api.TOKEN_PREFIX):
            # Chrome-assistant pairing token: only the few /api/extension/ routes, never anything else.
            agent = extension_api.authenticate(store, password, token)
            if agent is None:
                return JSONResponse({"detail": "Login required"}, status_code=401)
            if not extension_api.path_allowed(request.method, path):
                return JSONResponse({"detail": "A pairing token can only be used by the Chrome assistant."}, status_code=403)
            if agent["sub"] == "local" and not _is_local(request):
                return JSONResponse({"detail": "This app has no password yet, so it only answers on this computer."}, status_code=403)
            request.state.user = agent
            return await call_next(request)
        if not auth.auth_on(store, password):
            if not _is_local(request):
                return JSONResponse({"detail": "This app has no password yet, so it only answers on this computer. "
                                               "Set OUTREACH_ADMIN_PASSWORD before opening it to anyone else."}, status_code=403)
            request.state.user = LOCAL_OWNER
            return await call_next(request)
        user = auth.authenticate(store, password, token)
        if user is None:
            return JSONResponse({"detail": "Login required"}, status_code=401)
        request.state.user = user
        response = await call_next(request)
        renewed = auth.renewal(store, user) if path != "/api/logout" else None
        if renewed:
            response.headers["X-Renewed-Token"] = renewed
        return response

    # Outer layers (added last, run first): rate limits, then security headers on every response, even a 429 or 401.
    app.middleware("http")(rate_limit_middleware(make_limiters()))
    app.middleware("http")(security_headers_middleware(docs))
    app.include_router(make_auth_router(store, password, limiter))

    email_sender = make_email_sender(store, email_transport, BASE_URL, channel_transport, smtp_factory)
    transactional_sender = make_transactional_sender(store, email_transport, channel_transport, smtp_factory)

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
        leads = source_stats.real([_present(lead) for lead in store.read("leads")])     # labelled test leads are not counted
        by_stage = {s: 0 for s in ALL_STAGES}
        for lead in leads:
            by_stage[lead["stage"]] += 1
        setup = integrations.describe(store.read("integrations"))
        return {
            "total_leads": len(leads),
            "by_source": source_stats.by_source(leads),
            **source_stats.inbound_counts(leads),
            "inbound_connectors": sum(1 for c in capture_connectors.all_connectors(store) if c.get("enabled", True)),
            "hot": sum(1 for lead in leads if lead["band"] == "hot"),
            "by_stage": by_stage,
            "funnel": [{"stage": s, "count": by_stage[s]} for s in STAGES],
            "setup_done": sum(1 for i in setup if i["status"] == "configured"),
            "setup_total": len(setup),
            "reminders": [
                {"lead_id": lead["id"], "company": lead["company"], "text": lead["suggestion"]["action"]}
                for lead in leads if lead["band"] == "hot" and lead["stage"] not in ("customer", "advocate", "unsubscribed", "disqualified")
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
        """Quick paste: the simple importer, now reading Apollo, Sales Navigator and other column names too."""
        try:
            rows = quick_rows(body.csv) if body.csv.strip() else []
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        stats = {}
        erased = set(store.read("suppressed_hashes"))
        blocked = set(store.read("suppressed"))      # do-not-contact list, as the smart import skips it

        def merge_in(leads: list) -> list:
            keys = lambda l: {l["dedupe_key"], *(f"{k}:{l.get(k)}" for k in ("email", "phone", "domain"))}   # merges re-key
            seen = set().union(*map(keys, leads))
            created = duplicates = rejected = merged = suppressed = 0
            for row in rows:
                lead = _build_lead(row, body.source)
                if lead is None:
                    rejected += 1
                elif lead_hashes(lead) & erased or (lead.get("email") or "") in blocked:
                    suppressed += 1                  # this person asked for their data to be erased
                elif lead["dedupe_key"] in seen:
                    duplicates += 1
                elif (target := _contactless_match(leads, lead)) is not None:
                    leads[target] = _merge_contact(leads[target], lead)
                    seen |= keys(leads[target]) | {lead["dedupe_key"]}
                    merged += 1
                else:
                    seen.add(lead["dedupe_key"])
                    leads.append(lead)
                    created += 1
            stats.update(created=created, duplicates=duplicates, rejected=rejected, merged=merged, **({"suppressed": suppressed} if suppressed else {}))
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
        if body.stage == "unsubscribed":
            ledger_consent(store, lead_id, "withdrawn", "any", "", "moved to Unsubscribed by a person")
        fire(store, "stage_change", body.stage, lead_id)
        return _present(next(l for l in store.read("leads") if l["id"] == lead_id))

    @app.post("/api/leads/{lead_id}/consent")
    def record_consent(lead_id: str, body: ConsentBody):
        text = body.text.strip()
        if len(text) < 3:
            raise HTTPException(400, "Write what they said and how (for example: 'Yes on a call with Priya, 3 Nov').")
        saved = change_lead(lead_id, lambda lead: {
            **lead, "consent": {"text": text[:500], "at": _now(), "source": body.source or "recorded by a person",
                                "channel": body.channel or "any"},
            "consent_withdrawn_at": None,
            "timeline": [*lead["timeline"], _event("consent", f"Said yes: {text[:500]}")]})
        ledger_consent(store, lead_id, "given", body.channel or "any", text, body.source or "recorded by a person")
        return saved

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
        if body.mode not in next(i for i in integrations.CATALOGUE + integrations.SOURCE_CATALOGUE if i["id"] == integration_id)["modes"]:
            raise HTTPException(400, "This connection does not offer that mode")
        saved = store.read("integrations")
        existing = saved.get(integration_id, {}).get("values", {})
        merged = {**existing, **{k: v for k, v in body.values.items() if v and not str(v).startswith("••••")}}
        # keep hub data (other providers, last test, tokens) stored next to mode / values
        store.write("integrations", {**saved, integration_id: {**saved.get(integration_id, {}), "mode": body.mode, "values": merged}})
        return next(i for i in integrations.describe(store.read("integrations"), all_cards=True) if i["id"] == integration_id)

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
        spec = body.model_dump()
        if body.use_ai:
            written = ai_write_prompt(store, build_prompt_body(spec), ai_transport)
            if written:
                return {"prompt": with_guardrails(written), "ai": True}
            return {"prompt": build_prompt(spec), "ai": False,
                    "note": "The AI writer is not connected or did not answer, so the standard template was used."}
        return {"prompt": build_prompt(spec), "ai": False}

    app.include_router(make_router(store, _build_lead, _present, email_sender, ai_transport))
    app.include_router(make_jobs_router(store, _build_lead, _present))
    app.include_router(make_growth_router(store, _build_lead, _present))
    app.include_router(make_enrich_router(store, fetcher, apollo_transport))
    app.include_router(make_workflows_router(store, ai_transport))
    app.include_router(make_flows_router(store, email_sender, channel_transport, resolver, ai_transport))
    app.include_router(make_setup_router(store, email_transport, ai_transport, apollo_transport, BASE_URL, routes_growth.WEBHOOK_SECRET, lambda: bool(password),
                                           source_transports={"fetch": fetcher, **(campaign_transports or {})}))
    app.include_router(make_whatsapp_router(store, _present))
    app.include_router(make_insights_router(store, _present))
    app.include_router(make_today_router(store, _present))
    app.include_router(make_providers_router(store, fetcher))
    app.include_router(make_activities_router(store, transactional_sender))
    app.include_router(make_linkedin_router(store))
    app.include_router(make_extension_router(store))
    app.include_router(make_compliance_router(store))
    app.include_router(make_channels_router(store, channel_transport or channel_request, smtp_factory, imap_factory, resolver,
                                            BASE_URL, _present, inbound_processor, delivery_processor, transactional_sender))
    app.include_router(make_import_router(store))
    app.include_router(make_inbound_router(store, BASE_URL, capture_graph_transport, capture_sheet_fetcher))
    app.include_router(make_capture_router(store, BASE_URL))
    app.include_router(make_exports_router(store, _present))
    # Campaigns: Apollo / Google Places / Firecrawl POSTs, Hunter / verifier GETs, careers pages and the crawler.
    campaign_io = {"post": apollo_transport, "get": get_json, "fetch": fetcher, **(campaign_transports or {})}
    app.include_router(make_campaigns_router(store, campaign_io, _present, transactional_sender))
    app.include_router(make_find_leads_router(store, campaign_io, transactional_sender))
    app.include_router(make_hiring_engine_router(store, campaign_io))
    app.include_router(make_reply_inbox_router(store))
    app.include_router(make_icp_router(store, ai_transport))
    app.include_router(make_signals_router(store, signal_fetch, ai_transport))
    app.include_router(make_linkedin_agent_router(store, transactional_sender))
    app.include_router(make_sources_router(store, campaign_io))
    if os.environ.get("OUTREACH_AUTORUN") == "1":
        start_background(store, email_sender, capture_runner=lambda: run_sheet_syncs(store, capture_sheet_fetcher),
                         campaign_runner=lambda now: run_due_campaigns(
            store, now, campaign_io, campaign_notify, ignore_hours=os.environ.get("OUTREACH_IGNORE_BUSINESS_HOURS") == "1"))
        start_imap_poller(store, imap_factory)
    return app


app = create_app()
