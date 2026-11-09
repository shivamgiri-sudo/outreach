"""Accounts (app/accounts.py): one client company per account, above its workspaces.

Existing data becomes account "mcn" (idempotent migration); a client login of account B never sees or changes account
A's leads, lists, companies, replies, outbox, templates, logins, audit rows, reports, LinkedIn queue, stage board,
exports or settings; the same contact may live in two accounts while dedupe inside an account is unchanged; a client
is never an owner; sending for an account without its own identity or a grant is held; a client admin's new workspace
waits for an owner; audit rows carry account_id."""
import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import accounts
from app.main import create_app
from app.store import Store

T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)      # Tuesday 10:30 IST
MAIN_CSV = ("company,contact_name,title,email,phone,website\n"
            "Acme,Ravi Kumar,HR Head,ravi@acme.com,98765 43210,https://acme.com\n"
            "Beta Ltd,Sita Rao,HR,sita@beta.in,91234 56789,https://beta.in\n")


def setup(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    ho = {"Authorization": f"Bearer {c.post('/api/login', json={'password': 'admin-pass-123'}).json()['token']}"}
    c.post("/api/leads/import", json={"csv": MAIN_CSV, "source": "apollo"}, headers=ho)
    b = c.post("/api/accounts", json={"name": "Bravo Corp"}, headers=ho)
    assert b.status_code == 200, b.text
    b = b.json()
    ws = c.post("/api/workspaces", json={"name": "Bravo Sales", "account_id": b["id"]}, headers=ho).json()
    assert ws["account_id"] == b["id"]
    r = c.post("/api/users", json={"email": "admin@bravo.com", "name": "Bravo Admin", "role": "admin", "password": "long-password-1",
                                   "workspaces": [ws["id"]], "account_id": b["id"]}, headers=ho)
    assert r.status_code == 200, r.text
    token = c.post("/api/login", json={"email": "admin@bravo.com", "password": "long-password-1"}).json()["token"]
    return c, ho, {"Authorization": f"Bearer {token}"}, b, ws


def ids(c, h):
    return {l["id"]: l for l in c.get("/api/leads", headers=h).json()}


def test_migration_turns_existing_data_into_mcn_and_is_idempotent(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"settings": {"workspaces": [{"id": "c1-ab12", "name": "Client One"}],
                                             "users": [{"email": "a@x.com", "role": "caller", "workspaces": ["c1-ab12"]},
                                                       {"email": "o@x.com", "role": "owner"}]},
                                "leads": [{"id": "l1", "workspace": "c1-ab12", "dedupe_key": "email:a@b.com", "timeline": [], "stage": "new"}]}))
    create_app(data_file=path, admin_password="admin-pass-123")
    s = json.loads(path.read_text())["settings"]
    assert [a["id"] for a in s["accounts"]] == ["mcn"]
    assert all(w["account_id"] == "mcn" for w in s["workspaces"]) and all(u["account_id"] == "mcn" for u in s["users"])
    before = path.read_text()
    store = Store(path)
    assert accounts.migrate(store) is False                       # a second run changes nothing
    create_app(data_file=path, admin_password="admin-pass-123")
    assert path.read_text() == before
    assert json.loads(path.read_text())["leads"][0]["workspace"] == "c1-ab12"      # leads are never rewritten


def test_client_admin_of_b_sees_nothing_of_a(tmp_path):
    c, ho, hb, b, ws = setup(tmp_path)
    acme = next(l for l in c.get("/api/leads", headers=ho).json() if l["company"] == "Acme")
    c.post(f"/api/leads/{acme['id']}/reply", json={"text": "Yes, interested. Call me."}, headers=ho)
    c.post(f"/api/leads/{acme['id']}/enroll", headers=ho)
    c.post("/api/sequences/run", json={"now": T0.isoformat()}, headers=ho)
    c.post("/api/lists", json={"name": "Main list", "lead_ids": [acme["id"]]}, headers=ho)
    c.post("/api/snippets", json={"name": "Main snippet", "text": "Hello from Mas Callnet"}, headers=ho)
    c.put("/api/team", json={"team": [{"name": "Mcn Staffer", "email": "staff@mascallnet.com"}]}, headers=ho)
    c.put("/api/settings", json={"sender_name": "Mcn Sender", "sender_company": "Mas Callnet"}, headers=ho)
    for header in (hb, {**hb, "X-Workspace": "main"}, {**hb, "X-Account": "mcn"}):
        assert c.get("/api/leads", headers=header).json() == []
        assert c.get(f"/api/leads/{acme['id']}", headers=header).status_code == 404
    assert c.post(f"/api/leads/{acme['id']}/note", json={"text": "x"}, headers=hb).status_code == 404
    assert c.get("/api/lists", headers=hb).json() == []
    c.post("/api/tasks", json={"title": "Mcn team task"}, headers=ho)
    assert c.get("/api/companies", headers=hb).json()["items"] == []
    assert c.get("/api/reply-inbox", headers=hb).json()["replies"] == []
    assert c.get("/api/outbox", headers=hb).json() == [] and c.get("/api/inbox", headers=hb).json() == []
    assert "Main snippet" not in c.get("/api/snippets", headers=hb).text
    assert "Mas Callnet" not in c.get("/api/sequences", headers=hb).text or all(
        (s.get("workspace") or "main") != "main" for s in c.get("/api/sequences", headers=hb).json() if isinstance(s, dict))
    assert [u["email"] for u in c.get("/api/users", headers=hb).json()] == ["admin@bravo.com"]
    assert c.get("/api/audit", headers=hb).status_code == 403
    assert "Acme" not in c.get("/api/change-log", headers=hb).text
    assert "Acme" not in c.get("/api/reports/daily", headers=hb).text and "Ravi" not in c.get("/api/reports/daily", headers=hb).text
    assert c.get("/api/linkedin/queue", headers=hb).json()["suggestions"] == []
    assert "Acme" not in c.get("/api/linkedin/stages", headers=hb).text
    assert c.get("/api/linkedin/accounts", headers=hb).json() == {"accounts": [], "owner_defaults": {}}
    assert c.get("/api/export/leads.csv", headers=hb).status_code == 403
    assert c.get("/api/outbox/export.csv", headers=hb).status_code in (403, 200) and "acme" not in c.get("/api/outbox/export.csv", headers=hb).text.lower()
    settings = c.get("/api/settings", headers=hb).json()
    assert "Mcn" not in json.dumps(settings) and "Mas Callnet" not in json.dumps(settings)
    assert "staff@mascallnet.com" not in c.get("/api/team", headers=hb).text
    assert [w["id"] for w in c.get("/api/workspaces", headers=hb).json()] == [ws["id"]]
    assert c.get("/api/accounts", headers=hb).json() == {"accounts": [{"id": b["id"], "name": "Bravo Corp", "status": "active"}]}
    for path in ("/api/offer", "/api/call-script", "/api/icp", "/api/sequence/0/preview"):    # its own values, never Mas Callnet's
        r = c.get(path, headers=hb)
        assert r.status_code == 200 and not any(w in r.text for w in ("Mas Callnet", "Mcn Sender", "BookMyInterview", "Acme", "bravo-corp" + "x")), path
    assert "Bravo Corp" in c.get("/api/call-script", headers=hb).text
    assert "account_settings" not in c.get("/api/offer", headers=ho).text and "Bravo" not in c.get("/api/offer", headers=ho).text
    assert c.post("/api/linkedin/sequence/copy", json={}, headers=hb).status_code == 403
    assert all(not str(n.get("kind", "")).startswith("security") for n in c.get("/api/notifications", headers=hb).json())
    assert c.get("/api/tasks", headers=hb).json() == [] and "Mcn team task" not in c.get("/api/today", headers=hb).text
    assert [m["email"] for m in c.get("/api/tasks/board", headers=hb).json()["team"]] == ["admin@bravo.com"]
    me = c.get("/api/me", headers=hb).json()
    assert me["account_id"] == b["id"] and me["account_name"] == "Bravo Corp" and me["client"] is True


def test_owner_sees_every_account_and_can_pick_one(tmp_path):
    c, ho, hb, b, ws = setup(tmp_path)
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nZeta,Zed,zed@zeta.io\n"}, headers=hb)
    assert len(c.get("/api/leads", headers=ho).json()) == 3
    assert [l["company"] for l in c.get("/api/leads", headers={**ho, "X-Account": b["id"]}).json()] == ["Zeta"]
    assert len(c.get("/api/leads", headers={**ho, "X-Account": "mcn"}).json()) == 2
    names = {a["id"]: a for a in c.get("/api/accounts", headers=ho).json()["accounts"]}
    assert names["mcn"]["name"] == "Mas Callnet" and names[b["id"]]["logins"] == 1 and names[b["id"]]["leads"] == 1
    assert {w["id"] for w in c.get("/api/workspaces", headers={**ho, "X-Account": b["id"]}).json()} == {ws["id"]}


def test_same_contact_lives_in_two_accounts_dedupe_inside_unchanged(tmp_path):
    c, ho, hb, b, ws = setup(tmp_path)
    r = c.post("/api/leads/import", json={"csv": MAIN_CSV, "source": "upload"}, headers=hb).json()
    assert r["created"] == 2 and r["duplicates"] == 0
    again = c.post("/api/leads/import", json={"csv": MAIN_CSV, "source": "upload"}, headers=hb).json()
    assert again["created"] == 0 and again["duplicates"] == 2           # inside the account: one copy, as before
    smart = c.post("/api/import/commit", json={"csv": MAIN_CSV.replace("Acme,", "Acme Two,").replace("ravi@", "ravi2@"), "source": "upload"}, headers=hb)
    assert smart.status_code == 200, smart.text
    assert len(c.get("/api/leads", headers=ho).json()) == 4 + len(smart.json().get("created_ids") or [])
    assert len(ids(c, hb)) >= 2
    mine = next(l for l in c.get("/api/leads", headers=hb).json() if l.get("email") == "sita@beta.in")
    edit = c.patch(f"/api/leads/{mine['id']}", json={"phone": "98765 43210"}, headers=hb)       # Acme's phone in account A
    assert edit.status_code == 400 or edit.status_code == 200
    assert "Mas Callnet" not in edit.text and "main" not in edit.text.lower().split("domain")[0]
    # Mas Callnet's own workspaces still share one copy of a contact (a limited login is told only "duplicate")
    one = c.post("/api/workspaces", json={"name": "Client One"}, headers=ho).json()
    c.post("/api/users", json={"email": "raj@example.com", "role": "caller", "password": "long-password-1", "workspaces": [one["id"]]}, headers=ho)
    hr = {"Authorization": f"Bearer {c.post('/api/login', json={'email': 'raj@example.com', 'password': 'long-password-1'}).json()['token']}"}
    r = c.post("/api/leads/import", json={"csv": MAIN_CSV}, headers=hr).json()
    assert r["created"] == 0 and r["duplicates"] == 2 and "elsewhere" not in r


def test_client_is_never_an_owner_and_admin_stays_in_its_account(tmp_path):
    c, ho, hb, b, ws = setup(tmp_path)
    bad = c.post("/api/users", json={"email": "boss@bravo.com", "role": "owner", "password": "long-password-1", "account_id": b["id"],
                                     "workspaces": [ws["id"]]}, headers=ho)
    assert bad.status_code in (400, 403)
    assert c.put("/api/users/admin@bravo.com", json={"role": "owner"}, headers=ho).status_code in (400, 403)
    assert c.post("/api/users", json={"email": "x@bravo.com", "role": "caller", "password": "long-password-1", "account_id": "mcn"},
                  headers=hb).status_code == 403
    assert c.post("/api/users", json={"email": "x@bravo.com", "role": "caller", "password": "long-password-1", "workspaces": ["main"]},
                  headers=hb).status_code in (400, 403)
    ok = c.post("/api/users", json={"email": "caller@bravo.com", "role": "caller", "password": "long-password-1"}, headers=hb)
    assert ok.status_code == 200 and ok.json()["account_id"] == b["id"] and ok.json()["workspaces"] == [ws["id"]]
    assert c.post("/api/users", json={"email": "c2@x.com", "role": "caller", "password": "long-password-1", "account_id": b["id"],
                                      "workspaces": ["main"]}, headers=ho).status_code == 400         # main is Mas Callnet's
    assert c.post(f"/api/workspaces/{ws['id']}/members", json={"email": "raj@nowhere.com"}, headers=hb).status_code == 404
    assert c.get("/api/users", headers=hb).status_code == 200
    assert "admin-pass" not in c.get("/api/users", headers=hb).text


def test_sending_held_without_identity_works_with_a_grant(tmp_path):
    c, ho, hb, b, ws = setup(tmp_path)
    c.post("/api/leads/import", json={"csv": "company,contact_name,email,website\nZeta,Zed,zed@zeta.io,https://zeta.io\n"}, headers=hb)
    lid = next(iter(ids(c, hb)))
    assert c.post(f"/api/leads/{lid}/enroll", headers=hb).status_code == 200
    c.post("/api/sequences/run", json={"now": T0.isoformat()}, headers=ho)
    seq = c.get(f"/api/leads/{lid}", headers=hb).json()["sequence"]
    assert seq.get("blocked") == accounts.HOLD_EMAIL and not seq.get("practice_days")
    assert accounts.HOLD_EMAIL in str(c.get("/api/settings/live-sending", headers=hb).json()["blockers"])
    now = c.post(f"/api/leads/{lid}/email-now", headers=hb)
    assert now.status_code == 400 and "sending domain" in now.json()["detail"]
    grant = c.put(f"/api/accounts/{b['id']}", json={"integrations_granted": ["sending_domain"]}, headers=ho)
    assert grant.status_code == 200 and grant.json()["email_hold"] is None
    c.post("/api/sequences/run", json={"now": T0.isoformat()}, headers=ho)
    seq = c.get(f"/api/leads/{lid}", headers=hb).json()["sequence"]
    assert seq.get("practice_days") == [0]
    row = next(m for m in c.get("/api/outbox", headers=hb).json() if m["lead_id"] == lid)
    assert row["status"] == "dry_run"
    raw = json.loads((tmp_path / "a.json").read_text())
    assert next(m for m in raw["outbox"] if m["lead_id"] == lid)["account_id"] == b["id"]
    # own identity instead of a grant: the account's own sender name signs it, never Mas Callnet's
    c.put(f"/api/accounts/{b['id']}", json={"integrations_granted": []}, headers=ho)
    saved = c.put(f"/api/accounts/{b['id']}/settings", json={"sender_name": "Bea Bravo", "sending": {"api_key": "re_123456789",
                  "from_email": "bea@bravo.com", "from_name": "Bea"}}, headers=ho).json()
    assert saved["email_hold"] is None and saved["settings"]["sending"]["api_key"] == "••••6789"
    assert c.get("/api/settings", headers=hb).json()["sender_name"] == "Bea Bravo"


def test_client_admin_workspace_request_needs_owner_approval(tmp_path):
    c, ho, hb, b, ws = setup(tmp_path)
    req = c.post("/api/workspaces", json={"name": "Bravo Two"}, headers=hb)
    assert req.status_code == 200 and req.json()["status"] == "requested" and req.json()["account_id"] == b["id"]
    assert [w["id"] for w in c.get("/api/workspaces", headers=hb).json()] == [ws["id"]]
    assert c.post(f"/api/workspaces/{req.json()['id']}/approve", headers=hb).status_code == 403
    over = {w["id"]: w for w in c.get("/api/workspaces/overview", headers=ho).json()}
    assert over[req.json()["id"]]["status"] == "requested" and over[req.json()["id"]]["account_name"] == "Bravo Corp"
    assert c.post(f"/api/workspaces/{req.json()['id']}/approve", headers=ho).status_code == 200
    assert {w["id"] for w in c.get("/api/workspaces", headers=hb).json()} == {ws["id"], req.json()["id"]}
    other = c.post("/api/workspaces", json={"name": "Bravo Three"}, headers=hb).json()
    assert c.post(f"/api/workspaces/{other['id']}/reject", headers=ho).status_code == 200
    assert other["id"] not in {w["id"] for w in c.get("/api/workspaces/overview", headers=ho).json()}
    c.put(f"/api/accounts/{b['id']}", json={"limits": {"workspaces": 2}}, headers=ho)
    assert c.post("/api/workspaces", json={"name": "Bravo Four"}, headers=hb).status_code == 400


def test_audit_rows_carry_account_and_suspension_signs_out(tmp_path):
    c, ho, hb, b, ws = setup(tmp_path)
    lid = c.post("/api/leads/import", json={"csv": "company,contact_name,email\nZeta,Zed,zed@zeta.io\n"}, headers=hb) and next(iter(ids(c, hb)))
    c.post(f"/api/leads/{lid}/stage", json={"stage": "verified"}, headers=hb)
    rows = json.loads((tmp_path / "a.json").read_text())["audit"]
    by_action = {}
    for r in rows:
        by_action.setdefault(r.get("action"), []).append(r)
    assert by_action["account.create"][0]["account_id"] == b["id"]
    assert by_action["user.add"][-1]["account_id"] == b["id"]
    assert any(r["account_id"] == b["id"] for r in by_action.get("stage.change", []))
    assert all(r.get("account_id") for r in rows if r.get("action"))
    trail = c.get(f"/api/audit?account={b['id']}", headers=ho).json()["items"]
    assert trail and all(r.get("account_id") == b["id"] for r in trail)
    assert c.post("/api/accounts", json={"name": "Nope"}, headers=hb).status_code == 403
    c.put(f"/api/accounts/{b['id']}", json={"status": "suspended"}, headers=ho)
    assert c.get("/api/leads", headers=hb).status_code == 401
    login = c.post("/api/login", json={"email": "admin@bravo.com", "password": "long-password-1"})
    assert login.status_code == 403 and "not active" in login.json()["detail"]
    assert c.put("/api/accounts/mcn", json={"status": "suspended"}, headers=ho).status_code == 400


def test_mas_callnet_staff_without_a_workspace_list_stop_at_the_account_boundary(tmp_path):
    c, ho, hb, b, ws = setup(tmp_path)
    c.post("/api/users", json={"email": "cal@mcn.example", "role": "caller", "password": "long-password-1", "all_workspaces": True}, headers=ho)
    hc = {"Authorization": f"Bearer {c.post('/api/login', json={'email': 'cal@mcn.example', 'password': 'long-password-1'}).json()['token']}"}
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nZeta,Zed,zed@zeta.io\n"}, headers=hb)
    assert {l["company"] for l in c.get("/api/leads", headers=hc).json()} == {"Acme", "Beta Ltd"}
    assert ws["id"] not in {w["id"] for w in c.get("/api/workspaces", headers=hc).json()}
    zeta = next(l for l in c.get("/api/leads", headers=hb).json() if l["company"] == "Zeta")
    assert c.get(f"/api/leads/{zeta['id']}", headers={**hc, "X-Workspace": ws["id"]}).status_code == 404
    # a workspace name may repeat across accounts without telling the client anything
    assert c.post("/api/workspaces", json={"name": "Client One"}, headers=ho).status_code == 200
    assert c.post("/api/workspaces", json={"name": "Client One"}, headers=hb).json()["status"] == "requested"
