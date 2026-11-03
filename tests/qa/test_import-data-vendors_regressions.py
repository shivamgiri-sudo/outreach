"""Regression tests for bugs found while QA-ing vendor / CRM exports (Lusha, ZoomInfo, Seamless.AI, RocketReach,
Hunter, Snov.io, HubSpot, Salesforce, Zoho CRM, Pipedrive). All names, domains and numbers are made up."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.header_map import detect_format, map_columns
from app.lead_import import read_table, run_import
from app.main import create_app

TODAY = date(2026, 11, 3)


def mapping(headers, rows=()):
    fmt = detect_format(headers)
    return fmt, {c["column"]: c["field"] for c in map_columns(headers, list(rows), fmt)}


def run(csv_text, leads=None, suppressed=()):
    headers, table = read_table(csv_text)
    fmt = detect_format(headers)
    columns = map_columns(headers, [r for _, r in table[:25]], fmt)
    leads = [] if leads is None else leads
    return leads, run_import(leads, table, columns, fmt["source"], set(suppressed), {}, today=TODAY)


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(data_file=tmp_path / "d.json"))


# ---------------------------------------------------------------- ZoomInfo
def test_zoominfo_middle_name_does_not_replace_the_full_name():
    csv = ("ZoomInfo Contact ID,First Name,Middle Name,Last Name,Job Title,Email Address,Company Name\n"
           "1200000101,Ravi,K,Sharma,HR Manager,ravi.sharma@nimbus.example,Nimbus Pvt Ltd\n"
           "1200000102,Anita,,Rao,TA Lead,anita.rao@nimbus.example,Nimbus Pvt Ltd\n")
    leads, r = run(csv)
    assert {c["contact_name"] for c in [leads[0], *leads[0].get("other_contacts", [])]} == {"Ravi Sharma", "Anita Rao"}


def test_zoominfo_contact_id_is_not_read_as_a_noida_landline():
    """'1200000101' looks like STD 120 + number; an ID column must never become a phone number."""
    csv = ("ZoomInfo Contact ID,ZoomInfo Company ID,First Name,Last Name,Email Address,Company Name\n"
           "1200000101,3400101,Ravi,Sharma,ravi.sharma@nimbus.example,Nimbus\n"
           "1200000102,3400102,Imran,Khan,imran.khan@kaveri.example,Kaveri\n")
    leads, r = run(csv)
    assert all(not lead.get("phones") and not lead.get("phone") for lead in leads)
    _, m = mapping(read_table(csv)[0], [row for _, row in read_table(csv)[1]])
    assert m["ZoomInfo Contact ID"] is None and m["ZoomInfo Company ID"] is None


def test_known_extra_columns_are_not_resniffed_into_lead_fields():
    _, m = mapping(["Apollo Contact Id", "First Name", "Email"], [{"Apollo Contact Id": "9876543210"}] * 3)
    assert m["Apollo Contact Id"] is None


def test_zoominfo_email_domain_is_not_an_email_column():
    _, m = mapping(["First Name", "Email Address", "Email Domain", "Supplemental Email", "Company Name"],
                   [{"Email Domain": "nimbus.example"}] * 3)
    assert m["Email Domain"] != "email" and m["Supplemental Email"] == "email"


# ---------------------------------------------------------------- status columns must not become emails / phones
@pytest.mark.parametrize("header,values", [
    ("Email 1 Validation", ["Valid", "Catch-all"]), ("Email 1 Total AI", ["95%", "72%"]),
    ("Email Grade", ["A", "B"]), ("Personal Email Validation", ["Valid", "Invalid"]),
    ("Email Opt Out", ["0", "1"]), ("Unsubscribed from all email", ["false", "true"]),
    ("Contact Phone 1 Total AI", ["High", "Medium"]), ("Marketing contact status", ["Marketing", "Non-marketing"]),
])
def test_vendor_status_columns_are_not_contact_fields(header, values):
    headers = ["First Name", "Last Name", "Email", header]
    _, m = mapping(headers, [{header: v} for v in values * 2])
    assert m[header] is None, (header, m[header])


def test_status_columns_do_not_create_invalid_email_warnings():
    csv = ("First Name,Last Name,Company,Email 1,Email 1 Validation,Email 1 Total AI,Email Opt Out\n"
           "Ravi,Kumar,Nimbus,ravi@nimbus.example,Valid,95%,0\n"
           "Anita,Rao,Kaveri,anita@kaveri.example,Valid,88%,0\n")
    _, r = run(csv)
    assert "invalid_email" not in r["summary"]["warnings"] and r["summary"]["create"] == 2


# ---------------------------------------------------------------- header aliases
def test_salesforce_company_account_is_the_company_not_its_size():
    fmt, m = mapping(["First Name", "Last Name", "Title", "Company / Account", "Email", "Lead Status", "Lead Owner"])
    assert m["Company / Account"] == "company" and fmt["key"] == "salesforce"


def test_seamless_linkedin_and_staff_count_columns():
    fmt, m = mapping(["First Name", "Last Name", "Company", "Email 1", "Contact LI Profile URL", "Company LI Profile Url",
                      "Company Staff Count", "Company Staff Count Range"])
    assert fmt["key"] == "seamless"
    assert m["Contact LI Profile URL"] == "linkedin_url" and m["Company LI Profile Url"] == "company_linkedin_url"
    assert m["Company Staff Count"] == m["Company Staff Count Range"] == "company_size"


def test_pipedrive_organization_address_is_an_address():
    fmt, m = mapping(["Person - Name", "Person - Email - Work", "Person - Organization", "Organization - Name",
                      "Organization - Address"])
    assert fmt["key"] == "pipedrive" and m["Organization - Address"] == "address"


def test_rocketreach_country_code():
    _, m = mapping(["Name", "Email", "Email Grade", "Country Code"])
    assert m["Country Code"] == "country"


# ---------------------------------------------------------------- the file's source is named after the right tool
@pytest.mark.parametrize("headers,key", [
    (["First Name", "Last Name", "Title", "Company", "Email 1", "Email 1 Validation", "Contact Phone 1", "Company Phone 1",
      "Contact LI Profile URL"], "seamless"),
    (["Name", "Title", "Company", "Company Domain", "Email", "Email Grade", "Personal Email", "Work Phone",
      "Mobile Phone", "Country Code"], "rocketreach"),
    (["Record ID", "First Name", "Last Name", "Email", "Phone Number", "Mobile Phone Number", "Job Title", "Company Name",
      "State/Region", "Country/Region", "Lifecycle Stage", "Lead Status", "Contact owner"], "hubspot"),
    (["First Name", "Last Name", "Title", "Company / Account", "Email", "Phone", "Mobile", "State/Province", "Lead Owner",
      "Email Opt Out", "Lead Source"], "salesforce"),
    (["Record Id", "Lead Owner", "Company", "First Name", "Last Name", "Email", "Phone", "Email Opt Out",
      "Secondary Email", "Created Time"], "zoho_crm"),
    (["Person - Name", "Person - Email - Work", "Person - Phone - Work", "Person - Phone - Mobile",
      "Person - Organization"], "pipedrive"),
])
def test_crm_and_vendor_exports_are_not_labelled_apollo_or_directory(headers, key):
    assert detect_format(headers)["key"] == key


# ---------------------------------------------------------------- a "no" in the source CRM stays a "no"
SF = ("First Name,Last Name,Company / Account,Email,Mobile,Email Opt Out,Do Not Call\n"
      "Ravi,Kumar,Nimbus Pvt Ltd,ravi.kumar@nimbus.example,+91 90000 10001,1,0\n"
      "Anita,Rao,Kaveri Ltd,anita.rao@kaveri.example,090000 10002,0,1\n"
      "Sunil,Patil,Tulsi Services,sunil.patil@tulsi.example,9000010003,0,0\n")


def test_crm_email_opt_out_row_is_skipped_not_imported():
    leads, r = run(SF)
    assert r["summary"]["skipped"] == 1 and r["skipped"][0]["row"] == 2
    assert "opted out of email" in r["skipped"][0]["reason"] and r["source_opt_outs"] == ["ravi.kumar@nimbus.example"]
    assert "ravi.kumar@nimbus.example" not in {lead.get("email") for lead in leads}


def test_crm_do_not_call_drops_the_phone_but_keeps_the_email():
    leads, r = run(SF)
    anita = next(lead for lead in leads if lead["contact_name"] == "Anita Rao")
    assert not anita.get("phone") and not anita.get("phones") and anita["email"] == "anita.rao@kaveri.example"
    assert r["summary"]["warnings"]["do_not_call"] == 1


def test_commit_puts_crm_opt_outs_on_the_do_not_contact_list_even_for_an_existing_lead(client):
    first = "First Name,Last Name,Company,Email\nRavi,Kumar,Nimbus,ravi.kumar@nimbus.example\n"
    assert client.post("/api/import/commit", json={"csv": first}).json()["summary"]["create"] == 1
    preview = client.post("/api/import/preview", json={"csv": SF}).json()
    assert preview["source_opt_outs"] == ["ravi.kumar@nimbus.example"]
    assert client.app.state.store.read("suppressed") == []                     # a dry run saves nothing
    client.post("/api/import/commit", json={"csv": SF})
    assert client.app.state.store.read("suppressed") == ["ravi.kumar@nimbus.example"]
    client.post("/api/import/commit", json={"csv": SF})                         # importing again does not duplicate it
    assert client.app.state.store.read("suppressed") == ["ravi.kumar@nimbus.example"]
    again = client.post("/api/import/preview", json={"csv": first}).json()
    assert again["summary"]["skipped"] == 1                                     # and any later file is checked against it


@pytest.mark.parametrize("value,skipped", [("true", 1), ("TRUE", 1), ("Yes", 1), ("1", 1), ("false", 0), ("0", 0),
                                           ("", 0), ("No", 0)])
def test_opt_out_values(value, skipped):
    csv = f"Email,Company,Unsubscribed from all email\nravi@nimbus.example,Nimbus,{value}\n"
    assert run(csv)[1]["summary"]["skipped"] == skipped


# ---------------------------------------------------------------- vendor's verdict on an email is kept as a warning
@pytest.mark.parametrize("header,value,flagged", [
    ("Email Status", "Invalid", True), ("Verification status", "invalid", True), ("Email 1 Validation", "Invalid", True),
    ("Email status", "not valid", True), ("Email Grade", "F", True), ("Email Status", "Undeliverable", True),
    ("Email Status", "Verified", False), ("Verification status", "accept_all", False), ("Email Grade", "A", False),
    ("Email status", "unknown", False),
])
def test_bad_email_status_is_warned_and_noted(header, value, flagged):
    csv = f"First Name,Last Name,Company,Email,{header}\nRavi,Kumar,Nimbus,ravi@nimbus.example,{value}\n"
    leads, r = run(csv)
    assert ("email_flagged" in r["summary"]["warnings"]) is flagged
    noted = any("flagged by source" in e["text"] for e in leads[0]["timeline"])
    assert noted is flagged and leads[0]["email"] == "ravi@nimbus.example"     # warned, never silently dropped
