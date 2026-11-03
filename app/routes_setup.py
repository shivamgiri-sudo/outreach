"""Setup helpers: test each connection from the app, and the go-live checklist."""
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import integrations
from .ai import ANTHROPIC_URL, MODEL, _llm_config
from .channels.netguard import guarded_post_json
from .apollo import match_person
from .growth_core import public_base_url, webhook_secret
from .http_util import post_json
from .providers import check_firecrawl, check_hunter, get_json
from .senders import send_test

REQUIRED_FOR_LIVE_TEST = ("leads", "public_url", "sending_domain", "test_email", "bmi_limits")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0")


class TestBody(BaseModel):
    to: str = ""


def _hub_ready(store, channel_id: str) -> bool:
    """True when an Integrations-hub channel has a working provider (any saved one for reply capture)."""
    from .channels.config import channel_status
    return channel_status(store.read("integrations"), channel_id) == "configured"


def _public_url_ok(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return bool(host) and host not in LOCAL_HOSTS and not host.startswith("192.168.") and not host.startswith("10.")


def make_router(store, email_transport=post_json, ai_transport=post_json, apollo_transport=post_json,
                default_base_url: str = "", default_webhook_secret: str = "", auth_required=lambda: False,
                get_transport=get_json, firecrawl_transport=guarded_post_json) -> APIRouter:
    router = APIRouter()

    def saved(integration_id: str) -> dict:
        return store.read("integrations").get(integration_id, {})

    @router.post("/api/integrations/{integration_id}/test")
    def test_connection(integration_id: str, body: TestBody):
        if integration_id not in integrations.known_ids():
            raise HTTPException(404, "Unknown integration")
        if integration_id == "sending_domain":
            return send_test(store, email_transport, body.to)
        if integration_id == "llm":
            if not _llm_config(store):
                return {"ok": False, "message": "Connect the AI writer first (provider anthropic and an API key)."}
            key = _llm_config(store)["api_key"]
            request = {"model": MODEL, "max_tokens": 5, "messages": [{"role": "user", "content": "Reply with the single word OK."}]}
            try:
                reply = ai_transport(ANTHROPIC_URL, {"x-api-key": key, "anthropic-version": "2023-06-01"}, request)
            except PermissionError:
                return {"ok": False, "message": "The AI provider rejected the API key."}
            except (OSError, ValueError) as exc:
                return {"ok": False, "message": f"Could not reach the AI provider: {exc}"}
            text = "".join(p.get("text", "") for p in reply.get("content", []) if p.get("type") == "text")
            return {"ok": bool(text), "message": "AI writer works." if text else "The AI provider answered with nothing."}
        if integration_id == "apollo":
            key = saved("apollo").get("values", {}).get("api_key")
            if saved("apollo").get("mode") != "api" or not key:
                return {"ok": False, "message": "Connect Apollo first (direct API mode and a key)."}
            try:
                match_person(key, "Test", "Person", "example.com", "Example", apollo_transport)
            except PermissionError:
                return {"ok": False, "message": "Apollo rejected the API key."}
            except (OSError, ValueError) as exc:
                return {"ok": False, "message": f"Could not reach Apollo: {exc}"}
            return {"ok": True, "message": "Apollo accepted the key. The test lookup may use one credit."}
        if integration_id == "whatsapp":
            return {"ok": False, "message": "WhatsApp stays in practice mode: messages are queued and shown, not delivered. "
                                            "Tell us your provider and its sending can be added."}
        if integration_id == "telephony":
            url = saved("telephony").get("values", {}).get("click_to_call_url", "")
            ok = "{phone}" in url
            return {"ok": ok, "message": "Looks right. Phone numbers will be inserted at {phone}." if ok
                    else "The link must contain {phone} where the number goes."}
        if integration_id == "hunter":
            return check_hunter(saved("hunter"), get_transport)
        if integration_id == "firecrawl":
            return check_firecrawl(saved("firecrawl"), firecrawl_transport)
        if integration_id == "contact_scraper":
            return {"ok": True, "message": "Manual: run the scraper on your computer, then upload its JSON on the Data sources page."}
        if integration_id == "sales_navigator":
            return {"ok": True, "message": "Manual: export from Sales Navigator and import the CSV under Leads."}
        return {"ok": False, "message": "Automatic checking isn't built yet. Verify lists with your tool and import the result."}

    @router.get("/api/readiness")
    def readiness():
        settings = store.read("settings")
        ints = {i["id"]: i for i in integrations.describe(store.read("integrations"))}
        connected = lambda i: ints[i]["status"] == "configured"
        base = public_base_url(store, default_base_url)
        stories_ok = any(s.get("permission") for s in store.read("stories"))

        def row(item_id, title, done, detail, page, required=False):
            return {"id": item_id, "title": title, "status": "done" if done else ("todo" if required else "optional"),
                    "detail": detail, "page": page, "required": required}

        items = [
            row("leads", "Load your leads", bool(store.read("leads")), "Import your sheet or an Apollo export.", "leads", True),
            row("public_url", "Set your public web address", _public_url_ok(base),
                f"Unsubscribe and demo links use {base}. Recipients can't open a localhost address.", "setup", True),
            row("sending_domain", "Connect the sending domain", connected("sending_domain"),
                "A separate domain, with the email provider's API key.", "setup", True),
            row("test_email", "Send yourself a test email", bool(settings.get("test_email_ok_at")),
                "Use the test button on the sending domain card.", "setup", True),
            row("bmi_limits", "Confirm the free limits are set inside BookMyInterview", bool(settings.get("confirm_bmi_limits")),
                "Otherwise free use has no ceiling.", "setup", True),
            {"id": "live_sending", "title": "Switch on real sending", "status": "done" if settings.get("live_sending") else "todo",
             "detail": "A deliberate last step: tick 'Send for real' on the Outbox page.", "page": "outbox", "required": False},
            row("login", "Turn on login", bool(auth_required()), "Set OUTREACH_ADMIN_PASSWORD before sharing the app.", "setup"),
            row("secret_key", "Encrypt saved keys", bool(getattr(store, "secrets_encrypted", False)) and not getattr(store, "secret_errors", 0),
                "Saved keys are encrypted but OUTREACH_SECRET_KEY is missing or wrong, so they can't be used. Set the right key "
                "and restart." if getattr(store, "secret_errors", 0) else "Set OUTREACH_SECRET_KEY to encrypt saved keys.", "setup"),
            row("webhook", "Let the product report activity", bool(webhook_secret(store, default_webhook_secret)),
                "A secret the BookMyInterview product sends with each event.", "setup"),
            row("ai_writer", "Connect the AI writer", bool(_llm_config(store)), "Gives tailored reply drafts.", "setup"),
            row("apollo", "Connect Apollo", ints["apollo"]["mode"] == "api" and connected("apollo"), "Automatic email lookups.", "setup"),
            row("whatsapp", "Connect WhatsApp", connected("whatsapp"), "Messages stay in practice mode until sending is added.", "setup"),
            row("story", "Add one success story with permission", stories_ok, "Real results build trust.", "stories"),
            row("reply_capture", "Connect reply capture (inbound email)", _hub_ready(store, "inbound_email"),
                "Replies stop the sequence automatically instead of being pasted by hand.", "integrations"),
            row("calendar", "Connect a calendar", _hub_ready(store, "calendar"),
                "Book demos from a lead's page with a video link.", "integrations"),
        ]
        done = sum(1 for i in items if i["status"] == "done")
        ready = all(i["status"] == "done" for i in items if i["id"] in REQUIRED_FOR_LIVE_TEST)
        return {"items": items, "done": done, "total": len(items), "ready_for_live_test": ready,
                "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    return router
