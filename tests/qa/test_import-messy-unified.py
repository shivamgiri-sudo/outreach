"""QA: the smart importer and the team-sheet importer against messy real-world files.

BOM, Excel 'CSV UTF-8' with CRLF and semicolons, quoted newlines, tab files, UTF-16, Excel serial dates, trailing blank
columns, duplicate headers, merged two-row headers, a 5,000-row file, formula cells, emoji and Devanagari names, and the
team Google Sheet tabs. Every name, number and domain is made up (*.example, 90000 1xxxx).
"""
import csv
import io
import time
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

FIX = Path(__file__).parent / "fixtures" / "import-messy-unified"


def fixture_text(name: str) -> str:
    """The file as the browser hands it over: File.text() decodes UTF-8 and keeps a leading BOM out of nothing."""
    return (FIX / name).read_bytes().decode("utf-8", errors="replace")


@pytest.fixture()
def app(tmp_path):
    return create_app(data_file=tmp_path / "d.json")


@pytest.fixture()
def client(app):
    return TestClient(app)


def post(client, path, body, status=200):
    r = client.post(path, json=body)
    assert r.status_code == status, r.text
    return r.json()


def by_company(client):
    return {l["company"]: l for l in client.get("/api/leads").json()}


def notes(lead):
    return [e["text"] for e in lead["timeline"]]


# ---------------------------------------------------------------- encodings and delimiters
def test_excel_csv_utf8_with_bom_crlf_semicolons_quoted_newlines_and_trailing_blank_columns(client):
    r = post(client, "/api/import/commit", {"csv": fixture_text("excel_semicolon_bom_crlf.csv")})
    cols = r["mapping"]
    assert cols["Company Name"] == "company" and cols["E-mail ID"] == "email" and cols["Mobile No."] == "phone"
    assert cols["Column 10"] == cols["Column 11"] == "ignore"           # Excel's trailing ';;' columns
    assert not any(h.startswith("﻿") for h in cols)                # BOM never leaks into a header
    s = r["summary"]
    assert (s["rows"], s["create"], s["duplicates_in_file"], s["rejected"]) == (6, 5, 1, 0)   # blank ';;;' row dropped
    leads = by_company(client)
    ganesh = leads["Shree Ganesh Enterprises"]
    assert ganesh["contact_name"] == "Ramesh Kumar" and ganesh["title"] == "HR Manager"      # ALL CAPS tidied
    assert ganesh["phone"] == "+919000010051" and ganesh["city"] == "Pune"
    assert "Remarks: Call after 5pm; prefers Hindi" in notes(ganesh)  # the quoted newline did not split the row
    assert [c["contact_name"] for c in ganesh["other_contacts"]] == ["Sunita Devi"]        # 090000 10052 -> second person
    assert ganesh["other_contacts"][0]["phone"] == "+919000010052"
    assert 'Remarks: Ref: "Diwali" campaign' in notes(leads["Anand Motors & Co"])           # doubled quotes unescaped


def test_emoji_and_devanagari_names_survive_unchanged(client):
    post(client, "/api/import/commit", {"csv": fixture_text("excel_semicolon_bom_crlf.csv")})
    leads = by_company(client)
    assert "Café Mumbaiya 🍵" in leads
    hindi = leads["शुभ लाभ ट्रेडर्स"]
    assert hindi["contact_name"] == "महेश अग्रवाल" and hindi["title"] == "मालिक"
    assert hindi["email"] == "mahesh@shubhlabh.example" and hindi["state"] == "Rajasthan"


def test_excel_serial_and_datetime_posted_on_values_give_a_job_age(client):
    post(client, "/api/import/commit", {"csv": fixture_text("excel_semicolon_bom_crlf.csv")})
    leads = by_company(client)
    today = date.today()
    assert leads["Shree Ganesh Enterprises"]["max_job_age_days"] == (today - date(2026, 10, 23)).days  # 46318
    assert leads["Anand Motors & Co"]["max_job_age_days"] == (today - date(2026, 10, 13)).days        # "Oct 13, 2026"
    assert leads["शुभ लाभ ट्रेडर्स"]["max_job_age_days"] == (today - date(2026, 10, 1)).days


def test_job_age_parser_handles_excel_exports_at_a_fixed_today():
    from app.places import job_age_days
    today = date(2026, 11, 3)
    assert job_age_days("46318", today) == 11                 # Excel serial (General-formatted date cell)
    assert job_age_days("46318.4375", today) == 11            # serial with a time part
    assert job_age_days("28/10/2026 10:30", today) == 6       # Excel 'dd/mm/yyyy hh:mm'
    assert job_age_days("2026-10-28 10:30:00", today) == 6
    assert job_age_days("13 Oct 2026", today) == 21           # spaces inside a date still work
    assert job_age_days("12345", today) == 0                  # a 5-digit number outside the serial range is not a date
    assert job_age_days("46400", today) == 0                  # a serial in the future is never a negative age


def test_tab_delimited_file(client):
    r = post(client, "/api/import/commit", {"csv": fixture_text("tab_delimited.tsv")})
    assert (r["summary"]["create"], r["summary"]["duplicates_in_file"]) == (2, 1)
    assert set(by_company(client)) == {"Nimbus Labs", "Orbit Freight"}


def test_utf16_unicode_text_is_refused_with_a_clear_message(client):
    raw = "Company\tEmail\r\nNimbus Labs\ttara@nimbuslabs.example\r\n".encode("utf-16")   # Excel 'Unicode Text'
    as_browser_reads_it = raw.decode("utf-8", errors="replace")
    r = post(client, "/api/import/preview", {"csv": as_browser_reads_it}, status=400)
    assert "UTF-16" in r["detail"] and "CSV UTF-8" in r["detail"]
    assert client.get("/api/leads").json() == []
    # An API client that decodes the file itself sends plain text, which imports fine.
    ok = post(client, "/api/import/preview", {"csv": raw.decode("utf-16")})
    assert ok["summary"]["create"] == 1


def test_duplicate_header_names_keep_both_columns(client):
    csv_text = ("Company,Email,Email,Phone,Phone\n"
                "Lakeview Hotels,gm@lakeview.example,priya.s@lakeview.example,9000010081,0141-400 1081\n")
    r = post(client, "/api/import/commit", {"csv": csv_text, "source": "hand-made list"})
    assert r["mapping"] == {"Company": "company", "Email": "email", "Email (2)": "email", "Phone": "phone",
                            "Phone (2)": "phone"}
    lead = by_company(client)["Lakeview Hotels"]
    assert set(lead["emails"]) == {"gm@lakeview.example", "priya.s@lakeview.example"}
    assert set(lead["phones"]) == {"+919000010081", "+911414001081"}
    assert lead["source"] == "hand-made list"


def test_two_row_merged_header_uses_the_real_column_names(client):
    r = post(client, "/api/import/commit", {"csv": fixture_text("two_row_header.csv")})
    assert r["mapping"] == {"Company": "company", "City": "city", "Name": "contact_name", "Designation": "title",
                            "Email": "email", "Phone": "phone"}
    assert r["summary"]["create"] == 2 and [row["row"] for row in r["rows"]] == [3, 4]   # spreadsheet row numbers
    assert by_company(client)["Pixel Prints"]["contact_name"] == "Hetal Shah"


def test_trailing_blank_header_columns_are_not_mistaken_for_a_two_row_header(client):
    csv_text = "Company,Email,,,\nNimbus Labs,tara@nimbuslabs.example,,,\nOrbit Freight,kabir@orbitfreight.example,,,\n"
    r = post(client, "/api/import/preview", {"csv": csv_text})
    assert r["summary"]["create"] == 2 and r["rows"][0]["row"] == 2


def test_indiamart_lead_manager_headers_map_company_and_ignore_the_product(client):
    r = post(client, "/api/import/preview", {"csv": fixture_text("5_indiamart.csv"), "source": "indiamart"})
    m = r["mapping"]
    assert m["SENDER_COMPANY"] == "company" and m["SENDER_NAME"] == "contact_name"
    assert m["QUERY_PRODUCT_NAME"] == "ignore" and m["SENDER_MOBILE"] == "phone" and m["SENDER_CITY"] == "city"
    assert r["rows"][0]["company"] == "Nirmal Textiles" and r["rows"][0]["contact_name"] == "Imran Shaikh"


# ---------------------------------------------------------------- formula cells and CSV export safety
def test_formula_looking_cells_are_stored_as_text_and_exported_safely(client):
    post(client, "/api/import/commit", {"csv": fixture_text("excel_semicolon_bom_crlf.csv")})
    lead = by_company(client)['=HYPERLINK("http://evil.example","Click")']
    assert lead["email"] == "hack@formula.example"            # imported as plain text, nothing evaluated
    export = client.get("/api/export/leads.csv")
    assert export.status_code == 200
    rows = list(csv.reader(io.StringIO(export.text.lstrip("﻿"))))
    cells = [c for row in rows[1:] for c in row]
    assert '\'=HYPERLINK("http://evil.example","Click")' in cells
    assert not any(c[:1] in ("=", "+", "-", "@") for c in cells)   # every risky cell starts with a quote


def test_rejected_rows_keep_the_original_formula_cell_for_the_download(client):
    csv_text = "Company,Email\n,=HYPERLINK(\"http://evil.example\")\n,@SUM(1+1)\n"
    r = post(client, "/api/import/preview", {"csv": csv_text})
    assert r["summary"]["rejected"] == 2
    assert r["rejected"][0]["values"]["Email"] == '=HYPERLINK("http://evil.example")'   # web UI quotes it on download


# ---------------------------------------------------------------- volume
def _big_csv(n: int) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["First Name", "Last Name", "Title", "Company", "Email", "Mobile Phone", "Website", "# Employees", "City",
                "Country", "Person Linkedin Url", "Apollo Contact Id"])
    cities = ["Pune", "BANGALORE", "Gurgaon", "Noida", "Chennai", "Hyderabad", "Ahmedabad", "Kolkata"]
    first = ["RAVI", "Anita", "imran", "Pooja", "Sandeep", "Fatima", "Arjun", "Meena", "Gurpreet", "Lakshmi", "Rahul",
             "Sneha", "Vikram", "Ayesha", "Karthik", "Neha", "Rohan"]
    last = ["Kumar", "SHARMA", "Khan", "Iyer", "Reddy", "Singh", "Patil", "Das", "Nair", "Joshi", "Bose", "Mehta", "Gill"]
    for i in range(n):
        co = i // 3                                                        # three different people per company
        w.writerow([first[i % len(first)], last[(i * 7) % len(last)], "hr manager", f"Test Company {co:04d} PVT. LTD.",
                    f"p{i}@co{co:04d}.example", f"+91 90000 {10000 + i:05d}", f"www.co{co:04d}.example", "51-200",
                    cities[co % 8], "India", f"https://www.linkedin.com/in/p{i}-example", f"ap{i}"])
    return out.getvalue()


def test_five_thousand_row_file_previews_and_commits_in_reasonable_time(client):
    text = _big_csv(5000)
    started = time.perf_counter()
    r = post(client, "/api/import/preview", {"csv": text})
    preview_s = time.perf_counter() - started
    assert r["summary"]["rows"] == 5000 and r["summary"]["create"] == 1667
    assert r["summary"]["duplicates_in_file"] == 3333 and r["summary"]["contacts_added"] == 3333   # other_contacts
    assert len(r["duplicates"]) == 500                                 # response lists are capped
    started = time.perf_counter()
    c = post(client, "/api/import/commit", {"csv": text})
    commit_s = time.perf_counter() - started
    assert len(c["created_ids"]) == 1667
    assert preview_s < 30 and commit_s < 30, (preview_s, commit_s)
    again = post(client, "/api/import/preview", {"csv": text})       # re-importing the same file changes nothing
    assert again["summary"]["create"] == 0 and again["summary"]["unchanged"] == 5000


def test_more_than_the_row_limit_is_refused():
    from app.lead_import import MAX_ROWS, read_table
    with pytest.raises(ValueError, match="at most"):
        read_table("Company,Email\n" + "".join(f"C{i},a{i}@c{i}.example\n" for i in range(MAX_ROWS + 1)))


# ---------------------------------------------------------------- team Google Sheet tabs (/api/leads/import-sheet)
def test_team_sheet_leads_tab_with_bom_groups_rows_per_company(client):
    r = post(client, "/api/leads/import-sheet", {"csv": fixture_text("team_sheet_leads_bom.csv"), "tab": "leads"})
    assert r == {**r, "created": 4, "rows": 5}                        # BOM no longer hides 'Company Name'
    leads = by_company(client)
    ganga = leads["GANGA AGRO PVT. LTD."]
    assert ganga["stage"] == "engaged"                                 # furthest status of its two rows (Interested)
    assert ganga["open_roles"] == 10 and ganga["role"] == "Field Officer and Sales Officer"
    assert ganga["phones"] == ["+919000010031"] and ganga["landlines"] == ["+915224001031"]
    assert set(ganga["job_links"]) == {"https://www.naukri.example/job/ganga-1", "https://www.indeed.example/job/ganga-2"}
    assert leads["Shree Balaji Traders 🙏"]["contact_name"] == "राजेश गुप्ता"
    kesar = leads["Kesar Sweets"]
    assert kesar["stage"] == "disqualified" and kesar["phone_junk"] is True and kesar["phone"] is None


def test_team_sheet_never_brings_back_a_do_not_contact_email(client, app):
    app.state.store.write("suppressed", ["vikas@vikasunsub.example"])   # unsubscribed earlier; lead later deleted
    r = post(client, "/api/leads/import-sheet", {"csv": fixture_text("team_sheet_leads_bom.csv"), "tab": "leads"})
    assert r["suppressed"] == 1 and r["created"] == 3
    assert "Vikas Retail Unsub" not in by_company(client)
    d = post(client, "/api/leads/import-sheet", {"csv": fixture_text("team_demo.csv"), "tab": "demo"})
    assert d["suppressed"] == 1
    assert "Vikas Retail Unsub" not in by_company(client)


def test_quick_paste_never_brings_back_a_do_not_contact_email(client, app):
    app.state.store.write("suppressed", ["vikas@vikasunsub.example"])
    r = post(client, "/api/leads/import", {"csv": "company,email\nVikas Retail Unsub,Vikas@VikasUnsub.example\n"})
    assert r["suppressed"] == 1 and r["created"] == 0
    assert client.get("/api/leads").json() == []


def test_team_sheet_demo_and_decision_maker_tabs_after_the_leads_tab(client):
    post(client, "/api/leads/import-sheet", {"csv": fixture_text("team_sheet_leads_bom.csv"), "tab": "leads"})
    d = post(client, "/api/leads/import-sheet", {"csv": fixture_text("team_demo.csv"), "tab": "demo"})
    assert (d["created"], d["duplicates"]) == (1, 2)                 # Ganga Agro and Vikas Retail already exist
    leads = by_company(client)
    assert leads["Orchid Clinics"]["stage"] == "call_done" and leads["Orchid Clinics"]["demo_given"] == "30-Oct-2026"
    assert leads["GANGA AGRO PVT. LTD."]["stage"] == "engaged"       # the duplicate demo row did not move the stage
    dm = post(client, "/api/leads/import-sheet", {"csv": fixture_text("team_decision_makers.csv"),
                                                  "tab": "decision_makers"})
    assert dm["matched"] == 2 and dm["unmatched"] == ["Unknown Co"]  # '(company page)' and 'Notes' rows skipped
    leads = by_company(client)
    ganga = leads["GANGA AGRO PVT. LTD."]
    assert [c["contact_name"] for c in ganga["other_contacts"]] == ["Meera Singh"]
    assert ganga["other_contacts"][0]["linkedin_url"] == "https://www.linkedin.com/in/meera-singh-example"
    assert [c["contact_name"] for c in leads["Orchid Clinics"].get("other_contacts", [])] == ["Farah Khan"]


def test_team_sheet_rejects_an_unknown_tab(client):
    r = post(client, "/api/leads/import-sheet", {"csv": "Company Name\nX\n", "tab": "Sheet1"}, status=400)
    assert "tab must be" in r["detail"]


def test_sales_navigator_profile_url_is_never_taken_as_the_company_website(client):
    csv_text = ("Full Name,Job Title,Company Name,Company Domain,Sales Navigator Profile URL,Linkedin URL\n"
                "Tara Iyer,HR Head,Nimbus Labs,,https://www.linkedin.com/sales/lead/TARA1,"
                "https://www.linkedin.com/in/tara-iyer-example\n"
                "Kabir Das,TA Lead,Orbit Freight,,https://www.linkedin.com/sales/lead/KABIR1,"
                "https://www.linkedin.com/in/kabir-das-example\n")
    r = post(client, "/api/import/preview", {"csv": csv_text})
    assert r["mapping"]["Sales Navigator Profile URL"] == "ignore"
    assert not any("linkedin.com/sales" in w["text"] for w in r["warnings"])
    assert all(not row["domain"] for row in r["rows"])
