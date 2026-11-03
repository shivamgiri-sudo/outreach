"""Hardening: proxy fail-closed, unsafe links, CSV formulas, warm leads left alone, activation guard, Apollo checks."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.hygiene import csv_safe, safe_http_url
from app.main import create_app
from app.sheet_import import sheet_leads
from app.store import Store
from app.workflows import run_time_rules
from app.unsubscribe import unsub_path

T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)
CSV = ("company,contact_name,email,phone,website,open_roles,company_size,country\n"
       "Acme,Ravi Kumar,ravi@acme.com,98765 43210,https://acme.com,12,200,IN\n")


def make(tmp_path, **kwargs):
    c = TestClient(create_app(data_file=tmp_path / "d.json", **kwargs))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    return c


def lead(c):
    return c.get("/api/leads").json()[0]


def test_no_password_refuses_a_same_machine_proxy_forwarding_a_public_host(tmp_path):
    c = make(tmp_path)
    assert c.get("/api/leads").status_code == 200
    proxied = c.get("/api/leads", headers={"host": "outreach.example.in"})     # nginx on the box, no X-Forwarded-For
    assert proxied.status_code == 403


def test_unsafe_links_are_dropped():
    assert safe_http_url("https://www.linkedin.com/in/ravi") == "https://www.linkedin.com/in/ravi"
    for bad in ("javascript:alert(1)", "data:text/html,x", " JavaScript:alert(1)", "ftp://x", "https://x y"):
        assert safe_http_url(bad) == ""
    rows = [{"Company Name": "Acme", "Name": "Ravi", "LinkedIn Profile URL": "javascript:alert(document.cookie)"}]
    assert all(not l.get("linkedin_url") for l in sheet_leads(rows))


def test_csv_cells_cannot_become_formulas():
    assert csv_safe("=HYPERLINK(\"http://x\")") == "'=HYPERLINK(\"http://x\")"
    assert csv_safe("+91 98") == "'+91 98" and csv_safe("@cmd") == "'@cmd" and csv_safe("Acme") == "Acme"


def test_no_reply_rule_never_demotes_a_warm_lead(tmp_path):
    c = make(tmp_path)
    c.post("/api/workflows/starter")
    lid = lead(c)["id"]
    c.post(f"/api/leads/{lid}/enroll")
    c.post("/api/sequences/run", json={"now": T0.isoformat()})
    c.post(f"/api/leads/{lid}/consent", json={"text": "yes on a call with Priya"})
    for stage in ("verified", "enriched", "contacted", "engaged", "interested"):
        c.post(f"/api/leads/{lid}/stage", json={"stage": stage})
    store = Store(tmp_path / "d.json")
    run_time_rules(store, T0 + timedelta(days=30))
    assert store.read("leads")[0]["stage"] == "interested"


def test_converted_event_keeps_an_unsubscribed_stage(tmp_path):
    c = make(tmp_path)
    lid = lead(c)["id"]
    c.post(unsub_path(c.app.state.store, lid))
    c.post("/api/activation/event", json={"lead_id": lid, "event": "converted"})
    saved = lead(c)
    assert saved["stage"] == "unsubscribed" and saved["activation"]["step"] == "converted"


def test_webhook_secret_with_non_ascii_header_is_refused_not_crashed(tmp_path):
    c = make(tmp_path, admin_password="pw-pw-pw")
    token = c.post("/api/login", json={"password": "pw-pw-pw"}).json()["token"]
    c.put("/api/settings", json={"webhook_secret": "abcd1234efgh"}, headers={"Authorization": f"Bearer {token}"})
    r = c.post("/api/activation/event", json={"email": "x@y.com", "event": "role_drafted"},
               headers={"X-Webhook-Secret": "café".encode("utf-8")})
    assert r.status_code == 401


def test_apollo_match_at_another_company_is_not_stored(tmp_path):
    class Apollo:
        def __call__(self, url, headers, body):
            return {"person": {"email": "someone@othercorp.com", "title": "HR", "linkedin_url": "javascript:x"}}

    csv = "company,contact_name,website,country\nAcme,Ravi Kumar,https://acme.com,IN\n"
    c = TestClient(create_app(data_file=tmp_path / "d.json", apollo_transport=Apollo()))
    c.post("/api/leads/import", json={"csv": csv, "source": "sheet"})
    c.put("/api/integrations/apollo", json={"mode": "api", "values": {"api_key": "k"}})
    c.post("/api/enrich/apollo", json={"limit": 5})
    saved = lead(c)
    assert not saved.get("email") and not saved.get("linkedin_url")


def test_consent_needs_a_real_note(tmp_path):
    c = make(tmp_path)
    lid = lead(c)["id"]
    assert c.post(f"/api/leads/{lid}/consent", json={"text": " "}).status_code == 400
    ok = c.post(f"/api/leads/{lid}/consent", json={"text": "Yes on a call with Priya", "channel": "whatsapp", "source": "call"})
    assert ok.json()["consent"]["channel"] == "whatsapp" and ok.json()["consent"]["source"] == "call"
