"""Setup > Workspaces: rename, delete (move the leads or, owners only, delete them), typed confirmation, Main is kept,
members, workspace admin limits, audit rows, and logins left with no workspace see nothing."""
from fastapi.testclient import TestClient

from app import workspaces
from app.main import create_app

MAIN_CSV = ("company,contact_name,title,email,phone,website\n"
            "Acme,Ravi Kumar,HR Head,ravi@acme.com,98765 43210,https://acme.com\n")
CLIENT_CSV = ("company,contact_name,title,email,phone,website\n"
              "ClientCo,Neha Jain,CHRO,neha@clientco.test,98100 11111,https://clientco.test\n"
              "OtherCo,Arun Gowda,HR Lead,arun@otherco.test,99001 12233,https://otherco.test\n")
PW = "long-password-1"


def setup(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    owner = c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    ho = {"Authorization": f"Bearer {owner}"}
    c.post("/api/leads/import", json={"csv": MAIN_CSV, "source": "apollo"}, headers=ho)
    w = c.post("/api/workspaces", json={"name": "Client One"}, headers=ho).json()
    c.post("/api/leads/import", json={"csv": CLIENT_CSV, "source": "apollo"}, headers={**ho, "X-Workspace": w["id"]})
    return c, ho, w


def add(c, ho, email, role, spaces=None, **extra):
    body = {"email": email, "name": email.split("@")[0].title(), "role": role, "password": PW, **extra}
    if spaces is not None:
        body["workspaces"] = spaces
    return c.post("/api/users", json=body, headers=ho)


def login(c, email):
    return {"Authorization": f"Bearer {c.post('/api/login', json={'email': email, 'password': PW}).json()['token']}"}


def companies(c, h, ws=""):
    return sorted(l["company"] for l in c.get("/api/leads", headers={**h, **({"X-Workspace": ws} if ws else {})}).json())


def delete(c, h, ws_id, **body):
    return c.request("DELETE", f"/api/workspaces/{ws_id}", json=body, headers=h)


def audit_rows(c, ho, action):
    return [r for r in c.get("/api/audit", params={"action": action}, headers=ho).json()["items"] if r["action"] == action][::-1]


def test_rename_checks_names_and_is_audited(tmp_path):
    c, ho, w = setup(tmp_path)
    r = c.put(f"/api/workspaces/{w['id']}", json={"name": "  Client   Uno "}, headers=ho)
    assert r.status_code == 200 and r.json()["name"] == "Client Uno"
    assert {x["id"]: x["name"] for x in c.get("/api/workspaces", headers=ho).json()}[w["id"]] == "Client Uno"
    assert c.put(f"/api/workspaces/{w['id']}", json={"name": "main"}, headers=ho).status_code == 400       # name taken
    assert c.put("/api/workspaces/main", json={"name": "Head office"}, headers=ho).status_code == 400
    assert c.put("/api/workspaces/nope", json={"name": "X"}, headers=ho).status_code == 404
    rows = audit_rows(c, ho, "workspace.rename")
    assert rows and rows[-1]["target"] == "Client Uno" and "was Client One" in rows[-1]["detail"]


def test_overview_counts_leads_and_members(tmp_path):
    c, ho, w = setup(tmp_path)
    add(c, ho, "raj@example.com", "caller", [w["id"]])
    rows = {x["id"]: x for x in c.get("/api/workspaces/overview", headers=ho).json()}
    assert rows[w["id"]]["leads"] == 2 and rows[w["id"]]["members"] == 1 and rows["main"]["leads"] == 1
    assert rows[w["id"]]["created_by"] and rows[w["id"]]["created_at"]


def test_delete_needs_typed_name_and_keeps_main(tmp_path):
    c, ho, w = setup(tmp_path)
    assert delete(c, ho, "main", confirm_name="Main", move_to=w["id"]).status_code == 400
    r = delete(c, ho, w["id"], confirm_name="Client 1", move_to="main")
    assert r.status_code == 400 and "Type the workspace name" in r.json()["detail"]
    r = delete(c, ho, w["id"], confirm_name="Client One")                       # leads still inside, no choice made
    assert r.status_code == 400 and "2 leads" in r.json()["detail"]
    assert delete(c, ho, w["id"], confirm_name="Client One", move_to=w["id"]).status_code == 400
    assert delete(c, ho, w["id"], confirm_name="Client One", move_to="main", delete_leads=True).status_code == 400
    assert w["id"] in {x["id"] for x in c.get("/api/workspaces", headers=ho).json()}


def test_delete_moving_the_leads(tmp_path):
    c, ho, w = setup(tmp_path)
    other = c.post("/api/workspaces", json={"name": "Client Two"}, headers=ho).json()
    c.post("/api/lists", json={"name": "Client list", "companies": ["ClientCo"]}, headers={**ho, "X-Workspace": w["id"]})
    r = delete(c, ho, w["id"], confirm_name="client one", move_to=other["id"])
    assert r.status_code == 200 and r.json()["moved"] == 2 and r.json()["deleted"] == 0
    assert w["id"] not in {x["id"] for x in c.get("/api/workspaces", headers=ho).json()}
    assert companies(c, ho, other["id"]) == ["ClientCo", "OtherCo"]
    assert [x["name"] for x in c.get("/api/lists", headers={**ho, "X-Workspace": other["id"]}).json()] == ["Client list"]
    lead = next(l for l in c.get("/api/leads", headers=ho).json() if l["company"] == "ClientCo")
    assert any("workspace Client One was deleted" in e["text"] for e in c.get(f"/api/leads/{lead['id']}", headers=ho).json()["timeline"])
    row = audit_rows(c, ho, "workspace.delete")[-1]
    assert row["target"] == "Client One" and "2 leads moved to Client Two" in row["detail"]


def test_delete_moving_into_main_clears_the_tag(tmp_path):
    c, ho, w = setup(tmp_path)
    assert delete(c, ho, w["id"], confirm_name="Client One", move_to="main").status_code == 200
    assert companies(c, ho, "main") == ["Acme", "ClientCo", "OtherCo"]


def test_delete_with_its_leads_is_owner_only_and_audited(tmp_path):
    c, ho, w = setup(tmp_path)
    add(c, ho, "boss@example.com", "admin", [w["id"]])
    hb = login(c, "boss@example.com")
    assert delete(c, hb, w["id"], confirm_name="Client One", delete_leads=True).status_code == 403
    assert delete(c, hb, w["id"], confirm_name="Client One", move_to="main").status_code == 403       # delete is owner-only
    r = delete(c, ho, w["id"], confirm_name="Client One", delete_leads=True)
    assert r.status_code == 200 and r.json()["deleted"] == 2
    assert companies(c, ho) == ["Acme"]
    row = audit_rows(c, ho, "workspace.delete")[-1]
    assert "2 leads deleted permanently" in row["detail"]


def test_empty_workspace_deletes_with_just_the_name(tmp_path):
    c, ho, _ = setup(tmp_path)
    e = c.post("/api/workspaces", json={"name": "Empty"}, headers=ho).json()
    assert delete(c, ho, e["id"], confirm_name="Empty").status_code == 200


def test_members_add_and_remove(tmp_path):
    c, ho, w = setup(tmp_path)
    two = c.post("/api/workspaces", json={"name": "Client Two"}, headers=ho).json()
    add(c, ho, "raj@example.com", "caller", [w["id"]])
    add(c, ho, "all@example.com", "caller", all_workspaces=True)
    members = {m["email"]: m["access"] for m in c.get(f"/api/workspaces/{two['id']}/members", headers=ho).json()}
    assert "raj@example.com" not in members and members["all@example.com"] == "all"
    r = c.post(f"/api/workspaces/{two['id']}/members", json={"email": "raj@example.com"}, headers=ho)
    assert r.status_code == 200 and r.json()["workspaces"] == [w["id"], two["id"]]
    assert c.post(f"/api/workspaces/{two['id']}/members", json={"email": "raj@example.com"}, headers=ho).status_code == 400
    assert c.post(f"/api/workspaces/{two['id']}/members", json={"email": "all@example.com"}, headers=ho).status_code == 400
    assert c.post(f"/api/workspaces/{two['id']}/members", json={"email": "ghost@example.com"}, headers=ho).status_code == 404
    assert c.request("DELETE", f"/api/workspaces/{w['id']}/members/raj@example.com", headers=ho).status_code == 200
    me = c.get("/api/me", headers=login(c, "raj@example.com")).json()
    assert me["workspaces"] == [two["id"]] and me["no_workspace"] is False
    assert audit_rows(c, ho, "workspace.member_add")[-1]["target"] == "raj@example.com"
    assert audit_rows(c, ho, "workspace.member_remove")[-1]["target"] == "raj@example.com"


def test_workspace_admin_limits(tmp_path):
    c, ho, w = setup(tmp_path)
    two = c.post("/api/workspaces", json={"name": "Client Two"}, headers=ho).json()
    add(c, ho, "boss@example.com", "admin", [w["id"]])
    add(c, ho, "raj@example.com", "caller", [w["id"]])
    add(c, ho, "eve@example.com", "caller", [two["id"]])
    hb = login(c, "boss@example.com")
    assert [x["id"] for x in c.get("/api/workspaces/overview", headers=hb).json()] == [w["id"]]
    assert c.put(f"/api/workspaces/{w['id']}", json={"name": "Client Prime"}, headers=hb).status_code == 200
    assert c.put(f"/api/workspaces/{two['id']}", json={"name": "Mine now"}, headers=hb).status_code == 403
    assert c.post("/api/workspaces", json={"name": "New"}, headers=hb).status_code == 403
    assert c.get(f"/api/workspaces/{two['id']}/members", headers=hb).status_code == 403
    emails = {m["email"] for m in c.get(f"/api/workspaces/{w['id']}/members", headers=hb).json()}
    assert emails == {"boss@example.com", "raj@example.com"}
    assert c.post(f"/api/workspaces/{w['id']}/members", json={"email": "eve@example.com"}, headers=hb).status_code == 404   # not theirs
    assert c.request("DELETE", f"/api/workspaces/{w['id']}/members/boss@example.com", headers=hb).status_code == 400   # not themselves
    r = c.request("DELETE", f"/api/workspaces/{w['id']}/members/raj@example.com", headers=hb)
    assert r.status_code == 200 and r.json()["no_workspace"] is True
    # a caller cannot manage workspaces at all
    he = login(c, "eve@example.com")
    assert c.put(f"/api/workspaces/{two['id']}", json={"name": "X"}, headers=he).status_code == 403
    assert c.get("/api/workspaces/overview", headers=he).status_code == 403


def test_logins_left_without_a_workspace_see_nothing(tmp_path):
    c, ho, w = setup(tmp_path)
    add(c, ho, "raj@example.com", "caller", [w["id"]])
    add(c, ho, "boss@example.com", "admin", [w["id"]])
    hr = login(c, "raj@example.com")
    assert companies(c, hr) == ["ClientCo", "OtherCo"]
    r = delete(c, ho, w["id"], confirm_name="Client One", move_to="main")
    assert sorted(r.json()["left_without_workspace"]) == ["boss@example.com", "raj@example.com"]
    users = {u["email"]: u for u in c.get("/api/users", headers=ho).json()}
    assert users["raj@example.com"]["no_workspace"] is True and users["raj@example.com"]["workspaces"] == []
    for who in ("raj@example.com", "boss@example.com"):
        h = login(c, who)
        assert c.get("/api/me", headers=h).json()["no_workspace"] is True
        assert companies(c, h) == [] and companies(c, h, "main") == []
        assert c.get("/api/workspaces", headers=h).json() == []
        assert c.post("/api/leads/import", json={"csv": MAIN_CSV, "source": "apollo"}, headers=h).status_code == 403
        assert c.get("/api/campaigns", headers=h).status_code == 403
    assert workspaces.allowed_of({"role": "caller", "workspaces": [], "no_workspace": True}) == set()
    assert workspaces.allowed_of({"role": "owner", "no_workspace": True}) is None
    # an owner giving a workspace again ends "no workspace"
    assert c.put("/api/users/raj@example.com", json={"workspaces": ["main"]}, headers=ho).status_code == 200
    assert companies(c, login(c, "raj@example.com")) == ["Acme", "ClientCo", "OtherCo"]


def test_new_login_needs_an_explicit_workspace_choice(tmp_path):
    c, ho, w = setup(tmp_path)
    r = add(c, ho, "x@example.com", "caller")
    assert r.status_code == 400 and "All workspaces" in r.json()["detail"]
    assert add(c, ho, "y@example.com", "caller", all_workspaces=True).status_code == 200
    assert add(c, ho, "z@example.com", "viewer", [w["id"]]).status_code == 200
    add(c, ho, "boss@example.com", "admin", [w["id"]])
    hb = login(c, "boss@example.com")
    assert add(c, hb, "q@example.com", "caller", all_workspaces=True).status_code == 403     # owners only
    assert add(c, hb, "q@example.com", "caller", [w["id"]]).status_code == 200


def test_team_list_shows_two_step_and_last_sign_in(tmp_path):
    c, ho, w = setup(tmp_path)
    add(c, ho, "raj@example.com", "caller", [w["id"]])
    login(c, "raj@example.com")
    row = next(u for u in c.get("/api/users", headers=ho).json() if u["email"] == "raj@example.com")
    assert row["two_step_on"] is False and row["last_sign_in"] and row["no_workspace"] is False
