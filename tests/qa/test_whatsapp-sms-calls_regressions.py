"""QA regressions for bugs found in WhatsApp / SMS handling (one test group per fix commit)."""
import pytest

from app.replies import classify_reply
from tests.qa.whatsapp_sms_calls_support import *  # noqa: F401,F403  (autouse no_network)


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
