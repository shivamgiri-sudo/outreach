"""QA: activity trail completeness/ordering and the LinkedIn queue over several weeks. All data is fictional."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import activities, inbound, linkedin_seq as li, reminders, routes_today
from app.inbound import apply_reply, process_inbound_message
from app.main import create_app
from app.reminders import run_reminders
from app.unsubscribe import unsub_path

FIXTURES = Path(__file__).parent / "fixtures" / "tasks-linkedin-priya"


def ist(day: str, hm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:00+05:30").astimezone(timezone.utc)


class Clock:
    now = ist("2026-11-02", "09:00")


@pytest.fixture
def clock(monkeypatch):
    ck = Clock()

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return ck.now.astimezone(tz) if tz else ck.now.replace(tzinfo=None)

    for module in (activities, inbound, reminders, routes_today):
        monkeypatch.setattr(module, "datetime", FakeDateTime)
    return ck


@pytest.fixture
def app_with_leads(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    raw = (FIXTURES / "team_leads_export.csv").read_text(encoding="utf-8-sig")
    ids = c.post("/api/import/commit", json={"csv": raw, "source": "team export"}).json()["created_ids"]
    return c, c.app.state.store, ids


def li_open(store, lid):
    return [t for t in store.read("tasks") if t.get("lead_id") == lid and t["kind"] == "linkedin" and t["status"] == "open"]


def parse(stamp: str) -> datetime:
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- activity trail -----------------------------------

def test_activity_trail_is_complete_and_in_true_time_order(app_with_leads, clock):
    c, store, ids = app_with_leads
    lid = next(l["id"] for l in store.read("leads") if l.get("email") and l.get("phone"))
    store.update_lead(lid, lambda l: {**l, "timeline": [{**e, "at": "2026-11-01T06:00:00+00:00"} for e in l["timeline"]]})
    lead = next(l for l in store.read("leads") if l["id"] == lid)
    assert c.post(f"/api/leads/{lid}/enroll").status_code == 200
    c.post("/api/sequences/run", json={"now": "2026-11-03T05:00:00+00:00"})              # email 1 (practice), 10:30 IST
    clock.now = ist("2026-11-03", "12:00")
    apply_reply(store, lid, "Kitna cost hai per role? Please share details.", "email", None, source="imap")
    store.update("whatsapp_outbox", lambda rows: [*rows, {"lead_id": lid, "sent_at": "2026-11-03T08:00:00+00:00", "template": "demo_link",
                                                          "body": "Hi, here is the demo link. Reply STOP to opt out.", "status": "dry_run",
                                                          "to": lead.get("phone")}])
    # a WhatsApp provider reports its time in IST: 14:00 IST = 08:30 UTC, i.e. after the 13:30 IST template
    clock.now = ist("2026-11-03", "14:01")
    process_inbound_message(store, {"channel": "whatsapp", "from_phone": lead.get("phone"), "text": "ok will check",
                                    "message_id": "wamid.fake-1", "received_at": "2026-11-03T14:00:00+05:30"})
    clock.now = ist("2026-11-03", "15:30")
    r = c.post(f"/api/leads/{lid}/activity", json={"type": "call", "outcome": "connected", "notes": "Asked for pricing deck",
                                                   "duration_minutes": 7, "by": "Priya", "at": "2026-11-03T15:20:00+05:30"})
    assert r.status_code == 200
    r = c.post(f"/api/leads/{lid}/activity", json={"type": "note", "notes": "Hiring 12 drivers for Pune hub", "by": "Rohit",
                                                   "follow_up_at": "2026-11-05T11:00", "follow_up_text": "Send case study"}).json()
    assert r["follow_up_task"]["due_at"] == ist("2026-11-05", "11:00").isoformat() and r["follow_up_task"]["owner"] == "Rohit"
    store.update_lead(lid, lambda l: {**l, "meetings": [{"id": "m1", "title": "Demo", "start": "2026-11-06T11:00:00+05:30",
                                                         "end": "2026-11-06T11:30:00+05:30", "status": "scheduled",
                                                         "join_url": "https://meet.example/abc", "created_at": "2026-11-03T16:00:00+05:30"}]})
    trail = c.get(f"/api/leads/{lid}/activity").json()
    types = [i["type"] for i in trail]
    for kind in ("email", "whatsapp", "call", "note", "meeting", "task"):
        assert kind in types, kind
    email = next(i for i in trail if i["type"] == "email")
    assert unsub_path(store, lid) in email["body"] and email["status"] == "practice (not sent)"
    replies = [i for i in trail if i["direction"] == "in"]
    assert any("Kitna cost hai per role? Please share details." in i["body"] for i in replies)    # pasted: from the timeline
    assert any(i["body"] == "ok will check" and i["type"] == "whatsapp" for i in replies)       # captured message in full
    assert len([i for i in replies if "Kitna cost" in (i.get("body") or "")]) == 1         # reply shown once, in full
    assert any(i["type"] == "task" and i["title"] == "Answer a question" for i in trail)
    assert [i["body"] for i in trail if i["type"] == "note"] == ["Hiring 12 drivers for Pune hub"]   # a logged note shows once
    stamps = [parse(i["at"]) for i in trail]
    assert stamps == sorted(stamps, reverse=True)                                           # real time, not string order


# ---------------------------------------------------------------- LinkedIn queue over weeks -----------------------

def test_linkedin_queue_daily_limit_acceptance_rhythm_and_case_study(app_with_leads):
    c, store, ids = app_with_leads
    with_li = [l["id"] for l in store.read("leads") if l.get("linkedin_url")]
    assert len(with_li) >= 40 and all(l["linkedin_url"].startswith("https://www.linkedin.com/in/")
                                      for l in store.read("leads") if l.get("linkedin_url"))
    assert c.put("/api/linkedin/settings", json={"linkedin_daily_connections": 41}).status_code == 422
    assert c.put("/api/linkedin/settings", json={"case_study_url": "http://bmi.example/case"}).status_code == 400
    c.put("/api/linkedin/settings", json={"linkedin_daily_connections": 10, "linkedin_owner": "Rohit"})
    steps = c.get("/api/linkedin/steps").json()
    assert [s["n"] for s in steps if s["skipped"]] == [4]

    started = []
    for d, day in enumerate(["2026-11-02", "2026-11-03", "2026-11-04"]):
        now = ist(day, "10:00")
        q = li.queue(store, now)
        assert q["daily_limit"] == 10 and len(q["suggestions"]) == 10 and q["started_today"] == 0
        roles = [next(l for l in store.read("leads") if l["id"] == s["lead_id"]).get("open_roles") or 0 for s in q["suggestions"]]
        assert roles == sorted(roles, reverse=True)
        for s in q["suggestions"]:
            li.start(store, s["lead_id"], now)
            li.mark_sent(store, s["lead_id"], 1, now + timedelta(minutes=5))
            started.append(s["lead_id"])
        q = li.queue(store, now + timedelta(hours=1))
        assert q["suggestions"] == [] and q["started_today"] == 10
        assert len(q["waiting_for_acceptance"]) == 10 * (d + 1) and q["due"] == []
    assert len(set(started)) == 30
    owners = {t["owner"] for t in store.read("tasks") if t["kind"] == "linkedin"}
    assert owners == {"Rohit"}

    a, b, late = started[0], started[1], started[2]
    li.mark_accepted(store, a, ist("2026-11-05", "09:00"))
    step2 = li_open(store, a)[0]
    assert step2["title"] == "LinkedIn step 2: send message" and step2["due_at"] == ist("2026-11-07", "09:00").isoformat()
    assert all(d["lead_id"] != a for d in li.queue(store, ist("2026-11-05", "10:00"))["due"])      # not due yet
    assert any(d["lead_id"] == a and d["step"] == 2 for d in li.queue(store, ist("2026-11-07", "08:00"))["due"])
    # rhythm for lead a: 2 -> 3 (+4 days) -> 5 (case study skipped, +4) -> 6 (+5) -> 7 (+7) -> finished
    t = ist("2026-11-07", "10:00")
    seen = []
    for step in (2, 3, 5, 6, 7):
        nxt = li.mark_sent(store, a, step, t)["next_task"]
        if nxt is None:
            break
        n = int(nxt["title"].split()[2].rstrip(":"))
        gap = parse(nxt["due_at"]) - t
        seen.append((n, gap.days))
        t = parse(nxt["due_at"])
    assert seen == [(3, 4), (5, 4), (6, 5), (7, 7)]
    assert next(l for l in store.read("leads") if l["id"] == a)["linkedin_seq"]["status"] == "finished"

    # lead b accepted, sends 2 and 3; then a real case-study link is set: step 4 is offered next
    li.mark_accepted(store, b, ist("2026-11-05", "09:00"))
    li.mark_sent(store, b, 2, ist("2026-11-07", "09:00"))
    c.put("/api/linkedin/settings", json={"case_study_url": "https://bookmyinterview.example/case-study"})
    nxt = li.mark_sent(store, b, 3, ist("2026-11-11", "09:00"))["next_task"]
    assert nxt["title"].startswith("LinkedIn step 4") and "Would you be interested?" in nxt["text"]
    branch = c.post(f"/api/leads/{b}/linkedin/branch", json={"key": "case_study_yes"}).json()["text"]
    assert "https://bookmyinterview.example/case-study" in branch
    assert c.post(f"/api/leads/{b}/linkedin/branch", json={"key": "discount"}).status_code == 400
    slot = c.post(f"/api/leads/{b}/linkedin/branch", json={"key": "meeting_yes", "option_1": "Tue 11:00",
                                                          "option_2": "Wed 16:00"}).json()["text"]
    assert "Tue 11:00 or Wed 16:00" in slot


def test_case_study_link_set_late_never_reopens_a_skipped_step(app_with_leads):
    c, store, ids = app_with_leads
    lid = next(l["id"] for l in store.read("leads") if l.get("linkedin_url"))
    li.start(store, lid, ist("2026-11-02", "10:00"))
    li.mark_sent(store, lid, 1, ist("2026-11-02", "10:05"))
    li.mark_accepted(store, lid, ist("2026-11-03", "10:00"))
    t = ist("2026-11-05", "10:00")
    li.mark_sent(store, lid, 2, t)
    li.mark_sent(store, lid, 3, t + timedelta(days=4))
    li.mark_sent(store, lid, 5, t + timedelta(days=8))                  # step 4 skipped: no link yet
    c.put("/api/linkedin/settings", json={"case_study_url": "https://bookmyinterview.example/case-study"})
    nxt = li.mark_sent(store, lid, 6, t + timedelta(days=13))["next_task"]
    assert nxt["title"].startswith("LinkedIn step 7")                    # not "step 4" after the meeting ask
    assert li.mark_sent(store, lid, 7, t + timedelta(days=20))["next_task"] is None
    assert next(l for l in store.read("leads") if l["id"] == lid)["linkedin_seq"]["status"] == "finished"


def test_replies_on_any_channel_stop_linkedin_and_it_stays_stopped(app_with_leads, clock):
    c, store, ids = app_with_leads
    leads = [l for l in store.read("leads") if l.get("linkedin_url")][:4]
    for lead in leads:
        li.start(store, lead["id"], ist("2026-11-02", "10:00"))
        li.mark_sent(store, lead["id"], 1, ist("2026-11-02", "10:05"))
        li.mark_accepted(store, lead["id"], ist("2026-11-03", "10:00"))
    by_email, by_whatsapp, by_linkedin, by_unsub = (l["id"] for l in leads)
    clock.now = ist("2026-11-04", "11:00")
    apply_reply(store, by_email, "Not now, maybe next quarter", "email", None, source="imap")
    apply_reply(store, by_whatsapp, "Interested, call me", "whatsapp", None, source="webhook")
    li.mark_replied(store, by_linkedin, "Yes interested, let's talk")
    c.post(unsub_path(store, by_unsub))
    for lid in (by_email, by_whatsapp, by_linkedin):
        lead = next(l for l in store.read("leads") if l["id"] == lid)
        assert lead["linkedin_seq"]["status"] == "stopped" and li_open(store, lid) == [], lid
        assert c.post(f"/api/leads/{lid}/linkedin/sent", json={"step": 2}).status_code == 400
        assert c.post(f"/api/leads/{lid}/linkedin/accepted").status_code == 400            # never restarted
        assert li_open(store, lid) == []
    calls = [t for t in store.read("tasks") if t["kind"] == "call" and t["status"] == "open"]
    assert {t["lead_id"] for t in calls} == {by_whatsapp, by_linkedin}
    # an unsubscribe link click also takes the lead out of the LinkedIn queue
    due = li.queue(store, ist("2026-11-06", "10:00"))["due"]
    assert by_unsub not in {d["lead_id"] for d in due}
    assert c.post(f"/api/leads/{by_unsub}/linkedin/accepted").status_code == 400


def test_no_reminders_for_automatic_tasks_of_unsubscribed_leads(app_with_leads, clock):
    c, store, ids = app_with_leads
    lid = ids[0]
    clock.now = ist("2026-11-04", "10:00")
    apply_reply(store, lid, "Yes interested", "email", None)
    person = c.post("/api/tasks", json={"title": "Confirm erasure by phone", "lead_id": lid, "due_at": "2026-11-04T12:00"}).json()
    clock.now = ist("2026-11-04", "10:05")
    apply_reply(store, lid, "Please unsubscribe me and stop messaging", "email", None)
    for hm in ("10:10", "11:50", "13:30"):
        clock.now = ist("2026-11-04", hm)
        run_reminders(store, clock.now)
    notes = store.read("notifications")
    call = next(t for t in store.read("tasks") if t["kind"] == "call")
    assert [n for n in notes if n["task_id"] == call["id"] and n["kind"] == "task_overdue"] == []
    assert all("Call them" not in n["title"] for n in notes if n["at"] > ist("2026-11-04", "10:06").isoformat())
    assert sorted(n["kind"] for n in notes if n["task_id"] == person["id"]) == ["task_due", "task_overdue"]   # a person's task stays


def test_erasure_request_task_is_visible_and_reminded_although_the_lead_is_unsubscribed(app_with_leads, clock):
    c, store, ids = app_with_leads
    lid = ids[1]
    clock.now = ist("2026-11-05", "10:00")
    apply_reply(store, lid, "Please delete my data from your records.", "email", None, source="imap")
    assert next(l for l in store.read("leads") if l["id"] == lid)["stage"] == "unsubscribed"
    board = c.get("/api/tasks/board").json()
    assert [t["title"] for t in board["upcoming"] + board["today"]] == ["Erasure request"]
    assert [i["title"] for i in c.get("/api/today").json()["items"] if i.get("lead_id") == lid] == ["Erasure request"]
    clock.now = ist("2026-11-06", "09:50")
    run_reminders(store, clock.now)
    assert [n["kind"] for n in store.read("notifications")] == ["task_due"]
