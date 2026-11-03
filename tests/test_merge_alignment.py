"""Hub email providers follow the same safety rules as Resend: thread ids, pause on provider problems, live mode."""
import smtplib
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.channels import config
from app.channels.email_send import send_email
from app.main import create_app
from app.senders import email_ready
from app.store import Store
from tests.channel_fakes import FakeHTTP, SMTPFactory

T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)
SMTP = {"from_name": "Priya", "from_email": "priya@mail.example.in", "smtp_host": "smtp.example.in", "smtp_port": "587",
        "smtp_security": "starttls", "smtp_username": "priya", "smtp_password": "pw"}
MESSAGE = {"lead_id": "L1", "to": "asha@acme-retail.in", "subject": "Hiring at Acme", "body": "Hi Asha",
           "message_id": "<abc.L1@mail.example.in>", "idempotency_key": "L1:0:live"}


def smtp_store(tmp_path):
    store = Store(tmp_path / "s.json")
    config.save_provider(store, "email_send", "smtp", SMTP, resolver=lambda h: ["93.184.216.34"])
    store.update("settings", lambda s: {**s, "live_sending": True})
    return store


def test_smtp_send_uses_the_engines_message_id_for_threading(tmp_path):
    factory = SMTPFactory()
    out = send_email(smtp_store(tmp_path), MESSAGE, FakeHTTP(), smtp_factory=factory, base_url="https://o.example.in")
    sent = factory.clients[0].sent[0]
    assert out["status"] == "sent" and sent["Message-ID"] == "<abc.L1@mail.example.in>"
    assert len(sent.get_all("Message-ID")) == 1 and "X-Idempotency-Key" not in sent


def test_smtp_refused_recipient_fails_only_that_message(tmp_path):
    class Refusing(SMTPFactory):
        def __call__(self, host, port, use_ssl, timeout):
            client = super().__call__(host, port, use_ssl, timeout)
            client.send_message = lambda msg: (_ for _ in ()).throw(smtplib.SMTPRecipientsRefused({"asha@acme-retail.in": (550, b"no")}))
            return client

    out = send_email(smtp_store(tmp_path), MESSAGE, FakeHTTP(), smtp_factory=Refusing())
    assert out["status"] == "failed"


def test_unreachable_smtp_server_pauses_instead_of_failing_the_lead(tmp_path):
    def down(host, port, use_ssl, timeout):
        raise OSError("connection refused")

    out = send_email(smtp_store(tmp_path), MESSAGE, FakeHTTP(), smtp_factory=down)
    assert out["status"] == "provider_error" and out["retryable"] is True


def test_resend_through_the_hub_sends_the_idempotency_key(tmp_path):
    store = Store(tmp_path / "s.json")
    config.save_provider(store, "email_send", "resend", {"from_name": "Priya", "from_email": "priya@mail.example.in",
                                                         "domain": "mail.example.in", "api_key": "re_k"}, resolver=lambda h: ["93.184.216.34"])
    store.update("settings", lambda s: {**s, "live_sending": True})
    http = FakeHTTP({"api.resend.com": {"id": "em_1"}})
    send_email(store, MESSAGE, http, base_url="https://o.example.in")
    call = http.calls[0]
    assert call["headers"]["Idempotency-Key"] == "L1:0:live" and "X-Idempotency-Key" not in call["body"]["headers"]
    assert call["body"]["headers"]["Message-ID"] == "<abc.L1@mail.example.in>"


def test_a_live_run_through_an_smtp_provider_really_sends(tmp_path):
    factory = SMTPFactory()
    c = TestClient(create_app(data_file=tmp_path / "d.json", smtp_factory=factory, resolver=lambda h: ["93.184.216.34"]))
    c.post("/api/leads/import", json={"csv": "company,contact_name,email,website,country\nAcme,Asha Rao,asha@acme-retail.in,https://acme-retail.in,IN\n",
                                      "source": "apollo"})
    store = c.app.state.store
    config.save_provider(store, "email_send", "smtp", SMTP, resolver=lambda h: ["93.184.216.34"])
    config.set_active(store, "email_send", "smtp")
    assert email_ready(store)["smtp_host"] == "smtp.example.in"
    c.put("/api/settings", json={"public_base_url": "https://o.example.in", "sender_company": "BookMyInterview.ai",
                                 "sender_address": "Noida, India"})
    assert c.put("/api/settings", json={"live_sending": True}).status_code == 200
    lid = c.get("/api/leads").json()[0]["id"]
    c.post(f"/api/leads/{lid}/enroll")
    result = c.post("/api/sequences/run", json={"now": T0.isoformat()}).json()
    assert result["mode"] == "live" and result["sent"] == 1
    sent = factory.clients[0].sent[0]
    assert sent["Message-ID"] == c.get("/api/outbox").json()[0]["message_id"]


def test_whatsapp_from_a_lead_goes_through_the_connected_provider_when_live(tmp_path):
    from app.whatsapp import WhatsAppError, send
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": "company,contact_name,email,phone,website,country\nAcme,Asha Rao,asha@acme-retail.in,98765 43210,https://acme-retail.in,IN\n",
                                      "source": "apollo"})
    store = c.app.state.store
    lid = c.get("/api/leads").json()[0]["id"]
    c.post(f"/api/leads/{lid}/consent", json={"text": "Yes on WhatsApp — call with Priya", "channel": "whatsapp"})
    config.save_provider(store, "whatsapp", "meta_cloud", {"phone_number_id": "1234567890", "access_token": "EAAB",
                                                           "app_secret": "s", "verify_token": "v"}, resolver=lambda h: ["93.184.216.34"])
    config.set_active(store, "whatsapp", "meta_cloud")
    http = FakeHTTP({"graph.facebook.com": {"messages": [{"id": "wamid.1"}]}})
    send(store, lid, "first_after_yes", "", T0, "https://o.example.in", transport=http)
    assert http.calls == [] and store.read("whatsapp_outbox")[-1]["status"] == "dry_run"      # not live yet
    store.update("settings", lambda s: {**s, "live_whatsapp": True})
    send(store, lid, "first_after_yes", "", T0, "https://o.example.in", transport=http)
    body = http.calls[0]["body"]
    assert body["type"] == "template" and body["template"]["name"] == "first_after_yes" and body["to"] == "919876543210"
    assert [p["text"] for p in body["template"]["components"][0]["parameters"]][0] == "Asha"
    assert store.read("whatsapp_outbox")[-1]["status"] == "sent" and store.read("whatsapp_outbox")[-1]["provider_id"] == "wamid.1"
