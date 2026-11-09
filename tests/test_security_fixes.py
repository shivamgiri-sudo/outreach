"""Security review fixes (H1, M1-M4, L1-L3, L5, L6, L8): one or more tests per finding."""
import base64
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import backup, excel_import, linkedin_cloud as cloud, linkedin_runner as runner, smtp_probe
from app.activities import now_utc
from app.main import create_app
from app.routes_auth import ANYONE, CALLER_WRITES, needed_role
from tests.test_role_ui_parity import OPEN_WRITES, _routes

ADMIN = "admin-pass-123"
PW = "long-password-1"
ROOT = Path(__file__).resolve().parent.parent
CSV = ("company,contact_name,title,email,phone,website\n"
       "Acme,Ravi Kumar,HR Head,ravi@acme.com,98765 43210,https://acme.com\n")


def bearer(t):
    return {"Authorization": f"Bearer {t}"}


def rig(tmp_path, monkeypatch=None, superadmin=""):
    if monkeypatch is not None:
        monkeypatch.setenv("OUTREACH_SUPER_ADMIN", superadmin) if superadmin else monkeypatch.delenv("OUTREACH_SUPER_ADMIN", raising=False)
    app = create_app(data_file=tmp_path / "d.json", admin_password=ADMIN)
    clients = {}

    def client(ip="203.0.113.9"):
        return clients.setdefault(ip, TestClient(app, client=(ip, 50000)))
    c = client()
    owner = c.post("/api/login", json={"password": ADMIN}).json()["token"]
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=bearer(owner))

    def login_as(email, role, workspaces=(), ip="198.51.100.7", name=""):
        body = {"email": email, "name": name or email.split("@")[0], "role": role, "password": PW, "workspaces": list(workspaces)}
        if not workspaces:
            body["all_workspaces"] = True                     # an explicit choice is required once workspaces exist
        r = c.post("/api/users", json=body, headers=bearer(owner))
        assert r.status_code == 200, r.text
        from tests.two_step_helpers import sign_in             # owners must set up two-step login first
        return sign_in(client(ip), email, PW)
    return c, owner, app.state.store, login_as, client


# ---------- H1: /api/settings is an allowlist ----------
SECRET_KEYS = ("linkedin_runner_pairing", "linkedin_runners", "linkedin_cloud", "linkedin_cloud_state", "password_resets",
               "report_links", "linkedin_agent_state", "extension_tokens", "server_secret", "users", "reply_state")


def test_settings_returns_only_allowlisted_keys_per_role(tmp_path):
    c, owner, store, login_as, _ = rig(tmp_path)
    planted = [k for k in SECRET_KEYS if k not in ("server_secret", "users", "extension_tokens")]
    store.update("settings", lambda s: {**s, **{k: [{"hash": "deadbeef"}] for k in planted}, "webhook_secret": "abcdef123456",
                                        "sender_name": "Priya", "public_base_url": "https://o.example.in"})
    caller = login_as("cal@example.com", "caller")
    mine = c.get("/api/settings", headers=bearer(owner)).json()
    theirs = c.get("/api/settings", headers=bearer(caller)).json()
    for shown in (mine, theirs):
        assert not set(SECRET_KEYS) & set(shown) and "deadbeef" not in json.dumps(shown)
    assert mine["public_base_url"] == "https://o.example.in" and mine["webhook_secret"] == "••••3456"
    assert theirs["sender_name"] == "Priya" and "public_base_url" not in theirs and "webhook_secret" not in theirs
    from app.routes_outreach import SHOWN_TO_ALL, SHOWN_TO_OWNERS
    assert set(mine) <= set(SHOWN_TO_ALL + SHOWN_TO_OWNERS) and set(theirs) <= set(SHOWN_TO_ALL)


# ---------- H1: pairing code is keyed, longer, and failures count per address (L5) ----------
@pytest.fixture()
def runner_rig(tmp_path, monkeypatch):
    for name in ("OUTREACH_CLOUD_LI_DIR", "OUTREACH_OFFICE_LI_MAX", "OUTREACH_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    c, owner, store, login_as, client = rig(tmp_path)
    store.update("settings", lambda s: {**s, "public_base_url": "https://outreach.example.com"})
    return c, owner, store, tmp_path / "linkedin-cloud"


def https_client(c, ip):
    return TestClient(c.app, base_url="https://testserver", client=(ip, 1))


def test_pairing_code_is_12_chars_hmac_keyed_and_typeable(runner_rig):
    c, owner, store, _ = runner_rig
    made = c.post("/api/linkedin-cloud/runner/pair", json={}, headers=bearer(owner)).json()
    code = made["code"]
    assert len(code) == 12 and set(code) <= set(runner.CODE_ALPHABET)
    assert made["code_display"] == f"{code[:4]}-{code[4:8]}-{code[8:]}"
    stored = store.read("settings")[runner.PAIR_KEY]["hash"]
    assert stored.startswith("v2:") and hashlib.sha256(code.encode()).hexdigest() not in stored
    assert stored == runner._code_hash(store, code)
    r = https_client(c, "203.0.113.20").post("/api/linkedin-runner/pair", json={"code": made["code_display"].lower(), "hostname": "PC"})
    assert r.status_code == 200, r.text


def test_wrong_guesses_block_the_address_not_the_code(runner_rig):
    c, owner, store, _ = runner_rig
    code = c.post("/api/linkedin-cloud/runner/pair", json={}, headers=bearer(owner)).json()["code"]
    wrong = "".join("A" if ch != "A" else "B" for ch in code)
    stranger = https_client(c, "203.0.113.66")
    for _ in range(runner.MAX_FAILED):
        assert stranger.post("/api/linkedin-runner/pair", json={"code": wrong}).status_code == 400
    assert runner.pending(store) is not None                                           # the code survives
    assert runner.code_valid(store, code, "203.0.113.66") is False                    # this address is done, even with the code
    assert runner.code_valid(store, code, "203.0.113.67") is True
    assert https_client(c, "203.0.113.67").post("/api/linkedin-runner/pair", json={"code": code, "hostname": "PC"}).status_code == 200


def test_slot_tokens_last_90_days_and_renew_near_expiry(runner_rig, monkeypatch):
    c, owner, store, base = runner_rig
    slot = c.post("/api/linkedin-cloud/slots", json={"label": "Sales"}, headers=bearer(owner)).json()["slots"][-1]
    row = next(t for t in store.read("settings")["extension_tokens"] if t["label"] == "cloud:" + slot["id"])
    days = (datetime.fromisoformat(row["expires_at"]) - now_utc()).days
    assert 88 <= days <= 90
    first = json.loads((base / "slots.json").read_text())["slots"][0]["token"]
    assert cloud.renew_due(store, base) is False
    later = now_utc() + timedelta(days=cloud.TOKEN_DAYS - cloud.RENEW_DAYS + 1)
    monkeypatch.setattr(cloud, "now_utc", lambda: later)
    assert cloud.renew_due(store, base) is True
    assert json.loads((base / "slots.json").read_text())["slots"][0]["token"] != first
    monkeypatch.undo()
    old = next(t for t in store.read("settings")["extension_tokens"] if t["label"] == "cloud:" + slot["id"])["id"]
    store.update("settings", lambda s: {**s, "extension_tokens": [{**t, "expires_at": "2036-01-01T00:00:00+00:00"} if t["id"] == old else t
                                                                   for t in s["extension_tokens"]]})
    assert cloud.renew_due(store, base) is True                                       # a pre-fix 10-year token is replaced


def test_mcp_refuses_slot_tokens_and_scopes_to_workspace(runner_rig):
    c, owner, store, base = runner_rig
    c.post("/api/linkedin-cloud/slots", json={"label": "Sales"}, headers=bearer(owner))
    slot_token = json.loads((base / "slots.json").read_text())["slots"][0]["token"]
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_leads", "arguments": {}}}
    r = c.post("/api/mcp", json=body, headers=bearer(slot_token))
    assert r.status_code == 403 and "Acme" not in r.text
    from app import mcp_server, workspaces
    token = workspaces._SCOPE.set(("nowhere", {"nowhere"}))
    try:
        assert mcp_server.call_tool(store, "search_leads", {})["count"] == 0
        with pytest.raises(mcp_server.ToolError):
            mcp_server.call_tool(store, "add_lead_note", {"lead_id": store.read("leads")[0]["id"], "note": "x"})
    finally:
        workspaces._SCOPE.reset(token)


# ---------- M1: backups ----------
def test_secret_backup_and_restore_need_the_super_admin(tmp_path, monkeypatch):
    c, owner, store, login_as, client = rig(tmp_path, monkeypatch, superadmin="boss@example.com")
    other = login_as("own@example.com", "owner")
    assert c.get("/api/backup?include_secrets=1", headers=bearer(other)).status_code == 403
    assert c.get("/api/backup", headers=bearer(other)).status_code == 200
    file = c.get("/api/backup?include_secrets=1", headers=bearer(owner)).content    # the admin password login is super admin
    assert c.post("/api/backup/restore", content=file, headers=bearer(other)).status_code == 403
    assert c.post("/api/backup/restore", content=file, headers=bearer(owner)).json()["backup"]["signed"] is True


def test_restore_never_takes_logins_secret_or_two_step_from_the_file(tmp_path):
    c, owner, store, login_as, _ = rig(tmp_path)
    login_as("cal@example.com", "caller")
    store.update("settings", lambda s: {**s, "require_two_step": False})
    payload = backup.build_backup(store, include_secrets=True)
    payload["data"]["settings"]["users"] = [{"email": "evil@example.com", "role": "owner", "password_hash": "x"}]
    payload["data"]["settings"]["server_secret"] = "attacker"
    payload["data"]["settings"]["require_two_step"] = True
    payload["signature"] = backup.signature(store, payload)                    # even a correctly signed file
    secret, users = store.read("settings")["server_secret"], store.read("settings")["users"]
    result = backup.restore(store, payload)
    after = store.read("settings")
    assert after["server_secret"] == secret and after["users"] == users and after["require_two_step"] is False
    assert set(result["skipped"]) >= {"users", "server_secret", "require_two_step"}
    assert result["logins"]["skipped"] == ["evil@example.com"]


def test_unsigned_or_tampered_secret_backup_restores_without_secrets(tmp_path):
    c, owner, store, *_ = rig(tmp_path)
    store.update("integrations", lambda i: {**i, "sending_domain": {"values": {"api_key": "re_live_KEY"}}})
    store.update("settings", lambda s: {**s, "webhook_secret": "whsec_KEY"})
    payload = backup.build_backup(store, include_secrets=True)
    assert backup.signed_by_this_server(store, payload)
    payload["data"]["settings"]["webhook_secret"] = "whsec_CHANGED"            # tampered: signature no longer matches
    plan = backup.summary(store, payload)
    assert plan["backup"]["includes_secrets"] is False and any("not signed" in n for n in plan["notes"])
    result = backup.restore(store, payload)
    assert result["secrets_used"] is False and "webhook_secret" not in store.read("settings")
    old = backup.build_backup(store, include_secrets=True)
    old.pop("signature")                                                        # an older, unsigned backup
    assert backup.trusted(store, old)[0]["includes_secrets"] is False


# ---------- M2 / M3: writes default to owner; every route has a decision ----------
def test_every_write_route_has_a_reviewed_role_decision(tmp_path):
    app = create_app(data_file=tmp_path / "d.json", admin_password=ADMIN)
    writes = {(m, p) for m, p in _routes(app)}
    assert CALLER_WRITES - writes == set(), "CALLER_WRITES lists routes that no longer exist"
    for method, path in writes:
        role = needed_role(method, path)
        assert role in ("owner", "caller", "viewer"), (method, path)
        if role == "caller":
            assert (method, path) in CALLER_WRITES or path in ("/api/extension/pair",) or path.startswith("/api/extension/tokens"), (method, path)
        if role == "viewer":
            assert (method, path) in ANYONE | OPEN_WRITES, (method, path)
    assert needed_role("POST", "/api/some/new/route") == "owner"


def test_callers_are_refused_owner_writes_but_keep_daily_work(tmp_path):
    c, owner, store, login_as, _ = rig(tmp_path)
    caller = login_as("cal@example.com", "caller")
    lead = c.get("/api/leads", headers=bearer(owner)).json()[0]["id"]
    ws = c.post("/api/workspaces", json={"name": "Two"}, headers=bearer(owner)).json()["id"]
    for method, path, body in (("POST", "/api/campaigns", {"name": "x"}), ("POST", "/api/campaigns/c1/run", {}),
                               ("PUT", "/api/linkedin/settings", {}), ("POST", f"/api/workspaces/{ws}/move", {"lead_ids": [lead]}),
                               ("POST", "/api/enrich/apollo", {"limit": 1}), ("POST", "/api/providers/compare", {}),
                               ("POST", "/api/hygiene/merge", {"keep_id": lead, "merge_ids": []})):
        assert c.request(method, path, json=body, headers=bearer(caller)).status_code == 403, path
    assert c.post(f"/api/leads/{lead}/note", json={"text": "called"}, headers=bearer(caller)).status_code == 200
    assert c.post("/api/tasks", json={"lead_id": lead, "title": "call back"}, headers=bearer(caller)).status_code == 200


def test_role_guard_refuses_a_request_without_a_matched_route():
    from fastapi import HTTPException

    from app.routes_auth import role_guard

    class Req:
        method, scope = "POST", {}

        class state:
            user = {"role": "owner"}
    with pytest.raises(HTTPException) as err:
        role_guard(Req())
    assert err.value.status_code == 403


def test_requirements_are_pinned():
    lines = [l.strip() for l in (ROOT / "requirements.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
    assert lines and all(re.match(r"^[A-Za-z0-9_.\-]+==[0-9][\w.\-]*$", l) for l in lines), lines
    names = {l.split("==")[0].lower() for l in lines}
    assert {"fastapi", "uvicorn", "pydantic", "starlette"} <= names


# ---------- M4: Excel zip bomb ----------
def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_zip_bomb_is_refused_before_openpyxl(monkeypatch):
    bomb = _zip({"[Content_Types].xml": "<x/>", "xl/worksheets/sheet1.xml": b"0" * (20 * 1024 * 1024)})
    assert len(bomb) < 100_000
    monkeypatch.setattr("openpyxl.load_workbook", lambda *a, **k: pytest.fail("openpyxl must not open it"))
    with pytest.raises(excel_import.ExcelError, match="unpacks to far more"):
        excel_import.to_csv(bomb, "leads.xlsx")
    many = _zip({f"f{i}.xml": "x" for i in range(excel_import.ZIP_MAX_MEMBERS + 1)})
    with pytest.raises(excel_import.ExcelError, match="unpacks to far more"):
        excel_import.check_zip(many)


def test_a_normal_xlsx_still_imports():
    import openpyxl
    book = openpyxl.Workbook()
    book.active.append(["company", "email"])
    book.active.append(["Acme", "ravi@acme.com"])
    buf = io.BytesIO()
    book.save(buf)
    assert "Acme" in excel_import.to_csv(buf.getvalue(), "a.xlsx")["csv"]


# ---------- L1: security headers in the Caddy template ----------
def test_caddy_template_sets_security_headers_and_csp_matches_the_inline_script():
    text = (ROOT / "deploy" / "Caddyfile.template").read_text()
    for header in ("Strict-Transport-Security", "X-Content-Type-Options", "Referrer-Policy", "X-Frame-Options"):
        assert header in text
    csp = re.search(r'header Content-Security-Policy "([^"]+)"', text).group(1)
    assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp and "'unsafe-inline'" not in csp.split("style-src")[0]
    script = re.search(r"<script>(.*?)</script>", (ROOT / "web" / "index.html").read_text(), re.S).group(1)
    digest = "sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"'{digest}'" in csp, "update the hash in deploy/Caddyfile.template after changing web/index.html's inline script"
    # the app's own routes (tracking pixel, clicks, unsubscribe, report files) keep their own policy
    assert re.search(r"@api path /api/\* /u/\* /demo/\*", text) and "?X-Frame-Options" in text


# ---------- L2: workspace admins do not manage other admins ----------
def test_workspace_admin_cannot_change_or_remove_another_admin(tmp_path):
    c, owner, store, login_as, _ = rig(tmp_path)
    ws = c.post("/api/workspaces", json={"name": "Client"}, headers=bearer(owner)).json()["id"]
    boss = login_as("boss@client.example", "admin", [ws], ip="198.51.100.21")
    login_as("peer@client.example", "admin", [ws], ip="198.51.100.22")
    for call in (lambda: c.put("/api/users/peer@client.example", json={"password": "another-password-9"}, headers=bearer(boss)),
                 lambda: c.put("/api/users/peer@client.example", json={"role": "caller"}, headers=bearer(boss)),
                 lambda: c.delete("/api/users/peer@client.example", headers=bearer(boss)),
                 lambda: c.post("/api/users", json={"email": "x@client.example", "role": "admin", "password": PW}, headers=bearer(boss))):
        assert call().status_code == 403
    r = c.post("/api/users", json={"email": "agent@client.example", "role": "caller", "password": PW}, headers=bearer(boss))
    assert r.status_code == 200
    assert c.delete("/api/users/agent@client.example", headers=bearer(boss)).status_code == 200


# ---------- L3: limited logins learn nothing about other workspaces ----------
def test_limited_login_import_reports_duplicates_and_owner_list_is_scoped(tmp_path):
    c, owner, store, login_as, _ = rig(tmp_path)
    ws = c.post("/api/workspaces", json={"name": "Client"}, headers=bearer(owner)).json()["id"]
    login_as("someone@main.example", "caller", ip="198.51.100.31", name="Main Person")
    raj = login_as("raj@client.example", "caller", [ws], ip="198.51.100.32", name="Raj")
    login_as("mate@client.example", "caller", [ws], ip="198.51.100.33", name="Mate")
    r = c.post("/api/leads/import", json={"csv": CSV, "source": "upload"}, headers=bearer(raj)).json()
    assert r["duplicates"] == 1 and "elsewhere" not in r
    pre = c.post("/api/import/preview", json={"csv": CSV, "source": "upload"}, headers=bearer(raj)).json()
    assert "another workspace" not in json.dumps(pre)
    owners = c.get("/api/lead-owners", headers=bearer(raj)).json()["owners"]
    assert set(owners) == {"Raj", "Mate"}
    assert "Main Person" in c.get("/api/lead-owners", headers=bearer(owner)).json()["owners"]


# ---------- L6: SMTP probe only talks to public mail servers ----------
def test_smtp_probe_refuses_private_mx_addresses(monkeypatch):
    for private in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1"):
        monkeypatch.setattr(smtp_probe.socket, "getaddrinfo", lambda *a, ip=private, **k: [(2, 1, 6, "", (ip, 25))])
        with pytest.raises(OSError, match="not a public"):
            smtp_probe.mx_address("mx.evil.example")
        with pytest.raises(OSError):
            smtp_probe.smtp_connect("mx.evil.example", "helo.example")
    monkeypatch.setattr(smtp_probe.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("142.250.4.27", 25))])
    assert smtp_probe.mx_address("gmail-smtp-in.l.google.com") == "142.250.4.27"


# ---------- L8: code owned by root on the server ----------
def test_deploy_keeps_code_root_owned_and_data_writable():
    deploy = (ROOT / "deploy" / "deploy.sh").read_text()
    setup = (ROOT / "deploy" / "setup_server.sh").read_text()
    assert "chown -R outreach:outreach /opt/outreach " not in deploy and "chown -R outreach:outreach /opt/outreach\n" not in setup
    assert "chown -R root:root /opt/outreach/app" in deploy and "go-w" in deploy
    assert "chown -R outreach:outreach /var/lib/outreach /var/backups/outreach" in deploy
    unit = (ROOT / "deploy" / "outreach.service").read_text()
    assert "ReadWritePaths=/var/lib/outreach /var/backups/outreach" in unit


def test_templates_are_owner_and_workspace_admin_only(tmp_path):
    from fastapi import HTTPException

    from app.routes_auth import ADMIN_WRITES, role_guard
    c, owner, store, login_as, _ = rig(tmp_path)
    caller = login_as("cal@example.com", "caller")
    for method, path, body in (("POST", "/api/snippets", {"name": "x", "channel": "email", "body": "hi"}),
                               ("PUT", "/api/linkedin/variants", {"A": "a", "B": "b"})):
        assert c.request(method, path, json=body, headers=bearer(caller)).status_code == 403, path
    assert c.post("/api/snippets", json={"name": "x", "channel": "email", "body": "hi"}, headers=bearer(owner)).status_code == 200

    class Route:
        path = "/api/snippets"

    class Req:
        method, scope = "POST", {"route": Route()}

        class state:
            user = {"role": "admin", "workspaces": ["w1"]}
    role_guard(Req())                                   # a workspace admin passes the role check (routes check the workspace)
    Req.state.user = {"role": "caller"}
    with pytest.raises(HTTPException):
        role_guard(Req())
    assert ("PUT", "/api/linkedin/variants") in ADMIN_WRITES
