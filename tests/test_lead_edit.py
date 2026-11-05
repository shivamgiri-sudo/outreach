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
