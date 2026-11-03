"""Shared helpers for the flow builder tests: a client with fake transports, tiny graph builders, ticks."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import create_app

T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)          # Tuesday 10:30 IST
CSV = ("company,contact_name,title,email,phone,website,open_roles,company_size,country\n"
       "Acme,Ravi Kumar,HR Head,ravi@acme.com,98765 43210,https://acme.com,12,200,IN\n"
       "Beta,Sam Rao,Founder,sam@beta.in,,https://beta.in,1,10,IN\n")
IDENTITY = {"sender_company": "BookMyInterview.ai", "sender_address": "Sector 62, Noida, Uttar Pradesh 201309, India"}
UNSUB_BODY = "{{greeting}}\n\nA short note for {{company}}.\n\n— {{sender}}\n\nUnsubscribe: {{unsub_link}}"


class FakeTransport:
    """Records every outgoing request (channels.http.request shape) and returns a canned reply."""

    def __init__(self, reply=None, error=None):
        self.calls, self.reply, self.error = [], reply or {"id": "msg_1"}, error

    def __call__(self, url, headers=None, body=None, **kwargs):
        self.calls.append({"url": url, "headers": headers or {}, "body": body, **kwargs})
        if self.error:
            raise self.error
        return self.reply


def public_resolver(host):
    return ["93.184.216.34"]


def make_client(tmp_path, csv=CSV, **kwargs):
    kwargs.setdefault("channel_transport", FakeTransport())
    kwargs.setdefault("resolver", public_resolver)
    c = TestClient(create_app(data_file=tmp_path / "d.json", **kwargs))
    if csv:
        c.post("/api/leads/import", json={"csv": csv, "source": "apollo"})
    return c


def store_of(c):
    return c.app.state.store


def lead(c, company="Acme"):
    return next(l for l in c.get("/api/leads").json() if l["company"] == company)


def patch_lead(c, lead_id, **fields):
    return store_of(c).update_lead(lead_id, lambda l: {**l, **fields})


def node(nid, ntype, x=0, y=0, **config):
    return {"id": nid, "type": ntype, "config": config, "x": x, "y": y}


def edge(source, target, label="next"):
    return {"id": f"{source}-{label}-{target}", "from": source, "to": target, "label": label}


def chain(*ids):
    return [edge(a, b) for a, b in zip(ids, ids[1:])]


def email_node(nid="mail", subject="Hello {{company}}", body=UNSUB_BODY, **extra):
    return node(nid, "email", subject=subject, body=body, **extra)


def make_flow(c, nodes, edges, name="Test flow", activate=True, stop_on_reply=True):
    r = c.post("/api/flows", json={"name": name, "nodes": nodes, "edges": edges, "settings": {"stop_on_reply": stop_on_reply}})
    assert r.status_code == 200, r.text
    flow = r.json()
    if activate:
        a = c.post(f"/api/flows/{flow['id']}/activate")
        assert a.status_code == 200, a.text
        flow = a.json()
    return flow


def enroll(c, flow, *lead_ids):
    return c.post(f"/api/flows/{flow['id']}/enroll", json={"lead_ids": list(lead_ids)}).json()


def tick(c, at=T0):
    return c.post("/api/flows/tick", json={"now": at.isoformat()}).json()


def runs(c, flow):
    return c.get(f"/api/flows/{flow['id']}/runs").json()


def flow_outbox(c):
    return [m for m in c.get("/api/outbox").json() if m.get("flow_id")]


def timeline_texts(c, lead_id):
    return [e["text"] for e in c.get(f"/api/leads/{lead_id}").json()["timeline"]]


def connect_email(c):
    c.put("/api/integrations/sending_domain", json={"mode": "api", "values": {
        "domain": "mail.example.in", "from_name": "Priya", "from_email": "hello@mail.example.in", "api_key": "re_secret"}})


def go_live(c):
    c.put("/api/settings", json={"public_base_url": "https://outreach.example.in", **IDENTITY})
    r = c.put("/api/settings", json={"live_sending": True})
    assert r.status_code == 200, r.text
