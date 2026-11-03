from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)
CSV = ("company,contact_name,email,phone,website,open_roles,company_size,country\n"
       "Acme,Ravi Kumar,ravi@acme.com,98765 43210,https://acme.com,12,200,IN\n")


class FakeTransport:
    def __init__(self, reply=None, error=None):
        self.calls, self.reply, self.error = [], reply or {}, error

    def __call__(self, url, headers, body):
        self.calls.append({"url": url, "headers": headers, "body": body})
        if self.error:
            raise self.error
        return self.reply


def make(tmp_path, **kw):
    return TestClient(create_app(data_file=tmp_path / "d.json", **kw))


def item(c, item_id):
    return next(i for i in c.get("/api/readiness").json()["items"] if i["id"] == item_id)


def connect_email(c):
    c.put("/api/integrations/sending_domain", json={"mode": "api", "values": {
        "domain": "mail.example.in", "from_name": "Priya", "from_email": "hello@mail.example.in", "api_key": "re_secret"}})

IDENTITY = {"sender_company": "BookMyInterview.ai", "sender_address": "Sector 62, Noida, Uttar Pradesh 201309, India"}


def go_live(c, **extra):
    """Switch real sending on the way an owner must: public https address and sender identity first."""
    settings = {"public_base_url": "https://outreach.example.in", **IDENTITY, **extra}
    c.put("/api/settings", json=settings)
    r = c.put("/api/settings", json={"live_sending": True})
    assert r.status_code == 200, r.text
    return r



# ---- public address and webhook secret are set in the app, not in environment variables ----
def test_public_address_is_used_in_emails_and_unsubscribe_header(tmp_path):
    t = FakeTransport({"id": "e1"})
    c = make(tmp_path, email_transport=t)
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    connect_email(c)
    go_live(c, public_base_url="https://outreach.mycompany.in/")
    lid = c.get("/api/leads").json()[0]["id"]
    c.post(f"/api/leads/{lid}/enroll")
    c.post("/api/sequences/run", json={"now": T0.isoformat()})
    body = c.get("/api/outbox").json()[0]["body"]
    assert f"https://outreach.mycompany.in/u/{lid}" in body and "/demo/" not in body  # Email 1 has no demo link by default
    assert t.calls[0]["body"]["headers"]["List-Unsubscribe"].startswith(f"<https://outreach.mycompany.in/u/{lid}>")


def test_webhook_secret_from_settings_is_masked_and_enforced(tmp_path):
    c = make(tmp_path, admin_password="pw-pw-pw")
    token = c.post("/api/login", json={"password": "pw-pw-pw"}).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    shown = c.put("/api/settings", json={"webhook_secret": "abcd1234efgh"}, headers=auth).json()
    assert shown["webhook_secret"] == "••••efgh"
    c.put("/api/settings", json={"webhook_secret": shown["webhook_secret"]}, headers=auth)       # masked value must not overwrite
    ok = c.post("/api/activation/event", json={"email": "x@y.com", "event": "role_drafted"}, headers={"X-Webhook-Secret": "abcd1234efgh"})
    assert ok.status_code == 404                                                                    # past auth; lead not found
    bad = c.post("/api/activation/event", json={"email": "x@y.com", "event": "role_drafted"}, headers={"X-Webhook-Secret": "wrong"})
    assert bad.status_code == 401


# ---- connection tests ----
def test_email_test_sends_one_message_even_when_live_sending_is_off(tmp_path):
    t = FakeTransport({"id": "em_test"})
    c = make(tmp_path, email_transport=t)
    connect_email(c)
    r = c.post("/api/integrations/sending_domain/test", json={"to": "me@teammas.in"}).json()
    assert r["ok"] is True
    assert t.calls[0]["body"]["to"] == ["me@teammas.in"] and "test" in t.calls[0]["body"]["subject"].lower()
    assert item(c, "test_email")["status"] == "done"


def test_email_test_reports_provider_failure_and_missing_setup(tmp_path):
    c = make(tmp_path, email_transport=FakeTransport(error=PermissionError("401")))
    assert c.post("/api/integrations/sending_domain/test", json={"to": "me@teammas.in"}).json()["ok"] is False   # not connected
    connect_email(c)
    r = c.post("/api/integrations/sending_domain/test", json={"to": "me@teammas.in"}).json()
    assert r["ok"] is False and "key" in r["message"].lower()
    assert item(c, "test_email")["status"] == "todo"
    assert c.post("/api/integrations/sending_domain/test", json={"to": "not-an-email"}).json()["ok"] is False


def test_ai_and_apollo_tests(tmp_path):
    ai = FakeTransport({"content": [{"type": "text", "text": "OK"}]})
    apollo = FakeTransport({"person": None})
    c = make(tmp_path, ai_transport=ai, apollo_transport=apollo)
    assert c.post("/api/integrations/llm/test", json={}).json()["ok"] is False
    c.put("/api/integrations/llm", json={"mode": "api", "values": {"provider": "anthropic", "api_key": "sk"}})
    assert c.post("/api/integrations/llm/test", json={}).json()["ok"] is True
    c.put("/api/integrations/apollo", json={"mode": "api", "values": {"api_key": "ak"}})
    assert c.post("/api/integrations/apollo/test", json={}).json()["ok"] is True


def test_other_integration_tests_are_honest(tmp_path):
    c = make(tmp_path)
    wa = c.post("/api/integrations/whatsapp/test", json={}).json()
    assert wa["ok"] is False and "practice" in wa["message"].lower()
    c.put("/api/integrations/telephony", json={"mode": "api", "values": {"click_to_call_url": "https://dial.example/call?n={phone}"}})
    assert c.post("/api/integrations/telephony/test", json={}).json()["ok"] is True
    c.put("/api/integrations/telephony", json={"mode": "api", "values": {"click_to_call_url": "https://dial.example/call"}})
    assert c.post("/api/integrations/telephony/test", json={}).json()["ok"] is False
    assert c.post("/api/integrations/nope/test", json={}).status_code == 404


# ---- go-live checklist ----
def test_readiness_starts_with_nothing_done_and_not_ready(tmp_path):
    r = make(tmp_path).get("/api/readiness").json()
    assert r["ready_for_live_test"] is False and r["done"] == 0 and r["total"] >= 9
    ids = {i["id"] for i in r["items"]}
    assert {"leads", "public_url", "sending_domain", "test_email", "live_sending", "bmi_limits", "login", "webhook", "ai_writer", "whatsapp"} <= ids
    assert all(i["status"] in ("done", "todo", "optional") for i in r["items"])
    assert all(i["page"] for i in r["items"])


def test_readiness_turns_ready_after_required_steps(tmp_path):
    c = make(tmp_path, email_transport=FakeTransport({"id": "x"}))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    assert item(c, "leads")["status"] == "done"
    assert item(c, "public_url")["status"] == "todo"
    c.put("/api/settings", json={"public_base_url": "http://localhost:8000"})
    assert item(c, "public_url")["status"] == "todo"                        # localhost is not reachable by recipients
    c.put("/api/settings", json={"public_base_url": "https://outreach.mycompany.in", "confirm_bmi_limits": True})
    connect_email(c)
    c.post("/api/integrations/sending_domain/test", json={"to": "me@teammas.in"})
    r = c.get("/api/readiness").json()
    assert r["ready_for_live_test"] is True
    assert item(c, "live_sending")["status"] == "todo"                      # switching it on stays a deliberate, separate step
    refused = c.put("/api/settings", json={"live_sending": True})
    assert refused.status_code == 400 and "postal address" in refused.json()["detail"]   # sender identity is required first
    c.put("/api/settings", json=IDENTITY)
    assert c.put("/api/settings", json={"live_sending": True}).status_code == 200
    assert item(c, "live_sending")["status"] == "done"


def test_optional_items_do_not_block_readiness(tmp_path):
    c = make(tmp_path)
    assert item(c, "ai_writer")["status"] == "optional" and item(c, "whatsapp")["status"] == "optional"
    c.put("/api/integrations/llm", json={"mode": "api", "values": {"provider": "anthropic", "api_key": "k"}})
    assert item(c, "ai_writer")["status"] == "done"
