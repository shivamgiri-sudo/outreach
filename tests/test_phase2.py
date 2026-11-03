from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.autorun import tick
from app.demo import draft_kit
from app.main import create_app
from app.whatsapp import TEMPLATES

T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)
CSV = ("company,contact_name,email,phone,website,open_roles,company_size,country\n"
       "Acme,Ravi Kumar,ravi@acme.com,98765 43210,https://acme.com,12,200,IN\n"
       "Beta,Sam Rao,sam@beta.in,,https://beta.in,3,50,IN\n")


@pytest.fixture()
def client(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    return c


def leads_by_company(c):
    return {l["company"]: l for l in c.get("/api/leads").json()}


# ---- demo wording ----
def test_demo_questions_read_naturally_with_and_without_role():
    assert all("a this role" not in q and "an this" not in q for q in draft_kit("")["questions"])
    assert any("Telecaller" in q for q in draft_kit("Telecaller")["questions"])
    assert "work as a" not in draft_kit("Accountant")["questions"][0]


# ---- sequence must never email people who are paused, customers or opted out ----
@pytest.mark.parametrize("stage", ["not_now", "customer", "unsubscribed", "disqualified"])
def test_run_skips_leads_in_non_contact_stages(client, stage):
    lid = leads_by_company(client)["Acme"]["id"]
    client.post(f"/api/leads/{lid}/enroll")
    client.post(f"/api/leads/{lid}/stage", json={"stage": stage}) if stage != "customer" else client.post(
        "/api/activation/event", json={"lead_id": lid, "event": "converted"})
    r = client.post("/api/sequences/run", json={"now": T0.isoformat()}).json()
    assert r["sent"] == 0


# ---- WhatsApp (practice mode, consent gated) ----
def test_whatsapp_needs_consent_and_phone(client):
    acme = leads_by_company(client)["Acme"]["id"]
    beta = leads_by_company(client)["Beta"]["id"]
    r = client.post(f"/api/leads/{acme}/whatsapp", json={"template": "first_after_yes"})
    assert r.status_code == 400 and "consent" in r.json()["detail"].lower()
    client.post(f"/api/leads/{beta}/consent", json={"text": "yes"})
    r = client.post(f"/api/leads/{beta}/whatsapp", json={"template": "first_after_yes"})
    assert r.status_code == 400 and "phone" in r.json()["detail"].lower()


def test_whatsapp_sends_dry_run_and_advances_stage(client):
    lid = leads_by_company(client)["Acme"]["id"]
    client.post(f"/api/leads/{lid}/reply", json={"text": "Yes please send the story"})
    r = client.post(f"/api/leads/{lid}/whatsapp", json={"template": "first_after_yes"})
    assert r.status_code == 200
    msg = client.get("/api/whatsapp/outbox").json()[0]
    assert msg["to"] == "+919876543210" and msg["status"] == "dry_run"
    assert "Ravi" in msg["body"] and "/demo/" in msg["body"] and "{{" not in msg["body"]
    assert client.get(f"/api/leads/{lid}").json()["stage"] == "whatsapp_active"


def test_whatsapp_unknown_template_and_custom_window(client):
    lid = leads_by_company(client)["Acme"]["id"]
    client.post(f"/api/leads/{lid}/consent", json={"text": "yes"})
    assert client.post(f"/api/leads/{lid}/whatsapp", json={"template": "nope"}).status_code == 400
    # custom text is only allowed within 24h of their last WhatsApp message (Meta's customer-service window)
    refused = client.post(f"/api/leads/{lid}/whatsapp", json={"template": "custom", "text": "Thanks!"})
    assert refused.status_code == 400   # a consent noted by a person does not open the window
    from app.inbound import process_inbound_message
    process_inbound_message(client.app.state.store, {"channel": "whatsapp", "from_phone": "+919876543210",
                                                     "text": "Haan, details bhejiye", "raw_id": "wamid.test"})
    ok = client.post(f"/api/leads/{lid}/whatsapp", json={"template": "custom", "text": "Thanks!"})
    assert ok.status_code == 200        # their WhatsApp message opened it
    assert "first_after_yes" in TEMPLATES


def test_whatsapp_blocked_for_unsubscribed(client):
    lid = leads_by_company(client)["Acme"]["id"]
    client.post(f"/api/leads/{lid}/consent", json={"text": "yes"})
    client.post(f"/api/leads/{lid}/reply", json={"text": "please remove me"})
    assert client.post(f"/api/leads/{lid}/whatsapp", json={"template": "first_after_yes"}).status_code == 400


# ---- insights ----
def test_insights_summarise_sources_sectors_and_outreach(client):
    lid = leads_by_company(client)["Acme"]["id"]
    client.post(f"/api/leads/{lid}/enroll")
    client.post("/api/sequences/run", json={"now": T0.isoformat()})
    client.post(f"/api/leads/{lid}/reply", json={"text": "Yes please send the story"})
    ins = client.get("/api/insights").json()
    assert ins["totals"]["leads"] == 2 and ins["totals"]["emails_sent"] == 1
    assert ins["outreach"]["reply_rate"] == 100.0 and ins["outreach"]["interested"] == 1
    src = {s["source"]: s for s in ins["by_source"]}
    assert src["apollo"]["leads"] == 2 and src["apollo"]["interested"] == 1
    assert {"hot", "warm", "cold"} == set(ins["by_band"])
    assert ins["top_leads"][0]["company"] == "Acme"


def test_insights_empty_state_has_no_division_errors(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "e.json"))
    ins = c.get("/api/insights").json()
    assert ins["totals"]["leads"] == 0 and ins["outreach"]["reply_rate"] == 0.0


# ---- autorun ----
def test_tick_sends_due_emails_without_http(tmp_path):
    from app.store import Store
    c = TestClient(create_app(data_file=tmp_path / "t.json"))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    lid = c.get("/api/leads").json()[0]["id"]
    c.post(f"/api/leads/{lid}/enroll")
    result = tick(Store(tmp_path / "t.json"), now=T0)
    assert result["sent"] == 1
    assert tick(Store(tmp_path / "t.json"), now=T0)["sent"] == 0
    assert tick(Store(tmp_path / "t.json"), now=datetime(2026, 11, 3, 20, 0, tzinfo=timezone.utc))["skipped"]
