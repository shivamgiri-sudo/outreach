"""Flows and campaigns with lead ownership on: a flow started by a person keeps that person's rights, one started by
the system needs an owned lead and says it acted for the owner; a campaign starts owned leads only. Switch off: unchanged."""
import pytest

from app import campaigns
from tests.flow_helpers import T0, chain, email_node, enroll, flow_outbox, lead, make_client, make_flow, node, store_of, tick, timeline_texts

TRIGGER = node("t", "trigger.manual")
MEMBER = {"email": "priya@x.in", "name": "Priya", "role": "caller", "password_hash": "x", "workspaces": []}


@pytest.fixture()
def c(tmp_path):
    return make_client(tmp_path)


def own(c, company, owner):
    store_of(c).update_lead(lead(c, company)["id"], lambda l: {**l, "owner": owner})


def switch(c, on):
    store_of(c).update("settings", lambda s: {**s, "ownership_mode": {"main": True} if on else {}})


def started_flow(c):
    return make_flow(c, [TRIGGER, email_node()], chain("t", "mail"))


def test_system_flow_emails_an_owned_lead_and_says_for_whom(c):
    own(c, "Acme", "Ravi"); switch(c, True)
    enroll(c, started_flow(c), lead(c, "Acme")["id"])
    tick(c, T0)
    assert len(flow_outbox(c)) == 1
    assert any("by system for Ravi" in t for t in timeline_texts(c, lead(c, "Acme")["id"]))


def test_system_flow_skips_a_lead_nobody_owns(c):
    switch(c, True)
    enroll(c, started_flow(c), lead(c, "Acme")["id"])
    tick(c, T0)
    assert flow_outbox(c) == []


def test_flow_started_by_a_member_needs_that_member_to_own_the_lead(c, monkeypatch):
    from app import auth, flow_engine
    monkeypatch.setattr(auth, "find_user", lambda store, email: MEMBER if email == "priya@x.in" else None)      # (a real user list would switch sign-in on)
    own(c, "Acme", "Ravi"); own(c, "Beta", "Priya"); switch(c, True)
    store, flow = store_of(c), started_flow(c)
    for company in ("Acme", "Beta"):
        flow_engine.enroll(store, flow, lead(c, company)["id"], T0, by="Priya", by_email="priya@x.in", via="by hand")
    tick(c, T0)
    sent_to = {m["to"] for m in flow_outbox(c)}
    assert sent_to == {"sam@beta.in"}                                     # Beta is Priya's; Acme is Ravi's and was refused


def test_flow_unchanged_with_switch_off(c):
    enroll(c, started_flow(c), lead(c, "Acme")["id"])
    tick(c, T0)
    assert len(flow_outbox(c)) == 1
    assert not any("by system for" in t for t in timeline_texts(c, lead(c, "Acme")["id"]))


def test_campaign_starts_owned_leads_only(c):
    own(c, "Acme", "Ravi"); switch(c, True)
    store = store_of(c)
    report = {"enrolled": 0, "errors": []}
    campaigns._enrol(store, {"name": "Camp", "id": "c1", "rules": {}}, [lead(c, "Acme")["id"], lead(c, "Beta")["id"]], report)
    assert report["enrolled"] == 1 and lead(c, "Acme")["sequence"] and not lead(c, "Beta").get("sequence")
    assert "no owner" in report["errors"][0]["message"]
    assert "by system for Ravi" in lead(c, "Acme")["timeline"][-1]["text"]


def test_campaign_unchanged_with_switch_off(c):
    report = {"enrolled": 0, "errors": []}
    campaigns._enrol(store_of(c), {"name": "Camp", "id": "c1", "rules": {}}, [lead(c, "Acme")["id"], lead(c, "Beta")["id"]], report)
    assert report["enrolled"] == 2
