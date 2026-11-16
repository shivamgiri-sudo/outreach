"""Lead harvest API: auth and roles, workspace isolation, Places connect hint, run + results + CSV export, DND upload."""
import pytest
from fastapi.testclient import TestClient

from app import harvest
from app.main import create_app
from tests.harvest_fakes import PlacesFake, fake_http, osm_element, page, place

PASS = "admin-pass-123"


@pytest.fixture(autouse=True)
def no_worker(monkeypatch):
    monkeypatch.setattr(harvest, "KICK", False)
    monkeypatch.setattr(harvest, "_last_call", {})


def rig(tmp_path, http=None):
    http = http or fake_http(PlacesFake({("dentist in Pune", ""): page(1, 3)}), overpass={"elements": [osm_element(1)]})
    app = create_app(data_file=tmp_path / "h.json", admin_password=PASS, campaign_transports={"source_http": http})
    c = TestClient(app, client=("203.0.113.9", 50000))
    owner = {"Authorization": "Bearer " + c.post("/api/login", json={"password": PASS}).json()["token"]}
    store = app.state.store

    def login_as(email, role, workspaces=(), account=""):
        r = c.post("/api/users", json={"email": email, "name": email.split("@")[0], "role": role, "password": "long-password-1",
                                       "workspaces": list(workspaces), "account_id": account}, headers=owner)
        assert r.status_code == 200, r.text
        return {"Authorization": "Bearer " + c.post("/api/login", json={"email": email, "password": "long-password-1"}).json()["token"]}
    return c, owner, store, login_as, http


def connect_places(store):
    store.update("integrations", lambda s: {**s, "google_places": {"mode": "api", "values": {"api_key": "fake-key"}}})


def run(store):
    env_for = harvest.env_of(store)
    fast = lambda job: harvest.Env(**{**env_for(job).__dict__, "sleep": lambda s: None, "clock": lambda: 0.0})
    return harvest.run_until_idle(store, fast)


def test_google_needs_a_key_then_runs_and_exports(tmp_path):
    c, owner, store, _, http = rig(tmp_path)
    over = c.get("/api/harvest", headers=owner).json()
    assert over["sources"]["google_places"]["connected"] is False and "Integrations" in over["sources"]["google_places"]["reason"]
    body = {"keywords": "dentist", "locations": "Pune", "sources": ["google_places", "osm"], "max_tiles": 4}
    r = c.post("/api/harvest/jobs", json=body, headers=owner)
    assert r.status_code == 400 and "Integrations" in r.json()["detail"]
    connect_places(store)
    est = c.post("/api/harvest/estimate", json=body, headers=owner).json()
    assert est["pairs"] == 1 and est["estimate"]["google_max"] == 15 and est["estimate"]["default_cap"] == 15
    job = c.post("/api/harvest/jobs", json={**body, "credit_cap": 5, "name": "Pune dentists"}, headers=owner).json()
    assert job["status"] == "draft" and job["credit_cap"] == 5 and job["progress"]["pairs_total"] == 1
    assert c.post(f"/api/harvest/jobs/{job['id']}/start", headers=owner).json()["status"] == "queued"
    assert c.post(f"/api/harvest/jobs/{job['id']}/start", headers=owner).status_code == 409
    run(store)
    detail = c.get(f"/api/harvest/jobs/{job['id']}", headers=owner).json()
    assert detail["status"] == "done" and detail["counts"]["new"] == 4 and detail["credits_used"] == 1
    assert detail["pairs"][0]["location"] == "Pune"
    assert http.calls[0]["headers"]["X-Goog-Api-Key"] == "fake-key"
    from app.prospect_waterfall import month_of, month_spend
    assert month_spend(store, month_of()) == 1.0                  # the paid search is in the shared lookup ledger
    res = c.get(f"/api/harvest/jobs/{job['id']}/results", headers=owner).json()
    assert res["total"] == 4 and "Google" in res["attribution"] and all(r["lead_id"] for r in res["rows"])
    assert {r["source"] for r in res["rows"]} == {"google_places", "osm"}
    csv = c.get(f"/api/harvest/jobs/{job['id']}/export.csv", headers=owner)
    assert csv.status_code == 200 and csv.text.startswith("﻿company,category,phone")
    assert "Fake Dental Care 1" in csv.text and "attachment" in csv.headers["content-disposition"]
    assert any(a["action"] == "export.download" for a in store.read("audit"))


def test_export_cells_are_formula_safe(tmp_path):
    http = fake_http(PlacesFake({("dentist in Pune", ""): {"places": [place(1, displayName={"text": "=HYPERLINK(\"x\")"})]}}))
    c, owner, store, _, _ = rig(tmp_path, http)
    connect_places(store)
    job = c.post("/api/harvest/jobs", json={"keywords": "dentist", "locations": "Pune", "sources": ["google_places"], "start": True}, headers=owner).json()
    run(store)
    text = c.get(f"/api/harvest/jobs/{job['id']}/export.csv", headers=owner).text
    assert "'=HYPERLINK" in text or "\"'=HYPERLINK" in text


def test_roles_callers_like_import_but_no_paid_google_viewers_read_only(tmp_path):
    c, owner, store, login_as, _ = rig(tmp_path)
    connect_places(store)
    caller, viewer = login_as("cal@example.com", "caller"), login_as("view@example.com", "viewer")
    osm = {"keywords": "dentist", "locations": "Pune", "sources": ["osm"]}
    r = c.post("/api/harvest/jobs", json={**osm, "sources": ["google_places"]}, headers=caller)
    assert r.status_code == 403 and "owner" in r.json()["detail"]
    mine = c.post("/api/harvest/jobs", json=osm, headers=caller).json()
    assert c.post(f"/api/harvest/jobs/{mine['id']}/start", headers=caller).status_code == 200
    assert c.post(f"/api/harvest/jobs/{mine['id']}/pause", headers=caller).json()["status"] == "paused"
    paid = c.post("/api/harvest/jobs", json={**osm, "sources": ["google_places"]}, headers=owner).json()
    assert c.post(f"/api/harvest/jobs/{paid['id']}/start", headers=caller).status_code == 403     # a caller cannot start paid searches
    assert c.get(f"/api/harvest/jobs/{mine['id']}/export.csv", headers=caller).status_code == 403
    assert c.post("/api/harvest/dnd", json={"text": "98220 00001"}, headers=caller).status_code == 403
    assert c.post("/api/harvest/jobs", json=osm, headers=viewer).status_code == 403
    assert c.post(f"/api/harvest/jobs/{mine['id']}/cancel", headers=viewer).status_code == 403
    assert len(c.get("/api/harvest/jobs", headers=viewer).json()) == 2
    assert c.get("/api/harvest/jobs").status_code == 401                                        # no login


def test_workspace_isolation(tmp_path):
    from app import accounts, workspaces
    c, owner, store, login_as, _ = rig(tmp_path)
    acct = accounts.create(store, "Client Co", "test")["id"]
    ws = workspaces.create(store, "Client One", "test", account_id=acct)["id"]
    raj = login_as("raj@client.example", "caller", [ws], acct)
    main_job = c.post("/api/harvest/jobs", json={"keywords": "dentist", "locations": "Pune", "sources": ["osm"], "start": True}, headers=owner).json()
    run(store)
    raj_job = c.post("/api/harvest/jobs", json={"keywords": "dentist", "locations": "Pune", "sources": ["osm"], "start": True},
                     headers={**raj, "X-Workspace": ws}).json()
    run(store)
    assert harvest.get(store, raj_job["id"])["ws"] == ws and harvest.get(store, raj_job["id"])["account"] == acct
    assert [j["id"] for j in c.get("/api/harvest/jobs", headers=raj).json()] == [raj_job["id"]]
    for path in (f"/api/harvest/jobs/{main_job['id']}", f"/api/harvest/jobs/{main_job['id']}/results"):
        assert c.get(path, headers=raj).status_code == 404
    assert c.post(f"/api/harvest/jobs/{main_job['id']}/cancel", headers=raj).status_code == 404
    ws_leads = [l for l in store.read("leads") if l.get("workspace") == ws]
    assert len(ws_leads) == 1 and raj_job["id"] in ws_leads[0]["harvest_jobs"]                 # its own lead, not merged into main's
    assert len(c.get("/api/harvest/jobs", headers=owner).json()) == 2


def test_dnd_upload_feeds_the_suppression_list_and_flags_harvested_leads(tmp_path):
    http = fake_http(PlacesFake({("dentist in Pune", ""): {"places": [place(1), place(2)]}}))
    c, owner, store, login_as, _ = rig(tmp_path, http)
    connect_places(store)
    r = c.post("/api/harvest/dnd", json={"text": "Phone,Name\n98220 00002,Someone\n+91 98220 00009\n12345\n98220-00002"}, headers=owner)
    assert r.json() == {"added": 2, "already": 0, "invalid": 1, "total": 2}
    assert {"+919822000002", "+919822000009"} <= set(store.read("suppressed"))
    assert c.post("/api/harvest/dnd", json={"text": "nothing here"}, headers=owner).status_code == 400
    assert c.get("/api/harvest/dnd", headers=owner).json() == {"numbers": 2}
    job = c.post("/api/harvest/jobs", json={"keywords": "dentist", "locations": "Pune", "sources": ["google_places"], "start": True}, headers=owner).json()
    run(store)
    detail = c.get(f"/api/harvest/jobs/{job['id']}", headers=owner).json()
    assert detail["counts"]["dnd"] == 1 and detail["counts"]["new"] == 2
    rows = {r["company"]: r for r in c.get(f"/api/harvest/jobs/{job['id']}/results", headers=owner).json()["rows"]}
    assert rows["Fake Dental Care 2"]["dnd"] is True and rows["Fake Dental Care 1"]["dnd"] is False
    export = c.get("/api/export/suppressed.csv", headers=owner).text
    assert "+919822000002,phone" in export


def test_csv_upload_template_and_limits(tmp_path):
    c, owner, *_ = rig(tmp_path)
    tpl = c.get("/api/harvest/template.csv", headers=owner)
    assert tpl.status_code == 200 and "keyword,location" in tpl.text
    est = c.post("/api/harvest/estimate", json={"csv": tpl.text.lstrip("﻿"), "sources": ["osm"]}, headers=owner).json()
    assert est["pairs"] == 3 and est["errors"] == []
    many = "\n".join(f"kw{i},Pune" for i in range(501))
    assert c.post("/api/harvest/estimate", json={"csv": many, "sources": ["osm"]}, headers=owner).status_code == 400
    bad = c.post("/api/harvest/jobs", json={"csv": "x\n,", "sources": ["osm"]}, headers=owner)
    assert bad.status_code == 400
    assert c.post("/api/harvest/jobs", json={"keywords": "dentist", "locations": "Pune", "sources": []}, headers=owner).status_code == 400
