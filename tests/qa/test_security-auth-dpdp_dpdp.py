"""QA (DPDP): export / erase, the hashed never-again list across every import path and send channel, restore of an
older backup, the append-only consent ledger and secrets encrypted at rest.

The person (fictional): Kavya Iyer, HR Head, Zentrix Logistics Pvt. Ltd., kavya.iyer@zentrix-logistics.example,
+91 90000 10042. She appears, spelled differently, in four fixture files (Apollo export, Google Maps scrape with a BOM
and semicolons, the team's Google Sheet, Contact Scraper JSON) - see tests/qa/fixtures/security-auth-dpdp/.
"""
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import flow_actions
from app.main import create_app
from app.privacy import contact_hash, is_blocked
from app.unsubscribe import unsub_path
from app.whatsapp import WhatsAppError, send as send_whatsapp

FIX = Path(__file__).parent / "fixtures" / "security-auth-dpdp"
ADMIN = "qa-admin-password-01"
EMAIL, DIGITS = "kavya.iyer@zentrix-logistics.example", "9000010042"
T0 = "2026-11-04T05:00:00+00:00"                         # Wednesday 10:30 IST


def fixture(name):
    return (FIX / name).read_text(encoding="utf-8")


def make(tmp_path, password=ADMIN):
    c = TestClient(create_app(data_file=tmp_path / "store.json", admin_password=password), client=("198.51.100.20", 1))
    if password:
        token = c.post("/api/login", json={"password": password}).json()["token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
    return c


def holds_her(lead):
    text = json.dumps({k: lead.get(k) for k in ("email", "phone", "phones", "other_contacts", "contact_name")}).lower()
    return EMAIL in text or DIGITS in text.replace(" ", "").replace("-", "")


def kavya(c):
    return next(l for l in c.get("/api/leads").json() if (l.get("email") or "") == EMAIL)


def seeded(tmp_path):
    """Three real-world sources merged into the lead list, consent recorded, then a WhatsApp yes."""
    c = make(tmp_path)
    assert c.post("/api/import/commit", json={"csv": fixture("apollo_people_export.csv")}).status_code == 200
    assert c.post("/api/import/commit", json={"csv": fixture("google_maps_places_semicolon_bom.csv")}).status_code == 200
    assert c.post("/api/leads/import-sheet", json={"csv": fixture("team_sheet_leads.csv"), "tab": "leads"}).status_code == 200
    lead = kavya(c)
    c.post(f"/api/leads/{lead['id']}/consent", json={"text": "Haan, WhatsApp pe bhej do (call, 3 Nov)", "channel": "whatsapp"})
    return c, c.app.state.store, lead


# ---------- export and erase ----------
def test_export_then_erase_leaves_no_trace_in_the_data_file(tmp_path):
    c, store, lead = seeded(tmp_path)
    assert len(c.get("/api/leads").json()) > 150
    export = c.get(f"/api/leads/{lead['id']}/export")
    assert export.status_code == 200 and export.json()["lead"]["email"] == EMAIL and export.json()["consent_ledger"]
    r = c.post(f"/api/leads/{lead['id']}/erase", json={"confirm": True})
    assert r.status_code == 200 and r.json()["suppressed_hashes"] >= 2
    raw = (tmp_path / "store.json").read_text(encoding="utf-8").lower()
    assert EMAIL not in raw and DIGITS not in raw and "haan, whatsapp pe" not in raw
    assert {contact_hash(EMAIL), contact_hash("+91" + DIGITS)} <= set(store.read("suppressed_hashes"))
    assert c.get(f"/api/leads/{lead['id']}/export").status_code == 404


def test_erased_person_never_comes_back_through_any_import_path(tmp_path):
    c, store, lead = seeded(tmp_path)
    c.post(f"/api/leads/{lead['id']}/erase", json={"confirm": True})
    other = next(l for l in c.get("/api/leads").json() if not holds_her(l))
    attempts = {
        "quick paste (Apollo)": c.post("/api/leads/import", json={"csv": fixture("apollo_people_export.csv")}),
        "smart import (Apollo)": c.post("/api/import/commit", json={"csv": fixture("apollo_people_export.csv")}),
        "smart import (Maps, BOM + ;)": c.post("/api/import/commit", json={"csv": fixture("google_maps_places_semicolon_bom.csv")}),
        "team sheet": c.post("/api/leads/import-sheet", json={"csv": fixture("team_sheet_leads.csv"), "tab": "leads"}),
        "contact scraper": c.post("/api/providers/import-contact-scraper", json={"json": fixture("contact_scraper_output.json")}),
        "pasted web page": c.post("/api/scrape/extract", json={
            "url": "https://www.zentrix-logistics.example/contact", "company": "Zentrix Logistics", "add_leads": True,
            "html": "<p>HR: Kavya.Iyer@Zentrix-Logistics.example, +91-90000-10042</p>"}),
        "jobs CSV": c.post("/api/jobs/ingest", json={"csv": "company,title,location,date_posted,website\n"
                                                           "Zentrix Logistics Pvt. Ltd.,Warehouse Associate,Pune,2026-10-28,"
                                                           "https://www.zentrix-logistics.example\n"}),
        "quick paste, odd spellings": c.post("/api/leads/import", json={"csv": (
            "Company,Contact Person,Email ID,Contact Number\n"
            "ZENTRIX LOGISTICS PVT. LTD.,KAVYA IYER, KAVYA.IYER@ZENTRIX-LOGISTICS.EXAMPLE ,\n"
            "Zentrix (new branch),Kavya,,090000 10042\n"
            "Zentrix (Chakan),Kavya I.,,+91-90000-10042\n"
            "Zentrix WA,Kavya,,919000010042\n")}),
    }
    assert all(r.status_code == 200 for r in attempts.values()), {k: r.text[:200] for k, r in attempts.items() if r.status_code != 200}
    referral = c.post(f"/api/leads/{other['id']}/referral", json={"company": "Zentrix", "email": EMAIL.upper()})
    assert referral.status_code == 400
    leads = c.get("/api/leads").json()
    assert not [l["company"] for l in leads if holds_her(l)]
    raw = (tmp_path / "store.json").read_text(encoding="utf-8").lower()
    assert EMAIL not in raw and DIGITS not in raw


def test_every_send_channel_refuses_an_erased_contact_still_held_by_another_lead(tmp_path):
    """Kavya moved jobs: an older lead at another company still carries her mobile. Erasing the Zentrix lead puts the
    number on the hashed list; email, WhatsApp, flows (SMS / WhatsApp / email) and meeting invites must all stop."""
    c, store, lead = seeded(tmp_path)
    store.update("leads", lambda rows: [*rows, {**lead, "id": "qa-oldjob", "company": "Nimbus Staffing (old job)",
                                                 "email": EMAIL, "domain": "nimbus-staffing.example", "dedupe_key": "qa:oldjob",
                                                 "stage": "interested", "consent": {"text": "yes", "at": T0, "source": "call"}}])
    c.post(f"/api/leads/{lead['id']}/erase", json={"confirm": True})
    survivor = next(l for l in store.read("leads") if l["id"] == "qa-oldjob")
    assert is_blocked(store, survivor["email"], survivor["phone"])
    with pytest.raises(WhatsAppError):
        send_whatsapp(store, "qa-oldjob", "first_after_yes", "", datetime.now(timezone.utc), "https://x.example")
    assert flow_actions.blocked_reason(store, survivor)                  # flows check this before every message node
    assert flow_actions.blocked_reason(store, {**survivor, "email": None})               # by phone alone too
    c.post("/api/leads/qa-oldjob/enroll")
    assert c.post("/api/sequences/run", json={"now": T0}).json().get("sent", 0) == 0
    from app.channels.invites import send_invite
    meeting = {"id": "m1", "title": "Demo", "start": "2026-11-05T05:00:00+00:00", "end": "2026-11-05T05:30:00+00:00"}
    assert send_invite(store, survivor, meeting)["status"] == "skipped"
    assert send_invite(store, {**survivor, "email": "new.work@qa.example"}, {**meeting, "attendee_email": EMAIL.upper()})["status"] == "skipped"


def test_flow_blocked_reason_checks_the_hashed_erasure_list():
    """Regression: blocked_reason only looked at the plain suppression list, so a flow could SMS an erased number."""
    class Store:
        def __init__(self):
            self.data = {"suppressed": [], "suppressed_hashes": [contact_hash("+919000010042")]}

        def read(self, key):
            return self.data[key]
    lead = {"stage": "interested", "email": "someone@qa.example", "phone": "+91 90000 10042"}
    assert flow_actions.blocked_reason(Store(), lead)
    assert flow_actions.blocked_reason(Store(), {**lead, "phone": "+91 90000 10043"}) is None


# ---------- restore of an older backup ----------
def test_restoring_an_older_backup_does_not_resurrect_erased_or_unsubscribed_people(tmp_path):
    """Regression: restore replaced suppressed_hashes and suppressed with the backup's older lists, so a backup taken
    before an erasure brought the person back (and a later unsubscribe was forgotten)."""
    c, store, lead = seeded(tmp_path)
    backup = c.get("/api/backup")
    assert backup.status_code == 200 and EMAIL in gzip.decompress(backup.content).decode()
    victim = next(l for l in c.get("/api/leads").json() if l.get("email") and l["id"] != lead["id"])
    assert c.post(unsub_path(store, victim["id"])).status_code == 200
    c.post(f"/api/leads/{lead['id']}/erase", json={"confirm": True})
    dry = c.post("/api/backup/restore", content=backup.content)
    assert dry.status_code == 200 and dry.json()["dry_run"] is True
    r = c.post("/api/backup/restore?confirm=1", content=backup.content)
    assert r.status_code == 200, r.text
    assert not [l for l in c.get("/api/leads").json() if holds_her(l)]
    assert is_blocked(store, EMAIL) and is_blocked(store, phone="+91 90000 10042")
    assert is_blocked(store, victim["email"])                            # the later unsubscribe still holds
    assert len(c.get("/api/leads").json()) > 150                         # everything else came back


# ---------- consent ledger ----------
def test_consent_ledger_only_ever_grows_and_no_route_can_edit_it(tmp_path):
    c, store, lead = seeded(tmp_path)
    others = [l for l in c.get("/api/leads").json() if l["id"] != lead["id"]][:3]
    for l in others:
        c.post(f"/api/leads/{l['id']}/consent", json={"text": "YES on WhatsApp", "channel": "whatsapp"})
    snapshots = [store.read("consent_ledger")]
    c.post(unsub_path(store, others[0]["id"]))                                       # withdrawal = a new row
    c.post(unsub_path(store, others[0]["id"]))                                       # a second click adds nothing
    c.post(f"/api/leads/{others[1]['id']}/consent", json={"text": "Yes again by email", "channel": "email"})
    snapshots.append(store.read("consent_ledger"))
    c.post(f"/api/leads/{lead['id']}/erase", json={"confirm": True})
    snapshots.append(store.read("consent_ledger"))
    before, middle, after = snapshots
    assert middle[:len(before)] == before and len(middle) == len(before) + 2
    assert [r["event"] for r in middle[len(before):]] == ["withdrawn", "given"]
    # erasure: her rows are redacted in place (proof kept, no text, hashed id), everyone else's are byte-identical
    assert len(after) == len(middle) + 1
    for old, new in zip(middle, after):
        if old["lead_id"] == lead["id"]:
            assert new["lead_id"].startswith("erased:") and new["text"] == "" and new["at"] == old["at"]
        else:
            assert new == old
    consent_routes = {(m, p) for m, p in _routes(c.app) if "consent" in p}
    assert consent_routes == {("POST", "/api/leads/{lead_id}/consent"), ("GET", "/api/compliance/consent-ledger")}


def _routes(app):
    def walk(routes, prefix=""):
        for r in routes:
            if hasattr(r, "original_router"):
                yield from walk(r.original_router.routes, prefix + (getattr(getattr(r, "include_context", None), "prefix", "") or ""))
            elif getattr(r, "methods", None):
                for m in r.methods:
                    if m not in ("HEAD", "OPTIONS"):
                        yield m, prefix + r.path
    return set(walk(app.routes))


# ---------- secrets at rest ----------
def test_saved_keys_round_trip_encrypted_across_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTREACH_SECRET_KEY", "qa-a-long-random-server-key-0123456789abcdef")
    c = make(tmp_path)
    assert c.put("/api/integrations/apollo", json={"mode": "api", "values": {"api_key": "qa_apollo_SECRET_7781"}}).status_code == 200
    c.put("/api/settings", json={"webhook_secret": "qa_webhook_SECRET_5521"})
    raw = (tmp_path / "store.json").read_text()
    assert "qa_apollo_SECRET_7781" not in raw and "qa_webhook_SECRET_5521" not in raw
    for path in ("/api/integrations", "/api/settings"):
        assert "SECRET_7781" not in c.get(path).text and "SECRET_5521" not in c.get(path).text
    restarted = make(tmp_path)                                        # same key: the app reads its own keys back
    store = restarted.app.state.store
    assert store.read("integrations")["apollo"]["values"]["api_key"] == "qa_apollo_SECRET_7781"
    assert restarted.post("/api/activation/event", json={"email": "nobody@qa.example", "event": "role_drafted"},
                          headers={"X-Webhook-Secret": "qa_webhook_SECRET_5521", "Authorization": ""}).status_code == 404
    plain_backup = gzip.decompress(restarted.get("/api/backup").content).decode()
    assert "SECRET_7781" not in plain_backup and "SECRET_5521" not in plain_backup
