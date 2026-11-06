"""Regressions found by the browser QA sweep of setup, roles and data safety (area: admin)."""
from fastapi.testclient import TestClient

from app.brand import validate_brand, validate_offer
from app.main import create_app

PW = "long-password-1"


def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTREACH_SUPER_ADMIN", "boss@example.com")
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    h = lambda t: {"Authorization": f"Bearer {t}"}
    admin = c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    for email, role in (("boss@example.com", "owner"), ("cal@example.com", "caller"), ("view@example.com", "viewer")):
        assert c.post("/api/users", json={"email": email, "role": role, "password": PW}, headers=h(admin)).status_code == 200
    login = lambda e, p=PW: c.post("/api/login", json={"email": e, "password": p})
    return c, h, login, admin


def test_website_and_booking_link_accept_a_bare_domain():
    assert validate_brand({"website": "skylinehomes.in"})["website"] == "https://skylinehomes.in"
    assert validate_brand({"website": "www.skylinehomes.in/about"})["website"] == "https://www.skylinehomes.in/about"
    assert validate_offer({"booking_link": "cal.com/anita/visit"})["booking_link"] == "https://cal.com/anita/visit"
    for bad in ("not a url", "javascript:alert(1)", "ftp://x.com"):
        try:
            validate_brand({"website": bad})
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad}")


def test_every_login_changes_its_own_password(tmp_path, monkeypatch):
    c, h, login, admin = rig(tmp_path, monkeypatch)
    tokens = {e: login(e).json()["token"] for e in ("cal@example.com", "view@example.com")}   # /api/login allows 5 a minute
    for email, token in tokens.items():
        r = c.post("/api/me/password", json={"current": "wrong-password", "new": "brand-new-pass-1"}, headers=h(token))
        assert r.status_code == 400 and "current password" in r.json()["detail"]
        assert c.post("/api/me/password", json={"current": PW, "new": "short"}, headers=h(token)).status_code == 400
        assert c.post("/api/me/password", json={"current": PW, "new": "brand-new-pass-1"}, headers=h(token)).status_code == 200
        assert c.get("/api/me", headers=h(token)).status_code == 401           # every old session is signed out
    assert login("view@example.com").status_code == 401 and login("view@example.com", "brand-new-pass-1").status_code == 200
    # the server admin password cannot be changed from the app; no sign-in, no change
    assert c.post("/api/me/password", json={"current": "admin-pass-123", "new": "brand-new-pass-1"}, headers=h(admin)).status_code == 400
    assert c.post("/api/me/password", json={"current": PW, "new": "brand-new-pass-1"}).status_code == 401


def test_a_workspace_limited_login_never_sees_another_clients_tasks_or_calls(tmp_path, monkeypatch):
    from tests.flow_helpers import CSV
    from tests.test_workspaces import CSV2
    c, h, login, admin = rig(tmp_path, monkeypatch)
    c.post("/api/leads/import", json={"csv": CSV + "Konkan Realty,Suresh Naik,Sales Head,suresh@konkan.test,https://konkan.test\n", "source": "apollo"}, headers=h(admin))
    w = c.post("/api/workspaces", json={"name": "Acme Staffing"}, headers=h(admin)).json()
    c.post("/api/leads/import", json={"csv": CSV2, "source": "apollo"}, headers={**h(admin), "X-Workspace": w["id"]})
    ids = {l["company"]: l["id"] for l in c.get("/api/leads", headers=h(admin)).json()}
    for company in ("Konkan Realty", "ClientCo"):           # each says yes: an Interested lead with a call task
        r = c.post(f"/api/leads/{ids[company]}/consent", json={"text": "Yes, call me", "channel": "any", "move_to_interested": True}, headers=h(admin))
        assert r.status_code == 200, r.text
    assert c.put("/api/users/cal@example.com", json={"workspaces": [w["id"]]}, headers=h(admin)).status_code == 200
    cal = login("cal@example.com").json()["token"]
    for path in ("/api/tasks", "/api/tasks/board", "/api/notifications", "/api/today", "/api/dashboard", "/api/my-day", "/api/call-queue"):
        body = c.get(path, headers=h(cal)).text
        assert "Konkan" not in body, path
    assert [t["lead_id"] for t in c.get("/api/tasks", headers=h(cal)).json()] == [ids["ClientCo"]]
    assert c.get("/api/dashboard", headers=h(cal)).json()["total_leads"] == 1
    # knowing the id is not enough: reads and writes on the other client's lead answer 404
    assert c.post(f"/api/leads/{ids['Konkan Realty']}/note", json={"text": "hello"}, headers=h(cal)).status_code == 404
    assert c.post(f"/api/leads/{ids['Konkan Realty']}/call-outcome", json={"outcome": "no_answer"}, headers=h(cal)).status_code == 404
    konkan_task = next(t["id"] for t in c.get("/api/tasks", headers=h(admin)).json() if t["lead_id"] == ids["Konkan Realty"])
    assert c.post(f"/api/tasks/{konkan_task}/done", json={"note": ""}, headers=h(cal)).status_code == 404
    assert c.put(f"/api/tasks/{konkan_task}", json={"snooze_minutes": 60}, headers=h(cal)).status_code == 404
    assert c.post(f"/api/leads/{ids['ClientCo']}/note", json={"text": "called"}, headers=h(cal)).status_code == 200
    # owners still see everything
    assert "Konkan" in c.get("/api/tasks", headers=h(admin)).text and c.get("/api/dashboard", headers=h(admin)).json()["total_leads"] >= 2
