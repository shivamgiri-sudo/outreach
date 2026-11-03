"""Meeting invites (.ics), practice mode, cancellation, provider attachments, reminders and the no-show task."""
import base64
from datetime import datetime, timedelta, timezone

from app.channels import config
from app.channels.email_send import http_request_for, invite_attachments
from app.channels.invites import build_ics
from app.reminders import run_reminders
from tests.channel_fakes import FakeHTTP, SMTPFactory, add_lead, make_client

IST = timezone(timedelta(hours=5, minutes=30))
ZOOM_HTTP = {"zoom.us/oauth/token": {"access_token": "zoom-tok", "expires_in": 3600},
             "users/me/meetings": {"id": 987654321, "join_url": "https://zoom.us/j/987654321"}}
MEETING = {"id": "m1abc", "title": "BookMyInterview.ai demo — Acme, Retail; India", "start": "2026-11-05T10:00:00+05:30",
           "end": "2026-11-05T10:30:00+05:30", "join_url": "https://zoom.us/j/987654321", "notes": "Bring the JD"}


def future(days=2, hour=11):
    day = (datetime.now(IST) + timedelta(days=days)).date()
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=IST).isoformat()


def zoom_client(tmp_path, http=None, **kw):
    c = make_client(tmp_path, http or FakeHTTP(ZOOM_HTTP), **kw)
    c.put("/api/channels/calendar/zoom", json={"values": {"account_id": "acc1", "client_id": "zc", "client_secret": "zs"}})
    return c


def outbox(c, kind=None):
    return [r for r in c.app.state.store.read("transactional_outbox") if kind is None or r["kind"] == kind]


# ---------- the .ics itself ----------
def test_ics_request_has_every_required_field_in_utc_and_folds_long_lines():
    ics = build_ics(MEETING, "priya@mail.example.in", "Priya, BMI", "asha@acme-retail.in", "Asha Rao", domain="mail.example.in")
    assert ics.endswith("\r\n") and "\n" not in ics.replace("\r\n", "")
    lines = ics.split("\r\n")
    assert all(len(line.encode()) <= 75 for line in lines)
    unfolded = ics.replace("\r\n ", "")
    for expected in ("BEGIN:VCALENDAR", "METHOD:REQUEST", "BEGIN:VEVENT", "UID:m1abc@mail.example.in",
                     "DTSTART:20261105T043000Z", "DTEND:20261105T050000Z", "SEQUENCE:0", "STATUS:CONFIRMED",
                     "SUMMARY:BookMyInterview.ai demo — Acme\\, Retail\\; India",
                     "DESCRIPTION:Join: https://zoom.us/j/987654321\\nBring the JD\\n",
                     'ORGANIZER;CN="Priya, BMI":mailto:priya@mail.example.in',
                     'ATTENDEE;CN="Asha Rao";ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:asha@acme-retail.in',
                     "URL:https://zoom.us/j/987654321", "END:VEVENT", "END:VCALENDAR"):
        assert expected in unfolded, expected
    cancel = build_ics(MEETING, "priya@mail.example.in", "Priya", "asha@acme-retail.in", method="CANCEL", sequence=1,
                       domain="mail.example.in").replace("\r\n ", "")
    assert "METHOD:CANCEL" in cancel and "STATUS:CANCELLED" in cancel and "SEQUENCE:1" in cancel and "UID:m1abc@mail.example.in" in cancel


def test_attachments_for_each_api_provider():
    files = invite_attachments({"ics": "BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n", "ics_method": "REQUEST"})
    assert files[0]["content_type"] == "text/calendar; method=REQUEST; charset=UTF-8" and files[0]["filename"] == "invite.ics"
    values = {"api_key": "k", "server_token": "t", "from_email": "p@mail.example.in", "from_name": "Priya"}
    _, _, resend = http_request_for("resend", values, "a@b.in", "S", "T", {}, files)
    _, _, sendgrid = http_request_for("sendgrid", values, "a@b.in", "S", "T", {}, files)
    _, _, postmark = http_request_for("postmark", values, "a@b.in", "S", "T", {}, files)
    assert base64.b64decode(resend["attachments"][0]["content"]).startswith(b"BEGIN:VCALENDAR")
    assert sendgrid["attachments"][0]["type"].startswith("text/calendar") and sendgrid["attachments"][0]["disposition"] == "attachment"
    assert postmark["Attachments"][0]["Name"] == "invite.ics" and postmark["Attachments"][0]["ContentType"].startswith("text/calendar")
    assert "attachments" not in http_request_for("resend", values, "a@b.in", "S", "T", {})[2]


# ---------- booking sends the invite ----------
def test_zoom_meeting_invite_is_kept_as_practice_when_real_sending_is_off(tmp_path):
    http = FakeHTTP(ZOOM_HTTP)
    c = zoom_client(tmp_path, http)
    lead = add_lead(c)
    r = c.post(f"/api/leads/{lead['id']}/meetings", json={"start": future(), "duration_minutes": 30}).json()
    assert r["meeting"]["invite"] == "dry_run" and r["invite"] == {"status": "dry_run", "to": "asha@acme-retail.in"}
    row = outbox(c, "meeting_invite")[0]
    assert row["status"] == "dry_run" and row["meeting_id"] == r["meeting"]["id"] and row["to"] == "asha@acme-retail.in"
    assert "METHOD:REQUEST" in row["ics"] and "https://zoom.us/j/987654321" in row["body"]
    assert not any("resend" in call["url"] or "sendgrid" in call["url"] for call in http.calls)
    assert any("Calendar invite emailed" in e["text"] for e in c.get(f"/api/leads/{lead['id']}").json()["timeline"])


def test_live_invite_over_smtp_carries_a_calendar_part_and_cancel_withdraws_it(tmp_path):
    smtp = SMTPFactory()
    c = zoom_client(tmp_path, FakeHTTP({**ZOOM_HTTP, "/meetings/987654321": {"_status": 204}}), smtp_factory=smtp)
    store = c.app.state.store
    config.save_provider(store, "email_send", "smtp", {"from_name": "Priya", "from_email": "priya@mail.example.in", "smtp_host": "smtp.example.in",
                                                       "smtp_port": "587", "smtp_username": "priya", "smtp_password": "pw"},
                         resolver=lambda h: ["93.184.216.34"])
    config.set_active(store, "email_send", "smtp")
    store.update("settings", lambda s: {**s, "live_sending": True})
    lead = add_lead(c)
    meeting = c.post(f"/api/leads/{lead['id']}/meetings", json={"start": future()}).json()["meeting"]
    assert meeting["invite"] == "sent"
    msg = smtp.clients[0].sent[0]
    parts = [p.get_content_type() for p in msg.walk()]
    assert msg.get_content_type() == "multipart/mixed" and parts.count("text/calendar") == 2 and "text/plain" in parts
    calendar = next(p for p in msg.walk() if p.get_content_type() == "text/calendar")
    assert calendar.get_param("method") == "REQUEST" and "ORGANIZER;CN=" in calendar.get_content()
    assert "mailto:priya@mail.example.in" in calendar.get_content().replace("\r\n ", "")
    out = c.post(f"/api/meetings/{meeting['id']}/cancel").json()
    assert "cancellation (sent)" in out["message"]
    cancel = smtp.clients[-1].sent[0]
    body = next(p for p in cancel.walk() if p.get_content_type() == "text/calendar").get_content().replace("\r\n ", "")
    assert "METHOD:CANCEL" in body and f"UID:{meeting['id']}@mail.example.in" in body
    assert cancel["Subject"].startswith("Cancelled:")


def test_resend_classic_sender_attaches_the_invite(tmp_path):
    email = FakeHTTP({"api.resend.com": {"id": "em_1"}})
    c = zoom_client(tmp_path, email_transport=email)
    c.put("/api/integrations/sending_domain", json={"mode": "api", "values": {
        "domain": "mail.example.in", "from_name": "Priya", "from_email": "hello@mail.example.in", "api_key": "re_secret"}})
    c.app.state.store.update("settings", lambda s: {**s, "live_sending": True})
    lead = add_lead(c)
    c.post(f"/api/leads/{lead['id']}/meetings", json={"start": future()})
    sent = email.last("api.resend.com")["body"]
    assert sent["attachments"][0]["filename"] == "invite.ics"
    assert b"METHOD:REQUEST" in base64.b64decode(sent["attachments"][0]["content"])
    assert outbox(c, "meeting_invite")[0]["status"] == "sent"


def test_google_sends_its_own_invite_unless_the_owner_ticks_ours(tmp_path):
    import time
    http = FakeHTTP({"/events?conferenceDataVersion=1": {"id": "evt1", "hangoutLink": "https://meet.google.com/x"},
                     "/events/evt1": {"_status": 204}})
    c = make_client(tmp_path, http)
    c.put("/api/channels/calendar/google", json={"values": {"client_id": "cid", "client_secret": "gs"}})
    config.set_tokens(c.app.state.store, "calendar", "google", {"access_token": "a", "refresh_token": "r", "expires_at": int(time.time()) + 3000})
    lead = add_lead(c)
    plain = c.post(f"/api/leads/{lead['id']}/meetings", json={"start": future()}).json()
    assert plain["invite"] is None and outbox(c) == []
    c.post(f"/api/meetings/{plain['meeting']['id']}/cancel")
    assert outbox(c) == []                                          # Google withdraws its own invite
    ticked = c.post(f"/api/leads/{lead['id']}/meetings", json={"start": future(3), "send_invite": True}).json()
    assert ticked["meeting"]["invite"] == "dry_run" and len(outbox(c, "meeting_invite")) == 1


def test_booking_link_with_a_fixed_time_becomes_a_meeting_with_our_invite(tmp_path):
    c = make_client(tmp_path)
    c.put("/api/channels/calendar/booking_link", json={"values": {"booking_url": "https://meet.example.in/priya-room"}})
    lead = add_lead(c)
    r = c.post(f"/api/leads/{lead['id']}/meetings", json={"start": future(), "title": "Intro call"}).json()
    assert r["kind"] == "event" and r["meeting"]["join_url"] == "https://meet.example.in/priya-room"
    assert "https://meet.example.in/priya-room" in outbox(c, "meeting_invite")[0]["ics"].replace("\r\n ", "")
    assert c.post(f"/api/leads/{lead['id']}/meetings", json={}).json()["kind"] == "link"     # without a time: the old link


def test_no_invite_to_an_unsubscribed_or_erased_contact(tmp_path):
    c = zoom_client(tmp_path)
    store = c.app.state.store
    lead = add_lead(c)
    store.update("suppressed", lambda rows: [*rows, "asha@acme-retail.in"])
    r = c.post(f"/api/leads/{lead['id']}/meetings", json={"start": future()}).json()
    assert r["invite"]["status"] == "skipped" and outbox(c) == []


# ---------- reminders and no-shows ----------
class Sender:
    def __init__(self):
        self.sent = []

    def __call__(self, message):
        self.sent.append(message)
        return {"status": "sent"}


def test_reminders_use_the_join_link_and_skip_cancelled_meetings(tmp_path):
    c = zoom_client(tmp_path)
    store = c.app.state.store
    lead = add_lead(c)
    start = datetime.now(timezone.utc) + timedelta(minutes=50)
    store.update_lead(lead["id"], lambda l: {**l, "meetings": [
        {"id": "a", "title": "Demo", "start": start.isoformat(), "status": "scheduled", "join_url": "https://zoom.us/j/1"},
        {"id": "b", "title": "Old", "start": start.isoformat(), "status": "cancelled", "join_url": "https://zoom.us/j/2"}]})
    store.update("settings", lambda s: {**s, "live_sending": True})
    sender = Sender()
    run_reminders(store, datetime.now(timezone.utc), sender)
    assert len(sender.sent) == 1 and "Join: https://zoom.us/j/1" in sender.sent[0]["body"]
    assert "zoom.us/j/2" not in str(sender.sent) + str(store.read("notifications"))


def test_no_show_follow_up_task_once_thirty_minutes_after_the_end(tmp_path):
    c = zoom_client(tmp_path)
    store = c.app.state.store
    lead = add_lead(c)
    now = datetime.now(timezone.utc)
    store.update_lead(lead["id"], lambda l: {**l, "meetings": [
        {"id": "late", "title": "Demo", "start": (now - timedelta(minutes=70)).isoformat(), "end": (now - timedelta(minutes=40)).isoformat(),
         "status": "scheduled"},
        {"id": "recent", "title": "Demo 2", "start": (now - timedelta(minutes=40)).isoformat(), "end": (now - timedelta(minutes=10)).isoformat(),
         "status": "scheduled"},
        {"id": "held", "title": "Demo 3", "start": (now - timedelta(hours=3)).isoformat(), "end": (now - timedelta(hours=2)).isoformat(),
         "status": "done"},
        {"id": "ancient", "title": "Demo 4", "start": (now - timedelta(days=30)).isoformat(), "status": "scheduled"}]})
    assert run_reminders(store, now)["no_show_tasks"] == 1
    tasks = [t for t in store.read("tasks") if t["kind"] == "meeting_follow_up"]
    assert len(tasks) == 1 and tasks[0]["title"] == "Mark the meeting done or follow up" and tasks[0]["meeting_id"] == "late"
    assert run_reminders(store, now + timedelta(minutes=5))["no_show_tasks"] == 0                 # only once
    assert run_reminders(store, now + timedelta(minutes=25))["no_show_tasks"] == 1                # "recent" is now due
    done = c.post("/api/meetings/late/done").json()
    assert done["status"] == "done"
    closed = next(t for t in store.read("tasks") if t.get("meeting_id") == "late")
    assert closed["status"] == "done" and closed["done_note"] == "Meeting marked done"
    assert c.post("/api/meetings/nope/done").status_code == 404
