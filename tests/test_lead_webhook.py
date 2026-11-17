"""Lead webhook (app/lead_webhook.py, lead_webhook_events.py, retry_queue.py, routes_lead_webhook.py): signature, payloads,
retries with back-off, SSRF guard, off without an address, Google Sheets mode, owner-only setting, workspace isolation,
secrets sealed and left out of backups. No network: a fake transport and a fake DNS."""
import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import backup, lead_webhook as hooks, workspaces
from app.http_util import HttpStatusError
from app.main import create_app
from app.retry_queue import JobError, now_utc, parse

PASS = "admin-pass-123"
URL = "https://hooks.example.com/catch/1"
SHEET = "https://script.google.com/macros/s/AKfy123/exec"
SECRET = "a-long-shared-secret-1"
CSV = "company,contact_name,email,phone\nAcme,Ravi,ravi@acme.in,98765 43210\n"
DNS = {"hooks.example.com": ["93.184.216.34"], "script.google.com": ["142.250.1.1"], "inside.example.com": ["10.0.0.7"]}


class Transport:
    def __init__(self):
        self.calls, self.fail = [], []

    def __call__(self, url, headers, raw):
        self.calls.append({"url": url, "headers": headers, "raw": raw, "body": json.loads(raw)})
        if self.fail:
            raise self.fail.pop(0)
        return {"_status": 200}


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    t = Transport()
    monkeypatch.setattr(hooks, "TRANSPORT", t)
    monkeypatch.setattr(hooks, "RESOLVER", lambda host: DNS.get(host, ["93.184.216.35"]))
    monkeypatch.setattr(hooks.QUEUE, "kick_enabled", False)
    app = create_app(data_file=tmp_path / "w.json", admin_password=PASS)
    c = TestClient(app, client=("203.0.113.9", 50000))
    owner = {"Authorization": "Bearer " + c.post("/api/login", json={"password": PASS}).json()["token"]}

    def login_as(email, role, ws=()):
        r = c.post("/api/users", json={"email": email, "name": email.split("@")[0], "role": role, "password": "long-password-1",
                                       "workspaces": list(ws), **({} if ws else {"all_workspaces": True})}, headers=owner)
        assert r.status_code == 200, r.text
        return {"Authorization": "Bearer " + c.post("/api/login", json={"email": email, "password": "long-password-1"}).json()["token"]}
    return c, owner, app.state.store, t, login_as


def turn_on(c, owner, **extra):
    r = c.put("/api/lead-webhook", json={"url": URL, "secret": SECRET, "enabled": True, **extra}, headers=owner)
    assert r.status_code == 200, r.text
    return r.json()


def outbox(store):
    return store.read(hooks.OUTBOX)


def test_created_event_is_signed_and_shaped(rig):
    c, owner, store, t, _ = rig
    card = turn_on(c, owner)
    assert card["hook"]["secret_set"] is True and "secret" not in json.dumps(card["hook"]).replace("secret_set", "")
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=owner)
    assert [j["body"]["event"] for j in outbox(store)] == ["lead.created"]
    assert hooks.QUEUE.process_due(store) == 1
    call = t.calls[0]
    assert call["url"] == URL and call["headers"]["X-Outreach-Event"] == "lead.created"
    expected = "sha256=" + hmac.new(SECRET.encode(), call["raw"], hashlib.sha256).hexdigest()
    assert call["headers"]["X-Outreach-Signature"] == expected
    body = call["body"]
    assert body["event"] == "lead.created" and body["workspace"] == "main" and body["delivery_id"]
    assert body["lead"]["company"] == "Acme" and body["lead"]["phone"] == "+919876543210" and "timeline" not in body["lead"]
    assert outbox(store)[0]["status"] == "done"
    log = c.get("/api/lead-webhook", headers=owner).json()["log"]
    assert log[0]["status"] == "done" and log[0]["event"] == "lead.created" and log[0]["company"] == "Acme"


def test_updated_only_for_picked_fields(rig):
    c, owner, store, t, _ = rig
    turn_on(c, owner, fields=["stage"], events=["lead.updated"])
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=owner)
    assert outbox(store) == []                                                     # lead.created not asked for
    lead = store.read("leads")[0]
    c.post(f"/api/leads/{lead['id']}/note", json={"text": "called, no answer"}, headers=owner)
    assert outbox(store) == []                                                     # a note is not a watched field
    store.update_lead(lead["id"], lambda l: {**l, "stage": "contacted"})
    jobs = outbox(store)
    assert len(jobs) == 1 and jobs[0]["body"]["event"] == "lead.updated" and jobs[0]["body"]["changed"] == ["stage"]


def test_retries_back_off_then_fail_and_retry_button(rig):
    c, owner, store, t, _ = rig
    turn_on(c, owner)
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=owner)
    now = now_utc()
    t.fail = [HttpStatusError(503), OSError("Could not connect")]
    hooks.QUEUE.process_due(store, now)
    job = outbox(store)[0]
    near = lambda a, b: abs((a - b).total_seconds()) < 2
    assert job["status"] == "pending" and job["attempts"] == 1 and near(parse(job["next_at"]), now + timedelta(minutes=1))
    assert hooks.QUEUE.process_due(store, now) == 0                                # not due yet
    hooks.QUEUE.process_due(store, now + timedelta(minutes=2))
    job = outbox(store)[0]
    assert job["attempts"] == 2 and near(parse(job["next_at"]), now + timedelta(minutes=7))
    hooks.QUEUE.process_due(store, now + timedelta(minutes=10))
    assert outbox(store)[0]["status"] == "done" and len(t.calls) == 3
    log = c.get("/api/lead-webhook", headers=owner).json()["log"]
    assert [r["status"] for r in log[:3]] == ["done", "retrying", "retrying"]
    # a 4xx refusal is final at once; Retry puts it back
    store.update_lead(store.read("leads")[0]["id"], lambda l: {**l, "stage": "contacted"})
    t.fail = [HttpStatusError(410)]
    hooks.QUEUE.process_due(store, now + timedelta(minutes=20))
    failed = [j for j in outbox(store) if j["status"] == "failed"]
    assert len(failed) == 1 and failed[0]["error"] == "HTTP 410"
    assert c.post("/api/lead-webhook/retry", json={}, headers=owner).json() == {"requeued": 1}
    hooks.QUEUE.process_due(store, now + timedelta(minutes=21))
    assert all(j["status"] == "done" for j in outbox(store))


def test_gives_up_after_the_last_attempt(rig):
    c, owner, store, t, _ = rig
    turn_on(c, owner)
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=owner)
    when = now_utc()
    for _ in range(hooks.QUEUE.max_attempts):
        t.fail = [HttpStatusError(500)]
        hooks.QUEUE.process_due(store, when)
        when += timedelta(days=1)
    job = outbox(store)[0]
    assert job["status"] == "failed" and job["attempts"] == hooks.QUEUE.max_attempts


@pytest.mark.parametrize("url", ["https://127.0.0.1/hook", "https://10.0.0.5/x", "https://[::1]/x", "https://localhost/x",
                                 "https://inside.example.com/x", "http://hooks.example.com/x", "https://user:pw@hooks.example.com/x"])
def test_private_and_unsafe_addresses_are_refused(rig, url):
    c, owner, *_ = rig
    r = c.put("/api/lead-webhook", json={"url": url, "enabled": True}, headers=owner)
    assert r.status_code == 400


def test_send_time_guard_blocks_a_host_that_now_points_inside(monkeypatch):
    monkeypatch.setattr(hooks, "TRANSPORT", None)
    monkeypatch.setattr(hooks, "RESOLVER", lambda host: ["10.0.0.7"])
    with pytest.raises(JobError) as err:
        hooks.send({"url": "https://hooks.example.com/x", "mode": "webhook"}, hooks.payload("lead.created", {"id": "l1"}))
    assert err.value.retry is False and "private" in str(err.value)


def test_off_without_an_address_or_when_switched_off(rig):
    c, owner, store, t, _ = rig
    assert c.put("/api/lead-webhook", json={"enabled": True}, headers=owner).status_code == 400
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=owner)
    assert outbox(store) == []
    c.put("/api/lead-webhook", json={"url": URL, "enabled": False}, headers=owner)
    store.update_lead(store.read("leads")[0]["id"], lambda l: {**l, "stage": "contacted"})
    assert outbox(store) == [] and t.calls == []
    assert c.post("/api/lead-webhook/test", json={}, headers=owner).json()["ok"] is True      # the test button still works
    assert t.calls[0]["body"]["test"] is True and t.calls[0]["body"]["lead"]["id"] == "test-lead"


def test_sheets_mode_body_and_redirect_counts_as_success(rig):
    c, owner, store, t, _ = rig
    assert c.put("/api/lead-webhook", json={"url": URL, "mode": "sheets", "enabled": True}, headers=owner).status_code == 400
    card = c.put("/api/lead-webhook", json={"url": SHEET, "mode": "sheets", "enabled": True}, headers=owner).json()
    assert "doPost" in card["apps_script"] and card["hook"]["mode"] == "sheets"
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\n=HYPERLINK(1),Ravi,ravi@acme.in\n", "source": "apollo"},
           headers=owner)
    t.fail = [HttpStatusError(302)]
    hooks.QUEUE.process_due(store)
    body = t.calls[0]["body"]
    assert body["columns"] == list(hooks.SHEET_COLUMNS) and len(body["row"]) == len(body["columns"])
    row = dict(zip(body["columns"], body["row"]))
    assert row["event"] == "lead.created" and row["email"] == "ravi@acme.in" and row["company"].startswith("'=")
    assert outbox(store)[0]["status"] == "done" and "Apps Script" in outbox(store)[0]["result"]


def test_harvest_lead_found(rig, monkeypatch):
    from app import harvest
    from tests.harvest_fakes import fake_http, osm_element
    c, owner, store, t, _ = rig
    turn_on(c, owner, events=["harvest.lead_found"])
    monkeypatch.setattr(harvest, "KICK", False)
    monkeypatch.setattr(harvest, "_last_call", {})
    http = fake_http(None, overpass={"elements": [osm_element(1), osm_element(2)]})
    env_for = lambda job: harvest.Env(http, sleep=lambda s: None, clock=lambda: 0.0)
    job = c.post("/api/harvest/jobs", json={"keywords": "dentist", "locations": "Pune", "sources": ["osm"], "start": True,
                                            "name": "Pune dentists"}, headers=owner).json()
    harvest.run_until_idle(store, env_for)
    found = [j["body"] for j in outbox(store)]
    assert found and all(b["event"] == "harvest.lead_found" for b in found)
    assert found[0]["harvest"]["job_id"] == job["id"] and found[0]["harvest"]["job_name"] == "Pune dentists"
    assert found[0]["harvest"]["keyword"] == "dentist" and found[0]["harvest"]["outcome"] == "new"


def test_owner_only(rig):
    c, owner, store, t, login_as = rig
    ws = workspaces.create(store, "Client One", "test")["id"]
    for role, wss in (("caller", ()), ("viewer", ()), ("admin", (ws,))):
        h = login_as(f"{role}@x.example", role, wss)
        assert c.get("/api/lead-webhook", headers=h).status_code == 403, role
        assert c.put("/api/lead-webhook", json={"url": URL, "workspace": ws}, headers=h).status_code == 403, role
        assert c.post("/api/lead-webhook/test", json={}, headers=h).status_code == 403, role
    assert c.get("/api/lead-webhook").status_code == 401
    turn_on(c, owner)
    assert any(a.get("action") == "lead_webhook.save" and URL not in str(a) and SECRET not in str(a) for a in store.read("audit"))


def test_workspace_isolation(rig):
    c, owner, store, t, _ = rig
    ws = workspaces.create(store, "Client One", "test")["id"]
    turn_on(c, owner, workspace=ws)
    assert c.get("/api/lead-webhook", headers=owner).json()["hook"]["configured"] is False         # main has none
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=owner)               # into main
    assert outbox(store) == []
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nClientCo,Neha,neha@clientco.in\n", "source": "apollo"},
           headers={**owner, "X-Workspace": ws})
    assert [(j["ws"], j["body"]["lead"]["company"]) for j in outbox(store)] == [(ws, "ClientCo")]


def test_secrets_sealed_and_kept_out_of_backups(rig, tmp_path, monkeypatch):
    c, owner, store, *_ = rig
    turn_on(c, owner)
    plain = backup.strip_secrets(store.snapshot())
    assert plain["lead_hooks"]["main"]["url"] == "" and plain["lead_hooks"]["main"]["secret"] == ""
    assert plain["lead_hooks"]["main"]["enabled"] is False
    from app.secretbox import SecretBox
    sealed = SecretBox("k" * 32).seal({"lead_hooks": store.read("lead_hooks")})["lead_hooks"]["main"]
    assert sealed["url"].startswith("enc:v1:") and sealed["secret"].startswith("enc:v1:")
    assert SecretBox("k" * 32).unseal({"lead_hooks": {"main": sealed}})["lead_hooks"]["main"]["secret"] == SECRET
