"""QA: WhatsApp sending - consent, practice vs live, template variable order, 24-hour window, provider errors.

Payload shapes come from the providers' own docs (see docs/qa/whatsapp-sms-calls.md). No network: every provider
call goes through FakeHTTP (tests/qa/whatsapp_sms_calls_support.py).
"""
import json
from datetime import timedelta

import pytest

from app import whatsapp
from app.whatsapp import OPT_OUT, TEMPLATES, WhatsAppError
from tests.channel_fakes import FakeHTTP
from tests.qa.whatsapp_sms_calls_support import *  # noqa: F401,F403  (fixtures + helpers, incl. autouse no_network)
from tests.qa.whatsapp_sms_calls_support import (FIX, HOST, OK_REPLY, WA_PROVIDERS, Clock, client, connect_whatsapp, consent,
                                                 error_body, fresh, http_error, import_fixture_leads, lead_of, meta_message,
                                                 meta_post, phone, utc, webhook_path)

BASE = "http://localhost:8000"


@pytest.fixture
def c(tmp_path):
    cl = client(tmp_path)
    import_fixture_leads(cl)
    return cl


def store_of(c):
    return c.app.state.store


def test_messy_import_normalises_every_phone_format(c):
    leads = c.get("/api/leads").json()
    assert len(leads) == 122
    # +91 / 0 / spaces / dashes / (+91) / bare 10 digits all become +91XXXXXXXXXX
    for n in (1, 2, 3, 4, 5, 6):
        assert lead_of(c, n)["phone"] == phone(n)
    assert all(l["phone"].startswith("+91") and len(l["phone"]) == 13 for l in leads if l["phone"])


def test_no_consent_is_refused_and_nothing_is_queued(c, no_network):
    connect_whatsapp(c, "meta_cloud")
    lead = lead_of(c, 1)
    r = c.post(f"/api/leads/{lead['id']}/whatsapp", json={"template": "first_after_yes"})
    assert r.status_code == 400 and "consent" in r.json()["detail"].lower()
    assert c.get("/api/whatsapp/outbox").json() == [] and no_network.calls == []


def test_withdrawn_consent_by_whatsapp_stop_is_refused(c, no_network):
    connect_whatsapp(c, "meta_cloud")
    no_network.routes = {"graph.facebook.com": OK_REPLY["meta_cloud"]}
    lead = lead_of(c, 4)
    consent(c, lead["id"])
    assert c.post(f"/api/leads/{lead['id']}/whatsapp", json={"template": "first_after_yes"}).status_code == 200
    path, _ = webhook_path(c, "whatsapp", "meta_cloud")
    assert meta_post(c, meta_message(phone(4), "STOP", "wamid.QA.STOP.4"), path).status_code == 200
    after = fresh(c, lead["id"])
    assert after["stage"] == "unsubscribed" and after["consent"] is None
    sent_before = len(no_network.calls)
    r = c.post(f"/api/leads/{lead['id']}/whatsapp", json={"template": "pilot_offer"})
    assert r.status_code == 400 and len(no_network.calls) == sent_before
    # consent cannot simply be re-applied by a later automatic "haan" either: the lead stays closed
    meta_post(c, meta_message(phone(4), "haan ji bhej do", "wamid.QA.HAAN.4"), path)
    assert fresh(c, lead["id"])["stage"] == "unsubscribed" and fresh(c, lead["id"])["consent"] is None
    events = [r["event"] for r in c.get(f"/api/compliance/consent-ledger?lead_id={lead['id']}").json()]
    assert events.count("withdrawn") == 1


def test_practice_mode_by_default_never_calls_a_provider(c, no_network):
    lead = lead_of(c, 2)
    consent(c, lead["id"])
    r = c.post(f"/api/leads/{lead['id']}/whatsapp", json={"template": "first_after_yes"})
    assert r.status_code == 200          # a consent noted by a person does not by itself move the stage
    row = c.get("/api/whatsapp/outbox").json()[0]
    assert row["status"] == "dry_run" and row["body"].endswith(OPT_OUT) and no_network.calls == []
    # provider saved but "WhatsApp for real" off: still practice
    connect_whatsapp(c, "gupshup", live=False)
    c.post(f"/api/leads/{lead['id']}/whatsapp", json={"template": "pilot_offer"})
    assert c.get("/api/whatsapp/outbox").json()[0]["status"] == "dry_run" and no_network.calls == []


def _sent_params(pid: str, call: dict) -> list:
    if pid == "meta_cloud":
        return [p["text"] for p in call["body"]["template"]["components"][0]["parameters"]]
    if pid == "gupshup":
        return json.loads(call["form"]["template"])["params"]
    if pid == "interakt":
        return call["body"]["template"]["bodyValues"]
    if pid == "wati":
        return [p["value"] for p in sorted(call["body"]["parameters"], key=lambda p: int(p["name"]))]
    return call["body"]["templateParams"]


@pytest.mark.parametrize("pid", list(WA_PROVIDERS))
def test_live_template_send_uses_documented_payload_and_variable_order(c, no_network, pid):
    connect_whatsapp(c, pid)
    no_network.routes = {HOST[pid]: OK_REPLY[pid]}
    lead = lead_of(c, 1)            # "Priyanka SHARMA", "Annapurna Foods 001 Private Limited"
    consent(c, lead["id"])
    r = c.post(f"/api/leads/{lead['id']}/whatsapp", json={"template": "first_after_yes"})
    assert r.status_code == 200, r.text
    call = no_network.last(HOST[pid])
    first, sender, company, demo = _sent_params(pid, call)
    assert first == "Priyanka"                         # {{1}} cleaned first name, not ALL CAPS surname
    assert sender == c.get("/api/settings").json().get("sender_name", sender)   # {{2}}
    assert "Annapurna Foods" in company and "Private Limited" not in company     # {{3}} cleaned company
    assert demo.startswith(f"{BASE}/demo/")            # {{4}} demo link
    row = c.get("/api/whatsapp/outbox").json()[0]
    assert row["status"] == "sent" and row["provider_id"]
    if pid == "meta_cloud":
        assert call["body"]["to"] == "919000010001" and call["body"]["type"] == "template"
        assert call["headers"]["Authorization"] == "Bearer EAAqa-token" and "/100000000000002/messages" in call["url"]
    if pid == "gupshup":
        assert call["form"]["destination"] == "919000010001" and call["headers"]["apikey"] == "gs-qa-key"
    if pid == "interakt":
        assert call["body"]["countryCode"] == "+91" and call["body"]["phoneNumber"] == "9000010001"
    if pid == "wati":
        assert "whatsappNumber=919000010001" in call["url"] and call["headers"]["Authorization"] == "Bearer wati-qa-token"
    if pid == "aisensy":
        assert call["body"]["destination"] == "919000010001" and call["body"]["campaignName"] == "first_after_yes"


def test_hinglish_templates_render_with_opt_out_and_hinglish_names(c, no_network):
    lead = lead_of(c, 5)
    consent(c, lead["id"])
    for name in [t for t in TEMPLATES if t.endswith("_hinglish")]:
        assert c.post(f"/api/leads/{lead['id']}/whatsapp", json={"template": name}).status_code == 200
    bodies = [r["body"] for r in c.get("/api/whatsapp/outbox").json()]
    assert len(bodies) == 4
    for body in bodies:
        assert "BookMyInterview.ai se" in body and body.endswith(OPT_OUT) and "{{" not in body
    assert all(t.endswith(OPT_OUT) for t in TEMPLATES.values())


def test_custom_text_needs_a_whatsapp_message_not_an_email_reply(c, monkeypatch):
    store = store_of(c)
    clock = Clock(monkeypatch, utc(2026, 11, 3, 6, 0))
    lead = lead_of(c, 7)
    # an email "yes" records consent but must NOT open the 24-hour window
    from app.inbound import apply_reply
    apply_reply(store, lead["id"], "Yes, send me the details", "email")
    assert fresh(c, lead["id"])["consent"]
    with pytest.raises(WhatsAppError, match="24 hours"):
        whatsapp.send(store, lead["id"], "custom", "Hi, quick question", clock.now, BASE)
    # their WhatsApp message opens it ...
    apply_reply(store, lead["id"], "Haan, call me", "whatsapp")
    out = whatsapp.send(store, lead["id"], "custom", "Thanks! Calling you at 4.", clock.now + timedelta(hours=23), BASE)
    assert out["stage"] in ("whatsapp_active", "interested")
    # ... and it closes 24 hours later (multi-day time travel: day 2, day 3)
    for hours in (24, 49):
        with pytest.raises(WhatsAppError, match="24 hours"):
            whatsapp.send(store, lead["id"], "custom", "Following up", clock.now + timedelta(hours=hours, minutes=1), BASE)
    # approved templates still work outside the window
    whatsapp.send(store, lead["id"], "nudge_demo_not_opened", "", clock.now + timedelta(hours=49), BASE)


ERRORS = [("meta_cloud", "meta_131026_undeliverable", "131026"), ("meta_cloud", "meta_132001_translation", "132001"),
          ("meta_cloud", "meta_131047_reengagement", "131047"), ("meta_cloud", "meta_190_token_expired", "key or token"),
          ("gupshup", "gupshup_invalid_destination", "Invalid Destination"),
          ("interakt", "interakt_template_missing", "Template not found"),
          ("wati", "wati_invalid_number", "Invalid WhatsApp number"), ("aisensy", "aisensy_campaign_not_live", "not live")]


@pytest.mark.parametrize("pid,name,needle", ERRORS)
def test_provider_errors_are_shown_logged_and_do_not_advance_the_lead(c, no_network, pid, name, needle):
    connect_whatsapp(c, pid)
    reply = http_error(name) if load_status(name) >= 400 else error_body(name)
    no_network.routes = {HOST[pid]: reply}
    lead = lead_of(c, 10)
    consent(c, lead["id"])
    before = fresh(c, lead["id"])["stage"]
    r = c.post(f"/api/leads/{lead['id']}/whatsapp", json={"template": "first_after_yes"})
    assert r.status_code == 400 and needle in r.json()["detail"], r.text
    row = c.get("/api/whatsapp/outbox").json()[0]
    assert row["status"] == "failed" and needle in row["error"]
    assert fresh(c, lead["id"])["stage"] == before


def load_status(name: str) -> int:
    return json.loads((FIX / "provider_errors.json").read_text(encoding="utf-8"))[name]["status"]


def test_aisensy_free_text_is_refused_with_a_reason(c, no_network, monkeypatch):
    connect_whatsapp(c, "aisensy")
    store = store_of(c)
    clock = Clock(monkeypatch, utc(2026, 11, 4, 5, 0))
    lead = lead_of(c, 11)
    consent(c, lead["id"])
    from app.inbound import apply_reply
    apply_reply(store, lead["id"], "haan bolo", "whatsapp")
    with pytest.raises(WhatsAppError, match="templates"):
        whatsapp.send(store, lead["id"], "custom", "Hi", clock.now, BASE)
    assert no_network.calls == []


def test_unknown_template_and_missing_phone_are_refused(c):
    store = store_of(c)
    no_phone = next(l for l in c.get("/api/leads").json() if not l["phone"])
    consent(c, no_phone["id"])
    assert "phone" in c.post(f"/api/leads/{no_phone['id']}/whatsapp", json={"template": "pilot_offer"}).json()["detail"]
    lead = lead_of(c, 12)
    consent(c, lead["id"])
    assert c.post(f"/api/leads/{lead['id']}/whatsapp", json={"template": "bmi_promo"}).json()["detail"] == "Unknown template"
    assert store.read("whatsapp_outbox") == []
