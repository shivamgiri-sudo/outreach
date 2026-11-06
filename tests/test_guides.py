"""Every credential / connection screen has a plain-English setup guide, and the guides are well formed."""
import re

import pytest
from fastapi.testclient import TestClient

from app import capture_catalogue, guides, integrations
from app.channels import registry
from app.channels.email_send import smtp_settings
from app.main import create_app

ALLOWED_CATEGORIES = {"Email sending", "Reply capture", "Bounces", "WhatsApp", "SMS", "Calls", "Calendar & meetings",
                      "Lead data", "Email verification", "AI", "Lead capture", "App & team", "Custom"}


def test_every_integration_card_has_a_guide():
    """Fails when a new integration, provider, source or capture type is added without a guide in app/guides."""
    assert guides.missing() == [], "Add a guide (attach key) for: " + ", ".join(guides.missing())


@pytest.mark.parametrize("key", sorted(guides.required_keys()))
def test_each_required_key_is_attached(key):
    assert guides.ATTACH.get(key), f"no guide attached to {key}"


def test_catalogues_are_all_counted():
    keys = guides.required_keys()
    assert all(f"hub:{c}:{p['id']}" in keys for c, plist in registry.PROVIDERS.items() for p in plist)
    assert all(f"integration:{i['id']}" in keys for i in integrations.CATALOGUE + integrations.SOURCE_CATALOGUE)
    assert all(f"capture:{t['id']}" in keys for t in capture_catalogue.TYPES)


def test_every_setup_checklist_row_has_a_guide(tmp_path):
    client = TestClient(create_app(data_file=tmp_path / "d.json"))
    items = client.get("/api/readiness").json()["items"]
    assert items
    assert [i["id"] for i in items if not guides.ATTACH.get(f"setup:{i['id']}")] == []


def test_guides_endpoint_serves_everything(tmp_path):
    client = TestClient(create_app(data_file=tmp_path / "d.json"))
    body = client.get("/api/guides").json()
    assert len(body["guides"]) == len(guides.GUIDES)
    assert body["attach"]["hub:email_send:gmail_smtp"][0] == "gmail_smtp"


@pytest.mark.parametrize("g", guides.GUIDES, ids=lambda g: g["id"])
def test_guide_is_complete_and_plain(g):
    assert g["category"] in ALLOWED_CATEGORIES
    assert g["title"] and g["summary"] and len(g["steps"]) >= 2
    assert g["test"] and g["safety"] and g["time"] and g["cost"]
    assert all(len(s) < 420 for s in g["steps"]), "keep each step short"
    assert all(url.startswith("https://") for _, url in g["links"])
    assert all(r in guides.BY_ID for r in g["related"])
    assert all(len(e) == 2 and e[0] and e[1] for e in g["errors"])
    for key, f in g["fields"].items():
        assert f["hint"], key
        if f.get("pattern"):
            re.compile(f["pattern"])
            assert f["message"], f"{g['id']}.{key}: a pattern needs a friendly message"
            assert "(?<" not in f["pattern"], "lookbehind is not safe in every browser"
        for w in f.get("warn", []):
            re.compile(w["pattern"])
            assert w["message"]


def test_field_hints_match_real_form_fields():
    """A field hint keyed to a field that no attached form has would never show."""
    form_fields = {}
    for c, plist in registry.PROVIDERS.items():
        for p in plist:
            form_fields[f"hub:{c}:{p['id']}"] = {f["key"] for f in p["fields"]}
    for i in integrations.CATALOGUE + integrations.SOURCE_CATALOGUE:
        form_fields[f"integration:{i['id']}"] = {f["key"] for f in i["fields"]}
    for t in capture_catalogue.TYPES:
        form_fields[f"capture:{t['id']}"] = {f["key"] for f in t["fields"]}
    app_fields = {"public_base_url", "webhook_secret", "token"}
    for g in guides.GUIDES:
        known = set().union(*(form_fields.get(k, set()) for k in g["attach"])) | app_fields
        assert set(g["fields"]) <= known, (g["id"], set(g["fields"]) - known)


@pytest.mark.parametrize("gid,key,good,bad", [
    ("resend", "api_key", "re_123456789abcdef", "sk_live_123"),
    ("sendgrid", "api_key", "SG.abcdefghijklmnopqrstuv.wxyz", "re_123"),
    ("gmail_smtp", "app_password", "abcd efgh ijkl mnop", "MyNormalPassword@123"),
    ("click_to_call", "click_to_call_url", "https://d/call?n={phone}", "https://d/call?n=98"),
    ("meta_cloud", "phone_number_id", "123456789012345", "+91 98123 45678"),
    ("llm", "api_key", "sk-ant-api03-abcdefghijklmnopqrstuvwxyz", "sk-proj-abc"),
    ("public_url", "public_base_url", "https://outreach.yourbrand.in", "http://localhost:8000"),
])
def test_format_checks_accept_good_and_catch_bad(gid, key, good, bad):
    f = guides.BY_ID[gid]["fields"][key]
    assert re.search(f["pattern"], good)
    assert not re.search(f["pattern"], bad)


def test_openai_key_in_ai_writer_gets_a_plain_warning():
    warn = guides.BY_ID["llm"]["fields"]["api_key"]["warn"][0]
    assert re.search(warn["pattern"], "sk-proj-abc") and "OpenAI" in warn["message"]
    assert not re.search(warn["pattern"], "sk-ant-api03-abc")


def test_gmail_app_password_spaces_are_ignored():
    s = smtp_settings("gmail_smtp", {"smtp_username": "a@b.in", "app_password": "abcd efgh ijkl mnop"})
    assert s["password"] == "abcdefghijklmnop"


def test_a_providers_own_guide_comes_first():
    """Shared guides (DNS, DLT, custom) may be attached too, but a provider's own guide opens first."""
    for c, plist in registry.PROVIDERS.items():
        for p in plist:
            ids = guides.ATTACH[f"hub:{c}:{p['id']}"]
            if p["id"] in guides.BY_ID:
                assert ids[0] == p["id"], (c, p["id"], ids)
