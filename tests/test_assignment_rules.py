"""Lead assignment rules: ordered rules, round-robin teams, the API and the entry points. Off by default."""
import pytest
from fastapi.testclient import TestClient

from app import assignment as A
from app.main import create_app
from app.store import Store
from tests.limits_helpers import ADMIN, BOSS, LONG, bearer, make_rig

W1, W2 = "main", "w2"


def mk_store(tmp_path, names=("Ann", "Bob", "Cy", "Di")):
    store = Store(tmp_path / "d.json")
    store.update("settings", lambda s: {**s, "users": [
        {"email": f"{n.lower()}@x.in", "name": n, "role": "caller", "workspaces": [W1, W2]} for n in names]})
    return store


def lead(i, ws=W1, **kw):
    return {"id": f"l{i}", "workspace": ws, "owner": "", "stage": "new", "timeline": [], "source": kw.pop("source", "csv"), **kw}


def put(store, leads):
    store.update("leads", lambda old: [*old, *leads])


def cfg(teams, rules=(), on=True, **extra):
    return {"on": on, "teams": teams, "rules": list(rules), **extra}


def team(tid, members, default=False, cap=0):
    return {"id": tid, "name": tid.upper(), "members": members, "default": default, "daily_cap": cap}


def owner_of(store, lid):
    return next(l for l in store.read("leads") if l["id"] == lid)["owner"]


def test_off_by_default_changes_nothing(tmp_path):
    s = mk_store(tmp_path)
    A.save_config(s, W1, cfg([team("a", ["Ann"], True)], on=False))
    put(s, [lead(1)])
    assert A.assign_new(s, lead(1)) == "" and owner_of(s, "l1") == ""
    assert A.config(s, "never-set")["on"] is False


def test_first_matching_rule_wins_in_order(tmp_path):
    s = mk_store(tmp_path)
    A.save_config(s, W1, cfg([team("a", ["Ann"]), team("b", ["Bob"]), team("d", ["Cy"], True)], [
        {"id": "r1", "when": {"city": "Pune"}, "team": "a"},
        {"id": "r2", "when": {"city": "Pune", "tag": "vip"}, "team": "b"},
        {"id": "r3", "when": {"source": "meta"}, "team": "b"}]))
    put(s, [lead(1, city="pune", tags=["VIP"]), lead(2, source="meta", city="Delhi"), lead(3, city="Delhi")])
    assert A.assign_new(s, lead(1, city="pune", tags=["VIP"])) == "Ann"
    assert A.assign_new(s, lead(2, source="meta", city="Delhi")) == "Bob"
    assert A.assign_new(s, lead(3, city="Delhi")) == "Cy"                       # no rule: default team
    l = next(x for x in s.read("leads") if x["id"] == "l1")
    assert l["owner_why"]["kind"] == "rule" and l["owner_why"]["rule_id"] == "r1" and l["owner_why"]["at"]
    assert l["first_touch_due_at"] is None and any(e["kind"] == "owner" for e in l["timeline"])


def test_no_team_leaves_it_unassigned_and_counted(tmp_path):
    s = mk_store(tmp_path)
    A.save_config(s, W1, cfg([team("a", ["Ann"])], [{"id": "r1", "when": {"city": "Pune"}, "team": "a"}]))
    put(s, [lead(1, city="Delhi")])
    assert A.assign_new(s, lead(1, city="Delhi")) == ""
    assert A.stats(s, W1)["unassigned"] == 1
    assert A.run_unowned(s, W1) == {"assigned": 0, "left": 1}


def test_rotation_is_fair_nine_over_three(tmp_path):
    s = mk_store(tmp_path)
    A.save_config(s, W1, cfg([team("a", ["Ann", "Bob", "Cy"], True)]))
    put(s, [lead(i) for i in range(9)])
    for i in range(9):
        A.assign_new(s, lead(i))
    owners = [owner_of(s, f"l{i}") for i in range(9)]
    assert owners == ["Ann", "Bob", "Cy"] * 3
    assert {m["name"]: m["assigned"] for m in A.stats(s, W1)["members"]} == {"Ann": 3, "Bob": 3, "Cy": 3}


def test_next_member_skips_disabled_leave_and_over_cap(tmp_path):
    s = mk_store(tmp_path)
    s.update("settings", lambda st: {**st, "users": [
        {**u, **({"disabled": True} if u["name"] == "Ann" else {"on_leave": True} if u["name"] == "Bob" else {})} for u in st["users"]]})
    A.save_config(s, W1, cfg([team("a", ["Ann", "Bob", "Cy", "Di"], True, cap=1)]))
    assert A.next_member(s, W1, "a") == "Cy"
    put(s, [lead(1)])
    assert A.assign_new(s, lead(1)) == "Di"                                      # next_member only peeked; the pointer moved on
    put(s, [lead(2), lead(3)])
    assert A.assign_new(s, lead(2)) == "Cy"                                      # Cy had none today
    assert A.assign_new(s, lead(3)) == ""                                        # Cy and Di are at cap 1, Ann/Bob unavailable
    assert A.next_member(s, W1, "a", exclude=("Di",)) == ""
    assert A.next_member(s, W1, "missing") == ""


def test_never_reowns_and_uploader_member_owns(tmp_path):
    s = mk_store(tmp_path)
    A.save_config(s, W1, cfg([team("a", ["Ann"], True)]))
    put(s, [lead(1, owner="Zed"), lead(2), lead(3)])
    assert A.assign_new(s, {**lead(1), "owner": "Zed"}) == "Zed"
    assert A.assign_new(s, lead(1)) == "" or owner_of(s, "l1") == "Zed"           # stale copy without owner: store keeps Zed
    assert owner_of(s, "l1") == "Zed"
    assert A.assign_new(s, lead(2), uploader={"role": "caller", "name": "Cy", "email": "cy@x.in"}) == "Cy"
    assert next(x for x in s.read("leads") if x["id"] == "l2")["owner_why"]["kind"] == "upload"
    assert A.assign_new(s, lead(3), uploader={"role": "admin", "name": "Boss"}) == "Ann"   # admin upload goes by the rules


def test_workspace_isolation(tmp_path):
    s = mk_store(tmp_path)
    A.save_config(s, W1, cfg([team("a", ["Ann"], True)]))
    put(s, [lead(1, ws=W2)])
    assert A.assign_new(s, lead(1, ws=W2)) == "" and owner_of(s, "l1") == ""   # w2 never switched on
    A.save_config(s, W2, cfg([team("z", ["Bob"], True)]))
    assert A.assign_new(s, lead(1, ws=W2)) == "Bob"
    assert A.config(s, W1)["teams"][0]["id"] == "a"
    put(s, [lead(2)])
    assert A.run_unowned(s, W2) == {"assigned": 0, "left": 0} and owner_of(s, "l2") == ""
    assert A.team_of_owner(s, W2, "Ann") == "" and A.team_of_owner(s, W1, "ann") == "a"


def test_validation(tmp_path):
    s = mk_store(tmp_path)
    for bad in (cfg([team("a", ["Nobody"])]), cfg([team("a", ["Ann"], True), team("b", ["Bob"], True)]),
                cfg([team("a", ["Ann"])], [{"id": "r", "when": {}, "team": "a"}]),
                cfg([team("a", ["Ann"])], [{"id": "r", "when": {"city": "x"}, "team": "ghost"}]),
                cfg([team("a", ["Ann"])], first_touch={"alert_minutes": 90, "reassign_minutes": 30}),
                cfg([team("a", ["Ann"], cap=-1)])):
        with pytest.raises(A.AssignmentRefused):
            A.save_config(s, W1, bad)
    out = A.save_config(s, W1, cfg([team("a", ["ann"])]))
    assert out["teams"][0]["members"] == ["Ann"] and out["first_touch"] == A.FIRST_TOUCH


def test_manager_and_run_unowned(tmp_path):
    s = mk_store(tmp_path)
    s.update("settings", lambda st: {**st, "users": [*st["users"], {"email": "m@x.in", "name": "Mia", "role": "admin", "workspaces": [W2]},
                                                     {"email": "n@x.in", "name": "Nia", "role": "admin", "workspaces": [W1]}]})
    assert A.manager_of(s, W1, "Ann") == "Nia" and A.manager_of(s, W2, "Ann") == "Mia"
    A.save_config(s, W1, cfg([team("a", ["Ann", "Bob"], True)]))
    put(s, [lead(i) for i in range(4)])
    assert A.run_unowned(s, W1) == {"assigned": 4, "left": 0}


# ---------------- API
@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = make_rig(tmp_path, monkeypatch)
    return r


def test_api_permissions_and_scope(rig):
    c, ws = rig["c"], rig["ws"]
    hws = {"X-Workspace": ws}
    assert c.get("/api/assignment", headers={**rig["hc"], **hws}).status_code == 403
    assert c.put("/api/assignment", json={}, headers={**rig["hc"], **hws}).status_code == 403
    assert c.get("/api/assignment/stats", headers={**rig["hc"], **hws}).status_code == 403
    assert c.post("/api/assignment/run-unowned", headers={**rig["hc"], **hws}).status_code == 403
    got = c.get("/api/assignment", headers={**rig["hb"], **hws})
    assert got.status_code == 200 and got.json()["on"] is False
    body = cfg([team("t1", ["Bravo Caller"], True)], [{"id": "r1", "when": {"source": "meta"}, "team": "t1"}])
    saved = c.put("/api/assignment", json=body, headers={**rig["hb"], **hws})
    assert saved.status_code == 200 and saved.json()["teams"][0]["members"] == ["Bravo Caller"]
    assert c.put("/api/assignment", json=cfg([team("a", ["Nobody"])]), headers={**rig["hb"], **hws}).status_code == 400
    # the admin of ws never reaches main's settings; the owner's main is untouched
    assert c.get("/api/assignment", headers={**rig["ho"], "X-Workspace": "main"}).json()["teams"] == []
    assert c.get("/api/assignment", headers={**rig["hb"], "X-Workspace": "main"}).json()["teams"][0]["id"] == "t1"   # scope coerced to own ws
    assert c.get("/api/assignment/stats", headers={**rig["hb"], **hws}).json()["unassigned"] == 0
    assert c.post("/api/assignment/run-unowned", headers={**rig["hb"], **hws}).json() == {"assigned": 0, "left": 0}


def test_entry_points_assign_only_when_on(rig):
    c, ws, store = rig["c"], rig["ws"], rig["store"]
    hws = {**rig["hb"], "X-Workspace": ws}
    csv = "company,contact_name,email\nAlpha Co,Ann,ann@alpha.example\n"
    assert c.post("/api/leads/import", json={"csv": csv}, headers=hws).status_code == 200
    assert all(not l.get("owner") for l in store.read("leads"))
    c.put("/api/assignment", json=cfg([team("t1", ["Bravo Caller"], True)]), headers=hws)
    assert c.post("/api/assignment/run-unowned", headers=hws).json() == {"assigned": 1, "left": 0}
    assert store.read("leads")[0]["owner"] == "Bravo Caller"


def test_inbound_capture_assigns_only_when_on(tmp_path):
    from tests.capture_helpers import add_connector, lead_by_email, make_client, post_lead
    client, store = make_client(tmp_path)
    conn = add_connector(client, "generic", "Form")
    store.update("settings", lambda st: {**st, "users": [{"email": "ann@x.in", "name": "Ann", "role": "caller"}]})
    assert post_lead(client, conn, {"full_name": "Raj K", "email": "raj@acme.example", "organization": "Acme Ltd"}).status_code == 200
    assert lead_by_email(store, "raj@acme.example").get("owner") in (None, "")                  # off: no change
    A.save_config(store, "main", cfg([team("a", ["Ann"], True)]))
    assert post_lead(client, conn, {"full_name": "Sam T", "email": "sam@beta.example", "organization": "Beta Ltd"}).status_code == 200
    got = lead_by_email(store, "sam@beta.example")
    assert got["owner"] == "Ann" and got["owner_why"]["rule_id"] == "default"
    assert post_lead(client, conn, {"full_name": "Sam T", "email": "sam@beta.example", "organization": "Beta Ltd"}).status_code == 200   # merge: not re-owned
    assert lead_by_email(store, "sam@beta.example")["owner"] == "Ann"


def test_first_touch_clock_started_when_module_exists(tmp_path, monkeypatch):
    import sys
    import types
    calls = []
    mod = types.ModuleType("app.first_touch")
    mod.start = lambda store, lead_id: calls.append(lead_id)
    monkeypatch.setitem(sys.modules, "app.first_touch", mod)
    monkeypatch.setattr("app.first_touch", mod, raising=False)
    s = mk_store(tmp_path)
    A.save_config(s, W1, cfg([team("a", ["Ann"], True)]))
    put(s, [lead(1)])
    assert A.assign_new(s, lead(1)) == "Ann" and calls == ["l1"]
