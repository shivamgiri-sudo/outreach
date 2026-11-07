"""Owner rules: landlines are kept (calls only, never WhatsApp/SMS) and consent is per channel."""
from types import SimpleNamespace

import pytest

from app.channels.sms_send import send_sms
from app.channels.whatsapp_send import send_whatsapp
from app.contact_rules import LANDLINE_REASON, add_consent, consent_channels, has_consent_for, lead_is_landline
from tests.flow_helpers import T0, chain, enroll, lead, make_client, make_flow, node, patch_lead, runs, store_of, tick

LANDLINE_CSV = "company,contact_name,phone\nLandCo,Ravi Rao,0120-4567890\nNoStd,Sam Jain,4567890\n"


@pytest.fixture()
def c(tmp_path):
    return make_client(tmp_path)


def by_company(c, name):
    return next((l for l in c.get("/api/leads").json() if l["company"] == name), None)


# ---------------------------------------------------------------- landlines kept on import and edit
def test_quick_import_keeps_a_landline_with_std_code(c):
    r = c.post("/api/leads/import", json={"csv": LANDLINE_CSV, "source": "test"}).json()
    assert r["created"] == 1 and r["rejected"] == 1                      # no STD code: still left out
    land = by_company(c, "LandCo")
    assert land["phone"] == "+911204567890" and land["phone_type"] == "landline"
    assert by_company(c, "NoStd") is None


def test_smart_import_keeps_landline_and_warns_without_std(c):
    r = c.post("/api/import/commit", json={"csv": LANDLINE_CSV, "source": "test"}).json()
    assert r["summary"]["create"] == 2 and r["summary"]["warnings"] == {"landline_without_std": 1}
    assert by_company(c, "LandCo")["phone_type"] == "landline"
    assert any("has no STD (area) code" in w["text"] for w in r["warnings"])


def test_edit_phone_to_a_landline_is_saved(c):
    acme = lead(c)
    r = c.patch(f"/api/leads/{acme['id']}", json={"phone": "0120-4567890"})
    assert r.status_code == 200 and r.json()["phone"] == "+911204567890" and r.json()["phone_type"] == "landline"
    assert c.patch(f"/api/leads/{acme['id']}", json={"phone": "4567890"}).status_code == 400     # no STD code


def test_landline_only_lead_can_be_edited(c):
    c.post("/api/leads/import", json={"csv": LANDLINE_CSV, "source": "test"})
    land = by_company(c, "LandCo")
    r = c.patch(f"/api/leads/{land['id']}", json={"phone": "022 2345 6789"})
    assert r.status_code == 200 and r.json()["phone"] == "+912223456789" and r.json()["phone_type"] == "landline"


def test_lead_is_landline_reads_type_or_number():
    assert lead_is_landline({"phone": "+911204567890"}) and lead_is_landline({"phone": "+911204567890", "phone_type": "business"})
    assert not lead_is_landline({"phone": "+919876543210"}) and not lead_is_landline({"phone": "+442079460958"})
    assert not lead_is_landline({})


# ---------------------------------------------------------------- landlines never get WhatsApp / SMS
@pytest.mark.parametrize("sender", ["sms", "whatsapp"])
def test_low_level_senders_refuse_a_landline(c, sender):
    store = store_of(c)
    send = (lambda p: send_sms(store, p, "hi")) if sender == "sms" else (lambda p: send_whatsapp(store, p, "first_after_yes"))
    assert send("+911204567890") == {"status": "failed", "error": LANDLINE_REASON}
    assert send("+919876543210") == {"status": "dry_run"}


def test_one_off_whatsapp_to_a_landline_is_refused(c):
    acme = lead(c)["id"]
    c.post(f"/api/leads/{acme}/consent", json={"text": "Yes on WhatsApp please", "channel": "whatsapp"})
    patch_lead(c, acme, phone="+911204567890", phone_type="landline")
    r = c.post(f"/api/leads/{acme}/whatsapp", json={"template": "first_after_yes"})
    assert r.status_code == 400 and LANDLINE_REASON in r.json()["detail"]
    assert store_of(c).read("whatsapp_outbox") == []


@pytest.mark.parametrize("ntype,cfg", [("whatsapp", {"template": "first_after_yes"}), ("sms", {"text": "Hi {{first_name}}. Reply STOP to opt out."})])
def test_flow_skips_a_landline_with_a_clear_reason(c, ntype, cfg):
    acme = lead(c)["id"]
    c.post(f"/api/leads/{acme}/consent", json={"text": "Yes to WhatsApp", "channel": "whatsapp"})
    c.post(f"/api/leads/{acme}/consent", json={"text": "Yes to SMS", "channel": "sms"})
    patch_lead(c, acme, phone="+911204567890", phone_type="landline")
    flow = make_flow(c, [node("t", "trigger.manual"), node("msg", ntype, **cfg)], chain("t", "msg"))
    enroll(c, flow, acme)
    tick(c, T0)
    assert LANDLINE_REASON in runs(c, flow)[0]["stop_reason"]
    store = store_of(c)
    assert store.read("whatsapp_outbox") == [] and not [a for a in store.read("activities") if a["type"] == "sms"]


def test_flow_preview_names_the_landline(c):
    from app.flow_simulate import _simulate_action
    acme = {**lead(c), "phone": "+911204567890", "phone_type": "landline",
            "consent": {"text": "yes", "channel": "sms", "channels": ["whatsapp", "sms"]}}
    for ntype in ("whatsapp", "sms"):
        status, text, _ = _simulate_action(store_of(c), {"id": "f", "name": "F"}, node("n", ntype, template="first_after_yes", text="x"),
                                           acme, T0, "http://x")
        assert status == "error" and LANDLINE_REASON in text


# ---------------------------------------------------------------- consent per channel
def test_whatsapp_or_call_consent_does_not_let_sms_go_out(c):
    acme = lead(c)["id"]
    c.post(f"/api/leads/{acme}/consent", json={"text": "Yes on a call", "channel": "call"})
    c.post(f"/api/leads/{acme}/consent", json={"text": "Yes on WhatsApp", "channel": "whatsapp"})
    flow = make_flow(c, [node("t", "trigger.manual"), node("msg", "sms", text="Hi. Reply STOP to opt out.")], chain("t", "msg"))
    enroll(c, flow, acme)
    tick(c, T0)
    assert "no recorded SMS consent" in runs(c, flow)[0]["stop_reason"]
    assert not [a for a in store_of(c).read("activities") if a["type"] == "sms"]


def test_sms_consent_lets_sms_go_out_but_not_whatsapp(c):
    acme = lead(c)["id"]
    saved = c.post(f"/api/leads/{acme}/consent", json={"text": "Yes, text me", "channel": "sms"}).json()
    assert saved["consent"]["channels"] == ["sms"]
    r = c.post(f"/api/leads/{acme}/whatsapp", json={"template": "first_after_yes"})
    assert r.status_code == 400 and "WhatsApp consent" in r.json()["detail"]
    flow = make_flow(c, [node("t", "trigger.manual"), node("msg", "sms", text="Hi. Reply STOP to opt out.")], chain("t", "msg"))
    enroll(c, flow, acme)
    tick(c, T0)
    assert [a["outcome"] for a in store_of(c).read("activities") if a["type"] == "sms"] == ["dry_run"]


def test_consent_channels_add_up_and_bad_channel_is_refused(c):
    acme = lead(c)["id"]
    c.post(f"/api/leads/{acme}/consent", json={"text": "Yes on WhatsApp", "channel": "whatsapp"})
    saved = c.post(f"/api/leads/{acme}/consent", json={"text": "Yes to SMS too", "channel": "sms"}).json()
    assert saved["consent"]["channels"] == ["whatsapp", "sms"] and saved["consent"]["channel"] == "sms"
    assert c.post(f"/api/leads/{acme}/consent", json={"text": "Yes", "channel": "fax"}).status_code == 400
    ledger = c.get(f"/api/compliance/consent-ledger?lead_id={acme}").json()
    rows = ledger if isinstance(ledger, list) else ledger.get("rows") or ledger.get("items") or []
    assert {row["channel"] for row in rows if row.get("event") == "given"} >= {"whatsapp", "sms"}


def test_stored_records_keep_working():
    assert consent_channels({"consent": {"text": "y", "channel": "whatsapp"}}) == {"whatsapp"}      # old record, one channel
    assert not has_consent_for({"consent": {"text": "y", "channel": "whatsapp"}}, "sms")
    assert not has_consent_for({"consent": {"text": "y", "channel": "call"}}, "sms")
    old_any = {"consent": {"text": "y", "channel": "any"}}
    assert has_consent_for(old_any, "whatsapp") and not has_consent_for(old_any, "sms")
    assert consent_channels({"consent": None, "inbound_consent": {"email": {}, "call": {}}}) == {"email", "call"}
    assert add_consent({"consent": {"text": "y", "channel": "whatsapp"}}, {"text": "z", "channel": "sms"})["channels"] == ["whatsapp", "sms"]
    assert add_consent({}, {"text": "z", "channel": "linkedin"})["channels"] == []


def test_call_yes_on_the_call_desk_is_call_consent_only(c):
    acme = lead(c)["id"]
    r = c.post(f"/api/leads/{acme}/call-outcome", json={"outcome": "interested", "notes": "wants a demo"})
    assert r.status_code == 200, r.text
    saved = next(l for l in store_of(c).read("leads") if l["id"] == acme)
    assert consent_channels(saved) == {"call"}


def test_email_yes_reply_is_email_consent_only(c):
    from app.inbound import apply_reply
    acme = lead(c)["id"]
    patch_lead(c, acme, stage="contacted")
    apply_reply(store_of(c), acme, "Yes, interested. Please share details.", channel="email")
    saved = next(l for l in store_of(c).read("leads") if l["id"] == acme)
    assert saved["consent"] and consent_channels(saved) == {"email"}
    assert not has_consent_for(saved, "whatsapp") and not has_consent_for(saved, "sms")


def test_email_yes_that_names_whatsapp_or_call_is_consent_for_it(c):
    from app.inbound import apply_reply
    acme = lead(c)["id"]
    patch_lead(c, acme, stage="contacted")
    apply_reply(store_of(c), acme, "Yes interested, please WhatsApp me on 9876543210 or call me after 4.", channel="email")
    saved = next(l for l in store_of(c).read("leads") if l["id"] == acme)
    assert consent_channels(saved) == {"email", "whatsapp", "call"}
    assert not has_consent_for(saved, "sms")


@pytest.mark.parametrize("text,expected", [
    ("Sounds good, no WhatsApp please, email only", set()),
    ("Yes. Don't call me, just send a text", {"sms"}),
    ("Interested! Happy to take a call on Monday", {"call"}),
    ("Yes, interested", set()),
])
def test_channels_named_in_reply_ignores_negations(text, expected):
    from app.contact_rules import channels_named_in_reply
    assert channels_named_in_reply(text) == expected
