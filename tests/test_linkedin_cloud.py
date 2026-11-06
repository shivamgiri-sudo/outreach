"""Cloud LinkedIn agent (portal side): slots, slots.json, slot tokens, memory-only login and code jobs, state reports,
the login rate limit and owner-only access. The browser + supervisor runs are in test_linkedin_cloud_e2e.py."""
import json
import logging
import os
import stat

import pytest

from app import linkedin_cloud as cloud, linkedin_runner
from tests.test_extension_api import bearer, make, pair

SECRET = "Sup3r-Secret-Pa55word!"


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.delenv("OUTREACH_CLOUD_LI_DIR", raising=False)
    monkeypatch.delenv("OUTREACH_CLOUD_LI_MAX", raising=False)
    c, owner, acme, globex = make(tmp_path)
    linkedin_runner.set_where(c.app.state.store, "server")          # these tests are about the server supervisor's slots.json
    return c, owner, c.app.state.store, tmp_path / "linkedin-cloud"


def add(c, owner, label="Sales account"):
    r = c.post("/api/linkedin-cloud/slots", json={"label": label}, headers=bearer(owner))
    assert r.status_code == 200, r.text
    return r.json()["slots"][-1]


def slots_file(base):
    return json.loads((base / "slots.json").read_text())


def token_of(base, slot_id):
    return next(r["token"] for r in slots_file(base)["slots"] if r["slot_id"] == slot_id)


def test_adding_a_slot_writes_a_private_slots_file_with_its_own_token(rig):
    c, owner, store, base = rig
    s = add(c, owner)
    assert s["label"] == "Sales account" and s["enabled"] and s["state"] == "starting"
    data = slots_file(base)
    row = data["slots"][0]
    assert row["slot_id"] == s["id"] and row["enabled"] is True and row["token"].startswith("mcnx_")
    assert row["profile_dir"] == str(base / "profiles" / s["id"]) and data["max"] == 1
    assert stat.S_IMODE(os.stat(base / "slots.json").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(base).st_mode) == 0o700
    assert not [p for p in base.iterdir() if p.name.endswith(".tmp")]              # written atomically, no leftovers
    tok = next(t for t in store.read("settings")["extension_tokens"] if t["label"] == f"cloud:{s['id']}")
    assert row["token"] not in json.dumps(store.read("settings"))                  # the store keeps only the hash
    assert tok["expires_at"][:4] >= "2036"                                         # a slot token does not lapse after 90 days
    # it works as an agent token, but is not shown or revocable in the Chrome-assistant token list
    assert c.get("/api/extension/agent", headers=bearer(row["token"])).status_code == 200
    assert all(not t["label"].startswith("cloud:") for t in c.get("/api/extension/tokens", headers=bearer(owner)).json())
    assert c.delete(f"/api/extension/tokens/{tok['id']}", headers=bearer(owner)).status_code == 400
    audit = [r["action"] for r in store.read("audit")]
    assert "linkedin_cloud.add" in audit


def test_the_slot_limit_comes_from_the_environment(rig, monkeypatch):
    c, owner, store, base = rig
    add(c, owner)
    r = c.post("/api/linkedin-cloud/slots", json={"label": "second"}, headers=bearer(owner))
    assert r.status_code == 400 and "at most 1" in r.json()["detail"]
    monkeypatch.setenv("OUTREACH_CLOUD_LI_MAX", "2")
    add(c, owner, "second")
    assert len(slots_file(base)["slots"]) == 2 and slots_file(base)["max"] == 2


def test_removing_a_slot_revokes_its_token_and_deletes_its_profile(rig):
    c, owner, store, base = rig
    s = add(c, owner)
    token = token_of(base, s["id"])
    profile = base / "profiles" / s["id"]
    profile.mkdir(parents=True)
    (profile / "Cookies").write_text("session")
    r = c.delete(f"/api/linkedin-cloud/slots/{s['id']}", headers=bearer(owner))
    assert r.status_code == 200 and r.json()["slots"] == []
    assert slots_file(base)["slots"] == [] and not profile.exists()
    assert c.get("/api/extension/agent", headers=bearer(token)).status_code == 401     # rotated: the old token is dead
    assert not any(t["label"].startswith("cloud:") for t in store.read("settings").get("extension_tokens") or [])
    assert "linkedin_cloud.remove" in [r["action"] for r in store.read("audit")]
    assert c.delete(f"/api/linkedin-cloud/slots/{s['id']}", headers=bearer(owner)).status_code == 404


def test_switching_off_and_logging_out_bump_the_counters_the_supervisor_reads(rig):
    c, owner, store, base = rig
    s = add(c, owner)
    c.put(f"/api/linkedin-cloud/slots/{s['id']}", json={"enabled": False}, headers=bearer(owner))
    row = slots_file(base)["slots"][0]
    assert row["enabled"] is False
    assert c.get("/api/linkedin-cloud", headers=bearer(owner)).json()["slots"][0]["state"] == "stopped"
    c.put(f"/api/linkedin-cloud/slots/{s['id']}", json={"enabled": True}, headers=bearer(owner))
    assert slots_file(base)["slots"][0]["run_seq"] == row["run_seq"] + 1
    r = c.post(f"/api/linkedin-cloud/slots/{s['id']}/logout", headers=bearer(owner))
    assert r.json()["slots"][0]["state"] == "logging_out" and slots_file(base)["slots"][0]["wipe_seq"] == 1
    assert {"linkedin_cloud.off", "linkedin_cloud.on", "linkedin_cloud.logout"} <= {r["action"] for r in store.read("audit")}


def test_the_login_job_lives_only_in_memory_and_is_read_once(rig, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    c, owner, store, base = rig
    s = add(c, owner)
    token = token_of(base, s["id"])
    assert c.get("/api/extension/cloud/login-job", headers=bearer(token)).json() == {"job": None}
    r = c.post(f"/api/linkedin-cloud/slots/{s['id']}/login", json={"email": "rep@example.com", "password": SECRET}, headers=bearer(owner))
    assert r.status_code == 200 and SECRET not in r.text
    slot = r.json()["slots"][0]
    assert slot["state"] == "logging_in" and slot["pending"]["login"] is True
    assert c.get("/api/linkedin-cloud", headers=bearer(owner)).text.count(SECRET) == 0
    job = c.get("/api/extension/cloud/login-job", headers=bearer(token)).json()["job"]
    assert job == {"email": "rep@example.com", "password": SECRET}
    assert c.get("/api/extension/cloud/login-job", headers=bearer(token)).json() == {"job": None}       # one read only
    for f in [tmp for tmp in base.parent.rglob("*") if tmp.is_file()]:
        assert SECRET not in f.read_text(errors="ignore"), f                       # store, backups, slots.json
    assert SECRET not in json.dumps(store.read("settings")) and SECRET not in json.dumps(store.read("audit"))
    assert SECRET not in caplog.text and SECRET not in "".join(capsys.readouterr())
    row = next(r for r in store.read("audit") if r["action"] == "linkedin_cloud.login")
    assert "example.com" in row["detail"] and "rep@" not in row["detail"]


def test_only_the_slot_token_can_take_the_jobs(rig):
    c, owner, store, base = rig
    s = add(c, owner)
    c.post(f"/api/linkedin-cloud/slots/{s['id']}/login", json={"email": "rep@example.com", "password": SECRET}, headers=bearer(owner))
    other = pair(c, owner)["token"]                                                # an ordinary Chrome-assistant token
    assert c.get("/api/extension/cloud/login-job", headers=bearer(other)).status_code == 403
    assert c.get("/api/extension/cloud/login-job", headers=bearer(owner)).status_code == 403    # not even the owner's session
    assert c.post("/api/extension/cloud/state", json={"state": "connected"}, headers=bearer(other)).status_code == 403
    assert c.get("/api/extension/cloud/login-job", headers=bearer(token_of(base, s["id"]))).json()["job"]["password"] == SECRET


def test_jobs_expire_after_ten_minutes():
    now = [1000.0]
    jobs = cloud.CloudJobs(clock=lambda: now[0])
    jobs.put_login("s1", "a@b.co", "pw")
    jobs.put_code("s1", "123456")
    now[0] += cloud.JOB_TTL + 1
    assert jobs.take_login("s1") is None and jobs.take_code("s1") is None
    jobs.put_code("s1", "654321")
    now[0] += 30
    assert jobs.pending("s1") == {"login": False, "code": True}
    assert jobs.take_code("s1") == "654321" and jobs.take_code("s1") is None


def test_login_input_is_validated_without_echoing_the_password(rig):
    c, owner, store, base = rig
    s = add(c, owner)
    url = f"/api/linkedin-cloud/slots/{s['id']}/login"
    for body in ({"email": "not-an-email", "password": SECRET}, {"email": "rep@example.com", "password": ""},
                 {"email": "rep@example.com", "password": SECRET * 20}, {"email": "rep@example.com", "password": 12345}):
        r = c.post(url, json=body, headers=bearer(owner))
        assert r.status_code == 400 and SECRET not in r.text and "12345" not in r.text, r.text
    assert c.get(url, headers=bearer(owner)).status_code == 405                    # POST only


def test_login_attempts_are_rate_limited_per_slot(rig, monkeypatch):
    c, owner, store, base = rig
    monkeypatch.setenv("OUTREACH_CLOUD_LI_MAX", "2")
    s, t = add(c, owner), add(c, owner, "other")
    url = f"/api/linkedin-cloud/slots/{s['id']}/login"
    for _ in range(cloud.LOGIN_ATTEMPTS_PER_HOUR):
        assert c.post(url, json={"email": "rep@example.com", "password": SECRET}, headers=bearer(owner)).status_code == 200
    r = c.post(url, json={"email": "rep@example.com", "password": SECRET}, headers=bearer(owner))
    assert r.status_code == 429 and SECRET not in r.text
    assert c.post(f"/api/linkedin-cloud/slots/{t['id']}/login", json={"email": "rep@example.com", "password": SECRET},
                  headers=bearer(owner)).status_code == 200


def test_the_code_job_and_state_reports(rig):
    c, owner, store, base = rig
    s = add(c, owner)
    token = token_of(base, s["id"])
    r = c.post("/api/extension/cloud/state", json={"state": "needs_code", "note": "LinkedIn sent a code"}, headers=bearer(token))
    assert r.status_code == 200
    assert c.get("/api/linkedin-cloud", headers=bearer(owner)).json()["slots"][0]["state"] == "needs_code"
    for bad in ("12", "123456789", "12ab56"):
        assert c.post(f"/api/linkedin-cloud/slots/{s['id']}/code", json={"code": bad}, headers=bearer(owner)).status_code == 400
    assert c.post(f"/api/linkedin-cloud/slots/{s['id']}/code", json={"code": "246 810"}, headers=bearer(owner)).status_code == 200
    assert c.get("/api/extension/cloud/code", headers=bearer(token)).json() == {"code": "246810"}
    assert c.get("/api/extension/cloud/code", headers=bearer(token)).json() == {"code": None}
    assert "246810" not in json.dumps(store.read("settings")) + json.dumps(store.read("audit"))
    c.post("/api/extension/cloud/state", json={"state": "connected", "account": "Rep-One", "name": "Rep One"}, headers=bearer(token))
    slot = c.get("/api/linkedin-cloud", headers=bearer(owner)).json()["slots"][0]
    assert slot["state"] == "connected" and slot["account"] == "rep-one" and slot["name"] == "Rep One" and slot["online"]
    assert c.post("/api/extension/cloud/state", json={"state": "hacked"}, headers=bearer(token)).status_code == 400
    # a fatal pause of that account shows as paused
    c.post("/api/extension/agent/event", json={"kind": "error", "fatal": True, "note": "checkpoint"}, headers={**bearer(token), "X-LinkedIn-Account": "rep-one"})
    assert c.get("/api/linkedin-cloud", headers=bearer(owner)).json()["slots"][0]["state"] == "paused"


def test_cloud_routes_are_owner_only(rig):
    c, owner, store, base = rig
    s = add(c, owner)
    r = c.post("/api/users", json={"email": "caller@example.com", "role": "caller", "password": "Caller-pass-12345"}, headers=bearer(owner))
    assert r.status_code == 200, r.text
    caller = c.post("/api/login", json={"email": "caller@example.com", "password": "Caller-pass-12345"}).json()["token"]
    for method, path, body in (("GET", "/api/linkedin-cloud", None), ("POST", "/api/linkedin-cloud/slots", {"label": "x"}),
                               ("POST", f"/api/linkedin-cloud/slots/{s['id']}/login", {"email": "a@b.co", "password": "x"}),
                               ("POST", f"/api/linkedin-cloud/slots/{s['id']}/code", {"code": "123456"}),
                               ("POST", f"/api/linkedin-cloud/slots/{s['id']}/logout", None),
                               ("DELETE", f"/api/linkedin-cloud/slots/{s['id']}", None)):
        assert c.request(method, path, json=body, headers=bearer(caller)).status_code == 403, path
    token = token_of(base, s["id"])
    assert c.get("/api/linkedin-cloud", headers=bearer(token)).status_code == 403              # the agent token cannot manage slots
    assert c.post(f"/api/linkedin-cloud/slots/{s['id']}/login", json={"email": "a@b.co", "password": "x"}, headers=bearer(token)).status_code == 403


def test_a_lost_slots_file_or_revoked_token_is_repaired_at_start(rig):
    c, owner, store, base = rig
    s = add(c, owner)
    old = token_of(base, s["id"])
    (base / "slots.json").unlink()
    from app.routes_linkedin_cloud import make_router
    make_router(store, base.parent / "d.json")
    new = token_of(base, s["id"])
    assert new != old and c.get("/api/extension/agent", headers=bearer(new)).status_code == 200
    assert c.get("/api/extension/agent", headers=bearer(old)).status_code == 401
