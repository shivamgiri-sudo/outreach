"""With ownership on, prospect-pool people who are another member's CRM lead lose every contact detail for you,
and cannot be added, exported or reached through the company page."""
import pytest

from app import prospect_db as P, prospect_ingest as ingest
from tests.ownership_helpers import rig as make_rig


@pytest.fixture(autouse=True)
def no_sync(monkeypatch):
    monkeypatch.setattr(ingest, "sync_soon", lambda store, every_seconds=120.0: False)


@pytest.fixture
def r(tmp_path): return make_rig(tmp_path)
@pytest.fixture
def off(tmp_path): return make_rig(tmp_path, mode_on=False)


def seeded(r):
    ingest.sync_crm(r.store, force=True)                                   # Acme (Ravi), Beta (Priya), Cora (nobody) become prospects
    P.upsert_person(r.store, {"company_domain": "pool.example", "full_name": "Pool Person", "title": "CEO",
                              "email": "pool@pool.example", "phones": ["9000099999"]}, "apollo")
    people = r.c.get("/api/prospects/people?size=50", headers=r.h["owner"]).json()["results"]
    return {p["full_name"]: p for p in people}


def view(r, who, pid):
    return r.c.get(f"/api/prospects/people/{pid}", headers=r.h[who]).json()


def test_foreign_lead_prospect_loses_contacts(r):
    ids = seeded(r)
    ravi = ids["Ravi Kumar"]["id"]
    res = r.c.get("/api/prospects/people?size=50", headers=r.h["priya"]).json()["results"]
    by = {p["full_name"]: p for p in res}
    assert by["Ravi Kumar"]["contact_hidden"] and not by["Ravi Kumar"].get("email") and not by["Ravi Kumar"].get("linkedin_url")
    assert by["Sita Rao"]["email"] == "sita@beta.in"                         # Priya's own
    assert by["Pool Person"]["email"] == "pool@pool.example"                 # not a CRM lead: shared pool
    assert by["Tej Nair"]["contact_hidden"]                                  # an unowned lead is hidden from members too (as on the leads page)
    one = view(r, "priya", ravi)
    assert one["contact_hidden"] and not one.get("phones") and not one.get("email") and not one.get("provenance")
    assert "98765" not in str(r.c.post("/api/prospects/people/search", json={"filters": {"q": "ravi"}}, headers=r.h["priya"]).text)
    co = r.c.get(f"/api/prospects/companies/{ids['Ravi Kumar']['company']['id']}", headers=r.h["priya"]).json()
    assert "ravi@acme.com" not in str(co)


def test_owner_admin_and_own_lead_see_everything(r):
    ids = seeded(r)
    ravi = ids["Ravi Kumar"]["id"]
    assert view(r, "owner", ravi)["email"] == "ravi@acme.com"
    assert r.c.get(f"/api/prospects/people/{ravi}", headers=r.h["adm"]).status_code == 403   # workspace-limited logins never reach the pool
    assert view(r, "ravi", ravi)["email"] == "ravi@acme.com"


def test_mode_off_hides_nothing(off):
    ids = seeded(off)
    assert view(off, "priya", ids["Ravi Kumar"]["id"])["email"] == "ravi@acme.com"


def test_hidden_person_cannot_be_added_or_exported(r):
    ids = seeded(r)
    ravi = ids["Ravi Kumar"]["id"]
    out = r.c.post("/api/prospects/add-to-leads", json={"person_ids": [ravi]}, headers=r.h["priya"]).json()
    assert not out["added"] and not out["existing"] and out["skipped"][0]["reason"] == "already another member's lead"
    assert "ravi@acme.com" in r.c.post("/api/prospects/export", json={"person_ids": [ravi]}, headers=r.h["owner"]).text


def test_lead_unlinked_but_same_contact_does_not_leak_lead_id(r):
    seeded(r)
    pid = P.upsert_person(r.store, {"company_domain": "acme.com", "full_name": "Ravi Kumar", "title": "HR Head",
                                    "email": "ravi@acme.com"}, "apollo")["id"]
    out = r.c.post("/api/prospects/add-to-leads", json={"person_ids": [pid]}, headers=r.h["priya"]).json()
    assert not out["existing"] and not out["added"]


def test_no_prospect_get_shows_foreign_contact(r):
    seeded(r)
    paths = [p for p, d in r.c.app.openapi()["paths"].items() if "get" in d and "{" not in p and p.startswith("/api/prospects")]
    for who in ("priya", "view"):
        for p in paths:
            res = r.c.get(p, headers=r.h[who])
            if res.status_code == 200:
                for needle in ("ravi@acme.com", "98765 43210", "9876543210", "tej@cora.io"):
                    assert needle not in res.text, (who, p, needle)
