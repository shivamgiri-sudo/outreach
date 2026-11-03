"""QA: the Custom HTTP connector (send through any API) and the Custom webhook (receive from any provider).

Custom HTTP: every placeholder in a JSON body survives quotes, backslashes, newlines, tabs, Devanagari and emoji; the URL
and form bodies are URL-encoded; success_path / success_value / id_path; SSRF refusal at save and at send time; secret
headers masked in the preview; the reply preview is cut at 2 KB. Custom webhook: field mapping against realistic payloads
(an SMS gateway batch of 124 messages, an Exotel-style form post, Brevo / Mailgun-shaped JSON), the paste-a-sample preview.
"""
import json
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from app.channels.catalog_common import PLACEHOLDERS
from app.channels.custom_http import RESPONSE_PREVIEW_BYTES, call_custom, render_custom, response_preview
from tests.channel_fakes import FakeHTTP
from tests.qa.integrations_hub_support import (PUBLIC, Recorder, card, fixture_json, fixture_text, hook, make, masked,
                                               save, store_of, values_for)

MESSY = 'Rohit "RV" Verma \\ HR\nLine two\t✓ नमस्ते 🙏 </script> & 50% off'
ALL_PLACEHOLDERS_JSON = "{" + ", ".join(f'"{p}": "{{{{ {p} }}}}"' for p in PLACEHOLDERS) + "}"


def cfg(**over):
    return {"method": "POST", "url": "https://api.sms-gateway.example/v1/send",
            "headers": [{"key": "Authorization", "value": "Token sg-QAFAKE-O1p2", "secret": True},
                        {"key": "X-Client", "value": "mcn-outreach", "secret": False}],
            "body_type": "json", "body_template": ALL_PLACEHOLDERS_JSON, **over}


# ---------- templates and escaping ----------
def test_every_placeholder_is_json_escaped_and_round_trips():
    variables = {p: f"{p}: {MESSY}" for p in PLACEHOLDERS}
    rendered = render_custom(cfg(), variables)
    assert json.loads(rendered["body"]) == variables                               # exact text, nothing lost or doubled
    assert rendered["headers"]["Content-Type"] == "application/json"
    assert "नमस्ते 🙏" in rendered["body"]                                       # unicode kept readable (ensure_ascii off)


def test_missing_variables_become_empty_strings_and_json_stays_valid():
    body = json.loads(render_custom(cfg(), {"to": "+919000012345"})["body"])
    assert body["to"] == "+919000012345" and body["name"] == "" and body["join_url"] == ""


def test_url_placeholders_are_percent_encoded_so_they_cannot_add_parameters():
    rendered = render_custom(cfg(method="GET", url="https://api.sms-gateway.example/send?to={{phone}}&msg={{text}}"),
                             {"phone": "+91 90000 12345", "text": "Hi & welcome=yes?#frag/../x"})
    query = parse_qs(urlsplit(rendered["url"]).query)
    assert query == {"to": ["+91 90000 12345"], "msg": ["Hi & welcome=yes?#frag/../x"]}
    assert rendered["body"] is None                                                # GET never carries a body


def test_form_body_is_url_encoded_with_its_content_type():
    template = values_for("sms", "custom_http")["body_template"]
    rendered = render_custom(cfg(body_type="form", body_template=template),
                             {"phone": "+919000012345", "text": MESSY, "template": "first_after_yes"})
    assert rendered["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
    assert parse_qs(rendered["body"]) == {"to": ["+919000012345"], "message": [MESSY], "sender": ["BMIHRS"],
                                          "dlt_template": ["first_after_yes"]}


def test_body_type_none_and_explicit_content_type_are_respected():
    assert render_custom(cfg(body_type="none"), {"text": "x"})["body"] is None
    own = cfg(headers=[{"key": "content-type", "value": "application/vnd.api+json"}])
    assert render_custom(own, {})["headers"] == {"content-type": "application/vnd.api+json"}


@pytest.mark.parametrize("template,why", [
    ('{"to": "{{phone}}", "msg": "{{sms_text}}"}', "Unknown placeholder"),           # typo in a placeholder name
    ('{"to": {{phone}}}', "not valid JSON"),                                       # text placeholder outside quotes
    ('{"to": "{{phone}}",}', "not valid JSON"),                                    # trailing comma
])
def test_broken_templates_are_refused_on_save_with_a_plain_reason(tmp_path, template, why):
    c = make(tmp_path)
    r = save(c, "sms", "custom_http", {**values_for("sms", "custom_http"), "body_type": "json", "body_template": template})
    assert r.status_code == 400 and why in r.json()["detail"]
    assert card(c, "sms", "custom_http")["saved"] is False


def test_unknown_placeholder_in_url_or_header_is_refused_too(tmp_path):
    c = make(tmp_path)
    base = values_for("sms", "custom_http")
    assert save(c, "sms", "custom_http", {**base, "url": "https://api.sms-gateway.example/x?k={{api_key}}"}).status_code == 400
    bad_header = {**base, "headers": [{"key": "Authorization", "value": "Token {{secret}}", "secret": True}]}
    assert save(c, "sms", "custom_http", bad_header).status_code == 400
    with_break = {**base, "headers": [{"key": "Authorization", "value": "Token abc\r\nX-Evil: 1", "secret": True}]}
    r = save(c, "sms", "custom_http", with_break)
    assert r.status_code == 400 and "line breaks" in r.json()["detail"]


def test_calendar_custom_http_has_its_own_join_url_field(tmp_path):
    http = FakeHTTP({"api.meetings.example": {"created": True, "meeting": {"id": 8812, "join_url": "https://meet.meetings.example/j/8812"}}})
    c = make(tmp_path, http)
    save(c, "calendar", "custom_http")
    r = c.post("/api/channels/calendar/custom_http/test", json={}).json()
    assert r["ok"] is True
    sent = json.loads(http.calls[0]["raw"])
    assert sent and all("{{" not in str(v) for v in sent.values())


# ---------- success_path / success_value / id_path ----------
@pytest.mark.parametrize("success_path,success_value,reply,ok", [
    ("", "", {}, True),                                                            # empty = any 2xx counts
    ("status", "success", {"status": "SUCCESS"}, True),                            # case-insensitive
    ("status", "success", {"status": "failed", "error": "DLT template not approved"}, False),
    ("ok", "", {"ok": True}, True),
    ("ok", "", {"ok": False}, False),
    ("ok", "true", {"ok": True}, True),                                            # booleans compare as true/false
    ("data.0.accepted", "", {"data": [{"accepted": 1}]}, True),
    ("data.1.accepted", "", {"data": [{"accepted": 1}]}, False),                   # index out of range
    ("result.code", "200", {"result": {"code": 200}}, True),                       # numbers compare as text
    ("missing.path", "", {"status": "ok"}, False),
    ("_list.0.status", "queued", {"_list": [{"status": "queued"}]}, True),         # a JSON array reply
])
def test_success_rules(success_path, success_value, reply, ok):
    out = call_custom(cfg(success_path=success_path, success_value=success_value, id_path="data.0.id"),
                      {"phone": "+919000012345", "text": "hi"}, FakeHTTP({"": reply}), PUBLIC)
    assert out["ok"] is ok
    assert ("error" in out) is (not ok)


def test_id_path_reads_numbers_and_nested_lists():
    reply = {"status": "success", "data": [{"id": 4400123}, {"id": 4400124}]}
    out = call_custom(cfg(success_path="status", success_value="success", id_path="data.0.id"), {}, FakeHTTP({"": reply}), PUBLIC)
    assert out["provider_id"] == "4400123"
    out = call_custom(cfg(id_path="nope.id"), {}, FakeHTTP({"": reply}), PUBLIC)
    assert out["provider_id"] == ""


# ---------- SSRF ----------
@pytest.mark.parametrize("to", ["10.0.0.1", "169.254.169.254", "127.0.0.1", "localhost"])
def test_placeholder_in_the_host_cannot_reach_private_addresses(to):
    http = FakeHTTP()
    resolver = lambda host: [host] if host[0].isdigit() else ["127.0.0.1"]        # noqa: E731
    out = call_custom(cfg(url="https://{{to}}/send"), {"to": to}, http, resolver)
    assert out["ok"] is False and http.calls == [] and out["preview"] is None


def test_placeholder_cannot_smuggle_userinfo_or_a_new_host():
    http = FakeHTTP()
    out = call_custom(cfg(url="https://api.sms-gateway.example/{{text}}"), {"text": "@169.254.169.254/x"}, http, PUBLIC)
    assert out["ok"] is True
    assert urlsplit(http.calls[0]["url"]).hostname == "api.sms-gateway.example"
    assert unquote(urlsplit(http.calls[0]["url"]).path) == "/@169.254.169.254/x"


def test_dns_rebinding_at_send_time_is_refused(tmp_path):
    answers = {"n": 0}

    def resolver(host):          # public while saving, private when the message goes out
        answers["n"] += 1
        return ["93.184.216.34"] if answers["n"] == 1 else ["93.184.216.34", "10.1.2.3"]
    http = FakeHTTP()
    c = make(tmp_path, http, resolver=resolver)
    assert save(c, "sms", "custom_http").status_code == 200
    r = c.post("/api/channels/sms/custom_http/test", json={"to": "+919000012345"}).json()
    assert r["ok"] is False and "private" in r["message"].lower() and http.calls == []


# ---------- preview: keys hidden, reply cut ----------
def test_send_test_preview_hides_secret_headers_and_shows_the_rest(tmp_path):
    http = FakeHTTP({"api.sms-gateway.example": {"status": "success", "data": [{"id": "s-1"}],
                                                 "_headers": {"Set-Cookie": "session=provider-cookie-qa"}}})
    c = make(tmp_path, http)
    values = {**values_for("sms", "custom_http"),
              "headers": [{"key": "Authorization", "value": "Token sg-QAFAKE-O1p2", "secret": True},
                          {"key": "X-Api-Key", "value": "xk-QAFAKE-9a8b7c", "secret": True},
                          {"key": "X-Client", "value": "mcn-outreach", "secret": False}]}
    save(c, "sms", "custom_http", values)
    r = c.post("/api/channels/sms/custom_http/test", json={"to": "+91 90000 12345"})
    out = r.json()
    assert out["ok"] is True
    assert out["preview"]["headers"]["Authorization"] == masked("Token sg-QAFAKE-O1p2")
    assert out["preview"]["headers"]["X-Api-Key"] == masked("xk-QAFAKE-9a8b7c")
    assert out["preview"]["headers"]["X-Client"] == "mcn-outreach"
    assert "sg-QAFAKE-O1p2" not in r.text and "xk-QAFAKE-9a8b7c" not in r.text
    assert "provider-cookie-qa" not in out["response"]                            # reply headers are not echoed
    assert http.calls[0]["headers"]["Authorization"] == "Token sg-QAFAKE-O1p2"    # the real key still went out
    assert parse_qs(out["preview"]["body"])["to"] == ["+91 90000 12345"]


def test_reply_preview_is_cut_at_2kb_ascii():
    text = response_preview({"log": "x" * 5000})
    assert text.endswith("… (cut)") and len(text.removesuffix("… (cut)").encode("utf-8")) == RESPONSE_PREVIEW_BYTES
    assert response_preview({"ok": True}) == '{"ok": true}'


@pytest.mark.parametrize("error,needle", [(PermissionError("HTTP 401"), "refused"), (OSError("HTTP 500: upstream"), "error")])
def test_provider_errors_keep_the_preview_for_debugging(error, needle):
    out = call_custom(cfg(), {"to": "+919000012345"}, FakeHTTP({"": error}), PUBLIC)
    assert out["ok"] is False and needle in out["error"] and out["preview"]["headers"]["Authorization"].startswith("••••")


# ---------- Custom webhook: field mapping ----------
SMS_MAP = {"phone_path": "sender.msisdn", "text_path": "content.text", "message_id_path": "msg_id"}


def test_sms_gateway_batch_through_a_custom_webhook(tmp_path):
    seen = Recorder()
    c = make(tmp_path, inbound_processor=seen)
    assert save(c, "sms", "custom_webhook", SMS_MAP).status_code == 200
    batch = fixture_json("sms_gateway_batch.json")
    path, _ = hook(c, "sms", "custom_webhook")
    r = c.post(path, content=json.dumps(batch, ensure_ascii=False).encode("utf-8"),
               headers={"Content-Type": "application/json; charset=utf-8"})
    assert r.status_code == 200 and r.json() == {"ok": True, "messages": len(seen.items), "matched": 0}
    skipped = {"gw-blank-text", "gw-no-sender", "gw-shortcode"}                     # no text, no sender, a 5-digit shortcode
    assert {m["message_id"] for m in seen.items} == {b["msg_id"] for b in batch} - skipped
    assert all(m["from_phone"].startswith("+91") and len(m["from_phone"]) == 13 for m in seen.items)
    by_id = {m["message_id"]: m for m in seen.items}
    assert by_id["gw-2026110400002"]["from_phone"] == "+919000011002"              # 0-prefixed national number
    assert by_id["gw-2026110400001"]["text"] == "Haan ji, bhej dijiye"
    assert all(m["channel"] == "sms" and m["provider"] == "custom_webhook" for m in seen.items)


def test_custom_webhook_refuses_wrong_or_missing_secret_and_unsaved_card(tmp_path):
    seen = Recorder()
    c = make(tmp_path, inbound_processor=seen)
    path, secret = hook(c, "sms", "custom_webhook")
    body = {"sender": {"msisdn": "+919000012345"}, "content": {"text": "YES"}, "msg_id": "x1"}
    assert c.post(path, json=body).status_code == 401                              # not saved yet
    save(c, "sms", "custom_webhook", SMS_MAP)
    bare = path.split("?")[0]
    assert c.post(bare, json=body).status_code == 401
    assert c.post(bare + "?token=" + "0" * len(secret), json=body).status_code == 401
    assert c.post(bare, json=body, headers={"X-Webhook-Secret": secret}).status_code == 200
    assert c.post(bare, json=body, auth=("hook", secret)).status_code == 200      # basic-auth address form
    assert len(seen.items) == 2


def test_exotel_style_form_post_through_custom_mapping(tmp_path):
    seen = Recorder()
    c = make(tmp_path, inbound_processor=seen)
    save(c, "sms", "custom_webhook", {"phone_path": "From", "text_path": "Body", "message_id_path": "SmsSid"})
    path, _ = hook(c, "sms", "custom_webhook")
    r = c.post(path, content=fixture_text("exotel_sms_inbound.form").strip(),
               headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200 and r.json()["messages"] == 1
    assert seen.items[0]["from_phone"] == "+919000012346" and seen.items[0]["text"] == "STOP"


def test_whatsapp_custom_mapping_reads_a_nested_cloud_api_payload(tmp_path):
    seen = Recorder()
    c = make(tmp_path, inbound_processor=seen)
    mapping = {"phone_path": "entry.0.changes.0.value.messages.0.from",
               "text_path": "entry.0.changes.0.value.messages.0.text.body",
               "message_id_path": "entry.0.changes.0.value.messages.0.id"}
    save(c, "whatsapp", "custom_webhook", mapping)
    path, _ = hook(c, "whatsapp", "custom_webhook")
    assert c.post(path, json=fixture_json("webhook_payloads.json")["dialog360_whatsapp_inbound"]).status_code == 200
    msg = seen.items[0]
    assert msg["from_phone"] == "+919000012350" and "कल शाम 5 बजे" in msg["text"] and msg["message_id"].startswith("wamid.")


def test_inbound_email_custom_mapping_with_brevo_shaped_json(tmp_path):
    seen = Recorder()
    c = make(tmp_path, inbound_processor=seen)
    payload = fixture_json("webhook_payloads.json")["brevo_inbound"]
    mapping = {"from_path": "items.0.From.Address", "text_path": "items.0.ExtractedMarkdownMessage",
               "subject_path": "items.0.Subject", "message_id_path": "items.0.MessageId", "in_reply_to_path": "items.0.InReplyTo"}
    save(c, "inbound_email", "custom_webhook", mapping)
    path, _ = hook(c, "inbound_email", "custom_webhook")
    assert c.post(path, json=payload).status_code == 200
    msg = seen.items[0]
    assert msg["from_email"] == "venkatesh.s@zenith-staffing.example"             # lower-cased for matching
    assert msg["in_reply_to"] == "<bmi-step2-qa@mail.hirewise-outreach.example>"
    assert msg["subject"].startswith("RE:")


def test_bounce_events_custom_mapping_with_mailgun_shaped_events(tmp_path):
    events = Recorder()
    c = make(tmp_path, delivery_processor=events)
    save(c, "email_events", "custom_webhook", {"type_path": "event", "email_path": "recipient", "id_path": "id",
                                               "bounce_value": "FAILED", "complaint_value": "complained"})
    path, _ = hook(c, "email_events", "custom_webhook")
    p = fixture_json("webhook_payloads.json")
    for name in ("mailgun_failed_permanent", "mailgun_failed_temporary", "mailgun_complained"):
        assert c.post(path, json=p[name]).status_code == 200
    kinds = [(e["type"], e["email"]) for e in events.items]
    assert ("complaint", "ops.head@vistara-fintech.example") in kinds
    assert ("bounce", "nobody@trident-security.example") in kinds
    # limitation recorded in the report: a simple field mapping cannot tell Mailgun's temporary failures from permanent
    assert ("bounce", "hr@ganga-hospitality.example") in kinds


def test_paste_a_sample_preview_reads_what_the_webhook_would(tmp_path):
    c = make(tmp_path)
    batch = fixture_json("sms_gateway_batch.json")
    r = c.post("/api/channels/sms/custom_webhook/test", json={"sample": json.dumps(batch), "mapping": SMS_MAP}).json()
    assert r["ok"] is True and r["message"] == "Read 121 item(s) from the sample." and len(r["extracted"]) == 5
    assert r["extracted"][0]["from_phone"] == "+919000011000"
    bad_path = c.post("/api/channels/sms/custom_webhook/test",
                      json={"sample": json.dumps(batch), "mapping": {**SMS_MAP, "phone_path": "from"}}).json()
    assert bad_path["ok"] is False and "field paths" in bad_path["message"]
    not_json = c.post("/api/channels/sms/custom_webhook/test", json={"sample": "From=+919000012345&Body=YES"}).json()
    assert not_json["ok"] is False and "JSON" in not_json["message"]
    # the preview works before saving and saves nothing
    assert card(c, "sms", "custom_webhook")["saved"] is False
    assert store_of(c).read("integrations").get("sms", {}).get("providers", {}).get("custom_webhook") is None


def test_custom_webhook_preview_uses_saved_mapping_when_none_is_typed(tmp_path):
    c = make(tmp_path)
    save(c, "email_events", "custom_webhook", {"type_path": "event", "email_path": "recipient", "bounce_value": "failed"})
    sample = json.dumps(fixture_json("webhook_payloads.json")["mailgun_failed_permanent"])
    r = c.post("/api/channels/email_events/custom_webhook/test", json={"sample": sample}).json()
    assert r["ok"] is True and r["extracted"][0]["type"] == "bounce"
