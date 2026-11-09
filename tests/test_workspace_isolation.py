"""A login limited to one workspace sees and changes only that workspace: deny-by-default routes, scoped inbox /
outbox / replies / meetings, imports that never merge into or reveal another workspace's leads, and settings that
leave out other clients' names."""
from fastapi.testclient import TestClient

from app.main import create_app
from app.workspace_guard import LIMITED_OK

MAIN_CSV = ("company,contact_name,title,email,phone,website\n"
            "Acme,Ravi Kumar,HR Head,ravi@acme.com,98765 43210,https://acme.com\n"
            "Beta Ltd,Sita Rao,HR,sita@beta.in,91234 56789,https://beta.in\n")


def setup(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    owner = c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    ho = {"Authorization": f"Bearer {owner}"}
    c.post("/api/leads/import", json={"csv": MAIN_CSV, "source": "apollo"}, headers=ho)
    w = c.post("/api/workspaces", json={"name": "Client One"}, headers=ho).json()
    r = c.post("/api/users", json={"email": "raj@example.com", "name": "Raj", "role": "caller", "password": "long-password-1",
                                   "workspaces": [w["id"]]}, headers=ho)
    assert r.status_code == 200 and r.json()["workspaces"] == [w["id"]]
    raj = c.post("/api/login", json={"email": "raj@example.com", "password": "long-password-1"}).json()["token"]
    return c, ho, {"Authorization": f"Bearer {raj}"}, w


def test_new_login_can_be_limited_at_creation_and_unknown_workspace_refused(tmp_path):
    c, ho, hr, w = setup(tmp_path)
    assert c.get("/api/leads", headers=hr).json() == []
    bad = c.post("/api/users", json={"email": "x@example.com", "role": "caller", "password": "long-password-1", "workspaces": ["nope"]}, headers=ho)
    assert bad.status_code == 400


def test_routes_outside_the_reviewed_list_are_refused(tmp_path):
    c, ho, hr, w = setup(tmp_path)
    for path in ("/api/campaigns", "/api/hygiene/report", "/api/signals", "/api/prospects/stats",
                 "/api/find-leads", "/api/growth", "/api/extension/agent", "/api/linkedin/settings", "/api/flows/{x}/webhook".replace("{x}", "f1")):
        assert c.get(path, headers=hr).status_code == 403, path
    assert c.put("/api/linkedin/settings", json={}, headers=hr).status_code == 403
    assert c.post("/api/linkedin/results", json={"csv": "a"}, headers=hr).status_code == 403
    # the LinkedIn queue and the flows list are open to workspace logins, scoped to their workspace (test_workspace_templates.py)
    assert c.get("/api/linkedin/queue", headers=hr).json()["suggestions"] == []
    assert c.get("/api/campaigns", headers=ho).status_code == 200                 # the owner is not limited


def test_inbox_outbox_replies_meetings_hide_other_workspaces(tmp_path):
    c, ho, hr, w = setup(tmp_path)
    acme = next(l["id"] for l in c.get("/api/leads", headers=ho).json() if l["company"] == "Acme")
    c.post(f"/api/leads/{acme}/reply", json={"text": "Yes, interested. Call me."}, headers=ho)
    assert c.get("/api/reply-inbox", headers=ho).json()["replies"]
    assert c.get("/api/reply-inbox", headers=hr).json()["replies"] == []
    assert c.get("/api/inbox", headers=hr).json() == []
    assert c.get("/api/outbox", headers=hr).json() == []
    assert c.get("/api/meetings", headers=hr).json() == []
    key = f"{acme}|x"
    assert c.post("/api/reply-inbox/action", json={"key": key, "action": "read"}, headers=hr).status_code == 404
    assert c.get(f"/api/leads/{acme}", headers=hr).status_code == 404
    assert c.post("/api/tasks", json={"lead_id": acme, "title": "x"}, headers=hr).status_code == 404
    bulk = c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "tag_add", "value": "x"}, headers=hr).json()
    assert bulk["results"][0]["reason"] == "not found"


def test_quick_paste_skips_contacts_held_elsewhere_without_merging(tmp_path):
    c, ho, hr, w = setup(tmp_path)
    csv = "company,contact_name,email,phone\nAcme,Ravi Kumar,ravi@acme.com,98765 43210\nNewCo,Om Das,om@newco.in,99887 76655\n"
    r = c.post("/api/leads/import", json={"csv": csv, "source": "upload"}, headers=hr).json()
    assert r["created"] == 1 and r.get("elsewhere") == 1 and r["merged"] == 0
    assert [l["company"] for l in c.get("/api/leads", headers=hr).json()] == ["NewCo"]
    acme = next(l for l in c.get("/api/leads", headers=ho).json() if l["company"] == "Acme")
    assert (acme.get("workspace") or "main") == "main"


def test_smart_import_lands_in_the_workspace_and_hides_other_leads(tmp_path):
    c, ho, hr, w = setup(tmp_path)
    csv = ("company,contact_name,email,phone,website\n"
           "Acme,Ravi Kumar,ravi@acme.com,98765 43210,https://acme.com\n"
           "Acme,New Person,new@acme.com,90000 11122,https://acme.com\n"
           "NewCo,Om Das,om@newco.in,99887 76655,https://newco.in\n")
    pre = c.post("/api/import/preview", json={"csv": csv, "source": "upload"}, headers=hr).json()
    text = str(pre)
    assert "Sita" not in text and all(m.get("lead_id") is None or False for m in pre.get("merges", []))
    done = c.post("/api/import/commit", json={"csv": csv, "source": "upload"}, headers=hr).json()
    assert done["summary"]["create"] == 2 and done["summary"]["skipped"] == 1 and done["summary"]["merge"] == 0
    assert any(s["reason"].startswith("Already held in another workspace") for s in done["skipped"])
    mine = c.get("/api/leads", headers=hr).json()
    assert sorted(l["company"] for l in mine) == ["Acme", "NewCo"]
    owner_acme = [l for l in c.get("/api/leads", headers=ho).json() if l["company"] == "Acme"]
    assert len(owner_acme) == 2 and not any(o.get("contact_name") == "New Person" and (o.get("workspace") or "main") == "main" for o in owner_acme)
    hist = c.get("/api/import/history", headers=hr).json()
    assert len(hist) == 1 and c.get("/api/import/history", headers=ho).json()


def test_settings_leave_out_other_clients(tmp_path):
    c, ho, hr, w = setup(tmp_path)
    assert "workspaces" in c.get("/api/settings", headers=ho).json()
    assert "workspaces" not in c.get("/api/settings", headers=hr).json()


def test_allow_list_only_names_real_routes(tmp_path):
    app = create_app(data_file=tmp_path / "r.json")
    def walk(routes):
        for x in routes:
            if type(x).__name__ == "_IncludedRouter":
                yield from walk(x.original_router.routes)
            else:
                yield x
    real = {(m, r.path) for r in walk(app.routes) for m in (getattr(r, "methods", None) or [])}
    assert LIMITED_OK - real == set()


def admin_setup(tmp_path):
    c, ho, hr, w = setup(tmp_path)
    w2 = c.post("/api/workspaces", json={"name": "Client Two"}, headers=ho).json()
    assert c.post("/api/users", json={"email": "boss@client.example", "role": "admin", "password": "long-password-1"}, headers=ho).status_code == 400
    c.post("/api/users", json={"email": "boss@client.example", "name": "Boss", "role": "admin", "password": "long-password-1",
                               "workspaces": [w["id"]]}, headers=ho)
    tok = c.post("/api/login", json={"email": "boss@client.example", "password": "long-password-1"}).json()["token"]
    return c, ho, {"Authorization": f"Bearer {tok}"}, w, w2


def test_workspace_admin_manages_only_its_own_workspace_logins(tmp_path):
    c, ho, ha, w, w2 = admin_setup(tmp_path)
    c.post("/api/users", json={"email": "other@two.example", "role": "caller", "password": "long-password-1", "workspaces": [w2["id"]]}, headers=ho)
    seen = {u["email"] for u in c.get("/api/users", headers=ha).json()}
    assert seen == {"raj@example.com", "boss@client.example"}                      # not the owner, not Client Two's login
    new = c.post("/api/users", json={"email": "new@one.example", "role": "caller", "password": "long-password-1"}, headers=ha)
    assert new.status_code == 200 and new.json()["workspaces"] == [w["id"]]        # defaults to the admin's own workspace
    assert c.post("/api/users", json={"email": "x@one.example", "role": "caller", "password": "long-password-1",
                                      "workspaces": [w2["id"]]}, headers=ha).status_code == 403
    assert c.post("/api/users", json={"email": "y@one.example", "role": "owner", "password": "long-password-1"}, headers=ha).status_code == 403
    assert c.put("/api/users/other@two.example", json={"role": "viewer"}, headers=ha).status_code == 404
    assert c.delete("/api/users/other@two.example", headers=ha).status_code == 404
    assert c.put("/api/users/new@one.example", json={"workspaces": [w["id"], w2["id"]]}, headers=ha).status_code == 403
    assert c.put("/api/users/new@one.example", json={"role": "viewer"}, headers=ha).json()["role"] == "viewer"
    assert c.delete("/api/users/new@one.example", headers=ha).status_code == 200
    assert c.post("/api/workspaces", json={"name": "Mine"}, headers=ha).status_code == 403
    assert c.get("/api/campaigns", headers=ha).status_code == 403
    assert c.get("/api/leads", headers=ha).json() == []                           # Client One has no leads; main's stay hidden
    assert [x["id"] for x in c.get("/api/workspaces", headers=ha).json()] == [w["id"]]


def test_admin_without_workspace_sees_nothing(tmp_path):
    from app.workspaces import allowed_of
    assert allowed_of({"role": "admin", "workspaces": []}) == set()
    assert allowed_of({"role": "owner"}) is None
