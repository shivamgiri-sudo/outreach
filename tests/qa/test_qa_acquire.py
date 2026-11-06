"""Regressions found by the browser QA sweep of the lead-acquisition pages (area "acquire")."""
from fastapi.testclient import TestClient

from app.main import create_app
from app.scraper import normalise_url

CONTACT_PAGE = ("<p>Sales: sales@acmetools.in, Priya Shah priya.shah@acmetools.in, +91 98201 23456</p>"
                "<p>Report abuse: abuse@acmetools.in. Privacy: privacy@acmetools.in. noreply@acmetools.in</p>")


def test_typed_domain_without_https_is_read_as_a_website():
    assert normalise_url("persistent.com") == "https://persistent.com"
    assert normalise_url(" www.acme.in/contact ") == "https://www.acme.in/contact"
    assert normalise_url("https://acme.in") == "https://acme.in"
    assert normalise_url("not a url") == "not a url"


def test_find_contacts_leaves_complaint_and_no_reply_addresses_out_of_leads(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    r = c.post("/api/scrape/extract", json={"url": "acmetools.in/contact", "html": CONTACT_PAGE, "company": "Acme Tools",
                                            "add_leads": True}).json()
    assert len(r["emails"]) == 5 and r["added"] == 2 and r["not_for_outreach"] == 3 and r["phones"] == ["+919820123456"]
    emails = sorted(l["email"] for l in c.get("/api/leads").json())
    assert emails == ["priya.shah@acmetools.in", "sales@acmetools.in"]
    again = c.post("/api/scrape/extract", json={"url": "acmetools.in/contact", "html": CONTACT_PAGE, "add_leads": True}).json()
    assert again["added"] == 0


def test_unclosed_quote_is_refused_instead_of_swallowing_the_rest_of_the_file():
    import pytest
    from app.lead_import import read_table
    text = 'company,email,phone\n"Unclosed Quote Ltd,ceo@unclosed.in,9876500000\nGood Ltd,hello@good.in,9876500001\n'
    with pytest.raises(ValueError, match="Row 2 opens a quote"):
        read_table(text)
    headers, table = read_table('company,note\n"Acme, Pune","line one\nline two"\n')   # a real multi-line cell is fine
    assert table[0][1] == {"company": "Acme, Pune", "note": "line one\nline two"}


def test_talent_partner_is_a_recruiter_not_an_owner():
    from app.standardise import department_of, seniority_of
    assert (seniority_of("Senior Talent Partner"), department_of("Senior Talent Partner")) == ("executive", "TA")
    assert (seniority_of("People Partner"), department_of("People Partner")) == ("executive", "HR")
    assert (seniority_of("Managing Partner"), department_of("Managing Partner")) == ("owner", "Founder")
