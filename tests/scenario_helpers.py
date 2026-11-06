"""Helpers for the industry scenario tests: a world with a controllable clock, realistic leads, and readers that say
exactly what the app did for each lead (emails, WhatsApp, tasks, notifications, stage, tags, run path).

Everything runs in practice mode with fake transports: nothing is ever sent."""
import re
from datetime import datetime, timedelta, timezone

import pytest

from tests.flow_helpers import FakeTransport, make_client, public_resolver, store_of

IST = timezone(timedelta(hours=5, minutes=30))
BRACES = re.compile(r"\{\{|\}\}")
HEADER = "company,contact_name,title,email,phone,website,city,country,open_roles,company_size,hiring for\n"


def ist(day: int, hour: int, minute: int = 0, month: int = 11) -> datetime:
    """A moment in November 2026, India time. 2 Nov is a Monday."""
    return datetime(2026, month, day, hour, minute, tzinfo=IST)


def ist_text(stamp) -> str:
    """'Tue 03 Nov 10:30' for an ISO stamp or datetime, in India time."""
    when = stamp if isinstance(stamp, datetime) else datetime.fromisoformat(str(stamp))
    return when.astimezone(IST).strftime("%a %d %b %H:%M")


def csv_of(rows: list) -> str:
    return HEADER + "".join(",".join(str(v) for v in row) + "\n" for row in rows)


class World:
    """One app with its own data file, a clock the test moves, and the tick endpoint to run time forward."""

    def __init__(self, tmp_path, monkeypatch, start: datetime, csv: str = None, source: str = "apollo", **kwargs):
        from app import routes_flows, routes_outreach
        self.now = start
        real_resolve = routes_outreach.resolve_now
        monkeypatch.setattr(routes_outreach, "resolve_now", lambda raw: real_resolve(raw) if raw else self.now)
        monkeypatch.setattr(routes_flows, "now_iso", lambda: self.now.astimezone(timezone.utc).isoformat(timespec="seconds"))
        kwargs.setdefault("channel_transport", FakeTransport())
        kwargs.setdefault("resolver", public_resolver)
        self.c = make_client(tmp_path, csv=None, **kwargs)
        self.store = store_of(self.c)
        if csv:
            self.add_leads(csv, source)

    # ------------------------------------------------------------ setup
    def add_leads(self, csv: str, source: str = "apollo") -> dict:
        r = self.c.post("/api/leads/import", json={"csv": csv, "source": source})
        assert r.status_code == 200, r.text
        return r.json()

    def team(self, *names: str, caller: str = None) -> None:
        body = {"team": [{"name": n, "email": "", "phone": "", "role": "rep"} for n in names]}
        if caller:
            body["caller_name"] = caller
        assert self.c.put("/api/team", json=body).status_code == 200

    def lead(self, company: str) -> dict:
        return next(l for l in self.c.get("/api/leads").json() if l["company"] == company)

    def id(self, company: str) -> str:
        return self.lead(company)["id"]

    def full(self, company_or_id: str) -> dict:
        """The saved lead with every field (timeline included)."""
        rows = self.store.read("leads")
        return next(l for l in rows if l["id"] == company_or_id or l.get("company") == company_or_id)

    def consent(self, company: str, text: str = "Said yes to WhatsApp on a call with Priya, 2 Nov") -> None:
        r = self.c.post(f"/api/leads/{self.id(company)}/consent", json={"text": text, "channel": "whatsapp"})
        assert r.status_code == 200, r.text

    def owner(self, company: str, owner: str) -> None:
        r = self.c.post(f"/api/leads/{self.id(company)}/owner", json={"owner": owner})
        assert r.status_code == 200, r.text

    def tag(self, company: str, *tags: str) -> None:
        r = self.c.post(f"/api/leads/{self.id(company)}/tags", json={"add": list(tags)})
        assert r.status_code == 200, r.text

    def flow(self, name: str, nodes: list, edges: list, stop_on_reply: bool = True, activate: bool = True) -> dict:
        r = self.c.post("/api/flows", json={"name": name, "nodes": nodes, "edges": edges, "settings": {"stop_on_reply": stop_on_reply}})
        assert r.status_code == 200, r.text
        flow = r.json()
        assert flow["errors"] == [], flow["errors"]
        if activate:
            a = self.c.post(f"/api/flows/{flow['id']}/activate")
            assert a.status_code == 200, a.text
            flow = a.json()
        return flow

    def enroll(self, flow: dict, *companies: str) -> dict:
        ids = [self.id(name) for name in companies]
        return self.c.post(f"/api/flows/{flow['id']}/enroll", json={"lead_ids": ids}).json()

    def reply(self, company: str, text: str) -> dict:
        r = self.c.post(f"/api/leads/{self.id(company)}/reply", json={"text": text})
        assert r.status_code == 200, r.text
        return r.json()

    def unsubscribe(self, company: str) -> None:
        from app.unsubscribe import unsub_path
        assert self.c.post(unsub_path(self.store, self.id(company))).status_code == 200

    def delete(self, company: str) -> None:
        r = self.c.post("/api/leads/bulk", json={"lead_ids": [self.id(company)], "action": "delete"})
        assert r.status_code == 200 and r.json()["ok"] == 1, r.text

    # ------------------------------------------------------------ time
    def tick(self, at: datetime) -> dict:
        self.now = at
        r = self.c.post("/api/flows/tick", json={"now": at.isoformat()})
        assert r.status_code == 200, r.text
        return r.json()

    def run_until(self, end: datetime, every: timedelta = timedelta(minutes=30)) -> None:
        """Tick like the minute timer would (at a coarser step) from the current time up to `end`."""
        at = self.now
        while at < end:
            at = min(at + every, end)
            self.tick(at)

    # ------------------------------------------------------------ what happened
    def emails(self, company: str = None) -> list:
        rows = [m for m in self.c.get("/api/outbox").json() if m.get("flow_id")]
        if company:
            lid = self.id(company)
            rows = [m for m in rows if m["lead_id"] == lid]
        return sorted(rows, key=lambda m: (m["sent_at"], m["to"]))

    def whatsapps(self, company: str = None) -> list:
        rows = self.c.get("/api/whatsapp/outbox").json()
        rows = rows if isinstance(rows, list) else rows.get("messages", [])
        if company:
            lid = self.id(company)
            rows = [m for m in rows if m["lead_id"] == lid]
        return sorted(rows, key=lambda m: (m["sent_at"], m["to"]))

    def tasks(self, company: str) -> list:
        lid = self.id(company)
        return [t for t in self.c.get("/api/tasks").json() if t.get("lead_id") == lid]

    def notifications(self, company: str = None) -> list:
        rows = self.c.get("/api/notifications").json()
        if company:
            lid = self.id(company)
            rows = [n for n in rows if n.get("lead_id") == lid]
        return list(reversed(rows))

    def runs(self, flow: dict) -> dict:
        """{company: run} for a flow."""
        names = {l["id"]: l["company"] for l in self.store.read("leads")}
        return {names.get(r["lead_id"], r["lead_id"]): r for r in self.c.get(f"/api/flows/{flow['id']}/runs").json()}

    def timeline(self, company: str) -> list:
        return [e["text"] for e in self.full(company)["timeline"]]

    def simulate(self, flow: dict, company: str, assume: str = "timeout") -> list:
        r = self.c.post(f"/api/flows/{flow['id']}/simulate", json={"lead_id": self.id(company), "assume_reply": assume})
        assert r.status_code == 200, r.text
        return sim_path(r.json())


def run_path(run: dict) -> list:
    """The steps a real run went through, in order: the trigger left out, a wait's start and end counted once, and a
    step that only waited for its time (caps, business hours) counted once."""
    path = []
    for i, h in enumerate(run["history"]):
        if i == 0 or h["node"] is None:
            continue
        if path and path[-1] == h["node"]:
            continue
        path.append(h["node"])
    return path


def sim_path(simulation: dict) -> list:
    return [p["node"] for p in simulation["path"][1:]]


def assert_rendered(*texts: str) -> None:
    for text in texts:
        assert not BRACES.search(text or ""), f"unfilled placeholder in: {text!r}"


@pytest.fixture()
def world_factory(tmp_path, monkeypatch):
    def make(start: datetime, csv: str = None, source: str = "apollo", **kwargs) -> World:
        return World(tmp_path, monkeypatch, start, csv, source, **kwargs)
    return make
