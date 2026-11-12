"""Lead ownership at every send path: with the workspace switch on, a member sends only to leads they own, an admin of the
lead's workspace to all of them, nobody across workspaces; with the switch off nothing changes."""
import pytest

from app import linkedin_accounts as accts, linkedin_seq as li
from tests.ownership_helpers import lead, new_list, rig as make_rig


@pytest.fixture
def r(tmp_path): return make_rig(tmp_path)
@pytest.fixture
def off(tmp_path): return make_rig(tmp_path, mode_on=False)


def lid(r, company): return r.lead[company]["id"]
BELONGS = "belongs to"


# ---- single lead: email enrol
def test_enroll_refuses_someone_elses_lead(r):
    res = r.c.post(f"/api/leads/{lid(r, 'Acme')}/enroll", headers=r.h["priya"])             # Acme is Ravi's
    assert res.status_code == 409 and "Ravi" in res.json()["detail"] and not lead(r, "Acme").get("sequence")
    assert r.c.post(f"/api/leads/{lid(r, 'Beta Ltd')}/enroll", headers=r.h["priya"]).status_code == 200
    assert r.c.post(f"/api/leads/{lid(r, 'Cora Inc')}/enroll", headers=r.h["priya"]).status_code == 409     # nobody's lead


def test_enroll_admin_same_workspace_only(r):
    assert r.c.post(f"/api/leads/{lid(r, 'Acme')}/enroll", headers=r.h["adm"]).status_code == 200
    assert r.c.post(f"/api/leads/{r.other['id']}/enroll", headers=r.h["adm"]).status_code == 404     # another workspace: not even visible
    assert r.c.post(f"/api/leads/{lid(r, 'Beta Ltd')}/enroll", headers=r.h["adm2"]).status_code == 404   # w2's admin on a Main lead


def test_enroll_mode_off_unchanged(off):
    assert off.c.post(f"/api/leads/{lid(off, 'Acme')}/enroll", headers=off.h["priya"]).status_code == 200


# ---- bulk enrol
def test_bulk_enroll(r):
    ids = [lid(r, "Acme"), lid(r, "Beta Ltd"), lid(r, "Cora Inc")]
    out = r.c.post("/api/leads/bulk", json={"lead_ids": ids, "action": "enroll"}, headers=r.h["priya"]).json()
    by = {x["id"]: x for x in out["results"]}
    assert by[ids[1]]["ok"] and not by[ids[0]]["ok"] and BELONGS in by[ids[0]]["reason"] and not by[ids[2]]["ok"]
    assert lead(r, "Acme").get("sequence") is None and lead(r, "Beta Ltd")["sequence"]
    out = r.c.post("/api/leads/bulk", json={"lead_ids": [ids[0], ids[2]], "action": "enroll"}, headers=r.h["adm"]).json()
    assert out["ok"] == 2


def test_bulk_enroll_mode_off(off):
    out = off.c.post("/api/leads/bulk", json={"lead_ids": [lid(off, "Acme")], "action": "enroll"}, headers=off.h["priya"]).json()
    assert out["ok"] == 1


# ---- list start-email / start-linkedin
def test_list_start_email(r):
    mixed = new_list(r, "Mixed", ["Acme", "Beta Ltd"], assigned_to="Priya")
    out = r.c.post(f"/api/lists/{mixed['id']}/start-email", headers=r.h["priya"]).json()
    assert out["started"] == 1 and out["owner_refused"] == 1 and BELONGS in out["refused"][0]["reason"]
    assert not lead(r, "Acme").get("sequence")
    only_ravi = new_list(r, "Ravi's", ["Acme"], assigned_to="Priya")                 # a list she works, but the lead is Ravi's
    res = r.c.post(f"/api/lists/{only_ravi['id']}/start-email", headers=r.h["priya"])
    assert res.status_code == 409 and "Ravi" in res.json()["detail"]
    assert r.c.post(f"/api/lists/{only_ravi['id']}/start-email", headers=r.h["owner"]).json()["started"] == 1


def test_list_start_linkedin(r):
    mixed = new_list(r, "Mixed", ["Acme", "Beta Ltd"], assigned_to="Priya")
    out = r.c.post(f"/api/lists/{mixed['id']}/start-linkedin", headers=r.h["priya"]).json()
    assert out["started"] == 1 and out["owner_refused"] == 1 and not lead(r, "Acme").get("linkedin_seq") and lead(r, "Beta Ltd")["linkedin_seq"]
    only_ravi = new_list(r, "Ravi's", ["Acme"], assigned_to="Priya")
    assert r.c.post(f"/api/lists/{only_ravi['id']}/start-linkedin", headers=r.h["priya"]).status_code == 409
    assert r.c.post(f"/api/lists/{only_ravi['id']}/start-linkedin", headers=r.h["owner"]).json()["started"] == 1


def test_list_start_mode_off(off):
    row = new_list(off, "Any", ["Acme"], assigned_to="Priya")
    assert off.c.post(f"/api/lists/{row['id']}/start-email", headers=off.h["priya"]).json()["started"] == 1
    assert off.c.post(f"/api/lists/{row['id']}/start-linkedin", headers=off.h["priya"]).json()["started"] == 1


def test_view_only_share_cannot_start(r):
    row = new_list(r, "Shared", ["Beta Ltd"], assigned_to="Ravi")
    r.store.update("lists", lambda rows: [{**x, "shared_with": ["Priya"]} if x["id"] == row["id"] else x for x in rows])
    assert r.c.get(f"/api/lists/{row['id']}", headers=r.h["priya"]).status_code == 200
    assert r.c.post(f"/api/lists/{row['id']}/start-email", headers=r.h["priya"]).status_code == 409


# ---- LinkedIn single-lead routes
def test_linkedin_start_sent_find(r):
    acme, beta = lid(r, "Acme"), lid(r, "Beta Ltd")
    assert r.c.post(f"/api/leads/{acme}/linkedin/start", headers=r.h["priya"]).status_code == 409
    assert r.c.post(f"/api/leads/{acme}/linkedin/find", json={"action": "skip"}, headers=r.h["priya"]).status_code == 409
    li.start(r.store, acme)                                             # someone started it; priya still cannot mark it sent
    assert r.c.post(f"/api/leads/{acme}/linkedin/sent", json={"step": 1}, headers=r.h["priya"]).status_code == 409
    assert not lead(r, "Acme")["linkedin_seq"].get("done")
    assert r.c.post(f"/api/leads/{beta}/linkedin/start", headers=r.h["priya"]).status_code == 200
    assert r.c.post(f"/api/leads/{beta}/linkedin/sent", json={"step": 1}, headers=r.h["priya"]).status_code == 200
    assert r.c.post(f"/api/leads/{acme}/linkedin/sent", json={"step": 1}, headers=r.h["adm"]).status_code == 200
    assert r.c.post(f"/api/leads/{r.other['id']}/linkedin/start", headers=r.h["adm"]).status_code == 404


def test_linkedin_routes_mode_off(off):
    assert off.c.post(f"/api/leads/{lid(off, 'Acme')}/linkedin/start", headers=off.h["priya"]).status_code == 200


def test_linkedin_queue_shows_only_own(r):
    for c in ("Acme", "Beta Ltd"):
        li.start(r.store, lid(r, c))
    names = lambda who: {d["company"] for d in r.c.get("/api/linkedin/queue", headers=r.h[who]).json()["due"]}
    assert names("priya") == {"Beta Ltd"} and names("adm") == {"Acme", "Beta Ltd"}


# ---- extension (paired Chrome assistant / LinkedIn agent)
def pair(r, who):
    from app import linkedin_agent
    linkedin_agent.save_config(r.store, {"enabled": True, "weekdays_only": False, "start_hour": 0, "end_hour": 24, "gap_minutes": 3})      # claim re-checks the switch and hours and needs the LinkedIn account
    return {"Authorization": "Bearer " + r.c.post("/api/extension/pair", json={"label": "t"}, headers=r.h[who]).json()["token"],
            "X-LinkedIn-Account": "li-tool"}


def test_extension_paths(r):
    acme, beta = lid(r, "Acme"), lid(r, "Beta Ltd")
    for c in ("Acme", "Beta Ltd"):
        li.start(r.store, lid(r, c))
    tool = pair(r, "priya")
    assert r.c.post(f"/api/extension/leads/{acme}/claim", headers=tool).status_code == 409
    assert r.c.post(f"/api/extension/leads/{acme}/sent", json={"step": 1}, headers=tool).status_code == 409
    assert r.c.post(f"/api/extension/leads/{beta}/claim", headers=tool).status_code == 200
    assert r.c.post(f"/api/extension/leads/{acme}/lookup-result", json={"cards": []}, headers=tool).status_code in (409, 404)
    assert r.c.get("/api/extension/match", params={"profile_url": "https://www.linkedin.com/in/acme"}, headers=tool).json()["matched"] is False
    assert r.c.get("/api/extension/match", params={"profile_url": "https://www.linkedin.com/in/beta"}, headers=tool).json()["matched"] is True
    assert {d["company"] for d in r.c.get("/api/extension/queue", headers=tool).json()["due"]} == {"Beta Ltd"}


def test_extension_admin_tool_acts_on_the_workspace(r):
    acme = lid(r, "Acme")
    li.start(r.store, acme)
    tool = pair(r, "adm")
    assert r.c.post(f"/api/extension/leads/{acme}/claim", headers=tool).status_code == 200
    assert r.c.get("/api/extension/match", params={"profile_url": "https://www.linkedin.com/in/acme"}, headers=tool).json()["matched"] is True
    assert r.c.post(f"/api/extension/leads/{r.other['id']}/claim", headers=tool).status_code in (403, 404, 409)


def test_extension_mode_off(off):
    acme = lid(off, "Acme")
    li.start(off.store, acme)
    tool = pair(off, "priya")
    assert off.c.post(f"/api/extension/leads/{acme}/claim", headers=tool).status_code == 200
    assert off.c.get("/api/extension/match", params={"profile_url": "https://www.linkedin.com/in/acme"}, headers=tool).json()["matched"] is True


# ---- daily auto-start (account agent, and the global one)
def test_auto_start_for_only_leads_the_user_may_send_to(r):
    r.store.update("settings", lambda s: {**s, "linkedin_auto_start": True})
    users = {u["email"]: u for u in r.store.read("settings")["users"]}
    started = {s["lead_id"] for s in accts.auto_start_for(r.store, "li-a", 10, user={**users["priya@x.in"], "name": "Priya"})}
    assert started == {lid(r, "Beta Ltd")}
    system = {s["lead_id"] for s in accts.auto_start_for(r.store, "li-b", 10)}               # no person: only leads that have an owner
    assert system == {lid(r, "Acme")} and lid(r, "Cora Inc") not in system


def test_global_auto_start(r):
    r.store.update("settings", lambda s: {**s, "linkedin_auto_start": True})
    priya = {"role": "caller", "name": "Priya", "email": "priya@x.in"}
    assert {s["lead_id"] for s in li.auto_start(r.store, 10, user=priya)} == {lid(r, "Beta Ltd")}
    assert {s["lead_id"] for s in li.auto_start(r.store, 10)} == {lid(r, "Acme")}            # the system skips the lead nobody owns


def test_auto_start_mode_off_unchanged(off):
    off.store.update("settings", lambda s: {**s, "linkedin_auto_start": True})
    assert {s["lead_id"] for s in li.auto_start(off.store, 10)} == {lid(off, c) for c in ("Acme", "Beta Ltd", "Cora Inc")}


# ---- MCP and the Leads list
def mcp(r, who, name, args=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}}
    out = r.c.post("/api/mcp", json=body, headers=pair(r, who)).json()["result"]
    return out


def test_mcp_token_sees_only_own_leads(r):
    import json
    seen = json.loads(mcp(r, "priya", "search_leads")["content"][0]["text"])
    assert {l["company"] for l in seen["leads"]} == {"Beta Ltd", "Other WS Co"}
    assert mcp(r, "priya", "get_lead", {"lead_id": lid(r, "Acme")}).get("isError")
    assert mcp(r, "priya", "add_lead_note", {"lead_id": lid(r, "Acme"), "note": "x"}).get("isError")
    assert json.loads(mcp(r, "owner", "search_leads")["content"][0]["text"])["count"] == 4     # an owner's token: everything


def test_mcp_mode_off(off):
    import json
    assert json.loads(mcp(off, "priya", "search_leads")["content"][0]["text"])["count"] == 4


def test_leads_list_api(r):
    companies = lambda who: {l["company"] for l in r.c.get("/api/leads", headers=r.h[who]).json()}
    assert companies("priya") == {"Beta Ltd", "Other WS Co"} and companies("ravi") == {"Acme"}      # Priya owns a lead in the second workspace too
    assert companies("adm") == {"Acme", "Beta Ltd", "Cora Inc"} and companies("adm2") == {"Other WS Co"}
    assert companies("view") == {"Acme", "Beta Ltd", "Cora Inc", "Other WS Co"}              # viewers cannot send; the plan lets them read
    assert r.c.get(f"/api/leads/{lid(r, 'Acme')}", headers=r.h["priya"]).status_code == 404
    assert r.c.get(f"/api/leads/{lid(r, 'Beta Ltd')}", headers=r.h["priya"]).status_code == 200


def test_leads_list_api_mode_off_hides_nothing(off):
    for who in ("priya", "ravi", "view"):
        assert len(off.c.get("/api/leads", headers=off.h[who]).json()) == 4
    assert off.c.get(f"/api/leads/{lid(off, 'Acme')}", headers=off.h["priya"]).status_code == 200


def test_switch_is_per_workspace(r):
    r.mode(False)
    r.store.update("settings", lambda s: {**s, "ownership_mode": {r.w2: True}})            # only the second workspace is on
    assert {l["company"] for l in r.c.get("/api/leads", headers=r.h["priya"]).json()} == {"Acme", "Beta Ltd", "Cora Inc", "Other WS Co"}   # Main off
    assert r.c.post(f"/api/leads/{lid(r, 'Acme')}/enroll", headers=r.h["priya"]).status_code == 200
