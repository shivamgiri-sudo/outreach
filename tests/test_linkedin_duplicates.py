"""One LinkedIn person is contacted once per LinkedIn ID: same profile under any spelling, from any lead / team member /
workspace. A different LinkedIn ID may still contact the person. A workspace never sees or is blocked by another workspace."""
import pytest
from fastapi.testclient import TestClient

from app import linkedin_dupes as dupes, linkedin_seq as li
from app.main import create_app

CSV = ("company,contact_name,title,email,linkedin_url\n"
       "Acme,Ravi Kumar,CHRO,r@acme.example,https://www.linkedin.com/in/ravi-kumar\n"
       "Acme Two,Ravi K,HR Head,r2@acme2.example,https://in.linkedin.com/in/Ravi-Kumar/?utm_source=x\n"
       "Other,Meera Rao,Founder,m@o.example,https://www.linkedin.com/in/meera-rao\n")


def test_profile_key_canonical():
    same = ["https://www.linkedin.com/in/Ravi-Kumar", "linkedin.com/in/ravi-kumar/", "http://in.linkedin.com/in/ravi-kumar?utm=1#x",
            "https://de.linkedin.com/in/ravi-kumar/de", "https://www.linkedin.com/in/ravi%2Dkumar/"]
    assert {dupes.profile_key(u) for u in same} == {"/in/ravi-kumar"}
    assert dupes.profile_key("https://www.linkedin.com/in/j%C3%B6rg") == dupes.profile_key("https://www.linkedin.com/in/JÖRG/") == "/in/jörg"
    for bad in ("", None, "https://example.com/in/x", "https://www.linkedin.com/company/acme", "https://www.linkedin.com/in/"):
        assert dupes.profile_key(bad) == ""


@pytest.fixture
def rig(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV})
    store = c.app.state.store
    ids = {l["company"]: l["id"] for l in store.read("leads")}
    store.update("settings", lambda s: {**s, "workspaces": [{"id": "w1", "name": "W1", "account_id": "acctx"},
                                                            {"id": "w2", "name": "W2", "account_id": "acctx"},
                                                            {"id": "w3", "name": "W3", "account_id": "accty"},
                                                            {"id": "w4", "name": "W4"}]})
    tok = c.post("/api/extension/pair", json={"label": "agent"}).json()["token"]
    return {"c": c, "store": store, "a": ids["Acme"], "b": ids["Acme Two"], "m": ids["Other"],
            "h": {"Authorization": f"Bearer {tok}", "X-LinkedIn-Account": "li-b"}}


def put(store, lid, ws, **extra):
    store.update_lead(lid, lambda l: {**l, "workspace": ws, **extra})


def same_id(store, *ids, account="li-b"):
    """Both leads are sent from one LinkedIn ID (the "Send from" pick)."""
    for i in ids:
        store.update_lead(i, lambda l: {**l, "linkedin_account": account})


def lead(store, lid):
    return next(l for l in store.read("leads") if l["id"] == lid)


def test_second_lead_refused_everywhere_in_one_account(rig):
    c, store, a, b, h = rig["c"], rig["store"], rig["a"], rig["b"], rig["h"]
    put(store, a, "main"); put(store, b, "main")                    # two workspaces, one account
    same_id(store, a, b)
    li.start(store, a)
    li.mark_sent(store, a, 1)
    with pytest.raises(ValueError, match="Already contacted on LinkedIn by .* on \\d{4}-\\d\\d-\\d\\d from "):
        li.start(store, b)
    second = lead(store, b)
    assert second["linkedin_dup_of"] == a and not second.get("linkedin_seq")
    assert "Already contacted" in second["timeline"][-1]["text"]
    # route + list start + bulk give the reason
    r = c.post(f"/api/leads/{b}/linkedin/start")
    assert r.status_code == 400 and "Already contacted on LinkedIn" in r.text
    row = c.post("/api/lists", json={"name": "L", "lead_ids": [b]}).json()
    out = c.post(f"/api/lists/{row['id']}/start-linkedin", json={}).json()
    assert out["started"] == 0 and "Already contacted on LinkedIn" in out["refused"][0]["reason"]
    # the extension: never offered, match says no, claim and sent refused
    q = c.get("/api/extension/queue", headers=h).json()
    assert b not in [d["lead_id"] for d in q["due"]]
    m = c.get("/api/extension/match", params={"profile_url": "https://www.linkedin.com/in/ravi-kumar"}, headers=h).json()
    assert m["lead_id"] in (a, b)
    info = c.get(f"/api/extension/match", params={"name": "Ravi K", "company": "Acme Two"}, headers=h).json()
    assert info["lead_id"] == b and not info["can_send"] and "Already contacted" in info["reason"]
    assert c.post(f"/api/extension/leads/{b}/claim", headers=h).status_code == 409
    assert c.post(f"/api/extension/leads/{b}/sent", json={"step": 1}, headers=h).status_code == 409
    # the original goes on: its own follow-ups are not duplicates
    assert dupes.duplicate_of(store, lead(store, a)) is None
    li.mark_accepted(store, a)
    assert "Connection accepted (on the original lead" in lead(store, b)["timeline"][-1]["text"]


def test_queue_hides_duplicates_and_counts_them(rig):
    store, a, b = rig["store"], rig["a"], rig["b"]
    put(store, a, "w1"); put(store, b, "w1")
    same_id(store, a, b)
    li.start(store, a)
    li.mark_sent(store, a, 1)
    q = li.queue(store)
    assert b not in [s["lead_id"] for s in q["suggestions"]] and q["skipped_duplicates"] >= 1
    assert a not in [s["lead_id"] for s in q["suggestions"]]


def test_held_by_a_linkedin_account_blocks_the_sibling_before_any_send(rig):
    c, store, a, b, h = rig["c"], rig["store"], rig["a"], rig["b"], rig["h"]
    put(store, a, "main"); put(store, b, "main")
    same_id(store, a, b, account="li-a")
    li.start(store, a)
    store.update_lead(a, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "account": "li-a"}})
    with pytest.raises(ValueError, match="Already being handled on LinkedIn"):
        li.start(store, b)
    # two started but unsent leads: a claim by one account blocks the other account's claim
    store.update_lead(a, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "account": ""}})
    store.update_lead(b, lambda l: {k: v for k, v in l.items() if k != "linkedin_dup_of"} | {"linkedin_seq": {"started_at": "2026-01-01T00:00:00+00:00", "done": [], "status": "waiting_to_connect"}})
    assert c.post(f"/api/extension/leads/{a}/claim", headers={**h, "X-LinkedIn-Account": "li-a"}).status_code == 200
    store.update_lead(b, lambda l: {k: v for k, v in l.items() if k != "linkedin_account"})
    assert c.post(f"/api/extension/leads/{b}/claim", headers={**h, "X-LinkedIn-Account": "li-a"}).status_code == 409   # same ID: refused
    assert c.post(f"/api/extension/leads/{b}/claim", headers=h).status_code == 200                                   # li-b: a different ID may


def test_daily_auto_start_skips_duplicates(rig):
    from app import linkedin_accounts as accts
    store, a, b = rig["store"], rig["a"], rig["b"]
    put(store, a, "w1"); put(store, b, "w1")
    same_id(store, a, b, account="li-b")
    store.update("settings", lambda s: {**s, "linkedin_auto_start": True})
    li.start(store, a)
    li.mark_sent(store, a, 1)
    started = {s["lead_id"] for s in li.auto_start(store, 10)}
    assert b not in started
    started2 = {s["lead_id"] for s in accts.auto_start_for(store, "li-b", 10)}
    assert b not in started2


def test_two_unsent_leads_cannot_both_start_in_one_batch(rig):
    store, a, b = rig["store"], rig["a"], rig["b"]
    same_id(store, a, b)
    li.start(store, a)
    with pytest.raises(ValueError):
        li.start(store, b)


def test_different_workspaces_never_block_or_see_each_other(rig):
    store, a, b = rig["store"], rig["a"], rig["b"]
    put(store, a, "w1"); put(store, b, "w3")                      # acctx vs accty
    li.start(store, a)
    li.mark_sent(store, a, 1)
    assert dupes.duplicate_of(store, lead(store, b)) is None
    assert li.start(store, b)["lead"]["linkedin_seq"]["status"] == "waiting_to_connect"
    assert "linkedin_dup_of" not in lead(store, b)


def test_a_different_linkedin_id_may_contact_the_same_person(rig):
    store, a, b = rig["store"], rig["a"], rig["b"]
    put(store, a, "w1"); put(store, b, "w1")
    same_id(store, a, account="li-a"); same_id(store, b, account="li-b")
    li.start(store, a)
    li.mark_sent(store, a, 1)
    assert dupes.duplicate_of(store, lead(store, b)) is None
    assert li.start(store, b)["lead"]["linkedin_seq"]["status"] == "waiting_to_connect"
    # the same ID is still refused
    same_id(store, b, account="li-a")
    assert dupes.duplicate_of(store, lead(store, b), "li-a") is not None


def test_contacts_panel_lists_the_workspace_team_only(rig):
    c, store, a, b, m = rig["c"], rig["store"], rig["a"], rig["b"], rig["m"]
    put(store, a, "w1"); put(store, b, "w1")
    other = [l for l in store.read("leads") if l["id"] == m][0]
    store.update_lead(m, lambda l: {**l, "workspace": "w3", "linkedin_url": "https://www.linkedin.com/in/ravi-kumar"})   # same person, other workspace
    same_id(store, a, account="li-a"); same_id(store, b, account="li-b")
    li.start(store, a)
    store.update_lead(a, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "enrolled_by": "Priya"}})
    li.mark_sent(store, a, 1)
    rows = c.get(f"/api/leads/{b}/linkedin/contacts").json()["contacts"]
    assert {r["lead_id"] for r in rows} == {a, b} or {r["lead_id"] for r in rows} == {a}
    first = next(r for r in rows if r["lead_id"] == a)
    assert first["user"] == "Priya" and first["linkedin_account"] == "li-a" and "waiting" in first["status"] and first["workspace"] == "W1"
    assert m not in {r["lead_id"] for r in rows}                         # the other workspace's lead never shows
    assert [r["lead_id"] for r in c.get(f"/api/leads/{m}/linkedin/contacts").json()["contacts"]] == [m]
