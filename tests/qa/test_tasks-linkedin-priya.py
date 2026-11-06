"""QA: tasks, activity trail, reminders, the Priya call flow and team settings, simulated over 10 working days.

Team: Anil (owner), Priya (caller), Rohit (researcher, no email address). All data is fictional.
The clock is moved by a fake `datetime` in the modules that read "now" (no real external services are called).
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import activities, inbound, reminders, routes_today
from app.activities import next_call_slot
from app.inbound import apply_reply
from app.main import create_app
from app.reminders import run_reminders
from app.sequences import IST

FIXTURES = Path(__file__).parent / "fixtures" / "tasks-linkedin-priya"
TEAM = [{"name": "Anil", "email": "anil@mcn-outreach.invalid", "phone": "+91 90000 10001", "role": "owner"},
        {"name": "Priya", "email": "priya@mcn-outreach.invalid", "phone": "090000 10002", "role": "caller"},
        {"name": "Rohit", "email": "", "phone": "", "role": "researcher"}]


def ist(day: str, hm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:00+05:30").astimezone(timezone.utc)


class Clock:
    def __init__(self, start: datetime):
        self.now = start


@pytest.fixture
def clock(monkeypatch):
    ck = Clock(ist("2026-11-02", "09:00"))

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return ck.now.astimezone(tz) if tz else ck.now.replace(tzinfo=None)

    for module in (activities, inbound, reminders, routes_today):
        monkeypatch.setattr(module, "datetime", FakeDateTime)
    return ck


class Sender:
    def __init__(self, fail_for: str = ""):
        self.sent, self.fail_for = [], fail_for

    def __call__(self, message):
        self.sent.append(message)
        if self.fail_for and message["to"] == self.fail_for:
            return {"status": "failed", "error": "SMTP 421 try again later"}
        return {"status": "sent", "provider_id": f"t{len(self.sent)}"}


def make_app(tmp_path, sender=None):
    return TestClient(create_app(data_file=tmp_path / "d.json"))


def import_team_export(c) -> list:
    raw = (FIXTURES / "team_leads_export.csv").read_text(encoding="utf-8-sig")
    r = c.post("/api/import/commit", json={"csv": raw, "source": "team export"})
    assert r.status_code == 200, r.text
    return r.json()["created_ids"]


# ---------------------------------------------------------------- call slot ---------------------------------------

@pytest.mark.parametrize("when,expected", [
    (("2026-11-02", "10:00"), ("2026-11-02", "10:00")),     # Monday morning: right away
    (("2026-11-02", "18:29"), ("2026-11-02", "18:29")),     # one minute before the cut-off
    (("2026-11-02", "18:30"), ("2026-11-03", "11:00")),     # at the cut-off: next weekday 11:00
    (("2026-11-02", "23:50"), ("2026-11-03", "11:00")),
    (("2026-11-06", "19:05"), ("2026-11-09", "11:00")),     # Friday evening -> Monday
    (("2026-11-07", "12:00"), ("2026-11-09", "11:00")),     # Saturday
    (("2026-11-08", "09:00"), ("2026-11-09", "11:00")),     # Sunday
])
def test_next_call_slot(when, expected):
    assert next_call_slot(ist(*when)) == ist(*expected)


# ---------------------------------------------------------------- 10 working days ---------------------------------

WORKDAYS = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06",
            "2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]


def test_ten_working_days_priya_flow_and_reminders(tmp_path, clock):
    sender = Sender()
    c = make_app(tmp_path, sender)
    store = c.app.state.store
    ids = import_team_export(c)
    assert len(ids) >= 60
    r = c.put("/api/team", json={"caller_name": "Priya", "team": TEAM, "notify_email": True})
    assert r.status_code == 200 and [m["name"] for m in r.json()["team"]] == ["Anil", "Priya", "Rohit"]

    pool = iter(ids)
    expected_due, called_late, rohit_tasks = {}, set(), []
    day_after = {d: WORKDAYS[i + 1] if i + 1 < len(WORKDAYS) else "2026-11-16" for i, d in enumerate(WORKDAYS)}

    def tick(day, start="09:00", end="21:00"):
        t = ist(day, start)
        while t <= ist(day, end):
            clock.now = t
            run_reminders(store, t, sender)
            t += timedelta(minutes=15)

    def log_call(lead_id, day, hm):
        clock.now = ist(day, hm)
        r = c.post(f"/api/leads/{lead_id}/activity", json={"type": "call", "outcome": "meeting_booked", "by": "Priya",
                                                           "notes": "Demo fixed", "at": f"{day}T{hm}:00+05:30"})
        assert r.status_code == 200

    carry = []                                       # evening yeses called the next working day at 11:20
    for day in WORKDAYS:
        tick(day, "09:00", "10:00")
        morning, afternoon, evening = next(pool), next(pool), next(pool)
        clock.now = ist(day, "10:15")
        assert apply_reply(store, morning, "Yes, interested. Please call me.", "email", None, source="imap")["effect"] == "applied"
        apply_reply(store, morning, "YES pls call", "email", None, source="imap")        # second yes: still one task
        expected_due[morning] = ist(day, "10:15")
        called_late.add(morning)
        tick(day, "10:15", "11:15")
        for lead_id in carry:
            log_call(lead_id, day, "11:20")
        carry = []
        clock.now = ist(day, "14:40")
        apply_reply(store, afternoon, "haan ji, interested hai. call kar lo", "whatsapp", None, source="webhook")
        expected_due[afternoon] = ist(day, "14:40")
        rohit = c.post("/api/tasks", json={"title": "Find HR contact", "owner": "Rohit", "lead_id": next(pool),
                                           "due_at": f"{day}T16:00"}).json()
        rohit_tasks.append(rohit["id"])
        tick(day, "11:30", "15:00")
        log_call(afternoon, day, "15:10")
        clock.now = ist(day, "19:05")
        apply_reply(store, evening, "Sure, interested. Call tomorrow.", "email", None, source="imap")
        expected_due[evening] = ist(day_after[day], "11:00")
        carry.append(evening)
        tick(day, "15:15", "21:00")
    tick("2026-11-14", "10:00", "12:00")             # Saturday: nothing new should fire
    tick("2026-11-16", "09:00", "11:15")
    for lead_id in carry:
        log_call(lead_id, "2026-11-16", "11:20")

    tasks = store.read("tasks")
    notes = store.read("notifications")
    for lead_id, due in expected_due.items():
        calls = [t for t in tasks if t.get("lead_id") == lead_id and t["kind"] == "call"]
        assert len(calls) == 1, lead_id
        task = calls[0]
        assert task["owner"] == "Priya" and task["priority"] == "high" and task["due_at"] == due.isoformat()
        kinds = [n["kind"] for n in notes if n.get("task_id") == task["id"]]
        assert kinds.count("task_due") == 1                                     # once each, never repeated
        assert kinds.count("task_overdue") == (1 if lead_id in called_late else 0), (lead_id, kinds)
    # the evening yeses are due 11:00 next working day; Friday's waits for Monday
    assert expected_due[ids[18]] == ist("2026-11-09", "11:00")
    # Rohit has no email: in-app only, once due, once overdue (never closed)
    for tid in rohit_tasks:
        kinds = [n["kind"] for n in notes if n.get("task_id") == tid]
        assert sorted(kinds) == ["task_due", "task_overdue"] and all(n["to"] == "Rohit" for n in notes if n.get("task_id") == tid)
    mails = [m for m in store.read("transactional_outbox") if m["kind"] == "internal"]
    assert {m["to"] for m in mails} == {"priya@mcn-outreach.invalid"}
    assert len(mails) == sum(1 for n in notes if n["to"] == "Priya")
    # the overdue morning tasks are still open; everything Priya called is closed with the call note
    open_calls = [t for t in store.read("tasks") if t["kind"] == "call" and t["status"] == "open"]
    assert {t["lead_id"] for t in open_calls} == called_late


def test_reminder_email_failure_still_notifies_in_app_and_is_not_retried(tmp_path, clock):
    sender = Sender(fail_for="priya@mcn-outreach.invalid")
    c = make_app(tmp_path, sender)
    store = c.app.state.store
    c.put("/api/team", json={"team": TEAM, "notify_email": True})
    clock.now = ist("2026-11-03", "10:00")
    task = c.post("/api/tasks", json={"title": "Call back", "owner": "Priya", "due_at": "2026-11-03T10:30"}).json()
    for hm in ("10:14", "10:15", "10:16", "11:29", "11:30", "11:45"):
        clock.now = ist("2026-11-03", hm)
        run_reminders(store, clock.now, sender)
    kinds = [n["kind"] for n in store.read("notifications") if n["task_id"] == task["id"]]
    assert kinds == ["task_due", "task_overdue"]
    rows = store.read("transactional_outbox")
    assert [r["status"] for r in rows] == ["failed", "failed"] and rows[0]["error"].startswith("SMTP 421")
    # email reminders off: in-app only
    c.put("/api/team", json={"notify_email": False})
    other = c.post("/api/tasks", json={"title": "Second", "owner": "Priya", "due_at": "2026-11-03T12:00"}).json()
    clock.now = ist("2026-11-03", "11:50")
    run_reminders(store, clock.now, sender)
    assert len(store.read("transactional_outbox")) == 2
    assert any(n["task_id"] == other["id"] for n in store.read("notifications"))


def test_snooze_resets_reminders_and_done_tasks_stay_quiet(tmp_path, clock):
    c = make_app(tmp_path)
    store = c.app.state.store
    clock.now = ist("2026-11-04", "10:00")
    t = c.post("/api/tasks", json={"title": "Send proposal", "owner": "Anil", "due_at": "2026-11-04T10:10"}).json()
    run_reminders(store, clock.now)
    assert c.put(f"/api/tasks/{t['id']}", json={"snooze_minutes": 120}).json()["due_at"] == ist("2026-11-04", "12:10").isoformat()   # snooze counts from the later of due time and now
    clock.now = ist("2026-11-04", "11:55")
    run_reminders(store, clock.now)
    assert [n["kind"] for n in store.read("notifications")] == ["task_due", "task_due"]
    c.post(f"/api/tasks/{t['id']}/done", json={"note": "Sent"})
    clock.now = ist("2026-11-04", "15:00")
    assert run_reminders(store, clock.now)["task_reminders"] == 0


# ---------------------------------------------------------------- tasks CRUD + legacy ------------------------------

def test_task_create_snooze_reassign_done_and_validation(tmp_path, clock):
    c = make_app(tmp_path)
    ids = import_team_export(c)
    clock.now = ist("2026-11-05", "09:30")
    bad = [c.post("/api/tasks", json={"title": "  "}), c.post("/api/tasks", json={"title": "x", "priority": "urgent"}),
           c.post("/api/tasks", json={"title": "x", "due_at": "05/11/2026 4pm"}),
           c.post("/api/tasks", json={"title": "x", "lead_id": "nope"})]
    assert [r.status_code for r in bad] == [400, 400, 400, 404]
    t = c.post("/api/tasks", json={"title": "Research decision maker", "lead_id": ids[0], "owner": "Rohit",
                                   "due_at": "2026-11-05T12:00", "priority": "low"}).json()
    assert t["due_at"] == "2026-11-05T06:30:00+00:00" and t["status"] == "open" and t["source"] == "person"
    assert c.put(f"/api/tasks/{t['id']}", json={"snooze_minutes": 2}).status_code == 422
    assert c.put(f"/api/tasks/{t['id']}", json={"priority": "urgent"}).status_code == 400
    assert c.put("/api/tasks/missing", json={"owner": "Priya"}).status_code == 404
    moved = c.put(f"/api/tasks/{t['id']}", json={"owner": "Priya", "due_at": "2026-11-06T11:00:00+05:30"}).json()
    assert moved["owner"] == "Priya" and moved["due_at"] == ist("2026-11-06", "11:00").isoformat()
    board = c.get("/api/tasks/board", params={"owner": "Priya"}).json()
    assert [x["id"] for x in board["upcoming"]] == [t["id"]] and board["counts"]["upcoming"] == 1
    assert c.get("/api/tasks/board", params={"owner": "Rohit"}).json()["counts"]["upcoming"] == 0
    done = c.post(f"/api/tasks/{t['id']}/done", json={"note": "Found Ms. Kavya, HR Head"}).json()
    assert done["status"] == "done" and done["done_note"].startswith("Found")
    board = c.get("/api/tasks/board").json()
    assert board["done"][0]["id"] == t["id"] and sum(board["counts"].values()) == 0
    lead = c.get(f"/api/leads/{ids[0]}").json()
    assert lead["timeline"][-1]["text"] == "Done: Research decision maker — Found Ms. Kavya, HR Head"
    assert c.post("/api/tasks/missing/done", json={}).status_code == 404


def test_legacy_tasks_get_ids_and_done_by_index_closes_them_everywhere(tmp_path, clock):
    c = make_app(tmp_path)
    store = c.app.state.store
    lid = import_team_export(c)[0]
    store.write("tasks", [{"lead_id": lid, "kind": "question", "text": "Kitna charge hai?", "at": "2026-10-20T05:00:00+00:00"},
                          {"lead_id": lid, "kind": "review", "text": "Old one", "at": "2026-10-19T05:00:00+00:00", "done": True},
                          {"lead_id": None, "kind": "unmatched_reply", "text": "From unknown", "at": "2026-10-21T05:00:00+00:00"}])
    board = c.get("/api/tasks/board").json()
    rows = store.read("tasks")
    assert all(t["id"] and t["status"] for t in rows) and [t["status"] for t in rows] == ["open", "done", "open"]
    assert [t["title"] for t in rows] == ["Answer a question", "Check a reply", "Match a reply to a lead"]
    assert board["counts"]["no_date"] == 2
    # the old Today "done" button (by index) must close the task on the board as well
    assert c.post("/api/tasks/done", json={"task_index": 0}).status_code == 200
    board = c.get("/api/tasks/board").json()
    assert board["counts"]["no_date"] == 1 and store.read("tasks")[0]["status"] == "done"
    assert c.post("/api/tasks/done", json={"task_index": 9}).status_code == 404


# ---------------------------------------------------------------- Today ordering -----------------------------------

def test_today_orders_problems_overdue_calls_nudges_starts(tmp_path, clock):
    c = make_app(tmp_path)
    store = c.app.state.store
    ids = import_team_export(c)
    clock.now = ist("2026-11-10", "09:00")
    store.update_lead(ids[0], lambda l: {**l, "sequence": {"step": 1, "blocked": "role"}})          # problem
    c.post("/api/tasks", json={"title": "Overdue follow-up", "lead_id": ids[1], "due_at": "2026-11-09T16:00"})
    c.post("/api/tasks", json={"title": "Later today", "lead_id": ids[2], "due_at": "2026-11-10T17:00"})
    apply_reply(store, ids[3], "Yes interested", "email", None)                                     # call task
    store.update_lead(ids[4], lambda l: {**l, "stage": "interested", "consent": {"text": "yes", "at": "x", "source": "manual"}})
    store.update_lead(ids[5], lambda l: {**l, "activation": {"step": "first_booking", "since": "2026-11-01T00:00:00+00:00",
                                                              "events": [], "usage": {"candidates": 1, "bookings": 1}}})
    today = c.get("/api/today").json()
    order = [(i["kind"], i.get("title")) for i in today["items"]]
    kinds = [k for k, _ in order]
    assert kinds[0] == "problem" and order[1] == ("task", "Overdue follow-up")
    assert kinds.index("call") < kinds.index("nudge") < (kinds.index("start") if "start" in kinds else 99)
    call_items = [i for i in today["items"] if i["kind"] == "call"]
    assert {i["lead_id"] for i in call_items} == {ids[3], ids[4]}             # one per yes lead, no duplicate
    assert today["counts"]["calls"] == 2 and "1 overdue" in today["summary"]
    assert len(today["items"]) <= 20


# ---------------------------------------------------------------- notifications + team ----------------------------

def test_notifications_unread_owner_filter_and_mark_read(tmp_path, clock):
    c = make_app(tmp_path)
    store = c.app.state.store
    c.put("/api/team", json={"team": TEAM})
    clock.now = ist("2026-11-11", "10:00")
    for owner in ("Priya", "Rohit", "Anil"):
        c.post("/api/tasks", json={"title": f"For {owner}", "owner": owner, "due_at": "2026-11-11T10:05"})
    run_reminders(store, clock.now)
    assert len(c.get("/api/notifications", params={"unread": True}).json()) == 3
    mine = c.get("/api/notifications", params={"owner": "Rohit"}).json()
    assert [n["title"].split(": ", 1)[1] for n in mine] == ["For Rohit"]
    assert c.post("/api/notifications/read").json() == {"ok": True}
    assert c.get("/api/notifications", params={"unread": True}).json() == []
    assert len(c.get("/api/notifications").json()) == 3


def test_team_settings_clean_input(tmp_path):
    c = make_app(tmp_path)
    r = c.put("/api/team", json={"caller_name": "   ", "team": [{"name": "  Meena  ", "email": "meena@x.invalid", "role": "caller",
                                                                     "extra": "dropped"}, {"name": ""}, "junk", {"phone": "1"}]}).json()
    assert r["caller_name"] == "Sales team" and r["team"][0]["name"] == "Sales team"            # caller always present
    assert [m["name"] for m in r["team"]] == ["Sales team", "Meena"] and "extra" not in r["team"][1]
    r = c.put("/api/team", json={"caller_name": "Meena"}).json()
    assert [m["name"] for m in r["team"]] == ["Meena"] and r["notify_email"] is False
