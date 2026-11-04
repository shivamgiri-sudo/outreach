"""QA: email sequence copy on a messy 144-row lead file - variable cleaning, no-role / no-name variants, unfilled
placeholders (email and WhatsApp), A/B variant recording, identity footer + privacy line, Templates and Stories APIs.

Everything runs in practice mode with fake clocks (ALLOW_NOW_OVERRIDE); no transport ever leaves the process.
Fixture: tests/qa/fixtures/templates-prompts-ai/messy_leads.csv (fictional; BOM, ALL CAPS, Pvt/LLP/OPC/(P)/Pte
suffixes, Indian honorifics with and without a dot, mail-merge leftovers, a blank row, a duplicate company).
"""
import csv
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import whatsapp
from app.main import create_app
from app.sequences import DEFAULT_SEQUENCE, lead_variables, pick_variant, render

FIX = Path(__file__).parent / "fixtures" / "templates-prompts-ai"
CSV_TEXT = (FIX / "messy_leads.csv").read_text(encoding="utf-8-sig")
EXPECTED = json.loads((FIX / "messy_leads_expected.json").read_text(encoding="utf-8"))["rows"]
T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)          # Tuesday 10:30 IST
HONORIFIC_GREETING = re.compile(r"^Hi (Mr|Mrs|Ms|Dr|Shri|Sri|Smt|Km|Sh|Prof|Er|Ca|Adv|Capt)\b|^Hi \w\.|^Hi .*[{}]")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to reach a real provider")
    monkeypatch.setattr(whatsapp, "request", refuse)


def csv_rows():
    return [r for r in csv.DictReader(CSV_TEXT.splitlines()) if r["email"]]


def email1_for(row: dict, include_link=False) -> dict:
    lead = {**row, "id": "L1", "role": row.get("Hiring For")}
    v = lead_variables(lead, "Priya", "https://out.example.invalid",
                       {"offer_line": "Your first role is free.", "demo_link": "https://out.example.invalid/demo/t",
                        "include_link": include_link})
    step = pick_variant(DEFAULT_SEQUENCE[0], v)
    subject, ms = render(step["subject"], v)
    body, mb = render(step["body"], v)
    return {"v": v, "subject": subject, "body": body, "missing": ms + mb, "variant": step["variant"]}


def make_client(tmp_path, **kwargs):
    c = TestClient(create_app(data_file=tmp_path / "d.json", **kwargs))
    out = c.post("/api/leads/import", json={"csv": CSV_TEXT, "source": "apollo"}).json()
    assert out["created"] == 141 and out["duplicates"] == 2 and out["rejected"] == 0
    return c


def by_email(c):
    return {l["email"]: l for l in c.get("/api/leads").json()}


def run_day(c, day: datetime, runs=4):
    for i in range(runs):
        c.post("/api/sequences/run", json={"now": (day + timedelta(minutes=i * 20)).isoformat()})


# ---------------- per-row copy (pure render, every expected row) ----------------
@pytest.mark.parametrize("email", sorted(EXPECTED))
def test_email1_copy_for_each_messy_row(email):
    row = next(r for r in csv_rows() if r["email"] == email)
    exp, got = EXPECTED[email], email1_for(row)
    if exp.get("blocked"):
        return                                   # blocked at send time (see the end-to-end test): the lead's own data holds a mail-merge leftover
    assert not got["missing"]
    assert got["body"].startswith(exp["greeting"] + "\n\n")
    assert got["subject"] == "Are you closing vacancies or compromising hiring?" and got["variant"] == "default"


def test_no_rendered_email_in_the_file_leaks_honorifics_suffixes_or_braces():
    for row in csv_rows():
        got = email1_for(row)
        if got["missing"]:
            continue
        first = got["body"].split("\n")[0]
        assert not HONORIFIC_GREETING.search(first), (row["contact_name"], first)
        assert not re.search(r"\b(Pvt|Ltd|LLP|OPC|Pte|Limited|Private)\b|\(P\)| P is hiring", got["body"]), row["company"]
        assert "{{" not in got["body"] + got["subject"] and "}}" not in got["body"]
        assert not re.search(r"\b(and|or)\.\s", got["body"]), "a shortened role must not end on 'and'"
        assert len(got["v"]["role"]) <= 40


@pytest.mark.parametrize("name,greeting", [
    ("Dr.MEENA IYER", "Hi Meena,"), ("Mr.Vikram Singh Rathore", "Hi Vikram,"), ("Prof. imran nair", "Hi Imran,"),
    ("Er. Suresh Patil", "Hi Suresh,"), ("CA Rakesh Jain", "Hi Rakesh,"), ("KM. POOJA GUPTA", "Hi Pooja,"),
    ("Sh. Mahesh Yadav", "Hi Mahesh,"), ("Adv. Nisha Menon", "Hi Nisha,"), ("Capt. Arjun Nair", "Hi Arjun,"),
    ("Smt. KAVITA devi", "Hi Kavita,"), ("SHRI. RAMESH", "Hi Ramesh,"), ("A.K. Sharma", "Hello,"),
    ("{{First Name}}", "Hello,"), ("HR Team", "Hello,"), ("", "Hello,"), ("ravi.kumar@acme.example", "Hello,"),
    ("रवि कुमार", "Hi रवि,"),
])
def test_greeting_cleaning(name, greeting):
    assert lead_variables({"id": "x", "contact_name": name, "company": "Acme"}, "Priya", "https://b.invalid")["greeting"] == greeting


# ---------------- end to end: import -> enrol -> 3 weeks of practice sends ----------------
def test_three_week_practice_run_over_the_messy_file(tmp_path):
    c = make_client(tmp_path)
    c.put("/api/settings", json={"daily_cap": 200, "per_run_cap": 50, "sender_company": "MCN Outreach Demo",
                                 "sender_website": "https://mcn.example.invalid", "sender_contact_email": "hi@mcn.example.invalid",
                                 "privacy_url": "https://mcn.example.invalid/privacy"})
    leads = by_email(c)
    for lead in leads.values():
        assert c.post(f"/api/leads/{lead['id']}/enroll").status_code == 200
    for day in (T0, T0 + timedelta(days=1), T0 + timedelta(days=3), T0 + timedelta(days=7), T0 + timedelta(days=14)):
        run_day(c, day)
    box = c.get("/api/outbox").json()
    assert box and all(m["status"] == "dry_run" for m in box)          # practice mode: nothing left the computer
    first = [m for m in box if m["step"].startswith("Email 1")]
    assert len(first) == 140                                           # all but the mail-merge leftover
    assert {m["step"].split(" ")[1] for m in box} == {"1", "2", "3", "4"}      # five weeks are not run here, so Emails 5 and 6 are not due
    for m in box:
        lead = next(l for l in leads.values() if l["id"] == m["lead_id"])
        text = m["subject"] + m["body"]
        assert "{{" not in text and "}}" not in text
        assert not HONORIFIC_GREETING.search(m["body"].split("\n")[0]), m["body"].split("\n")[0]
        assert re.search(rf"Unsubscribe: http\S+/u/{lead['id']}\.[\w-]+\n", m["body"]), "signed unsubscribe link"
        assert m["body"].endswith("MCN Outreach Demo · https://mcn.example.invalid · hi@mcn.example.invalid\n"
                                  "Privacy notice: https://mcn.example.invalid/privacy")
        assert m["variant"] in ("default", "no_role")
    # the mail-merge leftover never went out and says why
    stuck = by_email(c)["nikhil@mailmerge-leftover.example"]
    assert all(m["lead_id"] != stuck["id"] for m in box)
    assert "missing" in stuck["sequence"]["blocked"]
    # two contacts at acmetech.example never got mail on the same IST day
    acme = [m for m in box if m["to"].endswith("@acmetech.example")]
    assert len({m["sent_at_ist_date"] for m in acme if m["step"].startswith("Email 1")}) == 2


def test_link_setting_changes_nothing_when_the_sequence_has_no_link_variant(tmp_path):
    c = make_client(tmp_path)
    leads = by_email(c)
    for email in ("ravi.kumar@acmetech.example", "kavita@xyzservices.example"):
        c.post(f"/api/leads/{leads[email]['id']}/enroll")
    c.put("/api/settings", json={"email1_include_link": True, "per_run_cap": 50})
    run_day(c, T0, runs=2)
    box = c.get("/api/outbox").json()
    assert len(box) == 2 and all("/demo/" not in m["body"] and m["variant"] == "default" for m in box)   # the executive copy has no demo link


def test_footer_without_company_settings_and_with_address(tmp_path):
    c = make_client(tmp_path)
    lid = by_email(c)["anil@kappahealth.example"]["id"]
    c.post(f"/api/leads/{lid}/enroll")
    run_day(c, T0, runs=1)
    m = c.get("/api/outbox").json()[0]
    assert m["body"].rstrip().split("\n")[-1].startswith("Not relevant? Unsubscribe: ")   # no footer, no privacy line
    assert c.put("/api/settings", json={"privacy_url": "http://mcn.example.invalid/p"}).status_code == 400
    assert c.put("/api/settings", json={"privacy_url": "javascript:alert(1)"}).status_code == 400
    c.put("/api/settings", json={"sender_company": "MCN", "sender_address": "Plot 0, Sector 0, Noida 201300"})
    lid2 = by_email(c)["vikram@piinfra.example"]["id"]
    c.post(f"/api/leads/{lid2}/enroll")
    run_day(c, T0 + timedelta(hours=1), runs=1)
    m2 = next(m for m in c.get("/api/outbox").json() if m["lead_id"] == lid2)
    assert m2["body"].endswith("\nMCN · Plot 0, Sector 0, Noida 201300")


# ---------------- WhatsApp: unfilled placeholders are refused ----------------
def consent(c, lid):
    assert c.post(f"/api/leads/{lid}/consent", json={"text": "Yes on email, 3 Nov", "channel": "whatsapp"}).status_code == 200


def test_whatsapp_template_refuses_a_mail_merge_leftover(tmp_path):
    c = make_client(tmp_path)
    lead = by_email(c)["nikhil@mailmerge-leftover.example"]
    consent(c, lead["id"])
    for template in whatsapp.TEMPLATES:
        r = c.post(f"/api/leads/{lead['id']}/whatsapp", json={"template": template})
        if "{{company}}" in whatsapp.TEMPLATES[template]:
            assert r.status_code == 400 and "company" in r.json()["detail"]
    assert all("{{" not in m["body"] for m in c.get("/api/whatsapp/outbox").json())


def test_whatsapp_templates_render_cleanly_for_a_messy_lead(tmp_path):
    c = make_client(tmp_path)
    lead = by_email(c)["meena@gammait.example"]
    consent(c, lead["id"])
    for template in ("first_after_yes", "first_after_yes_hinglish"):
        r = c.post(f"/api/leads/{lead['id']}/whatsapp", json={"template": template})
        assert r.status_code == 200, r.text
    sent = c.get("/api/whatsapp/outbox").json()
    assert len(sent) == 2 and all(m["status"] == "dry_run" for m in sent)
    for m in sent:
        assert m["body"].startswith("Hi Meena,") and m["body"].endswith(whatsapp.OPT_OUT)
        assert "{{" not in m["body"] and "/demo/" in m["body"]


def test_whatsapp_custom_text_with_unknown_placeholder_is_refused(tmp_path):
    c = make_client(tmp_path)
    lead = by_email(c)["anita@omegasteel.example"]
    consent(c, lead["id"])
    c.post(f"/api/leads/{lead['id']}/reply", json={"text": "haan ji"})        # not a WhatsApp message: no window
    r = c.post(f"/api/leads/{lead['id']}/whatsapp", json={"template": "custom", "text": "Hi {{first_name}}"})
    assert r.status_code == 400 and "24 hours" in r.json()["detail"]
    store = c.app.state.store
    stamp = datetime.now(timezone.utc).isoformat()
    store.update_lead(lead["id"], lambda l: {**l, "whatsapp_last_inbound_at": stamp})
    r = c.post(f"/api/leads/{lead['id']}/whatsapp", json={"template": "custom", "text": "Hi {{first_name}}, {{salary_band}}?"})
    assert r.status_code == 400 and "salary_band" in r.json()["detail"]
    assert c.get("/api/whatsapp/outbox").json() == []


def test_whatsapp_refused_without_consent_and_for_closed_leads(tmp_path):
    c = make_client(tmp_path)
    leads = by_email(c)
    r = c.post(f"/api/leads/{leads['anil@kappahealth.example']['id']}/whatsapp", json={"template": "first_after_yes"})
    assert r.status_code == 400 and "consent" in r.json()["detail"].lower()
    closed = leads["arjun@sigmamotors.example"]["id"]
    consent(c, closed)
    c.post(f"/api/leads/{closed}/reply", json={"text": "Please remove me from your list"})
    r = c.post(f"/api/leads/{closed}/whatsapp", json={"template": "first_after_yes"})
    assert r.status_code == 400 and c.get("/api/whatsapp/outbox").json() == []


# ---------------- Templates page API and Stories ----------------
def test_templates_page_api_lists_every_variant_with_unsubscribe(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    seq = c.get("/api/sequence").json()
    assert [s["day"] for s in seq] == [0, 3, 7, 11, 15, 20]
    for step in seq:
        for key, text in step.items():
            if key.startswith("body"):
                assert "{{unsub_link}}" in text and "{{greeting}}" in text
            if key.endswith("no_role"):
                assert "{{role}}" not in text, (step["name"], key)
    wa = c.get("/api/whatsapp/templates").json()
    assert set(wa) == set(whatsapp.TEMPLATES) and all(t.endswith(whatsapp.OPT_OUT) for t in wa.values())
    assert all("{{unsub_link}}" not in t for t in wa.values())          # WhatsApp opt-out is 'Reply STOP'


def test_stories_permission_flag(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    story_row = lambda: next(r for r in c.get("/api/readiness").json()["items"] if r["id"] == "story")
    assert c.get("/api/stories").json() == [] and story_row()["status"] == "optional"
    s = c.post("/api/stories", json={"title": "<b>Acme</b> BPO", "industry": "BPO", "result": "Faster shortlists",
                                     "quote": "It helped", "permission": False}).json()
    assert s["permission"] is False and s["title"] == "<b>Acme</b> BPO" and len(s["id"]) == 8
    assert story_row()["status"] == "optional"                                   # a story without permission never counts
    c.post("/api/stories", json={"title": "Beta Retail", "permission": True})
    assert story_row()["status"] == "done" and len(c.get("/api/stories").json()) == 2
    assert c.post("/api/stories", json={"industry": "no title"}).status_code == 422
    # stories never reach the AI writer or the sequence copy
    joined = json.dumps(c.get("/api/sequence").json())
    assert "Acme" not in joined and "Beta Retail" not in joined
