"""QA regressions for bugs found in WhatsApp / SMS handling (one test group per fix commit)."""
from datetime import timedelta

import pytest

from app import whatsapp
from app.replies import classify_reply
from app.whatsapp import OPT_OUT, WhatsAppError
from tests.qa.whatsapp_sms_calls_support import *  # noqa: F401,F403  (autouse no_network)
from tests.qa.whatsapp_sms_calls_support import Clock, client, consent, import_fixture_leads, lead_of, load, meta_message, phone, utc

BASE = "http://localhost:8000"


@pytest.fixture
def c(tmp_path):
    cl = client(tmp_path)
    import_fixture_leads(cl)
    return cl


def store_of(c):
    return c.app.state.store


@pytest.mark.parametrize("text", ["Stop promotions", "STOP PROMOTIONS", "Stop promotion", "STOPALL", "END", "quit", " Quit. "])
def test_platform_opt_out_keywords_unsubscribe(text):
    """Meta's marketing opt-out quick reply and Twilio's standard opt-out keywords were read as 'other'."""
    assert classify_reply(text) == "unsubscribe"


@pytest.mark.parametrize("text,label", [("The end of month works, send it", "interested"), ("Weekend is fine", "other"),
                                        ("We will quit the old ATS soon, tell me more", "interested")])
def test_new_keywords_only_match_as_the_whole_message(text, label):
    assert classify_reply(text) == label


def test_wati_button_and_list_replies_are_read():
    """WATI sends quick-reply taps as buttonReply {id, title} with no "text"; they were dropped, so a STOP tap was lost."""
    from app.channels.inbound_parse import whatsapp_webhook
    inbound = load("provider_inbound.json")
    [stop] = whatsapp_webhook("wati", inbound["wati_button_stop"])
    assert stop["text"] == "Stop promotions" and stop["from_phone"] == "+919000010025"
    assert stop["message_id"] == "wamid.QA.WATI.002"
    [later] = whatsapp_webhook("wati", inbound["wati_list_reply"])
    assert later["text"] == "Not now, next month"
    assert whatsapp_webhook("wati", {**inbound["wati_button_stop"], "buttonReply": {"payload": "x", "text": "STOP"}})[0]["text"] == "STOP"
    assert whatsapp_webhook("wati", inbound["wati_sent_by_us"]) == []          # our own echo still ignored
    assert whatsapp_webhook("wati", {**inbound["wati_button_stop"], "buttonReply": None}) == []


def test_custom_free_text_carries_the_opt_out_line(c, monkeypatch):
    """Regression: free text inside the 24-hour window went out without any opt-out (PRD 4.4 / DPDP)."""
    store = store_of(c)
    clock = Clock(monkeypatch, utc(2026, 11, 3, 6, 0))
    lead = lead_of(c, 8)
    consent(c, lead["id"])
    from app.inbound import apply_reply
    apply_reply(store, lead["id"], "kab call karoge?", "whatsapp")
    whatsapp.send(store, lead["id"], "custom", "Aaj 4 baje call karti hoon.", clock.now, BASE)
    body = store.read("whatsapp_outbox")[-1]["body"]
    assert body.startswith("Aaj 4 baje") and body.endswith(OPT_OUT) and body.count(OPT_OUT) == 1
    # already written by the person: not doubled
    whatsapp.send(store, lead["id"], "custom", "Theek hai. " + OPT_OUT, clock.now, BASE)
    assert store.read("whatsapp_outbox")[-1]["body"].count(OPT_OUT) == 1


def test_window_starts_at_the_message_time_not_when_a_late_webhook_arrives(c, monkeypatch):
    """Regression: Meta retries webhooks for days; a retried message must not reopen a window that already closed."""
    store = store_of(c)
    lead = lead_of(c, 9)
    consent(c, lead["id"])
    sent_at = utc(2026, 11, 3, 6, 0)
    clock = Clock(monkeypatch, sent_at + timedelta(hours=30))      # our server was down; the retry lands 30 h later
    from app.inbound import process_inbound_message
    from app.channels.inbound_parse import whatsapp_webhook
    msg = whatsapp_webhook("meta_cloud", meta_message(phone(9), "ok, tell me more", "wamid.QA.LATE.9",
                                                      ts=int(sent_at.timestamp())))[0]
    assert process_inbound_message(store, msg)["matched"]
    with pytest.raises(WhatsAppError, match="24 hours"):
        whatsapp.send(store, lead["id"], "custom", "Hi again", clock.now, BASE)
    # a fresh message (timestamp = now) opens it as usual
    msg = whatsapp_webhook("meta_cloud", meta_message(phone(9), "hello?", "wamid.QA.NOW.9", ts=int(clock.now.timestamp())))[0]
    process_inbound_message(store, msg)
    whatsapp.send(store, lead["id"], "custom", "Hi again", clock.now + timedelta(minutes=5), BASE)
