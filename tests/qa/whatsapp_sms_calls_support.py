"""Shared helpers for the WhatsApp / SMS / calls QA suite (tests/qa/test_whatsapp-sms-calls*.py).

Nothing here touches the network: provider calls go through FakeHTTP, DNS goes through a fake resolver and the real
HTTP transport is replaced by one that fails the test if anything reaches it.
"""
import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.channels import config
from app.http_util import HttpStatusError
from app.main import create_app
from tests.channel_fakes import PUBLIC_IP, FakeHTTP

FIX = Path(__file__).parent / "fixtures" / "whatsapp-sms-calls"
META_APP_SECRET = "qa-meta-app-secret"
META_VALUES = {"phone_number_id": "100000000000002", "access_token": "EAAqa-token", "app_secret": META_APP_SECRET}
WA_PROVIDERS = {
    "meta_cloud": META_VALUES,
    "gupshup": {"api_key": "gs-qa-key", "source_number": "917000000001", "app_name": "BMIOutreach"},
    "interakt": {"api_key": "aW50ZXJha3QtcWE="},
    "wati": {"api_endpoint": "https://live-mt-server.wati.io/100001", "access_token": "Bearer wati-qa-token"},
    "aisensy": {"api_key": "ais-qa-key"},
}
OK_REPLY = {
    "meta_cloud": {"messages": [{"id": "wamid.QA.SENT"}]},
    "gupshup": {"status": "submitted", "messageId": "gs-qa-sent"},
    "interakt": {"result": True, "message": "Message created successfully", "id": "ik-qa-sent"},
    "wati": {"result": True, "messageId": "wati-qa-sent"},
    "aisensy": {"success": "true", "submitted_message_id": "ais-qa-sent"},
}
HOST = {"meta_cloud": "graph.facebook.com", "gupshup": "gupshup.io", "interakt": "interakt.ai", "wati": "wati.io",
        "aisensy": "aisensy.com"}


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def http_error(name: str) -> Exception:
    """The exception the real transport raises for one of the provider error bodies in provider_errors.json."""
    item = load("provider_errors.json")[name]
    if item["status"] in (401, 403):
        return PermissionError(f"HTTP {item['status']}")
    return HttpStatusError(item["status"], json.dumps(item["body"])[:300])


def error_body(name: str) -> dict:
    return load("provider_errors.json")[name]["body"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any real HTTP or DNS lookup fails the test; app.whatsapp's default transport is a recorder."""
    from app.channels import http as channel_http, netguard
    from app import whatsapp

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to reach the network")
    monkeypatch.setattr(channel_http, "guarded_request", refuse)
    monkeypatch.setattr(netguard, "_resolve", lambda host: [PUBLIC_IP])
    default = FakeHTTP()
    monkeypatch.setattr(whatsapp, "request", default)
    return default


def client(tmp_path, http=None, **kwargs) -> TestClient:
    app = create_app(data_file=tmp_path / "qa.json", channel_transport=http or FakeHTTP(),
                     resolver=lambda host: [PUBLIC_IP], **kwargs)
    return TestClient(app)


def import_fixture_leads(c) -> dict:
    """Import the 123-row messy fixture; returns {normalised phone: lead} for leads that have one."""
    stats = c.post("/api/leads/import", json={"csv": (FIX / "leads_wa.csv").read_text(encoding="utf-8"),
                                              "source": "apollo"}).json()
    assert stats["created"] == 122 and stats["duplicates"] == 1, stats
    return {l["phone"]: l for l in c.get("/api/leads").json() if l["phone"]}


def phone(n: int) -> str:
    """+91 9000010001 … the fixture leads' numbers."""
    return f"+9190000{10000 + n:05d}"


def lead_of(c, n: int) -> dict:
    return next(l for l in c.get("/api/leads").json() if l["phone"] == phone(n))


def fresh(c, lead_id: str) -> dict:
    return c.get(f"/api/leads/{lead_id}").json()


def consent(c, lead_id: str, text: str = "Yes on a call with Priya, 3 Nov", channel: str = "whatsapp"):
    r = c.post(f"/api/leads/{lead_id}/consent", json={"text": text, "channel": channel, "source": "call with Priya"})
    assert r.status_code == 200, r.text
    return r.json()


def connect_whatsapp(c, pid: str, live: bool = True, extra: dict = None):
    values = {**WA_PROVIDERS[pid], **(extra or {})}
    r = c.put(f"/api/channels/whatsapp/{pid}", json={"values": values, "make_active": True})
    assert r.status_code == 200, r.text
    c.put("/api/channels/live", json={"live_whatsapp": live})


def store_connect(store, channel: str, pid: str, values: dict, live_flag: str = None):
    config.save_provider(store, channel, pid, values, resolver=lambda h: [PUBLIC_IP])
    config.set_active(store, channel, pid) if config.channel(channel)["kind"] == "exclusive" else None
    if live_flag:
        store.update("settings", lambda s: {**s, live_flag: True})


def webhook_path(c, channel: str, pid: str) -> tuple:
    data = c.get(f"/api/channels/webhooks?channel={channel}&provider={pid}").json()["webhooks"][0]
    return data["url"].split("8000", 1)[1], data["secret"]


def meta_post(c, payload: dict, path: str, secret: str = META_APP_SECRET):
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return c.post(path, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})


def meta_message(frm: str, text: str, mid: str, ts: int = 1793692800) -> dict:
    """One inbound Meta text message in the documented envelope."""
    return {"object": "whatsapp_business_account", "entry": [{"id": "100000000000001", "changes": [{
        "field": "messages", "value": {"messaging_product": "whatsapp",
                                       "metadata": {"display_phone_number": "917000000001", "phone_number_id": "100000000000002"},
                                       "contacts": [{"profile": {"name": "Lead"}, "wa_id": frm.lstrip("+")}],
                                       "messages": [{"from": frm.lstrip("+"), "id": mid, "timestamp": str(ts),
                                                     "type": "text", "text": {"body": text}}]}}]}]}


def tasks_for(store, lead_id: str, kind: str = None, status: str = None) -> list:
    return [t for t in store.read("tasks") if t.get("lead_id") == lead_id and (kind is None or t.get("kind") == kind)
            and (status is None or t.get("status", "open") == status)]


class Clock:
    """Moves app.inbound and app.activities time together (both read the wall clock)."""

    def __init__(self, monkeypatch, start: datetime):
        from app import activities, inbound
        self.now = start
        monkeypatch.setattr(inbound, "_now", lambda: self.now.isoformat(timespec="seconds"))
        monkeypatch.setattr(activities, "now_utc", lambda: self.now)

    def set(self, when: datetime) -> datetime:
        self.now = when
        return when


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)
