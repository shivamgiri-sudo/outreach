"""Pipeline funnel: total -> enriched -> attempted -> connected -> interested / not interested / follow-up, call
outcomes, drill-down to the leads behind a number, and workspace scope."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import create_app

CSV = ("company,contact_name,email,phone,website\n"
       "Acme,Ravi Kumar,ravi@acme.com,98765 43210,https://acme.com\n"
       "Beta Ltd,Sita Rao,sita@beta.in,91234 56789,https://beta.in\n"
       "Gamma,Om Das,om@gamma.in,99887 76655,https://gamma.in\n"
       "Delta,Ana Roy,ana@delta.in,90000 11122,https://delta.in\n")


def make(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    return c


def ids(c):
    return {l["company"]: l["id"] for l in c.get("/api/leads").json()}


def test_funnel_counts_each_step_and_outcome(tmp_path):
    c = make(tmp_path)
    lid = ids(c)
    soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
    c.post(f"/api/leads/{lid['Acme']}/call-outcome", json={"outcome": "not_interested"})
    c.post(f"/api/leads/{lid['Beta Ltd']}/call-outcome", json={"outcome": "call_back", "callback_at": soon})
    c.post(f"/api/leads/{lid['Gamma']}/call-outcome", json={"outcome": "no_answer"})
    c.post(f"/api/leads/{lid['Gamma']}/call-outcome", json={"outcome": "inactive_number"})
    data = c.get("/api/analytics/pipeline").json()
    o = data["overall"]
    assert o["total"] == 4 and o["contactable"] == 4
    assert o["attempted"] == 3 and o["not_attempted"] == 1
    assert o["connected"] == 2 and o["no_connect"] == 1
    assert o["not_interested"] == 1 and o["follow_up"] == 1 and o["interested"] == 0
    assert o["bad_number"] == 1
    assert data["calls"]["total"] == 4 and data["calls"]["leads"] == 3
    assert data["calls"]["by_outcome"]["no_answer"] == 1
    assert data["channels"]["call"] == 3
    assert sum(d["attempted"] for d in data["trend"]) == 3
    assert data["by_source"][0]["name"] == "apollo" and data["by_source"][0]["total"] == 4


def test_drill_down_lists_the_leads_behind_a_number(tmp_path):
    c = make(tmp_path)
    lid = ids(c)
    c.post(f"/api/leads/{lid['Acme']}/call-outcome", json={"outcome": "not_interested"})
    r = c.get("/api/analytics/pipeline/leads", params={"step": "not_interested"}).json()
    assert r["count"] == 1 and r["leads"][0]["company"] == "Acme"
    assert c.get("/api/analytics/pipeline/leads", params={"step": "not_attempted"}).json()["count"] == 3
    assert c.get("/api/analytics/pipeline/leads", params={"step": "nope"}).status_code == 400


def test_added_in_period_and_bad_period(tmp_path):
    c = make(tmp_path)
    assert c.get("/api/analytics/pipeline", params={"days": 7, "added": "true"}).json()["overall"]["total"] == 4
    assert c.get("/api/analytics/pipeline", params={"from": "2020-01-01", "to": "2020-01-05", "added": "true"}).json()["overall"]["total"] == 0
    assert c.get("/api/analytics/pipeline", params={"days": 0}).status_code == 400


def test_funnel_only_counts_the_selected_workspace(tmp_path):
    c = make(tmp_path)
    w = c.post("/api/workspaces", json={"name": "Client One"}).json()
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nZeta,Z Z,z@zeta.in\n", "source": "upload"},
           headers={"X-Workspace": w["id"]})
    assert c.get("/api/analytics/pipeline", headers={"X-Workspace": w["id"]}).json()["overall"]["total"] == 1
    assert c.get("/api/analytics/pipeline", headers={"X-Workspace": "main"}).json()["overall"]["total"] == 4
