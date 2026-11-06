"""Several LinkedIn accounts: caps, gap and pause per account; a lead belongs to the account that took it; legacy agents unchanged."""
from datetime import timedelta

from app import linkedin_agent as agent, linkedin_seq as li
from tests.test_extension_api import URL, bearer, make, pair
from tests.test_linkedin_agent import TUE_11

A, B = {"X-LinkedIn-Account": "acct-a"}, {"X-LinkedIn-Account": "acct-b"}


def rig(tmp_path):
    c, owner, acme, globex = make(tmp_path)
    store = c.app.state.store
    agent.save_config(store, {"enabled": True, "gap_minutes": 3})
    li.start(store, acme)
    tok = pair(c, owner)["token"]
    return c, owner, store, acme, tok


def test_caps_gap_and_pause_are_per_account(tmp_path):
    c, owner, store, acme, tok = rig(tmp_path)
    store.update("settings", lambda s: {**s, "linkedin_daily_connections": 2})
    for i in range(2):
        agent.record(store, {"kind": "connect", "lead_id": f"l{i}", "account": "acct-a"}, now=TUE_11 + timedelta(minutes=4 * i))
    t = TUE_11 + timedelta(minutes=20)
    assert agent.control(store, t, "acct-a")["connections_left_today"] == 0
    assert agent.control(store, t, "acct-b")["connections_left_today"] == 2 and agent.control(store, t, "acct-b")["may_connect"]
    assert not agent.control(store, TUE_11 + timedelta(minutes=5), "acct-a")["may_connect"]          # gap after A's 2nd action
    assert agent.control(store, TUE_11 + timedelta(minutes=5), "acct-b")["may_connect"]              # B has no gap to wait
    agent.record(store, {"kind": "error", "fatal": True, "note": "checkpoint", "account": "acct-a"}, now=t)
    assert agent.config(store)["enabled"] is True                                                    # B keeps going
    assert "paused" in agent.control(store, t, "acct-a")["why_not"] and agent.control(store, t, "acct-b")["may_connect"]
    assert "acct-a" in store.read("notifications")[-1]["title"]
    c.put("/api/linkedin-agent", json={"resume_account": "acct-a"}, headers=bearer(owner))
    assert "paused" not in agent.control(store, t, "acct-a")["why_not"]
    accts = {a["account"]: a for a in c.get("/api/linkedin-agent", headers=bearer(owner)).json()["accounts"]}
    assert accts["acct-a"]["paused"] is None and "connect_today" in accts["acct-a"]["counts"]


def test_a_lead_belongs_to_the_first_account_that_claims_it(tmp_path):
    c, owner, store, acme, tok = rig(tmp_path)
    h = lambda extra: {**bearer(tok), **extra}
    assert [d["lead_id"] for d in c.get("/api/extension/queue", headers=h(A)).json()["due"]] == [acme]
    assert [d["lead_id"] for d in c.get("/api/extension/queue", headers=h(B)).json()["due"]] == [acme]
    assert c.post(f"/api/extension/leads/{acme}/claim", headers=h(A)).json()["account"] == "acct-a"
    assert c.post(f"/api/extension/leads/{acme}/claim", headers=h(A)).status_code == 200              # again: fine
    r = c.post(f"/api/extension/leads/{acme}/claim", headers=h(B))
    assert r.status_code == 409 and "acct-a" in r.json()["detail"]
    assert c.get("/api/extension/queue", headers=h(B)).json()["due"] == []
    assert c.get("/api/extension/match", params={"profile_url": URL}, headers=h(B)).json()["can_send"] is False
    assert c.post(f"/api/extension/leads/{acme}/sent", json={"step": 1}, headers=h(B)).status_code == 409
    assert c.post(f"/api/extension/leads/{acme}/sent", json={"step": 1}, headers=h(A)).status_code == 200
    assert c.post(f"/api/extension/leads/{acme}/accepted", headers=h(B)).status_code == 409
    assert c.post(f"/api/extension/leads/{acme}/accepted", headers=h(A)).status_code == 200
    assert c.get("/api/extension/queue", headers=h(B)).json()["waiting_for_acceptance"] == []
    lead = c.get(f"/api/leads/{acme}", headers=bearer(owner)).json()
    assert lead["linkedin_seq"]["account"] == "acct-a" and any("acct-a took" in e["text"] for e in lead["timeline"])


def test_an_agent_without_an_account_works_as_before(tmp_path):
    c, owner, store, acme, tok = rig(tmp_path)
    assert c.post(f"/api/extension/leads/{acme}/claim", headers=bearer(tok)).json() == {"ok": True, "account": ""}
    assert c.post(f"/api/extension/leads/{acme}/sent", json={"step": 1}, headers=bearer(tok)).status_code == 200
    agent.record(store, {"kind": "error", "fatal": True, "note": "checkpoint"}, now=TUE_11)
    assert agent.config(store)["enabled"] is False
