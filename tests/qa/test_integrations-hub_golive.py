"""QA: setup and go-live. The readiness checklist, the "Send for real" switch and its blockers, the WhatsApp / SMS
switches, and the legacy /api/integrations endpoints staying consistent with the Integrations hub (/api/channels).

The journey runs over several days with a 150-row messy leads sheet (BOM, ALL CAPS, Pvt. Ltd., +91 / 0 / spaced phones,
blank and duplicate rows) from fixtures/integrations-hub/go_live_leads.csv. All providers are faked.
"""
import pytest

from app.channels import config
from app.senders import email_ready
from tests.channel_fakes import FakeHTTP
from tests.qa.integrations_hub_support import (card, channel_view, fixture_text, make, save, secret_values, store_of,
                                               values_for)

IDENTITY = {"sender_company": "HireWise Outreach Pvt. Ltd."}
SENDGRID_OK = {"api.sendgrid.com": {"_status": 202}}


def item(c, item_id):
    return next(i for i in c.get("/api/readiness").json()["items"] if i["id"] == item_id)


def legacy(c, integration_id):
    return next(i for i in c.get("/api/integrations").json() if i["id"] == integration_id)


def live(c, on=True):
    return c.put("/api/settings", json={"live_sending": on})


def blockers(c):
    return c.get("/api/sending/status").json()["live_blockers"]


# ---------- the whole journey ----------
def test_go_live_journey_over_three_days(tmp_path):
    http = FakeHTTP(dict(SENDGRID_OK))
    c = make(tmp_path, http)
    r = c.get("/api/readiness").json()
    assert r["ready_for_live_test"] is False and r["done"] == 0
    assert len(blockers(c)) == 3                                                   # address, sending domain, company name

    # day 1: the owner imports the sheet and sets the address, first on localhost
    imported = c.post("/api/leads/import", json={"csv": fixture_text("go_live_leads.csv"), "source": "sheet"})
    assert imported.status_code == 200, imported.text
    assert item(c, "leads")["status"] == "done"
    for url in ("http://localhost:8000", "http://127.0.0.1:8000", "http://192.168.1.20:8000", "http://10.0.0.5"):
        c.put("/api/settings", json={"public_base_url": url})
        assert item(c, "public_url")["status"] == "todo", url
    c.put("/api/settings", json={"public_base_url": "https://outreach.hirewise-outreach.example"})
    assert item(c, "public_url")["status"] == "done"

    # day 2: SendGrid on the Integrations page, then a test email
    assert item(c, "sending_domain")["status"] == "todo"
    save(c, "email_send", "sendgrid", make_active=True)
    assert item(c, "sending_domain")["status"] == "done" and item(c, "test_email")["status"] == "todo"
    test = c.post("/api/channels/email_send/sendgrid/test", json={"to": "qa.owner@hirewise-outreach.example"}).json()
    assert test["ok"] is True, test
    assert item(c, "test_email")["status"] == "done"
    c.put("/api/settings", json={"confirm_bmi_limits": True})
    r = c.get("/api/readiness").json()
    assert r["ready_for_live_test"] is True and item(c, "live_sending")["status"] == "todo"

    # day 3: the switch stays off until the footer identity is complete
    refused = live(c)
    assert refused.status_code == 400 and "company name" in refused.json()["detail"]
    assert c.get("/api/settings").json()["live_sending"] is False
    c.put("/api/settings", json=IDENTITY)
    refused = live(c)
    assert refused.status_code == 400 and "postal address" in refused.json()["detail"]
    c.put("/api/settings", json={"sender_website": "https://hirewise-outreach.example"})
    assert blockers(c) == []
    assert live(c).status_code == 200
    assert item(c, "live_sending")["status"] == "done"
    assert c.get("/api/sending/status").json()["mode"] == "live"
    assert c.get("/api/channels").json()["live"]["live_sending"] is True


# ---------- the switch's requirements, one at a time ----------
def ready_client(tmp_path, **settings):
    c = make(tmp_path)
    save(c, "email_send", "resend", make_active=True)
    c.put("/api/settings", json={"public_base_url": "https://outreach.hirewise-outreach.example", **IDENTITY,
                                 "sender_address": "Plot 12, Sector 62, Noida 201309", **settings})
    return c


@pytest.mark.parametrize("url", ["", "http://outreach.hirewise-outreach.example", "https://localhost", "https://127.0.0.1",
                                 "https://10.20.30.40", "https://192.168.0.10", "https://172.16.5.4", "https://[::1]"])
def test_switch_refused_without_a_public_https_address(tmp_path, url):
    c = ready_client(tmp_path, public_base_url=url)
    if not url:
        store_of(c).update("settings", lambda s: {k: v for k, v in s.items() if k != "public_base_url"})
    r = live(c)
    assert r.status_code == 400 and "https" in r.json()["detail"]
    assert store_of(c).read("settings").get("live_sending") is False


@pytest.mark.parametrize("contact", [{"sender_address": "12 MG Road, Bengaluru 560001"},
                                     {"sender_website": "https://hirewise-outreach.example"},
                                     {"sender_contact_email": "hello@hirewise-outreach.example"}])
def test_any_one_footer_contact_is_enough(tmp_path, contact):
    c = ready_client(tmp_path, sender_address="")
    assert live(c).status_code == 400
    c.put("/api/settings", json=contact)
    assert live(c).status_code == 200


@pytest.mark.parametrize("company", ["", "   "])
def test_blank_company_name_blocks_even_with_an_address(tmp_path, company):
    c = ready_client(tmp_path, sender_company=company)
    r = live(c)
    assert r.status_code == 400 and "company name" in r.json()["detail"]


def test_switch_refused_without_a_connected_sender_and_saved_incomplete_cards_do_not_count(tmp_path):
    c = make(tmp_path)
    c.put("/api/settings", json={"public_base_url": "https://outreach.hirewise-outreach.example", **IDENTITY,
                                 "sender_contact_email": "hello@hirewise-outreach.example"})
    r = live(c)
    assert r.status_code == 400 and "sending domain" in r.json()["detail"]
    # a saved but not-yet-active second card is not a sender either
    save(c, "email_send", "smtp")
    c.delete("/api/channels/email_send/smtp")
    assert live(c).status_code == 400
    save(c, "email_send", "smtp")
    assert live(c).status_code == 200


def test_deleting_the_sender_later_puts_mail_back_in_practice_and_blocks_a_retry(tmp_path):
    c = ready_client(tmp_path)
    assert live(c).status_code == 200
    c.delete("/api/channels/email_send/resend")
    assert c.get("/api/sending/status").json()["mode"] == "practice"               # nothing goes out without a sender
    assert not email_ready(store_of(c))
    assert item(c, "sending_domain")["status"] == "todo"
    r = live(c)
    assert r.status_code == 400 and store_of(c).read("settings")["live_sending"] is False


def test_switching_off_is_always_allowed(tmp_path):
    c = make(tmp_path)
    assert live(c, False).status_code == 200


def test_channel_live_endpoint_cannot_flip_email_sending(tmp_path):
    c = make(tmp_path)
    r = c.put("/api/channels/live", json={"live_sending": True, "live_sms": True})
    assert r.status_code == 200 and r.json() == {"live_sending": False, "live_whatsapp": False, "live_sms": True}
    assert store_of(c).read("settings").get("live_sending") is None


def test_whatsapp_and_sms_switches_are_independent_and_stay_practice_without_a_provider(tmp_path):
    from app.channels.sms_send import send_sms
    from app.channels.whatsapp_send import send_whatsapp
    http = FakeHTTP()
    c = make(tmp_path, http)
    c.put("/api/channels/live", json={"live_whatsapp": True})
    assert c.get("/api/channels").json()["live"] == {"live_sending": False, "live_whatsapp": True, "live_sms": False}
    store = store_of(c)
    assert send_whatsapp(store, "+919000012345", "first_after_yes", "en", [], None, http)["status"] == "dry_run"
    assert send_sms(store, "+919000012345", "Hello", None, None, [], http)["status"] == "dry_run"
    assert http.calls == []
    c.put("/api/channels/live", json={"live_whatsapp": False, "live_sms": None})
    assert c.get("/api/channels").json()["live"]["live_whatsapp"] is False


# ---------- optional checklist rows follow the hub ----------
def test_optional_rows_follow_hub_cards(tmp_path):
    c = make(tmp_path)
    assert item(c, "reply_capture")["status"] == "optional" and item(c, "calendar")["status"] == "optional"
    save(c, "inbound_email", "postmark_inbound")
    assert item(c, "reply_capture")["status"] == "done"
    save(c, "calendar", "google")                                                  # saved but not connected via OAuth
    assert item(c, "calendar")["status"] == "optional"
    save(c, "calendar", "booking_link")
    c.post("/api/channels/calendar/active", json={"provider": "booking_link"})
    assert item(c, "calendar")["status"] == "done"
    save(c, "whatsapp", "aisensy")
    assert item(c, "whatsapp")["status"] == "done"
    c.delete("/api/channels/whatsapp/aisensy")
    assert item(c, "whatsapp")["status"] == "optional"
    c.delete("/api/channels/inbound_email/postmark_inbound")
    assert item(c, "reply_capture")["status"] == "optional"


# ---------- legacy /api/integrations stays consistent with /api/channels ----------
LEGACY = [("email_send", "sending_domain"), ("whatsapp", "whatsapp"), ("calls", "telephony")]


@pytest.mark.parametrize("channel,integration_id", LEGACY)
def test_legacy_view_mirrors_the_active_hub_provider(tmp_path, channel, integration_id):
    c = make(tmp_path)
    pids = [p["id"] for p in channel_view(c, channel)["providers"]
            if p["id"] not in ("google", "microsoft", "custom_webhook")]
    for pid in pids:
        save(c, channel, pid)
        c.post(f"/api/channels/{channel}/active", json={"provider": pid})
        view = legacy(c, integration_id)
        assert view["status"] == "configured" and view["mode"] == "api" and view["values"]["provider"] == pid, pid
        values = values_for(channel, pid)
        assert not [s for s in secret_values(channel, pid, values) if s in str(view)], pid
    c.delete(f"/api/channels/{channel}/{pids[-1]}")
    assert legacy(c, integration_id)["status"] == "not_configured"
    assert channel_view(c, channel)["active"] is None


def test_legacy_save_of_a_hub_provider_shows_up_in_the_hub(tmp_path):
    c = make(tmp_path)
    r = c.put("/api/integrations/whatsapp", json={"mode": "api", "values": {"provider": "wati", **values_for("whatsapp", "wati")}})
    assert r.status_code == 200 and r.json()["status"] == "configured"
    assert channel_view(c, "whatsapp")["active"] == "wati" and card(c, "whatsapp", "wati")["status"] == "configured"
    # echoing the masked token back through the old endpoint keeps the real one
    echoed = {**r.json()["values"]}
    assert echoed["access_token"].startswith("••••")
    c.put("/api/integrations/whatsapp", json={"mode": "api", "values": echoed})
    assert config.config_for(store_of(c), "whatsapp")["values"]["access_token"] == values_for("whatsapp", "wati")["access_token"]


def test_legacy_classic_resend_card_and_hub_resend_are_the_same_thing(tmp_path):
    c = make(tmp_path)
    c.put("/api/integrations/sending_domain", json={"mode": "api", "values": {
        "domain": "mail.hirewise-outreach.example", "from_name": "Priya", "from_email": "priya@mail.hirewise-outreach.example",
        "api_key": "re_QAFAKE_classic_0000"}})
    assert channel_view(c, "email_send")["status"] == "configured"
    assert email_ready(store_of(c))["api_key"] == "re_QAFAKE_classic_0000"
    assert "re_QAFAKE_classic_0000" not in c.get("/api/channels").text + c.get("/api/integrations").text


def test_legacy_test_button_without_hub_provider_keeps_its_old_answers(tmp_path):
    c = make(tmp_path)
    assert c.post("/api/integrations/whatsapp/test", json={}).json()["ok"] is False
    assert c.post("/api/integrations/telephony/test", json={}).json()["ok"] is False
    save(c, "calls", "click_to_call")
    assert c.post("/api/integrations/telephony/test", json={}).json()["ok"] is True
