"""Designed emails: same words as the text, logo, unsubscribe link, escaping; sent as multipart alternative."""
from app.channels.email_send import build_message, http_request_for
from app.email_html import render
from app.sequences import DEFAULT_SEQUENCE
from tests.flow_helpers import make_client

TEXT = ("Dear Priya,\n\nIt brings together:\n- Role-specific tests: evaluate skills.\n- Candidate self-booking: simpler <scheduling>.\n\n"
        "Would you be open to a 20-minute walkthrough?\n\nRegards,\nTeam BookMyInterview.ai\n\nNot relevant? Unsubscribe: https://x.test/u/abc.sig")


def words(s):
    import re
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).replace("&lt;", "<").replace("&gt;", ">").strip()


def test_html_keeps_every_word_and_adds_logo_and_unsubscribe():
    html = render(TEXT, "https://portal.test")
    assert 'src="https://portal.test/bmi-logo.png"' in html
    assert 'href="https://x.test/u/abc.sig"' in html and "Unsubscribe" in html
    for phrase in ("Dear Priya,", "Role-specific tests", "evaluate skills.", "simpler <scheduling>.", "Would you be open to a 20-minute walkthrough?",
                   "Regards,", "Team BookMyInterview.ai"):
        assert phrase in words(html), phrase
    assert "<scheduling>" not in html                                  # escaped
    assert "<script" not in html.lower()


def test_every_sequence_step_renders():
    for step in DEFAULT_SEQUENCE:
        body = step["body"].replace("{{greeting}}", "Dear Priya,").replace("{{unsub_link}}", "https://x.test/u/1")
        html = render(body, "https://portal.test")
        assert "Dear Priya," in html and 'href="https://x.test/u/1"' in html


def test_multipart_and_providers_carry_the_html():
    values = {"from_email": "a@b.test", "from_name": "A"}
    msg = build_message(values, "to@x.test", "Hi", "plain", {}, html="<p>designed</p>")
    assert msg.is_multipart() and {p.get_content_type() for p in msg.iter_parts()} == {"text/plain", "text/html"}
    assert build_message(values, "to@x.test", "Hi", "plain", {}).get_content_type() == "text/plain"
    assert http_request_for("resend", {**values, "api_key": "k"}, "t@x.test", "s", "t", {}, html="<p>h</p>")[2]["html"] == "<p>h</p>"
    assert http_request_for("postmark", {**values, "server_token": "k"}, "t@x.test", "s", "t", {}, html="<p>h</p>")[2]["HtmlBody"] == "<p>h</p>"
    sg = http_request_for("sendgrid", {**values, "api_key": "k"}, "t@x.test", "s", "t", {}, html="<p>h</p>")[2]["content"]
    assert [c["type"] for c in sg] == ["text/plain", "text/html"]


def test_preview_endpoint(tmp_path):
    c = make_client(tmp_path)
    r = c.get("/api/sequence/0/preview")
    assert r.status_code == 200 and "Alex" in r.json()["html"] and r.json()["subject"]
    assert c.get("/api/sequence/99/preview").status_code == 404


def test_send_preview_needs_a_sending_domain_and_an_address(tmp_path):
    c = make_client(tmp_path)
    assert c.post("/api/sequence/0/send-preview", json={"to": "not-an-email"}).status_code in (400, 422)
    r = c.post("/api/sequence/0/send-preview", json={"to": "me@example.com"})
    assert r.status_code == 400 and "sending domain" in r.json()["detail"]
