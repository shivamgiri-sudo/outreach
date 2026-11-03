from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.replies import classify_reply
from app.scraper import extract_contacts, robots_allows
from app.sequences import DEFAULT_SEQUENCE, due_steps, render

CSV = "company,contact_name,email,website,open_roles,company_size,country\nAcme,Ravi Kumar,ravi@acme.com,https://acme.com,12,200,IN\n"
T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)  # 10:30 IST, Tuesday


@pytest.fixture()
def client(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    return c


def lead_id(c):
    return c.get("/api/leads").json()[0]["id"]


# ---- reply classifier ----
@pytest.mark.parametrize("text,label", [
    ("Yes please send the story", "interested"),
    ("Sure, sounds interesting. Call me tomorrow", "interested"),
    ("Please remove me from your list", "unsubscribe"),
    ("unsubscribe", "unsubscribe"),
    ("Not now, maybe after Diwali", "not_now"),
    ("I am not the right person, contact our HR head Meena", "wrong_person"),
    ("How much does it cost?", "question"),
    ("ok", "other"),
])
def test_classify_reply(text, label):
    assert classify_reply(text) == label


# ---- templates ----
def test_render_fills_variables_and_flags_missing():
    text, missing = render("Hi {{first_name}} at {{company}} {{city}}", {"first_name": "Ravi", "company": "Acme"})
    assert text == "Hi Ravi at Acme {{city}}" or text.startswith("Hi Ravi at Acme")
    assert missing == ["city"]


def test_default_sequence_has_four_steps_with_unsubscribe():
    assert [s["day"] for s in DEFAULT_SEQUENCE] == [0, 3, 7, 14]
    assert all("{{unsub_link}}" in s["body"] for s in DEFAULT_SEQUENCE)


def test_due_steps_respects_days_and_sent_log():
    enrolled = T0 - timedelta(days=3, hours=1)
    assert [s["day"] for s in due_steps(enrolled, sent_days=[0], now=T0)] == [3]
    assert due_steps(enrolled, sent_days=[0, 3], now=T0) == []
    assert [s["day"] for s in due_steps(T0, sent_days=[], now=T0)] == [0]


# ---- sequence run ----
def test_run_sends_step_zero_to_outbox_and_moves_stage(client):
    lid = lead_id(client)
    assert client.post(f"/api/leads/{lid}/enroll").status_code == 200
    r = client.post("/api/sequences/run", json={"now": T0.isoformat()}).json()
    assert r["sent"] == 1
    box = client.get("/api/outbox").json()
    assert box[0]["to"] == "ravi@acme.com" and "Ravi" in box[0]["body"]
    assert "unsubscribe" in box[0]["body"].lower()
    assert box[0]["status"] == "dry_run"
    # same tick again must not duplicate
    assert client.post("/api/sequences/run", json={"now": T0.isoformat()}).json()["sent"] == 0


def test_run_sends_followup_on_day_three_only(client):
    lid = lead_id(client)
    client.post(f"/api/leads/{lid}/enroll")
    client.post("/api/sequences/run", json={"now": T0.isoformat()})
    day3 = (T0 + timedelta(days=3)).isoformat()
    assert client.post("/api/sequences/run", json={"now": day3}).json()["sent"] == 1
    assert len(client.get("/api/outbox").json()) == 2


def test_run_skips_outside_business_hours_and_weekends(client):
    lid = lead_id(client)
    client.post(f"/api/leads/{lid}/enroll")
    night = datetime(2026, 11, 3, 20, 0, tzinfo=timezone.utc)  # 01:30 IST
    sunday = datetime(2026, 11, 8, 5, 0, tzinfo=timezone.utc)
    assert client.post("/api/sequences/run", json={"now": night.isoformat()}).json()["sent"] == 0
    assert client.post("/api/sequences/run", json={"now": sunday.isoformat()}).json()["sent"] == 0


def test_daily_cap_limits_sends(client):
    client.post("/api/leads/import", json={"csv": CSV.replace("ravi@acme.com", "b@acme.com") + "B2,Sam,s@b2.com,https://b2.com,5,50,IN\n", "source": "x"})
    for lead in client.get("/api/leads").json():
        client.post(f"/api/leads/{lead['id']}/enroll")
    client.put("/api/settings", json={"daily_cap": 1})
    assert client.post("/api/sequences/run", json={"now": T0.isoformat()}).json()["sent"] == 1


def test_reply_stops_sequence_and_updates_lead(client):
    lid = lead_id(client)
    client.post(f"/api/leads/{lid}/enroll")
    client.post("/api/sequences/run", json={"now": T0.isoformat()})
    r = client.post(f"/api/leads/{lid}/reply", json={"text": "Yes please send the story"}).json()
    assert r["label"] == "interested"
    lead = client.get(f"/api/leads/{lid}").json()
    assert lead["consent"] is not None and lead["engagement"]["replies"] == 1
    day3 = (T0 + timedelta(days=3)).isoformat()
    assert client.post("/api/sequences/run", json={"now": day3}).json()["sent"] == 0


def test_unsubscribe_reply_suppresses_forever(client):
    lid = lead_id(client)
    client.post(f"/api/leads/{lid}/enroll")
    client.post(f"/api/leads/{lid}/reply", json={"text": "please remove me"})
    assert client.get(f"/api/leads/{lid}").json()["stage"] == "unsubscribed"
    assert client.post(f"/api/leads/{lid}/enroll").status_code == 400
    assert client.post("/api/sequences/run", json={"now": T0.isoformat()}).json()["sent"] == 0


def test_enroll_requires_email(client):
    client.post("/api/leads/import", json={"csv": "company,phone,website\nNoMail,9876543210,https://nomail.in\n", "source": "x"})
    nomail = next(l for l in client.get("/api/leads").json() if l["company"] == "NoMail")
    assert client.post(f"/api/leads/{nomail['id']}/enroll").status_code == 400


# ---- scraper ----
HTML = """
<html><body>
<a href="mailto:hr@acme.com">Mail us</a> careers@acme.com
<p>Call +91 98765 43210 or 022-4000-1234. Not a number: 2026-11-03</p>
<span>name [at] acme [dot] com</span>
<img src="logo@2x.png">
</body></html>
"""


def test_extract_contacts_finds_public_emails_and_phones():
    out = extract_contacts(HTML, "https://acme.com/contact")
    assert {"hr@acme.com", "careers@acme.com", "name@acme.com"} <= set(out["emails"])
    assert "logo@2x.png" not in out["emails"]
    assert "+919876543210" in out["phones"]
    assert out["source_url"] == "https://acme.com/contact"


def test_robots_rules_are_respected():
    robots = "User-agent: *\nDisallow: /private\n"
    assert robots_allows(robots, "https://acme.com/contact") is True
    assert robots_allows(robots, "https://acme.com/private/x") is False


def test_scrape_endpoint_adds_leads_with_source_url(client):
    r = client.post("/api/scrape/extract", json={"html": HTML, "url": "https://newco.com/contact", "company": "NewCo", "add_leads": True})
    assert r.json()["added"] >= 1
    added = [l for l in client.get("/api/leads").json() if l["company"] == "NewCo"]
    assert added and added[0]["source"].startswith("web_scraper")
