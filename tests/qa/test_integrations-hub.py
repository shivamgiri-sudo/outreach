"""QA: every provider card in the Integrations hub catalogue.

For each card: save with its required fields, secrets masked everywhere, the wizard's re-save keeps the real secrets,
each required field is enforced, delete wipes the keys, the active provider rules, and webhook addresses (listing,
secret rotation, refusing old secrets). All HTTP is faked; values come from fixtures/integrations-hub/provider_values.json.
"""
import json

import pytest

from app.channels import config, registry
from tests.channel_fakes import FakeHTTP
from tests.qa.integrations_hub_support import (HUB_CARDS, OAUTH_CARDS, PUBLIC, VALUES, card, channel_view, hook, make,
                                               masked, save, secret_values, store_of, ui_form, values_for)

EXCLUSIVE = [k for k in HUB_CARDS if registry.channel(k[0])["kind"] == "exclusive"]
MULTI = [k for k in HUB_CARDS if registry.channel(k[0])["kind"] == "multi"]
WEBHOOK_CARDS = sorted(registry.webhook_pairs())


def required_without_default(channel, pid):
    provider = registry.get_provider(channel, pid)
    return [f for f in provider["fields"] if f.get("required") and f.get("default") is None]


# ---------- the catalogue itself ----------
def test_fixture_has_complete_values_for_every_hub_card():
    assert set(VALUES) == {f"{c}/{p}" for c, p in HUB_CARDS}
    assert len(HUB_CARDS) >= 40
    for channel, pid in HUB_CARDS:
        values = values_for(channel, pid)
        assert not config.missing(registry.get_provider(channel, pid), values), (channel, pid)


def test_catalogue_cards_are_honest_and_complete():
    for channel, pid in HUB_CARDS:
        p = registry.get_provider(channel, pid)
        assert p["docs_url"].startswith("https://") and p["steps"] and p["test"], pid
        assert "₹" not in p["cost_note"] and "$" not in p["cost_note"], pid           # never states prices
        assert not any("port 25" in s.lower() and "never" not in s.lower() for s in p["steps"]), pid
        if p.get("webhook"):
            assert p["webhook"]["auth"] in ("token", "meta", "svix", "calcom", "calendly"), pid
        keys = [f["key"] for f in p["fields"]]
        assert len(keys) == len(set(keys)), pid


# ---------- save / mask / re-save / delete, card by card ----------
@pytest.mark.parametrize("channel,pid", HUB_CARDS)
def test_card_save_masks_secrets_and_wizard_resave_keeps_them(tmp_path, channel, pid):
    c = make(tmp_path)
    before = card(c, channel, pid)
    assert before["status"] == "not_configured" and before["saved"] is False and before["values"] == {}
    values = values_for(channel, pid)
    r = save(c, channel, pid, values)
    assert r.status_code == 200, r.text
    shown = r.json()
    assert shown["saved"] is True
    assert shown["status"] == ("incomplete" if (channel, pid) in OAUTH_CARDS else "configured")
    secrets = secret_values(channel, pid, values)
    provider = registry.get_provider(channel, pid)
    for f in provider["fields"]:
        if f.get("secret") and values.get(f["key"]):
            assert shown["values"][f["key"]] == masked(values[f["key"]]) == "••••" + values[f["key"]][-4:]
        elif f["kind"] == "headers" and values.get(f["key"]):
            for sent, got in zip(values[f["key"]], shown["values"][f["key"]]):
                assert got["value"] == (masked(sent["value"]) if sent.get("secret") else sent["value"])
        elif values.get(f["key"]):
            assert shown["values"][f["key"]] == values[f["key"]]                   # plain values come back as typed
    for path in ("/api/channels", "/api/integrations", "/api/readiness", "/api/settings"):
        text = c.get(path).text
        assert not [s for s in secrets if s in text], path

    # the wizard re-opens the card and saves: secrets empty, headers masked, plain fields as shown
    form = ui_form(card(c, channel, pid))
    again = c.put(f"/api/channels/{channel}/{pid}", json={"values": form})
    assert again.status_code == 200, again.text
    stored = config.config_for(store_of(c), channel, pid)
    for f in provider["fields"]:
        if f.get("secret") and values.get(f["key"]):
            assert stored["values"][f["key"]] == values[f["key"]], f["key"]
        if f["kind"] == "headers" and values.get(f["key"]):
            assert stored["values"][f["key"]] == [{**h, "secret": bool(h.get("secret"))} for h in values[f["key"]]]
    # an API client echoing the masked values it read is just as safe
    echo = {k: v for k, v in card(c, channel, pid)["values"].items()}
    assert c.put(f"/api/channels/{channel}/{pid}", json={"values": echo}).status_code == 200
    assert secret_values(channel, pid, config.config_for(store_of(c), channel, pid)["values"]) == secrets


@pytest.mark.parametrize("channel,pid", HUB_CARDS)
def test_card_delete_wipes_its_keys_from_the_data_file(tmp_path, channel, pid):
    c = make(tmp_path)
    values = values_for(channel, pid)
    assert save(c, channel, pid, values).status_code == 200
    data_file = tmp_path / "d.json"
    secrets = secret_values(channel, pid, values)
    assert all(s in data_file.read_text(encoding="utf-8") for s in secrets)        # plain at rest without OUTREACH_SECRET_KEY
    r = c.delete(f"/api/channels/{channel}/{pid}")
    assert r.status_code == 200 and r.json()["status"] == "not_configured" and r.json()["saved"] is False
    assert r.json()["values"] == {}
    text = data_file.read_text(encoding="utf-8")
    assert not [s for s in secrets if s in text]
    assert config.config_for(store_of(c), channel, pid) is None
    view = channel_view(c, channel)
    if view["kind"] == "exclusive":
        assert view["active"] is None and view["status"] == "not_configured"
    assert c.delete(f"/api/channels/{channel}/{pid}").status_code == 200           # deleting twice is harmless


@pytest.mark.parametrize("channel,pid", HUB_CARDS)
def test_card_refuses_each_missing_required_field(tmp_path, channel, pid):
    required = required_without_default(channel, pid)
    if not required:
        pytest.skip("no required field without a default")
    c = make(tmp_path)
    for f in required:
        values = {k: v for k, v in values_for(channel, pid).items() if k != f["key"]}
        r = save(c, channel, pid, values)
        assert r.status_code == 400 and f["label"] in r.json()["detail"], (f["key"], r.text)
        blank = {**values_for(channel, pid), f["key"]: "   "}
        assert save(c, channel, pid, blank).status_code == 400, f["key"]          # spaces are not a value
    assert card(c, channel, pid)["saved"] is False


@pytest.mark.parametrize("channel,pid", [k for k in HUB_CARDS if k[1] in ("custom_http",)] + [
    ("whatsapp", "wati"), ("calendar", "calcom")])
def test_fetched_addresses_are_ssrf_checked_on_save(tmp_path, channel, pid):
    c = make(tmp_path)
    key = "api_endpoint" if pid == "wati" else ("api_base" if pid == "calcom" else "url")
    for bad in ("https://169.254.169.254/latest/meta-data/", "https://10.0.0.8/send", "https://127.0.0.1:8000/x",
                "https://[fd00::1]/x", "http://api.example.com/plain-http", "https://user:pw@api.example.com/x"):
        r = save(c, channel, pid, {**values_for(channel, pid), key: bad})
        assert r.status_code == 400, (bad, r.text)
    rebinding = make(tmp_path / "dns", resolver=lambda host: ["93.184.216.34", "10.0.0.9"])
    r = save(rebinding, channel, pid, {**values_for(channel, pid), key: "https://looks-public.example/x"})
    assert r.status_code == 400 and "private" in r.json()["detail"]


def test_unknown_and_legacy_cards_are_not_hub_cards(tmp_path):
    c = make(tmp_path)
    assert c.put("/api/channels/sms/kaleyra", json={"values": {}}).status_code == 404
    assert c.put("/api/channels/telegram/bot", json={"values": {}}).status_code == 404
    assert c.put("/api/channels/lead_data/hunter", json={"values": {"api_key": "x"}}).status_code == 404
    assert c.post("/api/channels/ai/llm/test", json={}).status_code == 404
    assert c.post("/api/channels/sms/active", json={"provider": "kaleyra"}).status_code == 404


# ---------- active provider ----------
@pytest.mark.parametrize("channel", sorted({c for c, _ in EXCLUSIVE}))
def test_exclusive_channel_first_saved_is_active_and_switching_keeps_every_card(tmp_path, channel):
    c = make(tmp_path)
    cards = [p for ch, p in EXCLUSIVE if ch == channel and (ch, p) not in OAUTH_CARDS]
    first, *others = cards
    save(c, channel, first)
    assert channel_view(c, channel)["active"] == first
    for pid in others:
        assert save(c, channel, pid).status_code == 200
        assert channel_view(c, channel)["active"] == first                         # saving never steals "active"
    for pid in [*others, first]:
        r = c.post(f"/api/channels/{channel}/active", json={"provider": pid})
        assert r.status_code == 200 and r.json()["active"] == pid and r.json()["status"] == "configured"
        stored = store_of(c).read("integrations")[registry.channel(channel)["integration_id"]]
        assert stored["values"]["provider"] == pid and stored["mode"] == "api"
        live_values = config.config_for(store_of(c), channel)["values"]
        expected = values_for(channel, pid)
        assert {k: live_values[k] for k in expected if k != "headers"} == {k: v for k, v in expected.items() if k != "headers"}
    for pid in cards:                                                              # nobody's keys were lost on the way
        assert secret_values(channel, pid, config.config_for(store_of(c), channel, pid)["values"]) == \
            secret_values(channel, pid, values_for(channel, pid))


@pytest.mark.parametrize("channel", sorted({c for c, _ in EXCLUSIVE}))
def test_make_active_flag_on_save_and_refusals(tmp_path, channel):
    c = make(tmp_path)
    cards = [p for ch, p in EXCLUSIVE if ch == channel and (ch, p) not in OAUTH_CARDS]
    save(c, channel, cards[0])
    r = save(c, channel, cards[-1], make_active=True)
    assert r.status_code == 200 and r.json()["active"] is True
    assert channel_view(c, channel)["active"] == cards[-1]
    unsaved = next((p for ch, p in EXCLUSIVE if ch == channel and p not in (cards[0], cards[-1])), None)
    if unsaved:
        refused = c.post(f"/api/channels/{channel}/active", json={"provider": unsaved})
        assert refused.status_code == 400 and "Finish setting up" in refused.json()["detail"]
    assert channel_view(c, channel)["active"] == cards[-1]


def test_oauth_calendar_cannot_be_made_active_before_connecting(tmp_path):
    c = make(tmp_path)
    save(c, "calendar", "booking_link")
    save(c, "calendar", "google")
    refused = c.post("/api/channels/calendar/active", json={"provider": "google"})
    assert refused.status_code == 400 and channel_view(c, "calendar")["active"] == "booking_link"
    assert card(c, "calendar", "google")["oauth_connected"] is False


@pytest.mark.parametrize("channel", sorted({c for c, _ in MULTI}))
def test_multi_channel_listens_to_every_saved_card_and_has_no_active_one(tmp_path, channel):
    c = make(tmp_path)
    pids = [p for ch, p in MULTI if ch == channel]
    for pid in pids:
        assert save(c, channel, pid, make_active=True).status_code == 200
    view = channel_view(c, channel)
    assert view["active"] is None and view["status"] == "configured"
    assert all(p["saved"] and p["status"] == "configured" for p in view["providers"])
    r = c.post(f"/api/channels/{channel}/active", json={"provider": pids[0]})
    assert r.status_code == 400 and "already in use" in r.json()["detail"]
    c.delete(f"/api/channels/{channel}/{pids[0]}")
    assert channel_view(c, channel)["status"] == "configured"                     # the others still listen


def test_failed_test_marks_card_error_and_a_resave_clears_it(tmp_path):
    http = FakeHTTP({"control.msg91.com": PermissionError("HTTP 401")})
    c = make(tmp_path, http)
    save(c, "sms", "msg91")
    r = c.post("/api/channels/sms/msg91/test", json={"to": "+91 90000 12345"}).json()
    assert r["ok"] is False and r["last_test"]["ok"] is False
    assert card(c, "sms", "msg91")["status"] == "error" and channel_view(c, "sms")["status"] == "error"
    assert c.post("/api/channels/sms/active", json={"provider": "msg91"}).status_code == 200   # an error card may stay active
    save(c, "sms", "msg91", {"authkey": "msg91-QAFAKE-new-key-Z9z9"})
    assert card(c, "sms", "msg91")["status"] == "configured" and card(c, "sms", "msg91")["last_test"] is None


# ---------- webhook addresses ----------
@pytest.mark.parametrize("channel,pid", WEBHOOK_CARDS)
def test_webhook_address_listing_and_secret_rotation(tmp_path, channel, pid):
    c = make(tmp_path)
    store = store_of(c)
    assert c.get("/api/channels/webhooks").json()["webhooks"] == []                # nothing saved, nothing listed
    if (channel, pid) in OAUTH_CARDS:
        pytest.skip("OAuth calendars have no webhook")
    save(c, channel, pid)
    listing = c.get("/api/channels/webhooks").json()
    rows = [w for w in listing["webhooks"] if (w["channel"], w["provider"]) == (channel, pid)]
    assert len(rows) == 1
    row = rows[0]
    assert row["url"] == f"{listing['public_base_url']}/api/webhooks/{channel}/{pid}?token={row['secret']}"
    assert row["saved"] is True and len(row["secret"]) >= 32
    assert row["basic_auth_url"] == (f"http://hook:{row['secret']}@localhost:8000/api/webhooks/{channel}/{pid}"
                                     if row["auth"] == "token" else "")
    assert row["secret"] not in c.get("/api/channels").text and row["secret"] not in c.get("/api/integrations").text
    old = row["secret"]
    rotated = c.post(f"/api/channels/{channel}/{pid}/rotate-secret")
    assert rotated.status_code == 200
    new = c.get(f"/api/channels/webhooks?channel={channel}&provider={pid}").json()["webhooks"][0]["secret"]
    assert new != old and rotated.json()["url"].endswith(f"token={new}")
    from app.channels.webhooks import verify
    if row["auth"] in ("token", "calcom", "calendly") or (row["auth"] == "meta" and not values_for(channel, pid).get("app_secret")) \
            or (row["auth"] == "svix" and not values_for(channel, pid).get("signing_secret")):
        assert verify(store, channel, pid, "POST", {}, {"token": new}, b"{}") is True
        assert verify(store, channel, pid, "POST", {}, {"token": old}, b"{}") is False
        assert verify(store, channel, pid, "POST", {"X-Webhook-Secret": new}, {}, b"{}") is True
    else:   # a signing secret is configured: only the provider's signature counts, the token alone is not enough
        assert verify(store, channel, pid, "POST", {}, {"token": new}, b"{}") is False


def test_webhook_listing_filters_and_refusals(tmp_path):
    c = make(tmp_path)
    assert c.get("/api/channels/webhooks?channel=sms&provider=fast2sms").status_code == 404     # Fast2SMS has no webhook
    assert c.get("/api/channels/webhooks?channel=sms").status_code == 404
    assert c.post("/api/channels/sms/fast2sms/rotate-secret").status_code == 404
    assert c.post("/api/channels/calls/exotel/rotate-secret").status_code == 404
    # an address can be shown before saving (wizard step 3), but it only works once the card is saved
    path, secret = hook(c, "sms", "twilio_sms")
    form = {"From": "+919000012345", "To": "+14155550100", "Body": "YES", "MessageSid": "SMqa1"}
    assert c.post(path, data=form).status_code == 401
    save(c, "sms", "twilio_sms")
    assert c.post(path, data=form).status_code == 200
    # deleting the card closes the address again; the secret is kept, so re-saving re-opens the same address
    c.delete("/api/channels/sms/twilio_sms")
    assert c.post(path, data=form).status_code == 401
    save(c, "sms", "twilio_sms")
    assert c.post(path, data=form).status_code == 200
    assert hook(c, "sms", "twilio_sms")[1] == secret


def test_every_saved_webhook_card_is_listed_sorted_with_where_to_paste(tmp_path):
    c = make(tmp_path)
    saved = [k for k in WEBHOOK_CARDS if k not in OAUTH_CARDS]
    for channel, pid in saved:
        assert save(c, channel, pid).status_code == 200
    rows = c.get("/api/channels/webhooks").json()["webhooks"]
    assert [(w["channel"], w["provider"]) for w in rows] == sorted(saved)
    assert all(w["where"] and w["title"] and w["channel_title"] for w in rows)
    assert len({w["secret"] for w in rows}) == len(rows)                           # one secret per provider


def test_webhook_addresses_follow_the_public_address_setting(tmp_path):
    c = make(tmp_path)
    save(c, "inbound_email", "postmark_inbound")
    c.put("/api/settings", json={"public_base_url": "https://outreach.hirewise-outreach.example/"})
    row = c.get("/api/channels/webhooks").json()["webhooks"][0]
    assert row["url"].startswith("https://outreach.hirewise-outreach.example/api/webhooks/inbound_email/postmark_inbound?token=")
    assert row["basic_auth_url"] == (f"https://hook:{row['secret']}@outreach.hirewise-outreach.example"
                                     "/api/webhooks/inbound_email/postmark_inbound")
    oauth = c.get("/api/channels").json()["oauth_redirects"]
    assert oauth["google"] == "https://outreach.hirewise-outreach.example/api/oauth/google/callback"


def test_rotating_one_secret_leaves_the_others_alone(tmp_path):
    c = make(tmp_path)
    for channel, pid in (("sms", "msg91"), ("sms", "twilio_sms"), ("whatsapp", "gupshup")):
        save(c, channel, pid)
    before = {(w["channel"], w["provider"]): w["secret"] for w in c.get("/api/channels/webhooks").json()["webhooks"]}
    c.post("/api/channels/sms/msg91/rotate-secret")
    after = {(w["channel"], w["provider"]): w["secret"] for w in c.get("/api/channels/webhooks").json()["webhooks"]}
    assert after[("sms", "msg91")] != before[("sms", "msg91")]
    assert {k: v for k, v in after.items() if k != ("sms", "msg91")} == {k: v for k, v in before.items() if k != ("sms", "msg91")}


# ---------- roles: integrations are owner-only ----------
def test_callers_and_viewers_cannot_touch_integrations_or_see_webhook_secrets(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password="qa-owner-password", resolver=PUBLIC,
                              channel_transport=FakeHTTP()))
    owner = {"Authorization": "Bearer " + c.post("/api/login", json={"password": "qa-owner-password"}).json()["token"]}
    for email, role in (("caller.qa@hirewise-outreach.example", "caller"), ("viewer.qa@hirewise-outreach.example", "viewer")):
        assert c.post("/api/users", json={"email": email, "role": role, "password": "a-long-qa-password"}, headers=owner).status_code == 200
    c.put("/api/channels/sms/msg91", json={"values": values_for("sms", "msg91")}, headers=owner)
    for email in ("caller.qa@hirewise-outreach.example", "viewer.qa@hirewise-outreach.example"):
        token = c.post("/api/login", json={"email": email, "password": "a-long-qa-password"}).json()["token"]
        h = {"Authorization": f"Bearer {token}"}
        assert c.get("/api/channels", headers=h).status_code == 200
        assert "msg91-QAFAKE-authkey-I5j6" not in c.get("/api/channels", headers=h).text
        for method, path, body in (("put", "/api/channels/sms/msg91", {"values": {}}), ("delete", "/api/channels/sms/msg91", None),
                                   ("post", "/api/channels/sms/active", {"provider": "msg91"}),
                                   ("post", "/api/channels/sms/msg91/test", {}), ("post", "/api/channels/sms/msg91/rotate-secret", None),
                                   ("put", "/api/channels/live", {"live_sms": True}), ("get", "/api/channels/webhooks", None),
                                   ("put", "/api/integrations/sending_domain", {"mode": "api", "values": {}}),
                                   ("post", "/api/integrations/sending_domain/test", {"to": "x@y.example"})):
            kwargs = {"headers": h, **({"json": body} if body is not None else {})}
            assert getattr(c, method)(path, **kwargs).status_code == 403, (email, method, path)
    assert store_of(c).read("settings").get("live_sms") is None
    assert json.loads((tmp_path / "d.json").read_text(encoding="utf-8"))["integrations"]["sms"]["values"]["provider"] == "msg91"
