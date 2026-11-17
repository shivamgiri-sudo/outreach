"""Company register routes and lead matching (app/routes_company_register.py, app/company_register_leads.py). No network:
the register is filled by uploading CSV text through the API, and the data.gov.in pull uses a fake HTTP transport."""
import pytest
from fastapi.testclient import TestClient

from app import company_register as R, routes_company_register
from app.main import create_app

CSV = ("CORPORATE_IDENTIFICATION_NUMBER,COMPANY_NAME,COMPANY_STATUS,COMPANY_CLASS,COMPANY_CATEGORY,DATE_OF_REGISTRATION,"
       "REGISTERED_STATE,AUTHORIZED_CAP,PAIDUP_CAPITAL,PRINCIPAL_BUSINESS_ACTIVITY_AS_PER_CIN,REGISTERED_OFFICE_ADDRESS,REGISTRAR_OF_COMPANIES\n"
       "U72200MH2010PTC123456,ACME TECHNOLOGIES PRIVATE LIMITED,Active,Private,Company limited by Shares,05-01-2010,"
       "Maharashtra,1000000,500000,Business Services,\"12 MG Road, Mumbai\",RoC-Mumbai\n"
       "U15400KA2015PTC111111,ZETA FOODS PRIVATE LIMITED,Active,Private,Company limited by Shares,09-07-2015,Karnataka,"
       "100000,100000,Manufacturing,\"1 Brigade Road, Bangalore\",RoC-Bangalore\n"
       "U15400DL2016PTC222222,ZETA FOODS LIMITED,Active,Public,Company limited by Shares,09-07-2016,Delhi,100000,100000,"
       "Manufacturing,\"Connaught Place, New Delhi\",RoC-Delhi\n")


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setattr(routes_company_register, "BACKGROUND", False)
    app = create_app(data_file=tmp_path / "d.json", admin_password="admin-pass-123")
    c = TestClient(app, client=("203.0.113.9", 50000))
    ho = {"Authorization": "Bearer " + c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]}
    c.post("/api/leads/import", json={"csv": "company,email,city,state\n"
                                             "Acme Technologies Pvt Ltd,ravi@acme.com,Mumbai,\n"
                                             "Zeta Foods,sita@zeta.in,,\n"
                                             "Nobody Known Traders,amit@nobody.in,,\n", "source": "manual"}, headers=ho)
    yield c, ho, app
    R.forget(app.state.store)


def leads(c, ho):
    return {l["company"]: l for l in c.get("/api/leads", headers=ho).json()}


def upload(c, ho, text=CSV, name="mh.csv"):
    return c.post(f"/api/company-register/upload?filename={name}", content=text.encode(), headers={**ho, "Content-Type": "text/csv"})


def test_upload_imports_and_summary_counts_per_state(app_client):
    c, ho, _ = app_client
    r = upload(c, ho)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "done" and r.json()["files"][0]["written"] == 3
    s = c.get("/api/company-register", headers=ho).json()
    assert s["total"] == 3 and {p["state"]: p["count"] for p in s["per_state"]} == {"Maharashtra": 1, "Karnataka": 1, "Delhi": 1}
    assert s["last_import"]["name"] == "mh.csv" and "GODL" in s["licence"] and s["size_bytes"] > 0
    assert upload(c, ho, "").status_code == 400
    again = upload(c, ho, CSV.replace("Active,Private,Company limited by Shares,05-01-2010", "Strike Off,Private,Company limited by Shares,05-01-2010"))
    assert again.json()["files"][-1]["added"] == 0 and c.get("/api/company-register", headers=ho).json()["total"] == 3   # upsert by CIN


def test_single_lead_high_match_attaches_and_fills_only_empty_fields(app_client):
    c, ho, _ = app_client
    upload(c, ho)
    acme = leads(c, ho)["Acme Technologies Pvt Ltd"]
    r = c.post(f"/api/leads/{acme['id']}/company-register", headers=ho)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["confidence"] == "high" and body["attached"] is True
    reg = body["registry"]
    assert reg["cin"] == "U72200MH2010PTC123456" and reg["status"] == "Active" and reg["class"] == "Private"
    assert reg["incorporated"] == "2010-01-05" and reg["paid_up"] == 500000 and reg["authorised"] == 1000000
    assert reg["roc"] == "RoC-Mumbai" and reg["activity"] == "Business Services" and reg["source"] == "mca-master-data"
    assert "email" not in reg and reg["confidence"] == "high" and reg["matched_at"]
    saved = c.get(f"/api/leads/{acme['id']}", headers=ho).json()
    assert saved["company"] == "Acme Technologies Pvt Ltd"                      # never overwritten
    assert saved["company_legal"] == "ACME TECHNOLOGIES PRIVATE LIMITED" and saved["state"] == "Maharashtra"
    assert saved["address"] == "12 MG Road, Mumbai" and saved["city"] == "Mumbai"
    assert saved["provenance"]["company_legal"] == "mca-master-data" and saved["provenance"]["state"] == "mca-master-data"
    assert sorted(saved["registry"]["filled"]) == ["address", "company_legal", "state"]
    assert any("company register" in e["text"] for e in saved["timeline"])


def test_medium_is_suggested_then_confirmed_or_rejected(app_client):
    c, ho, _ = app_client
    upload(c, ho)
    zeta = leads(c, ho)["Zeta Foods"]
    body = c.post(f"/api/leads/{zeta['id']}/company-register", headers=ho).json()
    assert body["confidence"] == "medium" and body["attached"] is False and body["registry"] is None   # two Zeta Foods, no state
    match = body["registry_match"]
    cins = {match["best"]["cin"], *[a["cin"] for a in match["alternatives"]]}
    assert cins == {"U15400KA2015PTC111111", "U15400DL2016PTC222222"}
    r = c.post(f"/api/leads/{zeta['id']}/company-register/reject", json={"cin": "U15400DL2016PTC222222"}, headers=ho).json()
    assert r["registry"] is None and r["registry_rejected"] == ["U15400DL2016PTC222222"]
    assert all(x["cin"] != "U15400DL2016PTC222222" for x in r["registry_match"]["alternatives"])
    again = c.post(f"/api/leads/{zeta['id']}/company-register", headers=ho).json()     # a rejected CIN never comes back
    assert again["confidence"] == "high" and again["registry"]["cin"] == "U15400KA2015PTC111111"
    out = c.post(f"/api/leads/{zeta['id']}/company-register/reject", json={"cin": "U15400KA2015PTC111111"}, headers=ho).json()
    saved = c.get(f"/api/leads/{zeta['id']}", headers=ho).json()
    assert out["registry"] is None and not saved.get("company_legal") and "company_legal" not in saved.get("provenance", {})
    ok = c.post(f"/api/leads/{zeta['id']}/company-register/confirm", json={"cin": "U15400DL2016PTC222222"}, headers=ho)
    assert ok.status_code == 200 and ok.json()["registry"]["confidence"] == "confirmed"
    saved = c.get(f"/api/leads/{zeta['id']}", headers=ho).json()
    assert saved["registry"]["cin"] == "U15400DL2016PTC222222" and "U15400DL2016PTC222222" not in saved["registry_rejected"]
    assert saved["state"] == "Delhi" and saved["registry"]["matched_by"]
    assert c.post(f"/api/leads/{zeta['id']}/company-register/confirm", json={"cin": "U00000XX0000PTC000000"}, headers=ho).status_code == 400


def test_low_confidence_is_never_attached_and_an_empty_register_says_so(app_client):
    c, ho, _ = app_client
    nobody = leads(c, ho)["Nobody Known Traders"]
    r = c.post(f"/api/leads/{nobody['id']}/company-register", headers=ho)
    assert r.status_code == 400 and "empty" in r.json()["detail"]
    upload(c, ho, CSV + "U51100GJ2000PTC333333,KNOWN WORLD TRADING PRIVATE LIMITED,Active,Private,,01-01-2000,Gujarat,1,1,Trade,Surat,RoC-Ahmedabad\n")
    body = c.post(f"/api/leads/{nobody['id']}/company-register", headers=ho).json()
    assert body["confidence"] == "low" and body["attached"] is False and body["registry"] is None
    saved = c.get(f"/api/leads/{nobody['id']}", headers=ho).json()
    assert "registry" not in saved and not saved.get("company_legal")
    assert saved["registry_match"]["confidence"] == "low"


def test_batch_matches_bounded_and_skips_recent_checks(app_client):
    c, ho, _ = app_client
    upload(c, ho)
    assert c.post("/api/company-register/batch", json={"limit": 501}, headers=ho).status_code == 422
    state = c.post("/api/company-register/batch", json={"limit": 500}, headers=ho).json()
    assert state["status"] == "done" and state["total"] == 3 and state["attached"] == 1 and state["suggested"] == 1 and state["low"] == 1
    assert c.get("/api/company-register/batch", headers=ho).json()["done"] == 3
    assert c.post("/api/company-register/batch", json={"limit": 500}, headers=ho).json()["total"] == 0


def test_owner_only_import_and_batch_callers_may_match_one_lead(app_client):
    c, ho, _ = app_client
    c.post("/api/users", json={"email": "ravi@example.com", "name": "Ravi", "role": "caller", "password": "long-password-1"}, headers=ho)
    hr = {"Authorization": "Bearer " + c.post("/api/login", json={"email": "ravi@example.com", "password": "long-password-1"}).json()["token"]}
    assert upload(c, hr).status_code == 403
    assert c.get("/api/company-register", headers=hr).status_code == 403
    assert c.post("/api/company-register/batch", json={"limit": 5}, headers=hr).status_code == 403
    assert c.post("/api/company-register/pull", json={}, headers=hr).status_code == 403
    upload(c, ho)
    acme = leads(c, ho)["Acme Technologies Pvt Ltd"]
    r = c.post(f"/api/leads/{acme['id']}/company-register", headers=hr)
    assert r.status_code == 200 and r.json()["registry"]["matched_by"] == "Ravi"


def test_workspace_isolation_of_lead_writes(app_client):
    c, ho, _ = app_client
    upload(c, ho)
    ws = c.post("/api/workspaces", json={"name": "Client B"}, headers=ho).json()
    ws_id = ws.get("id") or ws.get("workspace", {}).get("id")
    acme = leads(c, ho)["Acme Technologies Pvt Ltd"]
    other = {**ho, "X-Workspace": ws_id}
    assert c.post(f"/api/leads/{acme['id']}/company-register", headers=other).status_code == 404
    assert c.post(f"/api/leads/{acme['id']}/company-register/confirm", json={"cin": "U72200MH2010PTC123456"}, headers=other).status_code == 404
    assert c.post(f"/api/leads/{acme['id']}/company-register/reject", json={"cin": "U72200MH2010PTC123456"}, headers=other).status_code == 404
    assert c.post("/api/company-register/batch", json={"limit": 50}, headers=other).json()["total"] == 0   # no leads in that workspace
    assert "registry" not in c.get(f"/api/leads/{acme['id']}", headers=ho).json()


def test_api_pull_pages_resumes_and_needs_the_key(app_client, monkeypatch):
    c, ho, app = app_client
    assert c.post("/api/company-register/pull", json={"max_pages": 1}, headers=ho).status_code == 400
    rid = "1a2b3c4d-0000-1111-2222-333344445555"
    store = app.state.store
    store.write("integrations", {**store.read("integrations"), "data_gov_in": {"mode": "api", "values": {"api_key": "k", "resource_id": rid}}})
    calls = []

    def fake_http(method, url, headers, body, form):
        calls.append(url)
        offset = int(url.split("offset=")[1].split("&")[0])
        n = 500 if offset < 1000 else 3
        return {"total": 1003, "records": [{"corporate_identification_number": f"U72200MH2010PTC{offset + i:06d}",
                                            "company_name": f"Firm {offset + i} Pvt Ltd", "company_status": "Active"} for i in range(n)]}
    jobs = app.state.store._register_jobs
    jobs.transports = {"source_http": fake_http}
    jobs.sleep = lambda s: None
    first = c.post("/api/company-register/pull", json={"max_pages": 1}, headers=ho).json()
    assert first["status"] == "done" and first["written"] == 500 and first["pages"] == 1
    second = c.post("/api/company-register/pull", json={"max_pages": 10}, headers=ho).json()
    assert second["offset_start"] == 500 and second["written"] == 503 and second.get("complete") is True
    assert c.get("/api/company-register", headers=ho).json()["total"] == 1003 and len(calls) == 3
    assert all("api-key=k" in u for u in calls)
