"""Reports (daily log, per person, CSV, email with HTML + attachment + signed link), self-service password reset,
and extra phones / emails / people on a lead with audit rows."""
from fastapi.testclient import TestClient

from app.main import create_app

CSV = ("company,contact_name,email,phone,website\n"
       "Acme,Ravi Kumar,ravi@acme.com,98765 43210,https://acme.com\n"
       "Beta Ltd,Sita Rao,sita@beta.in,91234 56789,https://beta.in\n")


def make(tmp_path, password="admin-pass-123"):
    sent = []
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password=password), client=("203.0.113.9", 50000))
    tok = c.post("/api/login", json={"password": password}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=h)
    c.post("/api/users", json={"email": "priya@team.example", "name": "Priya", "role": "caller", "password": "long-password-1"}, headers=h)
    p = {"Authorization": "Bearer " + c.post("/api/login", json={"email": "priya@team.example", "password": "long-password-1"}).json()["token"]}
    return c, h, p, sent


def lid(c, h, company):
    return next(l["id"] for l in c.get("/api/leads", headers=h).json() if l["company"] == company)


def test_daily_report_counts_calls_and_shows_number_used(tmp_path):
    c, h, p, _ = make(tmp_path)
    acme = lid(c, h, "Acme")
    c.post(f"/api/leads/{acme}/call-outcome", json={"outcome": "not_interested", "notes": "no budget"}, headers=p)
    c.post(f"/api/leads/{lid(c, h, 'Beta Ltd')}/call-outcome", json={"outcome": "no_answer"}, headers=p)
    r = c.get("/api/reports/daily", params={"days": 7}, headers=h).json()
    assert r["totals"]["calls"] == 2 and r["totals"]["calls_connected"] == 1 and r["totals"]["not_interested"] == 1
    assert r["totals"]["leads_added"] == 2
    call = next(x for x in r["log"] if x["kind"] == "call" and x["company"] == "Acme")
    assert call["who"] == "Priya" and call["where"].endswith("9876543210") and call["notes"] == "no budget"
    assert next(t for t in r["team"] if t["who"] == "Priya")["calls"] == 2
    assert r["days"][0]["calls"] == 2
    assert c.get("/api/reports/daily", params={"days": 7, "who": "Priya", "kind": "call"}, headers=h).json()["log_total"] == 2


def test_csv_only_for_owners_and_admins(tmp_path):
    c, h, p, _ = make(tmp_path)
    assert c.get("/api/reports/daily.csv", params={"days": 7}, headers=p).status_code == 403
    r = c.get("/api/reports/daily.csv", params={"days": 7}, headers=h)
    assert r.status_code == 200 and "Number / email / profile used" in r.text


def test_report_email_has_html_attachment_and_working_link(tmp_path):
    c, h, p, _ = make(tmp_path)
    c.post(f"/api/leads/{lid(c, h, 'Acme')}/call-outcome", json={"outcome": "interested"}, headers=p)
    r = c.post("/api/reports/email", json={"days": 7}, headers=p).json()      # practice mode: nothing really sent
    assert r["sent_to"] == ["priya@team.example"]
    assert "What the numbers say" in r["html"] and "Download the activity log" in r["html"]
    token = r["link"].rsplit("/", 1)[1]
    f = TestClient(c.app).get(f"/api/reports/file/{token}")                   # no login: the token is the key
    assert f.status_code == 200 and "Acme" in f.text and "attachment" in f.headers["content-disposition"]
    assert TestClient(c.app).get("/api/reports/file/not-a-real-token-123").status_code == 404
    assert c.post("/api/reports/email", json={"days": 7, "send_to": ["boss@x.example"]}, headers=p).status_code == 403


def test_report_link_keeps_the_askers_workspace(tmp_path):
    c, h, p, _ = make(tmp_path)
    w = c.post("/api/workspaces", json={"name": "Client"}, headers=h).json()
    c.post("/api/users", json={"email": "raj@c.example", "role": "caller", "password": "long-password-1", "workspaces": [w["id"]]}, headers=h)
    raj = {"Authorization": "Bearer " + c.post("/api/login", json={"email": "raj@c.example", "password": "long-password-1"}).json()["token"]}
    r = c.post("/api/reports/email", json={"days": 7}, headers=raj).json()
    f = TestClient(c.app).get(f"/api/reports/file/{r['link'].rsplit('/', 1)[1]}")
    assert "Acme" not in f.text and "Beta" not in f.text


def test_password_reset_by_email_link(tmp_path):
    c, h, p, _ = make(tmp_path)
    anon = TestClient(c.app, client=("203.0.113.10", 50000))
    same = anon.post("/api/password/forgot", json={"email": "nobody@x.example"}).json()["message"]
    assert anon.post("/api/password/forgot", json={"email": "priya@team.example"}).json()["message"] == same
    rows = c.app.state.store.read("settings")["password_resets"]
    assert len(rows) == 1 and "token" not in rows[0]                          # only the hash is kept
    from app import password_reset
    import secrets
    token = secrets.token_urlsafe(32)                                          # forge a known token the same way
    c.app.state.store.update("settings", lambda s: {**s, "password_resets": [{**rows[0], "hash": password_reset._hash(token)}]})
    assert anon.post("/api/password/reset", json={"token": token, "password": "short"}).status_code == 400
    assert anon.post("/api/password/reset", json={"token": token, "password": "brand-new-pass-9"}).status_code == 200
    assert anon.post("/api/password/reset", json={"token": token, "password": "another-pass-99"}).status_code == 400   # once only
    assert c.get("/api/leads", headers=p).status_code == 401                  # old session signed out
    assert anon.post("/api/login", json={"email": "priya@team.example", "password": "brand-new-pass-9"}).status_code == 200
    for _ in range(4):
        anon.post("/api/password/forgot", json={"email": "priya@team.example"})
    recent = [r for r in c.app.state.store.read("settings")["password_resets"] if r["email"] == "priya@team.example" and not r.get("used")]
    assert len(recent) <= 3


def test_extra_phone_email_people_are_logged(tmp_path):
    c, h, p, _ = make(tmp_path)
    acme = lid(c, h, "Acme")
    r = c.post(f"/api/leads/{acme}/contact-points", json={"kind": "phone", "value": "99887 76655"}, headers=p)
    assert r.status_code == 200 and "+919988776655" in r.json()["phones"]
    assert c.post(f"/api/leads/{acme}/contact-points", json={"kind": "phone", "value": "99887 76655"}, headers=p).status_code == 400
    assert c.post(f"/api/leads/{acme}/contact-points", json={"kind": "email", "value": "not-an-email"}, headers=p).status_code == 400
    c.post(f"/api/leads/{acme}/contact-points", json={"kind": "email", "value": "ravi.k@acme.com"}, headers=p)
    lead = c.post(f"/api/leads/{acme}/contact-points/primary", json={"kind": "email", "value": "ravi.k@acme.com"}, headers=p).json()
    assert lead["email"] == "ravi.k@acme.com" and "ravi@acme.com" in lead["emails"]
    out = c.post(f"/api/leads/{acme}/call-outcome", json={"outcome": "wrong_number", "to": "+919988776655"}, headers=p).json()
    assert out["lead"]["bad_numbers"]["+919988776655"]["status"] == "wrong_number" and not out["lead"].get("phone_status")
    person = c.post(f"/api/leads/{acme}/people", json={"contact_name": "Neha Jain", "title": "CHRO", "phone": "90000 11122",
                                                        "linkedin_url": "linkedin.com/in/neha-jain"}, headers=p).json()
    pid = person["other_contacts"][-1]["id"]
    assert person["other_contacts"][-1]["linkedin_url"].startswith("https://")
    assert c.post(f"/api/leads/{acme}/people", json={"contact_name": "No Way"}, headers=p).status_code == 400
    c.put(f"/api/leads/{acme}/people/{pid}", json={"title": "VP HR"}, headers=p)
    c.delete(f"/api/leads/{acme}/people/{pid}", headers=p)
    labels = [r["label"] for r in c.get(f"/api/leads/{acme}/changes", headers=h).json()]
    for want in ("Extra phone", "Extra email", "Email", "Person: Name", "Person: Designation", "Person removed"):
        assert want in labels, want
    log = c.get("/api/reports/daily", params={"days": 7}, headers=h).json()["log"]
    assert any(x["kind"] == "call" and x["where"] == "+919988776655" for x in log)
