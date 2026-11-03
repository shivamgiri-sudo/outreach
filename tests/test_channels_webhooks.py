"""Webhooks (secret / signature checks, normalisation, processor hand-off) and IMAP reply capture."""
import base64
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from app.channels import config
from app.channels.imap_poll import poll_imap
from app.channels.inbound_parse import delivery_events, email_webhook, whatsapp_webhook
from app.main import create_app
from app.store import Store
from tests.channel_fakes import FakeIMAP, make_client, raw_email


class Recorder:
    def __init__(self, fail_on=None):
        self.items, self.fail_on = [], fail_on

    def __call__(self, store, item):
        if self.fail_on and self.fail_on in json.dumps(item):
            raise RuntimeError("boom")
        self.items.append(item)
        return {"matched": True}


def hook(c, channel, provider):
    data = c.get(f"/api/channels/webhooks?channel={channel}&provider={provider}").json()["webhooks"][0]
    return data["url"].split("8000", 1)[1], data["secret"]


def setup(tmp_path, channel, provider, values=None, **kw):
    inbound, delivery = Recorder(), Recorder()
    c = make_client(tmp_path, inbound_processor=inbound, delivery_processor=delivery, **kw)
    c.put(f"/api/channels/{channel}/{provider}", json={"values": values or {}})
    url, secret = hook(c, channel, provider)
    return c, url, secret, inbound, delivery


# ---- shared-secret webhooks ----
def test_token_good_bad_missing_and_non_ascii(tmp_path):
    c, url, secret, inbound, _ = setup(tmp_path, "inbound_email", "postmark_inbound")
    path = url.split("?")[0]
    payload = {"FromFull": {"Email": "Asha@Acme-Retail.in"}, "To": "replies@x.in", "Subject": "Re: hi", "TextBody": "Yes please",
               "MessageID": "pm-1", "Headers": [{"Name": "Message-ID", "Value": "<a@b>"}, {"Name": "In-Reply-To", "Value": "<m1@x>"}]}
    assert c.post(url, json=payload).status_code == 200
    assert c.post(path, json=payload, headers={"X-Webhook-Secret": secret}).status_code == 200
    basic = base64.b64encode(f"postmark:{secret}".encode()).decode()
    assert c.post(path, json=payload, headers={"Authorization": f"Basic {basic}"}).status_code == 200
    assert c.post(path, json=payload).status_code == 401
    assert c.post(path + "?token=wrong", json=payload).status_code == 401
    assert c.post(path, json=payload, headers={"X-Webhook-Secret": "café✓".encode("utf-8")}).status_code == 401
    assert c.post(path, json=payload, headers={"Authorization": "Basic %%%"}).status_code == 401
    assert len(inbound.items) == 3
    msg = inbound.items[0]
    assert msg["channel"] == "email" and msg["from_email"] == "asha@acme-retail.in" and msg["text"] == "Yes please"
    assert msg["in_reply_to"] == "<m1@x>" and msg["provider"] == "postmark_inbound" and msg["raw_id"] == "pm-1"
    assert set(msg) == {"channel", "from_email", "from_phone", "to", "subject", "text", "html", "message_id", "in_reply_to",
                        "references", "received_at", "provider", "raw_id"}


def test_unsaved_provider_and_unknown_route_are_refused(tmp_path):
    c = make_client(tmp_path)
    url, _ = hook(c, "inbound_email", "sendgrid_inbound")        # address exists, provider not saved yet
    assert c.post(url, data={"from": "a@b.in", "text": "hi"}).status_code == 401
    assert c.post("/api/webhooks/nope/thing?token=x", json={}).status_code in (401, 404)


def test_middleware_opens_webhooks_only_with_the_secret(tmp_path):
    inbound = Recorder()
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password="pw", inbound_processor=inbound,
                              resolver=lambda h: ["93.184.216.34"]))
    token = c.post("/api/login", json={"password": "pw"}).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    c.put("/api/channels/sms/twilio_sms", json={"values": {"account_sid": "AC1", "auth_token": "t", "from_number": "+1415"}}, headers=auth)
    url = c.get("/api/channels/webhooks?channel=sms&provider=twilio_sms", headers=auth).json()["webhooks"][0]["url"].split("8000", 1)[1]
    form = {"From": "+919812345678", "To": "+14155550100", "Body": "STOP", "MessageSid": "SM9"}
    assert c.post(url, data=form).status_code == 200                                  # no login needed: secret matches
    assert c.post(url.split("?")[0] + "?token=nope", data=form).status_code == 401    # middleware refuses
    assert c.get("/api/channels").status_code == 401                                  # everything else still needs login
    assert inbound.items[0]["channel"] == "sms" and inbound.items[0]["from_phone"] == "+919812345678"
    assert inbound.items[0]["text"] == "STOP"


def test_sendgrid_inbound_parse_multipart(tmp_path):
    c, url, _, inbound, _ = setup(tmp_path, "inbound_email", "sendgrid_inbound")
    files = {"from": (None, "Asha Rao <asha@acme-retail.in>"), "to": (None, "reply@reply.x.in"), "subject": (None, "Re: hiring"),
             "text": (None, "Interested, call me"), "headers": (None, "Message-ID: <s1@acme>\nIn-Reply-To: <m1@x>\nReferences: <m0@x> <m1@x>\n")}
    assert c.post(url, files=files).status_code == 200
    msg = inbound.items[0]
    assert msg["from_email"] == "asha@acme-retail.in" and msg["text"] == "Interested, call me"
    assert msg["message_id"] == "<s1@acme>" and msg["references"] == ["<m0@x>", "<m1@x>"]


def test_processor_failure_returns_500_so_the_provider_retries(tmp_path):
    inbound = Recorder(fail_on="boom-please")
    c = make_client(tmp_path, inbound_processor=inbound)
    c.put("/api/channels/inbound_email/postmark_inbound", json={"values": {}})
    url, _ = hook(c, "inbound_email", "postmark_inbound")
    r = c.post(url, json={"From": "a@b.in", "TextBody": "boom-please"})
    assert r.status_code == 500 and "boom" not in r.text


# ---- Meta WhatsApp ----
META = {"phone_number_id": "123", "access_token": "EAA", "app_secret": "meta-app-secret"}
META_PAYLOAD = {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
    "metadata": {"display_phone_number": "917000000000"},
    "messages": [{"from": "919812345678", "id": "wamid.IN1", "timestamp": "1762146000", "type": "text", "text": {"body": "Yes, send it"},
                  "context": {"id": "wamid.OUT1"}},
                 {"from": "919812345678", "id": "wamid.IN2", "timestamp": "1762146060", "type": "button", "button": {"text": "STOP"}}]}}]}]}


def test_meta_get_verify_challenge(tmp_path):
    c, url, secret, _, _ = setup(tmp_path, "whatsapp", "meta_cloud", META)
    path = url.split("?")[0]
    ok = c.get(path, params={"hub.mode": "subscribe", "hub.verify_token": secret, "hub.challenge": "1158201444"})
    assert ok.status_code == 200 and ok.text == "1158201444"
    assert c.get(path, params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"}).status_code == 403


def test_meta_signature_required_when_app_secret_is_set(tmp_path):
    c, url, _, inbound, _ = setup(tmp_path, "whatsapp", "meta_cloud", META)
    path = url.split("?")[0]
    body = json.dumps(META_PAYLOAD).encode()
    good = "sha256=" + hmac.new(b"meta-app-secret", body, hashlib.sha256).hexdigest()
    assert c.post(path, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": good}).status_code == 200
    assert c.post(path, content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=00"}).status_code == 401
    assert c.post(url, content=body, headers={"Content-Type": "application/json"}).status_code == 401   # token alone is not enough
    assert [m["text"] for m in inbound.items] == ["Yes, send it", "STOP"]
    first = inbound.items[0]
    assert first["channel"] == "whatsapp" and first["from_phone"] == "+919812345678" and first["in_reply_to"] == "wamid.OUT1"
    assert first["received_at"] == "2025-11-03T05:00:00+00:00"


def test_other_whatsapp_payloads_normalise():
    gup = whatsapp_webhook("gupshup", {"type": "message", "timestamp": 1762146000000, "payload": {
        "id": "g1", "source": "919812345678", "type": "text", "payload": {"text": "haan"}, "sender": {"phone": "919812345678"}}})
    assert gup[0]["from_phone"] == "+919812345678" and gup[0]["text"] == "haan"
    ik = whatsapp_webhook("interakt", {"type": "message_received", "data": {"customer": {"country_code": "+91", "phone_number": "9812345678"},
                                                                           "message": {"id": "i1", "message": "Yes"}}})
    assert ik[0]["from_phone"] == "+919812345678" and ik[0]["raw_id"] == "i1"
    wati = whatsapp_webhook("wati", {"eventType": "message", "waId": "919812345678", "text": "ok", "whatsappMessageId": "w1", "owner": False})
    assert wati[0]["message_id"] == "w1"
    assert whatsapp_webhook("wati", {"eventType": "message", "waId": "91", "text": "sent by us", "owner": True}) == []


# ---- Resend (Svix) and delivery events ----
def svix_headers(secret_b64: str, body: bytes, stamp: int = None):
    stamp = stamp or int(time.time())
    sig = base64.b64encode(hmac.new(base64.b64decode(secret_b64), f"msg_1.{stamp}.".encode() + body, hashlib.sha256).digest()).decode()
    return {"svix-id": "msg_1", "svix-timestamp": str(stamp), "svix-signature": f"v1,{sig}", "Content-Type": "application/json"}


def test_resend_events_with_svix_signature(tmp_path):
    key = base64.b64encode(b"resend-signing-key-bytes").decode()
    c, url, _, _, delivery = setup(tmp_path, "email_events", "resend_events", {"signing_secret": f"whsec_{key}"})
    path = url.split("?")[0]
    body = json.dumps({"type": "email.bounced", "created_at": "2026-11-03T05:00:00Z",
                       "data": {"email_id": "em_1", "to": ["Asha <asha@acme-retail.in>"]}}).encode()
    assert c.post(path, content=body, headers=svix_headers(key, body)).status_code == 200
    assert c.post(path, content=body, headers=svix_headers(key, body, stamp=int(time.time()) - 3600)).status_code == 401
    assert c.post(url, content=body, headers={"Content-Type": "application/json"}).status_code == 401
    assert delivery.items == [{"type": "bounce", "email": "asha@acme-retail.in", "provider_id": "em_1", "at": "2026-11-03T05:00:00+00:00"}]


def test_delivery_event_normalisers():
    sg = delivery_events("sendgrid_events", [
        {"email": "a@x.in", "event": "bounce", "type": "bounce", "sg_message_id": "abc.filter1", "timestamp": 1762146000},
        {"email": "b@x.in", "event": "bounce", "type": "blocked"}, {"email": "c@x.in", "event": "spamreport"},
        {"email": "d@x.in", "event": "open"}])
    assert [(e["type"], e["email"]) for e in sg] == [("bounce", "a@x.in"), ("complaint", "c@x.in")] and sg[0]["provider_id"] == "abc"
    pm = delivery_events("postmark_events", {"RecordType": "Bounce", "Type": "HardBounce", "Email": "a@x.in", "MessageID": "p1",
                                             "BouncedAt": "2026-11-03T05:00:00Z"})
    assert pm[0]["type"] == "bounce" and pm[0]["provider_id"] == "p1"
    assert delivery_events("postmark_events", {"RecordType": "Bounce", "Type": "SoftBounce", "Email": "a@x.in"}) == []
    custom = delivery_events("custom_webhook", [{"kind": "hard_bounce", "rcpt": "x@y.in"}],
                             {"type_path": "kind", "email_path": "rcpt", "bounce_value": "hard_bounce"})
    assert custom[0]["type"] == "bounce" and custom[0]["email"] == "x@y.in"


def test_sendgrid_event_webhook_hands_events_to_processor(tmp_path):
    c, url, _, _, delivery = setup(tmp_path, "email_events", "sendgrid_events")
    assert c.post(url, json=[{"email": "a@x.in", "event": "delivered", "sg_message_id": "m1.x"}]).json()["events"] == 1
    assert delivery.items[0]["type"] == "delivered"


def test_resend_inbound_normaliser():
    msgs = email_webhook("resend_inbound", {"type": "email.received", "data": {
        "email_id": "r1", "from": "Asha <asha@acme-retail.in>", "to": ["reply@x.in"], "subject": "Re", "text": "yes",
        "headers": {"In-Reply-To": "<m1@x>"}}})
    assert msgs[0]["from_email"] == "asha@acme-retail.in" and msgs[0]["in_reply_to"] == "<m1@x>" and msgs[0]["to"] == "reply@x.in"


# ---- custom inbound webhook ----
def test_custom_webhook_mapping_preview_and_delivery(tmp_path):
    mapping = {"phone_path": "contact.wa", "text_path": "msg.body", "message_id_path": "msg.id"}
    c, url, _, inbound, _ = setup(tmp_path, "whatsapp", "custom_webhook", mapping)
    sample = {"contact": {"wa": "919812345678"}, "msg": {"body": "Interested", "id": "x1"}}
    preview = c.post("/api/channels/whatsapp/custom_webhook/test", json={"sample": json.dumps(sample)}).json()
    assert preview["ok"] is True and preview["extracted"][0]["from_phone"] == "+919812345678"
    wrong = c.post("/api/channels/whatsapp/custom_webhook/test", json={"sample": json.dumps(sample), "mapping": {"text_path": "nope"}}).json()
    assert wrong["ok"] is False
    assert c.post(url, json=sample).status_code == 200 and inbound.items[0]["text"] == "Interested"


# ---- IMAP ----
def imap_store(tmp_path, mark_seen="yes"):
    store = Store(tmp_path / "s.json")
    config.save_provider(store, "inbound_email", "imap", {"imap_host": "imap.example.in", "imap_port": "993",
                                                          "username": "priya@mail.example.in", "password": "app-pass",
                                                          "folder": "INBOX", "mark_seen": mark_seen})
    return store


def test_imap_poll_normalises_marks_seen_and_never_repeats(tmp_path):
    store, fake, seen = imap_store(tmp_path), FakeIMAP({101: raw_email(101), 102: raw_email(102, body="Not interested")}), Recorder()
    out = poll_imap(store, seen, lambda host, port: fake)
    assert out["ok"] is True and out["processed"] == 2
    assert [m["text"].strip() for m in seen.items] == ["Yes please send it", "Not interested"]
    msg = seen.items[0]
    assert msg["channel"] == "email" and msg["from_email"] == "asha@acme-retail.in" and msg["provider"] == "imap"
    assert msg["in_reply_to"] == "<m1@mail.example.in>" and msg["raw_id"] == "101" and msg["received_at"].startswith("2026-11-03T04:30")
    assert fake.selected == ('"INBOX"', False) and [a[0] for a in fake.stored] == ["101", "102"]
    assert "SINCE" in fake.searches[0]                                    # first check only looks back a few days
    fake.unseen = {101, 102}                                              # even if marked unread again …
    assert poll_imap(store, seen, lambda host, port: fake)["processed"] == 0 and len(seen.items) == 2
    assert "UID" in fake.searches[1] and "103:*" in fake.searches[1]


def test_imap_stops_at_a_failure_and_retries_later(tmp_path):
    store = imap_store(tmp_path)
    fake = FakeIMAP({1: raw_email(1), 2: raw_email(2, body="boom-please"), 3: raw_email(3)})
    out = poll_imap(store, Recorder(fail_on="boom-please"), lambda host, port: fake)
    assert out["ok"] is False and out["processed"] == 1 and fake.unseen == {2, 3}
    retry = Recorder()
    assert poll_imap(store, retry, lambda host, port: fake)["processed"] == 2


def test_imap_read_only_mode_and_bad_password(tmp_path):
    store, fake = imap_store(tmp_path, mark_seen="no"), FakeIMAP({5: raw_email(5)})
    assert poll_imap(store, Recorder(), lambda host, port: fake)["processed"] == 1
    assert fake.selected == ('"INBOX"', True) and fake.stored == []
    bad = poll_imap(store, Recorder(), lambda host, port: FakeIMAP({}, password="other"))
    assert bad["ok"] is False and "app password" in bad["message"]


def test_imap_test_and_poll_endpoints(tmp_path):
    fake, seen = FakeIMAP({7: raw_email(7)}), Recorder()
    c = make_client(tmp_path, imap_factory=lambda host, port: fake, inbound_processor=seen)
    c.put("/api/channels/inbound_email/imap", json={"values": {"imap_host": "imap.example.in", "username": "u", "password": "app-pass"}})
    test = c.post("/api/channels/inbound_email/imap/test", json={}).json()
    assert test["ok"] is True and "1 unread" in test["message"]
    assert c.post("/api/channels/inbound_email/imap/poll").json()["processed"] == 1 and seen.items[0]["raw_id"] == "7"


def test_basic_auth_form_of_the_address_works_and_bad_headers_do_not_crash(tmp_path):
    c, url, secret, inbound, _ = setup(tmp_path, "inbound_email", "postmark_inbound")
    hook = c.get("/api/channels/webhooks?channel=inbound_email&provider=postmark_inbound").json()["webhooks"][0]
    assert hook["basic_auth_url"] == f"http://hook:{secret}@localhost:8000/api/webhooks/inbound_email/postmark_inbound"
    basic = base64.b64encode(f"hook:{secret}".encode()).decode()
    path = url.split("?")[0]
    assert c.post(path, json={"From": "a@b.in", "TextBody": "hi"}, headers={"Authorization": f"Basic {basic}"}).status_code == 200
    assert c.post(path, content=b"{}", headers={"Content-Type": "application/json", "Content-Length": "2", "X-Webhook-Secret": "x"}).status_code == 401
    meta = c.get("/api/channels/webhooks?channel=whatsapp&provider=meta_cloud").json()["webhooks"][0]
    assert meta["basic_auth_url"] == ""      # signature-based providers do not need it


def test_gate_tolerates_a_malformed_content_length():
    import asyncio
    from types import SimpleNamespace
    from app.channels.webhooks import gate_allows
    request = SimpleNamespace(url=SimpleNamespace(path="/api/webhooks/inbound_email/postmark_inbound"), method="POST",
                              headers={"content-length": "abc"}, query_params={})
    assert asyncio.run(gate_allows(None, request)) is False
