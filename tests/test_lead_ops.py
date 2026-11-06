"""Lead owner, tags, bulk actions (per-lead reasons), saved views and list extras."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.activities import add_task
from app.main import create_app
from tests.flow_helpers import lead, make_client, patch_lead, store_of

ADMIN = "admin-pass-123"
CSV3 = ("company,contact_name,email,website,open_roles,company_size,country\n"
        "Acme,Ravi,ravi@acme.com,https://acme.com,12,200,IN\n"
        "Beta,Sam,sam@beta.in,https://beta.in,1,10,IN\n"
        "Gamma,Neha,,https://gamma.in,3,50,IN\n")


def team_client(tmp_path):
    c = make_client(tmp_path, csv=CSV3)
    c.put("/api/team", json={"team": [{"name": "Priya"}, {"name": "Amit"}]})
    return c


def ids(c, *companies):
    return [lead(c, name)["id"] for name in companies]


# ---------- owner ----------
def test_owner_is_set_from_the_team_list_and_logged_with_who(tmp_path):
    c = team_client(tmp_path)
    lid = lead(c, "Acme")["id"]
    assert "Priya" in c.get("/api/lead-owners").json()["owners"]
    r = c.post(f"/api/leads/{lid}/owner", json={"owner": "priya"})       # matched case-insensitively, saved as spelled
    assert r.status_code == 200 and r.json()["owner"] == "Priya"
    last = r.json()["timeline"][-1]
    assert last["kind"] == "owner" and last["by"] == "This computer" and "nobody → Priya" in last["text"]
    assert c.post(f"/api/leads/{lid}/owner", json={"owner": "Stranger"}).status_code == 400
    r = c.post(f"/api/leads/{lid}/owner", json={"owner": ""})
    assert r.json()["owner"] == "" and "Priya → nobody" in r.json()["timeline"][-1]["text"]
    assert c.post("/api/leads/nope/owner", json={"owner": "Priya"}).status_code == 404


def test_a_reply_gives_an_unowned_lead_to_its_open_task_owner(tmp_path):
    c = team_client(tmp_path)
    lid = lead(c, "Acme")["id"]
    add_task(store_of(c), lid, "call", "Call them", due_at=datetime.now(timezone.utc) + timedelta(hours=3), owner="Amit")
    c.post(f"/api/leads/{lid}/reply", json={"text": "Can you tell me the price?"})
    got = lead(c, "Acme")
    assert got["owner"] == "Amit" and any(e["kind"] == "owner" and e["by"] == "auto" for e in got["timeline"])


def test_a_reply_never_takes_a_lead_from_its_owner_and_falls_back_to_the_linkedin_owner(tmp_path):
    c = team_client(tmp_path)
    acme, beta = ids(c, "Acme", "Beta")
    c.post(f"/api/leads/{acme}/owner", json={"owner": "Priya"})
    add_task(store_of(c), acme, "call", "x", owner="Amit")
    c.post(f"/api/leads/{acme}/reply", json={"text": "Tell me more"})
    assert lead(c, "Acme")["owner"] == "Priya"
    store_of(c).update("settings", lambda s: {**s, "linkedin_owner": "Amit"})
    patch_lead(c, beta, linkedin_url="https://www.linkedin.com/in/sam")
    c.post(f"/api/leads/{beta}/reply", json={"text": "Tell me more"})
    assert lead(c, "Beta")["owner"] == "Amit"


# ---------- tags ----------
def test_tags_add_and_remove_without_duplicates(tmp_path):
    c = team_client(tmp_path)
    lid = lead(c, "Acme")["id"]
    r = c.post(f"/api/leads/{lid}/tags", json={"add": ["VIP", " noida  bpo ", "vip"]})
    assert r.json()["tags"] == ["VIP", "noida bpo"] and r.json()["timeline"][-1]["kind"] == "tags"
    r = c.post(f"/api/leads/{lid}/tags", json={"remove": ["vip"]})
    assert r.json()["tags"] == ["noida bpo"]
    assert c.post(f"/api/leads/{lid}/tags", json={}).status_code == 400
    assert c.get("/api/lead-tags").json() == [{"tag": "noida bpo", "count": 1}]


# ---------- bulk ----------
def test_bulk_owner_and_tags_answer_per_lead(tmp_path):
    c = team_client(tmp_path)
    acme, beta = ids(c, "Acme", "Beta")
    r = c.post("/api/leads/bulk", json={"lead_ids": [acme, beta, "ghost"], "action": "owner", "value": "Priya"}).json()
    assert (r["ok"], r["failed"]) == (2, 1) and r["results"][2] == {"id": "ghost", "ok": False, "reason": "not found"}
    assert lead(c, "Beta")["owner"] == "Priya"
    r = c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "owner", "value": "Priya"}).json()
    assert r["results"][0]["reason"] == "already owned by Priya"
    c.post("/api/leads/bulk", json={"lead_ids": [acme, beta], "action": "tag_add", "value": "Hot list"})
    r = c.post("/api/leads/bulk", json={"lead_ids": [acme, beta], "action": "tag_add", "value": "hot list"}).json()
    assert r["ok"] == 0 and r["reasons"] == [{"reason": "already tagged", "count": 2}]
    r = c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "tag_remove", "value": "HOT LIST"}).json()
    assert r["ok"] == 1 and lead(c, "Acme")["tags"] == [] and lead(c, "Beta")["tags"] == ["Hot list"]
    assert c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "owner", "value": "Stranger"}).status_code == 400
    assert c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "explode"}).status_code == 400
    assert c.post("/api/leads/bulk", json={"lead_ids": [], "action": "tag_add", "value": "x"}).status_code == 400


def test_bulk_stage_follows_the_stage_rules_unless_an_owner_forces(tmp_path):
    c = team_client(tmp_path)
    acme, beta = ids(c, "Acme", "Beta")
    r = c.post("/api/leads/bulk", json={"lead_ids": [acme, beta], "action": "stage", "value": "contacted"}).json()
    assert r["ok"] == 0 and "cannot move from new to contacted" in r["results"][0]["reason"]
    r = c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "stage", "value": "verified"}).json()
    assert r["ok"] == 1 and lead(c, "Acme")["stage"] == "verified"
    r = c.post("/api/leads/bulk", json={"lead_ids": [beta], "action": "stage", "value": "contacted", "force": True}).json()
    assert r["ok"] == 1 and "forced" in lead(c, "Beta")["timeline"][-1]["text"]
    r = c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "stage", "value": "interested", "force": True}).json()
    assert r["results"][0]["reason"] == "no recorded consent for Interested"       # force never skips consent
    assert c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "stage", "value": "bogus"}).status_code == 400


def test_bulk_unsubscribe_is_final_even_when_forced(tmp_path):
    c = team_client(tmp_path)
    acme = ids(c, "Acme")[0]
    c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "stage", "value": "unsubscribed"})
    assert any(r["event"] == "withdrawn" for r in store_of(c).read("consent_ledger"))
    r = c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "stage", "value": "new", "force": True}).json()
    assert r["ok"] == 0 and lead(c, "Acme")["stage"] == "unsubscribed"


def test_bulk_do_not_contact_suppresses_and_blocks_enrolment(tmp_path):
    c = team_client(tmp_path)
    acme, gamma = ids(c, "Acme", "Gamma")
    r = c.post("/api/leads/bulk", json={"lead_ids": [acme, gamma], "action": "do_not_contact"}).json()
    assert r["ok"] == 2 and "ravi@acme.com" in store_of(c).read("suppressed")
    assert lead(c, "Acme")["do_not_contact"] is True
    again = c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "do_not_contact"}).json()
    assert again["results"][0]["reason"] == "already on the do-not-contact list"
    r = c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "enroll"}).json()
    assert r["ok"] == 0 and "suppression list" in r["results"][0]["reason"]


def test_bulk_enroll_uses_the_enrol_rules_and_explains_refusals(tmp_path):
    c = team_client(tmp_path)
    acme, beta, gamma = ids(c, "Acme", "Beta", "Gamma")
    r = c.post("/api/leads/bulk", json={"lead_ids": [acme, beta, gamma], "action": "enroll"}).json()
    assert r["ok"] == 2 and r["results"][2]["reason"] == "This lead has no valid email"
    assert lead(c, "Acme")["sequence"]["enrolled_at"] and "bulk, by This computer" in lead(c, "Acme")["timeline"][-1]["text"]
    again = c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "enroll"}).json()
    assert "already in the email sequence" in again["results"][0]["reason"]


def test_bulk_delete_is_owner_only_and_logged(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password=ADMIN), client=("203.0.113.9", 50000))
    owner = c.post("/api/login", json={"password": ADMIN}).json()["token"]
    h = {"Authorization": f"Bearer {owner}"}
    c.post("/api/users", json={"email": "cal@example.com", "name": "Cal", "role": "caller", "password": "long-password-1"}, headers=h)
    caller = {"Authorization": "Bearer " + c.post("/api/login", json={"email": "cal@example.com", "password": "long-password-1"}).json()["token"]}
    c.post("/api/leads/import", json={"csv": CSV3, "source": "apollo"}, headers=h)
    lid = next(l["id"] for l in c.get("/api/leads", headers=h).json() if l["company"] == "Beta")
    assert c.post("/api/leads/bulk", json={"lead_ids": [lid], "action": "delete"}, headers=caller).status_code == 403
    assert c.post(f"/api/leads/{lid}/owner", json={"owner": "Cal"}, headers=caller).json()["owner"] == "Cal"   # a login is an owner choice
    r = c.post("/api/leads/bulk", json={"lead_ids": [lid], "action": "delete"}, headers=h).json()
    assert r["ok"] == 1 and all(l["id"] != lid for l in c.get("/api/leads", headers=h).json())
    log = c.get("/api/change-log", headers=h).json()
    assert any(row["field"] == "deleted" and row["lead_id"] == lid and row["company"] == "Beta" for row in log)
    # caller cannot see or change scoring; owner can
    assert c.get("/api/scoring", headers=caller).status_code == 403
    assert c.get("/api/scoring", headers=h).status_code == 200


def test_bulk_skips_leads_in_other_workspaces(tmp_path):
    c = team_client(tmp_path)
    acme = ids(c, "Acme")[0]
    patch_lead(c, acme, workspace="client-b")
    store_of(c).update("settings", lambda s: {**s, "workspaces": [{"id": "client-b", "name": "Client B"}]})
    r = c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "tag_add", "value": "x"}, headers={"X-Workspace": "main"}).json()
    assert r["results"][0]["reason"] == "not found"


# ---------- saved views + list extras ----------
def test_saved_views_are_per_user_and_replace_by_name(tmp_path):
    c = team_client(tmp_path)
    v = c.post("/api/lead-views", json={"name": "My hot", "filters": {"band": "hot", "owner": "__me", "sources": ["apollo"], "junk": 1}}).json()
    assert v["filters"]["band"] == "hot" and "junk" not in v["filters"]
    c.post("/api/lead-views", json={"name": "my HOT", "filters": {"band": "warm"}})
    views = c.get("/api/lead-views").json()
    assert len(views) == 1 and views[0]["filters"]["band"] == "warm"
    assert set(store_of(c).read("settings")["lead_views"]) == {"local"}
    assert c.post("/api/lead-views", json={"name": "  ", "filters": {}}).status_code == 400
    assert c.delete(f"/api/lead-views/{views[0]['id']}").json() == {"ok": True}
    assert c.delete("/api/lead-views/nope").status_code == 404


def test_lead_list_has_last_activity_and_next_task_due(tmp_path):
    c = team_client(tmp_path)
    lid = lead(c, "Acme")["id"]
    soon, later = datetime.now(timezone.utc) + timedelta(hours=2), datetime.now(timezone.utc) + timedelta(days=2)
    add_task(store_of(c), lid, "call", "later", due_at=later)
    add_task(store_of(c), lid, "call", "soon", due_at=soon)
    got = lead(c, "Acme")
    assert got["next_task_due"] == soon.isoformat(timespec="seconds") and got["last_activity_at"]
    assert lead(c, "Beta")["next_task_due"] is None
