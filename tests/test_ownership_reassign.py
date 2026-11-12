"""Reassign / first-touch edge cases with ownership on: a running sequence continues under the new owner, the old owner loses
access at once, the three people are told, and a stuck lead reaches the manager."""
from datetime import datetime, timedelta, timezone

import pytest

from app import first_touch as ft, linkedin_seq as li
from tests.ownership_helpers import lead, new_list, rig as make_rig
from tests.test_ownership_enforcement import pair


@pytest.fixture
def r(tmp_path): return make_rig(tmp_path)


def lid(r, company): return r.lead[company]["id"]


def bells(r, to): return [n for n in r.store.read("notifications") if n.get("to") == to and n.get("kind") == "reassigned"]


def test_list_reassign_keeps_sequences_running_under_new_owner(r):
    acme = lid(r, "Acme")                                               # Ravi's
    row = new_list(r, "Ravi batch", ["Acme"], assigned_to="Ravi")
    assert r.c.post(f"/api/leads/{acme}/enroll", headers=r.h["ravi"]).status_code == 200
    li.start(r.store, acme)
    seq_before, li_before = lead(r, "Acme")["sequence"], lead(r, "Acme")["linkedin_seq"]
    out = r.c.put(f"/api/lists/{row['id']}/assign", json={"to": "Priya", "take_over": True}, headers=r.h["adm"]).json()
    assert out["moved"] == 1 and lead(r, "Acme")["owner"] == "Priya"
    assert lead(r, "Acme")["sequence"] == seq_before and lead(r, "Acme")["linkedin_seq"] == li_before      # nothing restarted, nothing re-sent
    # the old owner is out immediately, on every path
    assert r.c.get(f"/api/leads/{acme}", headers=r.h["ravi"]).status_code == 404
    assert r.c.post(f"/api/leads/{acme}/linkedin/sent", json={"step": 1}, headers=r.h["ravi"]).status_code == 409
    assert r.c.post(f"/api/leads/{acme}/enroll", headers=r.h["ravi"]).status_code == 409
    tool_old = pair(r, "ravi")
    assert r.c.post(f"/api/extension/leads/{acme}/claim", headers=tool_old).status_code == 409
    assert {d["company"] for d in r.c.get("/api/linkedin/queue", headers=r.h["ravi"]).json()["due"]} == set()
    # the new owner carries on with the same LinkedIn sequence
    tool_new = pair(r, "priya")
    assert r.c.post(f"/api/extension/leads/{acme}/claim", headers=tool_new).status_code == 200
    assert r.c.post(f"/api/leads/{acme}/linkedin/sent", json={"step": 1}, headers=r.h["priya"]).status_code == 200
    assert {d["company"] for d in r.c.get("/api/linkedin/queue", headers=r.h["priya"]).json()["due"]} <= {"Acme", "Beta Ltd"}


def test_reassign_tells_old_new_and_manager(r):
    row = new_list(r, "Ravi batch", ["Acme"], assigned_to="Ravi")
    r.c.put(f"/api/lists/{row['id']}/assign", json={"to": "Priya", "take_over": True}, headers=r.h["adm"])
    assert bells(r, "Ravi") and bells(r, "Priya") and bells(r, "Adm")
    assert "Ravi" in bells(r, "Priya")[0]["text"] and "Priya" in bells(r, "Ravi")[0]["text"]
    r.c.post(f"/api/leads/{lid(r, 'Beta Ltd')}/owner", json={"owner": "Ravi"}, headers=r.h["adm"])        # a single-lead change tells them too
    assert len(bells(r, "Ravi")) == 2 and any("Beta Ltd" in n["text"] for n in bells(r, "Priya"))


def test_new_lead_assignment_sends_no_reassign_bell(r):
    from app import lead_ops
    lead_ops.set_owner(r.store, lid(r, "Cora Inc"), "Priya", "Adm", "manual")                       # it had no owner: nothing was taken from anyone
    assert not bells(r, "Priya")


def test_stuck_lead_reaches_the_manager_and_the_dashboard(r):
    long_ago = datetime.now(timezone.utc) - timedelta(days=3)
    r.store.update("settings", lambda s: {**s, "assignment": {"main": {"on": True, "teams": [{"id": "t", "name": "T", "members": ["Priya"], "default": True}],
                                                                 "first_touch": {"alert_minutes": 1, "reassign_minutes": 2, "alert": True, "reassign": True}}}})
    acme = lid(r, "Beta Ltd")                                                                          # Priya's, the only team member
    r.set("Beta Ltd", first_touch_started=long_ago.isoformat(timespec="seconds"), first_touch_due_at=long_ago.isoformat(timespec="seconds"), reassigns=0)
    ft.tick(r.store, datetime.now(timezone.utc))
    assert lead(r, "Beta Ltd")["first_touch_stuck"] is True                                            # nobody left to give it to
    assert [n for n in r.store.read("notifications") if n.get("kind") == "first_touch" and "stuck" in n["title"].lower() and n["to"] == "Adm"]
    stats = r.c.get("/api/assignment/stats", headers=r.h["adm"]).json()
    assert stats["stuck"] == 1
    assert r.c.get(f"/api/leads/{acme}", headers=r.h["adm"]).status_code == 200                       # the manager can open it
