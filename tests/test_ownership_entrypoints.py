"""Every way a lead enters (quick paste, CSV commit, sheet import, inbound capture, Find leads, prospect search) gives it an owner by
the rules when assignment is on and nobody uploaded it, lets a member own what they upload, and does nothing when assignment is off.
With ownership on, an uploader always sees what they just added."""
import pytest
from fastapi.testclient import TestClient

from app import assignment as A, prospect_ingest as ingest
from app.main import create_app
from tests.campaign_fakes import FakeIO, client as fake_client, connect
from tests.test_find_leads import PERSON, go
from tests.test_prospect_api import seed
from tests.ownership_helpers import lead, rig as make_rig

TEAM = {"on": True, "teams": [{"id": "t", "name": "T", "members": ["Ravi"], "default": True, "daily_cap": 0}], "rules": []}


@pytest.fixture(autouse=True)
def no_background_sync(monkeypatch):
    monkeypatch.setattr(ingest, "sync_soon", lambda store, every_seconds=120.0: False)


@pytest.fixture
def r(tmp_path): return make_rig(tmp_path)


def assignment(r, on=True):
    r.store.update("settings", lambda s: {**s, "assignment": {"main": {**TEAM, "on": on}}})


def owner(r, company): return next(l for l in r.store.read("leads") if l["company"] == company).get("owner")


def sheet(company, email): return f"Company Name,Email ID,Contact Person\n{company},{email},Zed\n"


# ---- quick paste (/api/leads/import)
def test_quick_paste_admin_goes_by_the_rules_member_owns(r):
    assignment(r)
    csv = "company,contact_name,email\nPasteA,A One,a@pastea.example\n"
    assert r.c.post("/api/leads/import", json={"csv": csv}, headers=r.h["owner"]).json()["created"] == 1
    assert owner(r, "PasteA") == "Ravi"                                                       # no person owns an admin's paste: the rules
    out = r.c.post("/api/leads/import", json={"csv": "company,contact_name,email\nPasteB,B,b@pasteb.example\nAcme,Ravi,ravi@acme.com\n"},
                   headers=r.h["priya"]).json()
    assert owner(r, "PasteB") == "Priya" and out["list_id"] and out["owned_by_other"][0]["owner"] == "Ravi"
    assert owner(r, "Acme") == "Ravi"                                                          # not merged into Ravi's lead, not taken
    assert "PasteB" in {l["company"] for l in r.c.get("/api/leads", headers=r.h["priya"]).json()}      # she sees what she pasted (ownership on)
    assert "PasteA" not in {l["company"] for l in r.c.get("/api/leads", headers=r.h["priya"]).json()}


def test_quick_paste_assignment_off_leaves_admin_uploads_alone(r):
    assignment(r, on=False)
    r.c.post("/api/leads/import", json={"csv": "company,contact_name,email\nPasteC,C,c@pastec.example\n"}, headers=r.h["owner"])
    assert not owner(r, "PasteC")


# ---- CSV / Excel commit
def test_commit_admin_by_rules_member_owns(r):
    assignment(r)
    r.c.post("/api/import/commit", json={"csv": "Company,Email\nCommitA,a@commita.example\n", "source": "a.csv"}, headers=r.h["owner"])
    assert owner(r, "CommitA") == "Ravi"
    r.c.post("/api/import/commit", json={"csv": "Company,Email\nCommitB,b@commitb.example\n", "source": "b.csv"}, headers=r.h["priya"])
    assert owner(r, "CommitB") == "Priya"
    assignment(r, on=False)
    r.c.post("/api/import/commit", json={"csv": "Company,Email\nCommitC,c@commitc.example\n", "source": "c.csv"}, headers=r.h["owner"])
    assert not owner(r, "CommitC")


# ---- sheet import
def test_sheet_import_admin_by_rules_member_owns(r):
    assignment(r)
    res = r.c.post("/api/leads/import-sheet", json={"csv": sheet("SheetA", "a@sheeta.example"), "tab": "leads"}, headers=r.h["owner"])
    assert res.status_code == 200 and res.json()["created"] == 1 and owner(r, "SheetA") == "Ravi"
    res = r.c.post("/api/leads/import-sheet", json={"csv": sheet("SheetB", "b@sheetb.example"), "tab": "leads"}, headers=r.h["priya"])
    assert res.json()["created"] == 1 and owner(r, "SheetB") == "Priya" and res.json()["list_id"]
    again = r.c.post("/api/leads/import-sheet", json={"csv": sheet("SheetB", "b@sheetb.example"), "tab": "leads"}, headers=r.h["ravi"]).json()
    assert again["duplicates"] == 1 and again["owned_by_other"][0]["owner"] == "Priya" and owner(r, "SheetB") == "Priya"
    assignment(r, on=False)
    r.c.post("/api/leads/import-sheet", json={"csv": sheet("SheetC", "c@sheetc.example"), "tab": "leads"}, headers=r.h["owner"])
    assert not owner(r, "SheetC")


# ---- prospect search -> add to leads
def test_prospect_add_admin_by_rules_member_owns(tmp_path):
    from tests.test_security_auth import ADMIN, add_user, login
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password=ADMIN), client=("203.0.113.9", 50000))
    store = c.app.state.store
    ids = seed(store)
    owner_tok = login(c, ADMIN)
    add_user(c, owner_tok, "ravi@x.example", "caller")
    add_user(c, owner_tok, "cal@x.example", "caller")
    users = {u["email"]: u for u in store.read("settings")["users"]}
    store.update("settings", lambda s: {**s, "assignment": {"main": {**TEAM, "teams": [{**TEAM["teams"][0], "members": [users["ravi@x.example"]["name"]]}]}}})
    auth = {"Authorization": f"Bearer {owner_tok}"}
    out = c.post("/api/prospects/add-to-leads", json={"person_ids": [ids["Dev Nair"]]}, headers=auth).json()
    dev = next(l for l in store.read("leads") if l["id"] == out["added"][0]["lead_id"])
    assert dev["owner"] == users["ravi@x.example"]["name"] and dev["owner_why"]["kind"] == "rule"
    cal = {"Authorization": "Bearer " + login(c, "long-password-1", "cal@x.example")}
    out = c.post("/api/prospects/add-to-leads", json={"person_ids": [ids["Asha Rao"]]}, headers=cal).json()
    assert out["ownership"]["owned"] == 1 and out["list_id"]
    asha = next(l for l in store.read("leads") if l["id"] == out["added"][0]["lead_id"])
    assert asha["owner"] == users["cal@x.example"]["name"]


# ---- Find leads
def test_find_leads_by_rules_when_on_nothing_when_off(tmp_path):
    people = [{"id": "g3", "name": "Lata Sen", "email": "lata@fresh-co.in", "organization": {"name": "Fresh Co", "primary_domain": "fresh-co.in"}}]
    c = fake_client(tmp_path, FakeIO(post={"api_search": {"people": people}}))
    connect(c, "apollo", {"api_key": "ak"})
    store = c.app.state.store
    run = go(c, mode="find_new", max_new=5)
    assert run["report"]["new_leads"] == 1 and not next(l for l in store.read("leads") if l["company"] == "Fresh Co").get("owner")     # off: unowned
    store.update("settings", lambda s: {**s, "assignment": {"main": TEAM}})          # a team-list name with no login of its own is allowed
    people.append({"id": "g4", "name": "Omar Shah", "email": "omar@fresh-two.in", "organization": {"name": "Fresh Two", "primary_domain": "fresh-two.in"}})
    go(c, mode="find_new", max_new=5)
    two = next(l for l in store.read("leads") if l["company"] == "Fresh Two")
    assert two["owner"] == "Ravi" and two["owner_why"]["kind"] == "rule"
    assert not next(l for l in store.read("leads") if l["company"] == "Fresh Co").get("owner")       # an existing lead is never re-owned by a run


# ---- inbound capture (webhook / form): rules when on, hidden from others, ignored when off
def test_inbound_capture_owned_lead_hidden_from_other_members(tmp_path):
    from tests.capture_helpers import add_connector, lead_by_email, make_client, post_lead
    client, store = make_client(tmp_path)
    conn = add_connector(client, "generic", "Form")
    store.update("settings", lambda st: {**st, "users": [{"email": "ann@x.in", "name": "Ann", "role": "caller"}, {"email": "bob@x.in", "name": "Bob", "role": "caller"}],
                                         "ownership_mode": {"main": True}})
    A.save_config(store, "main", {"on": True, "teams": [{"id": "a", "name": "A", "members": ["Ann"], "default": True}], "rules": []})
    assert post_lead(client, conn, {"full_name": "Raj K", "email": "raj@acme.example", "organization": "Acme Ltd"}).status_code == 200
    got = lead_by_email(store, "raj@acme.example")
    assert got["owner"] == "Ann" and got["owner_why"]["kind"] == "rule"
    from app import lead_access
    assert lead_access.can_see(store, {"name": "Ann", "email": "ann@x.in", "role": "caller"}, got)
    assert not lead_access.can_see(store, {"name": "Bob", "email": "bob@x.in", "role": "caller"}, got)
    assert not lead_access.can_send(store, {"name": "Bob", "email": "bob@x.in", "role": "caller"}, got)
