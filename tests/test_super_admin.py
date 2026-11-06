"""Super admin (OUTREACH_SUPER_ADMIN): only they manage owner logins; their login cannot be removed or demoted."""
from fastapi.testclient import TestClient

from app.main import create_app

PW = "long-password-1"


def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTREACH_SUPER_ADMIN", "boss@example.com")
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    h = lambda t: {"Authorization": f"Bearer {t}"}
    admin = c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    assert c.post("/api/users", json={"email": "boss@example.com", "name": "Boss", "role": "owner", "password": PW}, headers=h(admin)).status_code == 200
    login = lambda e: c.post("/api/login", json={"email": e, "password": PW}).json()["token"]
    return c, h, login


def test_super_admin_rules(tmp_path, monkeypatch):
    c, h, login = rig(tmp_path, monkeypatch)
    boss = login("boss@example.com")
    assert c.get("/api/me", headers=h(boss)).json()["super_admin"] is True
    assert c.post("/api/users", json={"email": "own@example.com", "name": "Owner Two", "role": "owner", "password": PW}, headers=h(boss)).status_code == 200
    own = login("own@example.com")
    me = c.get("/api/me", headers=h(own)).json()
    assert me["role"] == "owner" and me["super_admin"] is False
    # the second owner has full data access ...
    assert c.get("/api/leads", headers=h(own)).status_code == 200 and c.get("/api/users", headers=h(own)).status_code == 200
    # ... and may add callers, but not owners, and cannot touch the super admin
    assert c.post("/api/users", json={"email": "cal@example.com", "role": "caller", "password": PW}, headers=h(own)).status_code == 200
    assert c.post("/api/users", json={"email": "own3@example.com", "role": "owner", "password": PW}, headers=h(own)).status_code == 403
    assert c.put("/api/users/cal@example.com", json={"role": "owner"}, headers=h(own)).status_code == 403
    assert c.delete("/api/users/boss@example.com", headers=h(own)).status_code == 403
    assert c.put("/api/users/boss@example.com", json={"password": "another-pass-99"}, headers=h(own)).status_code == 403
    assert c.delete("/api/users/own@example.com", headers=h(own)).status_code == 403
    # the super admin manages owners, but cannot remove or demote themselves
    assert c.put("/api/users/boss@example.com", json={"role": "caller"}, headers=h(boss)).status_code == 403
    assert c.delete("/api/users/boss@example.com", headers=h(boss)).status_code == 403
    assert c.delete("/api/users/own@example.com", headers=h(boss)).status_code == 200
    users = {u["email"]: u for u in c.get("/api/users", headers=h(boss)).json()}
    assert users["boss@example.com"]["super_admin"] is True and "own@example.com" not in users


def test_without_a_super_admin_every_owner_manages_logins(tmp_path, monkeypatch):
    monkeypatch.delenv("OUTREACH_SUPER_ADMIN", raising=False)
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    admin = c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    c.post("/api/users", json={"email": "a@example.com", "role": "owner", "password": PW}, headers={"Authorization": f"Bearer {admin}"})
    t = c.post("/api/login", json={"email": "a@example.com", "password": PW}).json()["token"]
    assert c.post("/api/users", json={"email": "b@example.com", "role": "owner", "password": PW}, headers={"Authorization": f"Bearer {t}"}).status_code == 200
