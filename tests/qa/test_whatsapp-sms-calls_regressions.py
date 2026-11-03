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
