"""Regression tests for bugs found while testing Indian job-board and directory imports. All data is made up."""
from datetime import date

from app.header_map import detect_format, map_columns
from app.lead_import import read_table, run_import
from app.standardise import parse_phone

TODAY = date(2026, 11, 3)


def _import(csv_text, leads=None, today=TODAY):
    headers, table = read_table(csv_text)
    fmt = detect_format(headers)
    columns = map_columns(headers, [r for _, r in table[:25]], fmt)
    leads = [] if leads is None else leads
    result = run_import(leads, table, columns, fmt["source"] or "test", set(), {}, today=today)
    return leads, result, {c["column"]: c["field"] for c in columns}


def test_salary_and_id_columns_are_never_read_as_phone_numbers():
    """An Apna 'salary' range and an Indeed hex 'id' were sniffed as phones; every row then shared one 'phone'
    and 20 different companies collapsed into a single lead."""
    names = ["Amla", "Bael", "Chiku", "Dhania", "Elaichi", "Falsa", "Guava", "Haldi", "Imli", "Jamun", "Kokum",
             "Lauki", "Methi", "Neem", "Ol", "Papita", "Rai", "Sitaphal", "Tamatar", "Urad"]
    rows = "".join(f"Telecaller,{n} Foods Pvt Ltd,Pune,₹12000 - ₹18000,{k:016x},{2700000000 + k}\n"
                   for k, n in enumerate(names))
    leads, result, mapping = _import("job_title,company_name,city,salary,id,UNIQUE_QUERY_ID\n" + rows)
    assert mapping["salary"] is None and mapping["id"] is None and mapping["UNIQUE_QUERY_ID"] is None
    assert result["summary"]["create"] == 20 and len(leads) == 20
    assert all(not lead.get("phone") for lead in leads)


def test_plus_zero_digits_are_not_an_international_number():
    assert parse_phone("+0000000000000001") == (None, "invalid_phone")
    assert parse_phone("+44 20 7946 0000")[0] == ("+442079460000", "intl")
