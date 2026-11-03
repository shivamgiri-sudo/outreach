"""Regression tests for bugs found while QA-ing LinkedIn Sales Navigator / scraper-tool imports.
All people, companies, numbers and domains are made up (*.example, 90000 1xxxx)."""
from datetime import date

import pytest

from app.header_map import detect_format, map_columns
from app.lead_import import read_table, run_import

TODAY = date(2026, 11, 3)


def run(csv_text, leads=None, suppressed=()):
    headers, table = read_table(csv_text)
    fmt = detect_format(headers)
    columns = map_columns(headers, [r for _, r in table[:25]], fmt)
    leads = [] if leads is None else leads
    result = run_import(leads, table, columns, fmt["source"] or "qa", set(suppressed), {}, today=TODAY,
                        at="2026-11-03T05:00:00+00:00")
    return leads, result, {c["column"]: c["field"] for c in columns}, fmt


def test_email_filled_on_another_contact_does_not_crash_the_import():
    """A row that adds an email to an existing 'other contact' of a lead whose main contact has no email used to raise
    AttributeError (is_role_address(None)) and fail the whole import with a 500."""
    leads, _, _, _ = run("Company,Full name,Phone\nKaveri Textiles,Imran Kaur,+91 90000 10001\n"
                         "Kaveri Textiles,Divya Das,+91 90000 10002\n")
    assert len(leads) == 1 and leads[0]["other_contacts"][0]["contact_name"] == "Divya Das"
    leads, result, _, _ = run("Company,Full name,Phone,Email\nKaveri Textiles,Divya Das,+91 90000 10002,"
                              "divya.das@kaveritextiles.example\n", leads)
    assert result["summary"]["merge"] == 1
    assert leads[0]["other_contacts"][0]["email"] == "divya.das@kaveritextiles.example"
    assert leads[0]["email"] is None and leads[0]["email_is_role"] is False
