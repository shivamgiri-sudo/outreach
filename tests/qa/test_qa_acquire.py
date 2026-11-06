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
