"""Office runner end to end: the real tools/linkedin_runner.mjs pairs with a code, comes online, runs the real
linkedin_agent.mjs --cloud (headless Chromium) through the portal login (needs_login -> code -> connected), sends a
connection request to the LinkedIn stand-in (tests/mock_linkedin.py), and exits when the owner disconnects it.
The stand-in's address is passed explicitly (--linkedin-base, forwarded by the runner to every agent): nothing here
opens linkedin.com. Also: install.sh run in a temporary HOME (opt-in, needs npm), and a structure check of install.ps1."""
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from app import linkedin_runner
from tests.test_linkedin_agent import CSV, enrol, open_up
from tests.test_linkedin_cloud_e2e import ROOT, SECRET, cloud_live, needs_browser, needs_node, wait_for  # noqa: F401  (fixture)

RUNNER = str(ROOT / "tools" / "linkedin_runner.mjs")


def office(live):
    linkedin_runner.set_where(live.store, "office")
    from app import linkedin_cloud
    linkedin_cloud.write_slots_file(live.store, live.base, {})


def runner_view(live):
    return live.call("/api/linkedin-cloud")["runner"]


def start_runner(live, home, code=None, extra=()):
    cmd = ["node", RUNNER, "--portal", f"http://127.0.0.1:{live.port}", "--home", str(home), "--headless", "--interval", "1",
           "--heartbeat", "1", "--backoff-base", "1", "--stop-grace", "3", "--linkedin-base", live.mock_base,
           "--agent-args=--poll 1 --settle 300 --login-poll 1", *extra]
    if code:
        cmd += ["--code", code]
    return live.spawn(cmd)


@needs_browser
def test_runner_pairs_runs_the_portal_login_sends_a_request_and_exits_when_revoked(cloud_live):
    live = cloud_live
    office(live)
    live.call("/api/leads/import", "POST", {"csv": CSV, "source": "apollo"})
    ravi = enrol(live, "Acme Pvt Ltd", "ravi-example")
    live.mock.install("ravi-example", "Ravi Kumar", "2nd", "connect")
    open_up(live)
    live.mock.LOGIN["mode"] = "code"
    home = live.tmp / "office-home"
    made = live.call("/api/linkedin-cloud/runner/pair", "POST", {})
    proc = start_runner(live, home, made["code"])
    r = wait_for(lambda: (x := runner_view(live)) and x["online"] and x, 30, what="runner online")
    assert r["hostname"] and "node" in r["version"]
    config = json.loads((home / "config.json").read_text())
    assert config["token"].startswith("mcnr_") and config["runner_id"] == r["id"]
    assert stat.S_IMODE(os.stat(home / "config.json").st_mode) == 0o600 and stat.S_IMODE(os.stat(home).st_mode) == 0o700
    # the owner adds an account and logs it in from the portal; the browser runs on the "office computer"
    slot = live.call("/api/linkedin-cloud/slots", "POST", {"label": "Sales"})["slots"][0]
    live.wait_state(slot["id"], "needs_login", timeout=90)
    assert json.loads((live.base / "slots.json").read_text())["slots"][0]["enabled"] is False      # the server supervisor would idle
    live.call(f"/api/linkedin-cloud/slots/{slot['id']}/login", "POST", {"email": "sender@example.com", "password": SECRET})
    live.wait_state(slot["id"], "needs_code")
    live.call(f"/api/linkedin-cloud/slots/{slot['id']}/code", "POST", {"code": "246810"})
    s = live.wait_state(slot["id"], "connected")
    assert s["account"] == "test-sender"
    wait_for(lambda: [e for e in live.mock.EVENTS if e["kind"] == "invite"], 60, what="the connection request")
    assert [e for e in live.mock.EVENTS if e["kind"] == "invite"][0]["note"].startswith("Hi Ravi,")
    wait_for(lambda: next(l for l in live.store.read("leads") if l["id"] == ravi)["linkedin_seq"]["status"] == "waiting_for_acceptance", 20)
    wait_for(lambda: (runner_view(live)["agents"] or [{}])[0].get("state") == "running", 10, what="agent state in the heartbeat")
    assert live.call("/api/linkedin-cloud")["slots"][0]["runner"] == "running"
    out = live.output()
    assert f"LinkedIn address: {live.mock_base}" in out and "www.linkedin.com" not in out.replace("https://www.linkedin.com/in/", "")
    assert live.mock.LOGIN["attempts"] == ["sender@example.com"]
    profile = home / "profiles" / slot["id"]
    assert profile.is_dir() and stat.S_IMODE(profile.stat().st_mode) == 0o700
    assert (home / "logs" / "runner.log").exists() and "started agent for" in (home / "logs" / "runner.log").read_text()
    assert config["token"] not in out and SECRET not in out
    # the owner disconnects the computer: the runner stops its agents, forgets the token and exits with a clear message
    live.call(f"/api/linkedin-cloud/runner/{r['id']}", "DELETE")
    assert proc.wait(30) == 0
    out = live.output()
    assert "disconnected in the portal" in out and "agent for" in out and "stopped" in out
    assert "token" not in json.loads((home / "config.json").read_text())
    agent_pids = re.findall(r"started agent for \w+ \(Sales\), pid (\d+)", out)
    assert agent_pids and all(not Path(f"/proc/{p}").exists() for p in agent_pids)
    # started again without a token it explains what to do and exits cleanly (auto-start does not loop)
    again = subprocess.run(["node", RUNNER, "--home", str(home)], capture_output=True, text=True, timeout=30)
    assert again.returncode == 0 and "not connected to the portal" in again.stdout


@needs_node
def test_runner_survives_a_portal_outage_and_refuses_a_bad_code(cloud_live, tmp_path):
    live = cloud_live
    office(live)
    home = tmp_path / "h"
    bad = subprocess.run(["node", RUNNER, "--portal", f"http://127.0.0.1:{live.port}", "--home", str(home), "--code", "ZZZZZZZZ"],
                         capture_output=True, text=True, timeout=30)
    assert bad.returncode == 2 and "wrong, already used or expired" in bad.stdout
    made = live.call("/api/linkedin-cloud/runner/pair", "POST", {})
    pair = subprocess.run(["node", RUNNER, "--portal", f"http://127.0.0.1:{live.port}", "--home", str(home), "--code", made["code"], "--pair-only"],
                          capture_output=True, text=True, timeout=30)
    assert pair.returncode == 0, pair.stdout
    # point it at a dead portal address: it keeps retrying with a growing wait instead of exiting
    config = json.loads((home / "config.json").read_text())
    proc = live.spawn(["node", RUNNER, "--home", str(home), "--portal", "http://127.0.0.1:9", "--interval", "1", "--headless"])
    wait_for(lambda: live.output().count("Could not reach the portal") >= 2, 30, what="retries")
    assert proc.poll() is None and "trying again in 10 s" in live.output()
    # a second copy on the same computer refuses to start (one runner per computer)
    second = subprocess.run(["node", RUNNER, "--home", str(home), "--portal", "http://127.0.0.1:9"], capture_output=True, text=True, timeout=30)
    assert second.returncode == 0 and "already running" in second.stdout
    proc.terminate()
    assert proc.wait(15) == 0 and json.loads((home / "config.json").read_text())["token"] == config["token"]


# ---------- installers ----------
def test_install_sh_is_valid_bash_and_ps1_is_well_formed(cloud_live):
    live = cloud_live
    made = live.call("/api/linkedin-cloud/runner/pair", "POST", {})
    import urllib.request
    base = f"http://127.0.0.1:{live.port}"
    sh = urllib.request.urlopen(f"{base}/api/linkedin-runner/install.sh?code={made['code']}").read().decode()
    ps = urllib.request.urlopen(f"{base}/api/linkedin-runner/install.ps1?code={made['code']}").read().decode()
    check = subprocess.run(["bash", "-n"], input=sh, capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    assert sh.rstrip().endswith('main "$@" </dev/null')                                    # nothing runs before the whole script arrived
    check_ps1_structure(ps)
    assert made["code"] in ps and base in ps


def check_ps1_structure(text):
    """Windows cannot be run here: check brackets / quotes balance outside strings and comments, and that every step is
    present (Node 18-24 or the verified zip, bundle, npm ci, Chromium without --with-deps, pairing, Task Scheduler)."""
    stack, i, n = [], 0, len(text)
    pairs = {"(": ")", "{": "}", "[": "]"}
    while i < n:
        ch = text[i]
        if ch == "#" and (i == 0 or text[i - 1] in " \t\n;"):
            i = text.find("\n", i)
            i = n if i < 0 else i
            continue
        if ch == "'":
            end = i + 1
            while True:
                end = text.find("'", end)
                assert end > 0, f"unclosed single quote at {text[:i].count(chr(10)) + 1}"
                if text[end + 1:end + 2] == "'":
                    end += 2
                    continue
                break
            i = end + 1
            continue
        if ch == '"':
            end = i + 1
            while end < n and not (text[end] == '"' and text[end - 1] != "`"):
                end += 1
            assert end < n, f"unclosed double quote at line {text[:i].count(chr(10)) + 1}"
            i = end + 1
            continue
        if ch in pairs:
            stack.append((ch, text[:i].count("\n") + 1))
        elif ch in pairs.values():
            assert stack and pairs[stack[-1][0]] == ch, f"unbalanced {ch} at line {text[:i].count(chr(10)) + 1}"
            stack.pop()
        i += 1
    assert not stack, f"unclosed {stack}"
    for needle in ("SHASUMS256.txt", "Get-FileHash", "bundle.zip?code=", "ci --omit=dev",
                   "playwright install chromium", "--pair-only", "Register-ScheduledTask", "New-ScheduledTaskTrigger -AtLogOn",
                   "MCN_NO_AUTOSTART", "uninstall.ps1", "icacls.exe"):
        assert needle in text, needle
    assert "--with-deps" not in text and "mcnr_" not in text and "mcnx_" not in text


@pytest.mark.skipif(os.environ.get("MCN_INSTALLER_E2E") != "1" or not shutil.which("npm"),
                    reason="runs npm ci from the registry; set MCN_INSTALLER_E2E=1 to run")
def test_install_sh_in_a_temporary_home_pairs_and_starts_the_runner(cloud_live, tmp_path):
    live = cloud_live
    office(live)
    home = tmp_path / "home"
    home.mkdir()
    made = live.call("/api/linkedin-cloud/runner/pair", "POST", {})
    env = {"HOME": str(home), "PATH": os.environ["PATH"], "LANG": "C.UTF-8",
           "npm_config_cache": str(Path.home() / ".npm"),                                     # reuse the download cache
           "PLAYWRIGHT_BROWSERS_PATH": str(Path.home() / ".cache" / "ms-playwright")}         # Chromium already downloaded here
    url = f"http://127.0.0.1:{live.port}/api/linkedin-runner/install.sh?code={made['code']}"
    done = subprocess.run(["bash", "-c", f"curl -fsSL '{url}' | bash -s -- --no-autostart"], env=env, capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout + done.stderr
    base = home / ".local" / "share" / "mcn-linkedin-runner"
    assert "Done." in done.stdout and (base / "app" / "node_modules" / "playwright").is_dir()
    assert json.loads((base / "config.json").read_text())["token"].startswith("mcnr_")
    assert not (home / ".config" / "autostart").exists() and not (home / ".config" / "systemd").exists()
    # The installed runner has no --linkedin-base: this test must never add a LinkedIn account (it would open linkedin.com).
    r = wait_for(lambda: (x := runner_view(live)) and x["online"] and x, 60, what="the installed runner online")
    live.call(f"/api/linkedin-cloud/runner/{r['id']}", "DELETE")
    wait_for(lambda: not (base / "runner.lock").exists(), 30, what="the runner to exit after revoke")
    out = subprocess.run(["bash", str(base / "uninstall.sh")], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and not base.exists()


def test_a_crash_after_pairing_counts_only_when_a_new_token_was_saved(tmp_path):
    """Node 24 on Windows can crash while exiting after a good pairing (libuv assertion): the installers then go on when a
    NEW token is in config.json. An earlier install's token must not make a failed re-pairing look connected."""
    sh = (ROOT / "deploy" / "office-runner" / "install.sh").read_text()
    step = sh[sh.index("# 3. Connect to the portal"):sh.index("# Uninstaller")]
    base = tmp_path / "base"
    base.mkdir()

    def run(node_body: str, old_config):
        cfg = base / "config.json"
        if old_config is None:
            cfg.unlink(missing_ok=True)
        else:
            cfg.write_text(old_config)
        fake = tmp_path / "node"
        fake.write_text("#!/usr/bin/env bash\n" + node_body + "\n")
        fake.chmod(0o755)
        script = (f'fail() {{ echo "FAILED: $1"; exit 1; }}\nNODE="{fake}"; RUNNER=r; PORTAL=p; CODE=c; BASE="{base}"\n'
                  + step + '\necho CONTINUED\n')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30).stdout

    old = '{"portal": "p", "token": "mcnr_old"}'
    writes_new_then_crashes = f"echo '{{\"portal\": \"p\", \"token\": \"mcnr_new\"}}' > '{base}/config.json'; exit 134"
    assert "CONTINUED" in run(writes_new_then_crashes, None)               # first install, crash after saving the token
    assert "CONTINUED" in run(writes_new_then_crashes, old)                # re-install, crash after saving a new token
    assert "FAILED" in run("exit 2", old)                                  # re-install, pairing refused: old token only
    assert "FAILED" in run("exit 2", None)
    assert "CONTINUED" in run("exit 0", old)
    ps = (ROOT / "deploy" / "office-runner" / "install.ps1").read_text(encoding="utf-8")
    pair_at = ps.index("--pair-only")
    assert ps.index("$before = ") < pair_at < ps.index("($now -ne $before)")
