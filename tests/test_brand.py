"""White label: brand & offer settings, logo upload checks, brand placeholders in every channel, defaults unchanged,
stage / lead-term labels and the onboarding state."""
import base64
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import brand as b
from app.email_html import render as render_html
from app.main import create_app
from app.prompt_gen import build_prompt
from app.sequences import DEFAULT_SEQUENCE, lead_variables, render
from tests.flow_helpers import make_client, store_of

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
SAFE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10" fill="#123456"/></svg>'
REAL_ESTATE = {"company_name": "Skyline Homes", "product_name": "Skyline Residences", "website": "https://skyline.example",
               "sender_name": "Priya from Skyline", "sign_off": "Warm wishes,\nPriya, Skyline Homes", "primary_color": "#14532d",
               "tagline": "Homes ready when you are", "industry": "Real estate"}
OFFER = {"what": "Ready-to-move 2 and 3 BHK homes in Pune.", "who": "Families in Pune", "value_1": "Possession in 6 months",
         "value_2": "No brokerage", "value_3": "Free site-visit pick-up", "cta": "Shall we book a site visit this weekend?",
         "booking_link": "https://cal.example/visit"}
LEAD = {"id": "L1", "contact_name": "Ravi Kumar", "company": "Acme Pvt Ltd", "role": "Store Manager"}


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def rebrand(c):
    assert c.put("/api/brand", json=REAL_ESTATE).status_code == 200
    assert c.put("/api/brand/offer", json=OFFER).status_code == 200


# ---------------------------------------------------------------- defaults stay as they were
def test_default_brand_renders_the_original_sign_off_and_email():
    step = DEFAULT_SEQUENCE[4]
    text, missing = render(step["body"], lead_variables(LEAD, "BookMyInterview team", "https://o.test"))
    assert not missing
    assert "Regards,\nTeam BookMyInterview.ai\n\nNot relevant? Unsubscribe: https://o.test/u/L1" in text
    assert "This is the problem BookMyInterview.ai is designed to address." in text
    html = render_html(text, "https://o.test")
    assert 'src="https://o.test/bmi-logo.png"' in html and "<title>BookMyInterview.ai</title>" in html
    assert "#0369a1" in html and "#e8552b" in html and "See your sample screen" not in html


def test_default_prompt_is_identical_with_and_without_the_profile():
    spec = {"channel": "email", "industry": "BPO", "role": "Telecaller"}
    assert build_prompt(spec) == build_prompt(spec, profile=b.prompt_profile({}))
    assert "Say plainly that you are from BookMyInterview and that it is in beta" in build_prompt(spec)


def test_api_brand_defaults(tmp_path):
    c = make_client(tmp_path, csv="")
    data = c.get("/api/brand").json()
    assert data["is_default"] and data["brand"]["product_name"] == "BookMyInterview.ai"
    assert data["public"]["header_logo"] == "/mcn-logo.png" and data["public"]["email_logo"] == "/bmi-logo.png"
    assert data["variables"]["sign_off"] == "Regards,\nTeam BookMyInterview.ai"
    assert c.get("/api/brand/public").json()["app_name"] == "MCN Outreach"


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("change,needle", [
    ({"primary_color": "blue"}, "hex"), ({"primary_color": "#fde68a"}, "too light"), ({"website": "skyline"}, "full address"),
    ({"support_email": "nope"}, "email"), ({"company_name": "A" * 81}, "too long"), ({"tagline": "{{x}}"}, "cannot contain"),
    ({"company_name": "<b>x</b>"}, "cannot contain"),
])
def test_brand_validation(tmp_path, change, needle):
    c = make_client(tmp_path, csv="")
    r = c.put("/api/brand", json=change)
    assert r.status_code == 400 and needle in r.json()["detail"]


def test_offer_validation(tmp_path):
    c = make_client(tmp_path, csv="")
    assert "booking link" in c.put("/api/brand/offer", json={"booking_link": "cal.com/x"}).json()["detail"]
    assert c.put("/api/brand/offer", json={"demo_kind": "other"}).status_code == 400


def test_contrast_ratio():
    assert b.contrast_ratio("#000000", "#ffffff") == 21.0
    assert b.contrast_ratio("#0369a1", "#ffffff") >= 4.5


def test_brand_save_syncs_footer_fields_and_audits(tmp_path):
    c = make_client(tmp_path, csv="")
    rebrand(c)
    s = c.get("/api/settings").json()
    assert s["sender_company"] == "Skyline Homes" and s["sender_website"] == "https://skyline.example"
    assert s["sender_name"] == "Priya from Skyline"
    assert any(a["action"] == "brand.save" for a in store_of(c).read("audit"))


def test_caller_cannot_change_the_brand(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    owner = {"Authorization": "Bearer " + c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]}
    c.post("/api/users", json={"email": "meena@example.com", "name": "Meena", "role": "caller", "password": "long-password-1"}, headers=owner)
    caller = {"Authorization": "Bearer " + c.post("/api/login", json={"email": "meena@example.com", "password": "long-password-1"}).json()["token"]}
    for method, path, body in (("put", "/api/brand", {"company_name": "X"}), ("put", "/api/brand/offer", {"who": "x"}),
                               ("post", "/api/brand/logo", {"content_type": "image/png", "data": b64(PNG)}),
                               ("put", "/api/labels", {"stage_labels": {}}), ("put", "/api/onboarding", {"step": "offer"})):
        assert getattr(c, method)(path, json=body, headers=caller).status_code == 403, path
    assert c.get("/api/brand", headers=caller).status_code == 200
    # public: the sign-in page and recipients' mail apps
    assert c.get("/api/brand/public").status_code == 200
    assert c.get("/api/brand").status_code == 401
    # without a login the public addresses are read-only
    assert c.post("/api/brand/logo", json={"content_type": "image/png", "data": b64(PNG)}).status_code == 401
    assert c.delete("/api/brand/logo").status_code == 401


# ---------------------------------------------------------------- logo
def test_logo_upload_png_and_serve(tmp_path):
    c = make_client(tmp_path, csv="")
    data = c.post("/api/brand/logo", json={"content_type": "image/png", "data": b64(PNG), "filename": "l.png"}).json()
    assert data["public"]["custom_logo"] and data["public"]["header_logo"].startswith("/api/brand/logo?v=")
    r = c.get(data["public"]["logo_url"])
    assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
    assert "sandbox" in r.headers["content-security-policy"]
    assert c.delete("/api/brand/logo").json()["public"]["custom_logo"] is False
    assert c.get("/api/brand/logo").status_code == 404


def test_logo_type_size_and_magic_checks(tmp_path):
    c = make_client(tmp_path, csv="")
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (b.LOGO_MAX_BYTES + 1)
    cases = [("image/gif", b64(b"GIF89a")), ("image/png", b64(JPG)), ("image/jpeg", b64(PNG)), ("image/png", "!!!not base64"),
             ("image/png", "")]
    for ctype, data in cases:
        assert c.post("/api/brand/logo", json={"content_type": ctype, "data": data}).status_code == 400, ctype
    with pytest.raises(ValueError, match="limit is 1 MB"):
        b.check_logo("image/png", b64(big))
    assert c.post("/api/brand/logo", json={"content_type": "image/jpeg", "data": b64(JPG)}).status_code == 200


@pytest.mark.parametrize("svg", [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:x="http://www.w3.org/1999/xlink"><a x:href="javascript:alert(1)"><rect/></a></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://evil.example/x.png"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><div>x</div></foreignObject></svg>',
    b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><style>@import url(https://evil.example/a.css);</style></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(http://evil.example/x)"/></svg>',
    b'<html><body>not svg</body></html>',
])
def test_unsafe_svg_is_refused(tmp_path, svg):
    c = make_client(tmp_path, csv="")
    r = c.post("/api/brand/logo", json={"content_type": "image/svg+xml", "data": b64(svg)})
    assert r.status_code == 400
    assert c.get("/api/brand/logo").status_code == 404


def test_safe_svg_is_kept_and_email_uses_the_name_instead(tmp_path):
    c = make_client(tmp_path, csv="")
    rebrand(c)
    data = c.post("/api/brand/logo", json={"content_type": "image/svg+xml", "data": b64(SAFE_SVG)}).json()
    assert data["public"]["header_logo"].startswith("/api/brand/logo") and data["public"]["email_logo"] == ""
    html = render_html("Hi,\n\nBody.", "https://o.test", brand=store_of(c))
    assert "/api/brand/logo" not in html and "Skyline Residences" in html


# ---------------------------------------------------------------- placeholders in every channel
def test_brand_placeholders_in_email_snippet_whatsapp_flow_and_linkedin(tmp_path):
    c = make_client(tmp_path)
    rebrand(c)
    store = store_of(c)
    lead = c.get("/api/leads").json()[0]
    text = "{{my_company}}|{{my_product}}|{{my_tagline}}|{{my_value_1}}|{{my_value_2}}|{{my_value_3}}|{{my_cta}}|{{my_website}}|{{sender}}|{{sign_off}}"
    filled, missing = render(text, lead_variables(lead, b.sender_name(store), "https://o.test", brand=store))
    assert not missing
    assert filled == ("Skyline Homes|Skyline Residences|Homes ready when you are|Possession in 6 months|No brokerage|"
                      "Free site-visit pick-up|Shall we book a site visit this weekend?|https://skyline.example|Priya from Skyline|"
                      "Warm wishes,\nPriya, Skyline Homes")
    # sequence library accepts them (snippets / sequences validation)
    r = c.post("/api/sequences", json={"name": "Homes", "steps": [{"day": 0, "subject": "{{my_product}} for {{company}}",
                                                                    "body": "{{greeting}}\n\n{{my_value_1}}\n\n{{sign_off}}\n\n{{unsub_link}}"}]})
    assert r.status_code in (200, 201), r.text
    # WhatsApp built-in template names the brand's product
    from app import whatsapp
    store.update_lead(lead["id"], lambda l: {**l, "phone": "+919000000001", "consent": {"at": "2026-11-03T04:00:00+00:00", "text": "yes"}})
    whatsapp.send(store, lead["id"], "first_after_yes", "", datetime(2026, 11, 4, 6, tzinfo=timezone.utc), "https://o.test")
    assert "from Skyline Residences." in store.read("whatsapp_outbox")[-1]["body"]
    # flows: the validators know the placeholders
    from app.flows import EMAIL_PLACEHOLDERS, TEXT_PLACEHOLDERS, _check_text_placeholders
    assert set(b.BRAND_PLACEHOLDERS) <= set(EMAIL_PLACEHOLDERS) and set(b.BRAND_PLACEHOLDERS) <= set(TEXT_PLACEHOLDERS)
    assert _check_text_placeholders("{{my_company}} {{my_cta}}", TEXT_PLACEHOLDERS, "The SMS") == []
    # LinkedIn: the brand placeholders work next to its own {first_name}
    from app import linkedin_seq
    out = linkedin_seq.render({"n": 2, "kind": "message", "text": "Hi {first_name}, {{my_product}}: {{my_value_2}}."}, lead, store.read("settings"))
    assert out.endswith("Skyline Residences: No brokerage.")


def test_rebranded_email_html_uses_brand_colours_names_and_sign_off(tmp_path):
    c = make_client(tmp_path, csv="")
    rebrand(c)
    c.post("/api/brand/logo", json={"content_type": "image/png", "data": b64(PNG)})
    store = store_of(c)
    text, _ = render(DEFAULT_SEQUENCE[0]["body"], lead_variables(LEAD, b.sender_name(store), "https://o.test", brand=store))
    assert "Warm wishes,\nPriya, Skyline Homes" in text and "BookMyInterview" not in text
    html = render_html(text, "https://o.test", brand=store)
    assert "#14532d" in html and "#0369a1" not in html and "bmi-logo" not in html
    assert 'src="https://o.test/api/brand/logo?v=' in html and "<title>Skyline Residences</title>" in html
    assert "Priya, Skyline Homes" in html


def test_preview_endpoint(tmp_path):
    c = make_client(tmp_path, csv="")
    r = c.post("/api/brand/preview", json={"brand": REAL_ESTATE, "offer": OFFER}).json()
    assert r["ok"] and "Skyline Residences" in r["html"] and not r["missing"]
    assert c.get("/api/brand").json()["is_default"]                     # nothing saved
    assert c.post("/api/brand/preview", json={"brand": {"primary_color": "#ffffff"}}).json()["ok"] is False


def test_rebranded_demo_page_unsubscribe_ai_and_call_script(tmp_path):
    c = make_client(tmp_path)
    rebrand(c)
    lead = c.get("/api/leads").json()[0]
    url = c.post(f"/api/leads/{lead['id']}/demo-link").json()["url"]
    page = c.get("/demo/" + url.rsplit("/demo/", 1)[1]).text
    assert "Skyline Residences" in page and "No brokerage" in page and "https://cal.example/visit" in page
    assert "BookMyInterview" not in page and "screen" not in page.lower()
    script = c.get("/api/call-script").json()
    assert script["variables"]["my_company"] == "Skyline Homes" and "{{my_value_1}}" in script["script"][2]["text"]
    profile = b.prompt_profile(store_of(c))
    prompt = build_prompt({"channel": "email"}, profile=profile)
    assert "Skyline Residences" in prompt and "BookMyInterview" not in prompt and "hiring" not in prompt.lower()
    assert lead["suggestion"] and "BookMyInterview" not in c.get("/api/leads").json()[0]["suggestion"]["pitch"]
    readiness = {i["id"]: i for i in c.get("/api/readiness").json()["items"]}
    assert readiness["brand"]["status"] == "done" and readiness["bmi_limits"]["required"] is False
    assert "Skyline Residences" in readiness["bmi_limits"]["title"]


def test_unsubscribe_page_names_the_product(tmp_path):
    c = make_client(tmp_path)
    from app.unsubscribe import unsub_path
    lead = c.get("/api/leads").json()[0]
    path = unsub_path(store_of(c), lead["id"])
    assert "Stop all emails from BookMyInterview.ai?" in c.get(path).text
    rebrand(c)
    assert "Stop all emails from Skyline Residences?" in c.get(path).text


def test_reset_brings_back_the_original(tmp_path):
    c = make_client(tmp_path, csv="")
    rebrand(c)
    data = c.post("/api/brand/reset").json()
    assert data["is_default"] and data["brand"]["sign_off"] == "Regards,\nTeam BookMyInterview.ai"


def test_saving_the_original_values_keeps_the_default_mode(tmp_path):
    c = make_client(tmp_path, csv="")
    c.put("/api/brand", json={k: v for k, v in b.DEFAULT_BRAND.items() if k != "logo_url"})
    assert c.get("/api/brand").json()["is_default"]


# ---------------------------------------------------------------- labels
def test_stage_labels_and_lead_terms(tmp_path):
    c = make_client(tmp_path)
    r = c.put("/api/labels", json={"stage_labels": {"interested": "Site visit booked", "new": "New"}, "lead_terms": {"open_roles": "Units"}})
    assert r.status_code == 200
    assert r.json()["stage_labels"] == {"interested": "Site visit booked"}            # same as the default = not stored
    assert r.json()["lead_terms"]["open_roles"] == "Units" and r.json()["lead_terms"]["role"] == "Role"
    assert c.put("/api/labels", json={"stage_labels": {"bogus": "x"}}).status_code == 400
    assert c.put("/api/labels", json={"lead_terms": {"bogus": "x"}}).status_code == 400
    assert c.put("/api/labels", json={"stage_labels": {"new": "x" * 41}}).status_code == 400
    lead = c.get("/api/leads").json()[0]
    store_of(c).update_lead(lead["id"], lambda l: {**l, "stage": "interested"})
    rows = c.get("/api/export/leads.json").json()
    row = next(x for x in rows["rows"] if x["id"] == lead["id"])
    assert row["stage"] == "interested" and row["stage_name"] == "Site visit booked"


# ---------------------------------------------------------------- onboarding
def test_onboarding_state_resume_finish_and_reopen(tmp_path):
    c = make_client(tmp_path, csv="")
    o = c.get("/api/onboarding").json()
    assert o["show"] and o["step"] == "business"
    o = c.put("/api/onboarding", json={"step": "industry", "done_steps": ["business", "offer"]}).json()
    assert o["step"] == "industry" and o["done_steps"] == ["business", "offer"] and o["show"]
    assert c.put("/api/onboarding", json={"step": "nope"}).status_code == 400
    o = c.put("/api/onboarding", json={"finished": True, "step": "done"}).json()
    assert o["onboarded"] and not o["show"]
    o = c.put("/api/onboarding", json={"reopen": True}).json()
    assert o["show"]
    assert not c.put("/api/onboarding", json={"skipped": True}).json()["show"]


def test_existing_install_with_leads_is_not_greeted_by_the_wizard(tmp_path):
    c = make_client(tmp_path)
    assert c.get("/api/onboarding").json()["show"] is False
