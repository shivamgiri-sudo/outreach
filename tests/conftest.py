"""Tests use fixed 'now' values in early Nov 2026, so enrolment must happen before them, whatever today is."""
import pytest

ENROL_TIME = "2026-11-03T04:00:00+00:00"


@pytest.fixture(autouse=True)
def fixed_enrolment_clock(monkeypatch):
    from app import routes_outreach
    monkeypatch.setattr(routes_outreach, "_now_iso", lambda: ENROL_TIME)
    monkeypatch.setattr(routes_outreach, "ALLOW_NOW_OVERRIDE", True)
