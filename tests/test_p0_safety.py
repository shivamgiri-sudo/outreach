"""P0 safety fixes: reply reading, practice mode, provider pause, safe claims, inbound replies, bounces, go-live rules."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import sequence_runner
from app.http_util import HttpStatusError
from app.inbound import process_delivery_event, process_inbound_message
from app.main import create_app
from app.replies import classify_reply, strip_quoted
from app.store import Store

T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)   # Tuesday 10:30 IST
CSV = ("company,contact_name,email,phone,website,open_roles,company_size,country\n"
       "Acme,Ravi Kumar,ravi@acme.com,98765 43210,https://acme.com,12,200,IN\n")
IDENTITY = {"sender_company": "BookMyInterview.ai", "sender_address": "Sector 62, Noida 201309, India"}


class FakeTransport:
    def __init__(self, reply=None, error=None):
        self.calls, self.reply, self.error = [], reply or {"id": "em_1"}, error

    def __call__(self, url, headers, body):
        self.calls.append({"url": url, "headers": headers, "body": body})
        if self.error:
            raise self.error
        return self.reply


def make(tmp_path, transport=None):
    c = TestClient(create_app(data_file=tmp_path / "d.json", email_transport=transport or FakeTransport()))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    return c


def lead(c):
    return c.get("/api/leads").json()[0]


def connect_and_go_live(c):
    c.put("/api/integrations/sending_domain", json={"mode": "api", "values": {
        "domain": "mail.example.in", "from_name": "Priya", "from_email": "hello@mail.example.in", "api_key": "re_secret"}})
    c.put("/api/settings", json={"public_base_url": "https://outreach.example.in", **IDENTITY})
    assert c.put("/api/settings", json={"live_sending": True}).status_code == 200


def run(c, at=T0):
    return c.post("/api/sequences/run", json={"now": at.isoformat()}).json()


# ---------- reply reading ----------
@pytest.mark.parametrize("text,label", [
    ("We aren't interested", "not_interested"),
    ("isn't interested, thanks", "not_interested"),
    ("We have 550 employees and need help", "other"),            # an ordinary number is not a bounce code
    ("550 5.1.1 The email account that you tried to reach does not exist", "bounce"),
    ("Sure, call me later", "interested"),
    ("Yes, but busy this week", "interested"),
    ("Can you send the pricing?", "question"),                    # a question is not consent
    ("Please stop", "unsubscribe"),
    ("Stop it", "unsubscribe"),
    ("No.", "not_interested"),
    ("nahin chahiye", "not_interested"),
    ("Mujhe interest nahi hai", "not_interested"),
    ("Remove karo please", "unsubscribe"),
    ("delete my data", "unsubscribe"),
    ("Please reach out to me on 98100 00000", "interested"),
    ("Please contact our HR team at hr@x.in", "wrong_person"),
    ("No problem, send it", "interested"),
    ("I have left the company", "wrong_person"),
])
def test_reply_reader_handles_contractions_hinglish_and_stop_requests(text, label):
    assert classify_reply(text) == label


def test_quoted_history_never_decides_the_label():
    reply = "Thanks!\n\nOn Tue, 3 Nov 2026 Priya <p@mail.x.in> wrote:\n> Not relevant? Unsubscribe: https://x/u/1"
    assert strip_quoted(reply) == "Thanks!"
    assert classify_reply(reply) == "other"
    assert classify_reply("Yes please\n> reply stop to unsubscribe") == "interested"


# ---------- practice mode ----------
def test_practice_runs_never_use_up_the_real_first_email(tmp_path):
    t = FakeTransport()
    c = make(tmp_path, t)
    lid = lead(c)["id"]
    c.post(f"/api/leads/{lid}/enroll")
    assert run(c)["mode"] == "practice" and run(c)["sent"] == 0      # practised once; same tick does nothing more
    practised = lead(c)
    assert practised["sequence"]["practice_days"] == [0] and practised["sequence"]["sent_days"] == []
    assert practised["stage"] == "new"                                # practice doesn't move the lead
    connect_and_go_live(c)
    result = run(c, T0 + timedelta(minutes=15))
    assert result["mode"] == "live" and result["sent"] == 1 and len(t.calls) == 1   # the real Email 1 still goes out
    assert lead(c)["sequence"]["sent_days"] == [0] and lead(c)["stage"] == "contacted"


def test_clearing_practice_removes_rehearsal_rows(tmp_path):
    c = make(tmp_path)
    lid = lead(c)["id"]
    c.post(f"/api/leads/{lid}/enroll")
    run(c)
    assert c.post("/api/practice/clear").json()["outbox"] == 1
    assert c.get("/api/outbox").json() == [] and "practice_days" not in lead(c)["sequence"]


# ---------- provider problems pause sending instead of killing leads ----------
def test_provider_outage_pauses_sending_without_burning_attempts(tmp_path):
    t = FakeTransport(error=HttpStatusError(503, "busy"))
    c = make(tmp_path, t)
    connect_and_go_live(c)
    lid = lead(c)["id"]
    c.post(f"/api/leads/{lid}/enroll")
    result = run(c)
    assert result["sent"] == 0 and "paused" in result
    seq = lead(c)["sequence"]
    assert "in_flight" not in seq and seq.get("failed_attempts") is None and seq["stopped"] is None
    assert "paused" in run(c, T0 + timedelta(minutes=15))["skipped"]          # no hammering while paused
    t.error = None
    assert run(c, T0 + timedelta(hours=1, minutes=1))["sent"] == 1           # lifts itself after an hour


def test_rejected_key_pauses_until_a_person_resumes(tmp_path):
    t = FakeTransport(error=PermissionError("HTTP 401"))
    c = make(tmp_path, t)
    connect_and_go_live(c)
    c.post(f"/api/leads/{lead(c)['id']}/enroll")
    run(c)
    status = c.get("/api/sending/status").json()
    assert status["paused"] and status["paused"]["resume_at"] is None
    t.error = None
    assert "paused" in run(c, T0 + timedelta(hours=3))["skipped"]
    c.post("/api/sending/resume")
    assert run(c, T0 + timedelta(hours=3, minutes=15))["sent"] == 1


def test_a_rejected_recipient_fails_only_that_message(tmp_path):
    t = FakeTransport(error=HttpStatusError(422, "invalid to"))
    c = make(tmp_path, t)
    connect_and_go_live(c)
    c.post(f"/api/leads/{lead(c)['id']}/enroll")
    assert run(c)["failed"] == 1
    assert lead(c)["sequence"]["failed_attempts"] == 1 and not c.get("/api/sending/status").json()["paused"]


def test_stopped_after_failures_can_be_resumed(tmp_path):
    c = make(tmp_path)
    lid = lead(c)["id"]
    c.post(f"/api/leads/{lid}/enroll")
    store = Store(tmp_path / "d.json")
    store.update_lead(lid, lambda l: {**l, "sequence": {**l["sequence"], "stopped": "send_failed", "failed_attempts": 3}})
    c2 = TestClient(create_app(data_file=tmp_path / "d.json"))
    assert c2.post("/api/sequences/resume-failed").json()["resumed"] == 1
    assert c2.get(f"/api/leads/{lid}").json()["sequence"]["stopped"] is None


# ---------- safe claim ----------
def test_claim_rechecks_the_latest_lead(tmp_path):
    c = make(tmp_path)
    lid = lead(c)["id"]
    c.post(f"/api/leads/{lid}/enroll")
    store = Store(tmp_path / "d.json")
    assert sequence_runner._claim(store, lid, 0, T0, "ravi@acme.com") is True
    assert sequence_runner._claim(store, lid, 0, T0, "ravi@acme.com") is False          # already in flight
    sequence_runner._release(store, lid)
    store.update("suppressed", lambda rows: [*rows, "ravi@acme.com"])
    assert sequence_runner._claim(store, lid, 0, T0, "ravi@acme.com") is False          # suppressed a moment ago


# ---------- inbound replies ----------
def test_inbound_reply_matches_the_thread_and_stops_the_sequence(tmp_path):
    t = FakeTransport()
    c = make(tmp_path, t)
    connect_and_go_live(c)
    lid = lead(c)["id"]
    c.post(f"/api/leads/{lid}/enroll")
    run(c)
    our_id = c.get("/api/outbox").json()[0]["message_id"]
    store = Store(tmp_path / "d.json")
    msg = {"channel": "email", "from_email": "someone-else@acme.com", "subject": "Re: hiring", "text": "No.\n\nOn Tue Priya wrote:\n> hi",
           "message_id": "<r1@acme.com>", "in_reply_to": our_id, "references": [], "provider": "imap"}
    result = process_inbound_message(store, msg)
    assert result["matched"] and result["matched_by"] == "thread" and result["label"] == "not_interested"
    saved = store.read("leads")[0]
    assert saved["sequence"]["stopped"] == "replied" and saved["stage"] == "disqualified"
    assert process_inbound_message(store, msg)["duplicate"] is True                 # the same message is handled once


def test_inbound_by_address_unsubscribe_suppresses_and_withdraws_consent(tmp_path):
    c = make(tmp_path)
    lid = lead(c)["id"]
    c.post(f"/api/leads/{lid}/consent", json={"text": "yes on call"})
    store = Store(tmp_path / "d.json")
    result = process_inbound_message(store, {"channel": "email", "from_email": "Ravi@Acme.com", "text": "Please stop",
                                             "message_id": "<r2@acme.com>"})
    assert result["label"] == "unsubscribe"
    saved = store.read("leads")[0]
    assert saved["stage"] == "unsubscribed" and saved["consent"] is None and saved["consent_withdrawn_at"]
    assert "ravi@acme.com" in store.read("suppressed")


def test_unmatched_reply_becomes_a_task_but_other_mail_is_ignored(tmp_path):
    make(tmp_path)
    store = Store(tmp_path / "d.json")
    newsletter = process_inbound_message(store, {"from_email": "alerts@linkedin.com", "subject": "Your code", "text": "123456", "message_id": "<n>"})
    assert newsletter["ignored"] and store.read("inbox") == [] and store.read("tasks") == []
    assert process_inbound_message(store, {"from_email": "who@nowhere.in", "subject": "Re: Hiring at Acme", "text": "hello",
                                           "message_id": "<x>"})["matched"] is False
    assert store.read("tasks")[-1]["kind"] == "unmatched_reply"


def test_whatsapp_reply_matches_by_phone_and_opens_the_window(tmp_path):
    make(tmp_path)
    store = Store(tmp_path / "d.json")
    result = process_inbound_message(store, {"channel": "whatsapp", "from_phone": "919876543210", "text": "haan bhej do", "raw_id": "wamid.1"})
    assert result["matched_by"] == "phone" and result["label"] == "interested"
    assert store.read("leads")[0]["whatsapp_last_inbound_at"]


# ---------- bounces and complaints ----------
def test_bounce_suppresses_and_complaint_unsubscribes(tmp_path):
    c = make(tmp_path)
    c.post(f"/api/leads/{lead(c)['id']}/enroll")
    store = Store(tmp_path / "d.json")
    process_delivery_event(store, {"type": "bounce", "email": "ravi@acme.com", "provider_id": "em_1"})
    saved = store.read("leads")[0]
    assert saved["stage"] == "disqualified" and saved["sequence"]["stopped"] == "bounced"
    assert "ravi@acme.com" in store.read("suppressed")
    assert process_delivery_event(store, {"type": "bounce", "email": "ravi@acme.com", "provider_id": "em_1"})["duplicate"]
    process_delivery_event(store, {"type": "complaint", "email": "ravi@acme.com", "provider_id": "em_1"})
    assert store.read("leads")[0]["stage"] == "unsubscribed"


def test_high_bounce_rate_pauses_live_sending(tmp_path):
    make(tmp_path)
    store = Store(tmp_path / "d.json")
    sent = [{"lead_id": "x", "to": f"p{i}@co.in", "status": "sent", "sent_at": T0.isoformat(), "sent_at_ist_date": "2026-11-03"} for i in range(100)]
    store.update("outbox", lambda rows: [*rows, *sent])
    for i in range(2):
        assert process_delivery_event(store, {"type": "bounce", "email": f"p{i}@co.in", "provider_id": f"b{i}"}, now=T0)["paused"] is None
    assert process_delivery_event(store, {"type": "bounce", "email": "p2@co.in", "provider_id": "b2"}, now=T0)["paused"]
    assert store.read("settings")["sending_paused"]["resume_at"] is None


# ---------- going live ----------
def test_live_sending_needs_https_address_sender_and_identity(tmp_path):
    c = make(tmp_path)
    refused = c.put("/api/settings", json={"live_sending": True})
    assert refused.status_code == 400
    detail = refused.json()["detail"]
    assert "https" in detail and "sending domain" in detail and "company name" in detail
    assert c.get("/api/settings").json()["live_sending"] is False
    blockers = c.get("/api/sending/status").json()["live_blockers"]
    assert len(blockers) == 3


def test_live_email_carries_the_sender_identity_footer(tmp_path):
    t = FakeTransport()
    c = make(tmp_path, t)
    connect_and_go_live(c)
    c.post(f"/api/leads/{lead(c)['id']}/enroll")
    run(c)
    assert "BookMyInterview.ai · Sector 62, Noida 201309, India" in t.calls[0]["body"]["text"]


def test_caps_are_validated_and_server_secret_is_never_shown(tmp_path):
    c = make(tmp_path)
    assert c.put("/api/settings", json={"daily_cap": 0}).status_code == 400
    assert c.put("/api/settings", json={"per_run_cap": 500}).status_code == 400
    Store(tmp_path / "d.json").update("settings", lambda s: {**s, "server_secret": "abc"})
    c2 = TestClient(create_app(data_file=tmp_path / "d.json"))
    assert "server_secret" not in c2.get("/api/settings").json()


def test_without_a_postal_address_the_footer_uses_website_and_email_and_us_leads_wait(tmp_path):
    t = FakeTransport()
    c = TestClient(create_app(data_file=tmp_path / "d.json", email_transport=t))
    c.post("/api/leads/import", json={"csv": "company,contact_name,email,website,country\n"
                                              "Acme,Ravi Kumar,ravi@acme.com,https://acme.com,IN\n"
                                              "Yankee,Joe Doe,joe@yankee-co.com,https://yankee-co.com,US\n", "source": "apollo"})
    c.put("/api/integrations/sending_domain", json={"mode": "api", "values": {
        "domain": "mail.example.in", "from_name": "Priya", "from_email": "hello@mail.example.in", "api_key": "re_secret"}})
    c.put("/api/settings", json={"public_base_url": "https://outreach.example.in", "sender_company": "BookMyInterview.ai"})
    refused = c.put("/api/settings", json={"live_sending": True})
    assert refused.status_code == 400 and "website and a contact email" in refused.json()["detail"]
    c.put("/api/settings", json={"sender_website": "bookmyinterview.ai", "sender_contact_email": "support@bookmyinterview.ai"})
    assert c.put("/api/settings", json={"live_sending": True}).status_code == 200
    for lead in c.get("/api/leads").json():
        c.post(f"/api/leads/{lead['id']}/enroll")
    result = c.post("/api/sequences/run", json={"now": T0.isoformat()}).json()
    assert result["sent"] == 1 and result["blocked"] == 1                    # the US lead waits for a postal address
    assert [call["body"]["to"] for call in t.calls] == [["ravi@acme.com"]]
    assert "BookMyInterview.ai · bookmyinterview.ai · support@bookmyinterview.ai" in t.calls[0]["body"]["text"]
