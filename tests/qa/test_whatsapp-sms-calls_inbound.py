"""QA: inbound WhatsApp / SMS webhooks - Meta verify + signature, reply classification -> stage / tasks / consent.

Payloads in tests/qa/fixtures/whatsapp-sms-calls/ follow each provider's documented webhook shape (sources in the
fixture files' "_sources" and in docs/qa/whatsapp-sms-calls.md).
"""
import json
from urllib.parse import urlencode

import pytest

from tests.qa.whatsapp_sms_calls_support import *  # noqa: F401,F403  (autouse no_network)
from tests.qa.whatsapp_sms_calls_support import (META_APP_SECRET, WA_PROVIDERS, client, connect_whatsapp, fresh,
                                                 import_fixture_leads, lead_of, load, meta_post, phone, store_connect,
                                                 tasks_for, webhook_path)


@pytest.fixture
def c(tmp_path):
    cl = client(tmp_path)
    import_fixture_leads(cl)
    return cl


def test_meta_get_verification_echoes_challenge_only_for_the_right_token(c):
    connect_whatsapp(c, "meta_cloud", live=False)
    path, secret = webhook_path(c, "whatsapp", "meta_cloud")
    bare = path.split("?", 1)[0]
    ok = c.get(bare + "?" + urlencode({"hub.mode": "subscribe", "hub.verify_token": secret, "hub.challenge": "1158201444"}))
    assert ok.status_code == 200 and ok.text == "1158201444"
    for query in ({"hub.mode": "subscribe", "hub.verify_token": "guess", "hub.challenge": "1"},
                  {"hub.mode": "unsubscribe", "hub.verify_token": secret, "hub.challenge": "1"}):
        assert c.get(bare + "?" + urlencode(query)).status_code in (401, 403)


def test_meta_post_needs_a_valid_x_hub_signature_256(c):
    connect_whatsapp(c, "meta_cloud", live=False)
    path, secret = webhook_path(c, "whatsapp", "meta_cloud")
    payload = load("meta_webhooks.json")["batch_day1"]
    body = json.dumps(payload).encode()
    # with an app secret saved, the ?token= in the URL alone is not enough: only the signature counts
    assert c.post(path, content=body, headers={"Content-Type": "application/json"}).status_code == 401
    assert meta_post(c, payload, path, secret="wrong-secret").status_code == 401
    lead = lead_of(c, 4)
    assert fresh(c, lead["id"])["stage"] == "new"              # nothing was processed
    assert meta_post(c, payload, path, secret=META_APP_SECRET).status_code == 200


def test_meta_batch_classifies_every_reply_type(c):
    connect_whatsapp(c, "meta_cloud", live=False)
    store = c.app.state.store
    path, _ = webhook_path(c, "whatsapp", "meta_cloud")
    r = meta_post(c, load("meta_webhooks.json")["batch_day1"], path)
    assert r.json() == {"ok": True, "messages": 8, "matched": 7}
    ids = {n: lead_of(c, n)["id"] for n in range(1, 8)}
    # 1: "Haan ji, sample bhej dijiye" -> interested, consent recorded from the WhatsApp reply, call task for Priya
    one = fresh(c, ids[1])
    assert one["stage"] == "interested" and one["consent"]["source"] == "whatsapp reply" and one["whatsapp_last_inbound_at"]
    call = tasks_for(store, ids[1], "call")
    assert len(call) == 1 and call[0]["owner"] == "Priya" and call[0]["priority"] == "high"
    # 2: Meta's marketing opt-out quick reply "Stop promotions" -> unsubscribed
    assert fresh(c, ids[2])["stage"] == "unsubscribed"
    # 3: interactive button "Yes, call me" -> interested
    assert fresh(c, ids[3])["stage"] == "interested" and tasks_for(store, ids[3], "call")
    # 4: "STOP" and 5: "Band karo bhai, roz roz message mat bhejo" -> unsubscribed, consent withdrawn in the ledger
    for n in (4, 5):
        lead = fresh(c, ids[n])
        assert lead["stage"] == "unsubscribed" and lead["consent"] is None and lead["consent_withdrawn_at"]
        assert not tasks_for(store, ids[n], "call")
    ledger = store.read("consent_ledger")
    assert {r["lead_id"] for r in ledger if r["event"] == "withdrawn"} >= {ids[2], ids[4], ids[5]}
    # 6: an image with no caption -> a person looks; 7: "Aapko mera number kahan se mila?" -> question task
    assert fresh(c, ids[6])["stage"] == "engaged" and tasks_for(store, ids[6], "other")
    assert tasks_for(store, ids[7], "question")
    # unknown number -> unmatched reply task, nothing else touched
    assert any(t["kind"] == "unmatched_reply" and "919000019999" in t["text"].replace("+", "") for t in store.read("tasks"))


def test_meta_redelivery_and_status_callbacks_change_nothing(c):
    connect_whatsapp(c, "meta_cloud", live=False)
    store = c.app.state.store
    path, _ = webhook_path(c, "whatsapp", "meta_cloud")
    hooks = load("meta_webhooks.json")
    meta_post(c, hooks["batch_day1"], path)
    snapshot = (len(store.read("tasks")), len(store.read("inbox")), len(store.read("consent_ledger")))
    assert meta_post(c, hooks["batch_day1"], path).json()["matched"] == 0         # Meta retry: same wamids
    assert meta_post(c, hooks["statuses_mixed"], path).json() == {"ok": True, "messages": 0, "matched": 0}
    assert (len(store.read("tasks")), len(store.read("inbox")), len(store.read("consent_ledger"))) == snapshot
    one = fresh(c, lead_of(c, 1)["id"])
    assert one["engagement"]["replies"] == 1
    # unsupported message type with an errors[] block: logged for a person, not treated as a yes or a no
    meta_post(c, hooks["unsupported_and_system_error"], path)
    thirteen = fresh(c, lead_of(c, 13)["id"])
    assert thirteen["stage"] == "engaged" and thirteen["consent"] is None


def test_closed_leads_stay_closed_on_a_whatsapp_yes(c):
    connect_whatsapp(c, "meta_cloud", live=False)
    store = c.app.state.store
    path, _ = webhook_path(c, "whatsapp", "meta_cloud")
    from tests.qa.whatsapp_sms_calls_support import meta_message
    cust = lead_of(c, 14)
    store.update_lead(cust["id"], lambda l: {**l, "stage": "customer"})
    meta_post(c, meta_message(phone(14), "Yes interested in one more role", "wamid.QA.C14"), path)
    after = fresh(c, cust["id"])
    assert after["stage"] == "customer" and not after.get("consent")
    assert tasks_for(store, cust["id"], "review")
    dq = lead_of(c, 15)
    store.update_lead(dq["id"], lambda l: {**l, "stage": "disqualified"})
    meta_post(c, meta_message(phone(15), "haan bhej do", "wamid.QA.D15"), path)
    assert fresh(c, dq["id"])["stage"] == "disqualified" and not fresh(c, dq["id"]).get("consent")


PROVIDER_CASES = [  # (channel, provider, fixture, lead number, expected stage)
    ("whatsapp", "gupshup", "gupshup_text", 21, "interested"),
    ("whatsapp", "gupshup", "gupshup_quick_reply_stop", 22, "unsubscribed"),
    ("whatsapp", "interakt", "interakt_text", 23, "disqualified"),          # "Nahi chahiye, thanks"
    ("whatsapp", "wati", "wati_text", 24, "interested"),
    ("whatsapp", "wati", "wati_button_stop", 25, "unsubscribed"),          # buttonReply "Stop promotions"
    ("whatsapp", "wati", "wati_list_reply", 26, "not_now"),                # listReply "Not now, next month"
    ("whatsapp", "aisensy", "aisensy_text", 28, "unsubscribed"),           # "mat bhejo ab"
    ("sms", "twilio_sms", "twilio_sms_form", 29, "unsubscribed"),          # Twilio opt-out keyword STOPALL
    ("sms", "msg91", "msg91_inbound", 30, "unsubscribed"),                 # "BMIHRS band karo"
]
SMS_VALUES = {"twilio_sms": {"account_sid": "ACQA000000000000000000000000000000", "auth_token": "tw-qa", "from_number": "+14155550100"},
              "msg91": {"authkey": "msg91-qa", "template_id": "6650f0d1d6fc05qa"}}


@pytest.mark.parametrize("ch,pid,name,n,stage", PROVIDER_CASES)
def test_provider_inbound_payloads_move_the_right_lead(c, ch, pid, name, n, stage):
    store = c.app.state.store
    store_connect(store, ch, pid, WA_PROVIDERS.get(pid) or SMS_VALUES[pid])
    path, _ = webhook_path(c, ch, pid)
    payload = load("provider_inbound.json")[name]
    if pid == "twilio_sms":
        r = c.post(path, content=urlencode(payload), headers={"Content-Type": "application/x-www-form-urlencoded"})
    else:
        r = c.post(path, json=payload)
    assert r.status_code == 200 and r.json()["matched"] == 1, (r.status_code, r.text)
    lead = fresh(c, lead_of(c, n)["id"])
    assert lead["stage"] == stage
    if stage == "unsubscribed":
        assert lead["consent"] is None
    if ch == "sms":
        assert not lead.get("whatsapp_last_inbound_at")         # an SMS never opens the WhatsApp window
    else:
        assert lead["whatsapp_last_inbound_at"]


def test_events_and_our_own_echoes_are_not_replies(c):
    store = c.app.state.store
    for pid in ("gupshup", "wati"):
        store_connect(store, "whatsapp", pid, WA_PROVIDERS[pid])
    inbound = load("provider_inbound.json")
    for pid, name in (("gupshup", "gupshup_message_event"), ("wati", "wati_sent_by_us")):
        path, _ = webhook_path(c, "whatsapp", pid)
        assert c.post(path, json=inbound[name]).json()["messages"] == 0
    assert store.read("inbox") == [] and lead_of(c, 27)["stage"] == "new"


def test_webhook_without_the_secret_is_rejected(c):
    store = c.app.state.store
    store_connect(store, "whatsapp", "gupshup", WA_PROVIDERS["gupshup"])
    path, _ = webhook_path(c, "whatsapp", "gupshup")
    bare = path.split("?", 1)[0]
    assert c.post(bare, json=load("provider_inbound.json")["gupshup_quick_reply_stop"]).status_code == 401
    assert c.post(bare + "?token=nope", json={}).status_code == 401
    assert lead_of(c, 22)["stage"] == "new"
