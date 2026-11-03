"""DPDP: export, erasure with hashed suppression, retention review, erasure requests, privacy link, consent ledger."""
import hashlib
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.channels.security import server_secret
from app.main import create_app
from app.privacy import asks_for_erasure, is_blocked
from app.unsubscribe import unsub_path
from app.whatsapp import WhatsAppError, send as send_whatsapp_message

CSV = ("company,contact_name,email,phone,website\n"
       "Acme Retail,Ravi Kumar,ravi@acme-retail.in,98765 43210,https://acme-retail.in\n"
       "Beta Logistics,Sam Rao,sam@beta-logistics.in,,https://beta-logistics.in\n")
T0 = "2026-11-04T05:00:00+00:00"


def make(tmp_path, **kw):
    return TestClient(create_app(data_file=tmp_path / "d.json", **kw))


def lead(c, company="Acme Retail"):
    return next(l for l in c.get("/api/leads").json() if l["company"] == company)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def seeded(tmp_path):
    c = make(tmp_path)
    c.post("/api/leads/import", json={"csv": CSV, "source": "test"})
    acme, beta = lead(c), lead(c, "Beta Logistics")
    store = c.app.state.store
    c.post(f"/api/leads/{acme['id']}/consent", json={"text": "Yes on a call, 3 Nov", "channel": "whatsapp"})
    c.post(f"/api/leads/{acme['id']}/enroll")
    c.post("/api/sequences/run", json={"now": T0})
    c.post(f"/api/leads/{acme['id']}/activity", json={"type": "call", "outcome": "connected", "notes": "Spoke to Ravi"})
    c.post("/api/tasks", json={"lead_id": acme["id"], "title": "Send the deck"})
    store.update("inbox", lambda rows: [*rows, {"id": "i1", "lead_id": None, "from": "ravi@acme-retail.in", "text": "hello"},
                                        {"id": "i2", "lead_id": beta["id"], "from": "sam@beta-logistics.in", "text": "hi"}])
    store.update("notifications", lambda rows: [*rows, {"id": "n1", "lead_id": acme["id"], "title": "x"}])
    store.update("whatsapp_outbox", lambda rows: [*rows, {"lead_id": acme["id"], "body": "Hi Ravi"}])
    store.update("transactional_outbox", lambda rows: [*rows, {"lead_id": acme["id"], "subject": "Reminder"}])
    store.update("delivery_events", lambda rows: [*rows, {"key": "k", "type": "delivered", "email": "ravi@acme-retail.in"}])
    store.update("suppressed", lambda rows: [*rows, "ravi@acme-retail.in", "other@x.in"])
    store.update("leads", lambda rows: [{**l, "referred_by": acme["id"]} if l["id"] == beta["id"] else l for l in rows])
    store.write("provider_runs", [{"id": "r1", "lead_ids": [acme["id"], beta["id"]],
                                   "rows": {"hunter": [{"lead_id": acme["id"], "emails": [{"value": "ravi@acme-retail.in"}]},
                                                       {"lead_id": beta["id"], "emails": []}]}}])
    return c, store, acme, beta


def test_export_returns_everything_held_on_the_person(tmp_path):
    c, store, acme, beta = seeded(tmp_path)
    r = c.get(f"/api/leads/{acme['id']}/export")
    assert r.status_code == 200 and f'lead-{acme["id"]}.json' in r.headers["content-disposition"]
    data = r.json()
    assert data["lead"]["email"] == "ravi@acme-retail.in" and data["suppressed"] is True
    assert len(data["outbox"]) == 1 and data["activities"][0]["body"] == "Spoke to Ravi"
    assert any(t["title"] == "Send the deck" for t in data["tasks"])
    assert [i["id"] for i in data["inbox"]] == ["i1"] and len(data["notifications"]) == 1
    assert len(data["whatsapp_outbox"]) == 1 and len(data["transactional_outbox"]) == 1 and len(data["delivery_events"]) == 1
    assert data["consent_ledger"][0]["event"] == "given" and data["data_source_runs"][0]["provider"] == "hunter"
    assert c.get("/api/leads/nope/export").status_code == 404


def test_erase_deletes_everything_and_keeps_only_hashes(tmp_path):
    c, store, acme, beta = seeded(tmp_path)
    assert c.post(f"/api/leads/{acme['id']}/erase", json={}).status_code == 400          # needs confirm
    r = c.post(f"/api/leads/{acme['id']}/erase", json={"confirm": True})
    assert r.status_code == 200 and r.json()["removed"]["leads"] == 1
    assert c.get(f"/api/leads/{acme['id']}").status_code == 404
    for key in ("outbox", "whatsapp_outbox", "transactional_outbox", "activities", "tasks", "notifications"):
        assert all(row.get("lead_id") != acme["id"] for row in store.read(key)), key
    assert [i["id"] for i in store.read("inbox")] == ["i2"] and store.read("delivery_events") == []
    assert store.read("suppressed") == ["other@x.in"]
    assert set(store.read("suppressed_hashes")) == {sha("ravi@acme-retail.in"), sha("+919876543210")}
    raw = (tmp_path / "d.json").read_text()
    assert "ravi@acme-retail.in" not in raw and "Ravi Kumar" not in raw and "9876543210" not in raw
    ledger = store.read("consent_ledger")
    assert all(row["lead_id"] != acme["id"] and row["text"] == "" for row in ledger if row["lead_id"].startswith("erased:"))
    assert ledger[-1]["event"] == "withdrawn" and ledger[-1]["source"] == "data erased on request"
    assert lead(c, "Beta Logistics")["referred_by"] is None
    run = store.read("provider_runs")[0]
    assert run["lead_ids"] == [beta["id"]] and [r["lead_id"] for r in run["rows"]["hunter"]] == [beta["id"]]


def test_erased_person_is_never_imported_again_by_any_path(tmp_path):
    c, store, acme, beta = seeded(tmp_path)
    c.post(f"/api/leads/{acme['id']}/erase", json={"confirm": True})
    again = c.post("/api/leads/import", json={"csv": "company,email\nAcme Again, RAVI@Acme-Retail.in \n"}).json()
    assert again["created"] == 0 and again["suppressed"] == 1
    by_phone = c.post("/api/leads/import", json={"csv": "company,phone\nAcme Phone,+91 98765-43210\n"}).json()
    assert by_phone["created"] == 0 and by_phone["suppressed"] == 1
    sheet = ("Company Name,Contact Person,Designation,Contact Number,Email ID,Open Position\n"
             "Acme Sheet,Ravi,HR,,ravi@acme-retail.in,Telecaller\n")
    assert c.post("/api/leads/import-sheet", json={"csv": sheet, "tab": "leads"}).json()["created"] == 0
    referral = c.post(f"/api/leads/{beta['id']}/referral", json={"company": "Acme Ref", "email": "ravi@acme-retail.in"})
    assert referral.status_code == 400
    # a merge or enrichment can't fill the erased address into another lead either
    store.update_lead(beta["id"], lambda l: {**l, "email": "ravi@acme-retail.in", "phones": ["+919876543210", "+919811122233"]})
    saved = lead(c, "Beta Logistics")
    assert saved["email"] == "sam@beta-logistics.in" and saved["phones"] == ["+919811122233"]
    assert not any("ravi@acme-retail.in" == l.get("email") for l in c.get("/api/leads").json())


def test_senders_check_the_hashed_list(tmp_path):
    c = make(tmp_path)
    c.post("/api/leads/import", json={"csv": CSV, "source": "test"})
    store = c.app.state.store
    acme = lead(c)
    c.post(f"/api/leads/{acme['id']}/enroll")
    c.post(f"/api/leads/{acme['id']}/consent", json={"text": "Yes on WhatsApp, 3 Nov"})
    store.update("suppressed_hashes", lambda rows: [*rows, sha("ravi@acme-retail.in")])    # e.g. erased via another record
    assert is_blocked(store, "Ravi@Acme-Retail.in ") and is_blocked(store, phone="+91 98765 43210") is False
    assert c.post("/api/sequences/run", json={"now": T0}).json()["sent"] == 0
    try:
        send_whatsapp_message(store, acme["id"], "first_after_yes", "", datetime.now(timezone.utc), "https://x.in")
        raise AssertionError("WhatsApp went out")
    except WhatsAppError as exc:
        assert "do-not-contact" in str(exc)


def test_retention_lists_cold_leads_for_review_without_deleting(tmp_path):
    c = make(tmp_path)
    c.post("/api/leads/import", json={"csv": CSV, "source": "test"})
    store = c.app.state.store
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(timespec="seconds")
    store.update("leads", lambda rows: [{**l, "created_at": old, "timeline": [{**e, "at": old} for e in l["timeline"]]} for l in rows])
    acme = lead(c)
    store.update_lead(lead(c, "Beta Logistics")["id"], lambda l: {**l, "engagement": {**l["engagement"], "replies": 1}})
    listed = c.get("/api/compliance/retention").json()
    assert listed["retention_days_cold"] == 365 and [r["id"] for r in listed["leads"]] == [acme["id"]]
    assert listed["leads"][0]["idle_days"] >= 399 and len(c.get("/api/leads").json()) == 2       # nothing deleted
    assert c.put("/api/settings", json={"retention_days_cold": 500}).status_code == 200
    assert c.get("/api/compliance/retention").json()["leads"] == []
    assert c.put("/api/settings", json={"retention_days_cold": 5}).status_code == 400


def test_a_delete_my_data_reply_creates_an_erasure_task_and_withdraws_consent(tmp_path):
    c = make(tmp_path)
    c.post("/api/leads/import", json={"csv": CSV, "source": "test"})
    acme = lead(c)
    r = c.post(f"/api/leads/{acme['id']}/reply", json={"text": "Please delete my data from your system."}).json()
    assert r["label"] == "unsubscribe" and r["lead"]["stage"] == "unsubscribed"
    tasks = [t for t in c.app.state.store.read("tasks") if t["kind"] == "erasure_request"]
    assert len(tasks) == 1 and tasks[0]["title"] == "Erasure request" and tasks[0]["priority"] == "high"
    beta = lead(c, "Beta Logistics")
    assert c.post(f"/api/leads/{beta['id']}/reply", json={"text": "forget me"}).json()["label"] == "unsubscribe"
    assert asks_for_erasure("mera data delete karo") and asks_for_erasure("Remove all my details please")
    assert not asks_for_erasure("Delete the old draft please") and not asks_for_erasure("Not interested, thanks")


def test_privacy_notice_link_goes_in_the_footer(tmp_path):
    c = make(tmp_path)
    assert c.put("/api/settings", json={"privacy_url": "http://bmi.example/privacy"}).status_code == 400
    assert c.put("/api/settings", json={"privacy_url": "https://bmi.example/privacy"}).json()["privacy_url"] == "https://bmi.example/privacy"
    c.put("/api/settings", json={"sender_company": "BookMyInterview.ai", "sender_address": "Noida"})
    c.post("/api/leads/import", json={"csv": CSV, "source": "test"})
    c.post(f"/api/leads/{lead(c)['id']}/enroll")
    c.post("/api/sequences/run", json={"now": T0})
    body = c.get("/api/outbox").json()[0]["body"]
    assert body.endswith("BookMyInterview.ai · Noida\nPrivacy notice: https://bmi.example/privacy")
    assert c.put("/api/settings", json={"privacy_url": ""}).json()["privacy_url"] == ""


def test_consent_ledger_records_every_yes_and_withdrawal(tmp_path):
    c = make(tmp_path)
    c.post("/api/leads/import", json={"csv": CSV, "source": "test"})
    store = c.app.state.store
    acme, beta = lead(c), lead(c, "Beta Logistics")
    c.post(f"/api/leads/{acme['id']}/consent", json={"text": "Yes on a call", "channel": "whatsapp", "source": "call with Priya"})
    c.post(unsub_path(store, acme["id"]))
    c.post(unsub_path(store, acme["id"]))                                     # clicking again adds nothing
    c.post(f"/api/leads/{beta['id']}/reply", json={"text": "Yes, interested. Send it."})
    c.post(f"/api/leads/{beta['id']}/reply", json={"text": "STOP"})
    rows = c.get(f"/api/compliance/consent-ledger?lead_id={acme['id']}").json()
    assert [(r["event"], r["channel"], r["source"]) for r in rows] == [
        ("given", "whatsapp", "call with Priya"), ("withdrawn", "email", "clicked unsubscribe link")]
    assert rows[0]["text"] == "Yes on a call" and rows[0]["withdrawn_at"] is None and rows[1]["withdrawn_at"] == rows[1]["at"]
    beta_rows = [(r["event"], r["source"]) for r in store.read("consent_ledger") if r["lead_id"] == beta["id"]]
    assert beta_rows == [("given", "email reply (pasted)"), ("withdrawn", "email reply (pasted)")]


def test_complaints_and_manual_unsubscribes_are_withdrawals(tmp_path):
    from app.inbound import process_delivery_event
    c = make(tmp_path)
    c.post("/api/leads/import", json={"csv": CSV, "source": "test"})
    store = c.app.state.store
    acme, beta = lead(c), lead(c, "Beta Logistics")
    process_delivery_event(store, {"type": "complaint", "email": "ravi@acme-retail.in", "provider_id": "p1"})
    c.post(f"/api/leads/{beta['id']}/stage", json={"stage": "unsubscribed"})
    sources = {r["lead_id"]: r["source"] for r in store.read("consent_ledger") if r["event"] == "withdrawn"}
    assert sources == {acme["id"]: "spam complaint reported by the email provider", beta["id"]: "moved to Unsubscribed by a person"}


def test_compliance_endpoints_are_owner_only(tmp_path):
    c = make(tmp_path, admin_password="admin-pass-123")
    owner = {"Authorization": "Bearer " + c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]}
    c.post("/api/users", json={"email": "p@example.com", "role": "caller", "password": "long-password-1"}, headers=owner)
    caller = {"Authorization": "Bearer " + c.post("/api/login", json={"email": "p@example.com", "password": "long-password-1"}).json()["token"]}
    c.post("/api/leads/import", json={"csv": CSV, "source": "test"}, headers=owner)
    lid = c.get("/api/leads", headers=owner).json()[0]["id"]
    assert c.get(f"/api/leads/{lid}/export", headers=caller).status_code == 403
    assert c.post(f"/api/leads/{lid}/erase", json={"confirm": True}, headers=caller).status_code == 403
    assert c.get("/api/compliance/retention", headers=caller).status_code == 403
    assert c.get(f"/api/leads/{lid}/export", headers=owner).status_code == 200
    server_secret(c.app.state.store)
