"""A small, fixed data set for the download and backup tests: three leads, messages of every kind, tasks, a campaign,
a meeting, a duplicate, the suppression list, and secrets in every place the app keeps them."""
import csv
import io
from datetime import timedelta

from fastapi.testclient import TestClient

from app.activities import iso, now_utc
from app.lead_model import build_lead
from app.main import create_app

BOM = "﻿"
SECRETS = ("re_live_SECRET_api_key_1", "whsec_SECRET_webhook_2", "server_SECRET_3", "ya29.SECRET_oauth_token_4",
           "hook_SECRET_token_5", "imap_SECRET_password_6", "Bearer HEADER_SECRET_7", "DEMO_TOKEN_SECRET_8",
           "REF_CODE_SECRET_9")


def make_client(tmp_path, **kwargs):
    app = create_app(data_file=tmp_path / "store.json", **kwargs)
    return TestClient(app), app.state.store


def _lead(lead_id: str, row: dict, source: str, **extra) -> dict:
    lead = build_lead(row, source)
    return {**lead, "id": lead_id, **extra}


def seed(store) -> dict:
    """Fill the store; returns handy values (ids, times) for the assertions."""
    now = now_utc()
    leads = [
        _lead("l1", {"company": '=HYPERLINK("http://evil.example","x")', "contact_name": "Ravi Kumar", "title": "Founder",
                     "email": "ravi@acme.in", "phone": "98765 43210", "website": "https://acme.in", "country": "IN",
                     "open_roles": "60", "company_size": "200", "max_job_age_days": "30"}, "apollo",
              campaign_id="c1", tags=["vip"], stage="contacted", created_at="2026-11-01T04:00:00+00:00",
              consent={"text": "Yes on a call with Priya", "at": "2026-11-02T06:30:00+00:00", "source": "recorded by a person",
                       "channel": "whatsapp"},
              sequence={"enrolled_at": "2026-11-01T05:00:00+00:00", "sent_days": [0, 3], "stopped": None},
              linkedin_seq={"status": "waiting_for_acceptance", "done": [1]}, linkedin_url="https://www.linkedin.com/in/ravi",
              demo_token=SECRETS[7], referral_code=SECRETS[8],
              meetings=[{"id": "m1", "provider": "google", "title": "Demo with Acme", "start": "2026-11-05T10:00:00+05:30",
                         "end": "2026-11-05T10:30:00+05:30", "join_url": "https://meet.google.com/abc", "status": "scheduled",
                         "created_at": "2026-11-03T04:00:00+00:00", "external_id": "ev1", "attendee_email": "ravi@acme.in"}],
              campaign_check={"qualified": True, "reasons": []}),
        _lead("l2", {"company": "Śrī Gaṇeśa Traders ₹", "contact_name": "@admin", "email": "sam@ganesa.in"},
              "web_scraper:https://ganesa.in", created_at="2026-10-01T04:00:00+00:00"),
        _lead("l3", {"company": "Gamma Foods", "contact_name": "-Neha", "email": "neha@gamma.in", "phone": "9123456780",
                     "country": "IN", "open_roles": "60", "company_size": "200"}, "manual", campaign_id="c1", stage="new",
              campaign_check={"qualified": False, "reasons": ["no email found yet", "score below 40"]}),
        _lead("l4", {"company": "Gamma Foods Pvt Ltd", "contact_name": "Neha", "email": "neha@gamma.in"}, "manual"),
    ]
    store.write("leads", leads)
    store.write("outbox", [
        {"lead_id": "l1", "to": "ravi@acme.in", "subject": "Hiring at Acme", "body": "Hi Ravi, full email text",
         "step": "Email 1", "variant": "default", "sent_at": "2026-11-03T04:00:00+00:00", "status": "sent",
         "provider_id": "p1", "delivered_at": "2026-11-03T04:01:00+00:00"},
        {"lead_id": "l1", "to": "ravi@acme.in", "subject": "Follow-up", "body": "Practice body", "step": "Email 2",
         "variant": "with_link", "sent_at": "2026-11-03T05:00:00+00:00", "status": "dry_run"},
        {"lead_id": "l3", "to": "neha@gamma.in", "subject": "+1 idea", "body": "x", "step": "Email 1", "variant": "default",
         "sent_at": "2026-11-03T06:00:00+00:00", "status": "failed", "error": "mailbox full"},
    ])
    store.write("inbox", [
        {"id": "i1", "message_id": "<a@x>", "channel": "email", "provider": "imap", "from": "ravi@acme.in",
         "subject": "Re: Hiring at Acme", "text": "Yes, send details", "received_at": "2026-11-03T07:00:00+00:00",
         "lead_id": "l1", "matched_by": "thread", "label": "interested"},
        {"id": "i2", "message_id": "<b@x>", "channel": "email", "provider": "imap", "from": "stranger@x.in",
         "subject": "Re: hello", "text": "who is this", "received_at": "2026-11-03T08:00:00+00:00", "lead_id": None,
         "matched_by": None, "label": None},
    ])
    store.write("whatsapp_outbox", [
        {"lead_id": "l1", "to": "+919876543210", "template": "first_after_yes", "body": "Namaste Ravi",
         "sent_at": "2026-11-03T09:00:00+00:00", "status": "dry_run", "provider_id": None, "error": None}])
    store.write("transactional_outbox", [
        {"id": "t1", "to": "priya@team.in", "subject": "[Outreach] Call Acme", "body": "Due soon", "kind": "internal",
         "lead_id": "l1", "status": "sent", "error": None, "at": "2026-11-03T10:00:00+00:00"},
        {"id": "t2", "to": "ravi@acme.in", "subject": "Invite", "body": "Meeting invite", "kind": "invite",
         "lead_id": "l1", "status": "dry_run", "error": None, "at": "2026-11-03T10:05:00+00:00"}])
    overdue, upcoming = iso(now - timedelta(days=2)), iso(now + timedelta(days=3))
    store.write("tasks", [
        {"id": "k1", "lead_id": "l1", "kind": "call", "title": "Call them", "text": "Call Acme", "due_at": overdue,
         "owner": "Priya", "priority": "high", "status": "open", "done": False, "source": "auto", "created_at": iso(now)},
        {"id": "k2", "lead_id": "l3", "kind": "follow_up", "title": "Send deck", "text": "", "due_at": upcoming,
         "owner": "Amit", "priority": "normal", "status": "open", "done": False, "source": "person", "created_at": iso(now)},
        {"id": "k3", "lead_id": "l1", "kind": "follow_up", "title": "Old task", "text": "", "due_at": overdue,
         "owner": "Priya", "priority": "normal", "status": "done", "done": True, "done_at": iso(now), "source": "person",
         "created_at": iso(now)},
    ])
    store.write("activities", [
        {"id": "a1", "lead_id": "l1", "type": "call", "direction": "out", "subject": "", "body": "Spoke to Ravi",
         "outcome": "interested", "duration_minutes": 5, "by": "Priya", "at": "2026-11-02T06:00:00+00:00",
         "created_at": "2026-11-02T06:00:00+00:00"}])
    store.write("campaigns", [
        {"id": "c1", "name": "Pune BPO", "status": "draft", "runs": [
            {"id": "r1", "started_at": "2026-11-02T04:00:00+00:00", "finished_at": "2026-11-02T04:05:00+00:00",
             "dry_run": False, "trigger": "manual", "discovered": 5, "new": 2, "merged": 1, "attached": 0, "duplicates": 1,
             "suppressed_skipped": 1, "over_cap": 0, "skipped_no_contact": 0, "enriched_emails": 1, "enriched_phones": 0,
             "verified": 1, "verification": {"valid": 1, "invalid": 1}, "qualified": 1, "enrolled": 0,
             "errors": [{"source": "Careers page", "message": "site unreachable"}], "credits_used_estimate": 3,
             "lead_ids": ["l1", "l3"]}]}])
    store.write("suppressed", ["blocked@spam.in", "a" * 64])
    store.write("settings", {"server_secret": SECRETS[2], "webhook_secret": SECRETS[1], "caller_name": "Priya",
                             "live_sending": True, "live_whatsapp": True, "sender_company": "BookMyInterview"})
    store.write("integrations", {
        "sending_domain": {"mode": "api", "values": {"provider": "resend", "domain": "mail.bmi.in", "from_email": "hi@mail.bmi.in",
                                                     "from_name": "Priya", "api_key": SECRETS[0]}},
        "calendar": {"mode": "api", "providers": {"google": {"values": {"client_id": "cid", "client_secret": "cs"},
                                                             "tokens": {"access_token": SECRETS[3], "refresh_token": "rt"}}},
                     "webhook_tokens": {"calcom": SECRETS[4]}},
        "inbound_email": {"providers": {"imap": {"values": {"host": "imap.x.in", "username": "u", "password": SECRETS[5]}}}},
        "sms": {"mode": "api", "values": {"provider": "custom_http", "url": "https://sms.example/send",
                                          "headers": [{"key": "Authorization", "value": SECRETS[6], "secret": True}]}},
    })
    return {"overdue": overdue, "upcoming": upcoming}


def read_csv(response) -> list:
    text = response.content.decode("utf-8")
    assert text.startswith(BOM)
    return list(csv.DictReader(io.StringIO(text[len(BOM):])))


def header(response) -> list:
    return next(csv.reader(io.StringIO(response.content.decode("utf-8")[len(BOM):])))
