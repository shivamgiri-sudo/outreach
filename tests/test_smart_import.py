"""Smart import API: dry run, commit, undo, dedupe within the file and against saved leads, merge rules, safety.
All companies, people, numbers and domains are made up."""
import pytest
from fastapi.testclient import TestClient

from app.unsubscribe import unsub_path
from app.main import create_app

APOLLO = ("First Name,Last Name,Title,Company,Email,Mobile Phone,Corporate Phone,Website,# Employees,City,Country\n"
          "RAVI,KUMAR,head - human resources,ACME RETAIL PVT. LTD.,Ravi.Kumar@acme.example,90000 10001,011-4000 1234,"
          "www.acme.example,51-200,Bangalore,India\n"
          "Anita,Rao,TA Lead,Acme Retail,anita@acme.example,,011-4000 1234,acme.example,51-200,Bengaluru,IN\n"
          ",,,,,,,,,,\n"
          ",,,,bad@,,,,,,\n")


@pytest.fixture()
def app(tmp_path):
    return create_app(data_file=tmp_path / "d.json")


@pytest.fixture()
def client(app):
    return TestClient(app)


def leads(client):
    return {l["company"] or l["domain"]: l for l in client.get("/api/leads").json()}


def preview(client, csv, **extra):
    r = client.post("/api/import/preview", json={"csv": csv, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def commit(client, csv, **extra):
    r = client.post("/api/import/commit", json={"csv": csv, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def test_preview_maps_standardises_and_saves_nothing(client):
    r = preview(client, APOLLO, source="apollo")
    assert r["dry_run"] is True and r["format"]["name"] == "Apollo export"
    assert r["summary"] == {**r["summary"], "rows": 3, "create": 1, "duplicates_in_file": 1, "rejected": 1}
    first = r["rows"][0]
    assert first["company"] == "Acme Retail" and first["contact_name"] == "Ravi Kumar" and first["title"] == "HR Head"
    assert first["email"] == "ravi.kumar@acme.example" and first["phone"] == "+919000010001"
    assert first["city"] == "Bengaluru" and first["state"] == "Karnataka" and first["country"] == "IN"
    assert first["company_size_bucket"] == "51-200" and first["seniority"] == "head" and first["department"] == "HR"
    assert r["rejected"][0]["row"] == 5 and "not a valid address" in r["rejected"][0]["reason"]
    assert r["rejected"][0]["values"]["Email"] == "bad@"           # original cells, for the rejected-rows download
    assert {c["column"]: c["field"] for c in r["columns"]}["# Employees"] == "company_size"
    assert client.get("/api/leads").json() == []
    assert client.get("/api/import/history").json() == []
    assert "import_mappings" not in client.get("/api/settings").json()


def test_commit_creates_one_company_with_two_people_and_records_it(client):
    r = commit(client, APOLLO, source="apollo")
    assert r["dry_run"] is False and r["import_id"] and len(r["created_ids"]) == 1
    acme = leads(client)["Acme Retail"]
    assert acme["company_legal"] == "Acme Retail Pvt. Ltd."
    assert acme["domain"] == "acme.example" and acme["phones"] == ["+919000010001", "+911140001234"]
    assert acme["phone_type"] == "mobile" and acme["field_sources"]["email"] == "apollo"
    assert [c["contact_name"] for c in acme["other_contacts"]] == ["Anita Rao"]
    assert acme["other_contacts"][0]["source"] == "apollo" and acme["other_contacts"][0]["title"] == "TA Lead"
    history = client.get("/api/import/history").json()
    assert history[0]["id"] == r["import_id"] and history[0]["created"] == 1 and history[0]["format"] == "Apollo export"


def test_reimporting_the_same_file_changes_nothing(client):
    commit(client, APOLLO, source="apollo")
    before = client.get("/api/leads").json()
    again = commit(client, APOLLO, source="apollo")
    assert again["summary"]["create"] == 0 and again["summary"]["merge"] == 0 and again["summary"]["unchanged"] == 2
    assert client.get("/api/leads").json() == before


def test_mapping_is_remembered_for_the_next_file_of_the_same_shape(client):
    csv = "Org,Person,Mail,Extra\nAcme Retail,Ravi Kumar,ravi@acme.example,keep me out\n"
    first = preview(client, csv)
    assert first["format"]["saved_mapping"] is False and first["mapping"]["Extra"] == "ignore"
    commit(client, csv, mapping={"Person": "contact_name", "Extra": "notes"})
    second = preview(client, "Org,Person,Mail,Extra\nBeta Steels,Sam Shah,sam@beta.example,x\n")
    assert second["format"]["saved_mapping"] is True and second["mapping"]["Extra"] == "notes"
    assert {c["column"]: c["how"] for c in second["columns"]}["Extra"] == "saved mapping"


def test_rows_body_works_like_csv(client):
    r = commit(client, None, rows=[{"Company Name": "Orion Softworks", "Email ID": "kavya@orion.example",
                                    "Contact Number": "9000050005"}], source="manual")
    assert r["summary"]["create"] == 1
    assert leads(client)["Orion Softworks"]["phone"] == "+919000050005"


# ---- dedupe
def test_duplicates_within_the_file_by_email_gmail_alias_and_phone(client):
    csv = ("Name,Company,Email,Phone\n"
           "Ravi Kumar,Acme Retail,ravi.k@gmail.com,9000010001\n"
           "Ravi Kumar,Acme Retail,RaviK+jobs@googlemail.com,\n"
           "Ravi K,,,+91 90000 10001\n"
           "Ravi Kumar,Acme Retail,ravi.k@gmail.com,\n")
    r = preview(client, csv)
    assert r["summary"]["create"] == 1 and r["summary"]["duplicates_in_file"] == 3
    reasons = [d["matched_by"] for d in r["duplicates"]]
    assert reasons[0].startswith("same Gmail inbox") and reasons[1] == "same phone" and reasons[2] == "same email"
    assert all(d["same_as_row"] == 2 for d in r["duplicates"])


def test_matches_existing_leads_by_every_key(client):
    client.post("/api/leads/import", json={"source": "manual", "csv": (
        "company,contact_name,email,phone,website\n"
        "Acme Retail,Ravi Kumar,ravi@acme.example,9000010001,https://acme.example\n"
        "Beta Steels,Sam Shah,sam.shah@gmail.com,,\n"
        "Gamma Foods,Meena Iyer,meena@gamma.example,,https://gamma.example\n")})
    client.post("/api/leads/import-sheet", json={"csv": "Company Name,City,Contact Number\nDelta Logistics Pvt Ltd,Pune,\n"})
    csv = ("Name,Company,Email,Phone,Website,City\n"
           "Ravi Kumar,Acme,RAVI@ACME.EXAMPLE,,,\n"                         # same email
           "Sam Shah,Beta Steels,samshah+x@gmail.com,,,\n"                   # same Gmail inbox
           "Somebody,,,+91-90000-10001,,\n"                                  # same phone (different name -> company)
           "Meena Iyer,Gamma Foods,meena.iyer@new.example,,gamma.example,\n"   # same domain + person
           ",Gamma Foods,,,https://www.gamma.example/careers,\n"            # company-only row, same domain
           "Arjun Das,Delta Logistic,arjun@delta.example,,,Poona\n")        # fuzzy name + same city
    r = preview(client, csv)
    by_row = {m["row"]: m["matched_by"] for m in r["merges"] + r["duplicates"] + r["unchanged"]}
    assert by_row[2] == "same email" and by_row[3].startswith("same Gmail inbox")
    assert by_row[4] == "same phone (different person)" and by_row[5] == "same company domain and person"
    assert by_row[6] == "same company domain" and by_row[7].startswith("similar company name in the same city")
    assert r["summary"]["create"] == 0


def test_same_name_in_different_cities_is_not_merged(client):
    client.post("/api/leads/import-sheet", json={"csv": "Company Name,City,Contact Number\nNimbus Tools,Pune,\n"})
    r = preview(client, "Company,City,Phone\nNimbus Tools,Chennai,9000060006\n")
    assert r["summary"]["create"] == 1 and r["summary"]["merge"] == 0


# ---- merge rules
def test_merge_fills_gaps_never_overwrites_and_logs_conflicts_with_provenance(client):
    client.post("/api/leads/import", json={"source": "manual", "csv": (
        "company,contact_name,title,email\nAcme Retail,Ravi Kumar,Founder,ravi@acme.example\n")})
    csv = "Name,Company,Title,Email,City,Phone,Industry\nRavi Kumar,Acme Retail,HR Head,ravi@acme.example,Gurgaon,9000010001,Retail\n"
    r = commit(client, csv, source="lusha")
    merge = r["merges"][0]
    assert set(merge["filled"]) >= {"city", "state", "phone", "sector"}
    assert merge["conflicts"] == [{"field": "title", "kept": "Founder", "incoming": "HR Head"}]
    acme = leads(client)["Acme Retail"]
    assert acme["title"] == "Founder" and acme["city"] == "Gurugram" and acme["state"] == "Haryana"
    assert acme["field_sources"]["city"] == "lusha" and "title" not in acme["field_sources"]
    conflict = acme["merge_conflicts"][0]
    assert conflict["field"] == "title" and conflict["incoming"] == "HR Head" and conflict["source"] == "lusha"
    assert any(e["text"].startswith("Merged from lusha (") for e in acme["timeline"])
    assert r["merged_ids"] == [acme["id"]]


def test_another_person_at_the_same_company_becomes_an_other_contact(client):
    client.post("/api/leads/import", json={"source": "manual", "csv": (
        "company,contact_name,email,website\nAcme Retail,Ravi Kumar,ravi@acme.example,https://acme.example\n")})
    r = commit(client, "Name,Title,Email\nAnita Rao,TA Lead,anita@acme.example\n", source="hunter")
    assert r["merges"][0]["added_contact"] == "Anita Rao" and r["summary"]["contacts_added"] == 1
    acme = leads(client)["Acme Retail"]
    assert acme["contact_name"] == "Ravi Kumar" and acme["email"] == "ravi@acme.example"
    assert acme["other_contacts"][0] == {**acme["other_contacts"][0], "contact_name": "Anita Rao",
                                         "email": "anita@acme.example", "source": "hunter"}


def test_company_only_lead_from_job_pages_gets_the_person(client):
    client.post("/api/leads/import", json={"source": "job_page", "csv": "company,website\nBeta Steels,https://beta.example\n"})
    commit(client, "Name,Email\nSam Shah,sam@beta.example\n", source="apollo")
    beta = leads(client)["Beta Steels"]
    assert beta["contact_name"] == "Sam Shah" and beta["email"] == "sam@beta.example" and not beta.get("other_contacts")


# ---- safety
def test_suppressed_addresses_are_skipped_and_reported(app, client):
    app.state.store.update("suppressed", lambda rows: [*rows, "stop@acme.example", "sam.shah@gmail.com"])
    csv = "Name,Company,Email\nNo Thanks,Acme,stop@acme.example\nSam Shah,Beta,SamShah+jobs@gmail.com\nOk Person,Gamma,ok@gamma.example\n"
    r = commit(client, csv)
    assert r["summary"]["skipped"] == 2 and r["summary"]["create"] == 1
    assert [s["row"] for s in r["skipped"]] == [2, 3] and r["summary"]["warnings"]["suppressed"] == 2
    assert list(leads(client)) == ["Gamma"]


def test_unsubscribed_and_disqualified_stages_are_never_resurrected(app, client):
    client.post("/api/leads/import", json={"source": "manual", "csv": (
        "company,contact_name,email,phone\nAcme,Ravi Kumar,ravi@acme.example,9000010001\n"
        "Beta,Sam Shah,sam@beta.example,9000020002\n")})
    ids = {l["company"]: l["id"] for l in client.get("/api/leads").json()}
    client.post(unsub_path(client.app.state.store, ids['Acme']))
    app.state.store.update_lead(ids["Beta"], lambda l: {**l, "stage": "disqualified"})
    csv = ("Company Name,Contact Person,Contact Number,Email ID,Lead Status\n"
           "Acme,Ravi Kumar,9000010001,ravi.new@acme.example,Interested\n"
           "Beta,Sam Shah,9000020002,,Demo Scheduled\n")
    r = commit(client, csv)
    assert r["summary"]["merge"] == 2 and r["summary"]["warnings"]["closed_lead"] == 2
    saved = leads(client)
    assert saved["Acme"]["stage"] == "unsubscribed" and saved["Beta"]["stage"] == "disqualified"


def test_team_sheet_status_sets_the_stage_of_new_leads_only(client):
    csv = "Company Name,Contact Person,Contact Number,Lead Status\nOrion Softworks,Kavya,9000050005,Demo Done\n"
    commit(client, csv)
    assert leads(client)["Orion Softworks"]["stage"] == "call_done"


def test_role_and_free_mail_options(client):
    csv = "Company,Email\nAcme,hr@acme.example\nBeta,someone@yahoo.co.in\n"
    r = preview(client, csv)
    assert r["summary"]["warnings"] == {**r["summary"]["warnings"], "role_address": 1, "free_mail": 1}
    r = preview(client, csv, options={"skip_role_addresses": True, "skip_free_mail": True})
    assert r["summary"]["warnings"]["role_address_skipped"] == 1 and r["summary"]["warnings"]["free_mail_skipped"] == 1
    assert [row["email"] for row in r["rows"]] == [None, None] and r["summary"]["create"] == 2
    only_mail = preview(client, "Email\nsomeone@yahoo.co.in\n", options={"skip_free_mail": True})
    assert only_mail["summary"]["rejected"] == 1 and "chose to leave out" in only_mail["rejected"][0]["reason"]


def test_foreign_numbers_are_kept_and_flagged(client):
    r = commit(client, "Company,Phone\nGlobex,+44 20 7946 0958\n")
    assert r["summary"]["warnings"]["foreign_phone"] == 1
    assert leads(client)["Globex"]["phone"] == "+442079460958" and leads(client)["Globex"]["phone_type"] == "intl"


# ---- undo
def test_undo_removes_untouched_created_leads_and_leaves_merged_details(client):
    client.post("/api/leads/import", json={"source": "manual", "csv": "company,email\nAcme,ravi@acme.example\n"})
    r = commit(client, "Company,Email,City\nAcme,ravi@acme.example,Pune\nBeta,sam@beta.example,\nGamma,meena@gamma.example,\n")
    gamma = leads(client)["Gamma"]["id"]
    client.post(f"/api/leads/{gamma}/note", json={"text": "Called, call back Monday"})
    undo = client.post(f"/api/import/{r['import_id']}/undo").json()
    assert undo["removed"] == 1 and undo["merged_leads_left"] == 1 and "left in place" in undo["note"]
    assert undo["kept"] == [{"lead_id": gamma, "company": "Gamma", "reason": undo["kept"][0]["reason"]}]
    assert undo["kept"][0]["reason"].startswith("changed since the import")
    saved = leads(client)
    assert "Beta" not in saved and saved["Acme"]["city"] == "Pune" and "Gamma" in saved
    assert client.post(f"/api/import/{r['import_id']}/undo").status_code == 400
    assert client.get("/api/import/history").json()[0]["undo"] == {"removed": 1, "kept": 1}
    assert client.post("/api/import/nope/undo").status_code == 404


# ---- input checks and the quick paste importer
def test_bad_input_is_refused_in_plain_words(client):
    assert client.post("/api/import/preview", json={"csv": ""}).json()["detail"] == "Paste a CSV or upload a file first."
    bad = client.post("/api/import/preview", json={"csv": "a\n1\n", "mapping": {"a": "salary"}})
    assert bad.status_code == 400 and "Unknown field" in bad.json()["detail"]


def test_quick_paste_now_reads_other_column_names(client):
    csv = ("First Name,Last Name,Company,Email,Mobile Phone,Website,# Employees\n"
           "Ravi,Kumar,Acme Retail,ravi@acme.example,90000 10001 / 90000 20002,acme.example,51-200\n")
    r = client.post("/api/leads/import", json={"csv": csv, "source": "apollo"}).json()
    assert r["created"] == 1
    acme = client.get("/api/leads").json()[0]
    assert acme["contact_name"] == "Ravi Kumar" and acme["phone"] == "+919000010001"
    assert acme["company_size"] == 125 and acme["domain"] == "acme.example"
