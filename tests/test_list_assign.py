"""List assignment: assign / unassign / split / share, who sees which list, workspace isolation, sequences left alone."""
import pytest

from app import lead_access, list_assign, lists
from tests.ownership_helpers import lead, new_list, rig as make_rig


@pytest.fixture
def r(tmp_path): return make_rig(tmp_path)
@pytest.fixture
def off(tmp_path): return make_rig(tmp_path, mode_on=False)


def lid(r, c): return r.lead[c]["id"]
def owners(r): return {l["company"]: l.get("owner") for l in r.store.read("leads")}


def test_assign_moves_free_leads_and_reports_owned_by_others(r):
    row = new_list(r, "Mix", ["Acme", "Beta Ltd", "Cora Inc"])
    out = r.c.put(f"/api/lists/{row['id']}/assign", json={"to": "Priya"}, headers=r.h["owner"]).json()
    assert out["moved"] == 1 and [s["owner"] for s in out["skipped"]] == ["Ravi"]       # Cora moved; Beta already hers; Acme is Ravi's
    assert owners(r)["Cora Inc"] == "Priya" and owners(r)["Acme"] == "Ravi"
    assert out["list"]["assigned_to"] == "Priya" and out["list"]["assigned_by"]
    out = r.c.put(f"/api/lists/{row['id']}/assign", json={"to": "Priya", "take_over": True}, headers=r.h["owner"]).json()
    assert owners(r)["Acme"] == "Priya" and out["skipped"] == []
    assert "list Mix" in lead(r, "Acme")["timeline"][-1]["text"]


def test_assign_unknown_or_other_workspace_login_refused(r):
    row = new_list(r, "L", ["Cora Inc"])
    assert r.c.put(f"/api/lists/{row['id']}/assign", json={"to": "Nobody"}, headers=r.h["owner"]).status_code == 400
    assert r.c.put(f"/api/lists/{row['id']}/assign", json={"to": "Adm2"}, headers=r.h["owner"]).status_code == 400    # Adm2 works in w2 only
    assert owners(r)["Cora Inc"] is None


def test_unassign_goes_to_the_admin_pool_and_leaves_lead_owners(r):
    row = new_list(r, "L", ["Beta Ltd"], assigned_to="Priya")
    out = r.c.put(f"/api/lists/{row['id']}/assign", json={"to": ""}, headers=r.h["adm"]).json()
    assert out["list"]["assigned_to"] == "" and owners(r)["Beta Ltd"] == "Priya"
    assert r.c.get("/api/lists", headers=r.h["priya"]).json() == []                       # a pool list is not hers any more
    assert [x["id"] for x in list_assign.pool(r.store, "main")] == [row["id"]]


def test_assign_changes_no_running_sequence(r):
    from app import linkedin_seq as li
    from app.routes_outreach import enroll_lead
    acme = lid(r, "Acme")
    enroll_lead(r.store, acme, by="Ravi")
    li.start(r.store, acme)
    before = lead(r, "Acme")
    row = new_list(r, "L", ["Acme"])
    r.c.put(f"/api/lists/{row['id']}/assign", json={"to": "Priya", "take_over": True}, headers=r.h["owner"])
    after = lead(r, "Acme")
    assert after["owner"] == "Priya" and after["sequence"] == before["sequence"] and after["linkedin_seq"] == before["linkedin_seq"]
    tasks = [t for t in r.store.read("tasks") if t.get("lead_id") == acme and t.get("kind") == "linkedin"]
    assert len(tasks) == 1                                                                 # nothing was scheduled or re-sent


def test_only_admins_of_that_workspace_assign(r):
    row = new_list(r, "L", ["Cora Inc"])
    assert r.c.put(f"/api/lists/{row['id']}/assign", json={"to": "Priya"}, headers=r.h["priya"]).status_code == 403
    assert r.c.put(f"/api/lists/{row['id']}/assign", json={"to": "Priya"}, headers=r.h["view"]).status_code == 403
    assert r.c.put(f"/api/lists/{row['id']}/assign", json={"to": "Priya"}, headers=r.h["adm2"]).status_code == 404    # another workspace's list
    assert r.c.put(f"/api/lists/{row['id']}/assign", json={"to": "Priya"}, headers=r.h["adm"]).status_code == 200


def test_split_even_and_by_count(r):
    r.set("Cora Inc", owner="")
    big = new_list(r, "Big", ["Cora Inc"])
    for i in range(5):                                                                      # five more free leads
        r.c.post("/api/leads/import", json={"csv": f"company,contact_name,email\nCo{i},P{i},p{i}@co{i}.com\n", "source": "apollo"}, headers=r.h["owner"])
    ids = [l["id"] for l in r.store.read("leads") if l["company"].startswith("Co") and l.get("workspace") in (None, "main")]
    lists.set_members(r.store, big["id"], ids, [], "Admin")
    total = len(lists.get(r.store, big["id"])["lead_ids"])
    out = r.c.post(f"/api/lists/{big['id']}/split", json={"members": ["Priya", "Ravi"], "mode": "even"}, headers=r.h["adm"]).json()
    assert sorted(x["leads"] for x in out["lists"]) == sorted([total - total // 2, total // 2]) and out["skipped"] == []
    mine = [x for x in r.c.get("/api/lists", headers=r.h["priya"]).json() if x["assigned_to"] == "Priya"]
    assert len(mine) == 1 and "Priya" in mine[0]["name"]
    out = r.c.post(f"/api/lists/{big['id']}/split", json={"members": ["Priya", "Ravi"], "mode": "count", "counts": [1, 2]}, headers=r.h["adm"])
    assert out.status_code == 200 and sorted(x["leads"] for x in out.json()["lists"]) == [1, 2]
    bad = r.c.post(f"/api/lists/{big['id']}/split", json={"members": ["Priya"], "mode": "count", "counts": [999]}, headers=r.h["adm"])
    assert bad.status_code == 400
    assert r.c.post(f"/api/lists/{big['id']}/split", json={"members": ["Priya"], "mode": "weird"}, headers=r.h["adm"]).status_code == 400


def test_split_leaves_leads_owned_by_other_people(r):
    row = new_list(r, "L", ["Acme", "Cora Inc"])                                            # Acme is Ravi's
    out = r.c.post(f"/api/lists/{row['id']}/split", json={"members": ["Priya"], "mode": "even"}, headers=r.h["adm"]).json()
    assert [s["owner"] for s in out["skipped"]] == ["Ravi"] and out["lists"][0]["leads"] == 1
    assert owners(r)["Cora Inc"] == "Priya" and owners(r)["Acme"] == "Ravi"


def test_share_is_view_only(r):
    row = new_list(r, "Shared", ["Beta Ltd"], assigned_to="Ravi")
    assert r.c.get(f"/api/lists/{row['id']}", headers=r.h["priya"]).status_code == 404
    out = r.c.put(f"/api/lists/{row['id']}/share", json={"users": ["Priya"]}, headers=r.h["adm"])
    assert out.status_code == 200 and out.json()["shared_with"] == ["Priya"]
    assert [x["id"] for x in r.c.get("/api/lists", headers=r.h["priya"]).json()] == [row["id"]]
    assert r.c.post(f"/api/lists/{row['id']}/members", json={"add": []}, headers=r.h["priya"]).status_code == 409
    assert r.c.delete(f"/api/lists/{row['id']}", headers=r.h["priya"]).status_code == 409
    assert r.c.put(f"/api/lists/{row['id']}/share", json={"users": ["Nobody"]}, headers=r.h["adm"]).status_code == 400


def test_lists_api_by_role(r):
    a = new_list(r, "Priya's", ["Beta Ltd"], assigned_to="Priya")
    b = new_list(r, "Ravi's", ["Acme"], assigned_to="Ravi")
    p = new_list(r, "Pool", ["Cora Inc"])
    w2 = new_list(r, "W2 list", ["Beta Ltd"], assigned_to="Priya", ws=r.w2)
    ids = lambda who: {x["id"] for x in r.c.get("/api/lists", headers=r.h[who]).json()}
    assert ids("priya") == {a["id"], w2["id"]} and ids("ravi") == {b["id"]}
    assert ids("adm") == {a["id"], b["id"], p["id"]} and ids("adm2") == {w2["id"]}          # admins: their workspace only
    assert ids("owner") == {a["id"], b["id"], p["id"], w2["id"]}
    assert r.c.get(f"/api/lists/{b['id']}", headers=r.h["priya"]).status_code == 404
    assert r.c.get(f"/api/lists/{a['id']}", headers=r.h["adm2"]).status_code == 404


def test_lists_api_with_switch_off_hides_nothing(off):
    a = new_list(off, "Priya's", ["Beta Ltd"], assigned_to="Priya")
    b = new_list(off, "Ravi's", ["Acme"], assigned_to="Ravi")
    p = new_list(off, "Pool", ["Cora Inc"])
    for who in ("priya", "ravi", "view"):
        assert {x["id"] for x in off.c.get("/api/lists", headers=off.h[who]).json()} == {a["id"], b["id"], p["id"]}
    assert off.c.get(f"/api/lists/{b['id']}", headers=off.h["priya"]).status_code == 200
    legacy = {k: v for k, v in b.items() if k not in ("assigned_to", "shared_with", "assigned_at", "assigned_by")}      # a list from before this feature
    assert list_assign.visible(off.store, {"role": "caller", "name": "Priya", "email": "priya@x.in"}, legacy)


def test_member_builds_lists_only_from_their_leads(r):
    mine, theirs = lid(r, "Beta Ltd"), lid(r, "Acme")
    out = r.c.post("/api/lists", json={"name": "Mine", "lead_ids": [mine, theirs]}, headers=r.h["priya"]).json()
    assert out["lead_ids"] == [mine] and out["assigned_to"] == "Priya"                       # Ravi's lead is silently not hers to list
    assert r.c.post("/api/lists", json={"name": "Nope", "lead_ids": [theirs]}, headers=r.h["priya"]).status_code == 400
    admin_list = r.c.post("/api/lists", json={"name": "Admin", "lead_ids": [mine, theirs]}, headers=r.h["owner"]).json()
    assert len(admin_list["lead_ids"]) == 2 and admin_list["assigned_to"] == ""
    names = {x["company"] for x in r.c.get("/api/lists/companies", headers=r.h["priya"]).json()}
    assert names == {"Beta Ltd", "Other WS Co"}
    members = r.c.get(f"/api/lists/{admin_list['id']}", headers=r.h["owner"]).json()["members"]
    assert len(members) == 2


def test_workspace_admin_function_level_send_rules(r):
    adm = {"role": "admin", "name": "Adm", "email": "adm@x.in", "workspaces": ["main"]}
    row = new_list(r, "L", ["Acme", "Cora Inc"])
    out = lists.start_email(r.store, row["id"], "Adm", user=adm)
    assert out["started"] == 2 and out["owner_refused"] == 0
    other = new_list(r, "Other", ["Beta Ltd"], ws=r.w2)
    r.store.update("lists", lambda rows: rows)
    out = lists.start_email(r.store, other["id"], "Adm", user={**adm, "workspaces": [r.w2 + "-x"]})      # an admin of a different workspace
    assert out["started"] == 0 and out["owner_refused"] == 1 and lead_access.ownership_on(r.store, "main")


def test_two_workspaces_same_person_stay_apart(r):
    r.mode(False)
    r.store.update("settings", lambda s: {**s, "ownership_mode": {"main": True}})            # only Main is on
    priya = {"role": "caller", "name": "Priya", "email": "priya@x.in", "workspaces": []}
    other = next(l for l in r.store.read("leads") if l["company"] == "Other WS Co")
    r.set("Other WS Co", id=other["id"], owner="Ravi")
    assert lead_access.can_send(r.store, priya, other)                                       # w2 is off: Priya is not stopped there
    assert not lead_access.can_send(r.store, {**priya, "workspaces": ["main"]}, other)       # ...but a Main-only login never reaches w2
