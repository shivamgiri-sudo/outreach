"""QA: importing exports from data vendors (Lusha, ZoomInfo, Seamless.AI, RocketReach, Hunter, Snov.io) and CRMs
(HubSpot, Salesforce, Zoho CRM, Pipedrive). Fixtures live in tests/qa/fixtures/import-data-vendors/ and are fictional:
60 made-up people at 30 made-up companies on *.example domains, with fake 90000/90001 numbers, spread over 11 files so
the same person turns up in several vendors with a different email, phone format or title."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.header_map import detect_format, map_columns
from app.hygiene_report import duplicate_clusters, report
from app.lead_import import read_table, run_import
from app.main import create_app

FIX = Path(__file__).parent / "fixtures" / "import-data-vendors"
TODAY = date(2026, 11, 3)
ORDER = ["lusha_prospecting", "zoominfo_contacts", "seamless_ai", "rocketreach", "hunter_domain_search",
         "hunter_email_finder", "snov_prospects", "hubspot_contacts", "salesforce_leads_report", "zoho_leads",
         "pipedrive_people"]


def text(name):
    return (FIX / f"{name}.csv").read_bytes().decode("utf-8")


def prepare(name):
    headers, table = read_table(text(name))
    fmt = detect_format(headers)
    return headers, table, fmt, map_columns(headers, [r for _, r in table[:25]], fmt)


def run(name, leads=None, at=None):
    _, table, fmt, columns = prepare(name)
    leads = [] if leads is None else leads
    return leads, run_import(leads, table, columns, fmt["source"], set(), {}, today=TODAY, at=at)


def person(leads, name):
    """The lead (or other contact) called `name`, with the company lead it sits on."""
    for lead in leads:
        if lead.get("contact_name") == name:
            return lead, lead
        for other in lead.get("other_contacts") or []:
            if other.get("contact_name") == name:
                return other, lead
    raise AssertionError(f"{name} not found")


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(data_file=tmp_path / "d.json"))


# ---------------------------------------------------------------- every format: detection, mapping, row counts
FORMATS = {
    # file: (format key, rows read, {column: field} that must hold, columns that must stay unimported)
    "lusha_prospecting": ("lusha", 31, {"Email 1": "email", "Email 2": "email", "Phone 1": "phone", "Phone 2": "phone",
                                        "Company Domain": "website", "Company Industry": "sector"},
                          ["Email 1 Type", "Phone 1 Type", "Phone 2 Type"]),
    "zoominfo_contacts": ("zoominfo", 30, {"Direct Phone Number": "phone", "Company HQ Phone": "phone",
                                           "Mobile phone": "phone", "Employee Range": "company_size",
                                           "LinkedIn Contact Profile URL": "linkedin_url", "Person City": "city"},
                          ["ZoomInfo Contact ID", "ZoomInfo Company ID", "Middle Name", "Salutation",
                           "Contact Accuracy Score", "Management Level"]),
    "seamless_ai": ("seamless", 25, {"Email 1": "email", "Contact Phone 1": "phone", "Company Phone 1": "phone",
                                     "Contact LI Profile URL": "linkedin_url", "Contact Country": "country",
                                     "Company Staff Count Range": "company_size"},
                    ["Email 1 Validation", "Email 1 Total AI", "Contact Phone 1 Total AI"]),
    "rocketreach": ("rocketreach", 15, {"Name": "contact_name", "Personal Email": "email", "Work Phone": "phone",
                                        "Mobile Phone": "phone", "Location": "location", "Country Code": "country"},
                    ["Email Grade"]),
    "hunter_domain_search": ("hunter", 5, {"Email address": "email", "Position": "title", "Organization": "company",
                                           "Domain name": "website", "Phone number": "phone"},
                             ["Type", "Confidence score", "Number of sources", "Verification status",
                              "Last verification date"]),
    "hunter_email_finder": ("hunter", 10, {"Email address": "email", "Domain name": "website"},
                            ["Confidence score", "Verification status"]),
    "snov_prospects": ("snov", 15, {"Email": "email", "Position": "title", "Company URL": "website",
                                    "Social URL": "linkedin_url", "Full name": "contact_name"}, ["Email status"]),
    "hubspot_contacts": ("hubspot", 15, {"Phone Number": "phone", "Mobile Phone Number": "phone",
                                         "Website URL": "website", "State/Region": "state", "Country/Region": "country",
                                         "Number of Employees": "company_size"},
                         ["Record ID", "Unsubscribed from all email", "Lifecycle Stage", "Contact owner"]),
    "salesforce_leads_report": ("salesforce", 13, {"Company / Account": "company", "Mobile": "phone",
                                                   "State/Province": "state", "No. of Employees": "company_size",
                                                   "Lead Source": "source_detail"},
                                ["Email Opt Out", "Do Not Call", "Lead Owner"]),
    "zoho_leads": ("zoho_crm", 10, {"Secondary Email": "email", "Mobile": "phone", "Website": "website",
                                    "Lead Status": "lead_status"}, ["Record Id", "Email Opt Out", "Lead Owner"]),
    "pipedrive_people": ("pipedrive", 11, {"Person - Name": "contact_name", "Person - Email - Work": "email",
                                           "Person - Phone - Mobile": "phone", "Person - Organization": "company",
                                           "Organization - Address": "address"}, ["Person - Label", "Person - Owner"]),
}


@pytest.mark.parametrize("name", list(FORMATS))
def test_vendor_export_is_recognised_and_mapped(name):
    key, rows, must, unmapped = FORMATS[name]
    headers, table, fmt, columns = prepare(name)
    by = {c["column"]: c for c in columns}
    assert fmt["key"] == key and fmt["source"] and len(table) == rows
    for column, field in must.items():
        assert by[column]["field"] == field, (column, by[column])
        assert 0.7 <= by[column]["confidence"] <= 1.0 and by[column]["how"]
    for column in unmapped:
        assert by[column]["field"] is None, (column, by[column])
    assert all(c["field"] is None or c["confidence"] > 0 for c in columns)
    assert not [h for h in headers if h.startswith("﻿")]


@pytest.mark.parametrize("name", list(FORMATS))
def test_every_row_is_accounted_for_and_no_junk_warnings(name):
    leads, r = run(name)
    s = r["summary"]
    assert s["rows"] == s["create"] + s["merge"] + s["unchanged"] + s["duplicates_in_file"] + s["rejected"] + s["skipped"]
    assert not {"invalid_email", "invalid_phone", "junk_phone", "foreign_phone"} & set(s["warnings"]), s["warnings"]
    for lead in leads:
        assert lead["source"] == FORMATS[name][0] and lead["field_sources"]
        assert all(p.startswith("+91") and len(p) == 13 for p in lead.get("phones") or []), lead.get("phones")
        assert lead["country"] in ("IN", ""), lead["country"]


# ---------------------------------------------------------------- per-vendor quirks
def test_lusha_caps_names_phone_variants_blank_and_repeated_row():
    leads, r = run("lusha_prospecting")
    s = r["summary"]
    assert s["create"] == 30 and s["duplicates_in_file"] == 1 and s["rows"] == 31     # blank row dropped on read
    assert r["duplicates"][0]["matched_by"] == "same email"
    sarovar, _ = person(leads, "Anita Gupta")                                         # exported as ANITA GUPTA
    assert sarovar["company"] == "Sarovar" and sarovar["company_legal"] == "Sarovar Private Limited"
    # '+91 90001 10001', '090000 10002', '9000110003', '91-90001-10003' ... all become one E.164 shape
    numbers = {p for lead in leads for p in lead.get("phones") or []}
    assert "+919000110001" in numbers and "+919000010002" in numbers and "+919000110003" in numbers
    assert all(lead["country"] == "IN" for lead in leads)                             # 'India' -> IN
    assert s["warnings"] == {"free_mail": 7}                                          # Email 2 = personal gmail


def test_zoominfo_bom_crlf_person_location_wins_and_hq_phone_is_kept_secondary():
    raw = (FIX / "zoominfo_contacts.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    leads, r = run("zoominfo_contacts")
    assert r["summary"]["create"] == 30 and r["summary"]["warnings"] == {}
    tushar, _ = person(leads, "Tushar Das")                                           # middle name 'K', Mr.
    assert tushar["title"] == "VP HR" and tushar["phone"] == "+919000110020" and tushar["phone_type"] == "mobile"
    assert "+918041001020" in tushar["phones"] and len(tushar["phones"]) == 2         # HQ landline kept, no ID number
    # person 45: the person is in Chennai, the company HQ is in San Jose, USA -> person location wins
    us_hq = next(lead for lead in leads if lead["domain"] == "himalaya.example")
    assert us_hq["country"] == "IN" and us_hq["city"] == "Chennai" and us_hq["state"] == "Tamil Nadu"
    assert {lead["company_size_bucket"] for lead in leads} == {"201-500"}             # Employees 350 / '250 - 499'


def test_seamless_semicolon_csv_and_invalid_verdicts_become_warnings():
    assert text("seamless_ai").split("\n", 1)[0].count(";") > 10
    leads, r = run("seamless_ai")
    flagged = [w for w in r["warnings"] if w["kind"] == "email_flagged"]
    assert len(flagged) == 2 and all("Email 1 Validation: Invalid" in w["text"] for w in flagged)
    assert all(lead["email"] == lead["email"].lower() for lead in leads)              # P0-4 exported in CAPS
    assert all(lead.get("company_linkedin_url", "").startswith("https://www.linkedin.com/company/") for lead in leads)
    assert {lead["country"] for lead in leads} == {"IN"}


def test_rocketreach_location_grade_f_and_personal_only_contact():
    leads, r = run("rocketreach")
    assert r["summary"]["warnings"] == {"free_mail": 15, "email_flagged": 1}
    assert "Email Grade: F" in next(w["text"] for w in r["warnings"] if w["kind"] == "email_flagged")
    kavya, _ = person(leads, "Kavya Rao")                                             # no work email, only gmail
    assert kavya["email"].endswith("@gmail.com") and kavya["domain"] == "sarovar.example"
    assert kavya["city"] == "Ahmedabad" and kavya["state"] == "Gujarat" and kavya["country"] == "IN"


def test_hunter_domain_search_role_inboxes_and_flagged_person_attach_to_one_company():
    leads, r = run("hunter_domain_search")
    assert len(leads) == 1 and r["summary"]["contacts_added"] == 2
    banyan = leads[0]
    assert banyan["contact_name"] == "Vikram Kulkarni" and banyan["email"] == "vikram.kulkarni@banyan.example"
    assert {"hr@banyan.example", "careers@banyan.example"} <= set(banyan["emails"])
    assert {o["contact_name"] for o in banyan["other_contacts"]} == {"Tushar Menon", "Swati Joshi"}
    assert r["summary"]["warnings"] == {"role_address": 2, "email_flagged": 1}
    assert any("tushar.menon@banyan.example flagged by source" in e["text"] for e in banyan["timeline"])


def test_hunter_email_finder_not_found_row_still_keeps_the_company_domain():
    leads, r = run("hunter_email_finder")
    assert r["summary"]["create"] == 10 and r["summary"]["warnings"] == {}
    missing = next(lead for lead in leads if lead["contact_name"] == "Kavya Gupta")
    assert missing["email"] is None and missing["domain"] == "shivalik.example"


def test_snov_not_valid_status_is_flagged_and_social_url_is_the_person_profile():
    leads, r = run("snov_prospects")
    assert r["summary"]["warnings"] == {"email_flagged": 1}
    assert all(lead["linkedin_url"].startswith("https://www.linkedin.com/in/") for lead in leads)


def test_crm_exports_skip_opt_outs_drop_do_not_call_phones_and_keep_stage_new():
    _, hub = run("hubspot_contacts")
    _, sf = run("salesforce_leads_report")
    zoho_leads, zoho = run("zoho_leads")
    assert [x["row"] for x in hub["skipped"]] == [5] and hub["source_opt_outs"] == ["pooja.iyer@trident.example"]
    assert sf["source_opt_outs"] == ["deepak.patil@monsoon.example"] and sf["summary"]["warnings"]["do_not_call"] == 1
    assert zoho["summary"]["skipped"] == 1                                             # work + secondary gmail
    assert zoho["source_opt_outs"] == ["pooja.singh@vindhya.example", "pooja.singh.qa.27@gmail.com"]
    assert {lead["stage"] for lead in zoho_leads} == {"new"}                           # 'Not Contacted' is not a stage


def test_pipedrive_person_without_email_is_kept_by_phone():
    leads, r = run("pipedrive_people")
    bhavesh, _ = person(leads, "Bhavesh Thakkar")
    assert bhavesh["phone"] == "+919000330001" and bhavesh["company"] == "Kesari Udyog" and not bhavesh.get("email")
    priya, _ = person(leads, "Priya Joshi")
    assert priya["pincode"] and priya["city"] == "Noida"


# ---------------------------------------------------------------- all vendors into one store, in order, over a week
def test_all_vendors_imported_in_turn_dedupe_record_conflicts_and_suppress_opt_outs(client):
    results = {}
    for name in ORDER:
        r = client.post("/api/import/commit", json={"csv": text(name)})
        assert r.status_code == 200, r.text
        results[name] = r.json()
    leads = client.get("/api/leads").json()
    assert len(leads) == 46                                       # 30 companies + people who were not merged in
    zi = results["zoominfo_contacts"]["summary"]
    assert zi["merge"] == 20 and zi["create"] == 10               # 20-29 already known from Lusha
    first_merge = results["zoominfo_contacts"]["merges"][0]
    assert first_merge["matched_by"] in ("same phone", "same LinkedIn profile")   # different email alias, same direct line (and, since qa/import-linkedin-tools, the same profile URL)
    assert ("email", "tushar.das@nilgiri-hr.example", "tdas@nilgiri-hr.example") in {
        (c["field"], c["kept"], c["incoming"]) for c in first_merge["conflicts"]}
    assert results["hunter_email_finder"]["summary"]["unchanged"] == 10
    assert results["snov_prospects"]["summary"]["create"] == 0

    tushar, _ = person(leads, "Tushar Das")
    assert tushar["title"] == "HR Manager" and tushar["field_sources"]["title"] == "lusha"   # first source kept
    assert "tdas@nilgiri-hr.example" in tushar["emails"]                                    # new alias not lost
    assert {c["source"] for c in tushar["merge_conflicts"]} == {"zoominfo"}
    rohit, _ = person(leads, "Rohit Patil")             # rohit.patilqa32@gmail.com (RR) vs rohit.patil.qa.32@gmail.com (Zoho)
    assert {"rohit.patilqa32@gmail.com", "rohit.patil.qa.32@gmail.com"} <= set(rohit["emails"])

    suppressed = client.app.state.store.read("suppressed")
    assert set(suppressed) == {"pooja.iyer@trident.example", "deepak.patil@monsoon.example",
                               "pooja.singh@vindhya.example", "pooja.singh.qa.27@gmail.com"}
    pooja, _ = person(leads, "Pooja Iyer")
    assert pooja["stage"] == "new"                       # stage untouched, but the address can no longer be emailed

    rep = client.get("/api/hygiene/report").json()
    assert rep["total"] == 46 and rep["duplicate_clusters"] == 0       # the importer and the hygiene page agree
    assert rep["conflicts"] == sum(len(l.get("merge_conflicts") or []) for l in leads) > 50
    assert rep["emails"]["suppressed"] == 3 and rep["foreign_phones"] == 0
    assert {c["field"] for c in rep["conflict_list"]} >= {"title", "email", "city", "company_size_bucket"}

    history = client.get("/api/import/history").json()
    assert [h["format"] for h in history][::-1][:3] == ["Lusha export", "ZoomInfo export", "Seamless.AI export"]
    undo = client.post(f"/api/import/{results['zoominfo_contacts']['import_id']}/undo")
    assert undo.status_code == 200, undo.text
    out = undo.json()          # later vendors enriched some of ZoomInfo's new leads, so those are kept, with a reason
    assert out["removed"] + len(out["kept"]) == 10 and out["merged_leads_left"] == 20
    assert all(k["reason"].startswith("changed since the import") for k in out["kept"])
    assert len(client.get("/api/leads").json()) == 46 - out["removed"]


def test_same_people_imported_into_separate_lists_cluster_on_the_hygiene_page():
    """Leads saved before smart import (or by the quick paste) are found by the hygiene report's clusters."""
    lusha, _ = run("lusha_prospecting")
    zoominfo, _ = run("zoominfo_contacts")
    snov, _ = run("snov_prospects")
    clusters = duplicate_clusters(lusha + zoominfo + snov)
    why = {w for c in clusters for w in c["why"]}
    assert {"same email", "same phone"} <= why
    tushar = next(c for c in clusters if any(l["contact_name"] == "Tushar Das" for l in c["leads"]))
    assert {l["source"] for l in tushar["leads"]} == {"lusha", "zoominfo"}
    snov_ids = {l["id"] for l in snov}
    assert len([c for c in clusters if set(c["lead_ids"]) & snov_ids]) == 15        # every Snov row was a repeat


def test_multi_day_imports_keep_first_timestamps_and_go_stale_after_90_days():
    day1 = "2026-11-03T05:00:00+00:00"
    day4 = "2026-11-06T05:00:00+00:00"
    leads, _ = run("lusha_prospecting", at=day1)
    run("zoominfo_contacts", leads, at=day4)
    tushar, _ = person(leads, "Tushar Das")
    assert tushar["created_at"] == day1 and tushar["merge_conflicts"][0]["at"] == day4
    fresh = report(leads, set(), now=datetime(2026, 11, 10, tzinfo=timezone.utc))
    later = report(leads, set(), now=datetime(2026, 11, 10, tzinfo=timezone.utc) + timedelta(days=95))
    assert fresh["stale"] == 0 and later["stale"] == len(leads)
