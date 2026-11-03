"""QA: SMS adapters (MSG91 flow / DLT, Fast2SMS, Twilio), calling (click-to-call, Exotel) and the call desk task loop."""
from datetime import timedelta

import pytest

from app.channels.calls import start_call
from app.channels.sms_send import send_sms
from tests.channel_fakes import FakeHTTP
from tests.qa.whatsapp_sms_calls_support import *  # noqa: F401,F403  (autouse no_network)
from tests.qa.whatsapp_sms_calls_support import (Clock, client, connect_whatsapp, error_body, fresh, http_error,
                                                 import_fixture_leads, lead_of, meta_message, meta_post, phone,
                                                 store_connect, tasks_for, utc, webhook_path)

MSG91 = {"authkey": "msg91-qa-key", "template_id": "6650f0d1d6fc05qa", "sender_id": "BMIHRS",
         "dlt_entity_id": "1201000000000000001", "dlt_template_id": "1207000000000000001"}
FAST2SMS = {"api_key": "f2s-qa-key", "sender_id": "BMIHRS", "message_id": "171000"}
TWILIO = {"account_sid": "ACQA000000000000000000000000000000", "auth_token": "tw-qa", "from_number": "BMIHRS"}
EXOTEL = {"subdomain": "api.in.exotel.com", "account_sid": "bmiqa1", "api_key": "exo-key", "api_token": "exo-token",
          "caller_id": "08047000000", "agent_phone": "+91 90000 19000"}


@pytest.fixture
def c(tmp_path):
    cl = client(tmp_path)
    import_fixture_leads(cl)
    return cl


@pytest.fixture
def store(c):
    return c.app.state.store


# ---------- SMS ----------
def test_sms_is_practice_until_connected_and_live(store):
    http = FakeHTTP()
    assert send_sms(store, "+91 90000 10001", "hi", transport=http) == {"status": "dry_run"}
    store_connect(store, "sms", "msg91", MSG91)
    assert send_sms(store, "+91 90000 10001", "hi", transport=http) == {"status": "dry_run"}
    assert http.calls == []
    store.update("settings", lambda s: {**s, "live_sms": True})
    http.routes = {"control.msg91.com": {"type": "success", "message": "3567686f6a5a313530303031"}}
    assert send_sms(store, "+91 90000 10001", "", variables=["Priyanka", "Asha"], transport=http)["status"] == "sent"


def test_msg91_flow_body_uses_dlt_template_and_ordered_vars(store):
    store_connect(store, "sms", "msg91", MSG91, live_flag="live_sms")
    http = FakeHTTP({"control.msg91.com": {"type": "success", "message": "3567686f6a5a313530303031"}})
    out = send_sms(store, "09000010003", "", variables=["Lakshmi", "Asha", "Sai Krupa Traders"], transport=http)
    assert out == {"status": "sent", "provider_id": "3567686f6a5a313530303031"}
    call = http.last("msg91")
    assert call["url"] == "https://control.msg91.com/api/v5/flow" and call["headers"]["authkey"] == "msg91-qa-key"
    assert call["body"] == {"template_id": "6650f0d1d6fc05qa", "short_url": "0", "recipients": [
        {"mobiles": "919000010003", "var1": "Lakshmi", "var2": "Asha", "var3": "Sai Krupa Traders"}]}
    http.routes = {"control.msg91.com": error_body("msg91_bad_template")}
    bad = send_sms(store, "09000010003", "", variables=["x"], transport=http)
    assert bad["status"] == "failed" and "Invalid template id" in bad["error"]


def test_fast2sms_dlt_route_takes_national_numbers_and_pipe_vars(store):
    store_connect(store, "sms", "fast2sms", FAST2SMS, live_flag="live_sms")
    http = FakeHTTP({"fast2sms.com": {"return": True, "request_id": "f2s-req-1", "message": ["SMS sent successfully."]}})
    assert send_sms(store, "(+91) 90000-10005", "", variables=["Arvind", "Asha"], transport=http)["provider_id"] == "f2s-req-1"
    body = http.last("fast2sms")["body"]
    assert body["route"] == "dlt" and body["numbers"] == "9000010005" and body["variables_values"] == "Arvind|Asha"
    assert body["sender_id"] == "BMIHRS" and body["message"] == "171000"
    http.routes = {"fast2sms.com": error_body("fast2sms_invalid_numbers")}
    assert "Invalid Numbers" in send_sms(store, "9000010005", "", transport=http)["error"]


def test_twilio_sms_form_and_errors(store):
    store_connect(store, "sms", "twilio_sms", TWILIO, live_flag="live_sms")
    http = FakeHTTP({"api.twilio.com": {"sid": "SMqa1", "status": "queued"}})
    assert send_sms(store, "+91 90000 10002", "Hi Sanjay, Asha from BMI. Reply STOP to opt out.", transport=http)["provider_id"] == "SMqa1"
    call = http.last("twilio")
    assert call["form"] == {"To": "+919000010002", "Body": "Hi Sanjay, Asha from BMI. Reply STOP to opt out.", "From": "BMIHRS"}
    assert call["url"].endswith("/Accounts/ACQA000000000000000000000000000000/Messages.json")
    assert "needs the message text" in send_sms(store, "+919000010002", "", transport=http)["error"]
    http.routes = {"api.twilio.com": http_error("twilio_21211")}
    assert "21211" in send_sms(store, "+919000010002", "x", transport=http)["error"]
    http.routes = {"api.twilio.com": PermissionError("HTTP 401")}
    assert send_sms(store, "+919000010002", "x", transport=http)["error"] == "The provider rejected the key or token."


@pytest.mark.parametrize("bad", ["", "12345", "+91 90000", "abc", "0000000000"])
def test_sms_refuses_invalid_numbers_without_calling(store, bad):
    store_connect(store, "sms", "msg91", MSG91, live_flag="live_sms")
    http = FakeHTTP()
    assert send_sms(store, bad, "", transport=http)["status"] == "failed" and http.calls == []


def test_sms_test_button_sends_through_the_channel_transport(tmp_path):
    http = FakeHTTP({"control.msg91.com": {"type": "success", "message": "req-1"}})
    c = client(tmp_path, http=http)
    assert c.put("/api/channels/sms/msg91", json={"values": MSG91, "make_active": True}).status_code == 200
    r = c.post("/api/channels/sms/msg91/test", json={"to": "+91 90000 10001"}).json()
    assert r["ok"] and http.last("msg91")["body"]["recipients"][0]["mobiles"] == "919000010001"


# ---------- calls ----------
def test_calls_dry_run_click_to_call_and_exotel(store):
    http = FakeHTTP()
    assert start_call(store, None, "+919000010001", transport=http) == {"status": "dry_run"}
    store_connect(store, "calls", "click_to_call", {"click_to_call_url": "https://pd.example.invalid/dial?num={phone}&agent=priya"})
    link = start_call(store, None, "+919000010001", transport=http)
    assert link == {"status": "link", "url": "https://pd.example.invalid/dial?num=+919000010001&agent=priya"} and http.calls == []
    store_connect(store, "calls", "exotel", EXOTEL)
    http.routes = {"exotel.com": {"Call": {"Sid": "b6cfaf5d3bqa", "Status": "in-progress"}}}
    assert start_call(store, None, "09000010003", transport=http) == {"status": "started", "provider_id": "b6cfaf5d3bqa"}
    call = http.last("exotel")
    assert call["url"] == "https://api.in.exotel.com/v1/Accounts/bmiqa1/Calls/connect.json"
    assert call["form"] == {"From": "+919000019000", "To": "+919000010003", "CallerId": "08047000000"}
    assert call["headers"]["Authorization"].startswith("Basic ")
    http.routes = {"exotel.com": http_error("exotel_bad_request")}
    assert "Invalid CallerId" in start_call(store, None, "09000010003", transport=http)["error"]
    assert start_call(store, None, "12345", transport=http)["error"] == "The lead's phone number is not valid."


def test_click_to_call_saved_on_the_hub_reaches_the_call_desk(c):
    url = "https://pd.example.invalid/dial?num={phone}"
    assert c.put("/api/channels/calls/click_to_call", json={"values": {"click_to_call_url": url}, "make_active": True}).status_code == 200
    tel = next(i for i in c.get("/api/integrations").json() if i["id"] == "telephony")
    # web/src/pages/CallDesk.jsx reads exactly these two fields to build the dial link
    assert tel["mode"] == "api" and tel["values"]["click_to_call_url"] == url
    assert c.post("/api/channels/calls/click_to_call/test", json={}).json()["ok"]
    bad = c.put("/api/channels/calls/click_to_call", json={"values": {"click_to_call_url": "https://pd.example.invalid/dial"}})
    assert bad.status_code == 400 or not c.post("/api/channels/calls/click_to_call/test", json={}).json()["ok"]


# ---------- the call desk loop ----------
def test_whatsapp_yes_gives_priya_a_call_task_and_logging_the_call_closes_it(c, store, monkeypatch):
    connect_whatsapp(c, "meta_cloud", live=False)
    path, _ = webhook_path(c, "whatsapp", "meta_cloud")
    clock = Clock(monkeypatch, utc(2026, 11, 6, 13, 30))          # Friday 19:00 IST, after the 18:30 cut-off
    lead = lead_of(c, 16)
    meta_post(c, meta_message(phone(16), "Haan ji, call kar lijiye", "wamid.QA.Y16", ts=int(clock.now.timestamp())), path)
    [task] = tasks_for(store, lead["id"], "call", "open")
    assert task["owner"] == "Priya" and task["due_at"].startswith("2026-11-09T05:30")   # Monday 11:00 IST
    # a second yes over the weekend does not stack another call task
    clock.set(utc(2026, 11, 7, 6, 0))
    meta_post(c, meta_message(phone(16), "haan, Monday theek hai", "wamid.QA.Y16b", ts=int(clock.now.timestamp())), path)
    assert len(tasks_for(store, lead["id"], "call", "open")) == 1
    today = c.get("/api/today").json()
    assert any(i.get("lead_id") == lead["id"] and i["kind"] == "call" for i in today["items"])
    # a plain note does not close it; the call desk's outcome (kind=call) does
    c.post(f"/api/leads/{lead['id']}/note", json={"text": "Sent the deck", "kind": "note"})
    assert len(tasks_for(store, lead["id"], "call", "open")) == 1
    clock.set(utc(2026, 11, 9, 5, 45))
    c.post(f"/api/leads/{lead['id']}/note", json={"text": "Call outcome: meeting booked", "kind": "call"})
    assert tasks_for(store, lead["id"], "call", "open") == []
    done = tasks_for(store, lead["id"], "call", "done")[0]
    assert done["done_note"] == "Call outcome: meeting booked" and done["done_at"].startswith("2026-11-09T05:45")


def test_activity_log_call_closes_task_for_a_renamed_caller(c, store, monkeypatch):
    c.put("/api/team", json={"caller_name": "Anita"})
    connect_whatsapp(c, "meta_cloud", live=False)
    path, _ = webhook_path(c, "whatsapp", "meta_cloud")
    Clock(monkeypatch, utc(2026, 11, 3, 6, 0))                    # Tuesday 11:30 IST: due the same day
    lead = lead_of(c, 17)
    meta_post(c, meta_message(phone(17), "Yes, send the sample", "wamid.QA.Y17"), path)
    [task] = tasks_for(store, lead["id"], "call", "open")
    assert task["owner"] == "Anita" and task["due_at"].startswith("2026-11-03T06:00")
    r = c.post(f"/api/leads/{lead['id']}/activity", json={"type": "call", "outcome": "no_answer", "duration_minutes": 0})
    assert r.status_code == 200 and tasks_for(store, lead["id"], "call", "open") == []
    assert c.post(f"/api/leads/{lead['id']}/activity", json={"type": "call", "outcome": "ringing"}).status_code == 400
    assert "Call — no answer" in [e["text"] for e in fresh(c, lead["id"])["timeline"]][-1]
