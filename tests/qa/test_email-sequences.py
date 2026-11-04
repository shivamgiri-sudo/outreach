"""QA: the 6-step cold email sequence end to end, over 30 days of time travel, for ~60 imported leads.

Every provider is a fake (no network): Resend through the classic sending-domain card, and the Integrations-hub
providers SendGrid, Postmark and SMTP. See docs/qa/email-sequences.md for the scenarios and the formats they copy.
"""
import smtplib
import socket
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import sequences
from app.channels import config
from app.http_util import HttpStatusError
from app.inbound import apply_reply, process_delivery_event
from app.main import create_app
from app.senders import make_email_sender
from app.sequence_runner import clear_practice, effective_daily_cap, run_due
from app.sequences import IST, in_business_hours
from app.unsubscribe import unsub_path
from tests.channel_fakes import FakeSMTP

FIXTURE = Path(__file__).parent / "fixtures" / "email-sequences" / "leads_wizard_60.csv"
BASE = "https://outreach.example.in"
START = date(2026, 11, 3)                                 # Tuesday; conftest enrols at 09:30 IST that day
SENDER = {"from_name": "Priya from BookMyInterview", "from_email": "priya@mail.example.in"}
PROVIDERS = {
    "resend": {**SENDER, "domain": "mail.example.in", "api_key": "re_test_key"},
    "sendgrid": {**SENDER, "api_key": "SG.test-key"},
    "postmark": {**SENDER, "server_token": "pm-test-token"},
    "smtp": {**SENDER, "smtp_host": "smtp.example.in", "smtp_port": "587", "smtp_security": "starttls",
             "smtp_username": "priya", "smtp_password": "app-pass"},
}
IDENTITY = {"sender_company": "BookMyInterview", "sender_website": "https://bookmyinterview.example",
            "sender_contact_email": "hello@bookmyinterview.example", "public_base_url": BASE}
US_CA = {"sophie.tremblay@mapleleafstaffing.invalid", "jacob.miller@lonestarlogistics.invalid",
         "olivia.brooks@hudsoncare.invalid"}
# Check-in times through each day (IST): before 9, the 9:00 edge, the working day, 17:59 and the 18:00 edge, evening.
SLOTS = [(8, 45), (9, 0), (9, 40), (10, 40), (11, 40), (12, 40), (13, 40), (14, 40), (15, 40), (16, 40), (17, 40),
         (17, 59), (18, 0), (21, 0)]
STEP_DAY = {s["name"]: s["day"] for s in sequences.DEFAULT_SEQUENCE}


def at_ist(day_offset: int, hour: int, minute: int = 0) -> datetime:
    d = START + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=IST)


# ---- fake providers ----
def ok_reply(pid: str, n: int) -> dict:
    return {"resend": {"id": f"re-{n}"}, "sendgrid": {"_status": 202, "_headers": {"x-message-id": f"sg-{n}"}},
            "postmark": {"ErrorCode": 0, "Message": "OK", "MessageID": f"pm-{n}"}}[pid]


class Wire:
    """Fake HTTP provider (Resend / SendGrid / Postmark): records requests; `script` holds outcomes for the next calls."""

    def __init__(self, pid: str):
        self.pid, self.calls, self.script = pid, [], []

    def __call__(self, url, headers=None, body=None, method="POST", **_):
        self.calls.append({"url": url, "headers": dict(headers or {}), "body": body})
        outcome = self.script.pop(0) if self.script else None
        if isinstance(outcome, Exception):
            raise outcome
        return outcome if outcome is not None else ok_reply(self.pid, len(self.calls))


class ScriptedSMTP(FakeSMTP):
    def __init__(self, factory, *args):
        super().__init__(*args)
        self.factory = factory

    def send_message(self, msg):
        self.factory.attempts.append(msg)
        outcome = self.factory.script.pop(0) if self.factory.script else None
        if isinstance(outcome, Exception):
            raise outcome
        self.sent.append(msg)


class SMTPServer:
    def __init__(self):
        self.script, self.attempts, self.fail_login = [], [], False

    def __call__(self, host, port, use_ssl, timeout):
        return ScriptedSMTP(self, host, port, use_ssl, timeout, self.fail_login)


def attempts(rig) -> list:
    """Every message handed to the provider, normalised: {to, subject, text, headers, idem}."""
    out = []
    if rig.pid == "smtp":
        for msg in rig.smtp.attempts:
            out.append({"to": msg["To"], "subject": msg["Subject"], "text": msg.get_content(),
                        "headers": {k: str(v) for k, v in msg.items()}, "idem": None})
        return out
    for call in rig.wire.calls:
        b, h = call["body"], call["headers"]
        if rig.pid == "resend":
            out.append({"to": b["to"][0], "subject": b["subject"], "text": b["text"], "headers": b["headers"],
                        "idem": h.get("Idempotency-Key")})
        elif rig.pid == "sendgrid":
            out.append({"to": b["personalizations"][0]["to"][0]["email"], "subject": b["subject"],
                        "text": b["content"][0]["value"], "headers": b.get("headers", {}), "idem": None})
        else:
            out.append({"to": b["To"], "subject": b["Subject"], "text": b["TextBody"],
                        "headers": {x["Name"]: x["Value"] for x in b["Headers"]}, "idem": None})
    return out


class Rig:
    """The app with the fixture imported through the import wizard and every lead with an email enrolled."""

    def __init__(self, tmp_path, pid="resend", live=False, **settings):
        self.pid, self.path = pid, tmp_path / "d.json"
        app = create_app(data_file=self.path)
        self.client, self.store = TestClient(app), app.state.store      # one shared store, as in production
        csv_text = FIXTURE.read_text(encoding="utf-8")                  # keeps the Excel BOM on purpose
        r = self.client.post("/api/import/commit", json={"csv": csv_text, "source": "qa-email-sequences"})
        assert r.status_code == 200, r.text
        self.leads = {l["id"]: l for l in self.store.read("leads")}
        for lead_id, lead in self.leads.items():
            if lead.get("email"):
                assert self.client.post(f"/api/leads/{lead_id}/enroll").status_code == 200
        self.by_email = {l["email"]: l["id"] for l in self.leads.values() if l.get("email")}
        config.save_provider(self.store, "email_send", pid, PROVIDERS[pid], resolver=lambda h: ["93.184.216.34"])
        self.set(**{**IDENTITY, "live_sending": live, **settings})
        self.wire, self.smtp = Wire(pid), SMTPServer()
        self.sender = make_email_sender(self.store, transport=self.wire, base_url=BASE, channel_transport=self.wire,
                                        smtp_factory=self.smtp)
        self.results = []

    def set(self, **changes):
        self.store.update("settings", lambda s: {**s, **changes})

    def run(self, now):
        result = run_due(self.store, now, BASE, self.sender)
        self.results.append((now, result))
        return result

    def outbox(self, status=None):
        return [m for m in self.store.read("outbox") if status is None or m["status"] == status]

    def lead(self, lead_id):
        return next(l for l in self.store.read("leads") if l["id"] == lead_id)

    def simulate(self, first_day, last_day, on_day=None):
        for d in range(first_day, last_day + 1):
            if on_day:
                on_day(d)
            for hour, minute in SLOTS:
                self.run(at_ist(d, hour, minute))


def check_schedule(rows, daily_limit, per_run=5):
    """Business hours, per-run and daily caps, one email per company domain a day, step order and spacing."""
    per_day, per_stamp, per_domain = Counter(), Counter(), Counter()
    per_lead = defaultdict(list)
    for m in rows:
        at = datetime.fromisoformat(m["sent_at"]).astimezone(IST)
        assert at.weekday() < 5 and 9 <= at.hour < 18, m["sent_at"]
        assert m["sent_at_ist_date"] == at.date().isoformat()
        per_day[at.date()] += 1
        per_stamp[m["sent_at"]] += 1
        per_domain[(at.date(), m["to"].rpartition("@")[2])] += 1
        per_lead[m["lead_id"]].append((at, STEP_DAY[m["step"]]))
    assert max(per_stamp.values()) <= per_run
    assert max(per_domain.values()) == 1
    for day, count in per_day.items():
        assert count <= daily_limit(day), (day, count)
    for sends in per_lead.values():
        days = [d for _, d in sends]
        assert days == [0, 3, 7, 11, 15, 20][:len(days)]                    # in order, never twice, never skipped
        for (a, da), (b, db) in zip(sends, sends[1:]):
            assert b - a >= timedelta(days=db - da)                   # spaced from the email actually sent
    return per_day, per_lead


# ---- business hours ----
@pytest.mark.parametrize("when,inside", [
    (datetime(2026, 11, 6, 17, 59, tzinfo=IST), True),     # Friday 17:59
    (datetime(2026, 11, 6, 18, 0, tzinfo=IST), False),     # Friday 18:00 is closed
    (datetime(2026, 11, 7, 11, 0, tzinfo=IST), False),     # Saturday
    (datetime(2026, 11, 8, 11, 0, tzinfo=IST), False),     # Sunday
    (datetime(2026, 11, 9, 8, 59, tzinfo=IST), False),
    (datetime(2026, 11, 9, 9, 0, tzinfo=IST), True),       # Monday 09:00 opens
    (datetime(2026, 11, 9, 3, 30, tzinfo=timezone.utc), True),   # UTC input is read in IST (09:00)
    (datetime(2026, 11, 6, 12, 30, tzinfo=timezone.utc), False),  # 18:00 IST on a Friday, given in UTC
])
def test_business_hours_ist_edges(when, inside):
    assert in_business_hours(when) is inside


def test_runs_outside_hours_send_nothing(tmp_path):
    rig = Rig(tmp_path)
    assert "outside business hours" in rig.run(at_ist(4, 9, 0) - timedelta(minutes=1))["skipped"]
    assert "outside business hours" in rig.run(at_ist(4, 18, 0))["skipped"]
    assert rig.outbox() == []


# ---- practice: six weeks, all leads, no provider calls ----
def test_practice_six_weeks_runs_the_whole_sequence_without_sending(tmp_path):
    rig = Rig(tmp_path, "sendgrid")
    rig.simulate(0, 42)
    rows = rig.outbox()
    assert rig.wire.calls == [] and {m["status"] for m in rows} == {"dry_run"}
    per_day, per_lead = check_schedule(rows, lambda day: 20)
    enrolled = {l["id"] for l in rig.store.read("leads") if l.get("sequence")}
    assert len(enrolled) == 60                                # 63 rows, 3 duplicates folded in
    assert {lid: len(s) for lid, s in per_lead.items()} == {lid: 6 for lid in enrolled}   # practice ignores US/CA
    assert max(per_day.values()) == 20                        # the daily cap was reached early on
    for lead in rig.store.read("leads"):
        seq = lead.get("sequence")
        if seq:
            assert seq["sent_days"] == [] and seq["practice_days"] == [0, 3, 7, 11, 15, 20]
            assert lead["stage"] == "new"                     # practice never moves a lead to contacted


# ---- live: practice first, then live for four weeks with replies, unsubscribes, bounces and a complaint ----
@pytest.mark.parametrize("pid", ["resend", "sendgrid", "postmark", "smtp"])
def test_live_month_with_stop_events(tmp_path, pid):
    rig = Rig(tmp_path, pid, daily_cap=50)
    rig.simulate(0, 0)                                        # a day of practice (10 runs of 5)
    practised = {m["lead_id"] for m in rig.outbox("dry_run")}
    assert len(practised) == 50 and attempts(rig) == []
    rig.set(live_sending=True)
    events = {}

    def on_day(d):
        if d == 6:                                            # Monday of week 2: what came back from week 1
            live_ids = [m["lead_id"] for m in rig.outbox("sent")]
            neg, pos, unsub, bounce = live_ids[0], live_ids[1], live_ids[2], live_ids[3]
            apply_reply(rig.store, neg, "Not interested, please don't contact us again.", "email")
            apply_reply(rig.store, pos, "Yes please send the sample screen, we need 40 telecallers.", "email")
            link = next(a["text"] for a in attempts(rig) if a["to"] == rig.lead(unsub)["email"]).split("Unsubscribe: ")[1].split()[0]
            token = link.rsplit("/u/", 1)[1]
            assert rig.client.get(f"/u/{token}").status_code == 200
            assert rig.lead(unsub)["stage"] != "unsubscribed"          # opening the page alone changes nothing
            assert rig.client.post(f"/u/{token}").status_code == 200
            out = process_delivery_event(rig.store, {"type": "bounce", "email": rig.lead(bounce)["email"],
                                                     "provider_id": "b-1"}, at_ist(d, 8, 0))
            assert out["paused"] is None                       # under 50 sends in the window: no rate pause yet
            waiting = next(l for l in rig.store.read("leads") if l.get("sequence") and not l["sequence"]["sent_days"]
                           and l["email"] not in US_CA)
            rig.store.update("suppressed", lambda rows: [*rows, waiting["email"]])
            events.update(neg=neg, pos=pos, unsub=unsub, bounce=bounce, suppressed=waiting["id"], at=at_ist(d, 8, 0))
        if d == 9:                                            # a spam complaint once volume is meaningful
            target = next(m["lead_id"] for m in reversed(rig.outbox("sent")) if m["lead_id"] not in events.values())
            out = process_delivery_event(rig.store, {"type": "complaint", "email": rig.lead(target)["email"],
                                                     "provider_id": "c-1"}, at_ist(d, 8, 0))
            assert out["paused"]                              # one complaint in 50+ sends is above 0.3%
            events.update(complaint=target, complaint_at=at_ist(d, 8, 0))
            assert "paused" in rig.run(at_ist(d, 10, 0))["skipped"]
        if d == 10:
            assert rig.client.post("/api/sending/resume").status_code == 200

    rig.simulate(2, 29, on_day)
    live = rig.outbox("sent")
    first_live = START + timedelta(days=2)

    def limit(day):
        return effective_daily_cap(50, True, first_live, day)
    per_day, per_lead = check_schedule(live, limit)
    assert per_day[first_live] == 10 and per_day[START + timedelta(days=13)] == 20   # warm-up: 10 in week 1, 20 in week 2
    assert max(per_day.values()) <= 35
    assert not [d for d in per_day if d == START + timedelta(days=9)]         # paused the day of the complaint

    # practice never burned Email 1: every practised lead (except US/Canada) got a real Email 1
    expected = {lid for lid in practised if rig.leads[lid]["email"] not in US_CA} - {events["suppressed"]}
    assert expected <= set(per_lead)
    for lid in practised - {events["suppressed"]}:
        seq = rig.lead(lid)["sequence"]
        assert seq["practice_days"] and (seq["sent_days"][:1] == [0] or rig.leads[lid]["email"] in US_CA)

    # stop rules: nothing after the event, and the right stage / reason
    sent_after = lambda lid, t: [m for m in live if m["lead_id"] == lid and datetime.fromisoformat(m["sent_at"]) > t]
    for key in ("neg", "pos", "unsub", "bounce", "suppressed"):
        assert sent_after(events[key], events["at"]) == [], key
    assert sent_after(events["complaint"], events["complaint_at"]) == []
    assert rig.lead(events["neg"])["stage"] == "unsubscribed"         # "don't contact us" is read as a stop request
    assert rig.lead(events["pos"])["stage"] == "interested"
    assert rig.lead(events["unsub"])["stage"] == "unsubscribed"
    assert rig.lead(events["unsub"])["sequence"]["stopped"] == "unsubscribed"
    assert rig.lead(events["bounce"])["sequence"]["stopped"] == "bounced"
    assert rig.lead(events["complaint"])["stage"] == "unsubscribed"
    assert per_lead.get(events["suppressed"]) is None

    # US/Canada leads (incl. "U.S.A." spelled with dots) are held while no postal address is set
    assert not [m for m in live if m["to"] in US_CA]
    assert any(r.get("blocked") for _, r in rig.results)

    # what went over the wire: identity footer, signed unsubscribe link, headers, threading ids, idempotency
    wire = attempts(rig)
    assert len(wire) == len(live)
    for m, a in zip(live, wire):
        assert a["to"] == m["to"] and a["subject"] == m["subject"] and a["text"].rstrip() == m["body"].rstrip()
        link = f"{BASE}{unsub_path(rig.store, m['lead_id'])}"
        assert f"Unsubscribe: {link}" in a["text"]
        assert "BookMyInterview · https://bookmyinterview.example · hello@bookmyinterview.example" in a["text"]
        assert a["headers"]["List-Unsubscribe"] == f"<{link}>, <mailto:{SENDER['from_email']}?subject=unsubscribe>"
        assert a["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
        assert a["headers"]["Message-ID"] == m["message_id"] and m["message_id"].endswith("@mail.example.in>")
        assert "X-Idempotency-Key" not in a["headers"]
        assert a["idem"] == (m["idempotency_key"] if pid == "resend" else None)
    keys = [m["idempotency_key"] for m in live]
    assert len(set(keys)) == len(keys) and all(k.endswith(":live") for k in keys)
    assert len({m["message_id"] for m in live}) == len(live)
    assert all(rig.lead(m["lead_id"])["stage"] != "new" for m in live)       # a real send marks contacted (or later)


def test_us_canada_leads_go_out_once_a_postal_address_is_set(tmp_path):
    rig = Rig(tmp_path, "resend", live=True, daily_cap=50)
    rig.simulate(1, 8)
    assert not [m for m in rig.outbox("sent") if m["to"] in US_CA]
    rig.set(sender_address="BookMyInterview, 12 Example Road, Pune 411001, India")
    rig.simulate(9, 12)
    held = [m for m in rig.outbox("sent") if m["to"] in US_CA]
    assert {m["to"] for m in held} == US_CA and all(m["step"].startswith("Email 1") for m in held)
    assert all("BookMyInterview · BookMyInterview, 12 Example Road, Pune 411001, India" in m["body"] for m in held)


@pytest.mark.parametrize("country", ["U.S.A.", "U.S.", "usa", "United States", "Canada", " CA "])
def test_postal_address_rule_reads_country_spellings(country):
    assert sequences.needs_postal_address({"country": country})


@pytest.mark.parametrize("country", ["India", "IN", "", None, "Cambodia", "Australia"])
def test_postal_address_rule_leaves_other_countries(country):
    assert not sequences.needs_postal_address({"country": country})


# ---- practice → live switch and clear practice ----
def test_practice_then_live_then_clear_practice(tmp_path):
    rig = Rig(tmp_path, "postmark")
    rig.simulate(0, 3)                                        # Email 1 and 2 practised for some leads
    lid = next(l["id"] for l in rig.store.read("leads") if (l.get("sequence") or {}).get("practice_days") == [0, 3])
    rig.set(live_sending=True)
    first = rig.run(at_ist(6, 10, 0))                         # Monday
    assert first["mode"] == "live" and first["sent"] == 5
    rig.simulate(6, 8)
    real = [m for m in rig.outbox("sent") if m["lead_id"] == lid]
    assert real and real[0]["step"].startswith("Email 1")     # the real sequence starts at Email 1
    practice_rows = len(rig.outbox("dry_run"))
    assert practice_rows and clear_practice(rig.store)["outbox"] == practice_rows
    assert rig.outbox("dry_run") == []
    seq = rig.lead(lid)["sequence"]
    assert "practice_days" not in seq and seq["sent_days"][:1] == [0]
    assert rig.outbox("sent") and len(rig.outbox("sent")) == len(attempts(rig))


def test_clear_practice_counts_and_keeps_live_rows(tmp_path):
    rig = Rig(tmp_path, "resend")
    rig.simulate(0, 1)
    practice_rows = len(rig.outbox("dry_run"))
    rig.set(live_sending=True)
    rig.run(at_ist(2, 10, 0))
    live_rows = len(rig.outbox("sent"))
    assert rig.client.post("/api/practice/clear").json() == {"outbox": practice_rows}
    assert len(rig.outbox()) == live_rows == 5


def test_practice_mode_ignores_a_provider_pause_and_never_calls_the_provider(tmp_path):
    rig = Rig(tmp_path, "smtp")
    rig.set(sending_paused={"reason": "HTTP 429", "at": "2026-11-04T04:00:00+00:00", "resume_at": None})
    assert rig.run(at_ist(1, 10, 0))["sent"] == 5
    assert rig.smtp.attempts == []


# ---- provider errors: pause vs per-lead failure, resume, same idempotency key and Message-ID on retry ----
def http_error(pid, kind):
    if kind == "401":
        return PermissionError("HTTP 401")
    if kind == "429":
        return HttpStatusError(429, '{"name":"rate_limit_exceeded"}')
    if kind == "500":
        return HttpStatusError(500, "Internal Server Error")
    if kind == "timeout":
        return socket.timeout("timed out")
    return {"resend": HttpStatusError(422, '{"name":"validation_error","message":"Invalid `to` field."}'),
            "sendgrid": HttpStatusError(400, '{"errors":[{"message":"Does not contain a valid address.","field":"personalizations.0.to.0.email"}]}'),
            "postmark": HttpStatusError(422, '{"ErrorCode":300,"Message":"Invalid \'To\' address"}')}[pid]


def smtp_error(kind, to="x@y.invalid"):
    return {"429": smtplib.SMTPSenderRefused(421, b"4.7.0 Too many messages, try later", "priya@mail.example.in"),
            "500": smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
            "timeout": socket.timeout("timed out"),
            "refused": smtplib.SMTPRecipientsRefused({to: (550, b"5.1.1 User unknown")})}[kind]


def inject(rig, kind):
    if rig.pid == "smtp":
        if kind == "401":
            rig.smtp.fail_login = True
        else:
            rig.smtp.script.append(smtp_error(kind))
    else:
        rig.wire.script.append(http_error(rig.pid, kind))


def clear_injection(rig):
    rig.smtp.fail_login = False


@pytest.mark.parametrize("pid", ["resend", "sendgrid", "postmark", "smtp"])
@pytest.mark.parametrize("kind", ["401", "429", "500", "timeout"])
def test_provider_wide_errors_pause_then_resume_with_the_same_ids(tmp_path, pid, kind):
    rig = Rig(tmp_path, pid, live=True)
    inject(rig, kind)
    t = at_ist(1, 10, 0)
    out = rig.run(t)
    assert out["sent"] == 0 and out["failed"] == 1 and out["paused"]
    failed = rig.outbox("provider_error")
    assert len(failed) == 1
    lid = failed[0]["lead_id"]
    seq = rig.lead(lid)["sequence"]
    assert "in_flight" not in seq and "failed_attempts" not in seq and seq["sent_days"] == []   # not the lead's fault
    pause = rig.store.read("settings")["sending_paused"]
    assert rig.run(t + timedelta(minutes=30))["skipped"].startswith("sending is paused")
    if kind == "401":
        assert pause["resume_at"] is None                    # a bad key needs a person
        clear_injection(rig)
        assert "paused" in rig.run(t + timedelta(hours=3))["skipped"]
        assert rig.client.post("/api/sending/resume").status_code == 200
        out = rig.run(t + timedelta(hours=3, minutes=5))
    else:
        assert pause["resume_at"] == (t + timedelta(hours=1)).isoformat(timespec="seconds")
        out = rig.run(t + timedelta(hours=1, minutes=1))
    assert out["sent"] == 5 and "sending_paused" not in rig.store.read("settings")
    retry = next(m for m in rig.outbox("sent") if m["lead_id"] == lid)
    assert retry["idempotency_key"] == failed[0]["idempotency_key"] == f"{lid}:0:live"
    # the retry must be the same request, so the provider can recognise it (Resend answers 409 to a changed payload)
    assert retry["message_id"] == failed[0]["message_id"]
    wire = attempts(rig)
    if pid != "smtp" or kind != "401":                       # a refused SMTP login never reaches send_message
        first, again = wire[0], next(a for a in wire[1:] if a["to"] == failed[0]["to"])
        assert first["headers"]["Message-ID"] == again["headers"]["Message-ID"] and first["text"] == again["text"]
        assert first["idem"] == again["idem"]


def test_retry_with_sample_link_repeats_the_same_body(tmp_path):
    """The retry after a timeout must carry the same body (and message id) as the first attempt."""
    rig = Rig(tmp_path, "resend", live=True, email1_include_link=True)
    inject(rig, "timeout")
    t = at_ist(1, 10, 0)
    rig.run(t)
    rig.run(t + timedelta(hours=1, minutes=1))
    wire = attempts(rig)
    again = next(a for a in wire[1:] if a["to"] == wire[0]["to"])
    assert "/demo/" not in wire[0]["text"] and again["text"] == wire[0]["text"]
    assert again["idem"] == wire[0]["idem"] and again["headers"]["Message-ID"] == wire[0]["headers"]["Message-ID"]


@pytest.mark.parametrize("pid", ["resend", "sendgrid", "postmark", "smtp"])
def test_refused_recipient_fails_only_that_lead(tmp_path, pid):
    rig = Rig(tmp_path, pid, live=True, per_run_cap=2)       # small runs so the warm-up limit (10) is not reached
    inject(rig, "refused")
    t = at_ist(1, 10, 0)
    out = rig.run(t)
    assert out["failed"] == 1 and out["sent"] == 2 and "paused" not in out    # the run carries on
    bad = rig.outbox("failed")[0]["lead_id"]
    seq = rig.lead(bad)["sequence"]
    assert seq["failed_attempts"] == 1 and seq["sent_days"] == [] and not seq.get("stopped")
    assert "sending_paused" not in rig.store.read("settings")
    rig.run(t + timedelta(minutes=30))
    assert not [m for m in rig.outbox() if m["lead_id"] == bad and m["sent_at"] > bad_time(rig, bad)]
    rig.run(t + timedelta(hours=1, minutes=5))                # back-off over: tried again and delivered
    assert [m for m in rig.outbox("sent") if m["lead_id"] == bad]


def bad_time(rig, lead_id):
    return next(m["sent_at"] for m in rig.outbox("failed") if m["lead_id"] == lead_id)


def test_postmark_200_with_error_code_is_a_per_lead_failure(tmp_path):
    rig = Rig(tmp_path, "postmark", live=True)
    rig.wire.script.append({"ErrorCode": 406, "Message": "You tried to send to a recipient that has been marked as inactive."})
    out = rig.run(at_ist(1, 10, 0))
    assert out["failed"] == 1 and out["sent"] == 5
    assert "406" in rig.outbox("failed")[0]["error"]


def test_three_refusals_stop_the_lead_and_resume_restarts_it(tmp_path):
    rig = Rig(tmp_path, "resend", live=True, per_run_cap=1)
    t = at_ist(1, 10, 0)
    first = None
    for i in range(3):
        rig.wire.script.append(http_error("resend", "refused"))
        rig.run(t + timedelta(hours=1, minutes=5) * i)
        first = first or rig.outbox("failed")[0]["lead_id"]
        rig.wire.script.clear()
        assert all(m["lead_id"] != first for m in rig.outbox("sent"))   # after its back-off it is first in line
    assert rig.lead(first)["sequence"]["stopped"] == "send_failed"
    assert rig.client.post(f"/api/leads/{first}/sequence/resume").status_code == 200
    assert rig.lead(first)["sequence"].get("stopped") is None
    rig.run(t + timedelta(hours=5))
    assert [m for m in rig.outbox("sent") if m["lead_id"] == first]
    assert rig.client.post(f"/api/leads/{first}/sequence/resume").status_code == 400   # only send_failed resumes


# ---- caps, domains, idempotency ----
def test_per_run_cap_and_daily_cap_in_practice(tmp_path):
    rig = Rig(tmp_path, "resend", per_run_cap=7, daily_cap=12)
    sent = [rig.run(at_ist(1, 9, 0) + timedelta(minutes=20 * i))["sent"] for i in range(4)]
    assert sent == [7, 5, 0, 0]
    assert rig.run(at_ist(2, 9, 0))["sent"] == 7              # a new IST day resets the cap


def test_one_email_per_company_domain_per_day_with_shared_domains(tmp_path):
    rig = Rig(tmp_path, "sendgrid", live=True)
    shared = [lid for lid, l in rig.leads.items() if l.get("email")][:3]
    rig.store.update("leads", lambda rows: [{**r, "email": f"hr{i}@sharedgroup.invalid"} if r["id"] in shared else r
                                            for i, r in enumerate(rows)])
    rig.simulate(1, 1)
    day1 = [m for m in rig.outbox("sent") if m["to"].endswith("@sharedgroup.invalid")]
    assert len(day1) == 1
    rig.simulate(2, 2)
    assert len([m for m in rig.outbox("sent") if m["to"].endswith("@sharedgroup.invalid")]) == 2


def test_message_ids_are_unique_per_step_and_mode(tmp_path):
    rig = Rig(tmp_path, "resend")
    rig.simulate(0, 1)
    rig.set(live_sending=True)
    rig.simulate(2, 3)
    rows = rig.outbox()
    pairs = {(m["idempotency_key"], m["message_id"]) for m in rows}
    assert len({k for k, _ in pairs}) == len({mid for _, mid in pairs}) == len(rows)


def test_resume_refuses_a_lead_stopped_by_unsubscribe(tmp_path):
    rig = Rig(tmp_path, "resend", live=True)
    # signed links started before this test's simulated timeline (the app records the real clock, which is later than the fixed START date)
    rig.store.update("settings", lambda st: {**st, "unsub_signed_since": "2026-11-02T00:00:00+00:00"})
    rig.run(at_ist(1, 10, 0))
    lid = rig.outbox("sent")[0]["lead_id"]
    token = unsub_path(rig.store, lid).rsplit("/", 1)[1]
    assert rig.client.post(f"/u/{token}").status_code == 200
    r = rig.client.post(f"/api/leads/{lid}/sequence/resume")
    assert r.status_code == 400 and "stay stopped" in r.json()["detail"]
    assert rig.lead(lid)["sequence"]["stopped"] == "unsubscribed"
    assert rig.client.post(f"/u/{token[:-2]}xx").status_code == 404        # a tampered signature is refused
    assert rig.client.get(f"/u/{lid}").status_code == 404                  # unsigned link: no grace for new mail
