"""Prospect search API and ingestion: roles, add to leads, saved searches, export audit, CSV import, CRM sync, bounces, erasure."""
import pytest
from fastapi.testclient import TestClient

from app import prospect_db as P
from app import prospect_ingest as ingest
from app.inbound import process_delivery_event
from app.main import create_app
from app.prospect_repo import repo_for
from tests.campaign_fakes import by_company, run
from tests.test_security_auth import ADMIN, add_user, bearer, login
from tests.test_sources_pipeline import campaign, make


@pytest.fixture(autouse=True)
def no_background_sync(monkeypatch):
    monkeypatch.setattr(ingest, "sync_soon", lambda store, every_seconds=120.0: False)


def app_client(tmp_path, password=None):
    app = create_app(data_file=tmp_path / "d.json", admin_password=password)
    return TestClient(app, client=("203.0.113.9", 50000)) if password else TestClient(app)


def seed(store):
    P.upsert_company(store, {"domain": "acme.example", "name": "Acme BPO", "industry": "BPO", "employees": 250, "hq_city": "Pune",
                             "signals": [{"kind": "hiring", "title": "12 open roles", "at": "2026-11-01"}]}, "apollo")
    ids = {}
    for name, title, email, status in (("Asha Rao", "Head of HR", "asha@acme.example", "verified"),
                                       ("Bala Iyer", "Recruiter", "bala@acme.example", "valid"),
                                       ("Chitra Das", "CEO", "chitra@acme.example", "invalid"),
                                       ("Dev Nair", "HR Manager", "", None)):
        ids[name] = P.upsert_person(store, {"company_domain": "acme.example", "full_name": name, "title": title, "email": email,
                                            "email_status": status, "phones": ["9876500001"] if name == "Dev Nair" else []}, "apollo")["id"]
    return ids


def test_search_endpoints_and_company_page(tmp_path):
    c = app_client(tmp_path)
    ids = seed(c.app.state.store)
    r = c.get("/api/prospects/people", params=[("title", "hr"), ("seniority", "head"), ("sort", "relevance")]).json()
    assert [p["full_name"] for p in r["results"]] == ["Asha Rao"] and r["took_ms"] < 300
    assert r["results"][0]["email_status"] == "verified" and r["results"][0]["email_confidence"] == 95
    assert c.get("/api/prospects/people", params={"verified_only": "1"}).json()["total"] == 2
    assert c.post("/api/prospects/people/search", json={"filters": {"q": "bala"}}).json()["total"] == 1
    comp = c.get("/api/prospects/companies/acme.example").json()
    assert comp["name"] == "Acme BPO" and len(comp["people"]) == 4 and comp["signals"][0]["kind"] == "hiring"
    assert comp["provenance"]["name"]["entries"][0]["source"] == "apollo"
    person = c.get(f"/api/prospects/people/{ids['Asha Rao']}").json()
    assert person["provenance"]["email"]["value"] == "asha@acme.example" and person["provenance"]["email"]["last_verified"]
    assert c.get("/api/prospects/people/nope").status_code == 404 and c.get("/api/prospects/companies/nope.example").status_code == 404
    facets = c.get("/api/prospects/facets").json()
    assert facets["industries"][0]["value"] == "BPO" and "hiring" in facets["signal_kinds"]
    assert c.get("/api/prospects/stats").json()["people"] == 4
    assert c.get("/api/prospects/companies", params={"q": "acme"}).json()["total"] == 1


def test_add_to_leads_dedupes_skips_suppressed_and_fills_list_and_sequence(tmp_path):
    c = app_client(tmp_path)
    store = c.app.state.store
    ids = seed(store)
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nAcme BPO,Bala,bala@acme.example", "source": "sheet"})
    store.update("suppressed", lambda rows: [*rows, "asha@acme.example"])
    r = c.post("/api/prospects/add-to-leads", json={"person_ids": list(ids.values()), "list_name": "Acme HR", "sequence": True}).json()
    reasons = {s["name"]: s["reason"] for s in r["skipped"]}
    assert reasons["Asha Rao"] == "on the do-not-contact list"
    assert len(r["existing"]) == 1 and len(r["added"]) == 2                       # Bala already in CRM; Chitra (bad email, website) + Dev (phone)
    leads = {l["contact_name"]: l for l in c.get("/api/leads").json()}
    assert leads["Dev Nair"]["source"].startswith("prospect_db:") and leads["Dev Nair"]["phone"] == "+919876500001"
    assert not leads["Chitra Das"].get("email")                                     # an invalid address is never copied into the CRM
    assert r["list_id"] and len(c.get(f"/api/lists/{r['list_id']}").json()["members"]) == 3
    assert r["sequence"]["started"] == 1                                            # only Bala has a usable email
    found = c.get("/api/prospects/people", params={"not_in_crm": "1"}).json()
    assert [p["full_name"] for p in found["results"]] == ["Asha Rao"]
    assert c.post("/api/prospects/add-to-leads", json={}).status_code == 400


def test_add_to_leads_is_workspace_aware(tmp_path):
    c = app_client(tmp_path)
    store = c.app.state.store
    ids = seed(store)
    ws = c.post("/api/workspaces", json={"name": "Client B"}).json()
    r = c.post("/api/prospects/add-to-leads", json={"person_ids": [ids["Dev Nair"]]}, headers={"X-Workspace": ws["id"]}).json()
    lead = next(l for l in store.read("leads") if l["id"] == r["added"][0]["lead_id"])
    assert lead["workspace"] == ws["id"]
    assert c.get("/api/prospects/people", params={"not_in_crm": "1"}, headers={"X-Workspace": ws["id"]}).json()["total"] == 3
    assert c.get("/api/prospects/people", params={"not_in_crm": "1"}, headers={"X-Workspace": "main"}).json()["total"] == 4


def test_add_whole_search_by_filters(tmp_path):
    c = app_client(tmp_path)
    seed(c.app.state.store)
    r = c.post("/api/prospects/add-to-leads", json={"filters": {"title": "hr"}}).json()
    assert len(r["added"]) == 2


def test_saved_searches_are_per_user_and_roles_are_enforced(tmp_path):
    c = app_client(tmp_path, ADMIN)
    owner = login(c)
    add_user(c, owner, "cal@x.example", "caller")
    add_user(c, owner, "view@x.example", "viewer")
    caller = login(c, "long-password-1", "cal@x.example")
    viewer = login(c, "long-password-1", "view@x.example")
    ids = seed(c.app.state.store)
    s = c.post("/api/prospects/saved-searches", json={"name": "HR heads", "filters": {"title": "hr", "seniority": ["head"], "junk": 1}},
               headers=bearer(caller)).json()
    assert s["filters"] == {"title": ["hr"], "seniority": ["head"]}
    assert [x["name"] for x in c.get("/api/prospects/saved-searches", headers=bearer(caller)).json()] == ["HR heads"]
    assert c.get("/api/prospects/saved-searches", headers=bearer(owner)).json() == []
    assert c.delete(f"/api/prospects/saved-searches/{s['id']}", headers=bearer(owner)).status_code == 404
    assert c.get("/api/prospects/people", headers=bearer(viewer)).status_code == 200
    assert c.post("/api/prospects/add-to-leads", json={"person_ids": [ids["Dev Nair"]]}, headers=bearer(viewer)).status_code == 403
    assert c.post("/api/prospects/add-to-leads", json={"person_ids": [ids["Dev Nair"]]}, headers=bearer(caller)).status_code == 200
    for method, path, body in (("post", "/api/prospects/export", {"person_ids": []}), ("post", "/api/prospects/import", {"csv": "a"}),
                               ("post", "/api/prospects/suppress", {"value": "x@y.example"}), ("delete", f"/api/prospects/people/{ids['Asha Rao']}", None),
                               ("put", "/api/prospects/settings", {"disabled_sources": []}), ("post", "/api/prospects/sync-crm", {}),
                               ("post", "/api/prospects/enrich", {"person_ids": ["x"]}), ("get", "/api/prospects/spend", None)):
        kwargs = {"headers": bearer(caller)}
        if body is not None:
            kwargs["json"] = body
        assert getattr(c, method)(path, **kwargs).status_code == 403, path


def test_export_is_owner_only_audited_and_never_includes_suppressed(tmp_path):
    c = app_client(tmp_path)
    store = c.app.state.store
    ids = seed(store)
    c.post("/api/prospects/suppress", json={"value": "bala@acme.example", "reason": "asked"})
    r = c.post("/api/prospects/export", json={"person_ids": list(ids.values())})
    assert r.status_code == 200 and r.headers["x-exported"] == "3" and r.headers["x-left-out"] == "1"
    assert "bala@acme.example" not in r.text and "asha@acme.example" in r.text
    assert any(a["action"] == "prospects.export" and a["target"] == "3 people" for a in store.read("audit"))
    r = c.post("/api/prospects/export", json={"filters": {"title": "recruiter"}})
    assert r.headers["x-exported"] == "0"                                           # the only recruiter is suppressed


def test_erase_and_manual_edit_endpoints(tmp_path):
    c = app_client(tmp_path)
    ids = seed(c.app.state.store)
    edited = c.patch(f"/api/prospects/people/{ids['Asha Rao']}", json={"changes": {"title": "Chief People Officer", "bad": 1}}).json()
    assert edited["title"] == "Chief People Officer"
    assert next(e for e in edited["provenance"]["title"]["entries"] if e["chosen"])["source"] == "manual"
    assert c.delete(f"/api/prospects/people/{ids['Asha Rao']}").json()["ok"]
    assert P.upsert_person(c.app.state.store, {"email": "asha@acme.example"}, "hunter") == {"skipped": "erased"}


def test_csv_import_uses_the_smart_header_mapping(tmp_path):
    c = app_client(tmp_path)
    text = ("Company Name,Contact Person,Designation,Email ID,Mobile,Website,City\n"
            "Zeta Foods Pvt Ltd,Kiran Shah,HR Head,kiran@zeta.example,98765 11111,https://zeta.example,Pune\n"
            "Omega Ltd,,,,,https://omega.example,\n")
    r = c.post("/api/prospects/import", json={"csv": text, "source": "trade fair list"}).json()
    assert r["rows"] == 2 and r["people"] == 1 and r["companies"] == 2
    p = c.get("/api/prospects/people", params={"q": "kiran"}).json()["results"][0]
    assert p["email"] == "kiran@zeta.example" and p["sources"] == ["trade_fair_list"] and p["seniority"] == "head" and p["department"] == "hr"
    assert c.post("/api/prospects/import", json={"csv": "\n"}).status_code == 400


def test_crm_sync_credits_each_field_to_its_source_and_is_incremental(tmp_path):
    c = app_client(tmp_path)
    store = c.app.state.store
    c.post("/api/leads/import", json={"csv": "company,contact_name,title,email,website,open_roles\nAcme BPO,Ravi Kumar,HR Head,ravi@acme.example,https://acme.example,8",
                                      "source": "sheet"})
    lead = store.read("leads")[0]
    store.update_lead(lead["id"], lambda l: {**l, "provenance": {"email": "hunter:https://api.hunter.io"}, "email_check": {"email": "ravi@acme.example", "status": "valid"}})
    first = c.post("/api/prospects/sync-crm", json={}).json()
    assert first["synced"] == 1
    assert c.post("/api/prospects/sync-crm", json={}).json() == {**c.post("/api/prospects/sync-crm", json={}).json(), "synced": 0}
    person = c.get("/api/prospects/people", params={"q": "ravi"}).json()["results"][0]
    assert person["in_crm"] and person["email_status"] == "verified" and set(person["sources"]) == {"crm", "hunter"}
    view = c.get(f"/api/prospects/people/{person['id']}").json()["provenance"]
    assert {e["source"] for e in view["email"]["entries"]} == {"hunter"} and view["title"]["entries"][0]["source"] == "crm"
    company = c.get("/api/prospects/companies/acme.example").json()
    assert company["signals"][0]["kind"] == "hiring"
    c.patch(f"/api/leads/{lead['id']}", json={"title": "CHRO", "reason": "called"})
    assert c.post("/api/prospects/sync-crm", json={}).json()["synced"] == 1
    view = c.get(f"/api/prospects/people/{person['id']}").json()
    assert view["title"] == "CHRO" and any(e["source"] == "manual" for e in view["provenance"]["title"]["entries"])
    store.update("suppressed", lambda rows: [*rows, "ravi@acme.example"])
    c.post("/api/prospects/sync-crm", json={"force": True})
    assert c.get("/api/prospects/people").json()["total"] == 0                     # CRM do-not-contact carries over


def test_bounces_and_complaints_reach_the_prospect_db(tmp_path):
    c = app_client(tmp_path)
    store = c.app.state.store
    ids = seed(store)
    process_delivery_event(store, {"type": "bounce", "email": "asha@acme.example", "provider_id": "b1"})
    assert P.get_person(store, ids["Asha Rao"])["email_status"] == "invalid"
    process_delivery_event(store, {"type": "complaint", "email": "bala@acme.example", "provider_id": "c1"})
    assert P.get_person(store, ids["Bala Iyer"])["suppressed"]


def test_erasing_a_crm_lead_erases_the_prospect(tmp_path):
    c = app_client(tmp_path)
    store = c.app.state.store
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nAcme,Eva,eva@acme.example", "source": "sheet"})
    c.post("/api/prospects/sync-crm", json={})
    assert c.get("/api/prospects/people").json()["total"] == 1
    lead = store.read("leads")[0]
    assert c.post(f"/api/leads/{lead['id']}/erase", json={"confirm": True}).status_code == 200
    assert c.get("/api/prospects/people", params={"include_suppressed": "1"}).json()["total"] == 0
    assert P.upsert_person(store, {"email": "eva@acme.example"}, "apollo") == {"skipped": "erased"}


def test_source_settings_disable_and_purge(tmp_path):
    c = app_client(tmp_path)
    store = c.app.state.store
    seed(store)
    s = c.put("/api/prospects/settings", json={"disabled_sources": ["apollo"], "purge": True}).json()
    assert s["disabled_sources"] == ["apollo"] and "apollo" in s["purged"]
    assert c.get("/api/prospects/people").json()["total"] == 0
    assert "apollo" in c.get("/api/prospects/settings").json()["known_sources"]
    assert P.upsert_company(store, {"domain": "n.example"}, "apollo")["skipped"] == "source_disabled"


def test_campaign_discovery_rows_are_ingested_but_previews_store_nothing(tmp_path):
    c, http = make(tmp_path)
    camp = campaign(c, [])
    run(c, camp["id"], dry_run=True)
    store = c.app.state.store
    assert P.stats(store)["companies"] == 0
    run(c, camp["id"])
    comp = P.get_company(store, "acme-hr.in")
    assert comp and "greenhouse" in comp["sources"] and comp["signals"][0]["kind"] == "hiring"
    assert by_company(c, "Acme HR Services")
    links = repo_for(store).q("SELECT * FROM pdb_crm_links")
    assert len(links) == 1                                                          # the new lead was synced after the run
