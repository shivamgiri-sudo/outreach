"""Integrations hub API: channel catalogue, save / test / activate providers, webhook addresses, live switches.

Also mounts the webhook + OAuth routes (app/channels/routes_hooks.py) and the meetings routes
(app/channels/routes_meetings.py). External calls use the injected transport, SMTP and IMAP factories.
"""
from typing import Optional, Union
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import integrations
from .channels import config, routes_hooks, routes_meetings
from .channels.catalog_common import PLACEHOLDERS
from .channels.http import request
from .channels.imap_poll import poll_imap
from .channels.oauth import OAUTH_PROVIDERS, redirect_uri
from .channels.registry import channel as get_channel, get_provider, is_hub_channel, webhook_pairs
from .channels.testers import run_test
from .growth_core import public_base_url

LIVE_FLAGS = ("live_whatsapp", "live_sms")


class SaveBody(BaseModel):
    values: dict = {}
    make_active: bool = False


class TestBody(BaseModel):
    to: str = ""
    template: str = ""
    text: str = ""
    sample: str = ""
    mapping: dict = {}
    variables: Union[list, dict] = []


class ActiveBody(BaseModel):
    provider: str


class LiveBody(BaseModel):
    live_whatsapp: Optional[bool] = None
    live_sms: Optional[bool] = None


def make_router(store, transport=request, smtp_factory=None, imap_factory=None, resolver=None, default_base_url: str = "",
                present=None, inbound_processor=None, delivery_processor=None, transactional_sender=None) -> APIRouter:
    router = APIRouter()
    base = lambda: public_base_url(store, default_base_url)

    def hub_provider(channel: str, provider: str) -> dict:
        if not is_hub_channel(channel):
            raise HTTPException(404, "Unknown channel (lead data and AI are saved under /api/integrations)")
        try:
            return get_provider(channel, provider)
        except KeyError:
            raise HTTPException(404, "Unknown provider") from None

    def webhook_url(channel: str, provider: str) -> str:
        return f"{base()}/api/webhooks/{channel}/{provider}?token={config.webhook_token(store, channel, provider)}"

    def basic_auth_url(url: str, token: str) -> str:
        """Same address with the secret as a basic-auth password: it travels in a header, so server logs never show it."""
        parts = urlparse(url)
        return parts._replace(netloc=f"hook:{token}@{parts.netloc}", query="").geturl()

    def described(channel: str, provider: str) -> dict:
        return next(p for c in channels_view() if c["id"] == channel for p in c["providers"] if p["id"] == provider)

    def channels_view() -> list:
        return config.describe(store.read("integrations"), integrations.describe(store.read("integrations")),
                               store.read("settings"))

    @router.get("/api/channels")
    def list_channels():
        return {"channels": channels_view(), "public_base_url": base(), "placeholders": list(PLACEHOLDERS),
                "oauth_redirects": {p: redirect_uri(base(), p) for p in OAUTH_PROVIDERS},
                "live": {k: bool(store.read("settings").get(k)) for k in ("live_sending", *LIVE_FLAGS)}}

    @router.get("/api/channels/webhooks")
    def list_webhooks(channel: str = "", provider: str = ""):
        """Addresses to paste into provider dashboards. They contain a secret: treat them like passwords."""
        if channel or provider:
            pairs = [(channel, provider)] if (channel, provider) in webhook_pairs() else []
            if not pairs:
                raise HTTPException(404, "This provider has no webhook")
        else:
            saved = store.read("integrations")
            pairs = sorted(p for p in webhook_pairs() if config.is_saved(p[0], config.entry_of(saved, p[0]), p[1]))
        out = []
        for ch, pid in pairs:
            prov = get_provider(ch, pid)
            token = config.webhook_token(store, ch, pid)
            url = f"{base()}/api/webhooks/{ch}/{pid}?token={token}"
            out.append({"channel": ch, "channel_title": get_channel(ch)["title"], "provider": pid, "title": prov["title"],
                        "url": url, "secret": token,
                        "basic_auth_url": basic_auth_url(url, token) if prov["webhook"]["auth"] == "token" else "",
                        "auth": prov["webhook"]["auth"], "where": prov["webhook"]["where"], "events": prov["webhook"]["events"],
                        "saved": config.is_saved(ch, config.entry_of(store.read("integrations"), ch), pid)})
        return {"public_base_url": base(), "webhooks": out}

    @router.put("/api/channels/live")
    def set_live(body: LiveBody):
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        store.update("settings", lambda s: {**s, **changes})
        return {k: bool(store.read("settings").get(k)) for k in ("live_sending", *LIVE_FLAGS)}

    @router.put("/api/channels/{channel}/{provider}")
    def save(channel: str, provider: str, body: SaveBody):
        hub_provider(channel, provider)
        try:
            config.save_provider(store, channel, provider, body.values, resolver)
            if body.make_active and get_channel(channel)["kind"] == "exclusive":
                config.set_active(store, channel, provider)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return described(channel, provider)

    @router.delete("/api/channels/{channel}/{provider}")
    def remove(channel: str, provider: str):
        hub_provider(channel, provider)
        config.delete_provider(store, channel, provider)
        return described(channel, provider)

    @router.post("/api/channels/{channel}/active")
    def make_active(channel: str, body: ActiveBody):
        hub_provider(channel, body.provider)
        try:
            config.set_active(store, channel, body.provider)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return next(c for c in channels_view() if c["id"] == channel)

    @router.post("/api/channels/{channel}/{provider}/test")
    def test(channel: str, provider: str, body: TestBody):
        hub_provider(channel, provider)
        deps = {"transport": transport, "smtp_factory": smtp_factory, "imap_factory": imap_factory, "resolver": resolver,
                "webhook_url": webhook_url, "webhook_secret": lambda ch, pid: config.webhook_token(store, ch, pid)}
        result = run_test(store, channel, provider, body.model_dump(), deps)
        return {**result, "last_test": config.record_test(store, channel, provider, result)}

    @router.post("/api/channels/{channel}/{provider}/rotate-secret")
    def rotate(channel: str, provider: str):
        hub_provider(channel, provider)
        if (channel, provider) not in webhook_pairs():
            raise HTTPException(404, "This provider has no webhook")
        config.webhook_token(store, channel, provider, rotate=True)
        return {"url": webhook_url(channel, provider), "message": "New secret made. Paste the new address into the provider."}

    @router.post("/api/channels/inbound_email/imap/poll")
    def poll_now():
        return poll_imap(store, inbound_processor or routes_hooks.default_inbound, imap_factory)

    router.include_router(routes_hooks.make_router(store, transport, default_base_url, inbound_processor, delivery_processor))
    router.include_router(routes_meetings.make_router(store, transport, resolver, present, transactional_sender))
    return router
