"""Team sheet 'Decision Makers' tab: hand-found people are attached to the matching company, never overwriting."""
from fastapi.testclient import TestClient

from app.main import create_app
from app.sheet_import import decision_makers, read_rows

LEADS = ("company,email,website\n"
         "Juniper Fragrances Pvt Ltd,hr@juniper.example,https://juniper.example\n"
         "Pebble Shoes (footwear),careers@pebble.example,https://pebble.example\n"
         "Quill Systems,hr@quill.example,https://quill.example\n")
TAB = ("Company,Name,Designation (as shown),LinkedIn Profile URL,Where I found it,Confidence,Fits 50–400?\n"
       "Pebble Shoes,Anita Rao,Co-founder (admin),https://www.linkedin.com/in/anita-example,Company about page,High,Yes (~50+ in 2022)\n"
       "Pebble Shoes,Dev Rao,Co-founder (business),https://www.linkedin.com/in/dev-example,Company about page,Medium,Yes\n"
       "Juniper Fragrances,Rahul Menon,Founder,https://www.linkedin.com/in/rahul-example,YourStory,Medium,Yes (222)\n"
       "Juniper Fragrances,(company page),—,https://in.linkedin.com/company/juniper,LinkedIn search,—,Yes (222)\n"
       "Quill Systems,Kirti Shah,HR – title not shown,https://in.linkedin.com/in/kirti-example,LinkedIn search,Low–Medium,Not verified\n"
       "Unknown Co,Someone,HR,,LinkedIn search,Low,Not verified\n"
       ",,,,,,\n"
       "Notes,Names and roles were found by hand,,,,,\n")


def test_parser_skips_company_pages_blank_rows_and_notes():
    people = decision_makers(read_rows(TAB))
    names = [p["contact_name"] for p in people]
    assert "(company page)" not in names and "Notes" not in [p["company"] for p in people]
    assert names[:2] == ["Anita Rao", "Dev Rao"]
    assert people[0]["title"] == "Co-founder (admin)" and people[0]["confidence"] == "High"


def make(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": LEADS, "source": "manual"})
    return c


def test_import_attaches_people_to_matching_companies(tmp_path):
    c = make(tmp_path)
    r = c.post("/api/leads/import-sheet", json={"csv": TAB, "tab": "decision_makers"}).json()
    assert r["matched"] == 4 and r["filled"] == 3 and r["unmatched"] == ["Unknown Co"]
    leads = {l["company"]: l for l in c.get("/api/leads").json()}
    cai = leads["Pebble Shoes (footwear)"]
    assert cai["contact_name"] == "Anita Rao" and cai["title"] == "Co-founder (admin)"
    assert cai["linkedin_url"] == "https://www.linkedin.com/in/anita-example"
    assert [p["contact_name"] for p in cai["other_contacts"]] == ["Dev Rao"]
    assert leads["Juniper Fragrances Pvt Ltd"]["contact_name"] == "Rahul Menon"


def test_import_never_overwrites_and_is_repeatable(tmp_path):
    c = make(tmp_path)
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nQuill Systems,Isha,hr@quill.example\n", "source": "manual"})
    c.post("/api/leads/import-sheet", json={"csv": TAB, "tab": "decision_makers"})
    again = c.post("/api/leads/import-sheet", json={"csv": TAB, "tab": "decision_makers"}).json()
    assert again["filled"] == 0
    cai = next(l for l in c.get("/api/leads").json() if l["company"].startswith("Pebble"))
    assert len(cai["other_contacts"]) == 1
