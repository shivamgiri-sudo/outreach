"""Pure clean-up rules used by the smart importer. All names, numbers and domains are made up."""
from datetime import date

import pytest

from app.places import (clean_city, clean_country, clean_state, job_age_days, parse_address, parse_company_size,
                        parse_count, parse_location, size_bucket, state_for_city)
from app.standardise import (clean_company, clean_email, clean_person_name, clean_text, clean_title, combine_name,
                             company_similarity, department_of, email_alias_key, is_free_mail, is_role_email,
                             linkedin_url, names_compatible, parse_phones, person_key, registrable_domain, seniority_of,
                             website_domain)


# ---- names
@pytest.mark.parametrize("raw, clean", [
    ("MR. RAVI KUMAR", "Ravi Kumar"), ("dr. anita rao", "Anita Rao"), ("Shri R K Sharma", "R K Sharma"),
    ("Smt. Kavya Iyer", "Kavya Iyer"), ("CA Priya Menon, ACA", "Priya Menon"), ("Ms Meera DeSouza", "Meera DeSouza"),
    ("RK SHARMA", "RK Sharma"), ("o'brien", "O'Brien"), ("Isha (HR)", "Isha"), ("N/A", ""),
])
def test_person_names_lose_honorifics_and_shouting(raw, clean):
    assert clean_person_name(raw) == clean


def test_first_and_last_name_combine_and_compare_in_any_order():
    assert combine_name("ravi", "KUMAR") == "Ravi Kumar"
    assert combine_name("Ravi", "Kumar", full="Dr. Ravi Kumar") == "Ravi Kumar"
    assert person_key("KUMAR, Ravi") == person_key("ravi kumar")


def test_names_with_initials_are_compatible_but_different_people_are_not():
    assert names_compatible("Ravi K", "Ravi Kumar") and names_compatible("R. Kumar", "RAVI KUMAR")
    assert names_compatible("Ravi", "Ravi Kumar") and names_compatible("", "Anita Rao")
    assert not names_compatible("Anita Rao", "Ravi Kumar") and not names_compatible("Ravi Shah", "Ravi Kumar")


def test_clean_text_drops_placeholders_and_invisible_characters():
    assert clean_text("  Acme​   Retail ") == "Acme Retail"
    assert clean_text("-") == "" and clean_text("NULL") == "" and clean_text(None) == ""


# ---- companies
@pytest.mark.parametrize("raw, display, legal", [
    ("ACME RETAIL PVT. LTD.", "Acme Retail", "Acme Retail Pvt. Ltd."),
    ("Juniper Fragrances Private Limited", "Juniper Fragrances", "Juniper Fragrances Private Limited"),
    ("Pebble Shoes (P) Ltd", "Pebble Shoes", "Pebble Shoes (P) Ltd"),
    ("Quill Systems LLP", "Quill Systems", "Quill Systems LLP"),
    ("Orion Softworks Inc.", "Orion Softworks", "Orion Softworks Inc."),
    ("Nimbus Tools OPC Private Limited", "Nimbus Tools", "Nimbus Tools OPC Private Limited"),
    ("ICICI BANK LIMITED", "ICICI Bank", "ICICI Bank Limited"),
    ("M/s Sharma Traders", "Sharma Traders", ""),
    ("Bank of Baroda", "Bank of Baroda", ""),
])
def test_company_display_name_drops_legal_suffix_and_keeps_original(raw, display, legal):
    assert clean_company(raw) == (display, legal)


def test_company_similarity_is_token_based_and_strict():
    assert company_similarity("Acme Retail Pvt Ltd", "ACME RETAILS") >= 0.92
    assert company_similarity("Bluestone Jewellery", "Blue Stone Jewellery") >= 0.92
    assert company_similarity("Acme", "Acme Technologies") >= 0.92        # generic word only
    assert company_similarity("Sharma", "Sharma Steels") < 0.92
    assert company_similarity("Acme Foods", "Acme Logistics") < 0.92


# ---- titles, seniority, department
@pytest.mark.parametrize("raw, title, seniority, department", [
    ("head - human resources", "HR Head", "head", "HR"), ("Head of HR", "HR Head", "head", "HR"),
    ("talent acquisition lead", "TA Lead", "head", "TA"), ("Lead - Talent Acquisition", "TA Lead", "head", "TA"),
    ("hr business partner", "HRBP", "manager", "HR"), ("FOUNDER/CEO", "Founder & CEO", "owner", "Founder"),
    ("ceo & founder", "Founder & CEO", "owner", "Founder"), ("Sr. HR Exec", "Senior HR Executive", "executive", "HR"),
    ("asst. manager - hr", "Assistant Manager - HR", "manager", "HR"), ("VP - Human Resources", "VP HR", "vp", "HR"),
    ("Managing Director", "Managing Director", "c-level", "Founder"), ("Plant Head", "Plant Head", "head", "Ops"),
    ("Proprietor", "Proprietor", "owner", "Founder"), ("Software Engineer", "Software Engineer", "", "Other"),
])
def test_titles_are_normalised_with_seniority_and_department(raw, title, seniority, department):
    assert clean_title(raw) == title
    assert seniority_of(raw) == seniority and department_of(raw) == department


# ---- email
def test_email_is_lowercased_validated_and_typos_fixed():
    assert clean_email("  Ravi.Kumar@Acme.EXAMPLE ") == ("ravi.kumar@acme.example", None)
    assert clean_email("mailto:hr@acme.example") == ("hr@acme.example", None)
    assert clean_email("ravi@gmial.com") == ("ravi@gmail.com", "typo_fixed")
    for bad in ("ravi@acme", "ravi..k@acme.example", "ravi kumar@acme.example", "@acme.example"):
        assert clean_email(bad) == (None, "invalid_email")


def test_gmail_alias_key_is_for_dedupe_only():
    assert email_alias_key("r.avi+jobs@googlemail.com") == "ravi@gmail.com" == email_alias_key("ravi@gmail.com")
    assert email_alias_key("ravi+x@acme.example") == "ravi+x@acme.example"     # other providers are left alone


def test_role_and_free_mail_detection():
    assert is_role_email("hr@acme.example") and is_role_email("careers.blr@acme.example")
    assert not is_role_email("hrishikesh@acme.example")
    assert is_free_mail("someone@rediffmail.com") and not is_free_mail("someone@acme.example")


# ---- phones
@pytest.mark.parametrize("raw", ["9000010001", "+91 90000 10001", "+91-90000-10001", "090000 10001", "919000010001",
                                 "0091 9000010001", "90000-10001", "(+91) 9000010001"])
def test_indian_mobile_variants_become_one_e164(raw):
    assert parse_phones(raw)["numbers"] == [("+919000010001", "mobile")]


def test_landlines_with_std_code_are_kept():
    assert parse_phones("011-4000 1234")["numbers"] == [("+911140001234", "landline")]
    assert parse_phones("(0120) 4000123")["numbers"] == [("+911204000123", "landline")]
    assert parse_phones("+91 80 4000 1234")["numbers"] == [("+918040001234", "landline")]
    assert parse_phones("4000123")["problems"] == [("landline_without_std", "4000123")]


def test_international_numbers_with_plus_are_kept_not_dropped():
    assert parse_phones("+44 20 7946 0958")["numbers"] == [("+442079460958", "intl")]
    assert parse_phones("0044 20 7946 0958")["numbers"] == [("+442079460958", "intl")]
    assert parse_phones("+1 (415) 555-0100")["numbers"] == [("+14155550100", "intl")]


def test_several_numbers_in_one_cell_and_junk():
    cell = "90000 10001 / 90000 20002; 011-4000 1234, 9000010001"
    assert [n for n, _ in parse_phones(cell)["numbers"]] == ["+919000010001", "+919000020002", "+911140001234"]
    assert parse_phones("1234567890")["problems"][0][0] == "junk_phone"
    assert parse_phones("9999999999")["numbers"] == []


# ---- domains
def test_website_to_registrable_domain():
    assert website_domain("https://www.Acme.co.in/careers?utm_source=x") == ("acme.co.in", "https://acme.co.in", None)
    assert website_domain("careers.acme.example")[0] == "acme.example"
    assert website_domain("http://blog.acme.org.in/")[0] == "acme.org.in"
    assert registrable_domain("hr.dept.up.gov.in") == "up.gov.in"
    assert registrable_domain("shop.acme.co.uk") == "acme.co.uk"
    assert website_domain("gmail.com")[2] == "free_mail_domain"
    assert website_domain("https://linkedin.com/company/acme")[2] == "not_company_site"
    assert website_domain("www.acme")[2] == "invalid_website"


def test_linkedin_links_are_tidied():
    assert linkedin_url("linkedin.com/in/ravi-example/") == "https://www.linkedin.com/in/ravi-example"
    assert linkedin_url("https://in.linkedin.com/company/acme-example?x=1", "company") == \
        "https://www.linkedin.com/company/acme-example"
    assert linkedin_url("javascript:alert(1)") == ""


# ---- places
@pytest.mark.parametrize("raw, city", [("Bangalore", "Bengaluru"), ("BENGALURU", "Bengaluru"), ("Gurgaon", "Gurugram"),
                                       ("bombay", "Mumbai"), ("Greater Bengaluru Area", "Bengaluru"),
                                       ("Delhi-NCR", "Delhi NCR"), ("Calcutta", "Kolkata"), ("Some Town", "Some Town")])
def test_city_aliases(raw, city):
    assert clean_city(raw) == city


def test_state_and_country():
    assert state_for_city("Gurgaon") == "Haryana" and state_for_city("Bangalore") == "Karnataka"
    assert clean_state("KA") == "Karnataka" and clean_state("tamilnadu") == "Tamil Nadu"
    assert clean_country("India") == clean_country("IND") == clean_country("in") == "IN"
    assert clean_country("U.S.A.") == "US" and clean_country("United Kingdom") == "GB"


def test_location_and_address_parsing():
    assert parse_location("Bengaluru, Karnataka, India") == {"city": "Bengaluru", "state": "Karnataka", "country": "IN"}
    assert parse_location("Greater Mumbai Area")["city"] == "Mumbai"
    found = parse_address("12, MG Road, Indiranagar, Bengaluru, Karnataka 560038, India")
    assert found == {"city": "Bengaluru", "state": "Karnataka", "country": "IN", "pincode": "560038"}
    assert parse_address("Plot 5, Sector 62, Noida 201309")["city"] == "Noida"


# ---- sizes, counts, job age
@pytest.mark.parametrize("raw, estimate, bucket", [
    ("51-200", 125, "51-200"), ("1,000+", 1000, "1000+"), ("10001+", 10001, "1000+"), ("201 - 500 employees", 350, "201-500"),
    ("11–50", 30, "11-50"), ("222", 222, "201-500"), ("Self-employed", 1, "1-10"), ("2-10", 6, "1-10"),
    ("Not verified", 0, ""), ("1001-5000", 3000, "1000+"),
])
def test_company_size_buckets(raw, estimate, bucket):
    assert parse_company_size(raw) == (estimate, bucket)


def test_size_bucket_edges_and_counts():
    assert [size_bucket(n) for n in (1, 10, 11, 50, 51, 200, 201, 500, 501, 1000, 1001)] == \
        ["1-10", "1-10", "11-50", "11-50", "51-200", "51-200", "201-500", "201-500", "501-1000", "501-1000", "1000+"]
    assert parse_count("10+ openings") == 10 and parse_count("") == 0


def test_job_age_from_relative_and_absolute_dates():
    today = date(2026, 11, 3)
    assert job_age_days("3 days ago", today) == 3 and job_age_days("30+ days ago", today) == 30
    assert job_age_days("Just now", today) == 0 and job_age_days("2 weeks ago", today) == 14
    assert job_age_days("13-Oct-2026", today) == 21 == job_age_days("2026-10-13", today)
    assert job_age_days("whenever", today) == 0
