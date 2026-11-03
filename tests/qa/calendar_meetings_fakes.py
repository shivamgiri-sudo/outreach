"""Shared helpers for the calendar & meetings QA tests: fixtures, fake clocks, provider set-up and signed webhooks.

Every external call goes through tests.channel_fakes.FakeHTTP (no network). All people, companies, domains and numbers
in the fixtures are made up (*.example domains, 90000 1xxxx numbers).
"""
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.channels import config
from tests.channel_fakes import FakeHTTP, make_client

FIXTURES = Path(__file__).parent / "fixtures" / "calendar-meetings"
IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc
GOOGLE_VALUES = {"client_id": "000000-fake.apps.googleusercontent.com", "client_secret": "GOCSPX-fake-secret"}
MS_VALUES = {"client_id": "00000000-0000-0000-0000-00000000fake", "client_secret": "ms~fake~secret", "tenant": "common"}
ZOOM_VALUES = {"account_id": "fakeAcct01", "client_id": "fakeZoomClient", "client_secret": "fakeZoomSecret"}


def load(name: str):
    text = (FIXTURES / name).read_text(encoding="utf-8-sig")
    return json.loads(text) if name.endswith(".json") else text


def replies() -> dict:
    return load("provider_replies.json")


def ist(days: int = 2, hour: int = 11, minute: int = 0) -> datetime:
    """A wall-clock time in IST, `days` days from today's IST date (always in the future for days >= 1)."""
    day = (datetime.now(IST) + timedelta(days=days)).date()
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def client(tmp_path, http=None, **kw):
    return make_client(tmp_path, http or FakeHTTP(), **kw)


def import_leads(c) -> dict:
    """Commit the messy Apollo-style export through the import wizard; returns {email: lead} for primary emails."""
    r = c.post("/api/import/commit", json={"csv": load("leads_apollo_export.csv"), "source": "apollo"})
    assert r.status_code == 200, r.text
    return {l["email"]: l for l in c.get("/api/leads").json() if l.get("email")}


def connect_google(c, calendar_id: str = "", expires_in: int = 3000):
    values = {**GOOGLE_VALUES, **({"calendar_id": calendar_id} if calendar_id else {})}
    assert c.put("/api/channels/calendar/google", json={"values": values}).status_code == 200
    config.set_tokens(c.app.state.store, "calendar", "google",
                      {"access_token": "ya29.cached", "refresh_token": "1//0gFAKE-refresh", "expires_at": int(time.time()) + expires_in})


def connect_microsoft(c):
    assert c.put("/api/channels/calendar/microsoft", json={"values": MS_VALUES}).status_code == 200
    config.set_tokens(c.app.state.store, "calendar", "microsoft",
                      {"access_token": "ms.cached", "refresh_token": "M.R3_BAY.FAKE", "expires_at": int(time.time()) + 3000})


def connect_zoom(c):
    assert c.put("/api/channels/calendar/zoom", json={"values": ZOOM_VALUES}).status_code == 200


def zoom_http(extra: dict = None) -> FakeHTTP:
    r = replies()
    return FakeHTTP({"zoom.us/oauth/token": r["zoom_token"], "users/me/meetings": r["zoom_meeting"],
                     "api.zoom.us/v2/users/me": r["zoom_user"], "/meetings/": {"_status": 204}, **(extra or {})})


def save_calcom(c, api_key: str = ""):
    values = {"booking_url": "https://cal.com/priya-bmi-fake/demo", **({"api_key": api_key} if api_key else {})}
    assert c.put("/api/channels/calendar/calcom", json={"values": values}).status_code == 200


def hook_secret(c, provider: str) -> str:
    return c.get("/api/channels/webhooks", params={"channel": "calendar", "provider": provider}).json()["webhooks"][0]["secret"]


def raw(payload) -> bytes:
    return payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")


def post_calcom(c, payload, secret: str = None, signature: str = None):
    body = raw(payload)
    secret = secret if secret is not None else hook_secret(c, "calcom")
    sig = signature if signature is not None else hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return c.post("/api/webhooks/calendar/calcom", content=body,
                  headers={"Content-Type": "application/json", "X-Cal-Signature-256": sig})


def calendly_signature(secret: str, body: bytes, stamp: int) -> str:
    return f"t={stamp},v1=" + hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()


def post_calendly(c, payload, secret: str, stamp: int = None):
    body = raw(payload)
    header = calendly_signature(secret, body, int(time.time()) if stamp is None else stamp)
    return c.post("/api/webhooks/calendar/calendly", content=body,
                  headers={"Content-Type": "application/json", "Calendly-Webhook-Signature": header})


def lead_by_email(c, email: str) -> dict:
    return next(l for l in c.get("/api/leads").json() if l.get("email") == email)


def meetings_of(c, lead_id: str) -> list:
    return c.get(f"/api/leads/{lead_id}/meetings").json()


def outbox(c, kind: str = None) -> list:
    return [r for r in c.app.state.store.read("transactional_outbox") if kind is None or r["kind"] == kind]


class Sender:
    """A transactional sender that records every message and answers 'sent'."""

    def __init__(self, status: str = "sent"):
        self.sent, self.status = [], status

    def __call__(self, message):
        self.sent.append(message)
        return {"status": self.status}


def ticks(start: datetime, end: datetime, step: timedelta):
    """The background timer: one `now` per step from start to end (inclusive)."""
    now = start
    while now <= end:
        yield now
        now += step
