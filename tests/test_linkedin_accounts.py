"""Which LinkedIn account contacts a lead (per lead, bulk, owner default), the agent queue / claim / auto-start
respecting it, no move after step 1, and the LinkedIn stage board (counts, message N rows, CSV, scope, audit)."""
from datetime import timedelta

from fastapi.testclient import TestClient

from app import linkedin_accounts as accts, linkedin_agent as agent, linkedin_seq as li, linkedin_stages as stages
from app.activities import now_utc
from app.main import create_app

ADMIN = "admin-pass-123"
CSV = ("company,contact_name,title,email,linkedin_url,open_roles\n"
       "Acme,Ravi Kumar,CHRO,ravi@acme.com,https://www.linkedin.com/in/ravi-kumar,1\n"
       "Beta,Sita Rao,HR Head,sita@beta.in,https://www.linkedin.com/in/sita-rao,2\n"
       "Gamma,Anil Shah,CEO,anil@gamma.in,https://www.linkedin.com/in/anil-shah,3\n"
       "Delta,Om Das,Founder,om@delta.in,https://www.linkedin.com/in/om-das,4\n")
A, B = {"X-LinkedIn-Account": "acct-a"}, {"X-LinkedIn-Account": "acct-b"}


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


def rig(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password=ADMIN), client=("203.0.113.9", 50000))
    owner = bearer(c.post("/api/login", json={"password": ADMIN}).json()["token"])
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=owner)
    store = c.app.state.store
    for acct, name in (("acct-a", "Asha"), ("acct-b", "Bala")):
        agent.heartbeat(store, "idle", account=acct, name=name)
    store.update("settings", lambda s: {**s, "team": [{"name": "Priya"}, {"name": "Karan"}]})
    ids = {l["company"]: l["id"] for l in store.read("leads")}
    tok = c.post("/api/extension/pair", json={"label": "agent"}, headers=owner).json()["token"]
    return c, owner, store, ids, tok


def lead(store, lead_id):
    return next(l for l in store.read("leads") if l["id"] == lead_id)


def audit_rows(store, action):
    return [r for r in store.read("audit") if r.get("action") == action]


def caller(c, owner, email="cal@example.com", **extra):
    c.post("/api/users", json={"email": email, "name": "Cal", "role": "caller", "password": "long-password-1", **extra}, headers=owner)
    return bearer(c.post("/api/login", json={"email": email, "password": "long-password-1"}).json()["token"])


# ---------- assignment ----------
def test_pick_account_per_lead_is_audited_and_on_the_timeline(tmp_path):
    c, owner, store, ids, _ = rig(tmp_path)
    known = c.get("/api/linkedin/accounts", headers=owner).json()
    assert [a["account"] for a in known["accounts"]] == ["acct-a", "acct-b"] and known["accounts"][0]["name"] == "Asha"
    r = c.put(f"/api/leads/{ids['Acme']}/linkedin-account", json={"account": "acct-b"}, headers=owner)
    assert r.status_code == 200 and r.json()["account"] == "acct-b" and r.json()["assigned_by"] == "lead"
    assert lead(store, ids["Acme"])["linkedin_account"] == "acct-b"
    assert any("any account → acct-b" in e["text"] for e in lead(store, ids["Acme"])["timeline"])
    row = audit_rows(store, "linkedin.account")[-1]
    assert row["target"] == "Acme" and "any account -> acct-b" in row["detail"] and ids["Acme"] in row["detail"]
    assert c.put(f"/api/leads/{ids['Acme']}/linkedin-account", json={"account": "nobody-here"}, headers=owner).status_code == 400
    assert c.put("/api/leads/missing/linkedin-account", json={"account": "acct-a"}, headers=owner).status_code == 404
    r = c.put(f"/api/leads/{ids['Acme']}/linkedin-account", json={"account": ""}, headers=owner)     # back to any account
    assert r.json()["account"] == "" and "linkedin_account" not in lead(store, ids["Acme"])
    assert "acct-b -> any account" in audit_rows(store, "linkedin.account")[-1]["detail"]


def test_bulk_assignment_answers_per_lead(tmp_path):
    c, owner, store, ids, _ = rig(tmp_path)
    r = c.post("/api/linkedin/assign", json={"lead_ids": [ids["Acme"], ids["Beta"], "nope"], "account": "acct-a"}, headers=owner).json()
    assert r["ok"] == 2 and r["failed"] == 1 and r["reasons"] == [{"reason": "not found", "count": 1}]
    assert {accts.assigned(lead(store, ids[k])) for k in ("Acme", "Beta")} == {"acct-a"}
    assert len(audit_rows(store, "linkedin.account")) == 2
    again = c.post("/api/linkedin/assign", json={"lead_ids": [ids["Acme"]], "account": "acct-a"}, headers=owner).json()
    assert again["ok"] == 0 and again["results"][0]["reason"] == "no change" and len(audit_rows(store, "linkedin.account")) == 2
    assert c.post("/api/linkedin/assign", json={"lead_ids": [], "account": "acct-a"}, headers=owner).status_code == 400


def test_owner_default_applies_unless_the_lead_has_its_own_pick(tmp_path):
    c, owner, store, ids, _ = rig(tmp_path)
    c.post("/api/leads/bulk", json={"lead_ids": [ids["Acme"], ids["Beta"]], "action": "owner", "value": "Priya"}, headers=owner)
    r = c.put("/api/linkedin/owner-accounts", json={"defaults": {"priya": "acct-b"}}, headers=owner)
    assert r.status_code == 200 and r.json()["owner_defaults"] == {"Priya": "acct-b"}
    assert audit_rows(store, "linkedin.owner_account")[-1]["detail"] == "default LinkedIn account none -> acct-b"
    settings = store.read("settings")
    assert accts.assigned(lead(store, ids["Acme"]), settings) == "acct-b" and accts.how_assigned(lead(store, ids["Acme"]), settings) == "owner"
    assert accts.assigned(lead(store, ids["Gamma"]), settings) == ""                     # no owner: any account
    c.put(f"/api/leads/{ids['Beta']}/linkedin-account", json={"account": "acct-a"}, headers=owner)
    assert accts.assigned(lead(store, ids["Beta"]), store.read("settings")) == "acct-a"   # the lead's own pick wins
    assert c.put("/api/linkedin/owner-accounts", json={"defaults": {"Stranger": "acct-a"}}, headers=owner).status_code == 400
    assert c.put("/api/linkedin/owner-accounts", json={"defaults": {"Priya": "zzz"}}, headers=owner).status_code == 400
    cal = caller(c, owner)
    assert c.put("/api/linkedin/owner-accounts", json={"defaults": {}}, headers=cal).status_code == 403    # owners only
    assert c.get("/api/linkedin/accounts", headers=cal).json()["owner_defaults"] == {"Priya": "acct-b"}


# ---------- agent queue, claim, no move after step 1 ----------
def test_queue_and_claim_respect_the_assignment(tmp_path):
    c, owner, store, ids, tok = rig(tmp_path)
    for k in ("Acme", "Beta", "Gamma"):
        li.start(store, ids[k])
    c.put(f"/api/leads/{ids['Acme']}/linkedin-account", json={"account": "acct-b"}, headers=owner)
    c.post("/api/leads/bulk", json={"lead_ids": [ids["Beta"]], "action": "owner", "value": "Karan"}, headers=owner)
    c.put("/api/linkedin/owner-accounts", json={"defaults": {"Karan": "acct-a"}}, headers=owner)
    due = lambda h: sorted(d["lead_id"] for d in c.get("/api/extension/queue", headers={**bearer(tok), **h}).json()["due"])
    assert due(A) == sorted([ids["Beta"], ids["Gamma"]])           # its owner-default lead + the unassigned one
    assert due(B) == sorted([ids["Acme"], ids["Gamma"]])
    r = c.post(f"/api/extension/leads/{ids['Acme']}/claim", headers={**bearer(tok), **A})
    assert r.status_code == 409 and "acct-b" in r.json()["detail"]
    assert c.post(f"/api/extension/leads/{ids['Beta']}/claim", headers={**bearer(tok), **B}).status_code == 409
    assert c.post(f"/api/extension/leads/{ids['Acme']}/sent", json={"step": 1}, headers={**bearer(tok), **A}).status_code == 409
    assert c.post(f"/api/extension/leads/{ids['Acme']}/claim", headers={**bearer(tok), **B}).json()["account"] == "acct-b"
    assert lead(store, ids["Acme"])["linkedin_seq"]["account"] == "acct-b"


def test_claimed_but_not_contacted_may_move_but_never_after_step_1(tmp_path):
    c, owner, store, ids, tok = rig(tmp_path)
    li.start(store, ids["Acme"])
    c.post(f"/api/extension/leads/{ids['Acme']}/claim", headers={**bearer(tok), **A})
    r = c.put(f"/api/leads/{ids['Acme']}/linkedin-account", json={"account": "acct-b"}, headers=owner)
    assert r.status_code == 200 and r.json()["account"] == "acct-b"
    assert "account" not in lead(store, ids["Acme"])["linkedin_seq"]
    assert c.post(f"/api/extension/leads/{ids['Acme']}/claim", headers={**bearer(tok), **A}).status_code == 409
    assert c.post(f"/api/extension/leads/{ids['Acme']}/claim", headers={**bearer(tok), **B}).status_code == 200
    assert c.post(f"/api/extension/leads/{ids['Acme']}/sent", json={"step": 1}, headers={**bearer(tok), **B}).status_code == 200
    r = c.put(f"/api/leads/{ids['Acme']}/linkedin-account", json={"account": "acct-a"}, headers=owner)
    assert r.status_code == 409 and "already contacted from LinkedIn account acct-b" in r.json()["detail"]
    bulk = c.post("/api/linkedin/assign", json={"lead_ids": [ids["Acme"]], "account": ""}, headers=owner).json()
    assert bulk["ok"] == 0 and "stays with that account" in bulk["results"][0]["reason"]
    assert accts.assigned(lead(store, ids["Acme"])) == "acct-b"
    # sent by hand (no account known) also never moves
    li.start(store, ids["Beta"]); li.mark_sent(store, ids["Beta"], 1)
    r = c.put(f"/api/leads/{ids['Beta']}/linkedin-account", json={"account": "acct-a"}, headers=owner)
    assert r.status_code == 409 and "never moved mid-sequence" in r.json()["detail"]


def test_auto_start_per_account_shares_the_work(tmp_path):
    c, owner, store, ids, tok = rig(tmp_path)
    store.update("settings", lambda s: {**s, "linkedin_auto_start": True, "linkedin_daily_connections": 2})
    c.put(f"/api/leads/{ids['Acme']}/linkedin-account", json={"account": "acct-b"}, headers=owner)
    c.put(f"/api/leads/{ids['Beta']}/linkedin-account", json={"account": "acct-a"}, headers=owner)
    started_a = [s["lead_id"] for s in accts.auto_start_for(store, "acct-a", 10)]
    assert started_a[0] == ids["Beta"] and len(started_a) == 2 and ids["Acme"] not in started_a       # own first, cap 2
    assert all(lead(store, i)["linkedin_seq"]["account"] == "acct-a" for i in started_a)
    assert accts.auto_start_for(store, "acct-a", 10) == []                     # its daily limit is used
    started_b = [s["lead_id"] for s in accts.auto_start_for(store, "acct-b", 1)]   # B's caps: 1 left today
    assert started_b == [ids["Acme"]]
    rest = [s["lead_id"] for s in accts.auto_start_for(store, "acct-b", 5)]
    assert len(rest) == 1 and rest[0] not in started_a and lead(store, rest[0])["linkedin_seq"]["account"] == "acct-b"
    store.update("settings", lambda s: {**s, "linkedin_auto_start": False})
    assert accts.auto_start_for(store, "acct-b", 5) == []


def test_queue_route_auto_starts_only_for_the_polling_account(tmp_path):
    c, owner, store, ids, tok = rig(tmp_path)
    agent.save_config(store, {"enabled": True, "start_hour": 0, "end_hour": 24, "weekdays_only": False})
    store.update("settings", lambda s: {**s, "linkedin_auto_start": True})
    c.post("/api/linkedin/assign", json={"lead_ids": [ids["Acme"], ids["Beta"], ids["Gamma"]], "account": "acct-b"}, headers=owner)
    q = c.get("/api/extension/queue", headers={**bearer(tok), **A}).json()
    assert [d["lead_id"] for d in q["due"]] == [ids["Delta"]]
    assert all(not lead(store, ids[k]).get("linkedin_seq") for k in ("Acme", "Beta", "Gamma"))


# ---------- stage board ----------
def _build_board(c, owner, store, ids, tok):
    """Acme: message 1 sent (step 3 due), Beta: waiting to accept, Gamma: queued, Delta: replied; plus extra leads."""
    now = now_utc()
    for k in ("Acme", "Beta", "Gamma", "Delta"):
        li.start(store, ids[k], now - timedelta(days=10))
    c.post(f"/api/extension/leads/{ids['Acme']}/claim", headers={**bearer(tok), **A})
    li.mark_sent(store, ids["Acme"], 1, now - timedelta(days=9))
    li.mark_accepted(store, ids["Acme"], now - timedelta(days=8))
    li.mark_sent(store, ids["Acme"], 2, now - timedelta(days=7))
    c.post(f"/api/extension/leads/{ids['Beta']}/claim", headers={**bearer(tok), **B})
    li.mark_sent(store, ids["Beta"], 1, now - timedelta(days=2))
    li.mark_sent(store, ids["Delta"], 1, now - timedelta(days=3))
    li.mark_replied(store, ids["Delta"], "")
    extra = ("company,contact_name,title,email,linkedin_url\n"
             "Eta,Eva Lal,HR,eva@eta.in,https://www.linkedin.com/in/eva-lal\n"
             "Zeta,Zed Roy,HR,zed@zeta.in,https://www.linkedin.com/in/zed-roy\n"
             "Theta,Tom Paul,HR,tom@theta.in,https://www.linkedin.com/in/tom-paul\n"
             "Iota,No Link,HR,no@iota.in,\n")
    c.post("/api/leads/import", json={"csv": extra, "source": "apollo"}, headers=owner)
    more = {l["company"]: l["id"] for l in store.read("leads")}
    li.start(store, more["Zeta"]); li.stop(store, more["Zeta"], "wrong person")
    li.start(store, more["Theta"]); store.update_lead(more["Theta"], lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "status": "finished", "done": [1, 2, 3]}})
    return more


def test_stage_board_counts_split_by_account_with_message_rows(tmp_path):
    c, owner, store, ids, tok = rig(tmp_path)
    more = _build_board(c, owner, store, ids, tok)
    b = c.get("/api/linkedin/stages", headers=owner).json()
    by = {s["key"]: s for s in b["stages"]}
    assert {k: by[k]["total"] for k in by} == {"not_contacted": 1, "queued": 1, "waiting_accept": 1, "accepted": 0, "message_1": 1,
                                               "replied": 1, "finished": 1, "stopped": 1}
    assert [s["key"] for s in b["stages"]][:5] == ["not_contacted", "queued", "waiting_accept", "accepted", "message_1"]
    assert by["message_1"]["label"] == "Message 1 sent, message 2 due" and by["message_1"]["by_account"] == {"acct-a": 1}
    task_due = min(t["due_at"] for t in store.read("tasks") if t.get("lead_id") == ids["Acme"] and t.get("status", "open") == "open")
    assert by["message_1"]["next_due"] == task_due
    assert by["waiting_accept"]["by_account"] == {"acct-b": 1} and by["queued"]["by_account"] == {"": 1}
    items = c.get("/api/linkedin/stages", params={"stage": "message_1"}, headers=owner).json()["items"]
    assert items == [{**items[0], "company": "Acme", "person": "Ravi Kumar", "account": "acct-a", "last_step": 2, "next_due": task_due}]
    stopped = c.get("/api/linkedin/stages", params={"stage": "stopped"}, headers=owner).json()["items"]
    assert stopped[0]["company"] == "Zeta" and stopped[0]["stopped_reason"] == "wrong person"
    only_b = {s["key"]: s["total"] for s in c.get("/api/linkedin/stages", params={"account": "acct-b"}, headers=owner).json()["stages"]}
    assert only_b["waiting_accept"] == 1 and "message_1" not in only_b
    unassigned = c.get("/api/linkedin/stages", params={"account": "__none", "stage": "queued"}, headers=owner).json()["items"]
    assert [i["company"] for i in unassigned] == ["Gamma"]
    c.post("/api/leads/bulk", json={"lead_ids": [ids["Gamma"]], "action": "owner", "value": "Priya"}, headers=owner)
    assert c.get("/api/linkedin/stages", params={"owner": "Priya"}, headers=owner).json()["total"] == 1
    assert more["Iota"] not in str(b)


def test_message_rows_grow_per_step(tmp_path):
    c, owner, store, ids, tok = rig(tmp_path)
    li.start(store, ids["Acme"]); li.mark_sent(store, ids["Acme"], 1); li.mark_accepted(store, ids["Acme"])
    assert stages.classify(lead(store, ids["Acme"])) == "accepted"
    li.mark_sent(store, ids["Acme"], 2); li.mark_sent(store, ids["Acme"], 3)
    assert stages.classify(lead(store, ids["Acme"])) == "message_2"
    assert stages.label("message_2") == "Message 2 sent, message 3 due"


def test_stage_csv_owner_only_audited(tmp_path):
    c, owner, store, ids, tok = rig(tmp_path)
    _build_board(c, owner, store, ids, tok)
    r = c.get("/api/linkedin/stages.csv", params={"stage": "message_1"}, headers=owner)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "linkedin-message-1-" in r.headers["content-disposition"]
    lines = r.text.lstrip("﻿").strip().splitlines()
    assert lines[0].startswith("company,person,title,account,owner,stage") and len(lines) == 2 and lines[1].startswith("Acme,Ravi Kumar")
    row = audit_rows(store, "linkedin.stages_export")[-1]
    assert row["target"] == "Message 1 sent, message 2 due" and row["detail"].startswith("1 rows")
    assert c.get("/api/linkedin/stages.csv", headers=caller(c, owner)).status_code == 403


def test_stage_board_and_assignment_respect_workspace_scope(tmp_path):
    c, owner, store, ids, tok = rig(tmp_path)
    w = c.post("/api/workspaces", json={"name": "Client One"}, headers=owner).json()
    assert c.post(f"/api/workspaces/{w['id']}/move", json={"lead_ids": [ids["Acme"]]}, headers=owner).status_code == 200
    assert lead(store, ids["Acme"])["workspace"] == w["id"]
    li.start(store, ids["Acme"]); li.start(store, ids["Beta"])
    raj = caller(c, owner, "raj@example.com", workspaces=[w["id"]])
    b = c.get("/api/linkedin/stages", headers=raj)
    assert b.status_code == 200 and b.json()["total"] == 1
    assert [i["company"] for i in c.get("/api/linkedin/stages", params={"stage": "queued"}, headers=raj).json()["items"]] == ["Acme"]
    r = c.post("/api/linkedin/assign", json={"lead_ids": [ids["Acme"], ids["Beta"]], "account": "acct-a"}, headers=raj).json()
    assert r["ok"] == 1 and {x["id"]: x.get("reason") for x in r["results"]}[ids["Beta"]] == "not found"
    assert c.put(f"/api/leads/{ids['Beta']}/linkedin-account", json={"account": "acct-a"}, headers=raj).status_code == 404
    assert c.get("/api/linkedin/accounts", headers=raj).status_code == 200
    assert c.get("/api/linkedin/stages", headers=owner).json()["total"] == 4


def test_leads_list_carries_stage_account_and_next_due(tmp_path):
    c, owner, store, ids, tok = rig(tmp_path)
    li.start(store, ids["Acme"])
    c.put(f"/api/leads/{ids['Acme']}/linkedin-account", json={"account": "acct-b"}, headers=owner)
    rows = {l["id"]: l for l in c.get("/api/leads", headers=owner).json()}
    acme = rows[ids["Acme"]]
    assert acme["li_stage"] == "queued" and acme["li_stage_filter"] == "queued" and acme["li_account"] == "acct-b" and acme["li_next_due"]
    assert rows[ids["Beta"]]["li_stage"] == "not_contacted" and rows[ids["Beta"]]["li_next_due"] is None
