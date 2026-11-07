import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.unsubscribe import unsub_path

T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)   # Tuesday 10:30 IST
CSV = ("company,contact_name,email,phone,website,open_roles,company_size,country\n"
       "Acme,Ravi Kumar,ravi@acme.com,98765 43210,https://acme.com,12,200,IN\n"
       "Beta,Sam Rao,sam@beta.in,,https://beta.in,3,50,IN\n")


class FakeTransport:
    """Records outgoing requests and returns a canned reply."""
    def __init__(self, reply=None, error=None):
        self.calls, self.reply, self.error = [], reply or {}, error

    def __call__(self, url, headers, body):
        self.calls.append({"url": url, "headers": headers, "body": body})
        if self.error:
            raise self.error
        return self.reply


def make_client(tmp_path, **kwargs):
    c = TestClient(create_app(data_file=tmp_path / "d.json", **kwargs))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    return c


def lead(c, company="Acme"):
    return next(l for l in c.get("/api/leads").json() if l["company"] == company)


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



# ================= AUTH =================
def test_auth_off_by_default(tmp_path):
    c = make_client(tmp_path)
    assert c.get("/api/auth/status").json() == {"auth_required": False}
    assert c.get("/api/leads").status_code == 200


def test_auth_blocks_api_but_not_public_pages(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="s3cret-pass"))
    assert c.get("/api/auth/status").json() == {"auth_required": True}
    assert c.get("/api/leads").status_code == 401
    assert c.get("/u/missing").status_code == 404          # public route is reachable (lead not found, not 401)
    assert c.get("/demo/missing").status_code == 404


def test_login_flow_and_token_use(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="s3cret-pass"))
    assert c.post("/api/login", json={"password": "wrong"}).status_code == 401
    token = c.post("/api/login", json={"password": "s3cret-pass"}).json()["token"]
    assert c.get("/api/leads", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert c.get("/api/leads", headers={"Authorization": "Bearer forged.token"}).status_code == 401


def test_login_locks_out_after_repeated_failures(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="s3cret-pass"))
    for _ in range(5):
        c.post("/api/login", json={"password": "bad"})
    assert c.post("/api/login", json={"password": "s3cret-pass"}).status_code == 429


def test_expired_token_rejected(tmp_path):
    from app.auth import ADMIN_SUBJECT, issue_token, subject_principal
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="s3cret-pass"))
    store = c.app.state.store
    old = issue_token(store, subject_principal(store, "s3cret-pass", ADMIN_SUBJECT), now=T0.timestamp() - 2 * 86400)
    assert c.get("/api/leads", headers={"Authorization": f"Bearer {old}"}).status_code == 401


def test_activation_webhook_uses_its_own_secret_when_auth_on(tmp_path, monkeypatch):
    import app.routes_growth as rg
    monkeypatch.setattr(rg, "WEBHOOK_SECRET", "hook-secret")
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="s3cret-pass"))
    r = c.post("/api/activation/event", json={"email": "x@y.com", "event": "role_drafted"}, headers={"X-Webhook-Secret": "hook-secret"})
    assert r.status_code == 404                              # got past auth and the secret; lead simply not found
    assert c.post("/api/activation/event", json={"email": "x@y.com", "event": "role_drafted"}).status_code == 401


# ================= REAL EMAIL SENDING =================
def test_live_sending_is_off_until_switched_on(tmp_path):
    t = FakeTransport({"id": "em_1"})
    c = make_client(tmp_path, email_transport=t)
    connect_email(c)
    c.post(f"/api/leads/{lead(c)['id']}/enroll")
    c.post("/api/sequences/run", json={"now": T0.isoformat()})
    assert t.calls == [] and c.get("/api/outbox").json()[0]["status"] == "dry_run"


def test_live_sending_posts_to_provider_with_unsubscribe_headers(tmp_path):
    t = FakeTransport({"id": "em_123"})
    c = make_client(tmp_path, email_transport=t)
    connect_email(c)
    go_live(c)
    lid = lead(c)["id"]
    c.post(f"/api/leads/{lid}/enroll")
    assert c.post("/api/sequences/run", json={"now": T0.isoformat()}).json()["sent"] == 1
    call = t.calls[0]
    assert call["url"] == "https://api.resend.com/emails"
    assert call["headers"]["Authorization"] == "Bearer re_secret"
    body = call["body"]
    assert body["from"] == '"Priya" <hello@mail.example.in>' and body["to"] == ["ravi@acme.com"]
    assert f"/u/{lid}" in body["headers"]["List-Unsubscribe"]
    assert "mailto:hello@mail.example.in?subject=unsubscribe" in body["headers"]["List-Unsubscribe"]
    assert call["headers"]["Idempotency-Key"] == f"{lid}:0:live"
    assert body["headers"]["Message-ID"].endswith("@mail.example.in>")
    assert body["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert c.get("/api/outbox").json()[0]["status"] == "sent"
    assert c.get("/api/outbox").json()[0]["provider_id"] == "em_123"


def test_failed_send_is_not_marked_sent_and_is_retried(tmp_path):
    t = FakeTransport(error=OSError("provider down"))
    c = make_client(tmp_path, email_transport=t)
    connect_email(c)
    go_live(c)
    lid = lead(c)["id"]
    c.post(f"/api/leads/{lid}/enroll")
    r = c.post("/api/sequences/run", json={"now": T0.isoformat()}).json()
    assert r["sent"] == 0 and r["failed"] == 1
    assert c.get(f"/api/leads/{lid}").json()["sequence"]["sent_days"] == []
    t.error = None
    t.reply = {"id": "em_ok"}
    assert c.post("/api/sequences/run", json={"now": (T0 + timedelta(hours=1)).isoformat()}).json()["sent"] == 1  # retried after the back-off


def test_suppressed_email_is_never_sent(tmp_path):
    t = FakeTransport({"id": "x"})
    c = make_client(tmp_path, email_transport=t)
    connect_email(c)
    go_live(c)
    lid = lead(c)["id"]
    c.post(f"/api/leads/{lid}/enroll")
    c.post(unsub_path(c.app.state.store, lid))                    # unsubscribe link (a person clicks the button)
    assert c.post("/api/sequences/run", json={"now": T0.isoformat()}).json()["sent"] == 0 and t.calls == []


def test_one_click_unsubscribe_post(tmp_path):
    c = make_client(tmp_path)
    lid = lead(c)["id"]
    assert c.post(unsub_path(c.app.state.store, lid)).status_code == 200
    assert c.get(f"/api/leads/{lid}").json()["stage"] == "unsubscribed"


# ================= WORKFLOWS =================
def add_rule(c, trigger, action, name="rule"):
    return c.post("/api/workflows", json={"name": name, "trigger": trigger, "action": action}).json()


def test_reply_interested_triggers_whatsapp(tmp_path):
    c = make_client(tmp_path)
    add_rule(c, {"type": "reply", "value": "interested"}, {"type": "whatsapp", "value": "first_after_yes"})
    lid = lead(c)["id"]
    c.post(f"/api/leads/{lid}/consent", json={"text": "Yes to WhatsApp on a call", "channel": "whatsapp"})
    c.post(f"/api/leads/{lid}/reply", json={"text": "Yes please send the story"})
    assert c.get("/api/whatsapp/outbox").json()[0]["template"] == "first_after_yes"
    assert c.get(f"/api/leads/{lid}").json()["stage"] == "whatsapp_active"


def test_rule_that_cannot_run_creates_a_task_instead(tmp_path):
    c = make_client(tmp_path)
    add_rule(c, {"type": "reply", "value": "interested"}, {"type": "whatsapp", "value": "first_after_yes"})
    lid = lead(c, "Beta")["id"]                               # Beta has no phone
    c.post(f"/api/leads/{lid}/consent", json={"text": "Yes to WhatsApp", "channel": "whatsapp"})
    c.post(f"/api/leads/{lid}/reply", json={"text": "Yes please send the story"})
    assert c.get("/api/whatsapp/outbox").json() == []
    assert any("phone" in t["text"].lower() for t in c.get("/api/tasks").json())


def test_stage_and_activation_triggers_and_actions(tmp_path):
    c = make_client(tmp_path)
    add_rule(c, {"type": "activation", "value": "role_published"}, {"type": "task", "value": "Call them today"})
    add_rule(c, {"type": "stage_change", "value": "contacted"}, {"type": "note", "value": "Started outreach"})
    lid = lead(c)["id"]
    c.post("/api/activation/event", json={"lead_id": lid, "event": "role_published"})
    for stage in ("verified", "enriched", "contacted"):
        c.post(f"/api/leads/{lid}/stage", json={"stage": stage})
    assert any(t["text"] == "Call them today" for t in c.get("/api/tasks").json())
    assert any(e["text"] == "Started outreach" for e in c.get(f"/api/leads/{lid}").json()["timeline"])


def test_rules_run_once_and_can_be_disabled(tmp_path):
    c = make_client(tmp_path)
    rule = add_rule(c, {"type": "activation", "value": "role_drafted"}, {"type": "task", "value": "Help publish"})
    lid = lead(c)["id"]
    c.post("/api/activation/event", json={"lead_id": lid, "event": "role_drafted"})
    c.post("/api/activation/event", json={"lead_id": lid, "event": "role_drafted"})
    assert sum(t["text"] == "Help publish" for t in c.get("/api/tasks").json()) == 1
    c.put(f"/api/workflows/{rule['id']}", json={"enabled": False})
    lid2 = lead(c, "Beta")["id"]
    c.post("/api/activation/event", json={"lead_id": lid2, "event": "role_drafted"})
    assert sum(t["text"] == "Help publish" for t in c.get("/api/tasks").json()) == 1


def test_no_reply_after_days_rule_runs_on_tick(tmp_path):
    from app.store import Store
    from app.workflows import run_time_rules
    c = make_client(tmp_path)
    add_rule(c, {"type": "no_reply_days", "value": 14}, {"type": "set_stage", "value": "not_now"})
    lid = lead(c)["id"]
    c.post(f"/api/leads/{lid}/enroll")
    c.post("/api/sequences/run", json={"now": T0.isoformat()})
    store = Store(tmp_path / "d.json")
    assert run_time_rules(store, T0 + timedelta(days=10)) == 0
    assert run_time_rules(store, T0 + timedelta(days=15)) == 1
    assert Store(tmp_path / "d.json").read("leads")[0]["stage"] == "not_now"


def test_workflow_validation_and_delete(tmp_path):
    c = make_client(tmp_path)
    assert c.post("/api/workflows", json={"name": "x", "trigger": {"type": "bogus", "value": 1}, "action": {"type": "note", "value": "a"}}).status_code == 400
    assert c.post("/api/workflows", json={"name": "x", "trigger": {"type": "reply", "value": "interested"}, "action": {"type": "bogus", "value": "a"}}).status_code == 400
    r = add_rule(c, {"type": "reply", "value": "interested"}, {"type": "note", "value": "n"})
    assert c.delete(f"/api/workflows/{r['id']}").status_code == 200
    assert c.get("/api/workflows").json() == []
    assert len(c.post("/api/workflows/starter").json()) == 2


# ================= AI DRAFTS =================
def test_draft_falls_back_to_template_without_ai_key(tmp_path):
    c = make_client(tmp_path)
    out = c.post(f"/api/leads/{lead(c)['id']}/draft-reply", json={"incoming": "How much does it cost?"}).json()
    assert out["source"] == "template" and out["label"] == "question" and "Ravi" in out["draft"]


def test_draft_uses_ai_when_connected_and_never_sends(tmp_path):
    t = FakeTransport({"content": [{"type": "text", "text": "Hi Ravi, it is free for your first role."}]})
    c = make_client(tmp_path, ai_transport=t)
    c.put("/api/integrations/llm", json={"mode": "api", "values": {"provider": "anthropic", "api_key": "sk-ant-x"}})
    out = c.post(f"/api/leads/{lead(c)['id']}/draft-reply", json={"incoming": "How much does it cost?"}).json()
    assert out["source"] == "ai" and out["draft"].startswith("Hi Ravi")
    call = t.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages" and call["headers"]["x-api-key"] == "sk-ant-x"
    assert "Never invent" in call["body"]["system"] and "How much does it cost?" in json.dumps(call["body"]["messages"])
    assert c.get("/api/outbox").json() == [] and c.get("/api/whatsapp/outbox").json() == []


def test_draft_falls_back_when_ai_call_fails(tmp_path):
    c = make_client(tmp_path, ai_transport=FakeTransport(error=OSError("boom")))
    c.put("/api/integrations/llm", json={"mode": "api", "values": {"provider": "anthropic", "api_key": "k"}})
    out = c.post(f"/api/leads/{lead(c)['id']}/draft-reply", json={"incoming": "hello?"}).json()
    assert out["source"] == "template" and "AI" in out["note"]


# ================= APOLLO =================
def connect_apollo(c):
    c.put("/api/integrations/apollo", json={"mode": "api", "values": {"api_key": "apollo-key"}})


def test_apollo_needs_connection(tmp_path):
    c = make_client(tmp_path)
    assert c.post("/api/enrich/apollo", json={"limit": 5}).status_code == 400


def test_apollo_fills_missing_email_and_marks_source(tmp_path):
    t = FakeTransport({"person": {"email": "meena@gamma.in", "title": "HR Head", "linkedin_url": "https://linkedin.com/in/meena"}})
    c = make_client(tmp_path, apollo_transport=t)
    connect_apollo(c)
    c.post("/api/leads/import", json={"csv": "company,contact_name,website\nGamma,Meena Iyer,https://gamma.in\n", "source": "sheet"})
    r = c.post("/api/enrich/apollo", json={"limit": 5}).json()
    assert r["matched"] == 1 and r["credits_used_estimate"] == 1
    g = lead(c, "Gamma")
    assert g["email"] == "meena@gamma.in" and g["title"] == "HR Head" and g["email_source"] == "apollo"
    call = t.calls[0]
    assert call["url"].startswith("https://api.apollo.io/api/v1/people/match") and call["headers"]["x-api-key"] == "apollo-key"
    assert call["body"]["first_name"] == "Meena" and call["body"]["last_name"] == "Iyer" and call["body"]["domain"] == "gamma.in"
    assert c.post("/api/enrich/apollo", json={"limit": 5}).json()["checked"] == 0      # not asked twice


def test_apollo_skips_leads_without_a_person_and_stops_on_bad_key(tmp_path):
    t = FakeTransport(error=PermissionError("401"))
    c = make_client(tmp_path, apollo_transport=t)
    connect_apollo(c)
    c.post("/api/leads/import", json={"csv": "company,contact_name,website\nGamma,Meena Iyer,https://gamma.in\nDelta,,https://delta.in\n", "source": "sheet"})
    r = c.post("/api/enrich/apollo", json={"limit": 5})
    assert r.status_code == 400 and "key" in r.json()["detail"].lower()
    assert len(t.calls) == 1                                  # Delta had no contact name, so it was never sent
