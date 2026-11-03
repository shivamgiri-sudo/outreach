"""QA: Cal.com and Calendly booking webhooks attach meetings to the right lead (incl. other_contacts).

Payloads: Cal.com BOOKING_CREATED / BOOKING_RESCHEDULED / BOOKING_CANCELLED (docs: cal.com/docs/developing/guides/
automation/webhooks, signed with X-Cal-Signature-256 = hex HMAC-SHA256 of the raw body) and Calendly invitee.created /
invitee.canceled (developer.calendly.com webhook samples, Calendly-Webhook-Signature "t=<unix>,v1=<hex HMAC of
'<t>.<body>'>"). 60 imported companies with 32 extra people; all fictional.
"""
import copy
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta, timezone

from app.reminders import run_reminders
from app.web_security import RateLimiter
from tests.channel_fakes import FakeHTTP
from tests.qa.calendar_meetings_fakes import (client, hook_secret, import_leads, lead_by_email, load, meetings_of, post_calcom,
                                             post_calendly, replies, save_calcom)

POOJA = "pooja.rao@sagar-fintech-01.example"
SNEHA = "sneha.kaur@kesar-foods-06.example"           # primary contact; Manoj Pillai is the other contact
UTC = timezone.utc


def calendly_client(tmp_path):
    http = FakeHTTP({"api.calendly.com/users/me": replies()["calendly_users_me"],
                     "webhook_subscriptions": {"resource": {"uri": "https://api.calendly.com/webhook_subscriptions/WFAKE"}}})
    c = client(tmp_path, http)
    leads = import_leads(c)
    c.put("/api/channels/calendar/calendly", json={"values": {"access_token": "fake-pat"}})
    assert c.post("/api/channels/calendar/calendly/test", json={}).json()["ok"] is True
    return c, leads, http.last("webhook_subscriptions")["body"]["signing_key"]


def lead_events(c, lead_id):
    return [e["text"] for e in c.get(f"/api/leads/{lead_id}").json()["timeline"] if e["kind"] == "meeting"]


# ---------- Cal.com ----------
def test_calcom_booking_created_lands_on_the_lead_without_touching_consent_or_stage(tmp_path):
    c = client(tmp_path)
    leads = import_leads(c)
    save_calcom(c)
    lead = leads[POOJA]
    r = post_calcom(c, load("calcom_booking_created.json"))
    assert r.status_code == 200 and r.json() == {"ok": True, "bookings": 1, "matched": 1}
    saved = c.get(f"/api/leads/{lead['id']}").json()
    meeting = saved["meetings"][0]
    assert meeting["provider"] == "calcom" and meeting["external_id"] == "bk-7Hq2Ld9xZ0" and meeting["status"] == "scheduled"
    assert meeting["start"] == "2026-11-12T04:30:00+00:00" and meeting["attendee_email"] == POOJA
    assert meeting["join_url"] == "https://app.cal.com/video/bk-7Hq2Ld9xZ0"
    assert saved["timeline"][-1]["text"] == ("Meeting booked: BookMyInterview.ai demo between Priya and Pooja Rao, "
                                             "Thu 12 Nov 2026, 10:00 AM IST")
    assert saved["consent"] == lead["consent"] and saved["stage"] == lead["stage"]
    assert all(not l.get("meetings") for l in c.get("/api/leads").json() if l["id"] != lead["id"])


def test_calcom_end_time_is_stored_in_a_form_the_server_can_read(tmp_path):
    """Cal.com sends endTime as '...Z'; Python 3.10's fromisoformat can't read 'Z', so the end must be normalised."""
    c = client(tmp_path)
    import_leads(c)
    save_calcom(c)
    post_calcom(c, load("calcom_booking_created.json"))
    lead = lead_by_email(c, POOJA)
    meeting = meetings_of(c, lead["id"])[0]
    assert datetime.fromisoformat(meeting["end"]) == datetime(2026, 11, 12, 5, 30, tzinfo=UTC), meeting["end"]
    store = c.app.state.store
    end = datetime(2026, 11, 12, 5, 30, tzinfo=UTC)                         # a 60-minute booking
    assert run_reminders(store, end)["no_show_tasks"] == 0                   # it just ended: too early
    assert run_reminders(store, end + timedelta(minutes=29))["no_show_tasks"] == 0
    assert run_reminders(store, end + timedelta(minutes=30))["no_show_tasks"] == 1


def test_calcom_reschedule_moves_the_meeting_and_the_old_slot_gets_no_reminders(tmp_path):
    c = client(tmp_path)
    import_leads(c)
    save_calcom(c)
    post_calcom(c, load("calcom_booking_created.json"))
    assert post_calcom(c, load("calcom_booking_rescheduled.json")).json()["matched"] == 1
    lead = lead_by_email(c, POOJA)
    meetings = meetings_of(c, lead["id"])
    live = [m for m in meetings if m["status"] == "scheduled"]
    assert len(live) == 1 and live[0]["external_id"] == "bk-Rs5Tt1Wq8M", meetings
    assert live[0]["start"] == "2026-11-13T09:30:00+00:00" and live[0]["join_url"].endswith("/bk-Rs5Tt1Wq8M")
    old = next((m for m in meetings if m["external_id"] == "bk-7Hq2Ld9xZ0"), None)
    assert old is None or old["status"] == "cancelled"
    store = c.app.state.store
    old_start = datetime(2026, 11, 12, 4, 30, tzinfo=UTC)
    store.update("settings", lambda s: {**s, "live_sending": True})
    for when in (old_start - timedelta(hours=24), old_start - timedelta(hours=1), old_start - timedelta(minutes=15)):
        assert run_reminders(store, when)["meeting_reminders"] == 0, when
    assert run_reminders(store, old_start + timedelta(hours=2))["no_show_tasks"] == 0
    # the lead then cancels the new slot: nothing is reminded any more
    assert post_calcom(c, load("calcom_booking_cancelled.json")).json()["matched"] == 1
    assert all(m["status"] == "cancelled" for m in meetings_of(c, lead["id"]))
    new_start = datetime(2026, 11, 13, 9, 30, tzinfo=UTC)
    assert run_reminders(store, new_start - timedelta(minutes=10))["meeting_reminders"] == 0
    assert lead_events(c, lead["id"])[-1] == "Meeting cancelled by the lead (calcom)"


def test_redelivered_booking_after_cancel_does_not_reopen_the_meeting(tmp_path):
    """Webhooks are retried and can arrive out of order: a late BOOKING_CREATED must not undo the cancellation."""
    c = client(tmp_path)
    import_leads(c)
    save_calcom(c)
    created = load("calcom_booking_created.json")
    post_calcom(c, created)
    cancel = copy.deepcopy(load("calcom_booking_cancelled.json"))
    cancel["payload"]["uid"] = created["payload"]["uid"]
    post_calcom(c, cancel)
    post_calcom(c, created)                                                  # the retry of the first delivery
    lead = lead_by_email(c, POOJA)
    meetings = meetings_of(c, lead["id"])
    assert [m["status"] for m in meetings] == ["cancelled"], meetings
    start = datetime(2026, 11, 12, 4, 30, tzinfo=UTC)
    assert run_reminders(c.app.state.store, start - timedelta(hours=1))["meeting_reminders"] == 0


def test_calcom_signature_and_token_rules(tmp_path):
    c = client(tmp_path)
    import_leads(c)
    body = json.dumps(load("calcom_booking_created.json")).encode()
    assert post_calcom(c, body, secret="whatever").status_code == 401        # provider not saved yet
    save_calcom(c)
    secret = hook_secret(c, "calcom")
    assert post_calcom(c, body, secret="someone-elses-secret").status_code == 401
    assert post_calcom(c, body, signature="").status_code == 401             # no signature and no token
    signed_for_other_body = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert post_calcom(c, body + b" ", signature=signed_for_other_body).status_code == 401   # body changed after signing
    by_token = c.post(f"/api/webhooks/calendar/calcom?token={secret}", content=body, headers={"Content-Type": "application/json"})
    assert by_token.status_code == 200 and by_token.json()["matched"] == 1  # Cal.com without a signing secret
    assert c.post("/api/webhooks/calendar/calcom", content=b"not json", headers={"Content-Type": "application/json",
                  "X-Webhook-Secret": secret}).status_code == 400


def test_calcom_volume_matching_messy_attendee_emails(tmp_path, monkeypatch):
    """A month of bookings: primary and other-contact emails in odd cases and spaces, strangers and odd triggers.

    A month replayed in one burst would hit the 120/min webhook rate limit (by design); lift it for the replay."""
    monkeypatch.setattr(RateLimiter, "allow", lambda self, key: True)
    c = client(tmp_path)
    import_leads(c)
    save_calcom(c)
    people = load("people_index.json")
    template = load("calcom_booking_created.json")
    expected, uid = {}, 0
    for i, person in enumerate(people):
        uid += 1
        email = person["email"]
        shown = [email, email.upper(), f"  {email}  ", email.title()][i % 4]
        payload = copy.deepcopy(template)
        payload["payload"].update({"uid": f"bk-vol-{uid:03d}", "bookingId": 50000 + uid,
                                   "startTime": f"2026-11-{16 + i % 10:02d}T{3 + i % 9:02d}:30:00Z",
                                   "endTime": f"2026-11-{16 + i % 10:02d}T{4 + i % 9:02d}:00:00Z",
                                   "attendees": [{"email": shown, "name": person["name"], "timeZone": "Asia/Kolkata"}]})
        assert post_calcom(c, payload).json()["matched"] == 1, shown
        expected[person["domain"]] = expected.get(person["domain"], 0) + 1
    for n in range(25):                                                       # people we never contacted
        stranger = copy.deepcopy(template)
        stranger["payload"].update({"uid": f"bk-x-{n}", "attendees": [{"email": f"walkin{n}@unknown-{n}.example", "name": "X"}]})
        assert post_calcom(c, stranger).json() == {"ok": True, "bookings": 1, "matched": 0}
    for trigger in ("BOOKING_REQUESTED", "MEETING_ENDED", "BOOKING_PAYMENT_INITIATED", "FORM_SUBMITTED"):
        odd = {**template, "triggerEvent": trigger}
        assert post_calcom(c, odd).json() == {"ok": True, "bookings": 0, "matched": 0}
    for payload in ([1, 2], {"triggerEvent": "BOOKING_CREATED"}, {"triggerEvent": "BOOKING_CREATED", "payload": {"attendees": "x"}}):
        assert post_calcom(c, payload).status_code == 200
    got = {l["domain"]: len(l.get("meetings") or []) for l in c.get("/api/leads").json()}
    assert {d: n for d, n in got.items() if n} == expected
    assert sum(expected.values()) == len(people) == 92
    for lead in c.get("/api/leads").json():                                   # nobody's consent or stage moved
        assert lead["stage"] == "new" and not lead.get("consent")


def test_booking_without_a_start_time_is_kept_but_never_reminded(tmp_path):
    c = client(tmp_path)
    import_leads(c)
    save_calcom(c)
    payload = copy.deepcopy(load("calcom_booking_created.json"))
    del payload["payload"]["startTime"], payload["payload"]["endTime"]
    assert post_calcom(c, payload).json()["matched"] == 1
    lead = lead_by_email(c, POOJA)
    assert meetings_of(c, lead["id"])[0]["start"] == "" and lead_events(c, lead["id"])[-1].endswith("time not given")
    assert run_reminders(c.app.state.store, datetime.now(UTC)) == {"task_reminders": 0, "meeting_reminders": 0, "no_show_tasks": 0}


# ---------- Calendly ----------
def test_calendly_booking_by_an_other_contact_across_ist_midnight(tmp_path):
    c, leads, key = calendly_client(tmp_path)
    sneha = leads[SNEHA]
    r = post_calendly(c, load("calendly_invitee_created.json"), key)
    assert r.status_code == 200 and r.json()["matched"] == 1
    meeting = meetings_of(c, sneha["id"])[0]
    assert meeting["external_id"] == "EFAKEEVENT000001" and meeting["join_url"] == "https://meet.google.com/fak-ecal-ndly"
    assert meeting["attendee_email"] == "manoj.pillai@kesar-foods-06.example"
    assert meeting["start"] == "2026-11-12T18:45:00+00:00"
    # 18:45 UTC on Thursday is 00:15 IST on Friday
    assert lead_events(c, sneha["id"])[-1] == "Meeting booked: BookMyInterview.ai demo (60 min), Fri 13 Nov 2026, 12:15 AM IST"


def test_calendly_reschedule_pair_cancels_the_old_event_and_adds_the_new_one(tmp_path):
    c, leads, key = calendly_client(tmp_path)
    sneha = leads[SNEHA]
    post_calendly(c, load("calendly_invitee_created.json"), key)
    post_calendly(c, load("calendly_invitee_canceled_rescheduled.json"), key)
    post_calendly(c, load("calendly_invitee_created_rescheduled.json"), key)
    meetings = {m["external_id"]: m for m in meetings_of(c, sneha["id"])}
    assert meetings["EFAKEEVENT000001"]["status"] == "cancelled"
    assert meetings["EFAKEEVENT000002"]["status"] == "scheduled"
    assert meetings["EFAKEEVENT000002"]["join_url"].startswith("https://us05web.zoom.us/j/")
    store = c.app.state.store
    old = datetime(2026, 11, 12, 18, 45, tzinfo=UTC)
    assert run_reminders(store, old - timedelta(hours=1))["meeting_reminders"] == 0


def test_calendly_signature_tolerance_and_tampering(tmp_path):
    c, leads, key = calendly_client(tmp_path)
    body = json.dumps(load("calendly_invitee_created.json")).encode()
    now = int(time.time())
    assert post_calendly(c, body, key, stamp=now - 301).status_code == 401  # outside the 5-minute window
    assert post_calendly(c, body, key, stamp=now + 400).status_code == 401
    assert post_calendly(c, body, "not-the-signing-key").status_code == 401
    bad = c.post("/api/webhooks/calendar/calendly", content=body,
                 headers={"Content-Type": "application/json", "Calendly-Webhook-Signature": "garbage"})
    assert bad.status_code == 401
    assert post_calendly(c, body, key, stamp=now - 120).status_code == 200
    assert meetings_of(c, leads[SNEHA]["id"])[0]["status"] == "scheduled"


def test_unsubscribed_lead_booking_is_recorded_but_only_the_owner_is_reminded(tmp_path):
    c = client(tmp_path)
    leads = import_leads(c)
    save_calcom(c)
    lead = leads[POOJA]
    c.post(f"/api/leads/{lead['id']}/stage", json={"stage": "unsubscribed"})
    assert post_calcom(c, load("calcom_booking_created.json")).json()["matched"] == 1
    saved = c.get(f"/api/leads/{lead['id']}").json()
    assert saved["stage"] == "unsubscribed" and len(saved["meetings"]) == 1
    store = c.app.state.store
    store.update("settings", lambda s: {**s, "live_sending": True})
    start = datetime(2026, 11, 12, 4, 30, tzinfo=UTC)
    sent = []
    run_reminders(store, start - timedelta(hours=1), lambda m: sent.append(m) or {"status": "sent"})
    run_reminders(store, start - timedelta(minutes=15), lambda m: sent.append(m) or {"status": "sent"})
    assert sent == [] and store.read("transactional_outbox") == []
    assert [n["kind"] for n in store.read("notifications")] == ["meeting_soon"]
