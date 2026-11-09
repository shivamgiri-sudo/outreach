"""Shared set-up for the account-limit, abuse-alert and scoping tests: an app with the server admin (an owner), a super
admin named in the environment, and a client account "Bravo Corp" with one workspace, an admin and a caller."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app import accounts
from app.main import create_app

ADMIN = "admin-pass-123"
BOSS = "boss@mcn.com"
LONG = "long-password-1"
CSV2 = "company,contact_name,email\nAlpha Co,Ann,ann@alpha.example\nBeta Co,Bob,bob@beta.example\n"
FAKE_AI = {"content": [{"type": "text", "text": "Hi Ann,\n\nThanks for your note. Let me check and come back.\n\n— Bea"}]}


def bearer(token, **extra):
    return {"Authorization": f"Bearer {token}", **extra}


def make_rig(tmp_path, monkeypatch, limits=None, ai=False, grants=None, name="Bravo Corp", prefix="bravo"):
    monkeypatch.setenv("OUTREACH_SUPER_ADMIN", BOSS)
    calls = []

    def ai_transport(url, headers, body):
        calls.append(body)
        return FAKE_AI

    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password=ADMIN, ai_transport=ai_transport), client=("203.0.113.9", 50000))
    ho = bearer(c.post("/api/login", json={"password": ADMIN}).json()["token"])
    if ai:
        c.put("/api/integrations/llm", json={"mode": "api", "values": {"provider": "anthropic", "api_key": "sk-test-1234"}}, headers=ho)
    b = c.post("/api/accounts", json={"name": name, "limits": limits or {}, "integrations_granted": grants or []}, headers=ho).json()
    ws = c.post("/api/workspaces", json={"name": f"{name} Sales", "account_id": b["id"]}, headers=ho).json()
    users = {}
    for role in ("admin", "caller"):
        email = f"{role}@{prefix}.com"
        r = c.post("/api/users", json={"email": email, "name": f"{prefix.title()} {role.title()}", "role": role, "password": LONG,
                                       "workspaces": [ws["id"]], "account_id": b["id"]}, headers=ho)
        assert r.status_code == 200, r.text
        users[role] = bearer(c.post("/api/login", json={"email": email, "password": LONG}).json()["token"])
    return {"c": c, "ho": ho, "hb": users["admin"], "hc": users["caller"], "b": b["id"], "ws": ws["id"], "store": c.app.state.store,
            "ai_calls": calls, "name": name}


def set_limits(rig, **limits):
    r = rig["c"].put(f"/api/accounts/{rig['b']}", json={"limits": limits}, headers=rig["ho"])
    assert r.status_code == 200, r.text
    return r.json()


def lead_of(rig, csv=CSV2, headers=None):
    """Import into the client account (its admin) and return the lead ids."""
    c = rig["c"]
    assert c.post("/api/leads/import", json={"csv": csv}, headers=headers or rig["hb"]).status_code == 200
    return [l["id"] for l in c.get("/api/leads", headers=headers or rig["hb"]).json()]


def notes_for(store, account_id):
    return [n for n in store.read("notifications") if n.get("account_id") == account_id]


def audit_rows(store, action, account_id=None):
    return [r for r in store.read("audit") if r.get("action") == action and (account_id is None or r.get("account_id") == account_id)]


def mails_to(store, address):
    return [m for m in store.read("transactional_outbox") if m.get("to") == address]


def today():
    return datetime.now(timezone.utc)
