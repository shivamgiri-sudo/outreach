"""QA: calendar providers and booking from the lead panel API.

Payload shapes follow the official docs (see docs/qa/calendar-meetings.md for every source URL):
Google OAuth web-server flow + Calendar events.insert (conferenceData / Meet) + freeBusy.query, Microsoft identity
platform auth-code flow + Graph POST /me/events (Teams), Zoom Server-to-Server OAuth + POST /users/me/meetings,
Cal.com / Calendly / any booking link. Every HTTP call goes to FakeHTTP; nothing touches the network.
"""
import base64
import time
from datetime import timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse

from fastapi.testclient import TestClient

from app.channels import config
from app.channels.oauth import access_token, zoom_token
from app.http_util import HttpStatusError
from app.main import create_app
from tests.channel_fakes import FakeHTTP
from tests.qa.calendar_meetings_fakes import (GOOGLE_VALUES, IST, MS_VALUES, ZOOM_VALUES, client, connect_google,
                                             connect_microsoft, connect_zoom, import_leads, ist, meetings_of, outbox,
                                             replies, save_calcom, zoom_http)

R = replies()
POOJA = "pooja.rao@sagar-fintech-01.example"          # primary contact of "Sagar Fintech Private Limited"
HARPREET = "harpreet.sharma@nirmal-textiles-02.example"
FARAH = "farah.khan@desi-hospitality-04.example"


def book(c, lead, **body):
    return c.post(f"/api/leads/{lead['id']}/meetings", json=body)


# ---------- Google: OAuth web-server flow ----------
def test_google_oauth_start_callback_exchange_and_secrets_never_leak(tmp_path):
    http = FakeHTTP({"oauth2.googleapis.com/token": R["google_token"]})
    c = client(tmp_path, http)
    c.put("/api/channels/calendar/google", json={"values": GOOGLE_VALUES})
    start = c.get("/api/oauth/google/start", params={"return_to": "http://localhost:5173/#leads"}).json()
    url = urlparse(start["url"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    assert q["response_type"] == "code" and q["access_type"] == "offline" and q["prompt"] == "consent"
    assert q["client_id"] == GOOGLE_VALUES["client_id"] and q["redirect_uri"] == start["redirect_uri"]
    assert set(q["scope"].split()) == {"https://www.googleapis.com/auth/calendar.events",
                                       "https://www.googleapis.com/auth/calendar.freebusy"}
    assert "GOCSPX" not in start["url"]
    done = c.get("/api/oauth/google/callback", params={"state": q["state"], "code": "4/0AfakeCode", "scope": q["scope"]},
                 follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == "http://localhost:5173/#integrations?connected=google"
    form = http.last("oauth2.googleapis.com/token")["form"]
    assert form == {"code": "4/0AfakeCode", "client_id": GOOGLE_VALUES["client_id"], "client_secret": GOOGLE_VALUES["client_secret"],
                    "redirect_uri": start["redirect_uri"], "grant_type": "authorization_code"}
    tokens = c.app.state.store.read("integrations")["calendar"]["providers"]["google"]["tokens"]
    assert tokens["refresh_token"] == R["google_token"]["refresh_token"] and tokens["expires_at"] > time.time() + 3000
    shown = c.get("/api/channels").text + c.get("/api/integrations").text + c.get("/api/meetings").text
    for secret in ("GOCSPX-fake-secret", "ya29.a0FAKE", "1//0gFAKE"):
        assert secret not in shown
    # the user pressed "Cancel" on Google's consent screen; and a callback without a code
    denied = c.get("/api/oauth/google/callback", params={"state": q["state"], "error": "access_denied"}, follow_redirects=False)
    assert "oauth_error=access_denied" in denied.headers["location"]
    no_code = c.get("/api/oauth/google/callback", params={"state": q["state"]}, follow_redirects=False)
    assert "oauth_error=no_code" in no_code.headers["location"]


def test_google_code_exchange_failure_is_reported_not_stored(tmp_path):
    http = FakeHTTP({"oauth2.googleapis.com/token": HttpStatusError(400, '{"error": "invalid_grant", "error_description": "Bad Request"}')})
    c = client(tmp_path, http)
    c.put("/api/channels/calendar/google", json={"values": GOOGLE_VALUES})
    state = parse_qs(urlparse(c.get("/api/oauth/google/start").json()["url"]).query)["state"][0]
    r = c.get("/api/oauth/google/callback", params={"state": state, "code": "4/used-twice"}, follow_redirects=False)
    assert r.status_code == 303 and "oauth_error=token_exchange_failed" in r.headers["location"]
    assert not (c.app.state.store.read("integrations")["calendar"]["providers"]["google"].get("tokens"))


def test_google_access_token_refreshes_inside_the_margin_and_keeps_the_refresh_token(tmp_path):
    http = FakeHTTP({"oauth2.googleapis.com/token": R["google_refresh"]})
    c = client(tmp_path, http)
    c.put("/api/channels/calendar/google", json={"values": GOOGLE_VALUES})
    store = c.app.state.store
    t0 = 1_800_000_000
    config.set_tokens(store, "calendar", "google", {"access_token": "ya29.old", "refresh_token": "1//0gFAKE-refresh", "expires_at": t0 + 3600})
    assert access_token(store, "google", http, now=t0) == "ya29.old" and http.calls == []          # still fresh
    assert access_token(store, "google", http, now=t0 + 3600 - 119) == "ya29.a0FAKE-refreshed-token"  # inside 120 s margin
    assert http.last()["form"]["grant_type"] == "refresh_token" and "scope" not in http.last()["form"]
    tokens = store.read("integrations")["calendar"]["providers"]["google"]["tokens"]
    assert tokens["refresh_token"] == "1//0gFAKE-refresh" and tokens["expires_at"] == t0 + 3600 - 119 + 3599
    assert access_token(store, "google", http, now=t0 + 3600) == "ya29.a0FAKE-refreshed-token" and len(http.calls) == 1


def test_revoked_or_expired_google_refresh_token_asks_to_reconnect(tmp_path):
    """Google answers 400 invalid_grant when a refresh token is revoked or expired (7 days for apps in 'Testing')."""
    detail = '{"error": "invalid_grant", "error_description": "Token has been expired or revoked."}'
    http = FakeHTTP({"oauth2.googleapis.com/token": HttpStatusError(400, detail), "freeBusy": R["google_freebusy"]})
    c = client(tmp_path, http)
    lead = import_leads(c)[POOJA]
    connect_google(c, expires_in=-60)                                  # the cached access token has expired
    r = book(c, lead, start=ist(3, 11).isoformat())
    assert r.status_code == 400 and "Reconnect" in r.json()["detail"], r.text
    assert meetings_of(c, lead["id"]) == []
    busy = c.get("/api/meetings/availability", params={"start": ist(3, 9).isoformat(), "end": ist(3, 18).isoformat()})
    assert busy.status_code == 400 and "Reconnect" in busy.json()["detail"]


# ---------- Google: events.insert with conferenceData ----------
def test_google_event_insert_body_and_meet_link_for_a_shared_calendar(tmp_path):
    http = FakeHTTP({"/events?conferenceDataVersion=1": R["google_event"]})
    c = client(tmp_path, http)
    lead = import_leads(c)[POOJA]
    connect_google(c, calendar_id="sales-team@group.calendar.google.com")
    first = book(c, lead, start=ist(3, 10).isoformat(), duration_minutes=45, notes="Pune warehouse, 40 roles")
    assert first.status_code == 200, first.text
    second = book(c, lead, start=ist(4, 15, 30).replace(tzinfo=None).isoformat())       # no zone: read as IST
    calls = [x for x in http.calls if "conferenceDataVersion" in x["url"]]
    assert len(calls) == 2
    url = calls[0]["url"]
    assert url.startswith("https://www.googleapis.com/calendar/v3/calendars/sales-team@group.calendar.google.com/events?")
    assert parse_qs(urlparse(url).query) == {"conferenceDataVersion": ["1"], "sendUpdates": ["all"]}
    body = calls[0]["body"]
    assert body["summary"] == "BookMyInterview.ai demo — Sagar Fintech" and body["description"] == "Pune warehouse, 40 roles"
    assert body["start"] == {"dateTime": ist(3, 10).isoformat(), "timeZone": "Asia/Kolkata"}
    assert body["end"] == {"dateTime": ist(3, 10, 45).isoformat(), "timeZone": "Asia/Kolkata"}
    assert body["attendees"] == [{"email": POOJA}]
    keys = [x["body"]["conferenceData"]["createRequest"] for x in calls]
    assert all(k["conferenceSolutionKey"] == {"type": "hangoutsMeet"} for k in keys)
    assert keys[0]["requestId"] != keys[1]["requestId"]                    # a new requestId per meeting
    assert calls[1]["body"]["start"]["dateTime"] == ist(4, 15, 30).isoformat()
    meeting = first.json()["meeting"]
    assert meeting["join_url"] == "https://meet.google.com/fak-emee-tqa" and meeting["external_id"] == R["google_event"]["id"]
    assert first.json()["invite"] is None and outbox(c) == []               # Google emails the attendee itself


def test_google_meet_link_from_entry_points_and_pending_conference(tmp_path):
    no_hangout = {k: v for k, v in R["google_event"].items() if k != "hangoutLink"}
    http = FakeHTTP({"/events?conferenceDataVersion=1": no_hangout})
    c = client(tmp_path, http)
    lead = import_leads(c)[POOJA]
    connect_google(c)
    assert book(c, lead, start=ist(3).isoformat()).json()["meeting"]["join_url"] == "https://meet.google.com/fak-emee-tqa"
    # Meet creation is asynchronous: statusCode "pending" has no entry points yet (product limitation, see report)
    http.routes["/events?conferenceDataVersion=1"] = R["google_event_pending"]
    pending = book(c, lead, start=ist(4).isoformat()).json()["meeting"]
    assert pending["join_url"] == R["google_event_pending"]["htmlLink"]


# ---------- Microsoft 365: auth-code flow + Graph event with Teams ----------
def test_microsoft_oauth_flow_and_tenant_is_validated(tmp_path):
    http = FakeHTTP({"login.microsoftonline.com": R["ms_token"]})
    c = client(tmp_path, http)
    c.put("/api/channels/calendar/microsoft", json={"values": MS_VALUES})
    start = c.get("/api/oauth/microsoft/start").json()
    url = urlparse(start["url"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.netloc}{url.path}" == "login.microsoftonline.com/common/oauth2/v2.0/authorize"
    assert q["response_mode"] == "query" and q["response_type"] == "code"
    assert set(q["scope"].split()) == {"offline_access", "User.Read", "Calendars.ReadWrite", "OnlineMeetings.ReadWrite"}
    r = c.get("/api/oauth/microsoft/callback", params={"state": q["state"], "code": "M.C1_fake"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("connected=microsoft")
    call = http.last("oauth2/v2.0/token")
    assert call["url"] == "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    assert call["form"]["grant_type"] == "authorization_code" and call["form"]["scope"] == q["scope"]
    tokens = c.app.state.store.read("integrations")["calendar"]["providers"]["microsoft"]["tokens"]
    assert tokens["refresh_token"] == "M.R3_BAY.FAKE-ms-refresh"
    c.put("/api/channels/calendar/microsoft", json={"values": {"tenant": "evil.example/../x"}})
    assert c.get("/api/oauth/microsoft/start").status_code == 400


def test_microsoft_graph_event_uses_utc_teams_and_cancel_endpoint(tmp_path):
    http = FakeHTTP({"graph.microsoft.com/v1.0/me/events": R["graph_event"]})
    c = client(tmp_path, http)
    lead = import_leads(c)[HARPREET]
    connect_microsoft(c)
    r = book(c, lead, start=ist(3, 10).isoformat())
    assert r.status_code == 200, r.text
    call = http.last("/me/events")
    assert call["url"] == "https://graph.microsoft.com/v1.0/me/events" and call["headers"]["Authorization"] == "Bearer ms.cached"
    body = call["body"]
    utc_start = ist(3, 10).astimezone(timezone.utc)                        # 10:00 IST is 04:30 UTC the same day
    assert body["start"] == {"dateTime": utc_start.strftime("%Y-%m-%dT04:30:00"), "timeZone": "UTC"}
    assert body["end"] == {"dateTime": utc_start.strftime("%Y-%m-%dT05:00:00"), "timeZone": "UTC"}
    assert body["isOnlineMeeting"] is True and body["onlineMeetingProvider"] == "teamsForBusiness"
    assert body["attendees"] == [{"emailAddress": {"address": HARPREET}, "type": "required"}]
    meeting = r.json()["meeting"]
    assert meeting["join_url"].startswith("https://teams.microsoft.com/l/meetup-join/")
    assert r.json()["invite"] is None                                      # Graph always emails attendees itself
    http.routes = {"/cancel": {"_status": 202}}
    out = c.post(f"/api/meetings/{meeting['id']}/cancel").json()
    cancel = http.last("/cancel")
    assert cancel["url"] == "https://graph.microsoft.com/v1.0/me/events/AAMkAGFAKE00EVENT00%3D/cancel"
    assert cancel["body"] == {"comment": "Cancelled from Outreach OS"} and out["meeting"]["status"] == "cancelled"


# ---------- Zoom: Server-to-Server OAuth + create meeting ----------
def test_zoom_token_request_cache_expiry_and_meeting_across_ist_midnight(tmp_path):
    http = zoom_http()
    c = client(tmp_path, http)
    lead = import_leads(c)[FARAH]
    connect_zoom(c)
    late = ist(3, 0, 15)                                                   # 00:15 IST is 18:45 UTC the day before
    r = book(c, lead, start=late.isoformat(), duration_minutes=45, notes="x" * 2500, title="  Intro — Desi Hospitality  ")
    assert r.status_code == 200, r.text
    token = http.calls[0]
    assert token["method"] == "POST" and token["url"].startswith("https://zoom.us/oauth/token?")
    assert parse_qs(urlparse(token["url"]).query) == {"grant_type": ["account_credentials"], "account_id": ["fakeAcct01"]}
    assert base64.b64decode(token["headers"]["Authorization"][6:]).decode() == "fakeZoomClient:fakeZoomSecret"
    body = http.last("users/me/meetings")["body"]
    day_before = (late - timedelta(days=1)).date().isoformat()
    assert body["start_time"] == f"{day_before}T18:45:00Z" and body["duration"] == 45 and body["type"] == 2
    assert body["topic"] == "Intro — Desi Hospitality" and len(body["agenda"]) == 2000
    meeting = r.json()["meeting"]
    assert meeting["external_id"] == "85746065432" and meeting["join_url"] == R["zoom_meeting"]["join_url"]
    for days in (4, 5):
        assert book(c, lead, start=ist(days).isoformat()).status_code == 200
    assert sum("oauth/token" in x["url"] for x in http.calls) == 1        # cached for the hour
    store = c.app.state.store
    later = time.time() + 3599                                             # an hour later: a new token, no refresh token
    zoom_token(store, http, now=later)
    assert sum("oauth/token" in x["url"] for x in http.calls) == 2


def test_zoom_refusal_and_errors_save_nothing(tmp_path):
    c = client(tmp_path, FakeHTTP({"zoom.us/oauth/token": PermissionError("HTTP 401")}))
    lead = import_leads(c)[FARAH]
    connect_zoom(c)
    r = book(c, lead, start=ist(3).isoformat())
    assert r.status_code == 400 and "Reconnect" in r.json()["detail"]
    for err in (HttpStatusError(400, '{"code": 300, "message": "Invalid meeting start time."}'), HttpStatusError(429, "Too Many Requests")):
        c2 = client(tmp_path / str(err.code), zoom_http({"users/me/meetings": err}))
        lead2 = import_leads(c2)[FARAH]
        connect_zoom(c2)
        bad = book(c2, lead2, start=ist(3).isoformat())
        assert bad.status_code == 502 and str(err.code) in bad.json()["detail"]
        assert meetings_of(c2, lead2["id"]) == [] and outbox(c2) == []


# ---------- cancelling when the calendar already lost the event ----------
def test_cancel_succeeds_when_the_event_was_already_deleted_in_the_calendar(tmp_path):
    """Google answers 410 Gone and Zoom 404 for an event deleted in the calendar itself; the meeting must still close."""
    gone = {"google": HttpStatusError(410, '{"error": {"code": 410, "message": "Resource has been deleted"}}'),
            "zoom": HttpStatusError(404, '{"code": 3001, "message": "Meeting does not exist: 85746065432."}')}
    for pid, error in gone.items():
        http = zoom_http() if pid == "zoom" else FakeHTTP({"/events?conferenceDataVersion=1": R["google_event"]})
        c = client(tmp_path / pid, http)
        lead = import_leads(c)[POOJA]
        connect_zoom(c) if pid == "zoom" else connect_google(c)
        meeting = book(c, lead, start=ist(3).isoformat()).json()["meeting"]
        http.routes = {**http.routes, f"/{meeting['external_id']}": error}
        out = c.post(f"/api/meetings/{meeting['id']}/cancel")
        assert out.status_code == 200, (pid, out.text)
        assert meetings_of(c, lead["id"])[0]["status"] == "cancelled"
    # any other provider error still stops the cancel, so the owner can retry
    http = FakeHTTP({"/events?conferenceDataVersion=1": R["google_event"]})
    c = client(tmp_path / "busy", http)
    lead = import_leads(c)[POOJA]
    connect_google(c)
    meeting = book(c, lead, start=ist(3).isoformat()).json()["meeting"]
    http.routes = {**http.routes, f"/{meeting['external_id']}": HttpStatusError(503, "Backend Error")}
    assert c.post(f"/api/meetings/{meeting['id']}/cancel").status_code == 502
    assert meetings_of(c, lead["id"])[0]["status"] == "scheduled"


# ---------- free / busy ----------
def test_free_busy_query_body_busy_blocks_and_calendar_errors(tmp_path):
    http = FakeHTTP({"calendar/v3/freeBusy": R["google_freebusy"]})
    c = client(tmp_path, http)
    assert c.get("/api/meetings/availability", params={"start": ist(3, 9).isoformat(), "end": ist(3, 18).isoformat()}).status_code == 400
    connect_google(c)
    r = c.get("/api/meetings/availability", params={"start": ist(3, 9).isoformat(), "end": ist(3, 18).isoformat()})
    assert r.status_code == 200 and r.json() == {"busy": R["google_freebusy"]["calendars"]["primary"]["busy"]}
    body = http.last("freeBusy")["body"]
    assert body["timeMin"] == (ist(3, 9) - timedelta(hours=5, minutes=30)).replace(tzinfo=None).isoformat() + "+00:00"
    assert body["items"] == [{"id": "primary"}] and body["timeZone"] == "Asia/Kolkata"
    # a wrong calendar id: Google reports notFound for that calendar instead of busy blocks
    connect_google(c, calendar_id="sales-team@group.calendar.google.com")
    http.routes = {"calendar/v3/freeBusy": R["google_freebusy_not_found"]}
    wrong = c.get("/api/meetings/availability", params={"start": ist(3, 9).isoformat(), "end": ist(3, 18).isoformat()})
    assert wrong.status_code == 400 and "notFound" in wrong.json()["detail"], wrong.text
    http.routes = {"calendar/v3/freeBusy": PermissionError("HTTP 403")}
    assert "Reconnect" in c.get("/api/meetings/availability", params={"start": ist(3, 9).isoformat(),
                                                                         "end": ist(3, 18).isoformat()}).json()["detail"]


# ---------- booking-link providers ----------
def test_booking_links_are_prefilled_and_logged(tmp_path):
    http = FakeHTTP({"api.calendly.com/users/me": R["calendly_users_me"], "webhook_subscriptions": PermissionError("HTTP 403")})
    c = client(tmp_path, http)
    leads = import_leads(c)
    lead = leads["ravi.kumar@shubh-logistics-00.example"]                  # imported as "RAVI KUMAR"
    save_calcom(c)
    out = book(c, lead).json()
    assert out["kind"] == "link" and out["url"] == "https://cal.com/priya-bmi-fake/demo?name=Ravi+Kumar&email=ravi.kumar%40shubh-logistics-00.example"
    assert c.get(f"/api/leads/{lead['id']}").json()["timeline"][-1]["text"] == f"Booking link prepared: {out['url']}"
    assert book(c, lead, start=ist(3).isoformat()).json()["kind"] == "link"   # Cal.com ignores a fixed time
    c.put("/api/channels/calendar/calendly", json={"values": {"access_token": "fake-pat"}})
    test = c.post("/api/channels/calendar/calendly/test", json={}).json()
    assert test["ok"] is True and "paid Calendly plan" in test["message"]
    link = book(c, lead, provider="calendly", attendee_email="  Anita.Nair@SHUBH-LOGISTICS-00.example ").json()
    assert link["url"] == "https://calendly.com/priya-bmi-fake?name=Ravi+Kumar&email=anita.nair%40shubh-logistics-00.example"
    c.put("/api/channels/calendar/booking_link", json={"values": {"booking_url": "https://book.example/priya?who={name}&mail={email}&src=bmi"}})
    plain = book(c, lead, provider="booking_link").json()
    assert plain["url"] == "https://book.example/priya?who=Ravi%20Kumar&mail=ravi.kumar%40shubh-logistics-00.example&src=bmi"
    assert unquote(plain["url"]).count("{") == 0 and outbox(c) == []


# ---------- the lead-panel booking API: rules and roles ----------
def test_booking_rules_ist_labels_and_roles(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password="owner-pass-123", channel_transport=zoom_http(),
                              resolver=lambda h: ["93.184.216.34"]))
    owner = {"Authorization": "Bearer " + c.post("/api/login", json={"password": "owner-pass-123"}).json()["token"]}
    for email, role in (("caller@team.example", "caller"), ("viewer@team.example", "viewer")):
        assert c.post("/api/users", json={"email": email, "name": role, "role": role, "password": "long-password-1"},
                      headers=owner).status_code == 200
    token = lambda e: {"Authorization": "Bearer " + c.post("/api/login", json={"email": e, "password": "long-password-1"}).json()["token"]}
    caller, viewer = token("caller@team.example"), token("viewer@team.example")
    c.post("/api/import/commit", json={"csv": "Company,First Name,Last Name,Email\nDesi Hospitality Ltd,Farah,Khan," + FARAH + "\n"}, headers=owner)
    lead = c.get("/api/leads", headers=owner).json()[0]
    c.put("/api/channels/calendar/zoom", json={"values": ZOOM_VALUES}, headers=owner)
    path = f"/api/leads/{lead['id']}/meetings"
    when = ist(3, 0, 15)
    assert c.post(path, json={"start": when.isoformat()}, headers=viewer).status_code == 403
    r = c.post(path, json={"start": when.isoformat()}, headers=caller)
    assert r.status_code == 200, r.text
    label = when.strftime("%a %d %b %Y, 12:15 AM IST")
    assert r.json()["lead"]["timeline"][-2]["text"] == f"Meeting booked: BookMyInterview.ai demo — Desi Hospitality, {label}"
    meeting = r.json()["meeting"]
    assert c.post(f"/api/meetings/{meeting['id']}/done", headers=viewer).status_code == 403
    assert c.post(f"/api/meetings/{meeting['id']}/cancel", headers=caller).status_code == 200
    assert c.post(f"/api/meetings/{meeting['id']}/done", headers=caller).status_code == 400          # cancelled stays cancelled
    for bad, code in (({"start": when.isoformat(), "duration_minutes": 4}, 422), ({"start": when.isoformat(), "duration_minutes": 481}, 422),
                      ({"start": "05/11/2026 10:00"}, 400), ({"start": ist(-1).isoformat()}, 400),
                      ({"start": when.isoformat(), "attendee_email": "farah at desi"}, 400)):
        assert c.post(path, json=bad, headers=caller).status_code == code, bad
    assert c.get("/api/oauth/google/start", headers=caller).status_code == 403                       # owner only
