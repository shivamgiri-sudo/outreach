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


PB_SALESNAV_HEAD = ("profileUrl,fullName,firstName,lastName,companyName,title,companyUrl,regularCompanyUrl,location,"
                    "pastExperienceCompanyName,pastExperienceCompanyUrl,pastExperienceCompanyTitle,profileImageUrl,vmid,"
                    "linkedInProfileUrl,defaultProfileUrl,searchAccountProfileName\n")


def test_previous_employer_columns_never_become_the_current_company():
    """PhantomBuster's pastExperienceCompanyName matched the 'company name' keyword rule, so a lead with no current
    position was filed under the employer they left; the scraper's profile image URL became the company website and
    the seller's own name (searchAccountProfileName) was a fallback contact name."""
    csv = PB_SALESNAV_HEAD + (
        '"https://www.linkedin.com/sales/lead/ACwAAQA00001xQa,NAME_SEARCH,q1",,Priya,Sharma,,,,,"Greater Delhi Area",'
        "Old Employer Pvt Ltd,https://www.linkedin.com/sales/company/9,HR Executive,https://media.example/img/1.jpg,"
        "ACwAAQA00001xQa,https://www.linkedin.com/in/ACwAAQA00001xQa,https://www.linkedin.com/in/priya-sharma-qa001,"
        "QA Seller\n")
    leads, _, mapping, fmt = run(csv)
    assert fmt["key"] == "sales_navigator"
    for column in ("pastExperienceCompanyName", "pastExperienceCompanyUrl", "pastExperienceCompanyTitle",
                   "profileImageUrl", "searchAccountProfileName"):
        assert mapping[column] is None, column
    lead = leads[0]
    assert lead["company"] == "" and lead["contact_name"] == "Priya Sharma"
    assert not lead.get("website") and not lead.get("domain")


def test_profile_scraper_second_job_is_not_the_current_job():
    csv = ("linkedinProfileUrl,fullName,company,companyUrl,jobTitle,company2,companyUrl2,jobTitle2,schoolUrl\n"
           "https://www.linkedin.com/in/rahul-mehta-qa002,Rahul Mehta,Deccan Pharma,,CHRO,Old Pharma,"
           "https://www.linkedin.com/company/old-pharma-qa/,HR Trainee,https://www.linkedin.com/school/fictional-qa/\n"
           "https://www.linkedin.com/in/kavya-r-qa003,Kavya R,,,,Earlier Corp,https://www.linkedin.com/company/earlier-qa,"
           "HR,https://www.linkedin.com/school/fictional-qa/\n")
    leads, _, mapping, _ = run(csv)
    assert mapping["company2"] is None and mapping["companyUrl2"] is None and mapping["jobTitle2"] is None
    by_name = {l["contact_name"]: l for l in leads}
    assert by_name["Rahul Mehta"]["company"] == "Deccan Pharma" and by_name["Rahul Mehta"]["title"] == "CHRO"
    assert not by_name["Rahul Mehta"].get("company_linkedin_url")          # old-pharma is where he used to work
    assert by_name["Kavya R"]["company"] == "" and by_name["Kavya R"]["title"] == ""


def test_dux_soup_middle_name_does_not_replace_the_full_name():
    csv = ("VisitTime,Profile,SalesProfile,First Name,Middle Name,Last Name,Company\n"
           "2026-10-28 14:03:11,https://www.linkedin.com/in/sunita-devi-qa004,"
           "https://www.linkedin.com/sales/people/ACwAAQA00004xQa,SUNITA,KUMARI,DEVI,Bharat Logistics Pvt Ltd\n"
           "2026-10-28 14:05:00,https://www.linkedin.com/in/amit-verma-qa005,,Amit,,Verma,Verma Foods\n")
    leads, _, mapping, _ = run(csv)
    assert mapping["Middle Name"] is None and mapping["SalesProfile"] is None
    assert sorted(l["contact_name"] for l in leads) == ["Amit Verma", "Sunita Devi"]


def test_extra_columns_are_not_remapped_by_their_values():
    """A known non-lead column (Sales Navigator URL, Twitter URL) was re-mapped to 'website' because its values look
    like web addresses; lemlist's lastState ('emailsSent') was read as the state."""
    csv = ("Sales Navigator Profile URL,LinkedIn Profile URL,First Name,Last Name,Company Name,Twitter URL,lastState\n"
           "https://www.linkedin.com/sales/lead/ACwAAQA00006xQa,https://www.linkedin.com/in/neha-gupta-qa006,Neha,Gupta,"
           "Zenith Infotech,https://twitter.com/neha_qa,emailsSent\n"
           "https://www.linkedin.com/sales/lead/ACwAAQA00007xQa,https://www.linkedin.com/in/raju-bhai-qa007,Raju,Bhai,"
           "Thar Solar,https://twitter.com/raju_qa,emailsOpened\n")
    leads, result, mapping, _ = run(csv)
    assert mapping["Sales Navigator Profile URL"] is None and mapping["Twitter URL"] is None
    assert mapping["lastState"] is None
    assert all(not l.get("state") for l in leads) and result["summary"]["warnings"] == {}
