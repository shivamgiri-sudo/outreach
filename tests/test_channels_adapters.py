"""Adapters: request mapping and reply parsing for each provider, all through fake transports; dry-run gating."""
from urllib.parse import unquote

import pytest

from app.channels import config
from app.channels.calls import start_call
from app.channels.email_send import deliver, send_email
from app.channels.sms_send import send_sms
from app.channels.whatsapp_send import send_whatsapp
from app.senders import make_email_sender
from app.store import Store
from tests.channel_fakes import FakeHTTP, SMTPFactory, make_client

SENDER = {"from_name": "Priya", "from_email": "priya@mail.example.in"}
MESSAGE = {"lead_id": "L1", "to": "asha@acme-retail.in", "subject": "Hiring at Acme", "body": "Hi Asha"}


def store_with(tmp_path, channel, pid, values, live=None):
    store = Store(tmp_path / "s.json")
    config.save_provider(store, channel, pid, values, resolver=lambda h: ["93.184.216.34"])
    if live:
        store.write("settings", {**store.read("settings"), live: True})
    return store


# ---- email ----
def test_resend_request_and_id(tmp_path):
    store = store_with(tmp_path, "email_send", "resend", {**SENDER, "domain": "mail.example.in", "api_key": "re_k"}, "live_sending")
    http = FakeHTTP({"api.resend.com": {"id": "em_1"}})
    out = send_email(store, MESSAGE, http, base_url="https://outreach.example.in")
    call = http.last()
    assert out == {"status": "sent", "provider_id": "em_1"}
    assert call["headers"]["Authorization"] == "Bearer re_k" and call["body"]["to"] == ["asha@acme-retail.in"]
    assert call["body"]["headers"]["List-Unsubscribe"].startswith("<https://outreach.example.in/u/L1>")


def test_sendgrid_request_and_message_id_header(tmp_path):
    store = store_with(tmp_path, "email_send", "sendgrid", {**SENDER, "api_key": "SG.k"}, "live_sending")
    http = FakeHTTP({"api.sendgrid.com": {"_status": 202, "_headers": {"x-message-id": "sg-123"}}})
    out = send_email(store, MESSAGE, http)
    body = http.last()["body"]
    assert out == {"status": "sent", "provider_id": "sg-123"}
    assert body["personalizations"] == [{"to": [{"email": "asha@acme-retail.in"}]}]
    assert body["from"] == {"email": "priya@mail.example.in", "name": "Priya"} and body["content"][0]["value"] == "Hi Asha"


def test_sendgrid_bad_key_fails_cleanly(tmp_path):
    store = store_with(tmp_path, "email_send", "sendgrid", {**SENDER, "api_key": "SG.k"}, "live_sending")
    out = send_email(store, MESSAGE, FakeHTTP({"sendgrid": PermissionError("HTTP 401")}))
    assert out["status"] == "provider_error" and out["retryable"] is False   # a bad key pauses sending, not one lead


def test_postmark_request_and_error_code(tmp_path):
    store = store_with(tmp_path, "email_send", "postmark", {**SENDER, "server_token": "pm-tok"}, "live_sending")
    http = FakeHTTP({"postmarkapp.com": {"ErrorCode": 0, "MessageID": "pm-1", "Message": "OK"}})
    assert send_email(store, MESSAGE, http) == {"status": "sent", "provider_id": "pm-1"}
    call = http.last()
    assert call["headers"]["X-Postmark-Server-Token"] == "pm-tok" and call["body"]["MessageStream"] == "outbound"
    assert call["body"]["To"] == "asha@acme-retail.in" and call["body"]["TextBody"] == "Hi Asha"
    failed = send_email(store, MESSAGE, FakeHTTP({"postmarkapp.com": {"ErrorCode": 406, "Message": "Inactive recipient"}}))
    assert failed["status"] == "failed" and "406" in failed["error"]


@pytest.mark.parametrize("port,security,ssl_expected", [("587", "starttls", False), ("465", "ssl", True)])
def test_smtp_starttls_and_ssl(tmp_path, port, security, ssl_expected):
    store = store_with(tmp_path, "email_send", "smtp", {**SENDER, "smtp_host": "smtp.example.in", "smtp_port": port,
                                                        "smtp_security": security, "smtp_username": "priya", "smtp_password": "pw"},
                       "live_sending")
    factory = SMTPFactory()
    out = send_email(store, MESSAGE, FakeHTTP(), smtp_factory=factory, base_url="https://o.example.in")
    client = factory.clients[0]
    assert out["status"] == "sent" and out["provider_id"].startswith("<")
    assert (client.host, client.port, client.use_ssl) == ("smtp.example.in", int(port), ssl_expected)
    assert ("starttls" in client.log) is (not ssl_expected) and ("login", "priya") in client.log and "quit" in client.log
    sent = client.sent[0]
    assert sent["To"] == "asha@acme-retail.in" and sent["List-Unsubscribe"].startswith("<https://o.example.in/u/L1>")
    assert sent["From"] == "Priya <priya@mail.example.in>"


def test_gmail_uses_smtp_gmail_and_bad_login_fails(tmp_path):
    store = store_with(tmp_path, "email_send", "gmail_smtp", {**SENDER, "smtp_username": "priya@mail.example.in", "app_password": "abcd"},
                       "live_sending")
    factory = SMTPFactory(fail_login=True)
    out = send_email(store, MESSAGE, FakeHTTP(), smtp_factory=factory)
    assert factory.clients[0].host == "smtp.gmail.com" and factory.clients[0].port == 587
    assert out["status"] == "provider_error" and "rejected" in out["error"]


def test_email_is_dry_run_without_live_switch(tmp_path):
    store = store_with(tmp_path, "email_send", "sendgrid", {**SENDER, "api_key": "SG.k"})
    http, factory = FakeHTTP(), SMTPFactory()
    assert send_email(store, MESSAGE, http, smtp_factory=factory) == {"status": "dry_run"}
    assert http.calls == [] and factory.clients == []


def test_sequence_sender_dispatches_to_the_active_hub_provider(tmp_path):
    store = store_with(tmp_path, "email_send", "sendgrid", {**SENDER, "api_key": "SG.k"}, "live_sending")
    legacy, http = FakeHTTP(), FakeHTTP({"sendgrid": {"_headers": {"x-message-id": "sg-9"}}})
    send = make_email_sender(store, legacy, "https://o.example.in", channel_transport=http)
    assert send(MESSAGE) == {"status": "sent", "provider_id": "sg-9"}
    assert legacy.calls == [] and "sendgrid" in http.calls[0]["url"]


def test_hub_email_test_endpoint_marks_test_email_done(tmp_path):
    factory = SMTPFactory()
    c = make_client(tmp_path, smtp_factory=factory)
    c.put("/api/channels/email_send/smtp", json={"values": {**SENDER, "smtp_host": "smtp.example.in", "smtp_port": "587",
                                                            "smtp_security": "starttls", "smtp_username": "u", "smtp_password": "p"}})
    r = c.post("/api/channels/email_send/smtp/test", json={"to": "me@teammas.in"}).json()
    assert r["ok"] is True and r["last_test"]["ok"] is True and factory.clients[0].sent[0]["To"] == "me@teammas.in"
    items = {i["id"]: i for i in c.get("/api/readiness").json()["items"]}
    assert items["sending_domain"]["status"] == "done" and items["test_email"]["status"] == "done"
    assert c.post("/api/channels/email_send/smtp/test", json={"to": "bad"}).json()["ok"] is False


# ---- WhatsApp ----
META = {"phone_number_id": "1234567890", "access_token": "EAAtok"}


def test_meta_template_and_text_mapping(tmp_path):
    store = store_with(tmp_path, "whatsapp", "meta_cloud", {**META, "template_map": "first_after_yes = bmi_first_v2"}, "live_whatsapp")
    http = FakeHTTP({"graph.facebook.com": {"messages": [{"id": "wamid.ABC"}]}})
    out = send_whatsapp(store, "+91 98123 45678", "first_after_yes", "", ["Asha", "https://demo"], transport=http)
    call = http.last()
    assert out == {"status": "sent", "provider_id": "wamid.ABC"}
    assert call["url"] == "https://graph.facebook.com/v23.0/1234567890/messages"
    assert call["headers"]["Authorization"] == "Bearer EAAtok"
    tpl = call["body"]["template"]
    assert call["body"]["to"] == "919812345678" and tpl["name"] == "bmi_first_v2" and tpl["language"] == {"code": "en"}
    assert tpl["components"][0]["parameters"] == [{"type": "text", "text": "Asha"}, {"type": "text", "text": "https://demo"}]
    send_whatsapp(store, "+919812345678", "", text="Thanks!", transport=http)
    assert http.last()["body"]["type"] == "text" and http.last()["body"]["text"]["body"] == "Thanks!"


def test_meta_error_is_reported(tmp_path):
    store = store_with(tmp_path, "whatsapp", "meta_cloud", META, "live_whatsapp")
    out = send_whatsapp(store, "+919812345678", "hello_world", transport=FakeHTTP({"graph": OSError("HTTP 400 template not found")}))
    assert out["status"] == "failed" and "template" in out["error"]


def test_gupshup_form_mapping(tmp_path):
    store = store_with(tmp_path, "whatsapp", "gupshup", {"api_key": "gk", "source_number": "917000000000", "app_name": "BMI"}, "live_whatsapp")
    http = FakeHTTP({"gupshup.io": {"status": "submitted", "messageId": "gs-1"}})
    assert send_whatsapp(store, "+919812345678", "tpl-uuid", variables={"name": "Asha"}, transport=http) == {"status": "sent", "provider_id": "gs-1"}
    call = http.last()
    assert call["url"].endswith("/template/msg") and call["headers"] == {"apikey": "gk"}
    assert call["form"]["destination"] == "919812345678" and call["form"]["src.name"] == "BMI"
    assert '"id": "tpl-uuid"' in call["form"]["template"] and '"Asha"' in call["form"]["template"]
    bad = send_whatsapp(store, "+919812345678", "tpl", transport=FakeHTTP({"gupshup": {"status": "error", "message": "Invalid app"}}))
    assert bad["status"] == "failed" and "Invalid app" in bad["error"]


def test_interakt_mapping(tmp_path):
    store = store_with(tmp_path, "whatsapp", "interakt", {"api_key": "base64key"}, "live_whatsapp")
    http = FakeHTTP({"interakt.ai": {"result": True, "id": "ik-1"}})
    assert send_whatsapp(store, "+919812345678", "first_after_yes", "", ["Asha"], transport=http)["provider_id"] == "ik-1"
    call = http.last()
    assert call["headers"]["Authorization"] == "Basic base64key"
    assert call["body"]["countryCode"] == "+91" and call["body"]["phoneNumber"] == "9812345678"
    assert call["body"]["template"] == {"name": "first_after_yes", "languageCode": "en", "bodyValues": ["Asha"]}


def test_wati_mapping_and_ssrf_on_endpoint(tmp_path):
    store = store_with(tmp_path, "whatsapp", "wati", {"api_endpoint": "https://live-mt-server.wati.io/123", "access_token": "Bearer wt"},
                       "live_whatsapp")
    http = FakeHTTP({"wati.io": {"result": True}})
    out = send_whatsapp(store, "+919812345678", "first_after_yes", "", {"name": "Asha"}, transport=http,
                        resolver=lambda h: ["93.184.216.34"])
    call = http.last()
    assert out["status"] == "sent" and call["headers"]["Authorization"] == "Bearer wt"
    assert call["url"] == "https://live-mt-server.wati.io/123/api/v1/sendTemplateMessage?whatsappNumber=919812345678"
    assert call["body"]["parameters"] == [{"name": "name", "value": "Asha"}]
    blocked = send_whatsapp(store, "+919812345678", "t", transport=http, resolver=lambda h: ["10.0.0.5"])
    assert blocked["status"] == "failed" and "private" in blocked["error"]


def test_whatsapp_dry_run_unless_connected_and_live(tmp_path):
    http = FakeHTTP()
    not_live = store_with(tmp_path, "whatsapp", "meta_cloud", META)
    assert send_whatsapp(not_live, "+919812345678", "hello_world", transport=http) == {"status": "dry_run"}
    empty = Store(tmp_path / "empty.json")
    empty.write("settings", {"live_whatsapp": True})
    assert send_whatsapp(empty, "+919812345678", "hello_world", transport=http) == {"status": "dry_run"}
    assert http.calls == []


# ---- SMS ----
def test_msg91_flow_mapping(tmp_path):
    store = store_with(tmp_path, "sms", "msg91", {"authkey": "ak", "template_id": "tpl-1", "sender_id": "BMIHRS"}, "live_sms")
    http = FakeHTTP({"msg91.com": {"type": "success", "message": "req-1"}})
    assert send_sms(store, "9812345678", variables=["Asha", "Acme"], transport=http) == {"status": "sent", "provider_id": "req-1"}
    call = http.last()
    assert call["url"] == "https://control.msg91.com/api/v5/flow" and call["headers"]["authkey"] == "ak"
    assert call["body"]["template_id"] == "tpl-1"
    assert call["body"]["recipients"] == [{"mobiles": "919812345678", "var1": "Asha", "var2": "Acme"}]
    bad = send_sms(store, "9812345678", transport=FakeHTTP({"msg91": {"type": "error", "message": "Invalid template"}}))
    assert bad["status"] == "failed" and "Invalid template" in bad["error"]


def test_twilio_sms_form_and_basic_auth(tmp_path):
    store = store_with(tmp_path, "sms", "twilio_sms", {"account_sid": "AC123", "auth_token": "tok", "from_number": "BMIHRS"}, "live_sms")
    http = FakeHTTP({"api.twilio.com": {"sid": "SM1", "status": "queued"}})
    assert send_sms(store, "+919812345678", "Hello", transport=http) == {"status": "sent", "provider_id": "SM1"}
    call = http.last()
    assert call["url"] == "https://api.twilio.com/2010-04-01/Accounts/AC123/Messages.json"
    assert call["headers"]["Authorization"].startswith("Basic ") and call["form"] == {"To": "+919812345678", "Body": "Hello", "From": "BMIHRS"}


def test_fast2sms_dlt_mapping(tmp_path):
    store = store_with(tmp_path, "sms", "fast2sms", {"api_key": "fk", "sender_id": "BMIHRS", "message_id": "1234"}, "live_sms")
    http = FakeHTTP({"fast2sms.com": {"return": True, "request_id": "f-1"}})
    assert send_sms(store, "+919812345678", variables=["a", "b"], transport=http)["provider_id"] == "f-1"
    body = http.last()["body"]
    assert body == {"route": "dlt", "sender_id": "BMIHRS", "message": "1234", "variables_values": "a|b", "numbers": "9812345678", "flash": "0"}


def test_sms_dry_run_without_live_switch(tmp_path):
    http = FakeHTTP()
    store = store_with(tmp_path, "sms", "msg91", {"authkey": "ak", "template_id": "t"})
    assert send_sms(store, "9812345678", transport=http) == {"status": "dry_run"} and http.calls == []


# ---- calls ----
def test_exotel_connects_agent_then_lead(tmp_path):
    store = store_with(tmp_path, "calls", "exotel", {"subdomain": "api.in.exotel.com", "account_sid": "bmi1", "api_key": "k",
                                                     "api_token": "t", "caller_id": "08012345678", "agent_phone": "+919000000001"})
    http = FakeHTTP({"exotel.com": {"Call": {"Sid": "ex-1", "Status": "queued"}}})
    assert start_call(store, None, "9812345678", http) == {"status": "started", "provider_id": "ex-1"}
    call = http.last()
    assert call["url"] == "https://api.in.exotel.com/v1/Accounts/bmi1/Calls/connect.json"
    assert call["form"] == {"From": "+919000000001", "To": "+919812345678", "CallerId": "08012345678"}
    assert call["headers"]["Authorization"].startswith("Basic ")


def test_twilio_voice_and_knowlarity(tmp_path):
    store = store_with(tmp_path, "calls", "twilio_voice", {"account_sid": "AC1", "auth_token": "t", "from_number": "+14155550100",
                                                           "agent_phone": "+919000000001"})
    http = FakeHTTP({"twilio.com": {"sid": "CA1"}})
    assert start_call(store, None, "+919812345678", http)["provider_id"] == "CA1"
    assert "<Number>+919812345678</Number>" in http.last()["form"]["Twiml"] and http.last()["form"]["To"] == "+919000000001"
    k = store_with(tmp_path / "k", "calls", "knowlarity", {"sr_api_key": "sr", "x_api_key": "xk", "k_number": "+918000000000",
                                                           "agent_phone": "+919000000001"})
    kh = FakeHTTP({"knowlarity": {"success": {"status": "success", "call_id": "kn-1"}}})
    assert start_call(k, None, "+919812345678", kh)["provider_id"] == "kn-1"
    assert kh.last()["headers"] == {"x-api-key": "xk", "Authorization": "sr"} and kh.last()["body"]["customer_number"] == "+919812345678"


def test_click_to_call_returns_link_and_nothing_connected_is_dry_run(tmp_path):
    store = store_with(tmp_path, "calls", "click_to_call", {"click_to_call_url": "https://dial.example/call?n={phone}"})
    out = start_call(store, None, "+919812345678", FakeHTTP())
    assert out["status"] == "link" and unquote(out["url"]) == "https://dial.example/call?n=+919812345678"
    assert start_call(Store(tmp_path / "none.json"), "+919000000001", "+919812345678", FakeHTTP()) == {"status": "dry_run"}


def test_deliver_reports_missing_values_without_raising():
    out = deliver({"provider": "sendgrid", "values": {}}, "a@b.in", "s", "t", {}, FakeHTTP())
    assert out["status"] == "failed"
