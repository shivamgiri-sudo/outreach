"""Migration to lead ownership: lists get their creator as assignee when that is an active login of the workspace, a
report lists the mismatches and the unowned, "fix owners" is explicit, the switch refuses while leads are unowned."""
import pytest

from app import lead_access, ownership_migration as mig
from tests.ownership_helpers import lead, rig as make_rig


@pytest.fixture
def r(tmp_path):
    return make_rig(tmp_path, mode_on=False)


def legacy_list(r, name, companies, created_by, ws="main"):
    """A list as saved before this feature: no assignee fields at all."""
    row = {"id": name.lower().replace(" ", "")[:10], "name": name, "lead_ids": [r.lead[c]["id"] for c in companies], "created_by": created_by,
           "created_at": "2026-10-01T00:00:00+00:00", "log": [], **({"workspace": ws} if ws != "main" else {})}
    r.store.update("lists", lambda rows: [*rows, row])
    return row


def by_name(r): return {x["name"]: x for x in r.store.read("lists")}


def test_migrate_assigns_creator_if_active_login_in_workspace(r):
    legacy_list(r, "Priya list", ["Beta Ltd"], "Priya")
    legacy_list(r, "Gone list", ["Acme"], "Someone Left")
    legacy_list(r, "Admin list", ["Cora Inc"], "Admin")                                     # the server admin password is not a login
    legacy_list(r, "W2 list", ["Beta Ltd"], "Adm", ws=r.w2)                                 # Adm works in Main only
    out = mig.migrate_lists(r.store, "main")
    assert out == {"migrated": 3, "assigned": 1, "pool": 2}
    lists = by_name(r)
    assert lists["Priya list"]["assigned_to"] == "Priya" and lists["Gone list"]["assigned_to"] == "" and lists["Admin list"]["assigned_to"] == ""
    assert "assigned_to" not in lists["W2 list"]                                            # another workspace is not touched
    assert lists["Priya list"]["log"][-1]["by"] == "migration"
    assert mig.migrate_lists(r.store, "main")["migrated"] == 0                              # repeating changes nothing


def test_migration_never_changes_a_lead(r):
    legacy_list(r, "Priya list", ["Acme", "Beta Ltd"], "Priya")
    before = r.store.read("leads")
    mig.migrate_lists(r.store, "main")
    assert r.store.read("leads") == before


def test_report_counts_mismatches_and_unowned(r):
    legacy_list(r, "Priya list", ["Acme", "Beta Ltd", "Cora Inc"], "Priya")
    legacy_list(r, "Pool list", ["Cora Inc"], "Admin")
    rep = r.c.get("/api/ownership/report", headers=r.h["adm"]).json()
    assert rep["lists_assigned"] == 1 and rep["lists_pool"] == 1 and rep["legacy_lists"] == 2 and rep["unowned"] == 1
    assert [(m["lead_id"], m["owner"], m["list_assignee"]) for m in rep["mismatches"]] == [(r.lead["Acme"]["id"], "Ravi", "Priya")]
    assert "assigned_to" not in by_name(r)["Priya list"]                                    # the report is read-only


def test_report_is_admin_only_and_workspace_strict(r):
    assert r.c.get("/api/ownership/report", headers=r.h["priya"]).status_code == 403
    assert r.c.get("/api/ownership/report", headers=r.h["view"]).status_code == 403
    assert r.c.get("/api/ownership/report", params={"workspace": r.w2}, headers=r.h["adm"]).status_code == 404
    rep = r.c.get("/api/ownership/report", params={"workspace": r.w2}, headers=r.h["adm2"]).json()
    assert rep["leads"] == 1 and rep["workspace"] == r.w2


def test_fix_owners_needs_confirm_and_changes_only_listed(r):
    legacy_list(r, "Priya list", ["Acme", "Cora Inc"], "Priya")
    mig.migrate_lists(r.store, "main")
    assert r.c.post("/api/ownership/fix-owners", json={}, headers=r.h["adm"]).status_code == 400
    assert lead(r, "Acme")["owner"] == "Ravi"
    out = r.c.post("/api/ownership/fix-owners", json={"confirm": True, "lead_ids": [r.lead["Beta Ltd"]["id"]]}, headers=r.h["adm"]).json()
    assert out["fixed"] == 0 and lead(r, "Acme")["owner"] == "Ravi"                          # Beta is not a mismatch: nothing happens
    out = r.c.post("/api/ownership/fix-owners", json={"confirm": True}, headers=r.h["adm"]).json()
    assert out["fixed"] == 1 and lead(r, "Acme")["owner"] == "Priya" and out["report"]["mismatches"] == []
    assert "fix owners" in lead(r, "Acme")["timeline"][-1]["text"]
    assert r.c.post("/api/ownership/fix-owners", json={"confirm": True}, headers=r.h["priya"]).status_code == 403


def test_fix_owners_skips_a_lead_two_lists_claim_differently(r):
    r.set("Acme", owner="Adm")
    legacy_list(r, "Priya list", ["Acme"], "Priya")
    legacy_list(r, "Ravi list2", ["Beta Ltd", "Acme"], "Ravi")
    mig.migrate_lists(r.store, "main")
    out = mig.fix_owners(r.store, "main", "Adm")
    assert out["ambiguous"][0]["lead_id"] == r.lead["Acme"]["id"] and lead(r, "Acme")["owner"] == "Adm"
    assert lead(r, "Beta Ltd")["owner"] == "Ravi"                                            # Priya's lead in Ravi's list: the only claim


def test_switch_refused_while_unowned_unless_acknowledged(r):
    legacy_list(r, "Priya list", ["Beta Ltd"], "Priya")
    res = r.c.put("/api/ownership-mode", json={"on": True}, headers=r.h["adm"])
    assert res.status_code == 400 and "1 leads have no owner" in res.json()["detail"]
    assert not lead_access.ownership_on(r.store, "main")
    res = r.c.put("/api/ownership-mode", json={"on": True, "acknowledge": True}, headers=r.h["adm"])
    assert res.status_code == 200 and res.json()["migrated"]["migrated"] == 1 and lead_access.ownership_on(r.store, "main")
    assert not lead_access.ownership_on(r.store, r.w2)                                       # per workspace
    assert by_name(r)["Priya list"]["assigned_to"] == "Priya"                                # nobody lost their list on switch-on
    assert [x["name"] for x in r.c.get("/api/lists", headers=r.h["priya"]).json()] == ["Priya list"]
    assert r.c.get("/api/ownership-mode", headers=r.h["adm"]).json()["on"] is True
    assert r.c.put("/api/ownership-mode", json={"on": False}, headers=r.h["adm"]).json()["on"] is False
    assert not lead_access.ownership_on(r.store, "main")


def test_switch_on_with_everything_owned_needs_no_acknowledgement(r):
    r.set("Cora Inc", owner="Priya")
    assert r.c.put("/api/ownership-mode", json={"on": True}, headers=r.h["adm"]).status_code == 200


def test_switch_cannot_be_set_by_members_or_for_another_workspace(r):
    assert r.c.put("/api/ownership-mode", json={"on": True, "acknowledge": True}, headers=r.h["priya"]).status_code == 403
    assert r.c.put("/api/ownership-mode", json={"on": True, "acknowledge": True, "workspace": r.w2}, headers=r.h["adm"]).status_code == 404
    assert r.c.put("/api/ownership-mode", json={"on": True, "acknowledge": True, "workspace": "nope"}, headers=r.h["owner"]).status_code == 404
    assert not lead_access.any_on(r.store)


def test_before_the_switch_nothing_is_hidden_from_members(r):
    legacy_list(r, "Ravi list", ["Acme"], "Ravi")
    assert [x["name"] for x in r.c.get("/api/lists", headers=r.h["priya"]).json()] == ["Ravi list"]      # legacy list, switch off
    assert len(r.c.get("/api/leads", headers=r.h["priya"]).json()) == 4
