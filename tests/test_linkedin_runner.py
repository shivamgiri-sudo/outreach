"""Office runner (portal side): where LinkedIn runs, pairing codes (TTL, single use, guess limit, rate limit), the runner
token (hashed, scoped, revocable), the slots it gets per mode, one runner at a time, heartbeat / online, the server
supervisor idling in office mode, and the installer + bundle endpoints. The real runner process is in
test_linkedin_runner_e2e.py."""
import io
import json
import tarfile
import zipfile
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import linkedin_cloud as cloud, linkedin_runner as runner
from app.activities import now_utc
from tests.test_extension_api import bearer, make, pair as pair_extension

PORTAL = "https://outreach.example.com"


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    for name in ("OUTREACH_CLOUD_LI_DIR", "OUTREACH_CLOUD_LI_MAX", "OUTREACH_OFFICE_LI_MAX", "OUTREACH_RUNNER_ALLOW_HTTP", "OUTREACH_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    c, owner, _, _ = make(tmp_path)
    store = c.app.state.store
    store.update("settings", lambda s: {**s, "public_base_url": PORTAL})
    https = TestClient(c.app, base_url="https://testserver", client=("203.0.113.9", 50000))     # the office computer, over HTTPS
    return c, https, owner, store, tmp_path / "linkedin-cloud"


def code_for(c, owner, replace=False):
    r = c.post("/api/linkedin-cloud/runner/pair", json={"replace": replace}, headers=bearer(owner))
    assert r.status_code == 200, r.text
    return r.json()


def pair(https, code, host="OFFICE-PC"):
    return https.post("/api/linkedin-runner/pair", json={"code": code, "hostname": host, "os": "Windows_NT 10.0 (win32/x64)", "version": "1.0.0"})


def connect(c, https, owner, replace=False, host="OFFICE-PC"):
    made = code_for(c, owner, replace)
    r = pair(https, made["code"], host)
    assert r.status_code == 200, r.text
    return r.json()


def add_slot(c, owner, label="Sales"):
    r = c.post("/api/linkedin-cloud/slots", json={"label": label}, headers=bearer(owner))
    assert r.status_code == 200, r.text
    return r.json()["slots"][-1]


def slots_file(base):
    return json.loads((base / "slots.json").read_text())


def travel(monkeypatch, seconds):
    later = now_utc() + timedelta(seconds=seconds)
    monkeypatch.setattr(runner, "now_utc", lambda: later)


# ---------- where ----------
def test_office_is_the_default_and_the_server_supervisor_idles(rig):
    c, https, owner, store, base = rig
    view = c.get("/api/linkedin-cloud", headers=bearer(owner)).json()
    assert view["where"] == "office" and view["runner"] is None and view["max"] == 3
    s = add_slot(c, owner)
    assert s["note"].startswith("waiting for the office computer")
    data = slots_file(base)
    assert data["where"] == "office" and data["slots"][0]["enabled"] is False and data["slots"][0]["token"].startswith("mcnx_")
    r = c.put("/api/linkedin-cloud/where", json={"where": "server"}, headers=bearer(owner))
    assert r.status_code == 200 and r.json()["where"] == "server" and r.json()["max"] == 1
    assert slots_file(base)["slots"][0]["enabled"] is True                         # the supervisor runs it now
    c.put("/api/linkedin-cloud/where", json={"where": "office"}, headers=bearer(owner))
    assert slots_file(base)["slots"][0]["enabled"] is False
    assert c.put("/api/linkedin-cloud/where", json={"where": "moon"}, headers=bearer(owner)).status_code == 400
    assert [r["detail"] for r in store.read("audit") if r["action"] == "linkedin_cloud.where"] == [
        "LinkedIn agents now run on the server", "LinkedIn agents now run on the office computer"]


# ---------- pairing codes ----------
def test_a_pairing_code_is_hashed_single_use_and_gives_a_hashed_runner_token(rig):
    c, https, owner, store, base = rig
    made = code_for(c, owner)
    code = made["code"]
    assert len(code) == 12 and set(code) <= set(runner.CODE_ALPHABET)
    assert code not in json.dumps(store.read("settings"))                          # only its hash is kept
    assert made["pairing"]["expires_at"] == made["expires_at"]
    r = pair(https, code.lower()[:4] + "-" + code.lower()[4:])                     # typed loosely: still the same code
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert token.startswith("mcnr_") and token not in json.dumps(store.read("settings"))
    assert pair(https, code).status_code == 400                                     # used up
    view = c.get("/api/linkedin-cloud", headers=bearer(owner)).json()
    assert view["pairing"] is None and view["runner"]["hostname"] == "OFFICE-PC" and view["runner"]["online"]
    assert token not in json.dumps(view) and "hash" not in view["runner"]
    actions = [a["action"] for a in store.read("audit")]
    assert "linkedin_runner.code" in actions and "linkedin_runner.pair" in actions


def test_a_pairing_code_expires_after_fifteen_minutes(rig, monkeypatch):
    c, https, owner, store, base = rig
    code = code_for(c, owner)["code"]
    travel(monkeypatch, runner.CODE_TTL + 1)
    assert pair(https, code).status_code == 400
    assert "expired" in https.get(f"/api/linkedin-runner/install.sh?code={code}").text


def test_wrong_guesses_lock_out_the_address_but_keep_the_code(rig):
    c, https, owner, store, base = rig
    code = code_for(c, owner)["code"]
    wrong = "".join("A" if ch != "A" else "B" for ch in code)
    for _ in range(runner.MAX_FAILED - 1):
        assert pair(https, wrong).status_code == 400
    assert store.read("settings")[runner.PAIR_KEY]["failures"] == runner.MAX_FAILED - 1
    assert https.get(f"/api/linkedin-runner/install.sh?code={wrong}").text.startswith("#!/usr/bin/env bash\necho")  # one more guess
    assert runner.PAIR_KEY in store.read("settings")                                # a stranger cannot cancel the owner's code
    assert pair(https, code).status_code == 400                                     # but this address may not try again
    other = TestClient(c.app, base_url="https://testserver", client=("203.0.113.77", 1))
    assert pair(other, code).status_code == 200


def test_pairing_is_rate_limited_per_address(rig):
    c, https, owner, store, base = rig
    statuses = [pair(https, "ZZZZZZZZ").status_code for _ in range(11)]
    assert statuses[:10] == [400] * 10 and statuses[10] == 429


def test_pairing_and_slots_need_https_unless_local(rig, monkeypatch):
    c, https, owner, store, base = rig
    code = code_for(c, owner)["code"]
    r = c.post("/api/linkedin-runner/pair", json={"code": code})                   # plain http from the internet
    assert r.status_code == 403 and "HTTPS" in r.text
    token = pair(https, code).json()["token"]
    assert c.get("/api/linkedin-runner/slots", headers=bearer(token)).status_code == 403
    assert https.get("/api/linkedin-runner/slots", headers=bearer(token)).status_code == 200
    behind_proxy = TestClient(c.app, client=("127.0.0.1", 40000))
    assert behind_proxy.get("/api/linkedin-runner/slots", headers={**bearer(token), "X-Forwarded-Proto": "https",
                                                                    "X-Forwarded-For": "198.51.100.7"}).status_code == 200
    monkeypatch.setenv("OUTREACH_RUNNER_ALLOW_HTTP", "1")
    assert c.get("/api/linkedin-runner/slots", headers=bearer(token)).status_code == 200


# ---------- the runner token ----------
def test_the_runner_token_only_opens_the_runner_routes_and_dies_when_revoked(rig):
    c, https, owner, store, base = rig
    s = add_slot(c, owner)
    paired = connect(c, https, owner)
    token = paired["token"]
    for method, path in (("GET", "/api/leads"), ("GET", "/api/linkedin-cloud"), ("GET", "/api/extension/agent"),
                         ("POST", "/api/linkedin-cloud/runner/pair"), ("GET", "/api/settings"), ("POST", "/api/mcp")):
        assert https.request(method, path, headers=bearer(token)).status_code == 403, path
    assert https.post("/api/linkedin-runner/heartbeat", json={"hostname": "OFFICE-PC"}, headers=bearer(token)).status_code == 200
    # other credentials cannot use the runner routes
    assert https.get("/api/linkedin-runner/slots", headers=bearer(owner)).status_code == 403
    slot_token = slots_file(base)["slots"][0]["token"]
    assert https.get("/api/linkedin-runner/slots", headers=bearer(slot_token)).status_code == 403
    assert https.get("/api/linkedin-runner/slots", headers=bearer(pair_extension(c, owner)["token"])).status_code == 403
    assert https.get("/api/linkedin-runner/slots", headers=bearer("mcnr_made-up")).status_code == 401
    # revoke: the runner token and the slot tokens stop working at once
    r = c.delete(f"/api/linkedin-cloud/runner/{paired['runner_id']}", headers=bearer(owner))
    assert r.status_code == 200 and r.json()["runner"] is None
    assert https.get("/api/linkedin-runner/slots", headers=bearer(token)).status_code == 401
    assert c.get("/api/extension/agent", headers=bearer(slot_token)).status_code == 401
    new_slot_token = slots_file(base)["slots"][0]["token"]
    assert new_slot_token != slot_token and c.get("/api/extension/agent", headers=bearer(new_slot_token)).status_code == 200
    row = next(a for a in store.read("audit") if a["action"] == "linkedin_runner.revoke")
    assert paired["runner_id"] in row["detail"] and token not in json.dumps(store.read("audit"))
    assert c.delete(f"/api/linkedin-cloud/runner/{paired['runner_id']}", headers=bearer(owner)).status_code == 404
    assert s["id"] in json.dumps(slots_file(base))


def test_runner_management_is_owner_only(rig):
    c, https, owner, store, base = rig
    paired = connect(c, https, owner)
    r = c.post("/api/users", json={"email": "caller@example.com", "role": "caller", "password": "Caller-pass-12345"}, headers=bearer(owner))
    assert r.status_code == 200, r.text
    caller = c.post("/api/login", json={"email": "caller@example.com", "password": "Caller-pass-12345"}).json()["token"]
    for method, path, body in (("POST", "/api/linkedin-cloud/runner/pair", {"replace": True}), ("DELETE", "/api/linkedin-cloud/runner/pair", None),
                               ("PUT", f"/api/linkedin-cloud/runner/{paired['runner_id']}", {"label": "x"}),
                               ("DELETE", f"/api/linkedin-cloud/runner/{paired['runner_id']}", None),
                               ("PUT", "/api/linkedin-cloud/where", {"where": "server"})):
        assert c.request(method, path, json=body, headers=bearer(caller)).status_code == 403, path


# ---------- what the runner runs ----------
def test_slots_carry_tokens_only_for_enabled_accounts_in_office_mode(rig, monkeypatch):
    c, https, owner, store, base = rig
    monkeypatch.setenv("OUTREACH_OFFICE_LI_MAX", "2")
    on, off = add_slot(c, owner, "On"), add_slot(c, owner, "Off")
    c.put(f"/api/linkedin-cloud/slots/{off['id']}", json={"enabled": False}, headers=bearer(owner))
    c.post(f"/api/linkedin-cloud/slots/{on['id']}/logout", headers=bearer(owner))
    token = connect(c, https, owner)["token"]
    data = https.get("/api/linkedin-runner/slots", headers=bearer(token)).json()
    assert data["where"] == "office" and data["max"] == 2 and data["poll_seconds"] == 15
    rows = {r["slot_id"]: r for r in data["slots"]}
    on_file = {r["slot_id"]: r["token"] for r in slots_file(base)["slots"]}
    assert rows[on["id"]]["enabled"] is True and rows[on["id"]]["token"] == on_file[on["id"]]          # the same secret the supervisor reads
    assert rows[on["id"]]["wipe_seq"] == 1 and rows[on["id"]]["run_seq"] >= 1
    assert rows[off["id"]] == {"slot_id": off["id"], "label": "Off", "enabled": False, "token": "", "run_seq": 0, "wipe_seq": 0, "restart_seq": 0}
    # server mode: the office computer gets the accounts switched off and no tokens (it stops without deleting sessions)
    c.put("/api/linkedin-cloud/where", json={"where": "server"}, headers=bearer(owner))
    data = https.get("/api/linkedin-runner/slots", headers=bearer(token)).json()
    assert data["where"] == "server" and len(data["slots"]) == 2 and all(not r["enabled"] and not r["token"] for r in data["slots"])
    # a lost slots.json is repaired when the runner asks
    c.put("/api/linkedin-cloud/where", json={"where": "office"}, headers=bearer(owner))
    (base / "slots.json").unlink()
    data = https.get("/api/linkedin-runner/slots", headers=bearer(token)).json()
    fresh = next(r for r in data["slots"] if r["slot_id"] == on["id"])["token"]
    assert fresh.startswith("mcnx_") and c.get("/api/extension/agent", headers=bearer(fresh)).status_code == 200


# ---------- one runner at a time ----------
def test_only_one_runner_a_new_pairing_replaces_the_old_after_confirmation(rig):
    c, https, owner, store, base = rig
    add_slot(c, owner)
    first = connect(c, https, owner, host="PC-ONE")
    r = c.post("/api/linkedin-cloud/runner/pair", json={}, headers=bearer(owner))
    assert r.status_code == 409 and "PC-ONE" in r.json()["detail"]
    old_slot_token = slots_file(base)["slots"][0]["token"]
    made = code_for(c, owner, replace=True)
    assert made["pairing"]["replace"] is True
    assert https.get("/api/linkedin-runner/slots", headers=bearer(first["token"])).status_code == 200    # still works until the new one pairs
    second = pair(https, made["code"], "PC-TWO").json()
    assert https.get("/api/linkedin-runner/slots", headers=bearer(first["token"])).status_code == 401
    assert https.get("/api/linkedin-runner/slots", headers=bearer(second["token"])).status_code == 200
    assert c.get("/api/extension/agent", headers=bearer(old_slot_token)).status_code == 401                 # old computer's agents stop
    active = [r for r in store.read("settings")[runner.SETTINGS_KEY] if r["status"] == "active"]
    assert [r["hostname"] for r in active] == ["PC-TWO"]
    assert all("hash" not in r for r in store.read("settings")[runner.SETTINGS_KEY] if r["status"] == "revoked")
    actions = [a["action"] for a in store.read("audit")]
    assert actions.count("linkedin_runner.pair") == 2 and actions.count("linkedin_runner.replace") == 1


def test_rename_and_cancel(rig):
    c, https, owner, store, base = rig
    paired = connect(c, https, owner)
    r = c.put(f"/api/linkedin-cloud/runner/{paired['runner_id']}", json={"label": "  Reception   PC "}, headers=bearer(owner))
    assert r.json()["runner"]["label"] == "Reception PC"
    assert c.put(f"/api/linkedin-cloud/runner/{paired['runner_id']}", json={"label": " "}, headers=bearer(owner)).status_code == 400
    assert c.put("/api/linkedin-cloud/runner/nope", json={"label": "x"}, headers=bearer(owner)).status_code == 404
    code = code_for(c, owner, replace=True)["code"]
    assert c.delete("/api/linkedin-cloud/runner/pair", headers=bearer(owner)).json()["pairing"] is None
    assert pair(https, code).status_code == 400


# ---------- heartbeat / online ----------
def test_heartbeat_and_online_status(rig, monkeypatch):
    c, https, owner, store, base = rig
    s = add_slot(c, owner)
    token = connect(c, https, owner)["token"]
    r = https.post("/api/linkedin-runner/heartbeat", headers=bearer(token), json={
        "hostname": "OFFICE-PC", "os": "Darwin 23.1.0 (darwin/arm64)", "version": "1.0.0 node 20.18.1",
        "agents": [{"slot_id": s["id"], "pid": 4242, "state": "running"}, {"slot_id": "zzzz9", "pid": None, "state": "evil<script>"}]})
    assert r.status_code == 200 and r.json() == {"ok": True, "where": "office"}
    view = c.get("/api/linkedin-cloud", headers=bearer(owner)).json()
    assert view["runner"]["online"] and view["runner"]["os"].startswith("Darwin") and view["runner"]["version"].startswith("1.0.0")
    assert view["slots"][0]["runner"] == "running"
    assert {a["state"] for a in view["runner"]["agents"]} == {"running", "unknown"}
    travel(monkeypatch, runner.ONLINE_SECONDS + 1)
    view = c.get("/api/linkedin-cloud", headers=bearer(owner)).json()
    assert view["runner"]["online"] is False and view["runner"]["last_seen"] and view["slots"][0]["runner"] is None
    assert https.post("/api/linkedin-runner/heartbeat", json={"agents": "x"}, headers=bearer(token)).status_code == 422


# ---------- installers and bundle ----------
def test_installers_embed_the_portal_address_and_the_code_and_nothing_else_secret(rig):
    c, https, owner, store, base = rig
    add_slot(c, owner)
    made = code_for(c, owner)
    code = made["code"]
    assert made["portal"] == PORTAL and made["insecure"] is False
    assert made["commands"]["windows"] == (f"powershell -NoProfile -ExecutionPolicy Bypass -Command \"[Net.ServicePointManager]::SecurityProtocol="
                                           f"[Net.ServicePointManager]::SecurityProtocol -bor 3072; irm '{PORTAL}/api/linkedin-runner/install.ps1"
                                           f"?code={code}' | iex\"")
    assert made["commands"]["mac"] == made["commands"]["linux"] == f"curl -fsSL '{PORTAL}/api/linkedin-runner/install.sh?code={code}' | bash"
    for kind in ("ps1", "sh"):
        r = c.get(f"/api/linkedin-runner/install.{kind}?code={code}")                # no login: it is pasted on the office computer
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        text = r.text
        assert f"$Portal = '{PORTAL}'" in text if kind == "ps1" else f"PORTAL='{PORTAL}'" in text
        assert code in text and "__PORTAL__" not in text and "__CODE__" not in text
        assert "mcnx_" not in text and "mcnr_" not in text and "--with-deps" not in text
    assert c.get(f"/api/linkedin-runner/install.sh?code={code}").status_code == 200   # looking does not use the code up
    assert pair(https, code).status_code == 200
    assert "expired" in c.get(f"/api/linkedin-runner/install.ps1?code={code}").text   # after pairing it is gone


def test_an_unusable_public_address_is_refused_before_a_code_is_made(rig):
    c, https, owner, store, base = rig
    store.update("settings", lambda s: {**s, "public_base_url": "https://x.example.com/'; rm -rf ~ #"})
    r = c.post("/api/linkedin-cloud/runner/pair", json={}, headers=bearer(owner))
    assert r.status_code == 400 and runner.PAIR_KEY not in store.read("settings")


def test_the_bundle_holds_only_the_runner_files_and_needs_the_code_or_the_token(rig):
    c, https, owner, store, base = rig
    want = {"tools/linkedin_agent.mjs", "tools/linkedin_runner.mjs", "tools/linkedin_cloud_login.mjs", "tools/linkedin_guard_dom.mjs", "tools/linkedin_agent_inbox.mjs", "tools/linkedin_agent_pool.mjs",
            "package.json", "package-lock.json"}
    assert c.get("/api/linkedin-runner/bundle.tar.gz").status_code == 403
    code = code_for(c, owner)["code"]
    r = c.get(f"/api/linkedin-runner/bundle.tar.gz?code={code}")
    assert r.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(r.content)) as t:
        assert set(t.getnames()) == want and all(m.isfile() for m in t.getmembers())
        assert b"createPool" in t.extractfile("tools/linkedin_agent_pool.mjs").read()
    with zipfile.ZipFile(io.BytesIO(c.get(f"/api/linkedin-runner/bundle.zip?code={code}").content)) as z:
        assert set(z.namelist()) == want
    token = pair(https, code).json()["token"]
    assert c.get(f"/api/linkedin-runner/bundle.zip?code={code}").status_code == 403
    assert c.get("/api/linkedin-runner/bundle.zip", headers=bearer(token)).status_code == 200


def test_runner_slots_and_supervisor_file_agree_after_where_changes(rig):
    c, https, owner, store, base = rig
    s = add_slot(c, owner)
    assert cloud.runner_slots(store, base)[0]["enabled"] is True
    runner.set_where(store, "server")
    assert cloud.runner_slots(store, base)[0]["enabled"] is False
    cloud.write_slots_file(store, base, {})
    assert slots_file(base)["slots"][0]["enabled"] is True and slots_file(base)["slots"][0]["slot_id"] == s["id"]
