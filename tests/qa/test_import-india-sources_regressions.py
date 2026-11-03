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


def test_scraper_headers_map_to_the_right_fields():
    """Indeed 'positionName' and Apify Maps 'categoryName' became a person's name; IndiaMART lost the company and
    used the seller's own RECEIVER_MOBILE (same on every row) as the buyer's phone, merging all buyers into one."""
    _, _, indeed = _import("positionName,company,location,salary,url,postedAt,externalApplyLink\n"
                           "Telecaller,Amla Foods,Pune,₹15000,https://in.indeed.example/viewjob?jk=1,Today,https://jobs.amla.example/1\n"
                           "Picker,Bael Retail,Pune,₹15000,https://in.indeed.example/viewjob?jk=2,Today,\n")
    assert indeed["positionName"] == "role" and indeed["externalApplyLink"] == "job_link" and indeed["url"] == "job_link"
    _, _, maps = _import("title,categoryName,phone,website,placeId\nAmla Foods,Caterer,080-41234567,amla.example,x1\n"
                         "Bael Retail,Store,080-41234568,bael.example,x2\n")
    assert maps["categoryName"] == "sector"
    _, _, naukri = _import("title,companyName,jdURL,location\nTelecaller,Amla Foods,https://www.naukri.com/j-1,Pune\n"
                           "Picker,Bael Retail,https://www.naukri.com/j-2,Pune\n")
    assert naukri["jdURL"] == "job_link"
    leads, result, im = _import(
        "SENDER_NAME,SENDER_MOBILE,SENDER_COMPANY,SENDER_CITY,QUERY_PRODUCT_NAME,QUERY_MCAT_NAME,RECEIVER_MOBILE\n"
        "Pinky Testwala,+91-9000010001,Amla Foods Pvt Ltd,Pune,AI Interview Software,HR Software,+91-9000099999\n"
        "Bunty Sampleji,+91-9000010002,Bael Retail LLP,Pune,AI Interview Software,HR Software,+91-9000099999\n")
    assert im["SENDER_COMPANY"] == "company" and im["RECEIVER_MOBILE"] is None
    assert im["QUERY_PRODUCT_NAME"] is None and im["QUERY_MCAT_NAME"] is None
    assert result["summary"]["create"] == 2
    assert {(l["company"], l["contact_name"], l["phone"]) for l in leads} == {
        ("Amla Foods", "Pinky Testwala", "+919000010001"), ("Bael Retail", "Bunty Sampleji", "+919000010002")}
    _, _, trade = _import("Company Name,Product Name,Mobile\nAmla Foods,Uniforms,9000010001\n")
    assert trade["Product Name"] is None
