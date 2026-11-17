"""Tests use fixed 'now' values in early Nov 2026, so enrolment must happen before them, whatever today is."""
import os
import tempfile

import pytest

# `app.main` builds a module-level app on import, which writes its data file. Under pytest-xdist every worker imports it
# at once, and they raced on data/store.json(.tmp). Each worker (and a plain run) gets its own throwaway file instead.
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")
if os.environ.get("OUTREACH_TEST_DATA_FOR") != _WORKER:           # workers inherit the controller's env: re-pick per worker
    os.environ["OUTREACH_DATA_FILE"] = os.path.join(tempfile.mkdtemp(prefix=f"outreach-test-{_WORKER}-"), "store.json")
    os.environ["OUTREACH_TEST_DATA_FOR"] = _WORKER

ENROL_TIME = "2026-11-03T04:00:00+00:00"


@pytest.fixture(autouse=True)
def fixed_enrolment_clock(monkeypatch):
    from app import domain_signals, email_domain, github_profiles, gravatar, routes_jobs, routes_outreach, store
    monkeypatch.setattr(email_domain, "CHECK", False)              # no DNS lookups in tests (no network)
    monkeypatch.setattr(gravatar, "LIVE", False)                   # no Gravatar calls either (tests pass a fake fetch)
    monkeypatch.setattr(domain_signals, "LIVE", False)             # no DNS / RDAP for domain trust signals (tests pass fakes)
    monkeypatch.setattr(github_profiles, "LIVE", False)            # no GitHub API calls (tests pass a fake get)
    monkeypatch.setattr(routes_jobs, "ALLOW_NOW_OVERRIDE", True)
    monkeypatch.setattr(store, "FSYNC", False)
    monkeypatch.setattr(routes_outreach, "_now_iso", lambda: ENROL_TIME)
    monkeypatch.setattr(routes_outreach, "ALLOW_NOW_OVERRIDE", True)
    monkeypatch.setenv("OUTREACH_EMAIL_GATE_DEFAULT", "0")          # the gate is OFF by default too (app/email_gate.py); this pins it for the whole suite
    for name in ("OUTREACH_SECRET_KEY", "OUTREACH_ALLOW_PRIVATE_HOSTS", "OUTREACH_ENABLE_DOCS"):
        monkeypatch.delenv(name, raising=False)      # tests set these themselves when they need them


@pytest.fixture(autouse=True)
def frozen_rate_limit_clock(monkeypatch):
    """Rate-limit buckets refill 2/s at 120/min; on a loaded machine a 120-request loop earned extra tokens.
    Tests that check refill pass their own clock to RateLimiter."""
    from app import web_security
    monkeypatch.setattr(web_security, "_clock", lambda: 1000.0)
