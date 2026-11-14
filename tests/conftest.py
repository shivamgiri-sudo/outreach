"""Tests use fixed 'now' values in early Nov 2026, so enrolment must happen before them, whatever today is."""
import pytest

ENROL_TIME = "2026-11-03T04:00:00+00:00"


@pytest.fixture(autouse=True)
def fixed_enrolment_clock(monkeypatch):
    from app import email_domain, routes_jobs, routes_outreach, store
    monkeypatch.setattr(email_domain, "CHECK", False)              # no DNS lookups in tests (no network)
    monkeypatch.setattr(routes_jobs, "ALLOW_NOW_OVERRIDE", True)
    monkeypatch.setattr(store, "FSYNC", False)
    monkeypatch.setattr(routes_outreach, "_now_iso", lambda: ENROL_TIME)
    monkeypatch.setattr(routes_outreach, "ALLOW_NOW_OVERRIDE", True)
    monkeypatch.setenv("OUTREACH_EMAIL_GATE_DEFAULT", "0")          # production default is ON (app/email_gate.py); older tests send to never-checked leads
    for name in ("OUTREACH_SECRET_KEY", "OUTREACH_ALLOW_PRIVATE_HOSTS", "OUTREACH_ENABLE_DOCS"):
        monkeypatch.delenv(name, raising=False)      # tests set these themselves when they need them


@pytest.fixture(autouse=True)
def frozen_rate_limit_clock(monkeypatch):
    """Rate-limit buckets refill 2/s at 120/min; on a loaded machine a 120-request loop earned extra tokens.
    Tests that check refill pass their own clock to RateLimiter."""
    from app import web_security
    monkeypatch.setattr(web_security, "_clock", lambda: 1000.0)
