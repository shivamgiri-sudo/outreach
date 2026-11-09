"""Phase 4: the super-admin console (app/super_console.py, routes_console.py, platform_controls.py, view_as.py).
Numbers refer to the plan's items."""
import io
import json
import zipfile
from datetime import datetime, timezone

from app import accounts, auth, flow_engine, sequence_runner
from tests.signup_helpers import SUPER, make, signed_up
from tests.two_step_helpers import sign_in


def active_client(c, hs, outbox, **over):
    hp, aid, email = signed_up(c, outbox, **over)
    assert c.post(f"/api/console/accounts/{aid}/approve", headers=hs).status_code == 200
    token = c.post("/api/login", json={"email": email, "password": "delta-long-password-1"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}, aid, email


def audit_rows(c, action):
    return [r for r in c.app.state.store.read("audit") if r.get("action") == action]


# ---------------------------------------------------------------- 7. the accounts table
def test_console_lists_accounts_with_figures_pending_first_and_filters(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    hb, bid, bemail = active_client(c, hs, outbox)
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nZed Co,Zed,zed@zedco.example\n"}, headers=hb)
    hp, pid, pemail = signed_up(c, outbox, email="sam@gmail.com", company="Sam Traders")
    data = c.get("/api/console/accounts", headers=hs).json()
    assert data["accounts"][0]["id"] == pid and data["accounts"][0]["status"] == "pending"
    b = next(a for a in data["accounts"] if a["id"] == bid)
    assert b["members"] == 1 and b["leads"] == 1 and len(b["workspaces"]) == 1 and b["last_activity"]
    for key in ("sends_today", "sends_7d", "bounce_rate", "flags", "limits", "created_at"):
        assert key in b
    assert "free email" in data["accounts"][0]["flags"]
    assert [a["id"] for a in c.get("/api/console/accounts?status=pending", headers=hs).json()["accounts"]] == [pid]


def test_console_is_for_super_admins_only(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    c.post("/api/users", json={"email": "own2@mcn.example", "role": "owner", "password": "owner-long-password-1"}, headers=hs)
    ho = {"Authorization": f"Bearer {sign_in(c, 'own2@mcn.example', 'owner-long-password-1')}"}
    assert c.get("/api/console/accounts", headers=ho).status_code == 403                 # an owner who is not a super admin
    assert c.put("/api/console/stop-sending", json={"on": True}, headers=ho).status_code == 403
    assert c.get("/api/console/accounts", headers=hs).status_code == 200


def test_sends_and_bounce_rate_flag(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    hb, bid, _ = active_client(c, hs, outbox)
    store = c.app.state.store
    space = next(w["id"] for w in store.read("settings")["workspaces"] if w["account_id"] == bid)
    today = datetime.now(timezone.utc).astimezone(sequence_runner.IST).date().isoformat()
    leads = [{"id": f"l{i}", "workspace": space, "stage": "contacted", "timeline": [], "dedupe_key": f"email:p{i}@x.example",
              "sequence": {"stopped": "bounced"} if i < 3 else {}} for i in range(20)]
    store.update("leads", lambda rows: [*rows, *leads])
    store.update("outbox", lambda rows: [*rows, *[{"lead_id": f"l{i}", "status": "sent", "sent_at_ist_date": today} for i in range(20)]])
    b = next(a for a in c.get("/api/console/accounts", headers=hs).json()["accounts"] if a["id"] == bid)
    assert b["sends_today"] == 20 and b["sends_7d"] == 20 and b["bounce_rate"] == 0.15 and "bounce rate 15%" in b["flags"]


# ---------------------------------------------------------------- 8. account detail actions
def test_suspend_signs_everyone_out_deletes_tokens_and_holds_sending(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    hb, bid, bemail = active_client(c, hs, outbox)
    pair = c.post("/api/extension/pair", json={"label": "laptop"}, headers=hb)
    assert pair.status_code == 200, pair.text
    ext = {"Authorization": f"Bearer {pair.json()['token']}"}
    assert c.get("/api/extension/queue", headers=ext).status_code == 200
    r = c.post(f"/api/console/accounts/{bid}/suspend", json={"reason": "unpaid"}, headers=hs)
    assert r.status_code == 200 and r.json()["tokens_removed"] == 1
    assert c.get("/api/leads", headers=hb).status_code == 401 and c.get("/api/extension/queue", headers=ext).status_code == 401
    assert c.post("/api/login", json={"email": bemail, "password": "delta-long-password-1"}).status_code == 403
    store = c.app.state.store
    assert "suspended" in accounts.hold_reason(store, bid, "email") and "suspended" in accounts.hold_reason(store, bid, "linkedin")
    assert c.post(f"/api/console/accounts/{bid}/reactivate", headers=hs).status_code == 200
    assert c.get("/api/leads", headers=hb).status_code == 401                         # old sessions stay void
    fresh = c.post("/api/login", json={"email": bemail, "password": "delta-long-password-1"}).json()["token"]
    assert c.get("/api/leads", headers={"Authorization": f"Bearer {fresh}"}).status_code == 200
    assert audit_rows(c, "account.suspend")[-1]["account_id"] == bid and audit_rows(c, "account.reactivate")


def test_limits_grants_and_workspace_requests(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    hb, bid, bemail = active_client(c, hs, outbox)
    r = c.put(f"/api/accounts/{bid}", json={"limits": {"seats": 1, "workspaces": 2, "daily_sends": 50, "imports_per_day": 3,
                                                        "enrich_credits": 10, "ai_credits": 5}, "integrations_granted": ["whatsapp"]}, headers=hs)
    assert r.status_code == 200 and r.json()["limits"]["seats"] == 1 and r.json()["integrations_granted"] == ["whatsapp"]
    assert accounts.hold_reason(c.app.state.store, bid, "whatsapp") is None
    req = c.post("/api/workspaces", json={"name": "Delta Two"}, headers=hb).json()
    assert req["status"] == "requested"
    assert c.post(f"/api/workspaces/{req['id']}/approve", headers=hs).status_code == 200
    assert c.post("/api/workspaces", json={"name": "Delta Three"}, headers=hb).status_code == 400     # limit 2
    added = c.post("/api/users", json={"email": "two@deltafreight.example", "role": "caller", "password": "long-password-1",
                                       "workspaces": [req["id"]], "account_id": bid}, headers=hs)
    assert added.status_code == 400 and "all its 1 logins" in added.json()["detail"]                # seats


def test_export_is_a_zip_of_the_account_without_secrets(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    hb, bid, bemail = active_client(c, hs, outbox)
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nZed Co,Zed,zed@zedco.example\n"}, headers=hb)
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nMain Co,Mo,mo@mainco.example\n"}, headers=ha)
    r = c.get(f"/api/console/accounts/{bid}/export", headers=hs)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"account.json", "logins.csv", "workspaces.csv", "leads.json", "change_log.csv", "README.txt"} <= names
    leads = json.loads(z.read("leads.json"))
    assert [l["company"] for l in leads] == ["Zed Co"]
    blob = b"".join(z.read(n) for n in names)
    assert b"password_hash" not in blob and b"two_step_secret" not in blob
    assert c.get(f"/api/console/accounts/{bid}/export", headers=hb).status_code == 403


def test_delete_needs_the_typed_name_and_erases_the_account(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    hb, bid, bemail = active_client(c, hs, outbox)
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nZed Co,Zed,zed@zedco.example\n"}, headers=hb)
    assert c.post(f"/api/console/accounts/{bid}/delete", json={"confirm": "delta"}, headers=hs).status_code == 400
    assert c.post("/api/console/accounts/mcn/delete", json={"confirm": "Mas Callnet"}, headers=hs).status_code == 404
    r = c.post(f"/api/console/accounts/{bid}/delete", json={"confirm": "Delta Freight"}, headers=hs)
    assert r.status_code == 200 and r.json()["leads"] == 1 and r.json()["logins"] == 1
    store = c.app.state.store
    assert accounts.find(store, bid) is None and auth.find_user(store, bemail) is None
    assert not any(l["company"] == "Zed Co" for l in store.read("leads"))
    assert not any(w.get("account_id") == bid for w in store.read("settings")["workspaces"])
    assert c.get("/api/leads", headers=hb).status_code == 401
    assert audit_rows(c, "account.delete")


# ---------------------------------------------------------------- 9. stop all sending
def test_stop_all_sending_holds_every_channel_and_shows_a_banner(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    hb, bid, bemail = active_client(c, hs, outbox)
    store = c.app.state.store
    assert c.put("/api/console/stop-sending", json={"on": True, "reason": "incident"}, headers=hs).status_code == 200
    banner = c.get("/api/platform/status", headers=hb).json()["stop_all_sending"]
    assert banner and banner["reason"] == "incident"
    assert c.get("/api/platform/status", headers=ha).json()["stop_all_sending"]
    for aid in ("mcn", bid):
        assert "stopped" in accounts.hold_reason(store, aid, "email") and "stopped" in accounts.hold_reason(store, aid, "whatsapp")
    run = sequence_runner._run(store, datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc), "http://x", lambda m: {"status": "sent"})
    assert "stopped" in run["skipped"]
    assert "stopped" in flow_engine.tick(store)["skipped"]
    from app.senders import make_email_sender
    assert make_email_sender(store)({"to": "a@b.c", "subject": "s", "body": "b", "lead_id": "x"})["status"] == "held"
    pair = c.post("/api/extension/pair", json={"label": "laptop"}, headers=ha).json()["token"]
    q = c.get("/api/extension/queue", headers={"Authorization": f"Bearer {pair}"})
    assert q.status_code == 423 and "stopped" in q.json()["detail"]
    c.put("/api/console/stop-sending", json={"on": False}, headers=hs)
    assert c.get("/api/platform/status", headers=hb).json()["stop_all_sending"] is None
    assert accounts.hold_reason(store, "mcn", "email") is None
    assert c.get("/api/extension/queue", headers={"Authorization": f"Bearer {pair}"}).status_code == 200
    rows = audit_rows(c, "platform.stop_sending")
    assert len(rows) == 2 and any("Stop all sending" in n["title"] for n in store.read("notifications") if n.get("kind") == "security:super_admin")


# ---------------------------------------------------------------- 10. view as
def test_view_as_is_read_only_time_limited_and_audited_for_both_sides(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    hb, bid, bemail = active_client(c, hs, outbox)
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nZed Co,Zed,zed@zedco.example\n"}, headers=hb)
    assert c.post("/api/console/view-as", json={"email": bemail}, headers=hb).status_code == 403
    assert c.post("/api/console/view-as", json={"email": SUPER}, headers=hs).status_code == 400          # not a client login
    r = c.post("/api/console/view-as", json={"email": bemail}, headers=hs)
    assert r.status_code == 200, r.text
    hv = {"Authorization": f"Bearer {r.json()['token']}"}
    assert [l["company"] for l in c.get("/api/leads", headers=hv).json()] == ["Zed Co"]
    status = c.get("/api/platform/status", headers=hv).json()
    assert status["view_as"]["as"] == bemail and status["view_as"]["by"] == SUPER and status["super_admin"] is False
    w = c.post("/api/leads/import", json={"csv": "company,email\nX,x@x.example\n"}, headers=hv)
    assert w.status_code == 403 and "Read-only" in w.json()["detail"]
    assert c.post("/api/console/view-as", json={"email": bemail}, headers=hv).status_code == 403          # no nesting
    rows = audit_rows(c, "view_as.request")
    assert rows and all(r["account_id"] == bid and r["view_as"] == bemail and r["by_email"] == SUPER for r in rows)
    mine = c.get("/api/account/audit", headers=hb).json()["items"]                                     # the client's admins see it
    assert any(r["action"] == "view_as.start" for r in mine) and any(r["action"] == "view_as.request" for r in mine)
    store = c.app.state.store
    token = r.json()["token"]
    claims = auth._claims(store, token, auth._now())
    assert claims["exp"] - claims["iat"] == 30 * 60
    assert auth.authenticate(store, "admin-pass-123", token, now=claims["iat"] + 31 * 60) is None
    assert auth.renewal(store, auth.authenticate(store, "admin-pass-123", token), now=claims["iat"] + 25 * 60) is None
    assert c.post("/api/logout", headers=hv).status_code == 200                                        # Stop
    assert c.get("/api/leads", headers=hv).status_code == 401 and audit_rows(c, "view_as.stop")


def test_view_as_ends_when_the_viewer_is_no_longer_a_super_admin(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    hb, bid, bemail = active_client(c, hs, outbox)
    hv = {"Authorization": f"Bearer {c.post('/api/console/view-as', json={'email': bemail}, headers=hs).json()['token']}"}
    assert c.get("/api/me", headers=hv).status_code == 200
    monkeypatch.setenv("OUTREACH_SUPER_ADMIN", "someone-else@mcn.example")
    assert c.get("/api/me", headers=hv).status_code == 401


# ---------------------------------------------------------------- 11. audit + alerts for console actions
def test_console_actions_are_hash_chained_and_alert_the_super_admins(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    hb, bid, bemail = active_client(c, hs, outbox)
    c.post(f"/api/console/accounts/{bid}/suspend", json={}, headers=hs)
    c.post(f"/api/console/accounts/{bid}/reactivate", headers=hs)
    c.get(f"/api/console/accounts/{bid}/export", headers=hs)
    c.post("/api/console/view-as", json={"email": bemail}, headers=hs)
    store = c.app.state.store
    for action in ("account.approve", "account.suspend", "account.reactivate", "account.export", "view_as.start"):
        row = audit_rows(c, action)[-1]
        assert row["hash"] and row["prev_hash"] is not None and row["account_id"] == bid and row["by_email"] == SUPER, action
    from app import audit_chain
    assert audit_chain.verify(store.read("audit"))["ok"]
    titles = [n["title"] for n in store.read("notifications") if n.get("kind") == "security:super_admin"]
    for word in ("Account approved", "Account suspended", "Account reactivated", "Account data exported", "View as started"):
        assert any(t.startswith(word) for t in titles), word
