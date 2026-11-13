"""With lead ownership on, every lead route and every list / count / summary hides other people's leads from a member;
a viewer sees only their team's; workspace-limited admins can start lists inside their own workspace only."""
import json

import pytest

from app import assignment, linkedin_seq as li
from tests.ownership_helpers import lead, new_list, rig as make_rig
from tests.test_ownership_enforcement import pair


@pytest.fixture
def r(tmp_path): return make_rig(tmp_path)
@pytest.fixture
def off(tmp_path): return make_rig(tmp_path, mode_on=False)


def lid(r, company): return r.lead[company]["id"]


def seen(r, who, path):
    return r.c.get(path, headers=r.h[who]).text


# ---- every /api/leads/{lead_id}/... route, GET and write
def test_every_lead_route_refuses_foreign_lead(r):
    acme = lid(r, "Acme")                                                   # Ravi's
    paths = r.c.app.openapi()["paths"]
    checked = 0
    for p, d in paths.items():
        if "{lead_id}" not in p or "{" in p.replace("{lead_id}", ""):
            continue
        for m in ("get", "post", "put", "patch", "delete"):
            if m not in d:
                continue
            res = r.c.request(m.upper(), p.replace("{lead_id}", acme), json={} if m != "get" else None, headers=r.h["priya"])
            assert res.status_code in (403, 404, 409), (m, p, res.status_code, res.text[:80])
            checked += 1
    assert checked > 40
    assert lead(r, "Acme")["owner"] == "Ravi" and not lead(r, "Acme").get("do_not_contact")


def test_own_lead_routes_still_work(r):
    beta = lid(r, "Beta Ltd")
    assert r.c.get(f"/api/leads/{beta}", headers=r.h["priya"]).status_code == 200
    assert r.c.get(f"/api/leads/{beta}/thread", headers=r.h["priya"]).status_code == 200
    assert r.c.post(f"/api/leads/{beta}/tags", json={"add": ["x"]}, headers=r.h["priya"]).status_code == 200
    assert r.c.get(f"/api/leads/{lid(r, 'Acme')}", headers=r.h["adm"]).status_code == 200


def test_foreign_lead_routes_mode_off(off):
    assert off.c.get(f"/api/leads/{lid(off, 'Acme')}/thread", headers=off.h["priya"]).status_code == 200


# ---- lists, counts and summaries
def test_no_aggregate_or_list_endpoint_leaks(r):
    acme = lid(r, "Acme")
    for l in (acme, lid(r, "Beta Ltd")):
        r.c.post(f"/api/leads/{l}/enroll", headers=r.h["owner"])
        li.start(r.store, l)
    r.c.post(f"/api/leads/{acme}/meetings", json={"title": "ACMEMEET", "at": "2026-12-01T10:00:00+00:00"}, headers=r.h["owner"])
    r.c.post("/api/tasks", json={"lead_id": acme, "title": "ACMETASK"}, headers=r.h["owner"])
    new_list(r, "Ravi private list", ["Acme"], assigned_to="Ravi")
    needles = ["acme.com", "Ravi Kumar", "98765 43210", "ravi@acme", "Ravi private list", "ACMEMEET", "ACMETASK", "\"Acme\""]
    gets = sorted(p for p, d in r.c.app.openapi()["paths"].items() if "get" in d and "{" not in p and p.startswith("/api")
                  and p != "/api/icp" and not p.startswith("/api/prospects"))   # /api/icp: a sample company called Acme; the prospect pool keeps name/title/company but redacts every contact (test_prospect_ownership)
    for who in ("priya", "view"):
        for p in gets:
            res = r.c.get(p, headers=r.h[who])
            if res.status_code == 200:
                assert not [n for n in needles if n in res.text], (who, p)
    assert json.loads(seen(r, "priya", "/api/dashboard"))["total_leads"] == 2               # Beta Ltd + her lead in the second workspace
    assert "Acme" in seen(r, "adm", "/api/companies")                                         # an admin still sees the workspace


def test_members_cannot_export(r):
    for ds in ("leads", "outbox", "inbox", "tasks", "activity", "meetings", "lists", "campaign_leads"):
        assert r.c.get(f"/api/export/{ds}.csv", headers=r.h["priya"]).status_code == 403
        assert r.c.get(f"/api/export/{ds}.json", headers=r.h["view"]).status_code == 403
    assert "Cora" in r.c.get("/api/export/leads.csv", headers=r.h["owner"]).text


def test_bulk_actions_skip_foreign_leads(r):
    acme = lid(r, "Acme")
    for act, val in (("tag_add", "x"), ("do_not_contact", None), ("owner", "Priya"), ("stage", "verified"), ("delete", None)):
        out = r.c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": act, "value": val}, headers=r.h["priya"])
        assert out.status_code == 403 or out.json()["ok"] == 0, act
    assert lead(r, "Acme")["owner"] == "Ravi" and not lead(r, "Acme").get("do_not_contact")


def test_task_and_list_creation_cannot_pull_foreign_leads(r):
    acme = lid(r, "Acme")
    assert r.c.post("/api/tasks", json={"lead_id": acme, "title": "t"}, headers=r.h["priya"]).status_code == 404
    assert r.c.post("/api/lists", json={"name": "grab", "lead_ids": [acme]}, headers=r.h["priya"]).status_code == 400


def test_save_profile_cannot_fill_foreign_lead(r):
    tool = pair(r, "priya")
    res = r.c.post("/api/extension/save-profile", json={"url": "https://www.linkedin.com/in/acme", "name": "Ravi Kumar", "title": "Boss",
                                                        "company": "Acme"}, headers=tool)
    assert res.status_code in (404, 409, 422) and lead(r, "Acme")["title"] == "HR Head"


def test_mode_off_nothing_hidden(off):
    assert "Acme" in seen(off, "priya", "/api/companies") and off.c.get(f"/api/leads/{lid(off, 'Acme')}", headers=off.h["priya"]).status_code == 200
    assert len(off.c.get("/api/leads", headers=off.h["view"]).json()) == 4


# ---- viewers: read-only, see their team's leads (none without a team)
def team(r, members):
    r.store.update("settings", lambda s: {**s, "assignment": {"main": {"on": False, "teams": [{"id": "t1", "name": "T1", "members": members}]}}})


def test_viewer_without_team_sees_nothing(r):
    assert r.c.get("/api/leads", headers=r.h["view"]).json() == []
    assert r.c.get(f"/api/leads/{lid(r, 'Beta Ltd')}", headers=r.h["view"]).status_code == 404
    assert r.c.get("/api/lists", headers=r.h["view"]).json() == []


def test_viewer_sees_only_their_teams_leads(r):
    team(r, ["Priya", "Viewer@x.in" if False else "view@x.in"])
    new_list(r, "Priya list", ["Beta Ltd"], assigned_to="Priya")
    new_list(r, "Ravi list", ["Acme"], assigned_to="Ravi")
    assert {l["company"] for l in r.c.get("/api/leads", headers=r.h["view"]).json()} == {"Beta Ltd"}
    assert r.c.get(f"/api/leads/{lid(r, 'Beta Ltd')}", headers=r.h["view"]).status_code == 200
    assert r.c.get(f"/api/leads/{lid(r, 'Acme')}", headers=r.h["view"]).status_code == 404
    assert {x["name"] for x in r.c.get("/api/lists", headers=r.h["view"]).json()} == {"Priya list"}
    assert r.c.post(f"/api/leads/{lid(r, 'Beta Ltd')}/tags", json={"add": ["x"]}, headers=r.h["view"]).status_code == 403     # still read-only
    assert r.c.post(f"/api/leads/{lid(r, 'Beta Ltd')}/enroll", headers=r.h["view"]).status_code == 403


# ---- workspace-limited admins start lists inside their workspace only
def test_limited_admin_starts_lists_in_own_workspace(r):
    main_list = new_list(r, "Main list", ["Acme", "Beta Ltd"], assigned_to="")
    for ep in ("start-email", "start-linkedin"):
        out = r.c.post(f"/api/lists/{main_list['id']}/{ep}", headers=r.h["adm"])
        assert out.status_code == 200 and out.json()["started"] == 2, ep
    assert r.c.post(f"/api/lists/{main_list['id']}/start-email", headers=r.h["adm2"]).status_code == 404       # other workspace: strict


def test_limited_admin_cannot_reach_other_workspace_leads_through_a_list(r):
    sneaky = new_list(r, "Mixed", ["Acme"], assigned_to="")
    r.store.update("lists", lambda rows: [{**x, "lead_ids": [*x["lead_ids"], r.other["id"]]} if x["id"] == sneaky["id"] else x for x in rows])
    out = r.c.post(f"/api/lists/{sneaky['id']}/start-email", headers=r.h["adm"]).json()
    assert out["started"] == 1 and not lead(r, "Other WS Co").get("sequence")
    out = r.c.post(f"/api/lists/{sneaky['id']}/start-linkedin", headers=r.h["adm"]).json()
    assert not lead(r, "Other WS Co").get("linkedin_seq")
