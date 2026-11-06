"""Workspaces: imports land in the chosen workspace, every list view is scoped, moving is logged, access can be limited."""
from fastapi.testclient import TestClient

from app.main import create_app
from tests.flow_helpers import CSV, make_client

CSV2 = "company,contact_name,title,email,website\nClientCo,Neha Jain,CHRO,neha@clientco.test,https://clientco.test\n"


def ids(c, ws=""):
    return {l["company"]: l["id"] for l in c.get("/api/leads", headers={"X-Workspace": ws} if ws else {}).json()}


def test_import_and_views_are_scoped_to_the_workspace(tmp_path):
    c = make_client(tmp_path)
    w = c.post("/api/workspaces", json={"name": "Client One"}).json()
    assert c.post("/api/workspaces", json={"name": "client one"}).status_code == 400
    c.post("/api/leads/import", json={"csv": CSV2, "source": "apollo"}, headers={"X-Workspace": w["id"]})
    assert set(ids(c, w["id"])) == {"ClientCo"}
    assert "ClientCo" not in ids(c, "main") and "Acme" in ids(c, "main")
    assert {"ClientCo", "Acme"} <= set(ids(c))                          # no header / "all" = everything
    client_id = ids(c)["ClientCo"]
    assert c.get(f"/api/leads/{client_id}", headers={"X-Workspace": "main"}).status_code == 404
    assert c.get("/api/intelligence", headers={"X-Workspace": w["id"]}).json()["quality"]["leads"] == 1
    r = c.post("/api/lists", json={"name": "Client list", "companies": ["ClientCo", "Acme"]}, headers={"X-Workspace": w["id"]})
    assert r.status_code == 200 and r.json()["lead_ids"] == [client_id] and r.json()["workspace"] == w["id"]
    assert c.get("/api/lists", headers={"X-Workspace": "main"}).json() == []
    counts = {x["id"]: x["leads"] for x in c.get("/api/workspaces").json()}
    assert counts[w["id"]] == 1 and counts["main"] >= 2


def test_move_is_logged_and_delete_needs_an_empty_workspace(tmp_path):
    c = make_client(tmp_path)
    w = c.post("/api/workspaces", json={"name": "Client Two"}).json()
    acme = ids(c)["Acme"]
    assert c.delete(f"/api/workspaces/main").status_code == 400
    assert c.post(f"/api/workspaces/{w['id']}/move", json={"lead_ids": [acme]}).json() == {"moved": 1}
    assert "Acme" in ids(c, w["id"])
    assert any("Moved to workspace Client Two" in e["text"] for e in c.get(f"/api/leads/{acme}").json()["timeline"])
    assert c.delete(f"/api/workspaces/{w['id']}").status_code == 400
    c.post("/api/workspaces/main/move", json={"lead_ids": [acme]})
    assert c.delete(f"/api/workspaces/{w['id']}").status_code == 200


def test_a_caller_limited_to_one_workspace_sees_only_it(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    h = lambda t, ws="": {"Authorization": f"Bearer {t}", **({"X-Workspace": ws} if ws else {})}
    owner = c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=h(owner))
    w = c.post("/api/workspaces", json={"name": "Client Three"}, headers=h(owner)).json()
    c.post("/api/leads/import", json={"csv": CSV2, "source": "apollo"}, headers=h(owner, w["id"]))
    c.post("/api/users", json={"email": "sam@example.com", "name": "Sam", "role": "caller", "password": "long-password-1"}, headers=h(owner))
    assert c.put("/api/users/sam@example.com", json={"workspaces": [w["id"]]}, headers=h(owner)).json()["workspaces"] == [w["id"]]
    sam = c.post("/api/login", json={"email": "sam@example.com", "password": "long-password-1"}).json()["token"]
    for ws in ("", "main", "all"):
        assert [l["company"] for l in c.get("/api/leads", headers=h(sam, ws)).json()] == ["ClientCo"]
    assert [x["id"] for x in c.get("/api/workspaces", headers=h(sam)).json()] == [w["id"]]
    assert c.post("/api/workspaces", json={"name": "Mine"}, headers=h(sam)).status_code == 403
    assert c.post("/api/workspaces/main/move", json={"lead_ids": []}, headers=h(sam)).status_code == 403
    assert c.get("/api/me", headers=h(sam)).json()["workspaces"] == [w["id"]]
