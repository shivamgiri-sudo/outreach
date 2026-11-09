"""Excel lead files: .xlsx sheets become CSV for the normal smart import (first sheet with data, or a chosen one)."""
import io
from datetime import date

import openpyxl
from fastapi.testclient import TestClient

from app import excel_import
from app.main import create_app


def workbook(sheets: dict) -> bytes:
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


LEADS = [[None], ["company", "contact_name", "email", "phone", "added on", None],
         ["Acme Pvt Ltd", "Ravi Kumar", "ravi@acme.com", 9876543210, date(2026, 11, 2), None],
         [None, None, None, None, None, None],
         ["Beta Ltd", "Sita Rao", "sita@beta.in", 919123456789.0, date(2026, 11, 3), None]]


def test_first_sheet_with_data_numbers_and_dates_clean():
    out = excel_import.to_csv(workbook({"Notes": [], "Leads": LEADS, "Other": [["x"], ["1"]]}), "leads.xlsx")
    assert out["sheet"] == "Leads" and out["sheets"] == ["Notes", "Leads", "Other"] and out["rows"] == 2
    lines = out["csv"].splitlines()
    assert lines[0] == "company,contact_name,email,phone,added on"              # empty column dropped
    assert lines[1] == "Acme Pvt Ltd,Ravi Kumar,ravi@acme.com,9876543210,2026-11-02"
    assert lines[2].endswith(",919123456789,2026-11-03")                        # no ".0", empty row skipped
    assert excel_import.to_csv(workbook({"A": [["h"], ["1"]], "B": [["k"], ["2"]]}), sheet="B")["csv"] == "k\n2\n"


def test_bad_files_get_clear_messages():
    for data, words in ((b"", "empty"), (b"company,email\nA,a@b.c\n", "not an Excel file"),
                        (b"PK\x03\x04garbage", "could not be read"), (workbook({"S": []}), "No data")):
        try:
            excel_import.to_csv(data, "x.xlsx")
            raise AssertionError("expected an error")
        except excel_import.ExcelError as exc:
            assert words in str(exc)


def test_upload_then_import_like_a_csv(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    r = c.post("/api/import/excel?filename=leads.xlsx", content=workbook({"Leads": LEADS}),
               headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200 and r.json()["rows"] == 2
    assert c.post("/api/import/excel?filename=leads.xlsx&sheet=Nope", content=workbook({"Leads": LEADS})).status_code == 400
    assert c.post("/api/import/commit", json={"csv": r.json()["csv"], "source": "excel"}).status_code == 200
    leads = c.app.state.store.read("leads")
    assert sorted(l["company"] for l in leads) == ["Acme", "Beta"]                 # standardised by the import
    assert all(l.get("phone") for l in leads)                                   # numbers came through as phone numbers
