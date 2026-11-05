"""Lead lists: choose companies, start email / LinkedIn for that list only, keep a journey."""
from tests.flow_helpers import lead, make_client, patch_lead


def test_company_list_starts_email_only_for_its_members(tmp_path):
    c = make_client(tmp_path)
    a, b = lead(c, "Acme"), lead(c, "Beta")
    r = c.post("/api/lists", json={"name": "Pilot", "companies": ["acme"]})
    assert r.status_code == 200 and r.json()["lead_ids"] == [a["id"]] and r.json()["log"][0]["by"]
    out = c.post(f"/api/lists/{r.json()['id']}/start-email").json()
    assert out["started"] == 1 and out["asked"] == 1
    assert lead(c, "Acme")["sequence"] and not lead(c, "Beta").get("sequence")
    tl = [e for e in lead(c, "Acme")["timeline"] if e["kind"] == "sequence"]
    assert "(list Pilot)" in tl[-1]["text"]
    again = c.post(f"/api/lists/{r.json()['id']}/start-email").json()
    assert again["started"] == 0 and again["refused"][0]["count"] == 1
    detail = c.get(f"/api/lists/{r.json()['id']}").json()
    assert detail["stats"]["email_started"] == 1 and detail["members"][0]["journey"]["email_started"]
    assert [x["name"] for x in c.get("/api/lists").json()] == ["Pilot"]


def test_linkedin_needs_profile_and_reports_refusals(tmp_path):
    c = make_client(tmp_path)
    a, b = lead(c, "Acme"), lead(c, "Beta")
    patch_lead(c, a["id"], linkedin_url="https://www.linkedin.com/in/ravi")
    lid = c.post("/api/lists", json={"name": "LI", "lead_ids": [a["id"], b["id"]]}).json()["id"]
    out = c.post(f"/api/lists/{lid}/start-linkedin").json()
    assert out["started"] == 1 and out["refused"][0]["count"] == 1 and "LinkedIn profile" in out["refused"][0]["reason"]
    assert lead(c, "Acme")["linkedin_seq"]
    # subset start
    assert c.post(f"/api/lists/{lid}/start-email", json={"lead_ids": [b["id"]]}).json()["started"] == 1
    assert not lead(c, "Acme").get("sequence")


def test_enriched_summary_members_and_delete(tmp_path):
    c = make_client(tmp_path)
    a = lead(c, "Acme")
    assert c.get("/api/lists/enriched-summary").json()["enriched"] == 0
    assert c.post("/api/lists", json={"name": "x", "enriched": True}).status_code == 400
    patch_lead(c, a["id"], timeline=[*a["timeline"], {"at": "2026-11-01T00:00:00+00:00", "kind": "enrichment", "text": "Apollo: email"}])
    s = c.get("/api/lists/enriched-summary").json()
    assert s["enriched"] == 1 and s["email_ready"] == 1 and s["ready_ids"] == [a["id"]]
    lid = c.post("/api/lists", json={"name": "Enriched", "enriched": True}).json()["id"]
    b = lead(c, "Beta")
    assert len(c.post(f"/api/lists/{lid}/members", json={"add": [b["id"]]}).json()["lead_ids"]) == 2
    assert len(c.post(f"/api/lists/{lid}/members", json={"remove": [a["id"]]}).json()["lead_ids"]) == 1
    names = {x["company"] for x in c.get("/api/lists/companies").json()}
    assert names == {"Acme", "Beta"}
    assert c.delete(f"/api/lists/{lid}").status_code == 200 and c.get(f"/api/lists/{lid}").status_code == 404
