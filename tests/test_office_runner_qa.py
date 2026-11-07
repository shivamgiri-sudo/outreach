"""Independent QA of the office runner (feat/office-runner): tries to break pairing, the runner token's scope, the
runner's resilience (portal outage, agent crash, closed browser, killed runner, second copy), multi-account isolation,
office/server switches and the public installer paths. Regression tests for the bugs found are marked "regression".
The LinkedIn stand-in is always passed explicitly (--linkedin-base / MCN_LINKEDIN_BASE): nothing here opens linkedin.com,
and every test that runs a browser fails if the output mentions a linkedin.com request."""
import json
import os
import re
import signal
import subprocess
import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import linkedin_cloud as cloud, linkedin_runner as runner
from app.activities import now_utc
from app.routes_linkedin_runner import PUBLIC_PATHS
from tests.test_extension_api import bearer
from tests.test_linkedin_cloud_e2e import ROOT, SECRET, cloud_live, needs_browser, needs_node, wait_for  # noqa: F401  (fixture)
from tests.test_linkedin_runner import add_slot, code_for, connect, pair, rig, slots_file  # noqa: F401  (fixture)

RUNNER = str(ROOT / "tools" / "linkedin_runner.mjs")
FAKE_AGENT = str(ROOT / "tests" / "agent_sim" / "fake_cloud_agent.mjs")


# =====================================================================================================================
# 1. Pairing
# =====================================================================================================================
def test_reused_expired_and_wrong_codes_are_refused_and_ten_wrong_guesses_cancel(rig, monkeypatch):
    c, https, owner, store, base = rig
    code = code_for(c, owner)["code"]
    assert pair(https, code).status_code == 200
    assert pair(https, code).status_code == 400                                         # reused
    # expired: one second past 15 minutes, also through the installer and the bundle
    code = code_for(c, owner, replace=True)["code"]
    later = now_utc() + timedelta(seconds=runner.CODE_TTL + 1)
    monkeypatch.setattr(runner, "now_utc", lambda: later)
    assert pair(https, code).status_code == 400
    assert https.get(f"/api/linkedin-runner/bundle.zip?code={code}").status_code == 403
    monkeypatch.undo()
    # ten wrong guesses spread over the three public paths cancel the code
    code = code_for(c, owner, replace=True)["code"]
    wrong = "".join("A" if ch != "A" else "B" for ch in code)
    fresh = TestClient(c.app, base_url="https://testserver", client=("203.0.113.50", 1))     # its own rate-limit bucket
    for i in range(runner.MAX_FAILED):
        if i % 3 == 0:
            assert fresh.post("/api/linkedin-runner/pair", json={"code": wrong}).status_code == 400
        elif i % 3 == 1:
            assert fresh.get(f"/api/linkedin-runner/bundle.tar.gz?code={wrong}").status_code == 403
        else:
            assert "exit 1" in fresh.get(f"/api/linkedin-runner/install.sh?code={wrong}").text
    assert runner.pending(store) is None
    assert fresh.post("/api/linkedin-runner/pair", json={"code": code}).status_code == 400


def test_a_code_from_another_owner_session_replaces_the_earlier_one(rig):
    c, https, owner, store, base = rig
    from tests.test_extension_api import ADMIN
    other = c.post("/api/login", json={"email": "", "password": ADMIN}).json()["token"]
    assert other != owner
    first = code_for(c, owner)["code"]
    second = code_for(c, other)["code"]
    assert first != second
    assert pair(https, first).status_code == 400                                        # only the newest code works
    assert pair(https, second).status_code == 200


def test_pairing_over_plain_http_from_a_non_local_address_is_refused_even_with_forged_headers(rig):
    c, https, owner, store, base = rig
    code = code_for(c, owner)["code"]
    remote = TestClient(c.app, base_url="http://localhost", client=("198.51.100.20", 4000))
    for headers in ({}, {"X-Forwarded-Proto": "https"}, {"Host": "localhost"}, {"X-Forwarded-For": "127.0.0.1", "X-Forwarded-Proto": "https"}):
        r = remote.post("/api/linkedin-runner/pair", json={"code": code}, headers=headers)
        assert r.status_code == 403 and "HTTPS" in r.text, headers
    # a local proxy forwarding plain http is refused too
    proxy = TestClient(c.app, client=("127.0.0.1", 4000))
    r = proxy.post("/api/linkedin-runner/pair", json={"code": code}, headers={"X-Forwarded-For": "198.51.100.20", "X-Forwarded-Proto": "http"})
    assert r.status_code == 403
    assert pair(https, code).status_code == 200                                         # refusals did not use the code up


def test_replace_needs_confirmation_and_revokes_the_old_runner_and_every_slot_token(rig, monkeypatch):
    c, https, owner, store, base = rig
    monkeypatch.setenv("OUTREACH_OFFICE_LI_MAX", "3")
    a, b = add_slot(c, owner, "A"), add_slot(c, owner, "B")
    first = connect(c, https, owner, host="PC-ONE")
    old = {r["slot_id"]: r["token"] for r in https.get("/api/linkedin-runner/slots", headers=bearer(first["token"])).json()["slots"]}
    assert all(old.values())
    assert c.post("/api/linkedin-cloud/runner/pair", json={}, headers=bearer(owner)).status_code == 409
    assert c.post("/api/linkedin-cloud/runner/pair", json={"replace": "yes please"}, headers=bearer(owner)).status_code in (409, 422)
    assert runner.pending(store) is None                                                # no code was made without confirmation
    second = connect(c, https, owner, replace=True, host="PC-TWO")
    assert https.get("/api/linkedin-runner/slots", headers=bearer(first["token"])).status_code == 401
    assert https.post("/api/linkedin-runner/heartbeat", json={}, headers=bearer(first["token"])).status_code == 401
    for token in old.values():
        assert c.get("/api/extension/agent", headers=bearer(token)).status_code == 401
    new = {r["slot_id"]: r["token"] for r in https.get("/api/linkedin-runner/slots", headers=bearer(second["token"])).json()["slots"]}
    assert set(new) == {a["id"], b["id"]} and not set(new.values()) & set(old.values())


# =====================================================================================================================
# 2. Runner token scope
# =====================================================================================================================
def _all_routes(routes, prefix=""):
    """Every APIRoute, also inside included routers (newer FastAPI keeps them as _IncludedRouter)."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route.methods
        elif hasattr(route, "original_router"):
            yield from _all_routes(route.original_router.routes, prefix + (route.include_context.prefix or ""))
        elif hasattr(route, "routes"):
            yield from _all_routes(route.routes, prefix + getattr(route, "path", ""))


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x1", path)


def test_the_runner_token_opens_no_route_except_its_own(rig):
    c, https, owner, store, base = rig
    add_slot(c, owner)
    token = connect(c, https, owner)["token"]
    from app.main import PUBLIC_API_PATHS, PUBLIC_GET_PATHS
    allowed = {("GET", "/api/linkedin-runner/slots"), ("POST", "/api/linkedin-runner/heartbeat"),
               ("GET", "/api/linkedin-runner/bundle.tar.gz"), ("GET", "/api/linkedin-runner/bundle.zip")}
    checked = 0
    for raw_path, methods in _all_routes(c.app.routes):
        if not raw_path.startswith("/api/"):
            continue
        path = _concrete(raw_path)
        if path in PUBLIC_API_PATHS or path in PUBLIC_GET_PATHS or path in PUBLIC_PATHS or path.startswith(("/api/webhooks/", "/api/oauth/", "/api/inbound/", "/api/reports/file/", "/api/track/")) \
                or path in ("/api/password/forgot", "/api/password/reset"):     # public by design: the token / generic answer is the gate
            continue
        for method in methods - {"HEAD", "OPTIONS"}:
            if (method, path) in allowed:
                continue
            r = https.request(method, path, headers=bearer(token), json={})
            assert r.status_code in (401, 403), (method, path, r.status_code)
            assert token not in r.text
            checked += 1
    assert checked > 100
    # the extension routes, leads, settings, users and export by name
    for method, path in (("GET", "/api/extension/agent"), ("POST", "/api/extension/event"), ("GET", "/api/extension/cloud/login-job"),
                         ("GET", "/api/leads"), ("GET", "/api/settings"), ("GET", "/api/users"), ("GET", "/api/export/leads.csv")):
        assert https.request(method, path, headers=bearer(token)).status_code in (401, 403), path


def test_after_revoke_the_runner_reads_no_slot_secret_and_tokens_are_hashed_at_rest(rig, tmp_path):
    c, https, owner, store, base = rig
    s = add_slot(c, owner)
    paired = connect(c, https, owner)
    token = paired["token"]
    slot_token = https.get("/api/linkedin-runner/slots", headers=bearer(token)).json()["slots"][0]["token"]
    https.post("/api/linkedin-runner/heartbeat", json={"hostname": "PC"}, headers=bearer(token))
    data_file = tmp_path / "d.json"
    text = data_file.read_text()
    assert token not in text and token[len(runner.TOKEN_PREFIX):] not in text
    assert slot_token not in text                                                       # slot tokens: only slots.json (0600)
    assert runner._hash(token) in text                                                  # the hash, not the token
    assert oct(os.stat(base / "slots.json").st_mode)[-3:] == "600"
    c.delete(f"/api/linkedin-cloud/runner/{paired['runner_id']}", headers=bearer(owner))
    for path in ("/api/linkedin-runner/slots", "/api/linkedin-runner/bundle.zip"):
        r = https.get(path, headers=bearer(token))
        assert r.status_code in (401, 403) and "mcnx_" not in r.text
    assert runner._hash(token) not in data_file.read_text()                             # the hash is dropped on revoke
    assert s["id"]


# =====================================================================================================================
# 7. Public paths
# =====================================================================================================================
def test_public_paths_refuse_without_a_code_never_leak_and_are_rate_limited(rig):
    c, https, owner, store, base = rig
    add_slot(c, owner)
    connect(c, https, owner)
    code_for(c, owner, replace=True)                                                    # a pending code exists
    view = json.dumps(store.read("settings"))
    for client_ip, path in enumerate(PUBLIC_PATHS):
        anon = TestClient(c.app, base_url="https://testserver", client=(f"198.51.100.{client_ip + 1}", 1))
        if path.endswith("/pair"):
            for body in ({}, {"code": ""}, {"code": "x" * 33}, {"code": None}):
                r = anon.post(path, json=body)
                assert r.status_code in (400, 422) and "mcnr_" not in r.text and "token" not in r.json().get("detail", "") if isinstance(r.json().get("detail"), str) else True
            assert anon.get(path).status_code == 405
        elif "install" in path:
            r = anon.get(path)
            assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
            assert "expired" in r.text and "__PORTAL__" not in r.text and "linkedin-runner/pair" not in r.text
            assert "mcnr_" not in r.text and "mcnx_" not in r.text
        else:
            for headers in ({}, {"Authorization": "Bearer mcnr_nope"}, {"Authorization": "Bearer mcnx_nope"}):
                r = anon.get(path, headers=headers)
                assert r.status_code in (401, 403), (path, headers, r.status_code)
    assert "mcnr_" not in view
    # rate limits: 10/min pair, 20/min install, 20/min bundle (per address)
    for n, (path, limit) in enumerate((("/api/linkedin-runner/install.sh", 20), ("/api/linkedin-runner/bundle.zip", 20),
                                       ("/api/linkedin-runner/install.ps1", 20))):
        anon = TestClient(c.app, base_url="https://testserver", client=(f"192.0.2.{n + 10}", 1))
        statuses = [anon.get(path).status_code for _ in range(limit + 1)]
        assert statuses[-1] == 429 and 429 not in statuses[:limit], (path, statuses)


# =====================================================================================================================
# 3. Resilience (the real runner process)
# =====================================================================================================================
def office(live):
    runner.set_where(live.store, "office")
    cloud.write_slots_file(live.store, live.base, {})


def start_runner(live, home, code=None, extra=(), agent=None, env=None):
    cmd = ["node", RUNNER, "--portal", f"http://127.0.0.1:{live.port}", "--home", str(home), "--headless", "--interval", "1",
           "--heartbeat", "1", "--backoff-base", "1", "--stop-grace", "3", "--linkedin-base", live.mock_base,
           "--agent-args=--poll 1 --settle 300 --login-poll 1", *extra]
    if agent:
        cmd += ["--agent", agent]
    if code:
        cmd += ["--code", code]
    return live.spawn(cmd, env)


def paired_home(live, tmp, name="office"):
    home = tmp / name
    made = live.call("/api/linkedin-cloud/runner/pair", "POST", {"replace": True})
    done = subprocess.run(["node", RUNNER, "--portal", f"http://127.0.0.1:{live.port}", "--home", str(home), "--code", made["code"], "--pair-only"],
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stdout
    return home


def alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError):
        return False
    try:                                                           # a zombie is dead for our purposes
        return Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0] != "Z"
    except OSError:
        return False


def children(pid) -> list:
    out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True).stdout.split()
    return [int(p) for p in out]


def agent_pids(text, slot_id):
    return [int(p) for p in re.findall(rf"started agent for {slot_id} \([^)]*\), pid (\d+)", text)]


def no_linkedin_dot_com(text):
    hits = [line for line in text.splitlines() if "linkedin.com" in line and "https://www.linkedin.com/in/" not in line]
    assert not hits, f"a request went to linkedin.com: {hits[:3]}"


@needs_node
def test_regression_a_stale_lock_whose_pid_was_reused_does_not_block_the_runner(cloud_live, tmp_path):
    """After a reboot the old runner.lock can name a pid that now belongs to another program: the runner must still start
    (before the fix it printed "already running" and exited 0, so auto-start never ran it)."""
    live = cloud_live
    office(live)
    home = paired_home(live, tmp_path)
    other = subprocess.Popen(["sleep", "300"])
    try:
        lock = home / "runner.lock"
        lock.write_text(str(other.pid))
        old = time.time() - 600
        os.utime(lock, (old, old))                                 # written before the reboot
        proc = start_runner(live, home)
        wait_for(lambda: "MCN office runner 1.0.0:" in live.output() or proc.poll() is not None, 30, what="the runner to start")
        assert proc.poll() is None and "already running" not in live.output()
        assert lock.read_text().strip() == str(proc.pid)
        # a real second copy is still refused while the first one runs
        second = subprocess.run(["node", RUNNER, "--home", str(home)], capture_output=True, text=True, timeout=30)
        assert second.returncode == 0 and "already running" in second.stdout
        proc.terminate()
        assert proc.wait(15) == 0 and not lock.exists()
    finally:
        other.kill()


@needs_node
def test_agent_crash_is_restarted_with_growing_waits_and_a_safety_stop_is_held(cloud_live, tmp_path):
    live = cloud_live
    office(live)
    s = live.call("/api/linkedin-cloud/slots", "POST", {"label": "Crashy"})["slots"][0]
    home = paired_home(live, tmp_path)
    (home / f"{s['id']}.mode").write_text("crash")
    proc = start_runner(live, home, agent=FAKE_AGENT)
    wait_for(lambda: live.output().count(f"agent for {s['id']} exited (code 1); restarting in") >= 3, 30, what="3 crashes")
    out = live.output()
    waits = [int(w) for w in re.findall(rf"agent for {s['id']} exited \(code 1\); restarting in (\d+) s", out)]
    assert waits[:3] == [1, 2, 4], waits                                               # backoff base 1 s here (5 s by default)
    assert "token_env=yes token_argv=no" in (home / "events.log").read_text()
    # a safety stop (exit 3) is not restarted until the owner acts in the portal
    (home / f"{s['id']}.mode").write_text("safety")
    wait_for(lambda: "stopped itself for safety" in live.output(), 30, what="safety stop")
    n = (home / "events.log").read_text().count("start ")
    time.sleep(4)
    assert (home / "events.log").read_text().count("start ") == n
    (home / f"{s['id']}.mode").write_text("run")
    live.call(f"/api/linkedin-cloud/slots/{s['id']}/restart", "POST", {})
    wait_for(lambda: (home / "events.log").read_text().count("start ") == n + 1, 20, what="started after Restart")
    proc.terminate()
    assert proc.wait(15) == 0


@needs_browser
def test_regression_killing_the_runner_does_not_leave_its_agents_running(cloud_live, tmp_path):
    """The runner is killed hard (Task Manager, OOM, a crash): its agents must stop too, or the restarted runner starts a
    second agent on the same LinkedIn account while the orphan keeps going (before the fix the orphan kept running)."""
    live = cloud_live
    office(live)
    s = live.call("/api/linkedin-cloud/slots", "POST", {"label": "Sales"})["slots"][0]
    home = paired_home(live, tmp_path)
    proc = start_runner(live, home)
    live.wait_state(s["id"], "needs_login", timeout=90)
    pid = agent_pids(live.output(), s["id"])[-1]
    browser = children(pid)
    assert alive(pid) and browser
    proc.send_signal(signal.SIGKILL)
    proc.wait(10)
    wait_for(lambda: not alive(pid), 20, what="the orphaned agent to exit")
    wait_for(lambda: not any(alive(b) for b in browser), 20, what="its browser to close")
    no_linkedin_dot_com(live.output())


@needs_browser
def test_regression_a_closed_linkedin_window_opens_again(cloud_live, tmp_path):
    """The browser is closed (window closed, or killed) while the agent waits for a login: the agent must notice and be
    restarted with a new window (before the fix it sat there with no browser until the next login failed)."""
    live = cloud_live
    office(live)
    s = live.call("/api/linkedin-cloud/slots", "POST", {"label": "Sales"})["slots"][0]
    home = paired_home(live, tmp_path)
    proc = start_runner(live, home)
    live.wait_state(s["id"], "needs_login", timeout=90)
    pid = agent_pids(live.output(), s["id"])[-1]
    browser = children(pid)
    assert browser
    for b in browser:
        os.kill(b, signal.SIGKILL)
    wait_for(lambda: len(agent_pids(live.output(), s["id"])) >= 2, 60, what="a new agent (new window)")
    new_pid = agent_pids(live.output(), s["id"])[-1]
    assert not alive(pid) and alive(new_pid)
    wait_for(lambda: children(new_pid), 30, what="the new browser")
    live.wait_state(s["id"], "needs_login", timeout=60)
    assert proc.poll() is None
    no_linkedin_dot_com(live.output())


def test_regression_a_portal_restart_during_login_shows_log_in_again_instead_of_hanging(rig, tmp_path, monkeypatch):
    """Login jobs live in memory only. Before the fix a restart between "Log in" and the agent taking the job left the
    account on "Logging in..." forever (and the portal hides the Log in button in that state)."""
    c, https, owner, store, base = rig
    s = add_slot(c, owner)
    r = c.post(f"/api/linkedin-cloud/slots/{s['id']}/login", json={"email": "sender@example.com", "password": SECRET}, headers=bearer(owner))
    assert r.json()["slots"][0]["state"] == "logging_in" and r.json()["slots"][0]["pending"]["login"] is True
    time.sleep(1.1)                                                                     # stored times are whole seconds
    from app.main import create_app
    from tests.test_extension_api import ADMIN
    again = TestClient(create_app(data_file=tmp_path / "d.json", admin_password=ADMIN), client=("203.0.113.9", 50000))   # restarted
    token = again.post("/api/login", json={"email": "", "password": ADMIN}).json()["token"]
    view = again.get("/api/linkedin-cloud", headers=bearer(token)).json()["slots"][0]
    assert view["state"] == "needs_login" and "Log in again" in view["note"] and view["pending"] == {"login": False, "code": False}
    # logging in again works normally: "logging_in" is shown while the new job waits
    r = again.post(f"/api/linkedin-cloud/slots/{s['id']}/login", json={"email": "sender@example.com", "password": SECRET}, headers=bearer(token))
    assert r.json()["slots"][0]["state"] == "logging_in"
    # the agent took the job and reports logging_in itself: still "logging_in" (no job pending, but fresh)
    slot_token = slots_file(base)["slots"][0]["token"]
    assert again.get("/api/extension/cloud/login-job", headers=bearer(slot_token)).json()["job"]["email"] == "sender@example.com"
    again.post("/api/extension/cloud/state", json={"state": "logging_in", "note": "typing"}, headers=bearer(slot_token))
    assert again.get("/api/linkedin-cloud", headers=bearer(token)).json()["slots"][0]["state"] == "logging_in"
    # ... and an agent that went quiet in "logging_in" for longer than any job lives is shown as needing a login
    later = now_utc() + timedelta(seconds=cloud.LOGIN_STUCK_SECONDS + 5)
    monkeypatch.setattr(cloud, "now_utc", lambda: later)
    assert again.get("/api/linkedin-cloud", headers=bearer(token)).json()["slots"][0]["state"] in ("needs_login", "stopped")


@needs_browser
def test_regression_switching_an_account_off_and_on_keeps_its_linkedin_session(cloud_live, tmp_path):
    """The agent pool stops an agent with an IPC "stop" and SIGTERM together. Both ran close(); the second one exited the
    process before Chromium had written its cookies, so every switch-off, Restart, mode switch or runner stop logged the
    account out (and LinkedIn locks accounts that log in again and again). Before the fix this needed a new login."""
    live = cloud_live
    office(live)
    live.mock.LOGIN["accounts"] = {"asha@example.com": {"password": "Asha-Pass-1", "slug": "asha-sender", "name": "Asha Sender", "mode": "direct"}}
    s = live.call("/api/linkedin-cloud/slots", "POST", {"label": "Asha"})["slots"][0]
    home = paired_home(live, tmp_path)
    proc = start_runner(live, home)
    live.wait_state(s["id"], "needs_login", timeout=90)
    live.call(f"/api/linkedin-cloud/slots/{s['id']}/login", "POST", {"email": "asha@example.com", "password": "Asha-Pass-1"})
    assert live.wait_state(s["id"], "connected")["account"] == "asha-sender"
    time.sleep(1)
    live.call(f"/api/linkedin-cloud/slots/{s['id']}", "PUT", {"enabled": False})
    wait_for(lambda: f"agent for {s['id']} stopped" in live.output(), 30, what="the agent stopped")
    live.call(f"/api/linkedin-cloud/slots/{s['id']}", "PUT", {"enabled": True})
    wait_for(lambda: len(agent_pids(live.output(), s["id"])) >= 2, 30, what="the agent started again")
    wait_for(lambda: "Logged in to LinkedIn as Asha Sender" in live.output().split(f"pid {agent_pids(live.output(), s['id'])[-1]}")[-1]
             or "Not logged in" in live.output().split(f"pid {agent_pids(live.output(), s['id'])[-1]}")[-1], 60, what="the second start")
    after = live.output().split(f"pid {agent_pids(live.output(), s['id'])[-1]}")[-1]
    assert "Not logged in" not in after and live.mock.LOGIN["attempts"] == ["asha@example.com"]
    proc.terminate()
    assert proc.wait(20) == 0
    no_linkedin_dot_com(live.output())


# =====================================================================================================================
# 6. Installers
# =====================================================================================================================
def test_install_ps1_static_check_https_base_only_the_short_code_and_every_step_in_order(rig):
    c, https, owner, store, base = rig
    made = code_for(c, owner)
    code = made["code"]
    ps = https.get(f"/api/linkedin-runner/install.ps1?code={code}").text
    from tests.test_linkedin_runner_e2e import check_ps1_structure
    check_ps1_structure(ps)
    assert "$Portal = 'https://outreach.example.com'" in ps and "http://" not in ps.replace("http://www.apple.com", "")
    assert ps.count(code) == 1 and "__PORTAL__" not in ps and "__CODE__" not in ps
    assert not re.search(r"mcn[rx]_|password|secret", ps, re.I)
    steps = ["icacls.exe", "Find-Node", "SHASUMS256.txt", "Stop-ScheduledTask", "bundle.zip?code=$Code",
             "ci --omit=dev", "playwright install chromium", "--pair-only", "uninstall.ps1", "Register-ScheduledTask", "Start-ScheduledTask"]
    body = ps[ps.index("\ntry {"):]                                                   # after the header comments
    at = [body.index(s) for s in steps]
    assert at == sorted(at), "installer steps out of order"
    assert made["commands"]["windows"].startswith("powershell -NoProfile -ExecutionPolicy Bypass -Command \"[Net.ServicePointManager]::SecurityProtocol=")
    assert "-bor 3072; irm 'https://outreach.example.com/" in made["commands"]["windows"]
    sh = https.get(f"/api/linkedin-runner/install.sh?code={code}").text
    assert "PORTAL='https://outreach.example.com'" in sh and sh.count(code) == 1 and not re.search(r"mcn[rx]_", sh)


def _fake_path(tmp_path) -> str:
    """A PATH with the usual tools but no node / npm, so the installer must fetch Node.js itself."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    for tool in ("bash", "sh", "curl", "tar", "gzip", "mkdir", "chmod", "rm", "mktemp", "grep", "cut", "sha256sum", "uname", "dirname",
                 "cat", "printf", "pkill", "nohup", "pgrep", "sleep", "id", "env", "ls", "mv", "cp", "xz", "head", "sed", "basename", "readlink", "touch"):
        found = subprocess.run(["bash", "-c", f"command -v {tool}"], capture_output=True, text=True).stdout.strip()
        if found and "/" in found:
            (bin_dir / tool).symlink_to(found)
    return str(bin_dir)


@pytest.mark.skipif(os.environ.get("MCN_INSTALLER_E2E") != "1", reason="downloads Node.js and Playwright; set MCN_INSTALLER_E2E=1 to run")
def test_install_sh_without_node_fetches_it_pairs_updates_and_uninstalls(cloud_live, tmp_path):
    live = cloud_live
    office(live)
    home = tmp_path / "home"
    home.mkdir()
    env = {"HOME": str(home), "PATH": _fake_path(tmp_path), "LANG": "C.UTF-8", "npm_config_cache": str(Path.home() / ".npm"),
           "PLAYWRIGHT_BROWSERS_PATH": str(Path.home() / ".cache" / "ms-playwright"), "MCN_LINKEDIN_BASE": live.mock_base}
    base = home / ".local" / "share" / "mcn-linkedin-runner"
    for round_ in (1, 2):                                                               # 2 = re-install (the update path)
        made = live.call("/api/linkedin-cloud/runner/pair", "POST", {"replace": True})
        url = f"http://127.0.0.1:{live.port}/api/linkedin-runner/install.sh?code={made['code']}"
        done = subprocess.run(["bash", "-c", f"curl -fsSL '{url}' | bash -s -- --no-autostart"], env=env, capture_output=True, text=True, timeout=900)
        assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-2000:]
        assert "Done." in done.stdout and (base / "node" / "bin" / "node").exists()
        assert ("Downloading Node.js" in done.stdout) == (round_ == 1)                    # the second run reuses it
        assert made["code"] not in (base / "config.json").read_text()
        wait_for(lambda: (r := live.call("/api/linkedin-cloud")["runner"]) and r["online"] and r["hostname"], 60, what="installed runner online")
        wait_for(lambda: (base / "runner.lock").exists(), 30, what="the runner running")
    assert not (home / ".config" / "autostart").exists() and not (home / ".config" / "systemd").exists()
    pids = subprocess.run(["pgrep", "-f", str(base / "app" / "tools" / "linkedin_runner.mjs")], capture_output=True, text=True).stdout.split()
    assert len(pids) == 1, f"one runner after a re-install, found {pids}"
    out = subprocess.run(["bash", str(base / "uninstall.sh")], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and not base.exists()
    assert not subprocess.run(["pgrep", "-f", str(base)], capture_output=True, text=True).stdout.strip()
    assert not list(home.rglob("*mcn*"))


@needs_node
def test_replacing_the_computer_stops_the_old_runner_and_its_agents_within_one_poll(cloud_live, tmp_path):
    live = cloud_live
    office(live)
    s = live.call("/api/linkedin-cloud/slots", "POST", {"label": "Sales"})["slots"][0]
    old_home = paired_home(live, tmp_path, "old-pc")
    old = start_runner(live, old_home, agent=FAKE_AGENT)
    wait_for(lambda: (old_home / "events.log").exists() and "start " in (old_home / "events.log").read_text(), 30, what="old agent")
    old_pid = int(re.search(r"pid=(\d+)", (old_home / "events.log").read_text()).group(1))
    new_home = paired_home(live, tmp_path, "new-pc")                                    # replace=True: the owner confirmed
    t0 = time.time()
    assert old.wait(15) == 0 and time.time() - t0 < 5                                   # poll interval 1 s here
    assert not alive(old_pid) and f"stop {s['id']}" in (old_home / "events.log").read_text()
    assert "token" not in json.loads((old_home / "config.json").read_text())
    assert (old_home / "profiles" / s["id"]).is_dir()                                   # its browser session stays on that disk
    new = start_runner(live, new_home, agent=FAKE_AGENT)
    wait_for(lambda: (new_home / "events.log").exists() and "start " in (new_home / "events.log").read_text(), 30, what="new agent")
    new.terminate()
    assert new.wait(15) == 0
