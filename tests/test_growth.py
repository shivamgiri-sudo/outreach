from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.activation import conversion_due, next_nudge, apply_event
from app.capacity import capacity_plan
from app.demo import draft_kit, render_demo_html
from app.main import create_app

NOW = datetime(2026, 11, 10, 5, 0, tzinfo=timezone.utc)
OFFER = {"candidate_cap": 25, "booking_cap": 5, "trial_days": 30, "referral_bonus_candidates": 10}
CSV = "company,contact_name,email,website,open_roles,company_size,country\nAcme,Ravi Kumar,ravi@acme.com,https://acme.com,3,200,IN\n"


@pytest.fixture()
def client(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    return c


def lid(c):
    return c.get("/api/leads").json()[0]["id"]


# ---------- pure logic ----------
def test_apply_event_moves_step_forward_only_and_counts_usage():
    a = {"step": "invited", "events": [], "usage": {"candidates": 0, "bookings": 0}}
    a = apply_event(a, "demo_opened", now=NOW)
    assert a["step"] == "opened"
    a = apply_event(a, "candidate_screened", count=4, now=NOW)
    assert a["step"] == "first_candidate" and a["usage"]["candidates"] == 4
    a = apply_event(a, "demo_opened", now=NOW)           # earlier event must not move us back
    assert a["step"] == "first_candidate"
    a = apply_event(a, "interview_booked", count=2, now=NOW)
    assert a["step"] == "first_booking" and a["usage"]["bookings"] == 2


def test_apply_event_does_not_mutate_input():
    a = {"step": "invited", "events": [], "usage": {"candidates": 0, "bookings": 0}}
    apply_event(a, "role_drafted", now=NOW)
    assert a["step"] == "invited" and a["events"] == []


def test_unknown_event_rejected():
    with pytest.raises(ValueError):
        apply_event({"step": "invited", "events": [], "usage": {"candidates": 0, "bookings": 0}}, "bogus", now=NOW)


@pytest.mark.parametrize("step,days,expect", [
    ("invited", 4, "demo"),
    ("opened", 2, "walkthrough"),
    ("drafted", 2, "publish"),
    ("published", 6, "applicants"),
    ("first_candidate", 3, "shortlist"),
    ("first_booking", 1, "story"),
])
def test_next_nudge_by_step(step, days, expect):
    act = {"step": step, "events": [], "usage": {"candidates": 1, "bookings": 0}, "since": (NOW - timedelta(days=days)).isoformat()}
    nudge = next_nudge(act, NOW)
    assert nudge and expect in nudge["text"].lower()


def test_no_nudge_when_too_early():
    act = {"step": "invited", "events": [], "usage": {"candidates": 0, "bookings": 0}, "since": NOW.isoformat()}
    assert next_nudge(act, NOW) is None


def test_conversion_due_by_usage_or_days():
    base = {"step": "published", "events": [], "usage": {"candidates": 0, "bookings": 0}, "since": NOW.isoformat()}
    assert conversion_due(base, OFFER, NOW, bonus=0) is None
    near_cap = {**base, "usage": {"candidates": 20, "bookings": 0}}
    assert "candidate" in conversion_due(near_cap, OFFER, NOW, bonus=0)["reason"]
    assert conversion_due(near_cap, OFFER, NOW, bonus=10) is None          # bonus lifts the cap to 35
    old = {**base, "since": (NOW - timedelta(days=24)).isoformat()}
    assert "days" in conversion_due(old, OFFER, NOW, bonus=0)["reason"]
    booked = {**base, "usage": {"candidates": 1, "bookings": 4}}
    assert "interview" in conversion_due(booked, OFFER, NOW, bonus=0)["reason"]


def test_capacity_plan_math_and_warning():
    p = capacity_plan(mailboxes=3, per_mailbox_per_day=30, target_leads=1000)
    assert p["daily"] == 90 and p["monthly"] == 1980 and p["days_needed"] == 12 and p["warning"] is None
    assert capacity_plan(1, 120, 100)["warning"]
    assert capacity_plan(0, 20, 100)["daily"] == 0


def test_draft_kit_is_role_specific_and_weights_total_100():
    kit = draft_kit("Telecaller")
    assert sum(kit["weights"].values()) == 100
    assert any("Telecaller" in q for q in kit["questions"])
    assert draft_kit("")["role"] == "this role"


def test_demo_html_escapes_untrusted_text():
    html = render_demo_html({"company": "<script>alert(1)</script>", "role": "Tele<b>caller"},
                            draft_kit("Telecaller"), OFFER, "https://bookmyinterview.ai/register?ref=x")
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "25" in html and "register?ref=x" in html


# ---------- API ----------
def test_demo_link_page_and_open_event(client):
    i = lid(client)
    link = client.post(f"/api/leads/{i}/demo-link").json()
    assert link["url"].endswith(link["token"])
    page = client.get(f"/demo/{link['token']}")
    assert page.status_code == 200 and "Acme" in page.text and "free" in page.text.lower()
    assert client.get(f"/api/leads/{i}").json()["activation"]["step"] == "invited"   # link scanners only load the page
    assert f"/demo/{link['token']}/opened" in page.text                                # a real browser runs this beacon
    assert client.post(f"/demo/{link['token']}/opened").status_code == 200
    lead = client.get(f"/api/leads/{i}").json()
    assert lead["activation"]["step"] == "opened"
    assert client.get("/demo/nope").status_code == 404


def test_activation_event_by_email_and_usage(client):
    i = lid(client)
    client.post(f"/api/leads/{i}/demo-link")
    r = client.post("/api/activation/event", json={"email": "RAVI@acme.com", "event": "candidate_screened", "count": 6})
    assert r.status_code == 200
    lead = client.get(f"/api/leads/{i}").json()
    assert lead["activation"]["step"] == "first_candidate" and lead["activation"]["usage"]["candidates"] == 6
    assert client.post("/api/activation/event", json={"email": "who@x.com", "event": "role_drafted"}).status_code == 404


def test_converted_event_moves_lead_stage_to_customer(client):
    i = lid(client)
    client.post("/api/activation/event", json={"lead_id": i, "event": "converted"})
    assert client.get(f"/api/leads/{i}").json()["stage"] == "customer"


def test_referral_creates_lead_and_gives_bonus_to_both(client):
    i = lid(client)
    r = client.post(f"/api/leads/{i}/referral", json={"company": "Beta Logistics", "contact_name": "Sam", "email": "sam@beta.in"})
    assert r.status_code == 200
    leads = {l["company"]: l for l in client.get("/api/leads").json()}
    assert leads["Beta Logistics"]["source"].startswith("referral:")
    assert leads["Beta Logistics"]["bonus_candidates"] == 10 and leads["Acme"]["bonus_candidates"] == 10
    assert client.post(f"/api/leads/{i}/referral", json={"company": "Beta", "email": "sam@beta.in"}).status_code == 400  # duplicate


def test_pilot_request_creates_task(client):
    i = lid(client)
    client.post(f"/api/leads/{i}/pilot", json={"applicants": 120})
    lead = client.get(f"/api/leads/{i}").json()
    assert lead["pilot"]["applicants"] == 120
    assert any(t["kind"] == "pilot" for t in client.get("/api/tasks").json())


def test_growth_summary_has_funnel_conversion_and_capacity(client):
    i = lid(client)
    client.post(f"/api/leads/{i}/demo-link")
    client.post("/api/activation/event", json={"lead_id": i, "event": "candidate_screened", "count": 24})
    client.put("/api/offer", json={"mailboxes": 2, "per_mailbox_per_day": 25})
    g = client.get("/api/growth").json()
    assert g["activation_funnel"]["first_candidate"] == 1
    assert g["conversion_due"][0]["company"] == "Acme"
    assert g["capacity"]["daily"] == 50
    assert g["offer"]["candidate_cap"] == 25


def test_partners_crud(client):
    p = client.post("/api/partners", json={"name": "HR Hub Pune", "kind": "community", "contact": "admin@hrhub.in"}).json()
    assert p["status"] == "to_contact"
    client.post(f"/api/partners/{p['id']}/status", json={"status": "active"})
    assert client.get("/api/partners").json()[0]["status"] == "active"


def test_sequence_endpoint_and_offer_terms_in_first_email(client):
    seq = client.get("/api/sequence").json()
    assert len(seq) == 6 and "{{demo_link}}" not in seq[0]["body"]       # the executive copy carries no free-offer line or demo link
    i = lid(client)
    client.post(f"/api/leads/{i}/enroll")
    client.post("/api/sequences/run", json={"now": "2026-11-10T05:00:00+00:00"})
    body = client.get("/api/outbox").json()[0]["body"]
    # cold emails carry no link except the unsubscribe link (the sample screen is offered, not linked)
    assert "/demo/" not in body and "{{" not in body and "/u/" in body


def test_demo_headline_has_no_awkward_text_without_role():
    html = render_demo_html({"company": "Beta", "role": ""}, draft_kit(""), OFFER, "https://x.in/r")
    assert "this role opening" not in html and "A sample screen for Beta" in html


def test_import_merges_contact_into_existing_company_lead_by_domain(client):
    client.post("/api/jobs/ingest", json={"csv": "company,title,location,date_posted,website\nBeta Logistics,Driver,Delhi,2026-10-01,https://beta.in\n"})
    r = client.post("/api/leads/import", json={"csv": "company,contact_name,email,website\nBeta Logistics,Sam Rao,sam@beta.in,https://beta.in\n", "source": "apollo"}).json()
    assert r["created"] == 0 and r["merged"] == 1
    leads = [l for l in client.get("/api/leads").json() if l["company"] == "Beta Logistics"]
    assert len(leads) == 1
    assert leads[0]["email"] == "sam@beta.in" and leads[0]["open_roles"] == 1 and leads[0]["role"] == "Driver"
    assert leads[0]["dedupe_key"] == "email:sam@beta.in"
