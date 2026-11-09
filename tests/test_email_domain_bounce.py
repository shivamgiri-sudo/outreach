"""A typo'd email domain is caught before sending, and a mail-server delivery report (lines wrapped) reads as a bounce."""
from fastapi.testclient import TestClient

from app import email_domain
from app.main import create_app
from app.replies import classify_reply

MAILCHANNELS = """This is the mail system at host relay.mailchannels.net.

I'm sorry to have to inform you that your message could not
be delivered to one or more recipients. It's attached below.

For further assistance, please send mail to postmaster.

                   The mail system

<yogesh.luthra@tvalueserve.com>: Host or domain name not found. Name service
    error for name=tvalueserve.com type=A: Host not found"""


def test_wrapped_delivery_report_is_a_bounce():
    assert classify_reply(MAILCHANNELS) == "bounce"
    assert classify_reply("Undelivered Mail Returned to Sender\n\nAddress not found") == "bounce"
    assert classify_reply("Sounds good, could we talk on Monday?") != "bounce"


def fake_dns(monkeypatch, answers):
    monkeypatch.setattr(email_domain, "CHECK", True)
    monkeypatch.setattr(email_domain, "_CACHE", {})
    monkeypatch.setattr(email_domain, "_lookup", lambda d: answers.get(d, "ok"))


def test_problem_sentences(monkeypatch):
    fake_dns(monkeypatch, {"tvalueserve.com": "no_domain", "nomail.in": "no_mail", "slow.in": "unknown"})
    assert "does not exist" in email_domain.problem("a@tvalueserve.com")
    assert "no mail server" in email_domain.problem("a@nomail.in")
    assert email_domain.problem("a@slow.in") is None and email_domain.problem("a@atain.com") is None   # never block on doubt


def test_edit_send_and_sequence_refuse_a_domain_that_does_not_exist(monkeypatch, tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": "company,contact_name,email,website\nAtain,Yogesh Luthra,yogesh.luthra@atain.com,https://atain.com\n"})
    store = c.app.state.store
    lid = store.read("leads")[0]["id"]
    fake_dns(monkeypatch, {"tvalueserve.com": "no_domain"})
    r = c.patch(f"/api/leads/{lid}", json={"email": "yogesh.luthra@tvalueserve.com"})
    assert r.status_code in (400, 409) and "does not exist" in r.text
    assert store.read("leads")[0]["email"] == "yogesh.luthra@atain.com"
    r = c.post(f"/api/leads/{lid}/contact-points", json={"kind": "email", "value": "y@tvalueserve.com"})
    assert r.status_code in (400, 409) and "does not exist" in r.text
    store.update_lead(lid, lambda l: {**l, "email": "yogesh.luthra@tvalueserve.com"})       # e.g. an old import
    r = c.post(f"/api/leads/{lid}/email-compose", json={"subject": "Hi", "body": "Hello"})
    assert r.status_code >= 400 and "does not exist" in r.text
