"""QA: the "Test connection" button on every Integrations hub card, with faked providers.

Three worlds per card: the provider answers like its docs say (success), it refuses the key (HTTP 401 -> PermissionError,
SMTP 535, IMAP AUTHENTICATIONFAILED), and the network is down (OSError). A test never answers 500, never leaks a secret,
and records its result on the card (status "error" after a failure, back to "configured" after a pass).
"""
import pytest

from app.channels import config
from tests.channel_fakes import FakeHTTP, FakeIMAP, SMTPFactory, raw_email
from tests.qa.integrations_hub_support import (HUB_CARDS, card, channel_view, make, save, secret_values, store_of,
                                               values_for)

# What each provider says when it worked (shapes from the providers' API docs; fake ids).
SUCCESS = {
    "fast2sms.com": {"return": True, "request_id": "f2s-qa-0001", "wallet": "412.50"},
    "msg91.com": {"type": "success", "message": "3a6b7c8d9e0f-qa"},
    "gupshup.io": {"status": "submitted", "messageId": "gs-qa-0001"},
    "zoom.us/oauth": {"access_token": "zoom-qa-access-token", "token_type": "bearer", "expires_in": 3599},
    "api.meetings.example": {"created": True, "meeting": {"id": 8812, "join_url": "https://meet.meetings.example/j/8812"}},
    "api.cloudcall.example": {"ok": True, "call": {"uuid": "c-qa-1"}},
    "api.mailer.example": {"status": "QUEUED", "data": {"id": "m-qa-1"}},
    "api.sms-gateway.example": {"status": "success", "data": [{"id": "s-qa-1"}]},
    "bsp.whatsapp-partner.example": {"messages": [{"id": "wamid.qa1", "message_status": "accepted"}]},
}
PASSIVE = {("calendar", "booking_link"), ("calls", "click_to_call")} | {
    (c, p) for c, p in HUB_CARDS if c in ("inbound_email", "email_events") and p not in ("imap", "custom_webhook")}
NOT_CONNECTED = {("calendar", "google"), ("calendar", "microsoft")}
SMTP_CARDS = {("email_send", "gmail_smtp"), ("email_send", "ses_smtp"), ("email_send", "smtp")}
CUSTOM_WEBHOOK = {(c, p) for c, p in HUB_CARDS if p == "custom_webhook"}
ACTIVE_TEST = [k for k in HUB_CARDS if k not in PASSIVE | NOT_CONNECTED | CUSTOM_WEBHOOK]


def body_for(channel):
    if channel == "email_send":
        return {"to": "qa.owner@hirewise-outreach.example"}
    if channel == "whatsapp":
        return {"to": "+91 90000 12345", "template": "first_after_yes", "variables": ["Rohit"]}
    if channel in ("sms", "calls"):
        return {"to": "+91 90000 12345"}
    return {}


def client(tmp_path, world):
    http = {"ok": FakeHTTP(dict(SUCCESS)), "auth": FakeHTTP({"": PermissionError("HTTP 401")}),
            "net": FakeHTTP({"": OSError("timed out")})}[world]
    imap_password = values_for("inbound_email", "imap")["password"] if world == "ok" else "not-the-password"
    c = make(tmp_path, http, smtp_factory=SMTPFactory(fail_login=world != "ok"),
             imap_factory=lambda host, port: FakeIMAP({1: raw_email(1)}, password=imap_password))
    return c, http


@pytest.mark.parametrize("world", ["ok", "auth", "net"])
@pytest.mark.parametrize("channel,pid", ACTIVE_TEST)
def test_card_test_button_in_each_world(tmp_path, channel, pid, world):
    c, http = client(tmp_path, world)
    values = values_for(channel, pid)
    assert save(c, channel, pid, values).status_code == 200
    r = c.post(f"/api/channels/{channel}/{pid}/test", json=body_for(channel))
    assert r.status_code == 200, r.text
    result = r.json()
    assert isinstance(result["ok"], bool) and result["message"]
    assert not [s for s in secret_values(channel, pid, values) if s in r.text], "a secret leaked into the test result"
    assert result["last_test"]["ok"] is result["ok"] and result["last_test"]["at"]
    if world == "ok":
        assert result["ok"] is True, result["message"]
        assert card(c, channel, pid)["status"] == "configured"
    else:
        assert result["ok"] is False, result["message"]
        assert card(c, channel, pid)["status"] == "error"
        assert card(c, channel, pid)["last_test"]["message"] == result["message"][:300]
    if (channel, pid) not in SMTP_CARDS | {("inbound_email", "imap")}:
        assert http.calls, "the test should reach the (fake) provider"
        assert all(call["url"].startswith("https://") for call in http.calls)


@pytest.mark.parametrize("channel,pid", sorted(PASSIVE))
def test_passive_cards_check_settings_without_calling_anyone(tmp_path, channel, pid):
    c, http = client(tmp_path, "net")
    unsaved = c.post(f"/api/channels/{channel}/{pid}/test", json={}).json()
    assert unsaved["ok"] is False and unsaved["last_test"]["ok"] is False
    assert card(c, channel, pid)["last_test"] is None                              # nothing recorded on an unsaved card
    save(c, channel, pid)
    r = c.post(f"/api/channels/{channel}/{pid}/test", json={}).json()
    assert r["ok"] is True and http.calls == []


@pytest.mark.parametrize("channel,pid", sorted(NOT_CONNECTED))
def test_oauth_calendar_test_says_connect_first(tmp_path, channel, pid):
    c, http = client(tmp_path, "ok")
    save(c, channel, pid)
    r = c.post(f"/api/channels/{channel}/{pid}/test", json={}).json()
    assert r["ok"] is False and "Connect" in r["message"] and http.calls == []


@pytest.mark.parametrize("channel,pid", ACTIVE_TEST)
def test_test_before_saving_asks_to_save_first(tmp_path, channel, pid):
    c, http = client(tmp_path, "ok")
    r = c.post(f"/api/channels/{channel}/{pid}/test", json=body_for(channel)).json()
    assert r == {"ok": False, "message": "Save all the required details first.",
                 "last_test": {**r["last_test"], "ok": False}}
    assert http.calls == [] and config.config_for(store_of(c), channel, pid) is None


def test_failed_then_fixed_key_goes_error_then_connected_over_two_days(tmp_path):
    """Day 1 the MSG91 key is wrong; day 2 the owner pastes the right one and re-tests."""
    http = FakeHTTP({"control.msg91.com": PermissionError("HTTP 401")})
    c = make(tmp_path, http)
    save(c, "sms", "msg91", make_active=True)
    day1 = c.post("/api/channels/sms/msg91/test", json={"to": "09000012345"}).json()
    assert day1["ok"] is False and channel_view(c, "sms")["status"] == "error"
    save(c, "sms", "msg91", {"authkey": "msg91-QAFAKE-right-key-K7k7"})
    http.routes = dict(SUCCESS)
    day2 = c.post("/api/channels/sms/msg91/test", json={"to": "09000012345"}).json()
    assert day2["ok"] is True and channel_view(c, "sms")["status"] == "configured"
    sent = http.last("msg91.com")
    assert sent["headers"]["authkey"] == "msg91-QAFAKE-right-key-K7k7"
    assert sent["body"]["recipients"][0]["mobiles"].endswith("9000012345")


def test_whatsapp_test_without_a_template_is_refused_locally(tmp_path):
    c, http = client(tmp_path, "ok")
    save(c, "whatsapp", "gupshup")
    r = c.post("/api/channels/whatsapp/gupshup/test", json={"to": "+91 90000 12345"}).json()
    assert r["ok"] is False and "template" in r["message"] and http.calls == []
    # Meta falls back to its pre-approved hello_world template
    save(c, "whatsapp", "meta_cloud")
    assert c.post("/api/channels/whatsapp/meta_cloud/test", json={"to": "+91 90000 12345"}).json()["ok"] is True
    assert "hello_world" in str(http.last("graph.facebook.com")["body"])


def test_test_send_never_turns_on_live_switches(tmp_path):
    c, _ = client(tmp_path, "ok")
    for channel, pid in (("sms", "fast2sms"), ("whatsapp", "interakt"), ("email_send", "sendgrid")):
        save(c, channel, pid, make_active=True)
        c.post(f"/api/channels/{channel}/{pid}/test", json=body_for(channel))
    assert c.get("/api/channels").json()["live"] == {"live_sending": False, "live_whatsapp": False, "live_sms": False}
