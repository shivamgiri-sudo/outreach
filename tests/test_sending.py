"""Sending quality: no double sends, sensible stop rules, spacing, warm-up, per-company limits and retry backoff."""
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import sequence_runner
from app.main import create_app
from app.sequence_runner import effective_daily_cap, run_due
from app.store import Store

T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)  # Tuesday 10:30 IST
ROWS = ["company,contact_name,email,website,open_roles,company_size,country"]


def csv_for(n, domain=None):
    lines = [f"Co{i},Person {i},p{i}@{domain or f'co{i}.in'},https://{domain or f'co{i}.in'},5,100,IN" for i in range(n)]
    return "\n".join(ROWS + lines) + "\n"


def make(tmp_path, n=1, domain=None, transport=None):
    kwargs = {"email_transport": transport} if transport else {}
    c = TestClient(create_app(data_file=tmp_path / "d.json", **kwargs))
    c.post("/api/leads/import", json={"csv": csv_for(n, domain), "source": "apollo"})
    return c


def leads(c):
    return c.get("/api/leads").json()


def run(c, at=T0):
    return c.post("/api/sequences/run", json={"now": at.isoformat()}).json()


# ---- enrolment ----
def test_enrolling_twice_is_refused_and_does_not_restart(tmp_path):
    c = make(tmp_path)
    lid = leads(c)[0]["id"]
    assert c.post(f"/api/leads/{lid}/enroll").status_code == 200
    assert run(c)["sent"] == 1
    r = c.post(f"/api/leads/{lid}/enroll")
    assert r.status_code == 400 and "already" in r.json()["detail"].lower()
    seq = c.get(f"/api/leads/{lid}").json()["sequence"]
    assert seq["practice_days"] == [0] and seq["sent_days"] == []   # a practice run never uses up the real Email 1


# ---- stop rules ----
@pytest.mark.parametrize("stage", ["interested", "whatsapp_active", "call_done", "trial"])
def test_warm_leads_get_no_more_cold_emails(tmp_path, stage):
    c = make(tmp_path)
    lid = leads(c)[0]["id"]
    c.post(f"/api/leads/{lid}/enroll")
    c.post(f"/api/leads/{lid}/consent", json={"text": "yes on the phone"})
    for target in ["verified", "enriched", "contacted", "engaged", "interested", "whatsapp_active", "call_done", "trial"]:
        c.post(f"/api/leads/{lid}/stage", json={"stage": target})
        if target == stage:
            break
    assert run(c)["sent"] == 0


# ---- spacing ----
def test_followups_keep_at_least_two_days_apart_after_downtime(tmp_path):
    c = make(tmp_path)
    lid = leads(c)[0]["id"]
    c.post(f"/api/leads/{lid}/enroll")
    late = T0 + timedelta(days=15)          # the app was off for two weeks
    assert run(c, late)["sent"] == 1        # day 0
    assert run(c, late + timedelta(minutes=15))["sent"] == 0
    assert run(c, late + timedelta(days=1))["sent"] == 0
    assert run(c, late + timedelta(days=2))["sent"] == 0       # follow-ups are spaced from the email actually sent:
    assert run(c, late + timedelta(days=3))["sent"] == 0       # day 3 comes 3 days after Email 1, not 2 — a Saturday,
    assert run(c, late + timedelta(days=5))["sent"] == 1       # so it waits for Monday's sending hours


def test_sends_are_spread_over_runs(tmp_path):
    c = make(tmp_path, n=12)
    for lead in leads(c):
        c.post(f"/api/leads/{lead['id']}/enroll")
    first = run(c)
    assert first["sent"] == sequence_runner.DEFAULT_PER_RUN_CAP
    assert run(c, T0 + timedelta(minutes=15))["sent"] == sequence_runner.DEFAULT_PER_RUN_CAP


def test_one_email_per_company_domain_per_day(tmp_path):
    c = make(tmp_path, n=3, domain="bigco.in")
    for lead in leads(c):
        c.post(f"/api/leads/{lead['id']}/enroll")
    assert run(c)["sent"] == 1
    assert run(c, T0 + timedelta(hours=1))["sent"] == 0
    assert run(c, T0 + timedelta(days=1))["sent"] == 1


def test_free_mail_addresses_are_not_grouped_as_one_company(tmp_path):
    c = make(tmp_path, n=3, domain="gmail.com")
    for lead in leads(c):
        c.post(f"/api/leads/{lead['id']}/enroll")
    assert run(c)["sent"] == 3


# ---- warm-up ----
def test_warm_up_ramp_limits_live_sending():
    first = T0.date()
    assert effective_daily_cap(50, live=True, first_send=first, today=first) == 10
    assert effective_daily_cap(50, live=True, first_send=first, today=first + timedelta(days=8)) == 20
    assert effective_daily_cap(50, live=True, first_send=first, today=first + timedelta(days=15)) == 35
    assert effective_daily_cap(50, live=True, first_send=first, today=first + timedelta(days=30)) == 50
    assert effective_daily_cap(15, live=True, first_send=first, today=first + timedelta(days=30)) == 15
    assert effective_daily_cap(20, live=False, first_send=None, today=first) == 20


# ---- double sends ----
class SlowSender:
    def __init__(self):
        self.calls = 0
        self.gate = threading.Event()

    def __call__(self, message):
        self.calls += 1
        self.gate.wait(2)
        return {"status": "dry_run"}


def test_two_runs_at_once_cannot_double_send(tmp_path):
    c = make(tmp_path)
    lid = leads(c)[0]["id"]
    c.post(f"/api/leads/{lid}/enroll")
    store = Store(tmp_path / "d.json")
    sender = SlowSender()
    results = []
    threads = [threading.Thread(target=lambda: results.append(run_due(store, T0, "http://x", sender, ignore_hours=True)))
               for _ in range(2)]
    [t.start() for t in threads]
    sender.gate.set()
    [t.join() for t in threads]
    assert sender.calls == 1
    assert any(r.get("skipped") for r in results)


def test_a_send_interrupted_by_a_crash_is_not_repeated(tmp_path):
    c = make(tmp_path)
    lid = leads(c)[0]["id"]
    c.post(f"/api/leads/{lid}/enroll")
    store = Store(tmp_path / "d.json")

    def crash(message):
        raise KeyboardInterrupt  # stands in for the process dying mid-send

    with pytest.raises(KeyboardInterrupt):
        run_due(store, T0, "http://x", crash, ignore_hours=True)
    sender = SlowSender()
    sender.gate.set()
    result = run_due(Store(tmp_path / "d.json"), T0 + timedelta(minutes=15), "http://x", sender, ignore_hours=True)
    assert sender.calls == 0 and result["needs_check"] == 1


# ---- failures ----
class FailingSender:
    def __init__(self):
        self.calls = 0

    def __call__(self, message):
        self.calls += 1
        return {"status": "failed", "error": "provider down"}


def test_failed_sends_back_off_and_stop_after_three_attempts(tmp_path):
    c = make(tmp_path)
    lid = leads(c)[0]["id"]
    c.post(f"/api/leads/{lid}/enroll")
    store = Store(tmp_path / "d.json")
    sender = FailingSender()
    run_due(store, T0, "http://x", sender, ignore_hours=True)
    run_due(store, T0 + timedelta(minutes=15), "http://x", sender, ignore_hours=True)   # too soon: backing off
    assert sender.calls == 1
    run_due(store, T0 + timedelta(hours=1, minutes=1), "http://x", sender, ignore_hours=True)
    run_due(store, T0 + timedelta(hours=2, minutes=2), "http://x", sender, ignore_hours=True)
    run_due(store, T0 + timedelta(hours=3, minutes=3), "http://x", sender, ignore_hours=True)
    assert sender.calls == 3
    assert store.read("leads")[0]["sequence"]["stopped"] == "send_failed"
