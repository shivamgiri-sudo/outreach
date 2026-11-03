"""Integrations hub: catalogue shape, saving, secret masking, active provider and compatibility with /api/integrations."""
import json

from app import integrations
from app.channels import registry
from app.channels.security import server_secret, sign_state, verify_state
from app.store import Store
from tests.channel_fakes import make_client, provider

LEGACY_IDS = {"sending_domain", "whatsapp", "apollo", "sales_navigator", "llm", "email_verifier", "telephony", "hunter",
              "firecrawl", "contact_scraper"}


# ---- catalogue ----
def test_every_provider_has_steps_fields_docs_and_a_test():
    assert len(registry.BY_KEY) >= 40
    for (channel, pid), p in registry.BY_KEY.items():
        assert p["id"] == pid and p["channel"] == channel
        assert p["steps"] and all(isinstance(s, str) and s for s in p["steps"]), pid
        assert isinstance(p["fields"], list), pid
        assert p["docs_url"].startswith("https://"), pid
        assert p["title"] and p["why"] and p["test"] and isinstance(p["capabilities"], list), pid
        for f in p["fields"]:
            assert {"key", "label", "required", "secret", "kind"} <= set(f), (pid, f)


def test_required_channels_and_providers_exist():
    ids = {c: {p["id"] for p in registry.providers_for(c)} for c in registry.CHANNELS_BY_ID}
    assert {"resend", "sendgrid", "postmark", "ses_smtp", "smtp", "gmail_smtp", "custom_http"} <= ids["email_send"]
    assert {"imap", "resend_inbound", "sendgrid_inbound", "postmark_inbound", "custom_webhook"} <= ids["inbound_email"]
    assert {"resend_events", "sendgrid_events", "postmark_events"} <= ids["email_events"]
    assert {"meta_cloud", "gupshup", "interakt", "wati", "aisensy", "custom_http"} <= ids["whatsapp"]
    assert {"msg91", "twilio_sms", "fast2sms", "custom_http"} <= ids["sms"]
    assert {"click_to_call", "exotel", "knowlarity", "twilio_voice", "custom_http"} <= ids["calls"]
    assert {"google", "microsoft", "zoom", "calcom", "calendly", "booking_link", "custom_http"} <= ids["calendar"]
    assert LEGACY_IDS - {"sending_domain", "whatsapp", "telephony"} <= ids["lead_data"] | ids["ai"]


def test_legacy_catalogue_ids_still_there():
    assert LEGACY_IDS <= integrations.known_ids()


def test_steps_explain_india_specifics_and_port_25():
    msg91 = registry.get_provider("sms", "msg91")
    assert any("DLT" in s and "Entity ID" in s for s in msg91["steps"])
    assert any("{#var#}" in s for s in msg91["steps"]) and any("21:00" in s for s in msg91["steps"])
    smtp = {f["key"]: f for f in registry.get_provider("email_send", "smtp")["fields"]}
    assert "25" in smtp["smtp_port"]["help"] and "587" in smtp["smtp_port"]["help"]
    assert any("cold" in s.lower() for s in registry.get_provider("email_send", "postmark")["steps"])


def test_recommended_badges():
    rec = {k for k, p in registry.BY_KEY.items() if p["recommended"]}
    assert {("email_send", "resend"), ("inbound_email", "imap"), ("whatsapp", "meta_cloud"), ("whatsapp", "interakt"),
            ("sms", "msg91"), ("calendar", "google"), ("calendar", "calcom")} <= rec


# ---- GET /api/channels ----
def test_channels_list_starts_empty_and_lists_webhook_free_state(tmp_path):
    data = make_client(tmp_path).get("/api/channels").json()
    channels = {c["id"]: c for c in data["channels"]}
    assert {"email_send", "inbound_email", "email_events", "whatsapp", "sms", "calls", "calendar", "lead_data", "ai"} <= set(channels)
    assert all(c["status"] == "not_configured" for c in channels.values())
    assert data["oauth_redirects"]["google"].endswith("/api/oauth/google/callback")
    assert "{{text}}" not in data["placeholders"] and "text" in data["placeholders"]


# ---- saving ----
def test_save_validates_required_fields(tmp_path):
    c = make_client(tmp_path)
    r = c.put("/api/channels/sms/msg91", json={"values": {"authkey": "k"}})
    assert r.status_code == 400 and "MSG91 template ID" in r.json()["detail"]
    assert c.put("/api/channels/sms/nope", json={"values": {}}).status_code == 404
    assert c.put("/api/channels/lead_data/apollo", json={"values": {}}).status_code == 404


def test_secrets_masked_and_masked_value_never_overwrites(tmp_path):
    c = make_client(tmp_path)
    r = c.put("/api/channels/sms/msg91", json={"values": {"authkey": "real-secret-9876", "template_id": "tpl1", "sender_id": "BMIHRS"}})
    assert r.status_code == 200 and r.json()["values"]["authkey"] == "••••9876" and r.json()["status"] == "configured"
    c.put("/api/channels/sms/msg91", json={"values": {"authkey": "••••9876", "template_id": "tpl2"}})
    c.put("/api/channels/sms/msg91", json={"values": {"authkey": ""}})
    saved = Store(tmp_path / "d.json").read("integrations")["sms"]
    assert saved["values"]["authkey"] == "real-secret-9876" and saved["values"]["template_id"] == "tpl2"
    assert "real-secret-9876" not in json.dumps(c.get("/api/channels").json())
    assert "real-secret-9876" not in json.dumps(c.get("/api/integrations").json())


def test_hub_secrets_are_masked_in_old_integrations_endpoint(tmp_path):
    c = make_client(tmp_path)
    c.put("/api/channels/email_send/smtp", json={"values": {
        "from_name": "Priya", "from_email": "priya@mail.example.in", "smtp_host": "smtp.example.in", "smtp_port": "587",
        "smtp_security": "starttls", "smtp_username": "priya", "smtp_password": "smtp-pass-4321"}})
    legacy = next(i for i in c.get("/api/integrations").json() if i["id"] == "sending_domain")
    assert legacy["values"]["smtp_password"] == "••••4321" and legacy["values"]["provider"] == "smtp"
    assert legacy["status"] == "configured"   # judged by the SMTP provider's own required fields


def test_first_saved_provider_becomes_active_and_switching_keeps_both(tmp_path):
    c = make_client(tmp_path)
    c.put("/api/channels/sms/msg91", json={"values": {"authkey": "k1", "template_id": "t1"}})
    c.put("/api/channels/sms/fast2sms", json={"values": {"api_key": "f2", "sender_id": "BMIHRS", "message_id": "123"}})
    assert provider(c, "sms", "msg91")["active"] is True and provider(c, "sms", "fast2sms")["active"] is False
    r = c.post("/api/channels/sms/active", json={"provider": "fast2sms"})
    assert r.status_code == 200 and r.json()["active"] == "fast2sms"
    c.post("/api/channels/sms/active", json={"provider": "msg91"})
    saved = Store(tmp_path / "d.json").read("integrations")["sms"]
    assert saved["values"]["provider"] == "msg91" and saved["values"]["authkey"] == "k1"
    assert saved["providers"]["fast2sms"]["values"]["api_key"] == "f2"


def test_cannot_activate_an_unfinished_provider_or_a_multi_channel(tmp_path):
    c = make_client(tmp_path)
    assert c.post("/api/channels/sms/active", json={"provider": "twilio_sms"}).status_code == 400
    c.put("/api/channels/inbound_email/sendgrid_inbound", json={"values": {}})
    assert c.post("/api/channels/inbound_email/active", json={"provider": "sendgrid_inbound"}).status_code == 400


def test_delete_clears_active_provider(tmp_path):
    c = make_client(tmp_path)
    c.put("/api/channels/sms/msg91", json={"values": {"authkey": "k1", "template_id": "t1"}})
    r = c.delete("/api/channels/sms/msg91")
    assert r.status_code == 200 and r.json()["status"] == "not_configured"
    assert next(ch for ch in c.get("/api/channels").json()["channels"] if ch["id"] == "sms")["active"] is None


def test_legacy_whatsapp_and_telephony_entries_show_up_in_the_hub(tmp_path):
    c = make_client(tmp_path)
    c.put("/api/integrations/telephony", json={"mode": "api", "values": {"click_to_call_url": "https://dial.example/call?n={phone}"}})
    assert provider(c, "calls", "click_to_call")["active"] is True and provider(c, "calls", "click_to_call")["status"] == "configured"
    c.put("/api/integrations/whatsapp", json={"mode": "api", "values": {"provider": "gupshup"}})
    assert provider(c, "whatsapp", "gupshup")["status"] == "incomplete"
    wa = next(i for i in c.get("/api/integrations").json() if i["id"] == "whatsapp")
    assert wa["status"] == "incomplete"


def test_old_integration_save_keeps_hub_test_results(tmp_path):
    c = make_client(tmp_path)
    c.put("/api/channels/sms/msg91", json={"values": {"authkey": "k1", "template_id": "t1"}})
    c.put("/api/channels/sms/fast2sms", json={"values": {"api_key": "f2", "sender_id": "BMIHRS", "message_id": "123"}})
    c.put("/api/integrations/telephony", json={"mode": "api", "values": {"click_to_call_url": "https://d/x?n={phone}"}})
    c.post("/api/channels/calls/click_to_call/test", json={})
    c.put("/api/integrations/telephony", json={"mode": "api", "values": {"click_to_call_url": "https://d/y?n={phone}"}})
    assert provider(c, "calls", "click_to_call")["last_test"]["ok"] is True


def test_field_format_validation(tmp_path):
    c = make_client(tmp_path)
    bad_email = c.put("/api/channels/email_send/sendgrid", json={"values": {"from_name": "P", "from_email": "nope", "api_key": "SG.x"}})
    assert bad_email.status_code == 400 and "email" in bad_email.json()["detail"].lower()
    bad_port = c.put("/api/channels/email_send/smtp", json={"values": {
        "from_name": "P", "from_email": "p@x.in", "smtp_host": "h", "smtp_port": "abc", "smtp_username": "u", "smtp_password": "p"}})
    assert bad_port.status_code == 400
    bad_link = c.put("/api/calendar", json={})
    assert bad_link.status_code in (404, 405)
    http_link = c.put("/api/channels/calendar/booking_link", json={"values": {"booking_url": "http://cal.example/x"}})
    assert http_link.status_code == 400 and "https" in http_link.json()["detail"]
    no_phone = c.put("/api/channels/calls/click_to_call", json={"values": {"click_to_call_url": "https://dial/x"}})
    assert no_phone.status_code == 400 and "{phone}" in no_phone.json()["detail"]


# ---- secrets that never leave the server ----
def test_server_secret_is_created_once_and_never_returned(tmp_path):
    c = make_client(tmp_path)
    store = c.app.state.store
    secret = server_secret(store)
    assert len(secret) == 64 and server_secret(store) == secret
    for path in ("/api/settings", "/api/offer", "/api/growth", "/api/channels", "/api/channels/webhooks", "/api/integrations"):
        assert secret not in c.get(path).text, path


def test_live_switches(tmp_path):
    c = make_client(tmp_path)
    assert c.put("/api/channels/live", json={"live_whatsapp": True}).json()["live_whatsapp"] is True
    data = c.get("/api/channels").json()
    assert data["live"]["live_whatsapp"] is True and data["live"]["live_sms"] is False
    assert next(ch for ch in data["channels"] if ch["id"] == "whatsapp")["live"] is True


def test_oauth_state_sign_and_verify(tmp_path):
    store = Store(tmp_path / "s.json")
    token = sign_state(store, {"provider": "google", "return_to": "http://localhost:5173/"}, now=1000)
    assert verify_state(store, token, now=1100)["provider"] == "google"
    assert verify_state(store, token, now=1000 + 601) is None                       # expired
    raw, sig = token.split(".")
    assert verify_state(store, raw + "." + sig[:-2] + ("AA" if sig[-2:] != "AA" else "BB"), now=1100) is None
    assert verify_state(store, "garbage", now=1100) is None and verify_state(store, "", now=1100) is None


def test_readiness_gains_optional_rows(tmp_path):
    c = make_client(tmp_path)
    items = {i["id"]: i for i in c.get("/api/readiness").json()["items"]}
    assert items["reply_capture"]["status"] == "optional" and items["calendar"]["required"] is False
    c.put("/api/channels/calendar/booking_link", json={"values": {"booking_url": "https://cal.example/priya"}})
    items = {i["id"]: i for i in c.get("/api/readiness").json()["items"]}
    assert items["calendar"]["status"] == "done"
