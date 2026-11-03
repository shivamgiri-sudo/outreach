"""QA: importing Indian job-board and business-directory files (Naukri, Indeed, Shine, Apna, foundit, LinkedIn Jobs,
IndiaMART, JustDial, TradeIndia, Google Maps scrapers). Fixtures in fixtures/import-india-sources are fictional and
generated from made-up names, *.example domains and 90000 1xxxx numbers. Formats and sources: docs/qa/import-india-sources.md.
"""
import socket
import urllib.request
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import http_util, scraper
from app.header_map import detect_format, map_columns
from app.lead_import import extract_record, read_table, run_import
from app.main import create_app
from app.places import parse_address
from app.standardise import parse_phone, parse_phones

FIX = Path(__file__).parent / "fixtures" / "import-india-sources"
TODAY = date(2026, 11, 3)
JOB_BOARDS = ("naukri_team_copy.csv", "naukri_apify.csv", "indeed_apify.csv", "linkedin_jobs.csv", "shine.csv",
              "foundit.csv", "apna.csv")
DIRECTORIES = ("justdial_apify.csv", "tradeindia.csv", "outscraper_gmaps.csv", "apify_gmaps.csv", "d7_leads.csv",
               "indiamart_leads.csv")


def text(name):
    return (FIX / name).read_text(encoding="utf-8")


def dry(name, leads=None, today=TODAY):
    """run_import on a fixture with a fixed 'today' (job ages are relative to it)."""
    headers, table = read_table(text(name))
    fmt = detect_format(headers)
    columns = map_columns(headers, [r for _, r in table[:25]], fmt)
    leads = [] if leads is None else leads
    result = run_import(leads, table, columns, fmt["source"] or "file", set(), {}, today=today)
    return leads, result, fmt, {c["column"]: c["field"] for c in columns}


@pytest.fixture()
def app(tmp_path):
    return create_app(data_file=tmp_path / "d.json")


@pytest.fixture()
def client(app):
    return TestClient(app)


def commit(client, name, **extra):
    r = client.post("/api/import/commit", json={"csv": text(name), **extra})
    assert r.status_code == 200, r.text
    return r.json()


def by_company(client):
    return {l["company"]: l for l in client.get("/api/leads").json()}


# ---------------------------------------------------------------- formats are recognised and mapped
EXPECTED = {
    "naukri_team_copy.csv": ("job_board", {"Company Name": "company", "Job Title": "role", "Posted On": "posted_on",
                                           "No. of Openings": "open_roles", "Job Link": "job_link", "Salary": None}),
    "naukri_apify.csv": ("job_board", {"companyName": "company", "title": "role", "jdURL": "job_link",
                                       "vacancy": "open_roles", "jobId": None, "footerPlaceholderLabel": None}),
    "indeed_apify.csv": ("job_board", {"positionName": "role", "company": "company", "postedAt": "posted_on",
                                       "url": "job_link", "externalApplyLink": "job_link", "id": None, "salary": None}),
    "linkedin_jobs.csv": ("job_board", {"title": "role", "companyName": "company", "companyUrl": "company_linkedin_url",
                                        "publishedAt": "posted_on", "jobUrl": "job_link", "applicationsCount": None}),
    "shine.csv": ("job_board", {"Job Title": "role", "Company": "company", "Posted Date": "posted_on"}),
    "foundit.csv": ("job_board", {"jobTitle": "role", "companyName": "company", "locations": "location"}),
    "apna.csv": ("job_board", {"job_title": "role", "company_name": "company", "no_of_openings": "open_roles",
                               "salary": None, "area": None}),
    "justdial_apify.csv": ("google_maps", {"name": "company", "phone": "phone", "address": "address",
                                           "category": "sector", "rating": None}),
    "tradeindia.csv": ("directory", {"Company Name": "company", "Contact Person": "contact_name", "Mobile": "phone",
                                     "Phone": "phone", "GST No": None, "Products": None}),
    "outscraper_gmaps.csv": ("google_maps", {"name": "company", "site": "website", "full_address": "address",
                                             "location_link": None, "place_id": None, "latitude": None}),
    "apify_gmaps.csv": ("google_maps", {"title": "company", "categoryName": "sector", "placeId": None}),
    "d7_leads.csv": ("google_maps", {"Name": "company", "Email": "email", "Category": "sector"}),
    "indiamart_leads.csv": (None, {"SENDER_COMPANY": "company", "SENDER_NAME": "contact_name",
                                   "SENDER_MOBILE": "phone", "RECEIVER_MOBILE": None, "UNIQUE_QUERY_ID": None,
                                   "QUERY_PRODUCT_NAME": None, "GSTIN": None}),
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_export_is_detected_and_mapped(name):
    _, result, fmt, mapping = dry(name)
    key, fields = EXPECTED[name]
    if key:
        assert fmt["key"] == key
    assert {c: mapping[c] for c in fields} == fields
    assert result["summary"]["rejected"] == 0 and result["summary"]["rows"] >= 20


# ---------------------------------------------------------------- job boards give company-only leads + hiring signals
@pytest.mark.parametrize("name", JOB_BOARDS)
def test_job_board_rows_become_company_only_leads(name):
    leads, result, _, _ = dry(name)
    assert leads and result["summary"]["rejected"] == 0
    for lead in leads:
        assert lead["company"] and lead["role"]
        assert not lead.get("contact_name") and not lead.get("email") and not lead.get("phone")
        assert not lead.get("title")                              # the job title is the opening, not a person's title
        assert lead["stage"] == "new" and lead["consent"] is None
        assert lead.get("domain") in (None, "")                   # job-board pages never become the company website
        assert lead["country"] == "IN"


def test_naukri_volume_dedupes_postings_into_companies_with_signals():
    leads, result, _, _ = dry("naukri_team_copy.csv")
    s = result["summary"]
    assert s["rows"] == 121 and s["create"] == 40 and s["duplicates_in_file"] == 81   # blank row dropped, dup row kept
    first = next(l for l in leads if l["company"] == "Shubh Labh Logistics")
    assert first.get("company_legal")                         # 'Pvt. Ltd.' kept aside, not in the name
    # postings k=0,40,80: openings 1,5,9; posted '3 days ago', '2026-10-20' (14 days), '1 week ago'
    assert first["open_roles"] == 9 and first["max_job_age_days"] == 14
    assert len(first["job_links"]) == 3 and first["city"] == "Bengaluru" and first["state"] == "Karnataka"


@pytest.mark.parametrize("name,company,age", [
    ("indeed_apify.csv", "Shubh Labh Logistics", None),   # relative labels, checked as a set below
    ("linkedin_jobs.csv", "Shubh Labh Logistics", 33),    # publishedAt 2026-10-01 (+ 2026-10-13)
    ("shine.csv", "Shubh Labh Logistics", 24),            # '10-Oct-26'
    ("foundit.csv", "Pragati Logistics", 14),             # '20/10/2026'
    ("apna.csv", "Sahyadri Logistics", 24),               # '2026-10-10'
])
def test_posted_dates_turn_into_job_age(name, company, age):
    leads, _, _, _ = dry(name)
    lead = next(l for l in leads if l["company"] == company)
    if name == "indeed_apify.csv":
        ages = [l["max_job_age_days"] for l in leads]
        assert set(ages) <= {0, 3, 7, 30} and {3, 7, 30} <= set(ages)   # 'Posted 7 days ago' and '30+ days ago' read
    else:
        assert lead["max_job_age_days"] == age


def test_unknowable_naukri_footer_label_gives_no_age():
    """Apify Naukri 'footerPlaceholderLabel' ('2 Days Ago') is not a known header: age stays 0 (see report)."""
    leads, _, _, mapping = dry("naukri_apify.csv")
    assert mapping["footerPlaceholderLabel"] is None and all(l["max_job_age_days"] == 0 for l in leads)
    assert {l["city"] for l in leads} <= {"Bengaluru", "Gurugram", "Delhi NCR", "New Delhi", "Mumbai", "Pune",
                                         "Hyderabad", "Chennai", "Noida", "Kolkata"}


# ---------------------------------------------------------------- city aliases
@pytest.mark.parametrize("a,b", [("Bangalore", "Bengaluru"), ("Gurgaon", "Gurugram"), ("Bengaluru(Whitefield)", "Bangalore"),
                                 ("Delhi NCR", "Gurgaon"), ("Delhi NCR", "Noida"), ("New Delhi", "Delhi")])
def test_city_spellings_merge_the_same_company(a, b):
    leads = []
    csv = ("Company Name,Job Title,Location,Posted On,Job Link\n"
           f"Toran Hospitality Pvt Ltd,Steward,\"{a}\",2 days ago,https://www.naukri.com/j-1\n"
           f"TORAN HOSPITALITY PRIVATE LIMITED,Chef,\"{b}\",9 days ago,https://www.naukri.com/j-2\n")
    headers, table = read_table(csv)
    columns = map_columns(headers, [r for _, r in table], detect_format(headers))
    run_import(leads, table, columns, "job_board", set(), {}, today=TODAY)
    assert len(leads) == 1 and leads[0]["max_job_age_days"] == 9


# ---------------------------------------------------------------- landline vs mobile
@pytest.mark.parametrize("raw,expected", [
    ("080-41234567", ("+918041234567", "landline")),
    ("(080) 4123 4567", ("+918041234567", "landline")),
    ("+91 80 4123 4567", ("+918041234567", "landline")),
    ("011-23456789", ("+911123456789", "landline")),
    ("011 2345 6789", ("+911123456789", "landline")),
    ("+91-11-23456789", ("+911123456789", "landline")),
    ("01123456789", ("+911123456789", "landline")),
    ("0124-4012345", ("+911244012345", "landline")),
    ("022 4012 3456", ("+912240123456", "landline")),
    ("1800-102-1234", ("+9118001021234", "landline")),
    ("90000 10001", ("+919000010001", "mobile")),
    ("+91-9000010001", ("+919000010001", "mobile")),
    ("09000010001", ("+919000010001", "mobile")),
    ("919000010001", ("+919000010001", "mobile")),
    ("+91 90000 10001", ("+919000010001", "mobile")),
])
def test_indian_landline_and_mobile_parsing(raw, expected):
    assert parse_phone(raw) == (expected, None)


def test_landline_without_std_code_is_flagged_not_guessed():
    assert parse_phone("4123 4567") == (None, "landline_without_std")
    assert parse_phones("9000010001, 080-41234567")["numbers"] == [("+919000010001", "mobile"),
                                                                  ("+918041234567", "landline")]


def test_directory_leads_prefer_a_mobile_and_keep_the_landline():
    headers, table = read_table(text("justdial_apify.csv"))
    columns = map_columns(headers, [r for _, r in table[:25]], detect_format(headers))
    records = [extract_record(row, columns, TODAY)[0] for _, row in table]
    both = [r for r in records if len(r["phones"]) == 2]
    assert len(both) == 20 and all(r["phone_type"] == "mobile" and r["phone"] == r["phones"][0] for r in both)
    assert {r["phone_type"] for r in records} == {"mobile", "landline"}
    # Across rows the first number wins: a landline-only listing merged with a later mobile keeps the landline as
    # primary and the mobile in phones (recorded in the report as a limitation).
    leads, _, _, _ = dry("justdial_apify.csv")
    lead = next(l for l in leads if l["company"] == "Shubh Labh Logistics")
    assert lead["phone_type"] == "landline" and "+919000010140" in lead["phones"]


# ---------------------------------------------------------------- addresses
@pytest.mark.parametrize("raw,city,state,pin", [
    ("Shop No 4, Koramangala 5th Block, Bangalore - 560034", "Bengaluru", "", "560034"),
    ("12, DLF Cyber City, Gurgaon, Haryana 122002", "Gurugram", "Haryana", "122002"),
    ("Plot 9, Udyog Vihar Phase 4, Gurugram, 122015", "Gurugram", "", "122015"),
    ("3rd Floor, BKC, Mumbai, Maharashtra 400 051, India", "Mumbai", "Maharashtra", "400051"),
    ("B-12, Sector 63, NOIDA, U.P.", "Noida", "", ""),
])
def test_address_parsing(raw, city, state, pin):
    found = parse_address(raw)
    assert (found["city"], found["state"], found["pincode"]) == (city, state, pin)


def test_outscraper_full_address_fills_pincode_city_and_state():
    leads, _, _, _ = dry("outscraper_gmaps.csv")
    for lead in leads:
        assert lead["pincode"] and len(lead["pincode"]) == 6 and lead["city"] and lead["country"] == "IN"
    assert next(l for l in leads if l["company"] == "Shubh Labh Logistics")["state"] == "Karnataka"


# ---------------------------------------------------------------- GST and other noise columns are ignored
def test_gst_numbers_never_end_up_in_a_lead():
    for name in ("tradeindia.csv", "indiamart_leads.csv"):
        leads, _, _, _ = dry(name)
        for lead in leads:
            blob = repr({k: v for k, v in lead.items() if k not in ("timeline",)})
            assert "AAACZ" not in blob and "AAAAA0000A1Z5" not in blob


def test_indiamart_buyers_are_people_at_companies_not_one_merged_blob():
    leads, result, _, _ = dry("indiamart_leads.csv")
    assert result["summary"]["create"] == 40 and len(leads) == 40
    assert all("+919000099999" not in (l.get("phones") or []) for l in leads)     # the seller's own number
    named = [l for l in leads if l["contact_name"] not in ("", "Buyer")]
    assert len(named) >= 30 and all(l["email"].startswith("buyer") for l in leads)


# ---------------------------------------------------------------- end to end through the API
def test_company_only_rows_then_contact_rows_merge_into_one_lead(client):
    jobs = commit(client, "naukri_team_copy.csv", source="naukri")
    assert jobs["summary"]["create"] == 40
    contacts = commit(client, "contacts_followup.csv", source="team_sheet")
    assert contacts["summary"]["create"] == 0 and contacts["summary"]["merge"] == 10
    saved = by_company(client)
    assert len(saved) == 40
    lead = saved["Shubh Labh Logistics"]
    assert lead["contact_name"] == "Pinky Testwala" and lead["title"] == "HR Manager"
    assert lead["email"] == "pinky@shubhlabhlogistics.example" and lead["phone_type"] == "mobile"
    assert lead["role"] and lead["open_roles"] == 9 and len(lead["job_links"]) == 3   # hiring signal kept
    assert lead["stage"] == "new" and lead["consent"] is None and lead["source"] == "naukri"
    assert client.get("/api/import/history").json()[0]["merged"] == 10


def test_job_board_contact_merge_respects_closed_leads(app, client):
    commit(client, "shine.csv", source="shine")
    lead_id = by_company(client)["Ganpati Retail"]["id"]
    app.state.store.update_lead(lead_id, lambda l: {**l, "stage": "unsubscribed"})
    r = commit(client, "naukri_team_copy.csv", source="naukri")
    assert r["summary"]["warnings"].get("closed_lead")
    assert by_company(client)["Ganpati Retail"]["stage"] == "unsubscribed"


def test_suppressed_buyer_email_row_is_skipped(app, client):
    app.state.store.update("suppressed", lambda rows: [*rows, "buyer3@navrangfacility.example"])
    r = commit(client, "indiamart_leads.csv", source="indiamart")
    assert r["summary"]["skipped"] >= 1
    assert all(l.get("email") != "buyer3@navrangfacility.example" for l in client.get("/api/leads").json())


def test_importing_every_file_never_scrapes_or_contacts_anyone(monkeypatch, client):
    """Job-board and directory imports are offline: no page fetch, no HTTP, no socket, nothing queued to send."""
    def boom(*a, **k):
        raise AssertionError("import tried to reach the network")
    monkeypatch.setattr(scraper, "fetch_page", boom)
    monkeypatch.setattr(scraper, "_get", boom)
    monkeypatch.setattr(http_util, "post_json", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    for name in JOB_BOARDS + DIRECTORIES + ("contacts_followup.csv",):
        r = client.post("/api/import/preview", json={"csv": text(name)})
        assert r.status_code == 200, (name, r.text)
        commit(client, name)
    store = client.app.state.store
    assert store.read("outbox") == [] and store.read("whatsapp_outbox") == []
    leads = client.get("/api/leads").json()
    assert leads and all(l["stage"] in ("new",) and l["consent"] is None for l in leads)
    assert all(not l.get("enriched_at") for l in leads)


def test_undo_removes_a_job_board_import_but_keeps_merged_contacts(client):
    first = commit(client, "contacts_followup.csv")
    jobs = commit(client, "naukri_team_copy.csv")
    assert jobs["summary"]["merge"] == 10 and jobs["summary"]["create"] == 30
    r = client.post(f"/api/import/{jobs['import_id']}/undo")
    assert r.status_code == 200 and r.json()["removed"] == 30
    assert len(client.get("/api/leads").json()) == 10 and first["summary"]["create"] == 10


def test_semicolon_bom_justdial_file_reads_cleanly(client):
    r = client.post("/api/import/preview", json={"csv": text("justdial_apify.csv")}).json()
    assert r["columns"][0]["column"] == "name" and r["summary"]["create"] == 40
    assert r["rows"][0]["company"] == "Shubh Labh Logistics"          # ALL CAPS + 'PVT. LTD.' cleaned
