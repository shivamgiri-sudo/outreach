"""Activity trail, tasks with due times, automatic call tasks and reminders."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.activities import next_call_slot, task_buckets
from app.main import create_app
from app.reminders import run_reminders
from app.unsubscribe import unsub_path
from app.sequences import IST

T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)   # Tuesday 10:30 IST
CSV = ("company,contact_name,email,phone,website,open_roles,company_size,country\n"
       "Acme,Ravi Kumar,ravi@acme.com,98765 43210,https://acme.com,12,200,IN\n")


class Sender:
    def __init__(self):
        self.sent = []

    def __call__(self, message):
        self.sent.append(message)
        return {"status": "sent", "provider_id": f"t{len(self.sent)}"}


def make(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    return c


def lead(c):
    return c.get("/api/leads").json()[0]


def test_trail_shows_full_email_text_replies_calls_and_notes(tmp_path):
    c = make(tmp_path)
    lid = lead(c)["id"]
    c.post(f"/api/leads/{lid}/enroll")
    c.post("/api/sequences/run", json={"now": T0.isoformat()})
    c.post(f"/api/leads/{lid}/reply", json={"text": "How much does it cost?"})
    r = c.post(f"/api/leads/{lid}/activity", json={"type": "call", "outcome": "connected", "notes": "Wants a demo for 5 roles",
                                                   "at": "2026-11-03T15:00:00+05:30", "duration_minutes": 6, "by": "Priya"})
    assert r.status_code == 200
    trail = c.get(f"/api/leads/{lid}/activity").json()
    kinds = [i["type"] for i in trail]
    email = next(i for i in trail if i["type"] == "email")
    assert "Acme" in email["title"] or email["body"]                        # full email text, not just "Email 1 — sent"
    assert "unsubscribe" in email["body"].lower() and email["status"] == "practice (not sent)"
    assert "call" in kinds and "reply" in kinds and "task" in kinds         # the question became a task
    call = next(i for i in trail if i["type"] == "call")
    assert call["status"] == "connected" and "5 roles" in call["body"] and call["at"] == "2026-11-03T09:30:00+00:00"   # stored in UTC
    assert trail == sorted(trail, key=lambda i: i["at"] or "", reverse=True)


def test_activity_validates_type_and_outcome(tmp_path):
    c = make(tmp_path)
    lid = lead(c)["id"]
    assert c.post(f"/api/leads/{lid}/activity", json={"type": "fax"}).status_code == 400
    assert c.post(f"/api/leads/{lid}/activity", json={"type": "call", "outcome": "maybe"}).status_code == 400
    assert c.post(f"/api/leads/{lid}/activity", json={"type": "note", "at": "tomorrow"}).status_code == 400


def test_follow_up_from_an_activity_becomes_a_task_with_due_time(tmp_path):
    c = make(tmp_path)
    lid = lead(c)["id"]
    r = c.post(f"/api/leads/{lid}/activity", json={"type": "call", "outcome": "call_back", "notes": "Busy, call Thursday",
                                                   "follow_up_at": "2026-11-05T11:00", "follow_up_text": "Call Ravi back", "by": "Priya"}).json()
    task = r["follow_up_task"]
    assert task["due_at"] == "2026-11-05T05:30:00+00:00" and task["owner"] == "Priya" and task["title"] == "Follow up"


def test_yes_reply_creates_one_call_task_for_priya_and_logging_the_call_closes_it(tmp_path):
    c = make(tmp_path)
    lid = lead(c)["id"]
    c.post(f"/api/leads/{lid}/reply", json={"text": "Yes please"})
    calls = [t for t in c.get("/api/tasks/board").json()["today"] + c.get("/api/tasks/board").json()["upcoming"]
             + c.get("/api/tasks/board").json()["overdue"] if t["kind"] == "call"]
    assert len(calls) == 1 and calls[0]["owner"] == "Sales team" and calls[0]["priority"] == "high"
    c.post(f"/api/leads/{lid}/stage", json={"stage": "whatsapp_active"})
    assert sum(1 for t in c.app.state.store.read("tasks") if t.get("kind") == "call") == 1     # no duplicate call task
    today = c.get("/api/today").json()
    assert any(i["kind"] == "call" and i["action"] == "Open call desk" for i in today["items"])
    c.post(f"/api/leads/{lid}/activity", json={"type": "call", "outcome": "meeting_booked"})
    assert not any(i["kind"] == "call" for i in c.get("/api/today").json()["items"])


def test_tasks_can_be_created_snoozed_reassigned_and_completed(tmp_path):
    c = make(tmp_path)
    lid = lead(c)["id"]
    c.put("/api/team", json={"team": [{"name": "Priya", "email": "priya@team.in", "role": "caller"}, {"name": "Shivam", "email": "s@team.in"}]})
    task = c.post("/api/tasks", json={"lead_id": lid, "title": "Send pricing deck", "due_at": "2026-11-04T10:00", "priority": "high"}).json()
    assert task["due_at"] == "2026-11-04T04:30:00+00:00"
    moved = c.put(f"/api/tasks/{task['id']}", json={"owner": "Shivam", "snooze_minutes": 30}).json()
    assert moved["owner"] == "Shivam" and moved["due_at"] != task["due_at"]
    done = c.post(f"/api/tasks/{task['id']}/done", json={"note": "Sent on WhatsApp"}).json()
    assert done["status"] == "done" and done["done_note"] == "Sent on WhatsApp"
    assert any("Done: Send pricing deck" in e["text"] for e in lead(c)["timeline"])
    assert c.post("/api/tasks", json={"title": " "}).status_code == 400
    assert c.post("/api/tasks", json={"title": "x", "priority": "urgent"}).status_code == 400


def test_old_tasks_get_ids_and_still_close_by_index(tmp_path):
    c = make(tmp_path)
    c.app.state.store.update("tasks", lambda rows: [*rows, {"lead_id": None, "kind": "workflow", "text": "legacy", "at": T0.isoformat()}])
    board = c.get("/api/tasks/board").json()
    legacy = next(t for t in board["no_date"] if t["text"] == "legacy")
    assert legacy["id"] and legacy["status"] == "open"
    c.post("/api/tasks/done", json={"task_index": 0})
    assert c.app.state.store.read("tasks")[0]["done"] is True


def test_buckets_and_call_slots():
    now = T0
    tasks = [{"id": "a", "status": "open", "due_at": (now - timedelta(days=1)).isoformat()},
             {"id": "b", "status": "open", "due_at": (now + timedelta(hours=2)).isoformat()},
             {"id": "c", "status": "open", "due_at": (now + timedelta(days=2)).isoformat()},
             {"id": "d", "status": "open", "due_at": None}, {"id": "e", "status": "done", "due_at": now.isoformat()}]
    b = task_buckets(tasks, now)
    assert [t["id"] for t in b["overdue"]] == ["a"] and [t["id"] for t in b["today"]] == ["b"]
    assert [t["id"] for t in b["upcoming"]] == ["c"] and [t["id"] for t in b["no_date"]] == ["d"]
    assert next_call_slot(T0) == T0                                                # Tuesday 10:30 IST: call today
    friday_evening = datetime(2026, 11, 6, 14, 0, tzinfo=timezone.utc)             # 19:30 IST Friday
    slot = next_call_slot(friday_evening).astimezone(IST)
    assert slot.weekday() == 0 and slot.hour == 11                                 # Monday 11:00


def test_task_reminders_fire_once_before_due_and_once_overdue(tmp_path):
    c = make(tmp_path)
    store = c.app.state.store
    c.put("/api/team", json={"team": [{"name": "Priya", "email": "priya@team.in"}], "notify_email": True})
    due = T0 + timedelta(hours=1)
    c.post("/api/tasks", json={"title": "Call back Ravi", "due_at": due.isoformat(), "owner": "Priya"})
    sender = Sender()
    assert run_reminders(store, T0, sender)["task_reminders"] == 0
    assert run_reminders(store, due - timedelta(minutes=10), sender)["task_reminders"] == 1
    assert run_reminders(store, due - timedelta(minutes=5), sender)["task_reminders"] == 0       # only once
    assert run_reminders(store, due + timedelta(hours=1, minutes=1), sender)["task_reminders"] == 1   # overdue alert
    notes = c.get("/api/notifications?unread=true").json()
    assert len(notes) == 2 and notes[0]["title"].startswith("Overdue")
    assert [m["to"] for m in sender.sent] == ["priya@team.in", "priya@team.in"]
    assert store.read("transactional_outbox")[0]["kind"] == "internal"
    c.post("/api/notifications/read")
    assert c.get("/api/notifications?unread=true").json() == []


def test_meeting_reminders_go_to_the_lead_24h_and_1h_before_only_when_live(tmp_path):
    c = make(tmp_path)
    store = c.app.state.store
    lid = lead(c)["id"]
    start = T0 + timedelta(days=2)
    meeting = {"id": "m1", "provider": "google", "title": "BookMyInterview.ai demo — Acme", "start": start.isoformat(),
               "end": (start + timedelta(minutes=30)).isoformat(), "join_url": "https://meet.example/abc", "status": "scheduled"}
    store.update_lead(lid, lambda l: {**l, "meetings": [meeting]})
    sender = Sender()
    assert run_reminders(store, start - timedelta(hours=30), sender)["meeting_reminders"] == 0
    assert run_reminders(store, start - timedelta(hours=23), sender)["meeting_reminders"] == 1
    row = store.read("transactional_outbox")[-1]
    assert row["status"] == "dry_run" and "meet.example/abc" in row["body"] and sender.sent == []   # practice mode: not sent
    store.update("settings", lambda s: {**s, "live_sending": True})
    assert run_reminders(store, start - timedelta(minutes=50), sender)["meeting_reminders"] == 1
    assert sender.sent[-1]["to"] == "ravi@acme.com" and "in about an hour" in sender.sent[-1]["body"]
    assert run_reminders(store, start - timedelta(minutes=10), sender)["meeting_reminders"] == 1     # owner heads-up
    assert run_reminders(store, start - timedelta(minutes=5), sender)["meeting_reminders"] == 0
    assert set(store.read("leads")[0]["meetings"][0]["reminders_sent"]) == {"24h", "1h", "owner_15m"}


def test_no_meeting_reminder_email_to_an_unsubscribed_lead(tmp_path):
    c = make(tmp_path)
    store = c.app.state.store
    lid = lead(c)["id"]
    start = T0 + timedelta(minutes=50)
    store.update_lead(lid, lambda l: {**l, "meetings": [{"id": "m1", "title": "Demo", "start": start.isoformat(), "status": "scheduled"}]})
    c.post(unsub_path(store, lid))
    store.update("settings", lambda s: {**s, "live_sending": True})
    sender = Sender()
    run_reminders(store, T0, sender)
    assert sender.sent == []
