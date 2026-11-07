"""LinkedIn agent rules: the switch, working hours, fixed gap, caps, and the automatic pause on a fatal problem."""
from datetime import datetime, timedelta, timezone

import pytest

from app import linkedin_agent as agent
from tests.test_extension_api import bearer, make, pair

IST = timezone(timedelta(hours=5, minutes=30))
TUE_11 = datetime(2026, 11, 3, 11, 0, tzinfo=IST).astimezone(timezone.utc)          # Tuesday 11:00 IST
SAT_11 = datetime(2026, 11, 7, 11, 0, tzinfo=IST).astimezone(timezone.utc)


@pytest.fixture()
def rig(tmp_path):
    c, owner, acme, globex = make(tmp_path)
    return c, owner, c.app.state.store


def test_agent_is_off_until_the_owner_switches_it_on(rig):
    c, owner, store = rig
    ctl = agent.control(store, TUE_11)
    assert ctl["enabled"] is False and not ctl["may_connect"] and not ctl["may_message"] and "switch is off" in ctl["why_not"]
    r = c.put("/api/linkedin-agent", json={"enabled": True}, headers=bearer(owner))
    assert r.status_code == 200 and r.json()["config"]["enabled"] is True
    ctl = agent.control(store, TUE_11)
    assert ctl["may_connect"] and ctl["may_message"] and ctl["connections_left_today"] == 12


def test_send_without_a_note_is_an_owner_switch_passed_to_the_agent(rig):
    c, owner, store = rig
    assert agent.control(store, TUE_11)["allow_no_note"] is False          # off by default: notes stay the norm
    r = c.put("/api/linkedin-agent", json={"connect_without_note": True}, headers=bearer(owner))
    assert r.status_code == 200 and r.json()["config"]["connect_without_note"] is True
    assert agent.control(store, TUE_11)["allow_no_note"] is True


def test_working_hours_and_weekends_are_respected(rig):
    c, owner, store = rig
    agent.save_config(store, {"enabled": True})
    assert agent.control(store, TUE_11 - timedelta(hours=3))["why_not"] == "outside working hours"      # 08:00 IST
    assert not agent.control(store, TUE_11 + timedelta(hours=8))["may_connect"]                          # 19:00 IST
    assert agent.control(store, SAT_11)["why_not"] == "outside working hours"
    agent.save_config(store, {"weekdays_only": False})
    assert agent.control(store, SAT_11)["may_connect"]


def test_a_fixed_gap_between_actions_survives_a_restart(rig):
    c, owner, store = rig
    agent.save_config(store, {"enabled": True, "gap_minutes": 6})
    agent.record(store, {"kind": "connect", "lead_id": "l1"}, now=TUE_11)
    ctl = agent.control(store, TUE_11 + timedelta(minutes=2))
    assert not ctl["may_connect"] and not ctl["may_message"] and 230 <= ctl["wait_seconds"] <= 240
    assert agent.control(store, TUE_11 + timedelta(minutes=6, seconds=1))["may_connect"]


def test_daily_and_weekly_caps(rig):
    c, owner, store = rig
    agent.save_config(store, {"enabled": True, "gap_minutes": 3, "weekly_connections": 5, "daily_messages": 2})
    store.update("settings", lambda s: {**s, "linkedin_daily_connections": 3})
    t = TUE_11
    for i in range(3):
        agent.record(store, {"kind": "connect", "lead_id": f"l{i}"}, now=t)
        t += timedelta(minutes=4)
    ctl = agent.control(store, t)
    assert ctl["connections_left_today"] == 0 and not ctl["may_connect"] and ctl["may_message"]
    for i in range(2):
        agent.record(store, {"kind": "message", "lead_id": f"m{i}"}, now=t)
        t += timedelta(minutes=4)
    assert not agent.control(store, t)["may_message"]
    nextday = TUE_11 + timedelta(days=1)                                        # Wednesday: a new day, but the week still counts
    store.update("settings", lambda s: {**s, "linkedin_daily_connections": 12})
    assert agent.control(store, nextday)["connections_left_today"] == 2         # weekly cap 5 minus 3 this week


def test_a_fatal_error_pauses_the_agent_and_tells_the_owner(rig):
    c, owner, store = rig
    agent.save_config(store, {"enabled": True})
    agent.record(store, {"kind": "error", "fatal": True, "note": "LinkedIn showed a security checkpoint"}, now=TUE_11)
    assert agent.config(store)["enabled"] is False
    note = store.read("notifications")[-1]
    assert "LinkedIn agent paused" in note["title"] and "checkpoint" in note["text"]
    agent.record(store, {"kind": "error", "note": "a button was missing"}, now=TUE_11)             # an ordinary error does not pause
    assert agent.config(store)["enabled"] is False                                                 # (it was already off)
    agent.save_config(store, {"enabled": True})
    agent.record(store, {"kind": "error", "note": "a button was missing"}, now=TUE_11)
    assert agent.config(store)["enabled"] is True


def test_settings_are_validated(rig):
    c, owner, store = rig
    assert c.put("/api/linkedin-agent", json={"weekly_connections": 500}, headers=bearer(owner)).status_code == 400
    assert c.put("/api/linkedin-agent", json={"gap_minutes": 1}, headers=bearer(owner)).status_code == 400
    assert c.put("/api/linkedin-agent", json={"start_hour": 18, "end_hour": 9}, headers=bearer(owner)).status_code == 400


def test_the_pairing_token_can_use_the_agent_calls_but_not_the_portal_switch(rig):
    c, owner, store = rig
    token = pair(c, owner)["token"]
    assert c.get("/api/extension/agent", headers=bearer(token)).status_code == 200
    r = c.post("/api/extension/agent/event", json={"kind": "started", "state": "running"}, headers=bearer(token))
    assert r.status_code == 200 and agent.status(store)["state"] == "running"
    assert c.put("/api/linkedin-agent", json={"enabled": True}, headers=bearer(token)).status_code in (401, 403)
    assert c.get("/api/linkedin-agent", headers=bearer(token)).status_code in (401, 403)
    assert c.post("/api/extension/agent/event", json={"kind": "nonsense"}, headers=bearer(token)).status_code == 400


# ---------------------------------------------------------------------------------------------------------------------
# The agent program itself, driven against the real app and a stand-in for LinkedIn's pages (tests/mock_linkedin.py).
import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import uvicorn

from app import linkedin_seq as li
from app.main import create_app
from tests import mock_linkedin

ROOT = Path(__file__).resolve().parent.parent
HAS_BROWSER = shutil.which("node") is not None and (ROOT / "web" / "node_modules" / "playwright").exists()
needs_browser = pytest.mark.skipif(not HAS_BROWSER, reason="node and Playwright (web/node_modules) are needed")


@pytest.fixture()
def live(tmp_path):
    """The real app on a local port, a LinkedIn stand-in, and a paired token. Yields helpers."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    app = create_app(data_file=tmp_path / "d.json", admin_password=ADMIN_PASSWORD)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    mock_server, mock_base = mock_linkedin.start()
    import urllib.request

    def call(path, method="GET", body=None, token=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", json.dumps(body).encode() if body is not None else None,
                                     {"Content-Type": "application/json", "Authorization": f"Bearer {token or owner}"}, method=method)
        return json.load(urllib.request.urlopen(req))
    owner = call("/api/login", "POST", {"email": "", "password": ADMIN_PASSWORD}, token="x")["token"]
    token = call("/api/extension/pair", "POST", {"label": "test agent"})["token"]
    store = app.state.store

    def run_agent(*extra, timeout=90):
        cmd = ["node", str(ROOT / "tools" / "linkedin_agent.mjs"), "--portal", f"http://127.0.0.1:{port}", "--token", token, "--linkedin-base", mock_base,
               "--profile", str(tmp_path / "profile"), "--headless", "--skip-login-check", "--once", "--settle", "300", *extra]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    yield type("Live", (), {"call": staticmethod(call), "store": store, "run_agent": staticmethod(run_agent), "owner": owner, "mock": mock_linkedin})
    server.should_exit = True; mock_server.shutdown(); thread.join(timeout=5)


ADMIN_PASSWORD = "admin-pass-123"
CSV = ("company,contact_name,title,email,phone,website,open_roles,company_size,country\n"
       "Acme Pvt Ltd,Ravi Kumar,Head of Talent Acquisition,ravi@acme.com,98765 43210,https://acme.com,12,200,IN\n"
       "Globex,Meera Rao,Founder,meera@globex.com,98765 43211,https://globex.com,3,50,IN\n"
       "Initech,Sam Jones,CEO,sam@initech.com,98765 43212,https://initech.com,3,50,IN\n")


def enrol(live, company, slug):
    """Put a lead on the LinkedIn sequence with a profile link."""
    lead = next(l for l in live.store.read("leads") if l["company"] == company)
    live.store.update_lead(lead["id"], lambda l: {**l, "linkedin_url": f"https://www.linkedin.com/in/{slug}"})
    live.call(f"/api/leads/{lead['id']}/linkedin/start", "POST")
    return lead["id"]


def seq_status(live, lead_id):
    return next(l for l in live.store.read("leads") if l["id"] == lead_id)["linkedin_seq"]["status"]


def open_up(live, **extra):
    live.call("/api/linkedin-agent", "PUT", {"enabled": True, "start_hour": 0, "end_hour": 24, "weekdays_only": False, "gap_minutes": 3, **extra})


@needs_browser
def test_agent_sends_a_connection_request_with_the_sequence_note(live):
    live.call("/api/leads/import", "POST", {"csv": CSV, "source": "apollo"})
    ravi = enrol(live, "Acme Pvt Ltd", "ravi-example")
    live.mock.install("ravi-example", "Ravi Kumar", "2nd", "connect")
    open_up(live)
    out = live.run_agent()
    assert out.returncode == 0, out.stdout + out.stderr
    sent = [e for e in live.mock.EVENTS if e["kind"] == "invite"]
    assert len(sent) == 1 and sent[0]["note"].startswith("Hi Ravi,") and len(sent[0]["note"]) <= 300
    assert seq_status(live, ravi) == "waiting_for_acceptance"
    log = live.call("/api/linkedin-agent")["log"]
    assert log[0]["kind"] == "connect" and log[0]["lead_id"] == ravi
    assert agent.control(live.store, account="test-sender")["wait_seconds"] > 0        # the fixed gap now applies to that account
    assert log[0].get("account") == "test-sender"


@needs_browser
def test_agent_notices_the_acceptance_and_then_sends_the_follow_up_message(live):
    live.call("/api/leads/import", "POST", {"csv": CSV, "source": "apollo"})
    meera = enrol(live, "Globex", "meera-example")
    li.mark_sent(live.store, meera, 1)
    live.store.update_lead(meera, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "last_sent_at": "2020-01-01T00:00:00+00:00"}})   # sent long ago
    live.mock.install("meera-example", "Meera Rao", "1st", "message")                    # she accepted: profile now shows 1st
    open_up(live)
    out = live.run_agent()
    assert out.returncode == 0, out.stdout + out.stderr
    assert seq_status(live, meera) != "waiting_for_acceptance"                            # the first run saw the 1st-degree badge and recorded it
    task = next(t for t in live.store.read("tasks") if t.get("lead_id") == meera and t.get("kind") == "linkedin" and t.get("status", "open") == "open")
    live.store.update("tasks", lambda rows: [{**t, "due_at": "2020-01-01T00:00:00+00:00"} if t["id"] == task["id"] else t for t in rows])
    live.call("/api/linkedin-agent", "PUT", {"gap_minutes": 3})
    live.store.update("settings", lambda s: {**s, "linkedin_agent_log": []})              # forget the gap so the second run may act at once
    out = live.run_agent()
    assert out.returncode == 0, out.stdout + out.stderr
    texts = [e["text"] for e in live.mock.EVENTS if e["kind"] == "message"]
    assert len(texts) == 1 and texts[0].startswith("Thanks for connecting, Meera")


@needs_browser
def test_agent_stops_itself_and_switches_off_when_linkedin_shows_a_security_check(live):
    live.call("/api/leads/import", "POST", {"csv": CSV, "source": "apollo"})
    enrol(live, "Initech", "blocked")
    open_up(live)
    out = live.run_agent()
    assert out.returncode == 3, out.stdout + out.stderr
    assert agent.paused(live.store).get("test-sender") is not None                  # only that LinkedIn account is paused
    assert "security check" in live.store.read("notifications")[-1]["text"]
    assert not [e for e in live.mock.EVENTS if e["kind"] in ("invite", "message")]


@needs_browser
def test_agent_does_nothing_while_the_switch_is_off_and_dry_run_clicks_nothing(live):
    live.call("/api/leads/import", "POST", {"csv": CSV, "source": "apollo"})
    ravi = enrol(live, "Acme Pvt Ltd", "ravi-example")
    live.mock.install("ravi-example", "Ravi Kumar", "2nd", "connect")
    out = live.run_agent()                                                                # switch is off
    assert out.returncode == 0 and "switch is off" in out.stdout and not live.mock.EVENTS
    open_up(live)
    out = live.run_agent("--dry")
    assert out.returncode == 0 and "DRY" in out.stdout and not live.mock.EVENTS
    assert seq_status(live, ravi) != "waiting_for_acceptance"                             # nothing was sent, so nothing was recorded as sent
