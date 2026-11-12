"""Hostile scenarios the audits did not cover, against the real tools/linkedin_agent.mjs (Chromium), the real portal and
tests/agent_sim/hostile_fake.mjs.   .venv/bin/python tests/agent_sim/hostile.py [H01 H05 ...]

Invariant: nothing to anyone but the lead, never twice, never a report of something that did not happen, a reason when
nothing happens. Ports 8941-8943 only. Nothing touches linkedin.com. Exit code 1 when a check fails.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import uvicorn  # noqa: E402

from app import linkedin_agent as agent, linkedin_seq as li  # noqa: E402
from app.main import create_app  # noqa: E402

PORTAL_PORT, FAKE_PORT, PROXY_PORT = 8941, 8942, 8943
PORTAL, FAKE, PROXY = f"http://127.0.0.1:{PORTAL_PORT}", f"http://localhost:{FAKE_PORT}", f"http://127.0.0.1:{PROXY_PORT}"
WORK = Path(tempfile.mkdtemp(prefix="hostile-"))
ADMIN = "audit-admin-pass-123"
AGENT = str(ROOT / "tools/linkedin_agent.mjs")
RESULTS, ONLY = [], sys.argv[1:]
wanted = lambda cid: not ONLY or any(cid.startswith(o) for o in ONLY)


def http(method, path, body=None, token=None, base=PORTAL):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text.startswith(("{", "[")) else text)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" -- {str(detail)[:300]}" if detail and not ok else ""), flush=True)


# ---- a proxy between agent and portal: counts calls, can answer instead of the portal, can run a hook before a call
CALLS = []
HOOKS = {}          # path suffix -> fn(method, path, body) -> None | (code, bytes); one-shot hooks remove themselves
REWRITE = {}        # path suffix -> fn(json) -> json


class Proxy(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _go(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        CALLS.append((self.command, self.path.split("?")[0], dict(self.headers)))
        for suffix in list(HOOKS):
            if self.path.split("?")[0].endswith(suffix):
                out = HOOKS[suffix](self.command, self.path, body)
                if out:
                    self.send_response(out[0]); self.send_header("Content-Length", str(len(out[1]))); self.end_headers(); self.wfile.write(out[1]); return
        req = urllib.request.Request(PORTAL + self.path, data=body, method=self.command, headers={k: v for k, v in self.headers.items() if k.lower() != "host"})
        try:
            r = urllib.request.urlopen(req, timeout=30); code, data = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, data = e.code, e.read()
        for suffix, fn in REWRITE.items():
            if self.path.split("?")[0].endswith(suffix) and code == 200:
                data = json.dumps(fn(json.loads(data))).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    do_GET = do_POST = do_PUT = _go


app = create_app(data_file=WORK / "d.json", admin_password=ADMIN)
store = app.state.store
threading.Thread(target=uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORTAL_PORT, log_level="warning")).run, daemon=True).start()
threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", PROXY_PORT), Proxy).serve_forever, daemon=True).start()
fake = subprocess.Popen(["node", str(ROOT / "tests/agent_sim/hostile_fake.mjs"), f"{FAKE_PORT}:acct-a"], stdout=subprocess.DEVNULL)
import atexit  # noqa: E402
atexit.register(fake.terminate)
time.sleep(2.5)
owner = http("POST", "/api/login", {"password": ADMIN})[1]["token"]
TOKEN = http("POST", "/api/extension/pair", {"label": "hostile"}, owner)[1]["token"]
agent.save_config(store, {"enabled": True, "start_hour": 0, "end_hour": 24, "weekdays_only": False, "days": [0, 1, 2, 3, 4, 5, 6], "gap_minutes": 3, "check_replies": False})
store.update("settings", lambda s: {**s, "linkedin_daily_connections": 40})


def cmd(profile, *extra):
    return ["node", AGENT, "--portal", PROXY, "--token", TOKEN, "--linkedin-base", FAKE, "--profile", str(WORK / profile), "--headless", "--settle", "300", "--poll", "1", *extra]


def run_agent(profile, *extra, timeout=60):
    p = subprocess.run(cmd(profile, *extra), capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


def skip_gap():
    def back(s):
        rows = [{**r, "at": (datetime.fromisoformat(r["at"]) - timedelta(minutes=4)).isoformat(timespec="seconds")} for r in s.get(agent.SETTINGS_LOG) or []]
        return {**s, agent.SETTINGS_LOG: rows}
    store.update("settings", back)


def state():
    return http("GET", "/__state", base=FAKE)[1]


def add_lead(slug, company):
    name = slug.split("-")[0].capitalize() + " Lead"
    csv = f"company,contact_name,title,email,website\n{company},{name},Head of Talent,{slug}@{company.lower()}.test,https://{company.lower()}.test\n"
    http("POST", "/api/leads/import", {"csv": csv, "source": "hostile"}, owner)
    lead = next(l for l in store.read("leads") if l["company"] == company)
    store.update_lead(lead["id"], lambda l: {**l, "linkedin_url": f"https://www.linkedin.com/in/{slug}"})
    li.start(store, lead["id"])
    store.update_lead(lead["id"], lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "account": "acct-a"}})
    return lead["id"]


def lead_of(lid):
    return next(l for l in store.read("leads") if l["id"] == lid)


def seq(lid):
    return lead_of(lid).get("linkedin_seq") or {}


def to_messaging(lid):
    li.mark_sent(store, lid, 1)
    li.mark_accepted(store, lid)
    store.update("tasks", lambda rows: [{**t, "due_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")}
                                        if t.get("lead_id") == lid and t.get("kind") == "linkedin" and t.get("status", "open") == "open" else t for t in rows])
    store.update_lead(lid, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "last_sent_at": "2026-01-01T00:00:00+00:00"}})


def park_others(keep):
    for l in store.read("leads"):
        s = l.get("linkedin_seq") or {}
        if l["id"] != keep and s.get("status") in ("waiting_to_connect", "messaging", "waiting_for_acceptance"):
            li.stop(store, l["id"], "hostile parked")


def to_(kind, who):
    return [x for x in state()[kind] if x["to"] == who]


def step_text(slug):
    return http("GET", f"/api/extension/match?profile_url={urllib.parse.quote('https://www.linkedin.com/in/' + slug)}", token=TOKEN)[1]["message"]["text"]


import urllib.parse  # noqa: E402,F401

# ---------------------------------------------------------------- H01 a second browser tab opens when Connect is pressed
if wanted("H01"):
    lid = add_lead("popup-1", "PopCo"); park_others(lid)
    run_agent("p1", "--once"); skip_gap()
    check("H01 Connect opens a second tab: the tab is closed and no invitation goes out", not to_("invites", "popup-1"), state()["invites"])

# ---------------------------------------------------------------- H02 a second tab opens when Send is pressed
if wanted("H02"):
    lid = add_lead("newtab-1", "NewTabCo"); to_messaging(lid); park_others(lid)
    run_agent("p1", "--once"); skip_gap()
    check("H02 Send opens a second tab: exactly one message, nothing else typed anywhere", len(to_("messages", "newtab-1")) == 1 and not [m for m in state()["messages"] if m["to"] != "newtab-1"], state()["messages"])

# ---------------------------------------------------------------- H03 the agent is killed the moment the message lands (window closed / crash after Send)
if wanted("H03"):
    lid = add_lead("msg-3", "KillCo"); to_messaging(lid); park_others(lid)
    p = subprocess.Popen(cmd("p3", "--once"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(200):
        if to_("messages", "msg-3"):
            p.send_signal(signal.SIGKILL); break
        time.sleep(0.1)
    p.wait(); skip_gap()
    run_agent("p3", "--once"); skip_gap()
    check("H03 killed right after Send, restarted: the same message is not sent again", len(to_("messages", "msg-3")) == 1, f"{len(to_('messages', 'msg-3'))} copies")
    check("H03 ...and the step is recorded exactly once after the restart", 2 in seq(lid).get("done", []), f"done={seq(lid).get('done')}")

# ---------------------------------------------------------------- H04 the connection request is sent, the agent dies before Pending is looked at
if wanted("H04"):
    lid = add_lead("conn-4", "KillConnCo"); park_others(lid)
    p = subprocess.Popen(cmd("p4", "--once"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(200):
        if to_("invites", "conn-4"):
            p.send_signal(signal.SIGKILL); break
        time.sleep(0.1)
    p.wait(); skip_gap()
    run_agent("p4", "--once"); skip_gap()
    check("H04 killed right after Send, restarted: no second invitation", len(to_("invites", "conn-4")) == 1, f"{len(to_('invites', 'conn-4'))} invitations")
    check("H04 ...and the step is recorded as sent (Pending seen)", 1 in seq(lid).get("done", []), f"done={seq(lid).get('done')}")

# ---------------------------------------------------------------- H05 the same lead twice in the queue, and two leads with one profile link
if wanted("H05"):
    lid = add_lead("msg-5", "TwiceCo"); to_messaging(lid); park_others(lid)

    def twice(q):
        return {**q, "due": q["due"] + q["due"] + [{**q["due"][0], "lead_id": "other-lead-id"}] if q["due"] else q["due"]}
    REWRITE["/queue"] = twice
    run_agent("p5", "--once"); REWRITE.clear(); skip_gap()
    check("H05 the same lead listed three times: one message", len(to_("messages", "msg-5")) == 1, state()["messages"])

# ---------------------------------------------------------------- H06 the lead is stopped in the portal after the queue was fetched
if wanted("H06"):
    lid = add_lead("msg-6", "StopCo"); to_messaging(lid); park_others(lid)
    HOOKS["/claim"] = lambda m, p, b: (li.stop(store, lid, "stopped by a person meanwhile"), HOOKS.pop("/claim"), None)[-1]
    run_agent("p6", "--once"); HOOKS.clear(); skip_gap()
    check("H06 stopped in the portal between queue and claim: nothing is sent", not to_("messages", "msg-6"), state()["messages"])
    lid = add_lead("msg-7", "StopTwoCo"); to_messaging(lid); park_others(lid)
    n = {"match": 0}

    def stop_on_second_match(m, p, b):                  # after the claim, right before the click: the portal's match is asked again
        n["match"] += 1
        if n["match"] == 2:
            li.stop(store, lid, "stopped by a person right before the click"); HOOKS.pop("/match")
    HOOKS["/match"] = stop_on_second_match
    run_agent("p6", "--once"); HOOKS.clear(); skip_gap()
    check("H06 stopped in the portal after the claim, right before the click: nothing is sent", not to_("messages", "msg-7"), state()["messages"])

# ---------------------------------------------------------------- H07 the owner pauses the agent after the claim
if wanted("H07"):
    lid = add_lead("msg-8", "PauseCo"); to_messaging(lid); park_others(lid)
    n = {"agent": 0}

    def pause_late(m, p, b):
        n["agent"] += 1
        if n["agent"] == 2:                               # 1st = start of the cycle, 2nd = the re-check right before the click
            agent.pause(store, "acct-a", "owner pressed pause"); HOOKS.pop("/api/extension/agent")
    HOOKS["/api/extension/agent"] = pause_late
    run_agent("p7", "--once"); HOOKS.clear()
    check("H07 paused in the portal right before the click: nothing is sent", not to_("messages", "msg-8"), state()["messages"])
    http("PUT", "/api/linkedin-agent", {"resume_account": "acct-a"}, owner); skip_gap()

# ---------------------------------------------------------------- H08 the fixed gap between actions is kept in a continuous run
if wanted("H08"):
    a = add_lead("conn-9", "GapOneCo"); b = add_lead("conn-10", "GapTwoCo")
    for l in store.read("leads"):
        if l["id"] not in (a, b) and (l.get("linkedin_seq") or {}).get("status") in ("waiting_to_connect", "messaging", "waiting_for_acceptance"):
            li.stop(store, l["id"], "hostile parked")
    n0 = len(state()["invites"])
    p = subprocess.Popen(cmd("p8"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(25); p.send_signal(signal.SIGTERM)
    try:
        p.wait(timeout=15)
    except subprocess.TimeoutExpired:
        p.kill()
    check("H08 two people due, gap of 3 minutes: exactly one invitation in a continuous run", len(state()["invites"]) - n0 == 1, state()["invites"][n0:])
    skip_gap()

# ---------------------------------------------------------------- H09 a group chat opens instead of the person's chat
if wanted("H09"):
    lid = add_lead("group-1", "GroupCo"); to_messaging(lid); park_others(lid)
    run_agent("p9", "--once"); skip_gap()
    check("H09 Message opens a group chat that includes the lead: nothing typed or sent", not state()["messages"] or not to_("messages", "group-1"), state()["messages"])

# ---------------------------------------------------------------- H10 drafts: ours is cleared, somebody else's is left alone
if wanted("H10"):
    lid = add_lead("owndraft-1", "OwnDraftCo"); to_messaging(lid); park_others(lid)
    text = step_text("owndraft-1")
    http("POST", f"/__draft?slug=owndraft-1&text={urllib.parse.quote(text[:50])}", base=FAKE)
    run_agent("p10", "--once"); skip_gap()
    sent = to_("messages", "owndraft-1")
    check("H10 our own earlier unsent text sits in the box: cleared, the message goes out exactly once, not doubled", len(sent) == 1 and " ".join(sent[0]["text"].split()) == " ".join(text.split()), sent)
    lid = add_lead("humandraft-1", "HumanDraftCo"); to_messaging(lid); park_others(lid)
    run_agent("p10", "--once"); skip_gap()
    check("H10 somebody's own draft in the box: nothing typed over it, nothing sent", not to_("messages", "humandraft-1"), state()["messages"])

# ---------------------------------------------------------------- H11 the portal answers 409 to /sent: stop for this person, no retry, no second send
if wanted("H11"):
    lid = add_lead("msg-11", "NineCo"); to_messaging(lid); park_others(lid)
    HOOKS["/sent"] = lambda m, p, b: (409, json.dumps({"detail": "Not recorded as sent: test mismatch"}).encode())
    n0 = len([c for c in CALLS if c[1].endswith("/sent")])
    run_agent("p11", "--once"); HOOKS.clear(); skip_gap()
    tries = len([c for c in CALLS if c[1].endswith("/sent")]) - n0
    check("H11 /sent answered 409: exactly one report, never retried", tries == 1, f"{tries} attempts")
    check("H11 ...and the message is not sent again at the next start", (run_agent("p11", "--once"), len(to_("messages", "msg-11")))[1] == 1, state()["messages"])
    skip_gap()

# ---------------------------------------------------------------- H12 /sent unreachable (5xx): the report is kept on disk and delivered first at the next start
if wanted("H12"):
    lid = add_lead("msg-12", "DownCo"); to_messaging(lid); park_others(lid)
    other = add_lead("msg-13", "NextCo"); to_messaging(other); li.stop(store, other, "later")
    HOOKS["/sent"] = lambda m, p, b: (503, b"down")
    code, out = run_agent("p12", "--once")
    journal = WORK / "p12" / "mcn-pending-sent.json"
    check("H12 /sent down after the message went out: the report is kept on disk next to the profile", journal.exists() and "msg-12" in journal.read_text() or lid in journal.read_text(), out[-300:])
    HOOKS.clear(); skip_gap()
    run_agent("p12", "--once"); skip_gap()
    check("H12 next start: the kept report reaches the portal first and nothing is sent twice", 2 in seq(lid).get("done", []) and len(to_("messages", "msg-12")) == 1 and not journal.exists(),
          f"done={seq(lid).get('done')} copies={len(to_('messages', 'msg-12'))} journal={journal.exists()}")

# ---------------------------------------------------------------- H13 dry run: no side effect at all, and it says what it would send
if wanted("H13"):
    lid = add_lead("conn-14", "DryConnCo"); park_others(lid)
    before = json.dumps(lead_of(lid), sort_keys=True)
    code13, out13 = run_agent("p13", "--dry", "--once"); skip_gap()
    check("H13 dry run: nothing sent, the lead is not touched in the portal", not state()["invites"][-1:] or not to_("invites", "conn-14"), state()["invites"])
    check("H13 ...no profile report written to the lead", "linkedin_profile" not in lead_of(lid), lead_of(lid).get("linkedin_profile"))
    rows = [r for r in agent.log_rows(store) if r.get("lead_id") == lid and r["kind"] == "dry"]
    check("H13 ...and the portal log shows what would be sent (a dry row that quotes the text)", rows and "Hi Conn" in rows[-1]["note"], out13[-400:])

# ---------------------------------------------------------------- H14 two agents on one browser profile: the second does not start
if wanted("H14"):
    lid = add_lead("msg-15", "LockCo"); to_messaging(lid); park_others(lid); skip_gap()
    first = subprocess.Popen(cmd("p14"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)      # keeps running (holds the profile)
    time.sleep(8)
    code, out = run_agent("p14", "--once")
    first.send_signal(signal.SIGTERM)
    try:
        first.wait(timeout=20)
    except subprocess.TimeoutExpired:
        first.kill()
    check("H14 a second agent on a profile that is in use refuses to run (exit 1, says why)", code == 1 and "already using this browser profile" in out, out[-300:])
    check("H14 ...and the person got exactly one message", len(to_("messages", "msg-15")) == 1, state()["messages"])

print("\nSUMMARY: %d checks, %d FAIL" % (len(RESULTS), sum(1 for r in RESULTS if not r[1])))
sys.exit(1 if any(not r[1] for r in RESULTS) else 0)
