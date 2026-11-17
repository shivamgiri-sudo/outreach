""""Where this came from" (app/lead_sources_view.py, GET /api/leads/{id}/sources): each provenance kind, manual edits,
not-recorded values, workspace / ownership isolation, and the DPDP export hook."""
from fastapi.testclient import TestClient

from app import lead_sources_view as V
from app.main import create_app
from tests.ownership_helpers import rig

REG = {"cin": "U72900KA2010PTC000001", "name": "ACME SOFTWARE PRIVATE LIMITED", "status": "Active", "confidence": "high",
       "matched_at": "2026-11-10T05:00:00+00:00", "matched_by": "System", "source": "mca-master-data"}


def make(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": "company,contact_name,email,phone,website\nAcme,Ravi Kumar,ravi@acme.com,+919890779946,https://acme.com\n",
                                      "source": "csv_upload"})
    store = c.app.state.store
    lead = c.get("/api/leads").json()[0]
    store.update_lead(lead["id"], lambda l: {
        **l, "email_source": "website:https://acme.com/contact", "title": "CTO", "company_legal": REG["name"], "address": "12 MG Road",
        "logo_url": "https://acme.com/logo.png", "photo_url": "https://gravatar.com/a.jpg", "linkedin_url": "https://www.linkedin.com/in/ravi",
        "social": {"x": "https://x.com/acme"}, "registry": REG, "email_guess": "r.kumar@acme.com", "email_guess_source": "pattern:f.last",
        "email_guess_status": "catch_all", "city": "Bengaluru",
        "provenance": {"logo_url": "site-markup", "social.x": "site-markup", "company_legal": "mca-master-data", "address": "mca-master-data",
                       "photo_url": "gravatar", "linkedin_url": "prospect_db:apollo", "title": "hunter:https://hunter.io"},
        "email_check": {"email": "ravi@acme.com", "status": "valid", "provider": "zerobounce", "at": "2026-11-12T06:00:00+00:00"},
        "timeline": [*l["timeline"], {"at": "2026-11-11T04:00:00+00:00", "kind": "enrichment", "text": "Find & enrich: city Bengaluru"}]})
    return c, store, lead["id"]


def by_field(body):
    return {r["field"]: r for r in body["fields"]}


def test_parse_source_kinds():
    assert V.parse_source("pattern:first.last") == {"tag": "pattern", "label": "Email format guess", "detail": "first.last"}
    assert V.parse_source("https://acme.com/team")["label"] == "Company website"
    assert V.parse_source("prospect_db:apollo")["label"] == "Prospect database (Apollo)"
    assert V.parse_source("site-markup")["label"] == "Company website markup"
    assert V.parse_source("mca-master-data")["label"].startswith("Company register")
    assert V.parse_source("")["label"] == "not recorded"
    assert V.parse_source("github:https://github.com/x")["tag"] == "github"           # a source added later still shows


def test_every_kind_and_not_recorded(tmp_path):
    c, store, lid = make(tmp_path)
    body = c.get(f"/api/leads/{lid}/sources").json()
    f = by_field(body)
    assert f["email"]["source_label"] == "Company website" and f["email"]["detail"] == "https://acme.com/contact"
    assert f["email"]["kind"] == "confirmed" and f["email"]["check"]["provider"] == "zerobounce" and f["email"]["email_state"] == "confirmed"
    assert f["email_guess"]["kind"] == "guessed" and f["email_guess"]["check"]["status"] == "catch_all"
    assert f["logo_url"]["source_label"] == "Company website markup" and f["logo_url"]["kind"] == "found"
    assert f["social.x"]["source"] == "site-markup" and f["social.x"]["label"] == "X (Twitter)"
    assert f["company_legal"]["at"] == REG["matched_at"] and f["company_legal"]["at_basis"] == "register match"
    assert f["photo_url"]["source_label"] == "Gravatar public profile"
    assert f["linkedin_url"]["source_label"] == "Prospect database (Apollo)"
    assert f["title"]["source_label"] == "Hunter"
    assert f["registry"]["value"].endswith("(CIN U72900KA2010PTC000001)") and f["registry"]["kind"] == "found"
    for field in ("phone", "contact_name", "company", "website"):                       # came with the import, but no record per field
        assert f[field]["source_label"] == "not recorded" and f[field]["kind"] == ""
    assert f["city"]["at"] == "2026-11-11T04:00:00+00:00" and f["city"]["at_basis"] == "history"
    assert body["not_recorded"] == 5 and body["origin"]["raw"] == "csv_upload"            # city has a date but no source either
    assert "Not recorded" not in str(body["origin"])


def test_manual_edit_wins_while_its_value_stands(tmp_path):
    c, store, lid = make(tmp_path)
    assert c.patch(f"/api/leads/{lid}", json={"title": "Founder", "reason": "spoke to him"}).status_code == 200
    f = by_field(c.get(f"/api/leads/{lid}/sources").json())
    assert f["title"]["kind"] == "typed" and f["title"]["source"] == "manual" and f["title"]["at"] and f["title"]["by"]
    store.update_lead(lid, lambda l: {**l, "title": "CTO"})                             # changed again by something else
    assert by_field(c.get(f"/api/leads/{lid}/sources").json())["title"]["source_label"] == "Hunter"


def test_workspace_and_ownership_isolation(tmp_path):
    r = rig(tmp_path)
    other = r.other["id"]
    assert r.c.get(f"/api/leads/{other}/sources", headers={**r.h["owner"], "X-Workspace": r.w2}).status_code == 200
    assert r.c.get(f"/api/leads/{other}/sources", headers=r.h["adm"]).status_code == 404            # admin of main only
    acme, beta = r.lead["Acme"]["id"], r.lead["Beta Ltd"]["id"]
    assert r.c.get(f"/api/leads/{acme}/sources", headers=r.h["priya"]).status_code == 404           # Ravi's lead, ownership on
    assert r.c.get(f"/api/leads/{beta}/sources", headers=r.h["priya"]).status_code == 200
    assert r.c.get("/api/leads/nope/sources", headers=r.h["owner"]).status_code == 404


def test_dpdp_export_includes_it(tmp_path):
    c, store, lid = make(tmp_path)
    data = c.get(f"/api/leads/{lid}/export").json()
    assert any(r["field"] == "email" and r["source_label"] == "Company website" for r in data["where_each_value_came_from"])
