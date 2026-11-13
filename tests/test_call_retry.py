"""Automatic call retry rules: off by default, delay ladder, cap, explicit call-back wins, no duplicate tasks, closing on a
reached outcome, calling hours, queue timing, exhausted leads, owner-only settings and plain-words validation."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import call_retry as cr, rep_workflow as rw
from app.activities import now_utc
from app.main import create_app

CSV = ("company,contact_name,email,phone,website\n"
       "Acme,Ravi Kumar,ravi@acme.com,98765 43210,https://acme.com\n")
IST = timezone(timedelta(hours=5, minutes=30))


def make(tmp_path, on=True, **cfg):
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password=None))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    store = c.app.state.store
    if on:
        store.update("settings", lambda s: {**s, "call_retry": {**cr.defaults(), "enabled": True, **cfg}})
    lead = c.get("/api/leads").json()[0]
    store.update_lead(lead["id"], lambda l: {**l, "consent": {"text": "yes", "at": "2026-01-01T00:00:00+00:00", "channel": "call"}})
    return c, store, lead["id"]


def tasks(store, lid, retry_only=True):
    return [t for t in store.read("tasks") if t.get("lead_id") == lid and t.get("status", "open") == "open" and (not retry_only or cr.is_retry_task(t))]


def ist(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=IST).astimezone(timezone.utc)


def call(store, lid, outcome, now=None, **kw):
    return rw.record_call_outcome(store, lid, outcome, "", "Priya", now=now or now_utc(), **kw)


def test_off_by_default_changes_nothing(tmp_path):
    c, store, lid = make(tmp_path, on=False)
    assert c.get("/api/call-retry").json()["enabled"] is False
    for _ in range(3):
        r = call(store, lid, "no_answer")
        assert r["task"] is None
    assert tasks(store, lid, retry_only=False) == []
    assert "retry_exhausted" not in store.read("leads")[0]


def test_ladder_of_delays_and_title(tmp_path):
    c, store, lid = make(tmp_path, max_attempts=4)
    now = ist(2026, 11, 16, 10)                     # a Monday morning
    r1 = call(store, lid, "no_answer", now)
    assert r1["task"]["title"] == "Retry call (attempt 2 of 4)" and r1["task"]["source"] == "callback" and r1["task"]["kind"] == "call"
    assert rw.parse_at(r1["task"]["due_at"]) == now + timedelta(minutes=180)
    r2 = call(store, lid, "no_answer", now)         # the 2nd unanswered try uses delay 2 (1440 minutes), earlier retry task is closed
    assert rw.parse_at(r2["task"]["due_at"]) == ist(2026, 11, 17, 10)
    assert len(tasks(store, lid)) == 1
    r3 = call(store, lid, "no_answer", now)         # the last delay repeats
    assert rw.parse_at(r3["task"]["due_at"]) == ist(2026, 11, 17, 10) and r3["task"]["title"].endswith("(attempt 4 of 4)")


def test_busy_and_disconnected_use_their_own_rules(tmp_path):
    c, store, lid = make(tmp_path)
    now = ist(2026, 11, 16, 11)
    assert rw.parse_at(call(store, lid, "busy", now)["task"]["due_at"]) == now + timedelta(minutes=60)
    assert rw.parse_at(call(store, lid, "call_disconnected", now)["task"]["due_at"]) == now + timedelta(minutes=1440)


def test_cap_stops_retries_and_removes_lead_from_queue(tmp_path):
    c, store, lid = make(tmp_path, max_attempts=2)
    now = ist(2026, 11, 16, 10)
    assert call(store, lid, "no_answer", now)["task"] is not None
    last = call(store, lid, "no_answer", now)
    assert last["task"] is None and tasks(store, lid) == []
    lead = store.read("leads")[0]
    assert lead["retry_exhausted"]["attempts"] == 2
    assert any(e["text"] == "Retry limit reached after 2 attempts" for e in lead["timeline"])
    assert rw.call_queue(store, lambda l: {}, now + timedelta(days=2)) == []
    assert c.get("/api/call-queue").json()["count"] == 0


def test_explicit_callback_wins_for_disconnected(tmp_path):
    c, store, lid = make(tmp_path)
    at = now_utc() + timedelta(minutes=15)
    r = call(store, lid, "call_disconnected", callback_at=at)
    assert r["task"]["title"] == "Call back (call dropped)"
    assert tasks(store, lid) == []                  # no retry task on top of the explicit one


def test_no_duplicate_when_a_callback_task_is_open(tmp_path):
    from app.activities import add_task, log_activity
    c, store, lid = make(tmp_path)
    log_activity(store, lid, "call", {"outcome": "no_answer", "by": "Priya"})
    add_task(store, lid, "call", "asked for a call", due_at=now_utc() + timedelta(hours=3), owner="Priya", title="Call back", source="callback")
    result = {"task": None}
    cr.after_outcome(store, store.read("leads")[0], "no_answer", "Priya", now_utc(), False, result)
    assert result["task"] is None and len(tasks(store, lid, retry_only=False)) == 1
    # a retry task is replaced, never stacked, when the next try is also unanswered
    call(store, lid, "no_answer")
    call(store, lid, "no_answer")
    assert len([t for t in tasks(store, lid) if t["title"].startswith("Retry")]) <= 1


def test_reached_outcome_closes_retry_task(tmp_path):
    c, store, lid = make(tmp_path)
    call(store, lid, "no_answer")
    assert len(tasks(store, lid)) == 1
    call(store, lid, "connected")
    assert tasks(store, lid) == []
    done = [t for t in store.read("tasks") if cr.is_retry_task(t)]
    assert done[0]["status"] == "done" and "connected" in done[0]["done_note"]


def test_business_hours_shift():
    sat_evening = ist(2026, 11, 14, 20)             # Saturday 20:00 -> Monday 10:00
    assert cr.shift_into_hours(sat_evening) == ist(2026, 11, 16, 10)
    assert cr.shift_into_hours(ist(2026, 11, 15, 12)) == ist(2026, 11, 16, 10)      # Sunday
    assert cr.shift_into_hours(ist(2026, 11, 16, 7)) == ist(2026, 11, 16, 10)       # before opening, same day
    assert cr.shift_into_hours(ist(2026, 11, 14, 18, 30)) == ist(2026, 11, 14, 18, 30)   # Saturday is a calling day, 19:00 is not
    assert cr.shift_into_hours(ist(2026, 11, 16, 19)) == ist(2026, 11, 17, 10)
    assert cr.retry_due([180], 1, ist(2026, 11, 16, 17)) == ist(2026, 11, 17, 10)


def test_queue_hides_retry_until_due_then_shows_even_if_called_today(tmp_path):
    c, store, lid = make(tmp_path)
    now = ist(2026, 11, 16, 10)
    task = call(store, lid, "no_answer", now)["task"]
    present = lambda l: {}
    assert rw.call_queue(store, present, now + timedelta(hours=1)) == []                 # same day, not yet due
    later = rw.call_queue(store, present, now + timedelta(hours=3, minutes=1))
    assert len(later) == 1 and later[0]["reason"] == "Retry (attempt 2 of 6)" and later[0]["task_id"] == task["id"]
    nextday = rw.call_queue(store, present, ist(2026, 11, 17, 9))
    assert len(nextday) == 0 or nextday[0]["reason"].startswith("Retry")                 # never as a plain lead before its time
    store.update("activities", lambda rows: rows)                                         # called today by activity: task path still wins
    assert rw.call_queue(store, present, now + timedelta(hours=4))[0]["reason"].startswith("Retry")


def test_role_rules_and_validation(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password="s3cret-pass"))
    owner = {"Authorization": "Bearer " + c.post("/api/login", json={"password": "s3cret-pass"}).json()["token"]}
    c.post("/api/users", json={"email": "p@x.in", "name": "Priya", "role": "caller", "password": "long-password-1"}, headers=owner)
    priya = {"Authorization": "Bearer " + c.post("/api/login", json={"email": "p@x.in", "password": "long-password-1"}).json()["token"]}
    assert c.get("/api/call-retry", headers=priya).json()["enabled"] is False
    assert c.put("/api/call-retry", json={"enabled": True}, headers=priya).status_code == 403
    ok = c.put("/api/call-retry", json={"enabled": True, "max_attempts": 3, "rules": {"busy": [30, 60]}}, headers=owner)
    assert ok.status_code == 200 and ok.json()["enabled"] and ok.json()["max_attempts"] == 3 and ok.json()["rules"]["busy"] == [30, 60]
    assert ok.json()["rules"]["no_answer"] == [180, 1440]
    for body, words in [({"max_attempts": 0}, "from 1 to 20"), ({"max_attempts": 21}, "from 1 to 20"), ({"enabled": "yes"}, "true or false"),
                        ({"rules": {"busy": [4]}}, "from 5 to 10080"), ({"rules": {"busy": [10081]}}, "from 5 to 10080"),
                        ({"rules": {"busy": [10] * 7}}, "at most 6"), ({"rules": {"busy": []}}, "at least one"),
                        ({"rules": {"ringing": [10]}}, "not an outcome")]:
        r = c.put("/api/call-retry", json=body, headers=owner)
        assert r.status_code == 400 and words in r.json()["detail"], (body, r.text)
