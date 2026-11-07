"""Cloud LinkedIn agent end to end: the real tools/linkedin_agent.mjs --cloud (headless Chromium) and the real
tools/linkedin_cloud_supervisor.mjs against the real app and the LinkedIn stand-in (tests/mock_linkedin.py), which has a
login page with dynamic ids, a verification-code page, an approve-in-the-app page and a CAPTCHA page.
Nothing here touches linkedin.com."""
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import uvicorn

from app import linkedin_agent as agent_mod, linkedin_runner
from app.main import create_app
from tests import mock_linkedin
from tests.test_linkedin_agent import ADMIN_PASSWORD, CSV, enrol, open_up

ROOT = Path(__file__).resolve().parent.parent
HAS_NODE = shutil.which("node") is not None
HAS_BROWSER = HAS_NODE and (ROOT / "web" / "node_modules" / "playwright").exists()
needs_browser = pytest.mark.skipif(not HAS_BROWSER, reason="node and Playwright (web/node_modules) are needed")
needs_node = pytest.mark.skipif(not HAS_NODE, reason="node is needed")
SECRET = mock_linkedin.LOGIN_DEFAULTS["password"]


def wait_for(fn, timeout=60, step=0.3, what="condition"):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = fn()
        if last:
            return last
        time.sleep(step)
    raise AssertionError(f"timed out waiting for {what} (last: {last!r})")


@pytest.fixture()
def cloud_live(tmp_path, monkeypatch):
    monkeypatch.delenv("OUTREACH_CLOUD_LI_DIR", raising=False)
    monkeypatch.delenv("OUTREACH_CLOUD_LI_MAX", raising=False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    app = create_app(data_file=tmp_path / "d.json", admin_password=ADMIN_PASSWORD)
    linkedin_runner.set_where(app.state.store, "server")             # the server supervisor runs the agents here
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    wait_for(lambda: server.started, 10, 0.05, "the app")
    mock_server, mock_base = mock_linkedin.start()
    mock_linkedin.LOGIN["required"] = True
    procs = []

    def call(path, method="GET", body=None, token=None, ok=True):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", json.dumps(body).encode() if body is not None else None,
                                     {"Content-Type": "application/json", "Authorization": f"Bearer {token or owner}"}, method=method)
        try:
            return json.load(urllib.request.urlopen(req))
        except urllib.error.HTTPError as e:
            if ok:
                raise AssertionError(f"{method} {path}: {e.code} {e.read()[:200]}") from None
            return {"status": e.code}
    owner = call("/api/login", "POST", {"email": "", "password": ADMIN_PASSWORD}, token="x")["token"]
    base = tmp_path / "linkedin-cloud"

    def slot_state(slot_id):
        return next((s for s in call("/api/linkedin-cloud")["slots"] if s["id"] == slot_id), None)

    def wait_state(slot_id, *states, timeout=60):
        return wait_for(lambda: (s := slot_state(slot_id)) and s["state"] in states and s, timeout, what=f"state {states}")

    def token(slot_id):
        return next(r["token"] for r in json.loads((base / "slots.json").read_text())["slots"] if r["slot_id"] == slot_id)

    def agent_args():
        return ["--linkedin-base", mock_base, "--poll", "1", "--settle", "300", "--login-poll", "1"]

    def spawn(cmd, env=None):
        out = open(tmp_path / f"proc{len(procs)}.log", "w")
        p = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, env={**os.environ, **(env or {})})
        procs.append((p, out))
        return p

    def output():
        return "".join((tmp_path / f"proc{i}.log").read_text() for i in range(len(procs)))

    yield type("CloudLive", (), {"call": staticmethod(call), "store": app.state.store, "owner": owner, "base": base, "port": port,
                                 "mock": mock_linkedin, "mock_base": mock_base, "slot_state": staticmethod(slot_state),
                                 "wait_state": staticmethod(wait_state), "token": staticmethod(token), "agent_args": staticmethod(agent_args),
                                 "spawn": staticmethod(spawn), "output": staticmethod(output), "tmp": tmp_path})
    print("---- process output ----\n" + output()[-4000:])                         # shown when a test fails
    for p, out in procs:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        out.close()
    server.should_exit = True; mock_server.shutdown(); thread.join(timeout=5)


def no_secret_anywhere(live):
    for f in [p for p in live.tmp.rglob("*") if p.is_file() and "profiles" not in p.parts]:
        assert SECRET not in f.read_text(errors="ignore"), f"password found in {f}"
    assert SECRET not in live.output()


@needs_browser
def test_supervisor_runs_the_agent_through_login_code_and_a_real_connect(cloud_live):
    live = cloud_live
    live.call("/api/leads/import", "POST", {"csv": CSV, "source": "apollo"})
    ravi = enrol(live, "Acme Pvt Ltd", "ravi-example")
    live.mock.install("ravi-example", "Ravi Kumar", "2nd", "connect")
    open_up(live)
    live.mock.LOGIN["mode"] = "code"
    slot = live.call("/api/linkedin-cloud/slots", "POST", {"label": "Sales"})["slots"][0]
    sup = live.spawn(["node", str(ROOT / "tools/linkedin_cloud_supervisor.mjs"), "--dir", str(live.base), "--portal", f"http://127.0.0.1:{live.port}",
                      "--interval", "1", "--max", "1", "--agent-args=" + " ".join(live.agent_args())])
    live.wait_state(slot["id"], "needs_login", timeout=90)
    assert json.loads((live.base / "supervisor.json").read_text())["agents"][slot["id"]] == "running"
    assert live.call("/api/linkedin-cloud")["supervisor"]["running"] is True
    # a wrong password first: LinkedIn refuses it and the agent asks again
    live.call(f"/api/linkedin-cloud/slots/{slot['id']}/login", "POST", {"email": "sender@example.com", "password": "wrong-one"})
    s = wait_for(lambda: (x := live.slot_state(slot["id"])) and x["state"] == "needs_login" and "did not accept" in x["note"] and x, 60, what="refused login")
    assert s["pending"]["login"] is False
    live.call(f"/api/linkedin-cloud/slots/{slot['id']}/login", "POST", {"email": "sender@example.com", "password": SECRET})
    live.wait_state(slot["id"], "needs_code")
    assert live.mock.LOGIN["attempts"] == ["sender@example.com", "sender@example.com"]
    live.call(f"/api/linkedin-cloud/slots/{slot['id']}/code", "POST", {"code": "246810"})
    s = live.wait_state(slot["id"], "connected")
    assert s["account"] == "test-sender" and s["name"] == "Test Sender" and s["online"]
    wait_for(lambda: [e for e in live.mock.EVENTS if e["kind"] == "invite"], 60, what="the connection request")
    sent = [e for e in live.mock.EVENTS if e["kind"] == "invite"]
    assert len(sent) == 1 and sent[0]["note"].startswith("Hi Ravi,")
    wait_for(lambda: next(l for l in live.store.read("leads") if l["id"] == ravi)["linkedin_seq"]["status"] == "waiting_for_acceptance", 20)
    assert any(a["account"] == "test-sender" for a in live.call("/api/linkedin-agent")["accounts"])
    profile = live.base / "profiles" / slot["id"]
    assert profile.is_dir() and oct(profile.stat().st_mode & 0o777) == "0o700"
    no_secret_anywhere(live)
    # log out: the supervisor stops the agent, deletes the browser session and starts it again logged out
    live.call(f"/api/linkedin-cloud/slots/{slot['id']}/logout", "POST")
    live.wait_state(slot["id"], "needs_login", timeout=90)
    assert "deleted the browser session" in live.output()
    # remove: agent stopped, profile deleted, token dead
    tok = live.token(slot["id"])
    live.call(f"/api/linkedin-cloud/slots/{slot['id']}", "DELETE")
    wait_for(lambda: "its cloud account was removed" in live.output() or "deleted the browser profile of removed account" in live.output(),
             30, what="the supervisor to stop the agent")
    wait_for(lambda: not profile.exists(), 30, what="the profile to be deleted")
    assert live.call("/api/extension/agent", token=tok, ok=False)["status"] == 401
    assert sup.poll() is None
    no_secret_anywhere(live)


def run_cloud_agent(live, slot_id, *extra):
    return live.spawn(["node", str(ROOT / "tools/linkedin_agent.mjs"), "--cloud", "--portal", f"http://127.0.0.1:{live.port}",
                       "--profile", str(live.base / "profiles" / slot_id), *live.agent_args(), *extra], env={"MCN_TOKEN": live.token(slot_id)})


@needs_browser
def test_app_approval_waits_for_the_phone_then_connects(cloud_live):
    live = cloud_live
    live.mock.LOGIN["mode"] = "app"
    slot = live.call("/api/linkedin-cloud/slots", "POST", {"label": "Sales"})["slots"][0]
    agent = run_cloud_agent(live, slot["id"])
    live.wait_state(slot["id"], "needs_login", timeout=60)
    live.call(f"/api/linkedin-cloud/slots/{slot['id']}/login", "POST", {"email": "sender@example.com", "password": SECRET})
    s = live.wait_state(slot["id"], "needs_app_approval")
    assert "approve" in s["note"].lower()
    time.sleep(2)
    assert live.slot_state(slot["id"])["state"] == "needs_app_approval"           # still waiting, not giving up
    live.mock.LOGIN["approved"] = True                                            # the person taps Yes on the phone
    s = live.wait_state(slot["id"], "connected")
    assert s["account"] == "test-sender"
    # a security check while working: the account is paused, the agent waits (it does not exit or retry) until Resume
    live.call("/api/leads/import", "POST", {"csv": CSV, "source": "apollo"})
    enrol(live, "Initech", "blocked")
    open_up(live)
    s = live.wait_state(slot["id"], "paused")
    assert "security check" in s["note"] and agent.poll() is None
    assert agent_mod.paused(live.store).get("test-sender")
    time.sleep(3)
    assert live.slot_state(slot["id"])["state"] == "paused" and agent.poll() is None
    live.call("/api/linkedin-agent", "PUT", {"resume_account": "test-sender"})
    wait_for(lambda: "Resumed from the portal." in live.output(), 60, what="resume")
    no_secret_anywhere(live)


@needs_browser
def test_a_captcha_stops_the_login_attempt_and_asks_for_a_person(cloud_live):
    live = cloud_live
    live.mock.LOGIN["mode"] = "captcha"
    slot = live.call("/api/linkedin-cloud/slots", "POST", {"label": "Sales"})["slots"][0]
    agent = run_cloud_agent(live, slot["id"])
    live.wait_state(slot["id"], "needs_login", timeout=60)
    live.call(f"/api/linkedin-cloud/slots/{slot['id']}/login", "POST", {"email": "sender@example.com", "password": SECRET})
    s = live.wait_state(slot["id"], "needs_manual")
    assert "CAPTCHA" in s["note"] and "does not solve" in s["note"]
    time.sleep(3)
    assert live.slot_state(slot["id"])["state"] == "needs_manual"                 # it stays there, no retry on its own
    assert live.mock.LOGIN["attempts"] == ["sender@example.com"] and agent.poll() is None
    no_secret_anywhere(live)


@needs_browser
def test_with_a_person_at_the_window_a_captcha_waits_for_them_then_connects_without_a_restart(cloud_live):
    """Office runner (visible window): the agent never solves the puzzle, it asks for a person, brings the window forward
    and carries on by itself once the person is in. Before, it gave up and the owner had to log in AND press Restart."""
    live = cloud_live
    live.mock.LOGIN["mode"] = "captcha"
    slot = live.call("/api/linkedin-cloud/slots", "POST", {"label": "Sales"})["slots"][0]
    agent = run_cloud_agent(live, slot["id"], "--person-at-window")
    live.wait_state(slot["id"], "needs_login", timeout=60)
    live.call(f"/api/linkedin-cloud/slots/{slot['id']}/login", "POST", {"email": "sender@example.com", "password": SECRET})
    s = live.wait_state(slot["id"], "needs_manual")
    assert "Solve it in the LinkedIn window" in s["note"]
    time.sleep(3)
    assert live.slot_state(slot["id"])["state"] == "needs_manual" and agent.poll() is None     # waiting, not giving up
    live.mock.LOGIN["by_hand"] = True                                             # the person solves the puzzle
    s = live.wait_state(slot["id"], "connected")
    assert s["account"] == "test-sender"
    assert live.mock.LOGIN["attempts"] == ["sender@example.com"]                  # one login, no retry
    no_secret_anywhere(live)


@needs_browser
def test_with_a_person_at_the_window_a_login_by_hand_is_noticed_while_waiting(cloud_live):
    live = cloud_live
    slot = live.call("/api/linkedin-cloud/slots", "POST", {"label": "Sales"})["slots"][0]
    run_cloud_agent(live, slot["id"], "--person-at-window")
    s = live.wait_state(slot["id"], "needs_login", timeout=60)
    assert "log in yourself in the LinkedIn window" in s["note"]
    live.mock.LOGIN["by_hand"] = True                                             # logged in by hand, no details in the portal
    s = live.wait_state(slot["id"], "connected")
    assert s["account"] == "test-sender" and live.mock.LOGIN["attempts"] == []


# ---------------------------------------------------------------------------------------------------------------------
# The supervisor alone, with a stand-in agent (no browser): start, stop, restart with backoff, safety hold, log out, remove.
def write_slots(base, rows):
    base.mkdir(parents=True, exist_ok=True)
    (base / "slots.json").write_text(json.dumps({"slots": [{"token": f"mcnx_{r['slot_id']}", "label": r["slot_id"], "enabled": True,
                                                             "run_seq": 0, "wipe_seq": 0, **r} for r in rows]}))


def events(base):
    try:
        return (base / "events.log").read_text().splitlines()
    except FileNotFoundError:
        return []


def count(base, word, slot):
    return sum(1 for e in events(base) if e.startswith(f"{word} {slot}"))


@needs_node
def test_supervisor_start_stop_restart_and_cleanup(tmp_path):
    base = tmp_path / "cloud"
    write_slots(base, [{"slot_id": "aaaa1"}, {"slot_id": "bbbb2"}])
    out = open(tmp_path / "sup.log", "w")
    sup = subprocess.Popen(["node", str(ROOT / "tools/linkedin_cloud_supervisor.mjs"), "--dir", str(base), "--interval", "0.3", "--max", "1",
                            "--agent", str(ROOT / "tests/agent_sim/fake_cloud_agent.mjs"), "--backoff-base", "0.5", "--stop-grace", "2"],
                           stdout=out, stderr=subprocess.STDOUT)
    try:
        wait_for(lambda: count(base, "start", "aaaa1") == 1, 15, what="first agent")
        time.sleep(1)
        assert count(base, "start", "bbbb2") == 0                                  # max 1: the second slot waits
        first = next(e for e in events(base) if e.startswith("start aaaa1"))
        assert "token_env=yes" in first and "token_argv=no" in first and "cloud=true" in first
        assert json.loads((base / "supervisor.json").read_text())["agents"] == {"aaaa1": "running", "bbbb2": "waiting"}
        # switch the first off: it stops, and the second may start
        write_slots(base, [{"slot_id": "aaaa1", "enabled": False}, {"slot_id": "bbbb2"}])
        wait_for(lambda: count(base, "stop", "aaaa1") == 1 and count(base, "start", "bbbb2") == 1, 15, what="swap")
        # log out the second: stopped, its profile wiped, started again
        assert (base / "profiles" / "bbbb2" / "Cookies").exists()
        write_slots(base, [{"slot_id": "aaaa1", "enabled": False}, {"slot_id": "bbbb2", "wipe_seq": 1}])
        wait_for(lambda: count(base, "start", "bbbb2") == 2, 15, what="restart after log out")
        assert count(base, "stop", "bbbb2") == 1
        assert json.loads((base / "supervisor-state.json").read_text())["wiped"] == {"bbbb2": 1}
        # Restart in the portal (restart_seq) stops a running agent and starts it again
        write_slots(base, [{"slot_id": "aaaa1", "enabled": False}, {"slot_id": "bbbb2", "wipe_seq": 1, "restart_seq": 1}])
        wait_for(lambda: count(base, "start", "bbbb2") == 3 and count(base, "stop", "bbbb2") == 2, 15, what="restart")
        # a crashing agent is restarted with a growing wait
        (base / "bbbb2.mode").write_text("crash")
        write_slots(base, [{"slot_id": "bbbb2", "wipe_seq": 1, "restart_seq": 1}])     # aaaa1 removed
        pid = int([e for e in events(base) if e.startswith("start bbbb2")][-1].split("pid=")[1].split()[0])
        os.kill(pid, 9)                                                               # the running one dies; restarts now crash
        wait_for(lambda: count(base, "crash", "bbbb2") >= 3, 30, what="three crashes")
        assert "restarting in 1 s" in (tmp_path / "sup.log").read_text() and "restarting in 2 s" in (tmp_path / "sup.log").read_text()
        assert not (base / "profiles" / "aaaa1").exists()                          # removed slot: profile deleted
        # a safety stop (exit 3) is not restarted until the owner acts (run_seq changes)
        (base / "bbbb2.mode").write_text("safety")
        wait_for(lambda: count(base, "safety", "bbbb2") == 1, 30, what="safety stop")
        starts = count(base, "start", "bbbb2")
        time.sleep(2)
        assert count(base, "start", "bbbb2") == starts
        assert json.loads((base / "supervisor.json").read_text())["agents"]["bbbb2"] == "held"
        (base / "bbbb2.mode").write_text("run")
        write_slots(base, [{"slot_id": "bbbb2", "wipe_seq": 1, "restart_seq": 1, "run_seq": 2}])
        wait_for(lambda: count(base, "start", "bbbb2") == starts + 1, 15, what="start after the owner acted")
        # removing the last slot stops it and deletes its profile
        write_slots(base, [])
        wait_for(lambda: count(base, "stop", "bbbb2") >= 2 and not (base / "profiles" / "bbbb2").exists(), 15, what="removal")
        sup.terminate()
        assert sup.wait(10) == 0
    finally:
        if sup.poll() is None:
            sup.kill()
        out.close()
