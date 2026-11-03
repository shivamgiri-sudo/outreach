import pytest
from fastapi.testclient import TestClient

from app.enrich import candidate_urls, needs, pick_best_email
from app.main import create_app
from app.sheet_import import parse_phones, sheet_leads

HEADER = ("Company Name,Contact Person,Designation,Contact Number,Email ID,Open Position,City,Company Sector,Job Portal Source,"
          "Job Posting Link,Posting Date,Days Live,No. of Openings,Position Level,Caller Name,Call Outcome,Demo Date & Time,Lead Status,"
          "Next Follow-up Date,Remarks,Company Size (employees),LinkedIn Profile URL,Contact Confidence,Status,Remarks\n")
ROWS = (
    "Acme Retail,Ravi Kumar,Founder,9000010001,hr@acme.in,Store Manager,Noida,FMCG & Retail,Other (Weekday),https://w.com/1,13-Oct-2026,20,1,Manager,,,,New,,P1 note,222,https://linkedin.com/in/ravi,Medium,Not Connected,Not Responding\n"
    "Acme Retail,Ravi Kumar,Founder,9000010001,hr@acme.in,Sales Manager,Noida,FMCG & Retail,Other (Weekday),https://w.com/2,22-Sep-2026,41,2,Manager,,,,Contacted,,,222,,Medium,Connected,Mail Shared\n"
    "Beta Steels,,,919000020002 / 9811122233,,Sales Head,Noida,Manufacturing,Other,,07-Oct-2026,26,1,Manager,,,,Demo Done,,,Not verified,,Search link only,,\n"
    "Gamma Co,,,911234567890,info@gmail.com,Driver,Delhi,Other,Other,,,,,,,,,Lost,,,,,,,\n"
)


@pytest.fixture()
def client(tmp_path):
    pages = {
        "https://beta.in/": "<a href='mailto:hr@beta.in'>x</a> sales@other-site.com",
        "https://beta.in/contact": "Call +91 98111 22233 careers@beta.in",
    }

    def fake_fetch(url):
        if url in pages:
            return pages[url]
        raise OSError("not found")

    return TestClient(create_app(data_file=tmp_path / "d.json", fetcher=fake_fetch))


# ---- phones ----
def test_parse_phones_splits_normalises_and_flags_junk():
    p = parse_phones("9000030003 / 919000020002")
    assert p["mobiles"] == ["+919000030003", "+919000020002"]
    assert parse_phones("90000 40004")["mobiles"] == ["+919000040004"]
    assert parse_phones("911234567890")["mobiles"] == [] and parse_phones("911234567890")["junk"]
    assert parse_phones("911204151755") == {"mobiles": [], "landlines": ["+911204151755"], "junk": False}
    assert parse_phones("")["mobiles"] == []


# ---- sheet mapping ----
def test_sheet_leads_groups_rows_by_company_and_maps_status():
    import csv, io
    rows = list(csv.DictReader(io.StringIO(HEADER + ROWS)))
    leads = {l["company"]: l for l in sheet_leads(rows)}
    acme = leads["Acme Retail"]
    assert acme["open_roles"] == 3 and acme["max_job_age_days"] == 41      # 1 + 2 openings, oldest 41 days
    assert acme["role"] == "Store Manager and Sales Manager"
    assert acme["email"] == "hr@acme.in" and acme["phone"] == "+919000010001"
    assert acme["domain"] == "acme.in" and acme["website_guess"] is True
    assert acme["stage"] == "contacted"                                    # highest sheet status across rows
    assert acme["city"] == "Noida" and acme["sector"] == "FMCG & Retail"
    assert any("Not Responding" in e["text"] for e in acme["timeline"])
    beta = leads["Beta Steels"]
    assert beta["stage"] == "call_done" and beta["phones"] == ["+919000020002", "+919811122233"]
    gamma = leads["Gamma Co"]
    assert gamma["stage"] == "disqualified" and gamma["domain"] is None    # gmail is not a company website
    assert gamma["phone"] is None


def test_import_sheet_endpoint_is_idempotent(client):
    first = client.post("/api/leads/import-sheet", json={"csv": HEADER + ROWS}).json()
    assert first["created"] == 3
    second = client.post("/api/leads/import-sheet", json={"csv": HEADER + ROWS}).json()
    assert second["created"] == 0 and second["duplicates"] == 3
    assert len(client.get("/api/leads").json()) == 3


def test_demo_tab_marks_onboarded_as_customer(client):
    demo = ("Company Name,Contact Person,Designation,Contact Number,Email ID,Demo Given Date,Status\n"
            "Orion Softworks,Kavya,HR Manager,9000050005,alisha@orionsoft.example,26-Oct-26,Onboarded - Positions shared\n"
            "Nimbus Tools,Meera,Asst Manager,9000060006,hr@nimbus.example,29-Oct-26,Will confirm after review\n")
    r = client.post("/api/leads/import-sheet", json={"csv": demo, "tab": "demo"}).json()
    assert r["created"] == 2
    leads = {l["company"]: l for l in client.get("/api/leads").json()}
    assert leads["Orion Softworks"]["stage"] == "customer" and leads["Nimbus Tools"]["stage"] == "call_done"
    assert leads["Orion Softworks"]["demo_given"] == "26-Oct-26"


# ---- enrichment ----
def test_needs_reports_gaps():
    assert needs({"email": None, "phone": None, "domain": "x.in", "contact_name": ""}) == ["email", "phone", "decision_maker"]
    assert needs({"email": "a@x.in", "phone": "+919999999999", "domain": "x.in", "contact_name": "A"}) == []


def test_candidate_urls_cover_contact_pages():
    urls = candidate_urls("beta.in")
    assert urls[0] == "https://beta.in/" and "https://beta.in/contact" in urls and "https://beta.in/careers" in urls


def test_pick_best_email_prefers_hiring_addresses_and_same_domain():
    emails = ["info@beta.in", "careers@beta.in", "x@other.com", "hr@beta.in"]
    assert pick_best_email(emails, "beta.in") == "hr@beta.in"
    assert pick_best_email(["x@other.com"], "beta.in") is None


def test_enrich_run_fills_email_from_public_pages_with_source(client):
    client.post("/api/leads/import-sheet", json={"csv": HEADER + ROWS})
    r = client.post("/api/enrich/run", json={"limit": 10}).json()
    # Beta Steels has no website/domain, so it cannot be enriched; Acme already has an email.
    assert r["skipped_no_website"] >= 1
    client.post("/api/leads/import", json={"csv": "company,website\nBeta Steels,https://beta.in\n", "source": "manual"})
    r2 = client.post("/api/enrich/run", json={"limit": 10}).json()
    beta = next(l for l in client.get("/api/leads").json() if l["company"] == "Beta Steels")
    assert r2["enriched"] == 1
    assert beta["email"] == "hr@beta.in" and beta["email_source"].startswith("enrichment:https://beta.in/")
    assert "sales@other-site.com" not in str(beta.get("enrichment", {}).get("emails", []))
    assert any(e["kind"] == "enrichment" for e in beta["timeline"])


def test_apollo_export_lists_leads_missing_email(client):
    client.post("/api/leads/import-sheet", json={"csv": HEADER + ROWS})
    csv_text = client.get("/api/enrich/apollo-export").text
    assert csv_text.splitlines()[0] == "company,contact_name,title,domain,city,lead_id"
    assert "Beta Steels" in csv_text and "Acme Retail" not in csv_text


def test_quality_counts(client):
    client.post("/api/leads/import-sheet", json={"csv": HEADER + ROWS})
    q = client.get("/api/quality").json()
    assert q["total"] == 3 and q["missing_email"] == 1 and q["missing_decision_maker"] == 2
