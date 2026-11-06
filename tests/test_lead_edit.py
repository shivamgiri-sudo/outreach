"""Manual contact corrections: validated, logged with who/what, visible in the lead journey."""
from tests.flow_helpers import lead, make_client


def test_edit_changes_fields_and_logs_who(tmp_path):
    c = make_client(tmp_path)
    a = lead(c, "Acme")
    r = c.patch(f"/api/leads/{a['id']}", json={"email": "Ravi.K@Acme.com", "contact_name": "Ravi K", "reason": "typo on card"})
    assert r.status_code == 200 and r.json()["email"] == "ravi.k@acme.com" and set(r.json()["changed"]) == {"email", "contact_name"}
    log = c.get(f"/api/leads/{a['id']}/changes").json()
    assert {x["field"] for x in log} == {"email", "contact_name"}
    em = next(x for x in log if x["field"] == "email")
    assert em["old"] == "ravi@acme.com" and em["new"] == "ravi.k@acme.com" and em["by"] and em["reason"] == "typo on card"
    journey = [e for e in c.get(f"/api/leads/{a['id']}").json()["timeline"] if e["kind"] == "edit"]
    assert len(journey) == 1 and "ravi@acme.com → ravi.k@acme.com" in journey[0]["text"] and journey[0]["by"]
    assert len(c.get("/api/change-log").json()) == 2


def test_edit_validates_and_refuses_duplicates(tmp_path):
    c = make_client(tmp_path)
    a, b = lead(c, "Acme"), lead(c, "Beta")
    assert c.patch(f"/api/leads/{a['id']}", json={"email": "nope"}).status_code == 400
    assert c.patch(f"/api/leads/{a['id']}", json={"phone": "123"}).status_code == 400
    assert c.patch(f"/api/leads/{a['id']}", json={"email": "sam@beta.in"}).status_code == 400      # Beta already has it
    assert c.patch(f"/api/leads/{a['id']}", json={}).status_code == 400
    assert c.patch("/api/leads/missing", json={"title": "x"}).status_code == 404
    assert c.patch(f"/api/leads/{a['id']}", json={"phone": "98111 22233"}).json()["phone"] == "+919811122233"
    assert c.get(f"/api/leads/{a['id']}/changes").json()[0]["field"] == "phone"
    assert c.get(f"/api/leads/{b['id']}/changes").json() == []


def test_no_change_writes_no_log(tmp_path):
    c = make_client(tmp_path)
    a = lead(c, "Acme")
    r = c.patch(f"/api/leads/{a['id']}", json={"company": "Acme"})
    assert r.status_code == 200 and r.json()["changed"] == [] and c.get("/api/change-log").json() == []


def test_owner_can_switch_off_contact_editing_for_one_caller(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.flow_helpers import CSV
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    h = lambda t: {"Authorization": f"Bearer {t}"}
    owner = c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    c.post("/api/users", json={"email": "ravi@example.com", "name": "Ravi", "role": "caller", "password": "long-password-1"}, headers=h(owner))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=h(owner))
    a = next(l for l in c.get("/api/leads", headers=h(owner)).json() if l["company"] == "Acme")
    ravi = c.post("/api/login", json={"email": "ravi@example.com", "password": "long-password-1"}).json()["token"]
    assert c.get("/api/me", headers=h(ravi)).json()["can_edit_contacts"] is True
    assert c.patch(f"/api/leads/{a['id']}", json={"title": "CHRO"}, headers=h(ravi)).status_code == 200
    r = c.put("/api/users/ravi@example.com", json={"can_edit_contacts": False}, headers=h(owner))
    assert r.status_code == 200 and r.json()["can_edit_contacts"] is False
    assert c.get("/api/me", headers=h(ravi)).json()["can_edit_contacts"] is False
    r = c.patch(f"/api/leads/{a['id']}", json={"title": "CEO"}, headers=h(ravi))
    assert r.status_code == 403 and "not allowed" in r.json()["detail"]
    assert c.patch(f"/api/leads/{a['id']}", json={"title": "CEO"}, headers=h(owner)).status_code == 200
