"""Custom HTTP connector: rendering, JSON escaping, success parsing, masking, and the SSRF guard."""
import json

import pytest

from app.channels.custom_http import call_custom, get_path, is_success, render_custom
from app.channels.netguard import check_public_url
from tests.channel_fakes import FakeHTTP, make_client

PUBLIC = lambda host: ["93.184.216.34"]
CONFIG = {"method": "POST", "url": "https://api.sms.example/v1/send?to={{phone}}",
          "headers": [{"key": "Authorization", "value": "Bearer live-secret-1234", "secret": True}, {"key": "X-Team", "value": "bmi"}],
          "body_type": "json", "body_template": '{"to": "{{phone}}", "msg": "{{text}}", "name": "{{name}}"}',
          "success_path": "data.status", "success_value": "ok", "id_path": "data.id"}


def test_json_placeholders_are_escaped():
    text = 'He said "hi"\nline two \\ backslash </script>'
    rendered = render_custom(CONFIG, {"phone": "+919812345678", "text": text, "name": "Asha"})
    body = json.loads(rendered["body"])
    assert body == {"to": "+919812345678", "msg": text, "name": "Asha"}
    assert rendered["url"] == "https://api.sms.example/v1/send?to=%2B919812345678"
    assert rendered["headers"]["Content-Type"] == "application/json"


def test_injection_through_a_placeholder_cannot_add_json_fields():
    rendered = render_custom(CONFIG, {"phone": "1", "text": '", "admin": true, "x": "', "name": ""})
    assert set(json.loads(rendered["body"])) == {"to", "msg", "name"}


def test_form_body_and_header_line_breaks():
    cfg = {**CONFIG, "body_type": "form", "body_template": "to={{phone}}&msg={{text}}",
           "headers": [{"key": "X-Name", "value": "{{name}}"}]}
    rendered = render_custom(cfg, {"phone": "+91 98", "text": "a&b=c", "name": "Asha\r\nX-Evil: 1"})
    assert rendered["body"] == "to=%2B91+98&msg=a%26b%3Dc"
    assert "\n" not in rendered["headers"]["X-Name"] and rendered["headers"]["Content-Type"] == "application/x-www-form-urlencoded"


def test_unknown_placeholders_and_broken_json_are_refused():
    with pytest.raises(ValueError, match="secret_key"):
        render_custom({**CONFIG, "body_template": '{"k": "{{secret_key}}"}'}, {})
    with pytest.raises(ValueError, match="JSON"):
        render_custom({**CONFIG, "body_template": '{"to": {{phone}}}'}, {"phone": "abc"})


def test_get_request_has_no_body():
    rendered = render_custom({**CONFIG, "method": "GET"}, {"phone": "1", "text": "x", "name": ""})
    assert rendered["method"] == "GET" and rendered["body"] is None


def test_success_path_and_value():
    reply = {"data": {"status": "OK", "id": "m-7", "items": [{"sent": True}]}}
    assert is_success(reply, CONFIG) is True
    assert is_success({"data": {"status": "queued"}}, CONFIG) is False
    assert is_success(reply, {"success_path": "data.items.0.sent"}) is True
    assert is_success({"data": {"items": [{"sent": False}]}}, {"success_path": "data.items.0.sent"}) is False
    assert is_success({"anything": 1}, {}) is True
    assert get_path(reply, "data.missing.deep") is None and get_path(reply, "data.items.5") is None


def test_call_custom_masks_secrets_in_preview_and_truncates_reply():
    http = FakeHTTP({"api.sms.example": {"data": {"status": "ok", "id": "m-1"}, "blob": "x" * 5000}})
    out = call_custom(CONFIG, {"phone": "+919812345678", "text": "hi", "name": "A"}, http, PUBLIC)
    assert out["ok"] is True and out["provider_id"] == "m-1"
    assert out["preview"]["headers"]["Authorization"] == "••••1234" and out["preview"]["headers"]["X-Team"] == "bmi"
    assert "live-secret-1234" not in json.dumps(out) and len(out["response"]) <= 2048 + 10
    assert http.last()["headers"]["Authorization"] == "Bearer live-secret-1234"     # the real request carries the key
    assert json.loads(http.last()["raw"]) == {"to": "+919812345678", "msg": "hi", "name": "A"}


def test_call_custom_reports_provider_errors():
    out = call_custom(CONFIG, {"phone": "1", "text": "x", "name": ""}, FakeHTTP({"sms": PermissionError("HTTP 401")}), PUBLIC)
    assert out["ok"] is False and "401" in out["error"]
    out = call_custom(CONFIG, {"phone": "1", "text": "x", "name": ""}, FakeHTTP({"sms": {"data": {"status": "fail"}}}), PUBLIC)
    assert out["ok"] is False and "success" in out["error"]


# ---- SSRF ----
@pytest.mark.parametrize("url", [
    "https://127.0.0.1/send", "https://10.1.2.3/send", "https://169.254.169.254/latest/meta-data/", "https://192.168.1.10/x",
    "https://[::1]/x", "https://0.0.0.0/x", "https://100.64.0.1/x", "https://localhost/x", "https://metadata.google.internal/x",
    "file:///etc/passwd", "http://api.example.com/x", "ftp://api.example.com/x", "https://user:pw@api.example.com/x", "https:///nohost",
])
def test_ssrf_guard_blocks(url):
    with pytest.raises(ValueError):
        check_public_url(url, PUBLIC)


def test_ssrf_guard_checks_what_a_name_resolves_to():
    with pytest.raises(ValueError, match="private"):
        check_public_url("https://sneaky.example.com/x", lambda h: ["93.184.216.34", "10.0.0.7"])
    with pytest.raises(ValueError, match="private"):
        check_public_url("https://sneaky.example.com/x", lambda h: ["::ffff:169.254.169.254"])
    assert check_public_url("https://api.example.com/x", PUBLIC) == "https://api.example.com/x"


def test_ssrf_blocks_call_before_any_request():
    http = FakeHTTP()
    out = call_custom({**CONFIG, "url": "https://169.254.169.254/latest"}, {}, http, PUBLIC)
    assert out["ok"] is False and http.calls == []


def test_custom_connector_via_api_saves_masks_and_tests(tmp_path):
    http = FakeHTTP({"api.sms.example": {"data": {"status": "ok", "id": "m-9"}}})
    c = make_client(tmp_path, http)
    blocked = c.put("/api/channels/sms/custom_http", json={"values": {**CONFIG, "url": "https://10.0.0.1/send"}})
    assert blocked.status_code == 400 and "private" in blocked.json()["detail"]
    assert c.put("/api/channels/sms/custom_http", json={"values": {**CONFIG, "url": "http://api.sms.example/x"}}).status_code == 400
    saved = c.put("/api/channels/sms/custom_http", json={"values": CONFIG}).json()
    assert saved["values"]["headers"][0]["value"] == "••••1234" and saved["status"] == "configured"
    # saving again with the masked header keeps the real key
    c.put("/api/channels/sms/custom_http", json={"values": {"headers": saved["values"]["headers"]}})
    result = c.post("/api/channels/sms/custom_http/test", json={"to": "+919812345678"}).json()
    assert result["ok"] is True and result["preview"]["headers"]["Authorization"] == "••••1234"
    assert http.last()["headers"]["Authorization"] == "Bearer live-secret-1234"
    assert json.loads(http.last()["raw"])["to"] == "+919812345678"


def test_custom_connector_rejects_unknown_placeholder_on_save(tmp_path):
    c = make_client(tmp_path)
    r = c.put("/api/channels/whatsapp/custom_http", json={"values": {**CONFIG, "body_template": '{"a": "{{password}}"}'}})
    assert r.status_code == 400 and "password" in r.json()["detail"]
