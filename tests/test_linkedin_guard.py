"""No agent can loop on one person: two tries in 24 h without progress hold the person (claim 409, left out of the
queue), progress clears it; acceptance checks are offered once per 6 hours per profile."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import linkedin_agent as agent, linkedin_guard as guard, linkedin_seq as li
from app.main import create_app

CSV = "company,contact_name,title,email,linkedin_url\nHDFC Bank,Vibhash Naik,CHRO,v@hdfc.example,https://www.linkedin.com/in/vibhash\n"


def make(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV})
    store = c.app.state.store
    lid = store.read("leads")[0]["id"]
    li.start(store, lid)
    agent.save_config(store, {"enabled": True, "weekdays_only": False, "start_hour": 0, "end_hour": 24, "gap_minutes": 3})      # claim re-checks the switch and hours
    tok = c.post("/api/extension/pair", json={"label": "agent"}).json()["token"]
    return c, store, lid, {"Authorization": f"Bearer {tok}", "X-LinkedIn-Account": "acct-a"}


def test_two_failed_tries_hold_the_person_and_progress_clears_it(tmp_path):
    c, store, lid, h = make(tmp_path)
    assert c.post(f"/api/extension/leads/{lid}/claim", headers=h).status_code == 200
    assert c.post(f"/api/extension/leads/{lid}/claim", headers=h).status_code == 200
    r = c.post(f"/api/extension/leads/{lid}/claim", headers=h)
    assert r.status_code == 409 and "held until" in r.text
    assert c.get("/api/extension/queue", headers=h).json()["due"] == []
    held_lines = lambda: sum(1 for r in agent.log_rows(store) if "held until" in r.get("note", ""))
    assert held_lines() == 1
    c.post(f"/api/extension/leads/{lid}/claim", headers=h)                     # repeated refusals log only once
    assert held_lines() == 1
    guard.clear(store, lid)
    assert c.post(f"/api/extension/leads/{lid}/claim", headers=h).status_code == 200


def test_hold_ends_after_24_hours():
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    assert guard.held_until({guard.KEY: [old, old]}) is None
    now = datetime.now(timezone.utc).isoformat()
    assert guard.held_until({guard.KEY: [now, now]})


def test_acceptance_checks_offered_once_per_six_hours(tmp_path):
    c, store, lid, h = make(tmp_path)
    li.mark_sent(store, lid, 1)
    assert [w["lead_id"] for w in c.get("/api/extension/queue", headers=h).json()["waiting_for_acceptance"]] == [lid]
    assert c.get("/api/extension/queue", headers=h).json()["waiting_for_acceptance"] == []      # restarts do not reopen it
    earlier = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(timespec="seconds")
    store.update_lead(lid, lambda l: {**l, guard.CHECKED: earlier})
    assert len(c.get("/api/extension/queue", headers=h).json()["waiting_for_acceptance"]) == 1
