"""Visibility: today's numbers, verified flag, the hourly summary and the "agent not running" alert."""
from datetime import datetime, timedelta, timezone

from app import linkedin_agent as agent, linkedin_watch as watch
from tests.test_linkedin_agent import rig  # noqa: F401

NOW = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc)


def _bell(store):
    return [n for n in store.read("notifications") if n.get("kind") == "linkedin"]


def test_verified_is_kept_and_counted_today(rig):
    c, owner, store = rig
    agent.heartbeat(store, "idle", NOW)
    agent.record(store, {"kind": "connect", "note": "connection request to A", "verified": True}, NOW)
    agent.record(store, {"kind": "connect", "note": "connection request to B", "verified": False}, NOW)
    agent.record(store, {"kind": "message", "note": "message to C", "verified": True}, NOW)
    agent.record(store, {"kind": "error", "note": "connect D: failed"}, NOW)
    s = watch.summary(store, NOW + timedelta(minutes=1))
    assert (s["connect"], s["message"], s["confirmed"], s["unconfirmed"], s["problems"], s["online"]) == (2, 1, 2, 1, 1, True)
    assert agent.status(store, NOW)["today"]["connect"] == 2


def test_hourly_summary_once_and_only_when_something_happened(rig):
    c, owner, store = rig
    assert watch.tick(store, NOW)["summaries"] == 0 and not _bell(store)
    agent.record(store, {"kind": "connect", "note": "x", "verified": True}, NOW)
    agent.record(store, {"kind": "error", "note": "connect Z: no Connect button"}, NOW)
    assert watch.tick(store, NOW + timedelta(minutes=2))["summaries"] == 1
    text = _bell(store)[-1]["text"]
    assert "1 connection request sent" in text and "1 confirmed" in text and "no Connect button" in text
    assert watch.tick(store, NOW + timedelta(minutes=30))["summaries"] == 0           # not again within the hour


def test_alert_when_people_wait_and_the_agent_is_gone(rig):
    c, owner, store = rig
    lead = store.read("leads")[0]
    store.update_lead(lead["id"], lambda l: {**l, "linkedin_seq": {"status": "waiting_to_connect", "done": []}})
    assert watch.tick(store, NOW)["offline"] == 1 and "not running" in _bell(store)[-1]["title"]
    assert watch.tick(store, NOW + timedelta(hours=1))["offline"] == 0                # repeats only every 6 hours
    assert watch.tick(store, NOW + timedelta(hours=7))["offline"] == 1


def test_a_team_member_sees_only_their_own_linkedin_profile(rig):
    from app import linkedin_scope as scope
    c, owner, store = rig
    shivam = {"sub": "user:shivam", "name": "Shivam", "email": "s@x.in", "role": "owner"}
    rajesh = {"sub": "user:rajesh", "name": "Rajesh", "email": "r@x.in", "role": "admin"}
    mine = {"id": "a1", "created_sub": "user:rajesh", "created_by": "Rajesh"}
    theirs = {"id": "b1", "created_sub": "user:shivam", "created_by": "Shivam"}
    assert scope.sees_everyone(shivam) and scope.slot_is_mine(theirs, shivam) and scope.my_slugs(store, shivam) is None
    assert scope.slot_is_mine(mine, rajesh) and not scope.slot_is_mine(theirs, rajesh)
    assert not scope.sees_everyone(rajesh)                               # an admin inside the shared Mas Callnet account
