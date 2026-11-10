"""The access module: one owner per lead, admins of the lead's workspace see all, other workspaces never, switch off = old behaviour."""
import pytest

from app import lead_access as la
from app.store import Store


def lead(owner="", ws="main"): return {"id": "l1", "owner": owner, "workspace": ws}
def user(role="caller", name="Priya", email="p@x.in", ws=None): return {"role": role, "name": name, "email": email, "workspaces": ws}


def make_store(tmp_path, on):
    s = Store(tmp_path / "d.json")
    s.update("settings", lambda st: {**st, "ownership_mode": {w: True for w in on}})
    return s


@pytest.fixture
def store_with_mode_on(tmp_path): return make_store(tmp_path, ["main", "w2"])
@pytest.fixture
def store_with_mode_off(tmp_path): return make_store(tmp_path, [])


def test_owner_name_or_email_matches():
    u = user()
    assert la.owns(u, lead("priya")) and la.owns(u, lead("P@X.IN")) and not la.owns(u, lead("Ravi")) and not la.owns(u, lead(""))


def test_admin_sees_all_in_workspace_not_others(store_with_mode_on):
    s = store_with_mode_on
    adm = user("admin", "Adm", "a@x.in", ["main"])
    assert la.can_send(s, adm, lead("Ravi", "main")) and not la.can_send(s, adm, lead("Ravi", "w2"))
    assert la.can_see(s, adm, lead("Ravi", "main")) and not la.can_see(s, adm, lead("Ravi", "w2"))


def test_member_only_own_when_on(store_with_mode_on):
    s = store_with_mode_on
    assert la.can_send(s, user(), lead("Priya")) and not la.can_send(s, user(), lead("Ravi")) and not la.can_send(s, user(), lead(""))
    assert la.can_see(s, user(), lead("Priya")) and not la.can_see(s, user(), lead("Ravi"))
    assert "Ravi" in la.refusal(s, user(), lead("Ravi")) and la.refusal(s, user(), lead("Priya")) == ""
    assert [l["owner"] for l in la.visible_leads(s, user(), [lead("Priya"), lead("Ravi")])] == ["Priya"]


def test_mode_off_changes_nothing(store_with_mode_off):
    s = store_with_mode_off
    assert la.can_send(s, user(), lead("Ravi")) and la.can_see(s, user(), lead("")) and la.refusal(s, user(), lead("Ravi")) == ""
    assert len(la.visible_leads(s, user(), [lead("Priya"), lead("Ravi"), lead("")])) == 3


def test_mode_is_per_workspace(tmp_path):
    s = make_store(tmp_path, ["w2"])
    assert la.can_send(s, user(), lead("Ravi", "main")) and not la.can_send(s, user(), lead("Ravi", "w2"))


def test_workspace_limit_always_applies_even_when_off(store_with_mode_off):
    limited = user("caller", ws=["main"])
    assert not la.can_send(store_with_mode_off, limited, lead("Priya", "w2"))


def test_paired_tool_keeps_real_role(store_with_mode_on):
    tool = {"role": "caller", "base_role": "owner", "name": "Boss", "email": "b@x.in", "workspaces": []}
    assert la.can_send(store_with_mode_on, tool, lead("Ravi"))
    assert not la.can_send(store_with_mode_on, {**tool, "base_role": "caller"}, lead("Ravi"))


def test_system_ok_needs_an_owner_when_on(store_with_mode_on, store_with_mode_off):
    assert la.system_ok(store_with_mode_on, lead("Ravi")) and not la.system_ok(store_with_mode_on, lead(""))
    assert la.system_ok(store_with_mode_off, lead(""))
