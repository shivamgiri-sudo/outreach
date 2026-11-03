"""Fakes for the Integrations hub tests: HTTP transport, SMTP client, IMAP client and an app factory."""
import imaplib
import smtplib
from email.message import EmailMessage

from fastapi.testclient import TestClient

from app.main import create_app

PUBLIC_IP = "93.184.216.34"
CSV = ("company,contact_name,email,phone,website,open_roles,company_size,country\n"
       "Acme Retail Pvt Ltd,Asha Rao,asha@acme-retail.in,9812345678,https://acme-retail.in,5,200,IN\n")


class FakeHTTP:
    """Records every call; replies by the first route whose key is part of the URL (an Exception is raised)."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []

    def __call__(self, url, headers=None, body=None, method="POST", form=None, raw=None):
        self.calls.append({"url": url, "headers": dict(headers or {}), "body": body, "method": method, "form": form, "raw": raw})
        for key, reply in self.routes.items():
            if key in url:
                if isinstance(reply, Exception):
                    raise reply
                return reply
        return {}

    def last(self, part=""):
        return next(c for c in reversed(self.calls) if part in c["url"])


class FakeSMTP:
    def __init__(self, host, port, use_ssl, timeout, fail_login=False):
        self.host, self.port, self.use_ssl, self.fail_login = host, port, use_ssl, fail_login
        self.log, self.sent = [], []

    def ehlo(self):
        self.log.append("ehlo")

    def starttls(self, context=None):
        self.log.append("starttls")

    def login(self, user, password):
        if self.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        self.log.append(("login", user))

    def send_message(self, msg):
        self.sent.append(msg)

    def quit(self):
        self.log.append("quit")


class SMTPFactory:
    def __init__(self, fail_login=False):
        self.fail_login, self.clients = fail_login, []

    def __call__(self, host, port, use_ssl, timeout):
        client = FakeSMTP(host, port, use_ssl, timeout, self.fail_login)
        self.clients.append(client)
        return client


def raw_email(uid: int, sender="asha@acme-retail.in", subject="Re: hiring", body="Yes please send it", in_reply_to="<m1@mail.example.in>"):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = f"Asha Rao <{sender}>", "priya@mail.example.in", subject
    msg["Message-ID"], msg["In-Reply-To"], msg["References"] = f"<r{uid}@acme-retail.in>", in_reply_to, in_reply_to
    msg["Date"] = "Tue, 03 Nov 2026 10:00:00 +0530"
    msg.set_content(body)
    return msg.as_bytes()


class FakeIMAP:
    def __init__(self, messages: dict, password="app-pass"):
        self.messages, self.unseen, self.password = dict(messages), set(messages), password
        self.searches, self.stored, self.selected = [], [], None

    def login(self, user, password):
        if password != self.password:
            raise imaplib.IMAP4.error("AUTHENTICATIONFAILED")
        return "OK", [b"logged in"]

    def select(self, mailbox, readonly=False):
        self.selected = (mailbox, readonly)
        return "OK", [str(len(self.messages)).encode()]

    def uid(self, command, *args):
        if command == "search":
            self.searches.append(args)
            uids = [u for u in sorted(self.messages) if ("UNSEEN" not in args or u in self.unseen)]
            return "OK", [b" ".join(str(u).encode() for u in uids)]
        if command == "fetch":
            uid = int(args[0])
            return "OK", [(f"{uid} (UID {uid} BODY[] {{1}}".encode(), self.messages[uid]), b")"]
        if command == "store":
            self.unseen.discard(int(args[0]))
            self.stored.append(args)
            return "OK", []
        raise AssertionError(command)

    def logout(self):
        return "BYE", []


def make_client(tmp_path, http=None, resolver=None, **kwargs):
    app = create_app(data_file=tmp_path / "d.json", channel_transport=http or FakeHTTP(),
                     resolver=resolver or (lambda host: [PUBLIC_IP]), **kwargs)
    return TestClient(app)


def add_lead(client) -> dict:
    client.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    return client.get("/api/leads").json()[0]


def provider(client, channel, pid) -> dict:
    ch = next(c for c in client.get("/api/channels").json()["channels"] if c["id"] == channel)
    return next(p for p in ch["providers"] if p["id"] == pid)
