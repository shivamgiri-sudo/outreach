"""Ownership on the LinkedIn routes that were open: web accepted/replied/stop/branch and the agent's accepted."""
import pytest

from app import linkedin_seq as li
from tests.ownership_helpers import lead, rig as make_rig
from tests.test_ownership_enforcement import pair


@pytest.fixture
def r(tmp_path): return make_rig(tmp_path)
@pytest.fixture
def off(tmp_path): return make_rig(tmp_path, mode_on=False)


def lid(r, company): return r.lead[company]["id"]


def test_web_linkedin_acted_routes_refuse_foreign_lead(r):
    acme, beta = lid(r, "Acme"), lid(r, "Beta Ltd")               # Acme is Ravi's
    li.start(r.store, acme)
    li.start(r.store, beta)
    for route, body in (("accepted", None), ("replied", {"text": "hi"}), ("stop", None), ("branch", {"key": "case_study_yes"})):
        res = r.c.post(f"/api/leads/{acme}/linkedin/{route}", json=body, headers=r.h["priya"])
        assert res.status_code == 409, route
    assert lead(r, "Acme")["linkedin_seq"]["status"] != "stopped"
    assert r.c.post(f"/api/leads/{beta}/linkedin/stop", headers=r.h["priya"]).status_code == 200
    assert r.c.post(f"/api/leads/{acme}/linkedin/stop", headers=r.h["adm"]).status_code == 200      # admin of the workspace
    assert r.c.post(f"/api/leads/{r.other['id']}/linkedin/stop", headers=r.h["adm"]).status_code in (404, 409)


def test_web_linkedin_acted_routes_mode_off(off):
    acme = lid(off, "Acme")
    li.start(off.store, acme)
    assert off.c.post(f"/api/leads/{acme}/linkedin/stop", headers=off.h["priya"]).status_code == 200


def test_viewer_cannot_act(r):
    acme = lid(r, "Acme")
    li.start(r.store, acme)
    assert r.c.post(f"/api/leads/{acme}/linkedin/stop", headers=r.h["view"]).status_code in (403, 409)


def test_extension_accepted_refuses_foreign_lead(r):
    acme, beta = lid(r, "Acme"), lid(r, "Beta Ltd")
    for l in (acme, beta):
        li.start(r.store, l)
        r.store.update_lead(l, lambda x: {**x, "linkedin_seq": {**x["linkedin_seq"], "status": "waiting_for_acceptance"}})
    tool = pair(r, "priya")
    assert r.c.post(f"/api/extension/leads/{acme}/accepted", headers=tool).status_code == 409
    assert lead(r, "Acme")["linkedin_seq"]["status"] == "waiting_for_acceptance"
    assert r.c.post(f"/api/extension/leads/{beta}/accepted", headers=tool).status_code == 200
    adm = pair(r, "adm")                                              # an admin's tool acts for the workspace
    assert r.c.post(f"/api/extension/leads/{acme}/accepted", headers=adm).status_code == 200


def test_extension_agent_token_acts_as_its_account_owner(r):
    """The agent token carries the paired person's name/email: Ravi's tool works on Ravi's lead, not Priya's."""
    acme, beta = lid(r, "Acme"), lid(r, "Beta Ltd")
    for l in (acme, beta):
        li.start(r.store, l)
        r.store.update_lead(l, lambda x: {**x, "linkedin_seq": {**x["linkedin_seq"], "status": "waiting_for_acceptance"}})
    tool = pair(r, "ravi")
    assert r.c.post(f"/api/extension/leads/{beta}/replied", json={"text": "x"}, headers=tool).status_code == 409
    assert r.c.post(f"/api/extension/leads/{beta}/profile", json={}, headers=tool).status_code == 409
    assert r.c.post(f"/api/extension/leads/{acme}/profile", json={}, headers=tool).status_code == 200
