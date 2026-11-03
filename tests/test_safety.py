"""Safety: consent can't be faked by the classifier, closed leads stay closed, no open API, no leaked secrets,
safe saving, and no client-controlled clock."""
import json
import threading
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import routes_outreach
from app.main import create_app
from app.replies import classify_reply
from app.store import Store

CSV = "company,contact_name,email,phone,website,open_roles,company_size,country\nAcme,Ravi Kumar,ravi@acme.com,9876543210,https://acme.com,12,200,IN\n"


@pytest.fixture()
def client(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    return c


def lead(c):
    return c.get("/api/leads").json()[0]


# ---- classifier ----
@pytest.mark.parametrize("text,label", [
    ("Not interested", "not_interested"),
    ("not interested, thanks", "not_interested"),
    ("No thanks", "not_interested"),
    ("We don't need this", "not_interested"),
    ("nahi chahiye", "not_interested"),
    ("I'm not sure", "other"),
    ("not sure yet, send details later", "not_now"),
    ("Stop sending me emails", "unsubscribe"),
    ("STOP", "unsubscribe"),
    ("opt out", "unsubscribe"),
    ("remove", "unsubscribe"),
    ("mat bhejo", "unsubscribe"),
    ("Please do not send me anything", "unsubscribe"),
    ("Out of office until 12 Nov, back later", "auto_reply"),
    ("Automatic reply: I am on leave", "auto_reply"),
    ("Undeliverable: mail delivery failed", "bounce"),
    ("Yes please", "interested"),
    ("Sure, sounds interesting. Call me tomorrow", "interested"),
    ("haan bhej do", "interested"),
    ("Yes, go ahead and send the sample", "interested"),
    ("Share your pricing", "question"),
    ("How much does it cost?", "question"),
])
def test_classifier_handles_negation_stop_words_and_autoreplies(text, label):
    assert classify_reply(text) == label


# ---- reply route: consent and closed leads ----
def reply(c, lid, text):
    return c.post(f"/api/leads/{lid}/reply", json={"text": text}).json()


def test_not_interested_gives_no_consent_and_no_whatsapp(client):
    lid = lead(client)["id"]
    client.post("/api/workflows/starter")
    out = reply(client, lid, "Not interested")
    got = lead(client)
    assert out["label"] == "not_interested"
    assert not got["consent"] and got["stage"] == "disqualified"
    assert client.get("/api/whatsapp/outbox").json() == []


def test_unsubscribed_lead_stays_unsubscribed_when_it_says_yes(client):
    lid = lead(client)["id"]
    reply(client, lid, "unsubscribe")
    reply(client, lid, "sure")
    got = lead(client)
    assert got["stage"] == "unsubscribed" and not got["consent"]


@pytest.mark.parametrize("stage", ["customer", "trial", "disqualified"])
def test_late_yes_does_not_pull_back_a_later_or_closed_stage(client, stage):
    lid = lead(client)["id"]
    path = f"/api/leads/{lid}/stage"
    client.post(f"/api/leads/{lid}/consent", json={"text": "yes by phone"})
    walk = ["disqualified"] if stage == "disqualified" else         ["verified", "enriched", "contacted", "engaged", "interested", "whatsapp_active", "call_done", "trial", "customer"]
    for target in walk[: walk.index(stage) + 1]:
        assert client.post(path, json={"stage": target}).status_code == 200, target
    before = lead(client)["stage"]
    assert before == stage
    reply(client, lid, "yes")
    assert lead(client)["stage"] == before


def test_auto_reply_is_not_a_reply_and_does_not_stop_the_sequence(client):
    lid = lead(client)["id"]
    client.post(f"/api/leads/{lid}/enroll")
    reply(client, lid, "Automatic reply: I am out of office")
    got = lead(client)
    assert got["engagement"]["replies"] == 0
    assert got["sequence"]["stopped"] is None
    assert got["stage"] == "new"


def test_hard_bounce_suppresses_the_address_and_stops_the_sequence(client):
    lid = lead(client)["id"]
    client.post(f"/api/leads/{lid}/enroll")
    out = reply(client, lid, "Undeliverable: 550 user unknown")
    got = lead(client)
    assert out["label"] == "bounce" and got["stage"] == "disqualified"
    assert got["sequence"]["stopped"] == "bounced" and got["engagement"]["replies"] == 0
    assert client.post(f"/api/leads/{lid}/enroll").status_code == 400


# ---- no open API without a password ----
def test_no_password_blocks_remote_callers(tmp_path):
    app = create_app(data_file=tmp_path / "d.json")
    remote = TestClient(app, client=("203.0.113.9", 50000))
    r = remote.get("/api/leads")
    assert r.status_code == 403 and "OUTREACH_ADMIN_PASSWORD" in r.json()["detail"]


def test_no_password_blocks_proxied_callers(tmp_path):
    local = TestClient(create_app(data_file=tmp_path / "d.json"))
    assert local.get("/api/leads").status_code == 200
    assert local.get("/api/leads", headers={"x-forwarded-for": "198.51.100.4"}).status_code == 403


def test_public_pages_stay_reachable_without_password(tmp_path):
    remote = TestClient(create_app(data_file=tmp_path / "d.json"), client=("203.0.113.9", 50000))
    assert remote.get("/demo/nope").status_code == 404
    assert remote.get("/api/auth/status").status_code == 200


def test_with_password_remote_callers_use_the_login(tmp_path):
    remote = TestClient(create_app(data_file=tmp_path / "d.json", admin_password="s3cret-pass"), client=("203.0.113.9", 50000))
    assert remote.get("/api/leads").status_code == 401
    token = remote.post("/api/login", json={"password": "s3cret-pass"}).json()["token"]
    assert remote.get("/api/leads", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_login_lockout_is_per_caller(tmp_path):
    app = create_app(data_file=tmp_path / "d.json", admin_password="s3cret-pass")
    attacker = TestClient(app, client=("203.0.113.9", 1))
    owner = TestClient(app, client=("198.51.100.7", 1))
    for _ in range(6):
        attacker.post("/api/login", json={"password": "wrong"})
    assert attacker.post("/api/login", json={"password": "s3cret-pass"}).status_code == 429
    assert owner.post("/api/login", json={"password": "s3cret-pass"}).status_code == 200


# ---- secrets are never returned ----
def test_offer_and_growth_never_return_the_webhook_secret(client):
    client.put("/api/settings", json={"webhook_secret": "super-secret-value-1234"})
    for path in ("/api/offer", "/api/growth"):
        assert "super-secret-value-1234" not in client.get(path).text


# ---- safe saving ----
def test_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "s.json"
    store = Store(path)
    store.write("tasks", [{"a": 1}])
    assert json.loads(path.read_text(encoding="utf-8"))["tasks"] == [{"a": 1}]
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]


def test_update_applies_changes_without_losing_concurrent_updates(tmp_path):
    store = Store(tmp_path / "s.json")
    store.write("tasks", [])

    def add(n):
        for i in range(25):
            store.update("tasks", lambda rows: [*rows, {"n": n, "i": i}])

    threads = [threading.Thread(target=add, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(store.read("tasks")) == 200


def test_corrupt_store_file_is_not_silently_replaced(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="corrupt"):
        Store(path)


# ---- the client cannot set the clock ----
def test_client_clock_is_ignored_outside_test_mode(monkeypatch):
    monkeypatch.setattr(routes_outreach, "ALLOW_NOW_OVERRIDE", False)
    got = routes_outreach.resolve_now("2031-01-01T00:00:00+00:00")
    assert got.year == datetime.now(timezone.utc).year


def test_client_clock_is_honoured_in_test_mode(monkeypatch):
    monkeypatch.setattr(routes_outreach, "ALLOW_NOW_OVERRIDE", True)
    assert routes_outreach.resolve_now("2031-01-01T00:00:00+00:00").year == 2031


# ---- AI reply reading: only for replies the rules cannot place, and an AI "yes" is never consent ----
class FakeAI:
    def __init__(self, label):
        self.label, self.calls = label, 0

    def __call__(self, url, headers, body):
        self.calls += 1
        return {"content": [{"type": "text", "text": '{"label": "%s"}' % self.label}]}


def ai_client(tmp_path, label):
    fake = FakeAI(label)
    c = TestClient(create_app(data_file=tmp_path / "d.json", ai_transport=fake))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    c.put("/api/integrations/llm", json={"mode": "api", "values": {"provider": "anthropic", "api_key": "k"}})
    return c, fake


def test_ai_is_not_asked_when_the_rules_are_sure(tmp_path):
    c, fake = ai_client(tmp_path, "interested")
    reply(c, lead(c)["id"], "Not interested")
    assert fake.calls == 0


def test_an_ai_yes_needs_a_person_and_records_no_consent(tmp_path):
    c, fake = ai_client(tmp_path, "interested")
    out = reply(c, lead(c)["id"], "Bilkul, aage badhte hain")
    got = lead(c)
    assert fake.calls == 1 and out["ai_label"] == "interested" and out["effect"] == "review"
    assert not got["consent"] and got["stage"] == "engaged"


def test_ai_label_is_used_for_unclear_questions(tmp_path):
    c, _ = ai_client(tmp_path, "wrong_person")
    out = reply(c, lead(c)["id"], "Kripya Meena ji se baat karein")
    assert out["label"] == "wrong_person"


def test_sample_reply_to_email_four_is_a_yes():
    assert classify_reply("sample") == "interested"
    assert classify_reply("Please send a sample") == "interested"


# ---- unsubscribe link: link scanners (GET) can't unsubscribe anyone; the button (POST) does ----
def test_unsubscribe_link_asks_before_acting(client):
    lid = lead(client)["id"]
    page = client.get(f"/u/{lid}")
    assert page.status_code == 200 and "Yes, unsubscribe me" in page.text
    assert lead(client)["stage"] != "unsubscribed"
    client.post(f"/u/{lid}")
    assert lead(client)["stage"] == "unsubscribed"
