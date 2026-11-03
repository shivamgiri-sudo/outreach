"""Calendars: OAuth flow and token storage, Google/Zoom/Cal.com/Calendly adapters, meetings on leads, booking webhooks."""
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.channels import config
from app.channels.oauth import access_token
from app.main import create_app
from tests.channel_fakes import FakeHTTP, add_lead, make_client, provider

GOOGLE = {"client_id": "cid.apps.googleusercontent.com", "client_secret": "gsecret-777"}
TOKEN_REPLY = {"access_token": "ya29.access", "refresh_token": "1//refresh-xyz", "expires_in": 3599, "scope": "calendar.events"}
EVENT_REPLY = {"id": "evt123", "htmlLink": "https://calendar.google.com/event?eid=1", "hangoutLink": "https://meet.google.com/abc-defg-hij",
               "conferenceData": {"entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"}]}}
IST = timezone(timedelta(hours=5, minutes=30))


def future_ist(days=2, hour=11):
    day = (datetime.now(IST) + timedelta(days=days)).date()
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=IST).isoformat()


def connect_google(c, http=None):
    c.put("/api/channels/calendar/google", json={"values": GOOGLE})
    config.set_tokens(c.app.state.store, "calendar", "google",
                      {"access_token": "ya29.access", "refresh_token": "1//refresh-xyz", "expires_at": int(time.time()) + 3000})


# ---- OAuth ----
def test_oauth_start_and_callback_store_tokens_server_side(tmp_path):
    http = FakeHTTP({"oauth2.googleapis.com/token": TOKEN_REPLY})
    c = make_client(tmp_path, http)
    assert c.get("/api/oauth/google/start").status_code == 400                     # client id not saved yet
    c.put("/api/channels/calendar/google", json={"values": GOOGLE})
    assert provider(c, "calendar", "google")["status"] == "incomplete"            # saved but not connected
    start = c.get("/api/oauth/google/start", params={"return_to": "http://localhost:5173/"}).json()
    query = parse_qs(urlparse(start["url"]).query)
    assert start["url"].startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert query["redirect_uri"] == ["http://localhost:8000/api/oauth/google/callback"] and query["access_type"] == ["offline"]
    assert "calendar.events" in query["scope"][0] and "gsecret" not in start["url"]
    r = c.get("/api/oauth/google/callback", params={"state": query["state"][0], "code": "4/code"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "http://localhost:5173/#integrations?connected=google"
    exchange = http.last("oauth2.googleapis.com")
    assert exchange["form"]["code"] == "4/code" and exchange["form"]["grant_type"] == "authorization_code"
    assert exchange["form"]["redirect_uri"] == "http://localhost:8000/api/oauth/google/callback"
    tokens = c.app.state.store.read("integrations")["calendar"]["providers"]["google"]["tokens"]
    assert tokens["refresh_token"] == "1//refresh-xyz"
    shown = provider(c, "calendar", "google")
    assert shown["status"] == "configured" and shown["oauth_connected"] is True
    everything = c.get("/api/channels").text + c.get("/api/integrations").text
    assert "refresh-xyz" not in everything and "ya29.access" not in everything and "gsecret-777" not in everything


def test_oauth_callback_rejects_bad_state_and_handles_denial(tmp_path):
    c = make_client(tmp_path, FakeHTTP({"oauth2": TOKEN_REPLY}))
    c.put("/api/channels/calendar/google", json={"values": GOOGLE})
    assert c.get("/api/oauth/google/callback", params={"state": "forged.sig", "code": "x"}).status_code == 400
    state = parse_qs(urlparse(c.get("/api/oauth/google/start").json()["url"]).query)["state"][0]
    assert c.get("/api/oauth/microsoft/callback", params={"state": state, "code": "x"}).status_code == 400   # wrong provider
    denied = c.get("/api/oauth/google/callback", params={"state": state, "error": "access_denied"}, follow_redirects=False)
    assert denied.status_code == 303 and "oauth_error=access_denied" in denied.headers["location"]


def test_oauth_return_to_cannot_redirect_elsewhere(tmp_path):
    c = make_client(tmp_path, FakeHTTP({"oauth2": TOKEN_REPLY}))
    c.put("/api/channels/calendar/google", json={"values": GOOGLE})
    url = c.get("/api/oauth/google/start", params={"return_to": "https://evil.example/steal"}).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    r = c.get("/api/oauth/google/callback", params={"state": state, "code": "c"}, follow_redirects=False)
    assert r.headers["location"].startswith("http://localhost:8000/#integrations")


def test_oauth_callback_is_open_only_with_a_valid_state_when_login_is_on(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password="pw", channel_transport=FakeHTTP({"oauth2": TOKEN_REPLY})))
    auth = {"Authorization": "Bearer " + c.post("/api/login", json={"password": "pw"}).json()["token"]}
    c.put("/api/channels/calendar/google", json={"values": GOOGLE}, headers=auth)
    assert c.get("/api/oauth/google/start").status_code == 401                     # starting needs login
    state = parse_qs(urlparse(c.get("/api/oauth/google/start", headers=auth).json()["url"]).query)["state"][0]
    assert c.get("/api/oauth/google/callback", params={"state": state, "code": "c"}, follow_redirects=False).status_code == 303
    assert c.get("/api/oauth/google/callback", params={"state": "bad.state", "code": "c"}).status_code == 401


def test_access_token_refreshes_when_expired(tmp_path):
    http = FakeHTTP({"oauth2.googleapis.com/token": {"access_token": "ya29.new", "expires_in": 3600}})
    c = make_client(tmp_path, http)
    c.put("/api/channels/calendar/google", json={"values": GOOGLE})
    store = c.app.state.store
    config.set_tokens(store, "calendar", "google", {"access_token": "old", "refresh_token": "1//r", "expires_at": int(time.time()) - 10})
    assert access_token(store, "google", http) == "ya29.new"
    assert http.last()["form"]["grant_type"] == "refresh_token" and http.last()["form"]["refresh_token"] == "1//r"
    tokens = store.read("integrations")["calendar"]["providers"]["google"]["tokens"]
    assert tokens["access_token"] == "ya29.new" and tokens["refresh_token"] == "1//r"   # the refresh token is kept


# ---- meetings via Google ----
def test_book_list_and_cancel_a_google_meeting(tmp_path):
    http = FakeHTTP({"/events?conferenceDataVersion=1": EVENT_REPLY, "/events/evt123": {"_status": 204}})
    c = make_client(tmp_path, http)
    lead = add_lead(c)
    connect_google(c)
    start = future_ist()
    r = c.post(f"/api/leads/{lead['id']}/meetings", json={"start": start, "duration_minutes": 45, "notes": "Demo"})
    assert r.status_code == 200, r.text
    data = r.json()
    meeting = data["meeting"]
    assert data["kind"] == "event" and meeting["join_url"] == "https://meet.google.com/abc-defg-hij" and meeting["status"] == "scheduled"
    assert meeting["external_id"] == "evt123" and meeting["title"] == "BookMyInterview.ai demo — Acme Retail"
    sent = http.last("conferenceDataVersion")
    assert sent["headers"]["Authorization"] == "Bearer ya29.access"
    assert sent["body"]["conferenceData"]["createRequest"]["conferenceSolutionKey"] == {"type": "hangoutsMeet"}
    assert sent["body"]["attendees"] == [{"email": "asha@acme-retail.in"}] and sent["body"]["start"]["timeZone"] == "Asia/Kolkata"
    end = datetime.fromisoformat(sent["body"]["end"]["dateTime"]) - datetime.fromisoformat(sent["body"]["start"]["dateTime"])
    assert end == timedelta(minutes=45)
    assert any(e["kind"] == "meeting" and "Meeting booked" in e["text"] and "IST" in e["text"] for e in data["lead"]["timeline"])
    assert [m["id"] for m in c.get(f"/api/leads/{lead['id']}/meetings").json()] == [meeting["id"]]
    upcoming = c.get("/api/meetings").json()
    assert upcoming[0]["company"] == "Acme Retail Pvt Ltd" and upcoming[0]["lead_id"] == lead["id"]
    assert c.get("/api/meetings", params={"from": future_ist(days=5)}).json() == []
    cancelled = c.post(f"/api/meetings/{meeting['id']}/cancel").json()
    assert cancelled["meeting"]["status"] == "cancelled" and http.last("/events/evt123")["method"] == "DELETE"
    timeline = c.get(f"/api/leads/{lead['id']}").json()["timeline"]
    assert timeline[-1]["text"].startswith("Meeting cancelled")
    assert c.post("/api/meetings/nope/cancel").status_code == 404


def test_meeting_needs_a_calendar_and_a_sensible_time(tmp_path):
    c = make_client(tmp_path, FakeHTTP({"events": EVENT_REPLY}))
    lead = add_lead(c)
    r = c.post(f"/api/leads/{lead['id']}/meetings", json={"start": future_ist()})
    assert r.status_code == 409 and "Integrations" in r.json()["detail"]
    connect_google(c)
    assert c.post(f"/api/leads/{lead['id']}/meetings", json={"start": "2020-01-01T10:00:00+05:30"}).status_code == 400
    assert c.post(f"/api/leads/{lead['id']}/meetings", json={"start": "tomorrow"}).status_code == 400
    assert c.post(f"/api/leads/{lead['id']}/meetings", json={"start": future_ist(), "duration_minutes": 9999}).status_code == 422
    assert c.post(f"/api/leads/{lead['id']}/meetings", json={"start": future_ist(), "attendee_email": "nope"}).status_code == 400
    assert c.post("/api/leads/missing/meetings", json={"start": future_ist()}).status_code == 404
    c.post(f"/api/leads/{lead['id']}/stage", json={"stage": "unsubscribed"})
    assert c.post(f"/api/leads/{lead['id']}/meetings", json={"start": future_ist()}).status_code == 400


def test_calendar_refusal_is_reported(tmp_path):
    c = make_client(tmp_path, FakeHTTP({"events": OSError("HTTP 500 backend")}))
    lead = add_lead(c)
    connect_google(c)
    r = c.post(f"/api/leads/{lead['id']}/meetings", json={"start": future_ist()})
    assert r.status_code == 502 and c.get(f"/api/leads/{lead['id']}/meetings").json() == []


def test_free_busy(tmp_path):
    busy = [{"start": "2026-11-05T05:00:00Z", "end": "2026-11-05T06:00:00Z"}]
    c = make_client(tmp_path, FakeHTTP({"freeBusy": {"calendars": {"primary": {"busy": busy}}}}))
    connect_google(c)
    r = c.get("/api/meetings/availability", params={"start": "2026-11-05T09:00:00+05:30", "end": "2026-11-05T18:00:00+05:30"})
    assert r.json() == {"busy": busy}


# ---- Zoom, Cal.com, Calendly, booking link ----
def test_zoom_server_to_server_token_and_meeting(tmp_path):
    http = FakeHTTP({"zoom.us/oauth/token": {"access_token": "zoom-tok", "expires_in": 3600},
                     "users/me/meetings": {"id": 987654321, "join_url": "https://zoom.us/j/987654321"},
                     "api.zoom.us/v2/users/me": {"email": "priya@x.in"}})
    c = make_client(tmp_path, http)
    lead = add_lead(c)
    c.put("/api/channels/calendar/zoom", json={"values": {"account_id": "acc1", "client_id": "zc", "client_secret": "zs"}})
    assert c.post("/api/channels/calendar/zoom/test", json={}).json()["message"] == "Connected to Zoom as priya@x.in."
    token_call = http.calls[0]
    assert "grant_type=account_credentials" in token_call["url"] and "account_id=acc1" in token_call["url"]
    assert token_call["headers"]["Authorization"].startswith("Basic ")
    meeting = c.post(f"/api/leads/{lead['id']}/meetings", json={"start": future_ist(), "duration_minutes": 30}).json()["meeting"]
    assert meeting["join_url"] == "https://zoom.us/j/987654321" and meeting["external_id"] == "987654321"
    body = http.last("users/me/meetings")["body"]
    assert body["type"] == 2 and body["duration"] == 30 and body["start_time"].endswith("Z")
    assert len([c_ for c_ in http.calls if "oauth/token" in c_["url"]]) == 1           # the token is cached


def test_calcom_event_types_link_and_signed_booking_webhook(tmp_path):
    http = FakeHTTP({"api.cal.com/v2/event-types": {"status": "success", "data": [{"title": "BMI demo", "slug": "demo"}]}})
    c = make_client(tmp_path, http)
    lead = add_lead(c)
    c.put("/api/channels/calendar/calcom", json={"values": {"booking_url": "https://cal.com/priya/demo", "api_key": "cal_live_k"}})
    test = c.post("/api/channels/calendar/calcom/test", json={}).json()
    assert test["ok"] is True and "BMI demo" in test["message"]
    assert http.last()["headers"]["cal-api-version"] == "2024-06-14"
    link = c.post(f"/api/leads/{lead['id']}/meetings", json={}).json()
    assert link["kind"] == "link" and link["url"].startswith("https://cal.com/priya/demo?")
    assert "email=asha%40acme-retail.in" in link["url"] and "name=Asha+Rao" in link["url"]
    hook = c.get("/api/channels/webhooks?channel=calendar&provider=calcom").json()["webhooks"][0]
    body = json.dumps({"triggerEvent": "BOOKING_CREATED", "payload": {
        "title": "BMI demo", "startTime": "2026-11-06T05:30:00Z", "endTime": "2026-11-06T06:00:00Z", "uid": "bk_1",
        "attendees": [{"email": "ASHA@acme-retail.in", "name": "Asha"}], "metadata": {"videoCallUrl": "https://app.cal.com/video/bk_1"}}}).encode()
    sig = hmac.new(hook["secret"].encode(), body, hashlib.sha256).hexdigest()
    path = "/api/webhooks/calendar/calcom"
    assert c.post(path, content=body, headers={"Content-Type": "application/json", "X-Cal-Signature-256": "bad"}).status_code == 401
    r = c.post(path, content=body, headers={"Content-Type": "application/json", "X-Cal-Signature-256": sig})
    assert r.status_code == 200 and r.json()["matched"] == 1
    saved = c.get(f"/api/leads/{lead['id']}").json()
    assert saved["meetings"][0]["join_url"] == "https://app.cal.com/video/bk_1" and saved["meetings"][0]["status"] == "scheduled"
    assert saved["timeline"][-1]["text"].startswith("Meeting booked") and saved["consent"] is None   # consent untouched
    c.post(path, content=body, headers={"Content-Type": "application/json", "X-Cal-Signature-256": sig})
    assert len(c.get(f"/api/leads/{lead['id']}").json()["meetings"]) == 1                  # the same booking is not added twice
    cancel = json.dumps({"triggerEvent": "BOOKING_CANCELLED", "payload": {"uid": "bk_1", "attendees": [{"email": "asha@acme-retail.in"}]}}).encode()
    c.post(path, content=cancel, headers={"Content-Type": "application/json",
                                          "X-Cal-Signature-256": hmac.new(hook["secret"].encode(), cancel, hashlib.sha256).hexdigest()})
    assert c.get(f"/api/leads/{lead['id']}").json()["meetings"][0]["status"] == "cancelled"


def test_calendly_test_registers_webhook_and_signed_booking_attaches_by_other_contact(tmp_path):
    me = {"resource": {"uri": "https://api.calendly.com/users/U1", "name": "Priya", "scheduling_url": "https://calendly.com/priya",
                       "current_organization": "https://api.calendly.com/organizations/O1"}}
    http = FakeHTTP({"users/me": me, "webhook_subscriptions": {"resource": {"uri": "https://api.calendly.com/webhook_subscriptions/W1"}}})
    c = make_client(tmp_path, http)
    lead = add_lead(c)
    store = c.app.state.store
    store.update("leads", lambda rows: [{**r, "other_contacts": [{"name": "Ravi", "email": "ravi@acme-retail.in"}]} for r in rows])
    c.put("/api/channels/calendar/calendly", json={"values": {"access_token": "cal-pat"}})
    result = c.post("/api/channels/calendar/calendly/test", json={}).json()
    assert result["ok"] is True and "registered" in result["message"]
    sub = http.last("webhook_subscriptions")["body"]
    assert sub["events"] == ["invitee.created", "invitee.canceled"] and sub["url"].startswith("http://localhost:8000/api/webhooks/calendar/calendly")
    link = c.post(f"/api/leads/{lead['id']}/meetings", json={}).json()
    assert link["url"].startswith("https://calendly.com/priya?")
    body = json.dumps({"event": "invitee.created", "payload": {"email": "ravi@acme-retail.in", "name": "Ravi", "scheduled_event": {
        "uri": "https://api.calendly.com/scheduled_events/SE1", "name": "BMI demo", "start_time": "2026-11-06T05:30:00Z",
        "end_time": "2026-11-06T06:00:00Z", "location": {"join_url": "https://zoom.us/j/1"}}}}).encode()
    stamp = str(int(time.time()))
    sig = hmac.new(sub["signing_key"].encode(), f"{stamp}.".encode() + body, hashlib.sha256).hexdigest()
    r = c.post("/api/webhooks/calendar/calendly", content=body,
               headers={"Content-Type": "application/json", "Calendly-Webhook-Signature": f"t={stamp},v1={sig}"})
    assert r.status_code == 200 and r.json()["matched"] == 1
    meeting = c.get(f"/api/leads/{lead['id']}/meetings").json()[0]
    assert meeting["external_id"] == "SE1" and meeting["join_url"] == "https://zoom.us/j/1"
    unknown = json.dumps({"event": "invitee.created", "payload": {"email": "stranger@else.in", "scheduled_event": {}}}).encode()
    sig2 = hmac.new(sub["signing_key"].encode(), f"{stamp}.".encode() + unknown, hashlib.sha256).hexdigest()
    r2 = c.post("/api/webhooks/calendar/calendly", content=unknown,
                headers={"Content-Type": "application/json", "Calendly-Webhook-Signature": f"t={stamp},v1={sig2}"})
    assert r2.json()["matched"] == 0


def test_booking_link_prefill(tmp_path):
    c = make_client(tmp_path)
    lead = add_lead(c)
    c.put("/api/channels/calendar/booking_link", json={"values": {"booking_url": "https://book.example.in/priya?n={name}&e={email}"}})
    out = c.post(f"/api/leads/{lead['id']}/meetings", json={}).json()
    assert out == {"kind": "link", "provider": "booking_link", "url": "https://book.example.in/priya?n=Asha%20Rao&e=asha%40acme-retail.in"}
    test = c.post("/api/channels/calendar/booking_link/test", json={}).json()
    assert test["ok"] is True and "Test%20Lead" in test["message"]


def test_availability_needs_a_start_and_end(tmp_path):
    c = make_client(tmp_path)
    assert c.get("/api/meetings/availability", params={"start": "", "end": ""}).status_code == 400
    assert c.get("/api/meetings/availability", params={"start": "2026-11-05T18:00:00+05:30", "end": "2026-11-05T09:00:00+05:30"}).status_code == 400
