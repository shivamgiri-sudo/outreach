"""Column mapping for real export layouts, fuzzy fallback, value sniffing and saved mappings. Made-up data only."""
from datetime import date

import pytest

from app.header_map import detect_format, loose_header, map_columns
from app.lead_import import extract_record, read_table

APOLLO = ["First Name", "Last Name", "Title", "Company", "Company Name for Emails", "Email", "Work Direct Phone",
          "Mobile Phone", "Corporate Phone", "Person Linkedin Url", "Website", "Company Linkedin Url", "# Employees",
          "Industry", "City", "State", "Country"]
SALES_NAV = ["profileUrl", "fullName", "firstName", "lastName", "companyName", "title", "companyUrl", "location",
             "industry", "linkedInProfileUrl", "vmid", "query", "timestamp"]
EVABOOT = ["First Name", "Last Name", "Job Title", "Company Name", "Linkedin URL", "Sales Navigator URL", "Location",
           "Company Location", "Industry", "Email", "Company Size"]
LUSHA = ["First Name", "Last Name", "Job Title", "Company Name", "Company Domain", "Email 1", "Email 2", "Phone 1",
         "Phone 2", "LinkedIn URL", "Country", "City", "Company Size", "Company Industry"]
ZOOMINFO = ["ZoomInfo Contact ID", "First Name", "Last Name", "Job Title", "Email Address", "Direct Phone Number",
            "Mobile phone", "Company Name", "Website", "Company HQ Phone", "Employee Range", "Primary Industry",
            "Person City", "Person State", "Country", "LinkedIn Contact Profile URL"]
HUNTER = ["Domain name", "Organization", "Email address", "First name", "Last name", "Position", "Phone number",
          "LinkedIn URL", "Confidence score", "Number of sources"]
NAUKRI = ["Company Name", "Job Title", "Location", "Experience", "Salary", "Posted On", "Openings", "Job Link"]
INDIAMART = ["Firm Name", "Contact Person", "Mobile No.", "Address", "City", "Category", "GST No"]
MAPS = ["name", "phone", "website", "address", "category"]
TEAM = ("Company Name,Contact Person,Designation,Contact Number,Email ID,Open Position,City,Company Sector,Job Portal Source,"
        "Job Posting Link,Posting Date,Days Live,No. of Openings,Position Level,Caller Name,Call Outcome,Demo Date & Time,"
        "Lead Status,Next Follow-up Date,Remarks,Company Size (employees),LinkedIn Profile URL,Contact Confidence,Status,"
        "Remarks (2)").split(",")
DECISION_MAKERS = ["Company", "Name", "Designation (as shown)", "LinkedIn Profile URL", "Where I found it", "Confidence",
                   "Fits 50–400?"]


def mapped(headers, rows=()):
    fmt = detect_format(headers)
    return fmt, {c["column"]: c["field"] for c in map_columns(headers, list(rows), fmt)}


def test_apollo_export():
    fmt, m = mapped(APOLLO)
    assert fmt["key"] == "apollo" and fmt["source"] == "apollo"
    assert m["First Name"] == "first_name" and m["Last Name"] == "last_name" and m["Title"] == "title"
    assert m["Company"] == m["Company Name for Emails"] == "company"
    assert m["Work Direct Phone"] == m["Mobile Phone"] == m["Corporate Phone"] == "phone"
    assert m["Person Linkedin Url"] == "linkedin_url" and m["Company Linkedin Url"] == "company_linkedin_url"
    assert m["# Employees"] == "company_size" and m["Industry"] == "sector" and m["Website"] == "website"
    assert (m["City"], m["State"], m["Country"]) == ("city", "state", "country")


def test_apollo_company_column_beats_company_name_for_emails():
    cols = map_columns(APOLLO, [], detect_format(APOLLO))
    conf = {c["column"]: c["confidence"] for c in cols}
    assert conf["Company"] > conf["Company Name for Emails"]


@pytest.mark.parametrize("headers", [SALES_NAV, EVABOOT])
def test_sales_navigator_style_exports(headers):
    fmt, m = mapped(headers)
    assert fmt["key"] == "sales_navigator"
    assert "title" in m.values() and "company" in m.values() and "location" in m.values()
    assert m.get("profileUrl", m.get("Linkedin URL")) == "linkedin_url"
    assert m.get("vmid") is None and m.get("Sales Navigator URL") is None


def test_phantombuster_company_url_with_linkedin_values_becomes_company_linkedin():
    rows = [{"companyUrl": "https://www.linkedin.com/company/acme-example"},
            {"companyUrl": "https://www.linkedin.com/company/beta-example"}]
    assert mapped(SALES_NAV, rows)[1]["companyUrl"] == "company_linkedin_url"


def test_lusha_zoominfo_and_hunter_exports():
    fmt, m = mapped(LUSHA)
    assert fmt["key"] == "lusha" and m["Email 1"] == m["Email 2"] == "email" and m["Phone 2"] == "phone"
    assert m["Company Domain"] == "website" and m["Company Industry"] == "sector"
    fmt, m = mapped(ZOOMINFO)
    assert fmt["key"] == "zoominfo" and m["Direct Phone Number"] == "phone" and m["Employee Range"] == "company_size"
    assert m["Person City"] == "city" and m["LinkedIn Contact Profile URL"] == "linkedin_url"
    fmt, m = mapped(HUNTER)
    assert fmt["key"] == "hunter" and m["Position"] == "title" and m["Organization"] == "company"
    assert m["Domain name"] == "website" and m["Number of sources"] is None and m["Confidence score"] is None


def test_job_board_export_maps_job_title_to_the_opening_not_the_person():
    fmt, m = mapped(NAUKRI)
    assert fmt["key"] == "job_board"
    assert m["Job Title"] == "role" and m["Openings"] == "open_roles" and m["Posted On"] == "posted_on"
    assert m["Location"] == "location" and m["Job Link"] == "job_link" and m["Salary"] is None


def test_directory_and_google_maps_name_is_the_business():
    fmt, m = mapped(INDIAMART)
    assert fmt["key"] == "directory"
    assert m["Firm Name"] == "company" and m["Contact Person"] == "contact_name" and m["Mobile No."] == "phone"
    assert m["Address"] == "address" and m["Category"] == "sector"
    fmt, m = mapped(MAPS)
    assert fmt["key"] == "google_maps" and m["name"] == "company" and m["category"] == "sector"


def test_team_sheet_tabs():
    fmt, m = mapped(TEAM)
    assert fmt["key"] == "team_leads"
    assert m["Contact Number"] == "phone" and m["Email ID"] == "email" and m["Open Position"] == "role"
    assert m["No. of Openings"] == "open_roles" and m["Days Live"] == "job_age_days" and m["Lead Status"] == "lead_status"
    assert m["Remarks"] == m["Remarks (2)"] == m["Status"] == "notes" and m["Caller Name"] is None
    fmt, m = mapped(DECISION_MAKERS)
    assert fmt["key"] == "team_decision_makers" and m["Designation (as shown)"] == "title"
    assert m["Where I found it"] == "source_detail"


def test_unknown_headers_use_keywords_fuzzy_match_and_values():
    headers = ["Organisation", "E-mail", "Mob", "Web Site", "Town", "Head count", "Hiring For", "Desigantion", "Contact"]
    rows = [{"Contact": "Ravi Kumar", "Desigantion": "HR Head"}, {"Contact": "Anita Rao", "Desigantion": "Founder"}]
    fmt, m = mapped(headers, rows)
    assert fmt["name"] == "Unrecognised layout" and fmt["key"].startswith("custom:")
    assert m["Organisation"] == "company" and m["E-mail"] == "email" and m["Mob"] == "phone"
    assert m["Web Site"] == "website" and m["Town"] == "city" and m["Head count"] == "company_size"
    assert m["Hiring For"] == "role" and m["Desigantion"] == "title"          # typo found by fuzzy match
    assert m["Contact"] == "contact_name"                                       # values are names, not numbers


def test_a_badly_named_column_full_of_emails_is_read_as_email():
    rows = [{"Column 3": "ravi@acme.example"}, {"Column 3": "anita@beta.example"}, {"Column 3": "sam@gamma.example"}]
    cols = map_columns(["Column 3"], rows, detect_format(["Column 3"]))
    assert cols[0]["field"] == "email" and "values look like" in cols[0]["how"] and cols[0]["confidence"] < 1


def test_saved_mapping_and_your_choices_win():
    fmt = detect_format(["Org", "Who"])
    cols = map_columns(["Org", "Who"], [], fmt, saved={loose_header("Org"): "company"}, overrides={"Who": "ignore"})
    assert [(c["field"], c["how"]) for c in cols] == [("company", "saved mapping"), (None, "you chose")]


def test_header_matching_ignores_case_space_and_punctuation():
    m = mapped(["  E-MAIL ID ", "company_name", "PHONE-NO.", "linkedIn_profile_URL"])[1]
    assert list(m.values()) == ["email", "company", "phone", "linkedin_url"]


def test_first_and_last_name_columns_are_combined_into_one_person():
    headers, table = read_table("First Name,Last Name,Company\nRAVI,KUMAR,Acme Retail Pvt Ltd\n")
    cols = map_columns(headers, [r for _, r in table], detect_format(headers))
    rec, _ = extract_record(table[0][1], cols, date(2026, 11, 3))
    assert rec["contact_name"] == "Ravi Kumar" and rec["company"] == "Acme Retail"
    assert rec["company_legal"] == "Acme Retail Pvt Ltd"


def test_read_table_handles_tabs_semicolons_bom_and_duplicate_headers():
    headers, table = read_table("﻿Company\tEmail\tEmail\nAcme\ta@acme.example\tb@acme.example\n\t\t\n")
    assert headers == ["Company", "Email", "Email (2)"] and len(table) == 1
    headers, table = read_table("Company;City\nAcme;Pune\n")
    assert table[0] == (2, {"Company": "Acme", "City": "Pune"})
    with pytest.raises(ValueError):
        read_table("")


def test_plain_sheet_with_mobile_phone_is_not_a_hubspot_export():
    """QA sweep: a 300-row team sheet with a "Mobile Phone" column was detected as HubSpot, so every lead's source
    (Dashboard 'Where leads come from', Insights 'By lead source') said HubSpot."""
    plain = ["Company Name", "First Name", "Last Name", "Job Title", "Email", "Mobile Phone", "Website", "City", "State",
             "Country", "Industry", "Employees"]
    assert detect_format(plain)["source"] != "hubspot"
    real = ["Record ID", "First Name", "Last Name", "Email", "Phone Number", "Mobile Phone Number", "Lifecycle Stage",
            "Company Name", "Website URL", "City", "State/Region", "Country/Region"]
    assert detect_format(real)["key"] == "hubspot"
