"""Preview of a campaign whose audience includes hand-picked leads: the preview lists every picked lead and counts the
same ones the real run attaches, leaving out only blocked leads (with the reason)."""
from tests.campaign_fakes import FakeIO, by_company, client, create, run

CSV = ("company,contact_name,email,website\n"
       "Alpha Works,Anu,anu@alpha-works.in,https://alpha-works.in\n"
       "Beta Labs,Bala,bala@beta-labs.in,https://beta-labs.in\n"
       "Gamma Tech,Gita,gita@gamma-tech.in,https://gamma-tech.in\n"
       "Delta Co,Dev,dev@delta-co.in,https://delta-co.in\n"
       "Eps Co,Esha,esha@eps-co.in,https://eps-co.in\n")


def _setup(tmp_path):
    c = client(tmp_path, FakeIO())
    assert c.post("/api/leads/import", json={"csv": CSV, "source": "manual"}).status_code == 200
    ids = {name: by_company(c, name)["id"] for name in ("Alpha Works", "Beta Labs", "Gamma Tech", "Delta Co", "Eps Co")}
    store = c.app.state.store
    store.update("leads", lambda rows: [{**l, "do_not_contact": True} if l["company"] == "Gamma Tech"
                                        else {**l, "workspace": "ws-other"} if l["company"] == "Eps Co" else l for l in rows])
    store.update("suppressed", lambda rows: [*rows, "dev@delta-co.in"])
    return c, ids


def test_preview_lists_every_picked_lead_and_matches_the_real_run(tmp_path):
    c, ids = _setup(tmp_path)
    picked = [ids["Alpha Works"], ids["Beta Labs"], ids["Gamma Tech"], ids["Delta Co"]]
    camp = create(c, sources={"csv_import_ids": picked})
    preview = run(c, camp["id"], dry_run=True)
    rows = {r["company"]: r for r in preview["preview"]}
    assert set(rows) == {"Alpha Works", "Beta Labs", "Gamma Tech", "Delta Co"}           # none silently dropped
    assert rows["Alpha Works"]["would"] == rows["Beta Labs"]["would"] == "attach"
    assert rows["Alpha Works"]["score"] is not None
    assert rows["Gamma Tech"]["would"] == "attach_blocked" and rows["Gamma Tech"]["reasons"] == ["on the do-not-contact list"]
    assert rows["Delta Co"]["would"] == "attach_blocked" and "suppressed" in rows["Delta Co"]["reasons"][0]
    assert preview["attached"] == 2
    assert any("2 picked lead(s) were left out" in e["message"] for e in preview["errors"])
    real = run(c, camp["id"])
    assert real["attached"] == preview["attached"]                                      # same check, same count
    assert by_company(c, "Gamma Tech").get("campaign_id") is None                        # do-not-contact never attached
    assert by_company(c, "Alpha Works")["campaign_id"] == camp["id"]


def test_picked_lead_in_another_workspace_is_left_out_with_the_reason(tmp_path):
    c, ids = _setup(tmp_path)
    camp = create(c, sources={"csv_import_ids": [ids["Alpha Works"], ids["Eps Co"]]})
    r = c.post(f"/api/campaigns/{camp['id']}/run", json={"dry_run": True, "now": "2026-11-03T05:00:00+00:00"},
               headers={"X-Workspace": "main"})
    preview = r.json()
    rows = {row["company"]: row for row in preview["preview"]}
    assert rows["Eps Co"]["would"] == "attach_blocked" and rows["Eps Co"]["reasons"] == ["in another workspace"]
    assert preview["attached"] == 1
