"""QA: .ics invites (RFC 5545 METHOD:REQUEST / CANCEL), reminders 24 h / 1 h to the lead and 15 min to the owner,
the no-show follow-up task, across IST/UTC boundaries, with practice vs live sending.

RFC 5545 (iCalendar) and RFC 5546 (iTIP: METHOD, SEQUENCE, same UID on CANCEL) shape the invite checks. Time travel
uses run_reminders(store, now) exactly as the background timer calls it. All people and domains are fictional.
"""
from datetime import datetime, timedelta, timezone

from app.channels import meetings
from app.reminders import run_reminders
from app.unsubscribe import unsub_path
from tests.qa.calendar_meetings_fakes import (Sender, client, connect_google, connect_zoom, import_leads, ist, outbox,
                                             replies, ticks, zoom_http)
from tests.channel_fakes import FakeHTTP

UTC = timezone.utc
FARAH = "farah.khan@desi-hospitality-04.example"
POOJA = "pooja.rao@sagar-fintech-01.example"
SNEHA = "sneha.kaur@kesar-foods-06.example"
MANOJ = "manoj.pillai@kesar-foods-06.example"          # other contact on Sneha's lead


def ics_props(ics: str) -> dict:
    unfolded = ics.replace("\r\n ", "")
    return {line.split(":", 1)[0].split(";", 1)[0]: line.split(":", 1)[1] for line in unfolded.split("\r\n") if ":" in line}


# ---------- .ics invites ----------
def test_zoom_invite_and_cancellation_in_practice_mode(tmp_path):
    c = client(tmp_path, zoom_http())
    lead = import_leads(c)[FARAH]
    connect_zoom(c)
    late = ist(3, 0, 15)                                                   # 00:15 IST = 18:45 UTC the day before
    r = c.post(f"/api/leads/{lead['id']}/meetings", json={"start": late.isoformat(), "duration_minutes": 45,
                                                          "notes": "Hiring 25 stewards; Andheri, Powai, Thane"})
    assert r.status_code == 200, r.text
    meeting = r.json()["meeting"]
    assert r.json()["invite"] == {"status": "dry_run", "to": FARAH} and meeting["invite"] == "dry_run"
    row = outbox(c, "meeting_invite")[0]
    ics = row["ics"]
    assert ics.endswith("\r\n") and "\n" not in ics.replace("\r\n", "")
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))   # folded at 75 octets
    p = ics_props(ics)
    day_before = (late - timedelta(days=1)).strftime("%Y%m%d")
    assert p["METHOD"] == "REQUEST" and p["STATUS"] == "CONFIRMED" and p["SEQUENCE"] == "0"
    assert p["DTSTART"] == f"{day_before}T184500Z" and p["DTEND"] == f"{day_before}T193000Z"
    assert p["UID"] == f"{meeting['id']}@outreach.local" and p["URL"] == replies()["zoom_meeting"]["join_url"]
    assert "Andheri\\, Powai\\, Thane" in p["DESCRIPTION"] and p["ATTENDEE"] == f"mailto:{FARAH}"
    store = c.app.state.store
    assert "Unsubscribe: " in row["body"] and row["body"].rstrip().endswith(unsub_path(store, lead["id"]))
    # cancel: same UID, METHOD:CANCEL, higher SEQUENCE, STATUS:CANCELLED; reminders stop
    out = c.post(f"/api/meetings/{meeting['id']}/cancel").json()
    assert out["meeting"]["status"] == "cancelled" and "cancellation (dry_run)" in out["message"]
    cancel = outbox(c, "meeting_cancel")[0]
    q = ics_props(cancel["ics"])
    assert q["UID"] == p["UID"] and q["METHOD"] == "CANCEL" and q["SEQUENCE"] == "1" and q["STATUS"] == "CANCELLED"
    assert q["DTSTART"] == p["DTSTART"] and cancel["subject"].startswith("Cancelled: ")
    assert c.post(f"/api/meetings/{meeting['id']}/cancel").json()["message"] == "Already cancelled."
    assert len(outbox(c, "meeting_cancel")) == 1
    for when in (late - timedelta(hours=24), late - timedelta(hours=1), late - timedelta(minutes=10), late + timedelta(hours=2)):
        assert run_reminders(store, when) == {"task_reminders": 0, "meeting_reminders": 0, "no_show_tasks": 0}


def test_live_invites_google_only_on_request_and_fixed_booking_link(tmp_path):
    c = client(tmp_path)
    leads = import_leads(c)
    store = c.app.state.store
    store.update("settings", lambda s: {**s, "live_sending": True})
    connect_google(c)
    sender = Sender()
    http = FakeHTTP({"/events?conferenceDataVersion=1": replies()["google_event"]})
    plain = meetings.book(store, leads[POOJA]["id"], {"start": ist(3).isoformat()}, http, sender=sender)
    assert plain["invite"] is None and sender.sent == []                    # Google emails the attendee itself
    own = meetings.book(store, leads[POOJA]["id"], {"start": ist(4).isoformat(), "send_invite": True}, http, sender=sender)
    assert own["invite"]["status"] == "sent" and sender.sent[-1]["ics_method"] == "REQUEST"
    assert "https://meet.google.com/fak-emee-tqa" in sender.sent[-1]["ics"]
    c.put("/api/channels/calendar/booking_link", json={"values": {"booking_url": "https://book.example/priya"}})
    fixed = meetings.book(store, leads[FARAH]["id"], {"provider": "booking_link", "start": ist(5, 16).isoformat()}, http, sender=sender)
    assert fixed["kind"] == "event" and fixed["meeting"]["join_url"] == "https://book.example/priya"
    assert fixed["invite"] == {"status": "sent", "to": FARAH}
    out = meetings.cancel(store, fixed["meeting"]["id"], http, sender=sender)
    assert "Also cancel it in your calendar tool" in out["message"] and sender.sent[-1]["ics_method"] == "CANCEL"
    assert all("Unsubscribe: " in m["body"] for m in sender.sent)


def test_no_invite_for_unsubscribed_or_suppressed_people(tmp_path):
    c = client(tmp_path, zoom_http())
    leads = import_leads(c)
    connect_zoom(c)
    lead = leads[FARAH]
    c.post(f"/api/leads/{lead['id']}/stage", json={"stage": "unsubscribed"})
    r = c.post(f"/api/leads/{lead['id']}/meetings", json={"start": ist(3).isoformat()})
    assert r.status_code == 400 and "unsubscribed" in r.json()["detail"] and outbox(c) == []


# ---------- reminders and the no-show task over two days of timer ticks ----------
def _meeting(mid, start, status="scheduled", attendee="", minutes=30, join="https://zoom.example/j/9000010"):
    return {"id": mid, "provider": "zoom", "title": f"Demo {mid}", "start": start.isoformat(),
            "end": (start + timedelta(minutes=minutes)).isoformat(), "join_url": join, "status": status,
            "created_at": "2026-11-01T05:00:00+00:00", "external_id": "", "attendee_email": attendee}


def test_two_days_of_ticks_send_each_reminder_once_and_one_no_show_task(tmp_path):
    c = client(tmp_path)
    leads = import_leads(c)
    store = c.app.state.store
    sneha = leads[SNEHA]
    assert MANOJ in [o.get("email") for o in c.get(f"/api/leads/{sneha['id']}").json().get("other_contacts") or []]
    midnight = datetime(2026, 11, 20, 18, 45, tzinfo=UTC)                 # Sat 21 Nov 00:15 IST
    morning = datetime(2026, 11, 20, 4, 30, tzinfo=UTC)                   # Fri 20 Nov 10:00 IST
    plan = {
        FARAH: [_meeting("m-midnight", midnight, minutes=45)],
        POOJA: [_meeting("m-morning", morning), _meeting("m-cancelled", morning + timedelta(hours=3), "cancelled"),
                _meeting("m-done", morning + timedelta(hours=5), "done")],
        SNEHA: [_meeting("m-manoj", morning + timedelta(hours=7), attendee=MANOJ)],
    }
    for email, items in plan.items():
        store.update_lead(leads[email]["id"], lambda l, items=items: {**l, "meetings": items})
    store.update("settings", lambda s: {**s, "live_sending": True})
    sender = Sender()
    totals = {"meeting_reminders": 0, "no_show_tasks": 0}
    for now in ticks(morning - timedelta(hours=30), midnight + timedelta(hours=4), timedelta(minutes=10)):
        out = run_reminders(store, now, sender)
        for k in totals:
            totals[k] += out[k]
    mails = [m for m in sender.sent if m["kind"] == "meeting_reminder"]
    by_to = {}
    for m in mails:
        by_to.setdefault(m["to"], []).append(m)
    # 3 live meetings x (24h + 1h) emails; the other contact who booked is the one reminded, not the primary
    assert sorted(by_to) == sorted([FARAH, POOJA, MANOJ]) and all(len(v) == 2 for v in by_to.values())
    assert all("Unsubscribe: " in m["body"] and "/u/" in m["body"] and "Join: https://zoom.example/j/9000010" in m["body"]
               for m in mails)
    assert "Sat 21 Nov, 12:15 AM IST" in by_to[FARAH][0]["subject"]
    assert by_to[MANOJ][0]["body"].startswith("Hi Manoj,") and by_to[FARAH][0]["body"].startswith("Hi Farah,")
    owner = [n for n in store.read("notifications") if n["kind"] == "meeting_soon"]
    assert len(owner) == 3 and totals["meeting_reminders"] == 9
    assert not any("m-cancelled" in str(m) or "m-done" in str(m) for m in mails)
    tasks = [t for t in store.read("tasks") if t.get("kind") == "meeting_follow_up"]
    assert totals["no_show_tasks"] == 3 and sorted(t["meeting_id"] for t in tasks) == ["m-manoj", "m-midnight", "m-morning"]
    # the owner marks the morning one done: its task closes, nothing new appears on later ticks
    meetings.mark_done(store, "m-morning")
    assert next(t for t in store.read("tasks") if t.get("meeting_id") == "m-morning")["status"] != "open"
    assert run_reminders(store, midnight + timedelta(hours=5), sender)["no_show_tasks"] == 0


def test_no_show_timing_follows_the_real_end_and_the_7_day_window(tmp_path):
    c = client(tmp_path)
    leads = import_leads(c)
    store = c.app.state.store
    start = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)                      # 17:30 IST, 90 minutes
    store.update_lead(leads[FARAH]["id"], lambda l: {**l, "meetings": [_meeting("m-long", start, minutes=90),
                                                                       _meeting("m-stale", start - timedelta(days=9))]})
    assert run_reminders(store, start + timedelta(minutes=60))["no_show_tasks"] == 0      # still running
    assert run_reminders(store, start + timedelta(minutes=119))["no_show_tasks"] == 0
    assert run_reminders(store, start + timedelta(minutes=120))["no_show_tasks"] == 1     # end + 30 min; stale one skipped
    assert run_reminders(store, start + timedelta(days=1))["no_show_tasks"] == 0


def test_practice_mode_reminders_are_rows_only_and_late_timer_sends_the_nearest_one(tmp_path):
    c = client(tmp_path)
    leads = import_leads(c)
    store = c.app.state.store
    start = datetime(2026, 11, 20, 4, 30, tzinfo=UTC)
    store.update_lead(leads[POOJA]["id"], lambda l: {**l, "meetings": [_meeting("m1", start)]})
    sender = Sender()
    # the server was off until 40 minutes before: only the 1 h email and the owner heads-up, not the stale 24 h one
    out = run_reminders(store, start - timedelta(minutes=40), sender)
    assert out["meeting_reminders"] == 1 and sender.sent == []
    assert run_reminders(store, start - timedelta(minutes=14), sender)["meeting_reminders"] == 1
    rows = outbox(c, "meeting_reminder")
    assert len(rows) == 1 and rows[0]["status"] == "dry_run" and "in about an hour" in rows[0]["body"]
