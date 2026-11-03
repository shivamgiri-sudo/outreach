"""Incoming webhooks (replies, bounces, WhatsApp, SMS, bookings) and the calendar OAuth flow."""
import os
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse

from ..growth_core import public_base_url
from . import config
from .inbound_parse import custom_messages, delivery_events, email_webhook, parse_body, sms_webhook, whatsapp_webhook
from .meetings import attach_booking, parse_booking
from .oauth import OAUTH_PROVIDERS, authorize_url, exchange_code, redirect_uri
from .registry import get_provider
from .security import sign_state, verify_state
from .webhooks import MAX_BODY_BYTES, verify

LOCAL_UI_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000")


def _origin(url: str) -> str:
    parts = urlparse(url or "")
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme in ("http", "https") and parts.netloc else ""


def default_inbound(store, msg):
    from .. import inbound  # the core branch replaces the stub; imported late so its version is used
    return inbound.process_inbound_message(store, msg)


def default_delivery(store, event):
    from .. import inbound
    return inbound.process_delivery_event(store, event)


def make_router(store, transport, default_base_url: str = "", inbound_processor=None, delivery_processor=None) -> APIRouter:
    router = APIRouter()
    process_inbound = inbound_processor or default_inbound
    process_delivery = delivery_processor or default_delivery
    base = lambda: public_base_url(store, default_base_url)

    def allowed_return(url: str) -> str:
        extra = tuple(o.strip() for o in os.environ.get("OUTREACH_UI_ORIGINS", "").split(",") if o.strip())
        origin = _origin(url)
        if origin and origin in (_origin(base()), *LOCAL_UI_ORIGINS, *extra):
            return url.split("#", 1)[0]
        return base() + "/"

    # ---------- webhooks ----------
    @router.get("/api/webhooks/{channel}/{provider}")
    def webhook_check(channel: str, provider: str, request: Request):
        """Meta's one-time 'verify token' check: echo the challenge when the token matches."""
        if not verify(store, channel, provider, "GET", request.headers, request.query_params, b""):
            raise HTTPException(403, "Verification failed")
        return PlainTextResponse(request.query_params.get("hub.challenge", ""))

    @router.post("/api/webhooks/{channel}/{provider}")
    async def webhook_in(channel: str, provider: str, request: Request):
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "Too large")
        if not verify(store, channel, provider, "POST", request.headers, request.query_params, body):
            raise HTTPException(401, "Bad webhook secret or signature")
        try:
            payload = parse_body(request.headers.get("content-type", ""), body)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        entry = config.entry_of(store.read("integrations"), channel)
        values = config.effective(get_provider(channel, provider), config.provider_values(channel, entry, provider))
        try:
            return _dispatch(channel, provider, payload, values)
        except Exception:  # the provider retries on a 5xx; details stay out of the reply
            return JSONResponse({"ok": False, "detail": "Processing failed"}, status_code=500)

    def _dispatch(channel: str, provider: str, payload, values: dict) -> dict:
        if channel == "email_events":
            events = delivery_events(provider, payload, values)
            for event in events:
                process_delivery(store, event)
            return {"ok": True, "events": len(events)}
        if channel == "calendar":
            results = [attach_booking(store, provider, b) for b in parse_booking(provider, payload)]
            return {"ok": True, "bookings": len(results), "matched": sum(1 for r in results if r.get("matched"))}
        if provider == "custom_webhook":
            mapping = get_provider(channel, provider)["mapping"]
            messages = custom_messages("email" if channel == "inbound_email" else channel, mapping, values, payload)
        elif channel == "inbound_email":
            messages = email_webhook(provider, payload)
        elif channel == "whatsapp":
            messages = whatsapp_webhook(provider, payload)
        else:
            messages = sms_webhook(provider, payload)
        results = [process_inbound(store, m) for m in messages]
        return {"ok": True, "messages": len(messages), "matched": sum(1 for r in results if isinstance(r, dict) and r.get("matched"))}

    # ---------- OAuth (Google, Microsoft) ----------
    @router.get("/api/oauth/{provider}/start")
    def oauth_start(provider: str, return_to: str = ""):
        if provider not in OAUTH_PROVIDERS:
            raise HTTPException(404, "Unknown provider")
        cfg = config.config_for(store, "calendar", provider)
        if not cfg:
            raise HTTPException(400, "Save the client ID and secret first.")
        state = sign_state(store, {"provider": provider, "return_to": allowed_return(return_to)})
        try:
            url = authorize_url(provider, cfg["values"], redirect_uri(base(), provider), state)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"url": url, "redirect_uri": redirect_uri(base(), provider)}

    @router.get("/api/oauth/{provider}/callback")
    def oauth_callback(provider: str, state: str = "", code: str = "", error: str = ""):
        payload = verify_state(store, state)
        if provider not in OAUTH_PROVIDERS or not payload or payload.get("provider") != provider:
            raise HTTPException(400, "This sign-in link expired or is not valid. Start again from the Integrations page.")
        back = allowed_return(payload.get("return_to", ""))

        def finish(**query) -> RedirectResponse:
            return RedirectResponse(f"{back}#integrations?{urlencode(query)}", status_code=303)
        if error or not code:
            return finish(oauth_error=(error or "no_code")[:60])
        cfg = config.config_for(store, "calendar", provider)
        if not cfg:
            return finish(oauth_error="not_set_up")
        try:
            tokens = exchange_code(provider, cfg["values"], code, redirect_uri(base(), provider), transport)
        except (PermissionError, OSError, ValueError):
            return finish(oauth_error="token_exchange_failed")
        config.set_tokens(store, "calendar", provider, tokens)
        config.record_test(store, "calendar", provider, {"ok": True, "message": "Connected."})
        return finish(connected=provider)

    return router
