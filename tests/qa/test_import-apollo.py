"""QA: Apollo.io people and account exports through the smart import (preview/commit/undo) and quick paste.

Fixtures in tests/qa/fixtures/import-apollo/ are fictional: made-up names, *.example domains, 90000 xxxxx numbers.
  apollo_people_export.csv   - 168 rows, Apollo "Export contacts" columns: locked emails, 5 phone columns,
                               duplicate people, ALL CAPS / Hinglish names, Pvt. Ltd. suffixes, owner columns
  apollo_accounts_export.csv - 59 rows, Apollo "Export accounts" columns, 2 duplicate companies
  apollo_people_edited.csv   - the same people after an Excel round trip: BOM, ';' delimiter, renamed headers
"""
import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import lead_model, routes_import
from app.main import create_app

FIX = Path(__file__).parent / "fixtures" / "import-apollo"
OWNER = "owner@outreach.example"
LOCKED = "email_not_unlocked@domain.com"


def load(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8-sig")


PEOPLE, ACCOUNTS, EDITED = (load(n) for n in ("apollo_people_export.csv", "apollo_accounts_export.csv",
                                               "apollo_people_edited.csv"))


@pytest.fixture()
def app(tmp_path):
    return create_app(data_file=tmp_path / "d.json")


@pytest.fixture()
def client(app):
    return TestClient(app)


def post(client, path, **body):
    r = client.post(path, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def preview(client, csv_text, source="apollo", **extra):
    return post(client, "/api/import/preview", csv=csv_text, source=source, **extra)


def commit(client, csv_text, source="apollo", **extra):
    return post(client, "/api/import/commit", csv=csv_text, source=source, **extra)


def leads(client) -> list:
    return client.get("/api/leads").json()


def by_company(client) -> dict:
    return {lead["company"]: lead for lead in leads(client)}


# ------------------------------------------------------------------ format detection and column mapping
def test_people_export_is_detected_and_every_meaningful_column_is_mapped(client):
    r = preview(client, PEOPLE)
    assert r["format"]["key"] == "apollo" and r["source"] == "apollo"
    m = r["mapping"]
    expected = {
        "First Name": "first_name", "Last Name": "last_name", "Title": "title", "Company Name": "company",
        "Email": "email", "Secondary Email": "email", "Tertiary Email": "email",
        "Work Direct Phone": "phone", "Mobile Phone": "phone", "Corporate Phone": "phone", "Home Phone": "phone",
        "Other Phone": "phone", "Company Phone": "phone", "# Employees": "company_size", "Industry": "sector",
        "Person Linkedin Url": "linkedin_url", "Company Linkedin Url": "company_linkedin_url", "Website": "website",
        "City": "city", "State": "state", "Country": "country", "Company Address": "address",
    }
    assert {k: m[k] for k in expected} == expected
    # Apollo's own bookkeeping never becomes lead data (owner emails, ids, statuses, social and funding columns)
    for column in ("Contact Owner", "Account Owner", "Email Status", "Apollo Contact Id", "Apollo Account Id",
                   "Facebook Url", "Twitter Url", "Stage", "Lists", "Last Contacted", "Do Not Call", "Keywords",
                   "Technologies", "Annual Revenue", "Total Funding", "Subsidiary of (Organization ID)",
                   "Primary Email Last Verified At", "Email Confidence"):
        assert m[column] == "ignore", column


def test_account_export_maps_company_columns(client):
    r = preview(client, ACCOUNTS)
    m = r["mapping"]
    assert r["format"]["key"] == "apollo"
    assert {k: m[k] for k in ("Company Name", "# Employees", "Industry", "Website", "Company Linkedin Url",
                              "Company City", "Company State", "Company Country", "Company Address",
                              "Company Phone")} == {
        "Company Name": "company", "# Employees": "company_size", "Industry": "sector", "Website": "website",
        "Company Linkedin Url": "company_linkedin_url", "Company City": "city", "Company State": "state",
        "Company Country": "country", "Company Address": "address", "Company Phone": "phone"}
    for column in ("Logo Url", "Apollo Account Id", "SIC Codes", "NAICS Codes", "Short Description", "Founded Year",
                   "Primary Intent Topic", "Account Stage"):
        assert m[column] == "ignore", column


def test_edited_reexport_with_bom_semicolons_and_renamed_headers_still_maps(client):
    r = preview(client, EDITED)
    m = r["mapping"]
    assert {k: m[k] for k in ("Name", "Designation", "Organisation", "Official Email", "Direct No.", "Mobile No.",
                              "Board Line", "Emp Size", "Company Website", "LinkedIn Profile", "Company LinkedIn",
                              "Location", "Sector", "Remarks")} == {
        "Name": "contact_name", "Designation": "title", "Organisation": "company", "Official Email": "email",
        "Direct No.": "phone", "Mobile No.": "phone", "Board Line": "phone", "Emp Size": "company_size",
        "Company Website": "website", "LinkedIn Profile": "linkedin_url", "Company LinkedIn": "company_linkedin_url",
        "Location": "location", "Sector": "sector", "Remarks": "notes"}
    assert m["Contact Owner"] == m["Apollo ID"] == m["Sr No"] == "ignore"
    assert r["summary"]["rows"] == 63 and r["summary"]["rejected"] == 0
    first = r["rows"][0]
    assert (first["contact_name"], first["company"], first["city"], first["state"], first["country"]) == (
        "Lakshmi Singh", "Acme Retail", "Bengaluru", "Karnataka", "IN")
    assert first["company_size_bucket"] == "201-500"


# ------------------------------------------------------------------ people export: values
def test_people_preview_summary_dedupes_and_rejects_only_the_empty_locked_row(client):
    s = preview(client, PEOPLE)["summary"]
    assert s["rows"] == 168
    assert s["create"] == 40                     # one lead per company, colleagues become extra contacts
    assert s["duplicates_in_file"] == 127 and s["contacts_added"] == 119
    assert s["rejected"] == 1                    # "Unknown Prospect": locked email and nothing else
    assert s["warnings"]["invalid_email"] == 17  # every email_not_unlocked@domain.com is flagged


def test_people_commit_values(client):
    r = commit(client, PEOPLE)
    assert r["summary"]["create"] == 40 and len(r["created_ids"]) == 40
    all_leads = leads(client)
    assert len(all_leads) == 40
    every_email = [e for lead in all_leads for e in (lead.get("emails") or [])]
    every_email += [c.get("email") for lead in all_leads for c in lead.get("other_contacts") or [] if c.get("email")]
    assert LOCKED not in every_email and OWNER not in every_email
    assert len(every_email) == len(set(every_email))                 # nobody stored twice
    for lead in all_leads:
        assert lead["domain"] not in ("domain.com", "facebook.com", "twitter.com", "linkedin.com", "amazonaws.com")
        assert lead["stage"] == "new" and lead["consent"] is None    # import never grants consent
        for url in (lead.get("linkedin_url"), lead.get("company_linkedin_url"), lead.get("website")):
            assert not url or url.startswith(("https://", "http://"))
        if lead.get("phone_type") == "mobile":                       # Bengaluru 080 landlines are not mobiles
            assert lead["phone"][3] in "6789" and not lead["phone"].startswith("+9180")
    acme = by_company(client)["Acme Retail"]
    assert acme["company_legal"] == "Acme Retail Pvt. Ltd."
    assert (acme["contact_name"], acme["title"], acme["seniority"], acme["department"]) == (
        "Ravi Kumar", "HR Head", "head", "HR")                       # "RAVI","KUMAR" combined and cased
    assert acme["phone"] == "+919000020001" and acme["phone_type"] == "mobile"   # Mobile beats Work Direct landline
    assert set(acme["phones"]) == {"+918040001500", "+919000020001", "+918040001000"}
    assert (acme["company_size"], acme["company_size_bucket"]) == (350, "201-500")
    assert acme["linkedin_url"] == "https://www.linkedin.com/in/ravi-kumar-000demo"     # http:// export -> https
    assert acme["company_linkedin_url"] == "https://www.linkedin.com/company/acme-retail"
    assert (acme["city"], acme["state"], acme["country"], acme["pincode"]) == ("Bengaluru", "Karnataka", "IN", "560010")
    assert {c["contact_name"] for c in acme["other_contacts"]} >= {"Rekha Khan", "Rohit Narayanan", "Vikram Bose"}


def test_company_size_buckets_from_employees(client):
    commit(client, PEOPLE)
    got = {c: (lead.get("company_size"), lead.get("company_size_bucket")) for c, lead in by_company(client).items()}
    assert got["Acme Retail"] == (350, "201-500")
    assert got["Nimbus Logistics (India)"] == (820, "501-1000")
    assert got["Orion Softworks"] == (160, "51-200")


def test_domain_comes_from_website_then_company_email_never_free_mail(client):
    commit(client, PEOPLE)
    got = by_company(client)
    assert got["Acme Retail"]["domain"] == "acmeretail.example"                       # from the Website column
    sg = got["Shree Ganesh Traders"]                                                  # no website in any row
    assert sg["domain"] == "shreeganeshtraders.example" and sg.get("website_guess") is True
    royal = got["Royal Caterers"]          # first row: salma's gmail; colleagues later give the company address
    assert royal["domain"] in ("", "royalcaterers.example")
    assert all(lead["domain"] not in ("gmail.com", "googlemail.com") for lead in leads(client))


def test_linkedin_urls_are_rebuilt_as_https_linkedin_links_only(client):
    csv_text = ("First Name,Last Name,Company Name,Email,Person Linkedin Url,Company Linkedin Url\n"
                "Asha,Rao,Gokul Dairy,asha@gokuldairy.example,javascript:alert(1)//linkedin.com/in/asha-rao,"
                "ftp://linkedin.com/company/gokul\n"
                "Bunty,Singh,Hari Om Transport,bunty@hariom.example,not a url,http://www.linkedin.com/company/hari-om\n")
    rows = preview(client, csv_text)["rows"]
    assert len(rows) == 2
    commit(client, csv_text)
    got = by_company(client)
    # since qa/import-linkedin-tools the link must START with the LinkedIn address: junk before it means no link at all
    assert not got["Gokul Dairy"].get("linkedin_url")
    assert not got["Gokul Dairy"].get("company_linkedin_url")          # ftp:// is not a web link either
    assert not got["Hari Om Transport"].get("linkedin_url")
    assert got["Hari Om Transport"]["company_linkedin_url"] == "https://www.linkedin.com/company/hari-om"


# ------------------------------------------------------------------ accounts, then people, then the edited re-export
def test_accounts_then_people_then_reexport_never_duplicate_a_company(client):
    a = commit(client, ACCOUNTS)
    assert a["summary"]["create"] == 57 and a["summary"]["duplicates_in_file"] == 2
    acme_before = by_company(client)["Acme Retail"]
    assert (acme_before["phone"], acme_before["company_size_bucket"]) == ("+918040001000", "1000+")

    p = commit(client, PEOPLE)
    assert p["summary"]["merge"] + p["summary"]["unchanged"] > 0
    acme = by_company(client)["Acme Retail"]
    assert acme["id"] == acme_before["id"]
    assert acme["company_size"] == acme_before["company_size"]       # 1100 from accounts is not overwritten by 350
    assert acme["contact_name"] == "Ravi Kumar"                      # empty contact filled
    assert "+918040001000" in acme["phones"]

    e = commit(client, EDITED, source="apollo-edited")
    assert e["summary"]["rejected"] == 0
    domains = [lead["domain"] for lead in leads(client) if lead["domain"]]
    assert len(domains) == len(set(domains))                         # still one lead per company


def test_merge_never_overwrites_what_we_already_have(client):
    commit(client, "Company,Contact Name,Title,Email,Phone,Website\n"
                   "Acme Retail,Meera Iyer,Talent Partner,meera@acmeretail.example,+91 90000 99999,acmeretail.example\n",
           source="manual")
    before = by_company(client)["Acme Retail"]
    r = commit(client, PEOPLE)
    after = by_company(client)["Acme Retail"]
    for field in ("contact_name", "title", "email", "phone", "website", "domain"):
        assert after[field] == before[field], field
    assert after["field_sources"]["contact_name"] == "manual"
    assert after["company_size"] == 350 and after["field_sources"]["company_size"] == "apollo"   # blank filled
    assert "Ravi Kumar" in {c["contact_name"] for c in after["other_contacts"]}
    assert r["summary"]["merge"] >= 1


@pytest.mark.parametrize("stage", ["unsubscribed", "disqualified"])
def test_closed_leads_stay_closed_when_apollo_brings_them_back(app, client, stage):
    commit(client, PEOPLE)
    acme = by_company(client)["Acme Retail"]
    app.state.store.update_lead(acme["id"], lambda lead: {**lead, "stage": stage})
    r = commit(client, PEOPLE)
    again = by_company(client)["Acme Retail"]
    assert again["stage"] == stage and again["consent"] is None
    assert r["summary"]["create"] == 0


def test_reimporting_the_same_export_creates_nothing(client):
    commit(client, PEOPLE)
    r = commit(client, PEOPLE)
    assert r["summary"]["create"] == 0 and r["summary"]["fields_filled"] == 0
    assert len(leads(client)) == 40


def test_do_not_contact_people_in_the_export_are_skipped(app, client):
    app.state.store.update("suppressed", lambda rows: [*rows, "ravi.kumar@acmeretail.example"])
    r = commit(client, PEOPLE)
    assert r["summary"]["skipped"] >= 1
    every = [e for lead in leads(client) for e in lead.get("emails") or []]
    every += [c.get("email") for lead in leads(client) for c in lead.get("other_contacts") or []]
    assert "ravi.kumar@acmeretail.example" not in every


# ------------------------------------------------------------------ provenance, history, undo, multi-day
def test_provenance_and_import_history(client):
    r = commit(client, PEOPLE, source="Apollo Oct 2026 (HR heads)")
    lead = by_company(client)["Acme Retail"]
    assert lead["source"] == "Apollo Oct 2026 (HR heads)" and lead["import_id"] == r["import_id"]
    assert set(lead["field_sources"].values()) == {"Apollo Oct 2026 (HR heads)"}
    assert lead["timeline"][0]["text"] == "Imported from Apollo Oct 2026 (HR heads)"
    assert all(c["source"] == "Apollo Oct 2026 (HR heads)" for c in lead["other_contacts"])
    hist = client.get("/api/import/history").json()
    assert hist[0]["id"] == r["import_id"] and hist[0]["format"] == "Apollo export" and hist[0]["created"] == 40


def test_preview_saves_nothing(client):
    preview(client, PEOPLE)
    assert leads(client) == [] and client.get("/api/import/history").json() == []


def test_undo_removes_only_untouched_created_leads(app, client):
    r = commit(client, PEOPLE)
    acme = by_company(client)["Acme Retail"]
    app.state.store.update_lead(acme["id"], lambda lead: {**lead, "notes": "called reception"})
    u = post(client, f"/api/import/{r['import_id']}/undo")
    assert u["removed"] == 39 and len(u["kept"]) == 1 and u["kept"][0]["company"] == "Acme Retail"
    assert [lead["company"] for lead in leads(client)] == ["Acme Retail"]
    again = client.post(f"/api/import/{r['import_id']}/undo")
    assert again.status_code == 400


def test_multi_day_undo_of_day_one_keeps_leads_the_day_three_reexport_merged_into(client, monkeypatch):
    clock = {"now": "2026-10-01T05:00:00+00:00"}
    for mod in (lead_model, routes_import):
        monkeypatch.setattr(mod, "now_iso", lambda: clock["now"])
    from app import lead_import
    monkeypatch.setattr(lead_import, "now_iso", lambda: clock["now"])
    day1 = commit(client, ACCOUNTS)
    clock["now"] = "2026-10-03T06:30:00+00:00"
    day3 = commit(client, PEOPLE)
    assert day3["summary"]["merge"] > 0
    hist = client.get("/api/import/history").json()
    assert [h["at"][:10] for h in hist] == ["2026-10-03", "2026-10-01"]
    u = post(client, f"/api/import/{day1['import_id']}/undo")
    kept_ids = {k["lead_id"] for k in u["kept"]}
    assert kept_ids == set(day3["merged_ids"]) & set(day1["created_ids"])
    assert u["removed"] == 57 - len(kept_ids)
    assert by_company(client)["Acme Retail"]["contact_name"] == "Ravi Kumar"


# ------------------------------------------------------------------ quick paste (/api/leads/import)
def test_quick_paste_people_export(client):
    r = post(client, "/api/leads/import", csv=PEOPLE, source="apollo")
    assert r == {"created": 148, "duplicates": 5, "rejected": 4, "merged": 11}   # one lead per person, not per company
    all_leads = leads(client)
    emails = [lead["email"] for lead in all_leads if lead.get("email")]
    assert LOCKED not in emails and OWNER not in emails and len(emails) == len(set(emails))
    assert all(lead["domain"] not in ("domain.com", "facebook.com", "amazonaws.com") for lead in all_leads)
    assert all(lead["stage"] == "new" and lead["consent"] is None for lead in all_leads)
    again = post(client, "/api/leads/import", csv=PEOPLE, source="apollo")
    # limitation (see report): Harish's row was merged into Royal Caterers by filling its domain, his own email was
    # dropped, so a second paste still creates him once
    assert again["created"] == 1 and again["duplicates"] == 163


def test_quick_paste_accounts_and_edited(client):
    a = post(client, "/api/leads/import", csv=ACCOUNTS, source="apollo")
    # limitation (see report): quick paste keys only on email, mobile or website, so 8 landline-only accounts
    # without a website are rejected (the smart import keeps all 57 companies)
    assert a == {"created": 49, "duplicates": 2, "rejected": 8, "merged": 0}
    e = post(client, "/api/leads/import", csv=EDITED, source="apollo")
    assert e["created"] + e["duplicates"] + e["rejected"] + e["merged"] == 63


def test_fixtures_are_fictional():
    for text in (PEOPLE, ACCOUNTS, EDITED):
        delim = ";" if text.splitlines()[0].count(";") > text.splitlines()[0].count(",") else ","
        for row in csv.reader(io.StringIO(text), delimiter=delim):
            for cell in row:
                if "@" in cell and "." in cell.split("@")[-1]:
                    host = cell.strip().split("@")[-1].lower()
                    assert host.endswith((".example", ".invalid")) or host in (
                        "domain.com", "gmail.com", "gmial.com", "googlemail.com", "yahoo.co.in", "rediffmail.com"), cell
