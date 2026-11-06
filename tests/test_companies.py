"""Companies (accounts): grouping by domain / name, free-mail safety, workspace scope, rollups, combined timeline."""
from app import companies
from tests.flow_helpers import make_client, patch_lead, store_of

CSV = ("company,contact_name,title,email,phone,website,open_roles,company_size,country\n"
       "Acme,Ravi Kumar,HR Head,ravi@acme.com,,https://acme.com,12,200,IN\n"
       "Acme Pvt Ltd,Neha Shah,Talent Lead,neha@acme.com,,,30,200,IN\n"
       "ACME,Old Name,Recruiter,,9876543210,,5,200,IN\n"
       "Beta Works,Sam Rao,Founder,sam@gmail.com,,,1,10,IN\n"
       "beta works,Anu Das,HR,anu@yahoo.com,,,2,10,IN\n"
       ",Lone One,,lone1@gmail.com,,,0,0,IN\n"
       ",Lone Two,,lone2@gmail.com,,,0,0,IN\n")


def by_name(c, **params):
    return {r["name"]: r for r in c.get("/api/companies", params={"limit": 200, **params}).json()["items"]}


def ids(c):
    return {l["contact_name"]: l["id"] for l in c.get("/api/leads").json()}


def test_groups_by_domain_then_name_and_never_by_free_mail(tmp_path):
    c = make_client(tmp_path, csv=CSV)
    rows = c.get("/api/companies", params={"limit": 200}).json()
    groups = {r["key"]: r for r in rows["items"]}
    assert groups["d:acme.com"]["contacts"] == 3            # 2 on acme.com + the name-only "ACME" row that matches its name
    assert groups["d:acme.com"]["open_roles"] == 30
    assert groups["n:beta works"]["contacts"] == 2          # gmail/yahoo: grouped by the company name, not the mail domain
    assert not any(k.startswith("d:gmail") or k.startswith("d:yahoo") for k in groups)
    lone = [r for r in rows["items"] if r["key"].startswith("p:")]
    assert len(lone) == 2 and all(r["contacts"] == 1 for r in lone)   # two strangers on gmail stay apart
    assert rows["total"] == 4


def test_group_keys_unit():
    leads = [{"id": "1", "company": "X", "email": "a@gmail.com"}, {"id": "2", "company": "", "email": "b@gmail.com"},
             {"id": "3", "company": "Y", "domain": "gmail.com", "email": "c@gmail.com"}, {"id": "4", "company": "Z", "website": "www.z.io/about"}]
    keys = companies.group_keys(leads)
    assert keys == {"1": "n:x", "2": "p:2", "3": "n:y", "4": "d:z.io"}


def test_search_sort_and_paging(tmp_path):
    c = make_client(tmp_path, csv=CSV)
    r = c.get("/api/companies", params={"q": "beta"}).json()
    assert r["total"] == 1 and r["items"][0]["name"].lower() == "beta works"
    r = c.get("/api/companies", params={"sort": "contacts", "limit": 1, "offset": 0}).json()
    assert r["total"] == 4 and r["items"][0]["key"] == "d:acme.com" and len(r["items"]) == 1
    r2 = c.get("/api/companies", params={"sort": "contacts", "limit": 1, "offset": 1}).json()
    assert r2["items"][0]["key"] == "n:beta works"
    assert c.get("/api/companies", params={"limit": 0}).status_code == 422


def test_rollup_stage_engaged_owner_linkedin_and_health(tmp_path):
    c = make_client(tmp_path, csv=CSV)
    i = ids(c)
    patch_lead(c, i["Ravi Kumar"], stage="contacted", owner="Asha")
    patch_lead(c, i["Neha Shah"], stage="interested", owner="Vik", linkedin_seq={"status": "messaging", "accepted_at": "2026-11-01T10:00:00+05:30"})
    patch_lead(c, i["Old Name"], stage="disqualified")
    store = store_of(c)
    store.update("settings", lambda s: {**s, "company_health": {"acme.com": {"score": 71, "band": "strong", "built_at": "2026-11-01T00:00:00+00:00"}}})
    acme = by_name(c)["Acme"]
    assert acme["best_stage"] == "interested" and acme["engaged"] is False
    assert acme["owners"] == ["Asha", "Vik"] and acme["linkedin_accepted"] == 1
    assert acme["health"]["score"] == 71
    store.update("inbox", lambda rows: [*rows, {"id": "m1", "lead_id": i["Ravi Kumar"], "received_at": "2026-11-04T09:00:00Z", "text": "yes please", "channel": "email"}])
    acme = by_name(c)["Acme"]
    assert acme["engaged"] is True and acme["replies"] == 1
    assert acme["last_touch"] == "2026-11-04T09:00:00+00:00"
    assert [r["key"] for r in c.get("/api/companies", params={"sort": "health"}).json()["items"]][0] == "d:acme.com"
    assert companies.best_stage(["not_now", "disqualified"]) == "not_now"


def test_detail_timeline_contacts_and_overlap_warning(tmp_path):
    c = make_client(tmp_path, csv=CSV)
    i = ids(c)
    store = store_of(c)
    store.update("outbox", lambda rows: [*rows,
        {"id": "o1", "lead_id": i["Ravi Kumar"], "sent_at": "2026-11-03T04:00:00+00:00", "subject": "Hi Ravi", "body": "x", "status": "sent", "step": "Day 0"},
        {"id": "o2", "lead_id": i["Neha Shah"], "sent_at": "2026-11-03T15:00:00+05:30", "subject": "Hi Neha", "body": "x", "status": "sent", "step": "Day 0"}])
    patch_lead(c, i["Ravi Kumar"], sequence={"enrolled_at": "2026-11-03T03:00:00+00:00", "sent_days": [0], "stopped": None})
    patch_lead(c, i["Neha Shah"], sequence={"enrolled_at": "2026-11-02T03:00:00+00:00", "sent_days": [0], "stopped": None})
    store.update("tasks", lambda rows: [*rows, {"id": "t1", "lead_id": i["Neha Shah"], "title": "Call Neha", "status": "open", "due_at": "2026-11-07T05:00:00+00:00"}])
    d = c.get("/api/companies/d:acme.com").json()
    assert d["contacts"] == 3 and len(d["contacts_list"]) == 3
    neha = next(x for x in d["contacts_list"] if x["contact_name"] == "Neha Shah")
    assert neha["next_task"]["title"] == "Call Neha" and neha["in_sequence"] is True
    emails = [t for t in d["timeline"] if t["type"] == "email"]
    assert [t["contact"]["name"] for t in emails] == ["Neha Shah", "Ravi Kumar"]     # 09:30 UTC after 04:00 UTC
    stamps = [companies.parse_when(t["at"]) for t in d["timeline"] if t.get("at")]
    assert stamps == sorted(stamps, reverse=True)
    assert len(d["warnings"]) == 1 and "2026-11-03" in d["warnings"][0]["text"]
    assert d["touches"] == 2
    patch_lead(c, i["Neha Shah"], sequence={"enrolled_at": "2026-11-02T03:00:00+00:00", "sent_days": [0], "stopped": "replied"})
    assert c.get("/api/companies/d:acme.com").json()["warnings"] == []
    assert c.get("/api/companies/n:beta works").json()["contacts"] == 2
    assert c.get("/api/companies/d:nope.com").status_code == 404


def test_of_lead_lists_the_other_people(tmp_path):
    c = make_client(tmp_path, csv=CSV)
    i = ids(c)
    r = c.get(f"/api/companies/of-lead/{i['Ravi Kumar']}").json()
    assert r["key"] == "d:acme.com" and {o["contact_name"] for o in r["others"]} == {"Neha Shah", "Old Name"}
    assert c.get(f"/api/companies/of-lead/{i['Lone One']}").json()["others"] == []
    assert c.get("/api/companies/of-lead/missing").status_code == 404


def test_workspace_scope(tmp_path):
    c = make_client(tmp_path, csv=CSV)
    w = c.post("/api/workspaces", json={"name": "Client One"}).json()
    r = c.post("/api/leads/import", json={"csv": "company,contact_name,email,website\nGamma,Client Person,cp@gamma.io,https://gamma.io\n"
                                                "Gamma,Second Person,sp@gamma.io,https://gamma.io\n", "source": "apollo"},
               headers={"X-Workspace": w["id"]})
    assert r.status_code == 200, r.text
    main = c.get("/api/companies", headers={"X-Workspace": "main"}).json()
    assert main["total"] == 4 and not any(x["key"] == "d:gamma.io" for x in main["items"])
    client = c.get("/api/companies", headers={"X-Workspace": w["id"]}).json()
    assert client["total"] == 1 and client["items"][0]["contacts"] == 2 and client["items"][0]["workspaces"] == [w["id"]]
    assert c.get("/api/companies/n:beta works", headers={"X-Workspace": w["id"]}).status_code == 404
    assert c.get("/api/companies/d:gamma.io", headers={"X-Workspace": "main"}).status_code == 404
    assert c.get("/api/companies").json()["total"] == 5
    ravi = ids(c)["Ravi Kumar"]
    assert c.get(f"/api/companies/of-lead/{ravi}", headers={"X-Workspace": w["id"]}).status_code == 404
