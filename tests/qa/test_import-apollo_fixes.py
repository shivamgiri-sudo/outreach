"""Regression tests for the bugs QA found importing Apollo.io exports. Every name, number and domain is made up."""
import pytest
from fastapi.testclient import TestClient

from app.header_map import detect_format, map_columns
from app.main import create_app

OWNER = "owner@outreach.example"


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(data_file=tmp_path / "d.json"))


def preview(client, csv, **extra):
    r = client.post("/api/import/preview", json={"csv": csv, **extra})
    assert r.status_code == 200, r.text
    return r.json()


# ---- 1. value sniffing turned Apollo's owner / social / logo columns into lead data
def test_known_extra_columns_stay_out_even_when_their_values_look_like_emails_or_websites():
    headers = ["First Name", "Last Name", "Company Name", "Email", "Contact Owner", "Account Owner", "Website",
               "Facebook Url", "Twitter Url", "Logo Url", "Subsidiary of (Organization ID)", "Person Linkedin Url",
               "Work Direct Phone"]
    rows = [{"First Name": f"Pers{i}", "Last Name": "Demo", "Company Name": f"Company {i}", "Email": f"p{i}@co{i}.example",
             "Contact Owner": OWNER, "Account Owner": OWNER, "Website": f"http://www.co{i}.example",
             "Facebook Url": f"https://facebook.com/co{i}", "Twitter Url": f"https://twitter.com/co{i}",
             "Logo Url": f"https://zenprospect-production.s3.amazonaws.com/uploads/pictures/demo{i}/picture",
             "Subsidiary of (Organization ID)": f"5f2a3b4c5d6e7f80912a3b{i:02d}",
             "Person Linkedin Url": f"http://www.linkedin.com/in/pers{i}-demo", "Work Direct Phone": f"'+91 90000 1000{i}"}
            for i in range(8)]
    fmt = detect_format(headers)
    fields = {c["column"]: c["field"] for c in map_columns(headers, rows, fmt)}
    assert fmt["key"] == "apollo"
    for column in ("Contact Owner", "Account Owner", "Facebook Url", "Twitter Url", "Logo Url",
                   "Subsidiary of (Organization ID)"):
        assert fields[column] is None, column
    assert fields["Email"] == "email" and fields["Website"] == "website" and fields["Company Name"] == "company"


def test_the_apollo_owner_address_never_collapses_an_export_into_one_lead(client):
    csv = "First Name,Last Name,Title,Company,Email,Email Status,Contact Owner,Account Owner,Mobile Phone,Website,City,Country\n"
    for i in range(6):
        email = "" if i % 2 else f"p{i}@co{i}.example"          # Apollo leaves Email blank when it is 'Unavailable'
        csv += (f"Pers{'abcdef'[i]},Kumar,HR Head,Company {'ABCDEF'[i]} Pvt Ltd,{email},Verified,{OWNER},{OWNER},"
                f"'+91 90000 1000{i},http://www.co{i}.example,Pune,India\n")
    r = preview(client, csv, source="apollo")
    assert r["summary"]["create"] == 6 and r["summary"]["duplicates_in_file"] == 0
    assert r["mapping"]["Contact Owner"] == r["mapping"]["Account Owner"] == "ignore"
    assert all(row["email"] != OWNER for row in r["rows"])
    assert [row["domain"] for row in r["rows"]] == [f"co{i}.example" for i in range(6)]


def test_accounts_without_a_website_never_take_the_logo_host_as_their_domain(client):
    csv = "Company Name,Website,Company City,Logo Url,Company Phone\n"
    for i, name in enumerate(["Gokul Dairy Farms", "Hari Om Transport", "Jai Mata Di Caterers"]):
        csv += (f"{name},,Pune,https://zenprospect-production.s3.amazonaws.com/uploads/pictures/nosite{i}/picture,"
                f"'+91 20 4000 52{i:02d}\n")
    r = preview(client, csv, source="apollo")
    assert r["summary"]["create"] == 3
    assert [row["domain"] for row in r["rows"]] == ["", "", ""]


def test_quick_paste_never_uses_a_facebook_page_as_the_company_website(client):
    csv = ("First Name,Last Name,Company Name,Email,Website,Facebook Url,Contact Owner\n"
           f"Salma,Begum,Royal Caterers,salma@royalcaterers.example,,https://facebook.com/royal-caterers,{OWNER}\n"
           f"Mahesh,Lokhande,Shree Ganesh Traders,,,https://facebook.com/shree-ganesh,{OWNER}\n"
           f"Harish,Gupta,Deccan Motors,harish@deccanmotors.example,,https://facebook.com/deccan-motors,{OWNER}\n")
    r = client.post("/api/leads/import", json={"csv": csv, "source": "apollo"}).json()
    leads = {lead["company"]: lead for lead in client.get("/api/leads").json()}
    assert r["created"] == 2 and r["rejected"] == 1        # Shree Ganesh has no email, phone or real website
    assert leads["Royal Caterers"]["domain"] == "royalcaterers.example"
    assert leads["Deccan Motors"]["email"] == "harish@deccanmotors.example"
    assert all("facebook.com" not in (lead.get("website") or "") for lead in leads.values())
