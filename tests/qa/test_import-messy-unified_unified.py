"""QA: one unified lead list built from six sources that describe the same companies and people.

Day 1 Apollo, day 2 Sales Navigator (Evaboot-style), day 3 Lusha, day 4 Naukri job scrape, day 5 IndiaMART Lead Manager,
day 6 the team Google Sheet (Leads tab). Between day 1 and 2 the Udaan Retail HR head unsubscribes; Lotus Pharma's HR
address bounced earlier and is on the do-not-contact list. Then the last import is undone and the one duplicate the
importer must not merge on its own (same company, city and person, two different websites) is merged in hygiene.
All companies, people, numbers and domains are made up.
"""
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import lead_import, routes_import
from app.lead_import import extract_record, read_table
from app.header_map import detect_format, map_columns
from app.main import create_app
from app.unsubscribe import unsub_path

FIX = Path(__file__).parent / "fixtures" / "import-messy-unified"
SOURCES = [("1_apollo.csv", {}), ("2_salesnav.csv", {}), ("3_lusha.csv", {}), ("4_naukri.csv", {}),
           ("5_indiamart.csv", {"source": "indiamart"}), ("6_team_leads.csv", {})]
EXPECTED_SOURCE = ["apollo", "sales_navigator", "lusha", "job_board", "indiamart", "bmi_sheet"]


def day(n: int) -> str:
    return f"2026-11-{n:02d}T05:30:00+00:00"


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """Run all six imports, one per day, and hand back the client, store and each import's response."""
    app = create_app(data_file=tmp_path / "d.json")
    client, store = TestClient(app), app.state.store
    store.write("suppressed", ["kiran@lotuspharma.example"])          # bounced last month
    results = []
    for n, (name, extra) in enumerate(SOURCES, start=1):
        monkeypatch.setattr(lead_import, "now_iso", lambda n=n: day(n))
        monkeypatch.setattr(routes_import, "now_iso", lambda n=n: day(n))
        r = client.post("/api/import/commit", json={"csv": (FIX / name).read_text(encoding="utf-8"), **extra})
        assert r.status_code == 200, r.text
        results.append(r.json())
        if n == 1:                                                     # Vikas clicks unsubscribe in our first email
            udaan = next(l for l in store.read("leads") if l["company"] == "Udaan Retail")
            assert client.post(unsub_path(store, udaan["id"])).status_code == 200
    return client, store, results


def leads_by_company(store) -> dict:
    out = {}
    for lead in store.read("leads"):
        out.setdefault(lead["company"], []).append(lead)
    return out


def one(store, company: str) -> dict:
    found = leads_by_company(store)[company]
    assert len(found) == 1, f"{company}: {len(found)} leads"
    return found[0]


def all_emails(lead: dict) -> set:
    return {*(lead.get("emails") or []), lead.get("email"), *(c.get("email") for c in lead.get("other_contacts") or [])}


def all_phones(lead: dict) -> set:
    people = [lead, *(lead.get("other_contacts") or [])]
    return {p for person in people for p in [person.get("phone"), *(person.get("phones") or [])] if p}


def test_each_source_is_recognised_and_attributed(world):
    _, _, results = world
    assert [r["source"] for r in results] == EXPECTED_SOURCE
    assert [r["format"]["name"].split(" ")[0] for r in results] == ["Apollo", "LinkedIn", "Lusha", "Job", "Unrecognised",
                                                                     "Team"]
    assert [r["summary"]["create"] for r in results] == [5, 1, 0, 1, 0, 1]
    assert all(r["summary"]["rejected"] == 0 for r in results)


def test_one_lead_per_company_and_people_collected(world):
    _, store, _ = world
    companies = leads_by_company(store)
    assert {c: len(v) for c, v in companies.items()} == {
        "Sharma Logistics": 1, "Kaveri Foods": 1, "Nirmal Textiles": 1, "Brightpath Edu": 2, "Udaan Retail": 1,
        "Meghdoot Hospitality": 1, "Zenith Motors": 1}               # Brightpath: two websites, left for a person
    sharma = one(store, "Sharma Logistics")
    assert sharma["contact_name"] == "Rohit Sharma" and sharma["title"] == "HR Head"
    assert [c["contact_name"] for c in sharma["other_contacts"]] == ["Neha Kulkarni"]
    neha = sharma["other_contacts"][0]
    assert neha["email"] == "neha.k@sharmalogistics.example" and neha["source"] == "sales_navigator"
    assert neha["linkedin_url"] == "https://www.linkedin.com/in/neha-kulkarni-example" and neha["added_at"] == day(2)


def test_no_field_is_lost_across_sources(world):
    """Every email and phone in every file ends up on the list, unless that row was on the do-not-contact list."""
    _, store, results = world
    leads = store.read("leads")
    emails = set().union(*map(all_emails, leads))
    phones = set().union(*map(all_phones, leads))
    suppressed = set(store.read("suppressed"))
    for (name, _), result in zip(SOURCES, results):
        headers, table = read_table((FIX / name).read_text(encoding="utf-8"))
        columns = map_columns(headers, [r for _, r in table[:25]], detect_format(headers))
        skipped = {s["row"] for s in result["skipped"]}
        for n, row in table:
            rec, _ = extract_record(row, columns, date(2026, 11, 3))
            if n in skipped:
                assert rec["email"] in suppressed, (name, n)
                continue
            assert set(rec["emails"]) <= emails, (name, n, rec["emails"])
            assert set(rec["phones"]) <= phones, (name, n, rec["phones"])
    sharma = one(store, "Sharma Logistics")
    assert sharma["emails"] == ["rohit.sharma@sharmalogistics.example", "rohit.hr@sharmalogistics.example"]
    assert sharma["phones"] == ["+919000010001", "+912040001001", "+919000010011"]
    assert sharma["linkedin_url"] == "https://www.linkedin.com/in/rohit-sharma-example"
    assert sharma["company_linkedin_url"] == "https://www.linkedin.com/company/sharma-logistics-example"
    assert set(sharma["job_links"]) == {"https://www.naukri.example/job/sharma-1", "https://www.naukri.example/job/sharma-2"}
    assert (sharma["open_roles"], sharma["role"]) == (40, "Warehouse Supervisor")
    assert sharma["max_job_age_days"] >= 11   # age is measured against the real clock, so it grows by a day each day   # hiring signals keep the larger value (qa/import-india-sources), 12 and 40 openings on two postings
    nirmal = one(store, "Nirmal Textiles")
    assert "imran.shaikh.example@gmail.com" in nirmal["emails"] and "+919000010013" in nirmal["phones"]
    assert nirmal["pincode"] == "395002" and nirmal["address"].startswith("Plot 12, Ring Road")
    kaveri = one(store, "Kaveri Foods")
    texts = [e["text"] for e in kaveri["timeline"]]
    assert "Remarks: Wants a demo on Friday" in texts
    assert "Lead status in bmi_sheet: Demo Scheduled" in texts        # kept as a note; the stage is never changed
    assert kaveri["stage"] == "new" and kaveri["source_detail"] == "Naukri"


def test_conflicts_are_recorded_not_overwritten(world):
    _, store, _ = world
    kaveri = one(store, "Kaveri Foods")
    assert kaveri["title"] == "HR Manager"                             # first value kept
    got = {(c["field"], c["incoming"], c["source"], c["at"]) for c in kaveri["merge_conflicts"]}
    assert got == {("title", "VP People", "sales_navigator", day(2)), ("sector", "Food & Beverages", "sales_navigator", day(2)),
                   ("sector", "Food", "lusha", day(3)), ("sector", "FMCG", "bmi_sheet", day(6))}
    assert {c["kept"] for c in kaveri["merge_conflicts"]} == {"HR Manager", "Food Production"}
    sharma = one(store, "Sharma Logistics")
    assert [(c["field"], c["incoming"]) for c in sharma["merge_conflicts"]] == [("sector", "Logistics")]


def test_provenance_per_field(world):
    _, store, _ = world
    sharma = one(store, "Sharma Logistics")
    assert sharma["field_sources"] == {**sharma["field_sources"], "company": "apollo", "email": "apollo", "phone": "apollo",
                                       "linkedin_url": "sales_navigator", "open_roles": "job_board", "role": "job_board",
                                       "max_job_age_days": "job_board"}
    meghdoot = one(store, "Meghdoot Hospitality")
    assert meghdoot["source"] == "job_board"
    assert meghdoot["field_sources"] == {**meghdoot["field_sources"], "company": "job_board", "open_roles": "job_board",
                                         "contact_name": "indiamart", "email": "indiamart", "phone": "indiamart",
                                         "domain": "indiamart"}
    assert one(store, "Nirmal Textiles")["field_sources"]["address"] == "indiamart"
    for lead in store.read("leads"):                                   # every filled key field says where it came from
        for field in ("company", "contact_name", "email", "phone", "domain", "city"):
            if lead.get(field):
                assert lead["field_sources"].get(field) in EXPECTED_SOURCE, (lead["company"], field)


def test_suppressed_and_unsubscribed_are_never_resurrected(world):
    _, store, results = world
    assert "Lotus Pharma" not in leads_by_company(store)              # Lusha day 3 and team sheet day 6 both skipped
    assert [s["row"] for s in results[2]["skipped"]] == [5, 7] and [s["row"] for s in results[5]["skipped"]] == [4, 5]
    udaan = one(store, "Udaan Retail")
    assert udaan["stage"] == "unsubscribed" and udaan["consent"] is None and udaan["consent_withdrawn_at"]
    assert "vikas@udaanretail.example" in store.read("suppressed")
    assert [c["contact_name"] for c in udaan["other_contacts"]] == ["Sneha Rao"]   # added, but the lead stays closed
    assert udaan["address"] == "Andheri East, Mumbai"                  # details still fill in (IndiaMART, day 5)
    assert any(w["kind"] == "closed_lead" for r in results for w in r["warnings"])
    assert all(l.get("stage") != "unsubscribed" for l in store.read("leads") if l["company"] != "Udaan Retail")


def test_undo_last_import_then_hygiene_merges_the_remaining_duplicate(world):
    client, store, results = world
    team = results[5]
    undo = client.post(f"/api/import/{team['import_id']}/undo").json()
    assert undo["removed"] == 1 and undo["kept"] == [] and undo["merged_leads_left"] == 2
    companies = leads_by_company(store)
    assert "Zenith Motors" not in companies
    assert one(store, "Kaveri Foods")["source_detail"] == "Naukri"     # merged details stay (documented in the note)
    assert client.post(f"/api/import/{team['import_id']}/undo").status_code == 400
    history = client.get("/api/import/history").json()
    assert history[0]["undone_at"] and [h["source"] for h in history][::-1] == EXPECTED_SOURCE

    report = client.get("/api/hygiene/report").json()
    assert report["duplicate_clusters"] == 1
    cluster = report["clusters"][0]
    assert cluster["why"] == ["same company name and city and person (websites differ)"]
    first, second = sorted(companies["Brightpath Edu"], key=lambda l: l["created_at"])
    assert set(cluster["lead_ids"]) == {first["id"], second["id"]}
    merged = client.post("/api/hygiene/merge", json={"keep_id": first["id"], "merge_ids": [second["id"]]}).json()["lead"]
    assert merged["linkedin_url"] == "https://www.linkedin.com/in/ankita-verma-example"
    assert merged["field_sources"]["linkedin_url"] == "sales_navigator"
    assert merged["domain"] == "brightpath.example" and merged["email"] == "ankita@brightpath.example"
    conflicts = {(c["field"], c["incoming"]) for c in merged["merge_conflicts"]}
    assert ("domain", "brightpathedu.example") in conflicts and ("title", "Co-founder & CEO") in conflicts
    after = leads_by_company(store)
    assert all(len(v) == 1 for v in after.values()) and len(after) == 6
    assert client.get("/api/hygiene/report").json()["duplicate_clusters"] == 0


def test_reimporting_every_source_changes_nothing(world):
    client, store, _ = world
    before = store.read("leads")
    for name, extra in SOURCES:
        r = client.post("/api/import/preview", json={"csv": (FIX / name).read_text(encoding="utf-8"), **extra}).json()
        assert r["summary"]["create"] == 0 and r["summary"]["merge"] == 0, (name, r["summary"])
    assert store.read("leads") == before


def test_company_level_phone_fill_records_its_source(tmp_path):
    """A directory row with no person fills a missing phone on a lead that has a person: provenance is kept."""
    client = TestClient(create_app(data_file=tmp_path / "d.json"))
    client.post("/api/import/commit", json={"csv": "Company,Contact Name,Email,City\n"
                                                    "Pixel Prints,Hetal Shah,hetal@pixelprints.example,Ahmedabad\n",
                                            "source": "apollo"})
    r = client.post("/api/import/commit", json={"csv": "Firm Name,Mobile No.,City,GSTIN\n"
                                                       "PIXEL PRINTS,+91 90000 10071,Ahmedabad,24AAAAA0000A1Z5\n"}).json()
    assert r["summary"]["merge"] == 1
    lead = client.get("/api/leads").json()[0]
    assert lead["phone"] == "+919000010071" and lead["contact_name"] == "Hetal Shah"
    assert lead["field_sources"]["phone"] == "directory"
