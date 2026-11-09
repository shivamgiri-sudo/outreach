"""Drive tools/linkedin_agent.mjs (real Chromium) against the fake LinkedIn and a real portal, scenario by scenario.

    .venv/bin/python tests/agent_sim/run_sim.py

The portal runs in this process (uvicorn thread) so the script can step time between actions (the fixed gap is
minutes) by moving the agent log back. Nothing here touches linkedin.com.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import uvicorn  # noqa: E402

from app import linkedin_agent as agent, linkedin_seq as li  # noqa: E402
from app.main import create_app  # noqa: E402

PORTAL_PORT, PORT_A, PORT_B = (int(p) for p in os.environ.get("AGENT_SIM_PORTS", "8811,8797,8796").split(","))   # override when busy
PORTAL, FAKE, FAKE_B = f"http://127.0.0.1:{PORTAL_PORT}", f"http://localhost:{PORT_A}", f"http://localhost:{PORT_B}"
BASE = {}
WORK = Path(tempfile.mkdtemp(prefix="agent-sim-"))
ADMIN = "sim-admin-pass-123"
RESULTS = []


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
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail and not ok else ""), flush=True)


# ---------------------------------------------------------------- setup
app = create_app(data_file=WORK / "d.json", admin_password=ADMIN)
store = app.state.store
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORTAL_PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
fake = subprocess.Popen(["node", str(ROOT / "tests/agent_sim/fake_linkedin.mjs"), f"{PORT_A}:acct-a,{PORT_B}:acct-b"], stdout=subprocess.DEVNULL)
import atexit  # noqa: E402
atexit.register(fake.terminate)
time.sleep(2.5)
owner = http("POST", "/api/login", {"password": ADMIN})[1]["token"]
TOKEN = http("POST", "/api/extension/pair", {"label": "sim"}, owner)[1]["token"]
agent.save_config(store, {"enabled": True, "start_hour": 0, "end_hour": 24, "weekdays_only": False, "gap_minutes": 3})
store.update("settings", lambda s: {**s, "linkedin_daily_connections": 40})

PW = str(ROOT / "web/node_modules/playwright/index.mjs")


def login_profile(profile, acct):
    """Point a browser profile at the fake LinkedIn port 'logged in' as acct (what a person does once in the agent window)."""
    if profile not in BASE:
        BASE[profile] = FAKE if not BASE else FAKE_B
    http("POST", f"/__as?acct={acct}", base=BASE[profile])


def run_agent(profile, *extra, timeout=45):
    cmd = ["node", str(ROOT / "tools/linkedin_agent.mjs"), "--portal", PORTAL, "--token", TOKEN, "--linkedin-base", BASE[profile],
           "--profile", str(profile), "--headless", "--settle", "300", "--poll", "1", *extra]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        print("AGENT TIMEOUT, output so far:\n" + out[-800:], flush=True)
        return 124, out
    return p.returncode, p.stdout + p.stderr


def skip_gap():
    """Move every agent log entry 4 minutes back so the fixed gap is over (time travel instead of waiting)."""
    def back(s):
        rows = [{**r, "at": (datetime.fromisoformat(r["at"]) - timedelta(minutes=4)).isoformat(timespec="seconds")} for r in s.get(agent.SETTINGS_LOG) or []]
        return {**s, agent.SETTINGS_LOG: rows}
    store.update("settings", back)


def six_hours_pass():
    """Acceptance checks are offered once per 6 hours per profile (app/linkedin_guard.py): time travel past that."""
    from app import linkedin_guard
    for l in store.read("leads"):
        if l.get(linkedin_guard.CHECKED):
            store.update_lead(l["id"], lambda x: {**x, linkedin_guard.CHECKED: None})


def fake_state():
    return http("GET", "/__state", base=FAKE)[1]


def add_lead(slug, company):
    csv = f"company,contact_name,title,email,website\n{company},{pretty(slug)},Head of Talent,{slug}@{company.lower()}.test,https://{company.lower()}.test\n"
    http("POST", "/api/leads/import", {"csv": csv, "source": "sim"}, owner)
    lead = next(l for l in store.read("leads") if l["company"] == company)
    store.update_lead(lead["id"], lambda l: {**l, "linkedin_url": f"https://www.linkedin.com/in/{slug}"})
    li.start(store, lead["id"])
    return lead["id"]


def pretty(slug):
    return " ".join(w.capitalize() for w in slug.split("-")[:-1]) + " Lead"


def seq(lead_id):
    return next(l for l in store.read("leads") if l["id"] == lead_id).get("linkedin_seq") or {}


def run_until_idle(profile, max_runs=8):
    out = ""
    for _ in range(max_runs):
        code, text = run_agent(profile, "--once")
        out += text
        skip_gap()
        if "nothing due" in text or code != 0:
            return code, out
    return 0, out


PROFILE_A, PROFILE_B = WORK / "prof-a", WORK / "prof-b"
login_profile(PROFILE_A, "acct-a")
login_profile(PROFILE_B, "acct-b")

# ---------------------------------------------------------------- S1: identity detection
code, out = run_agent(PROFILE_A, "--once", "--dry")
check("S1 agent reads which LinkedIn account is logged in", "acct-a" in out, out[-300:])
accts = {a["account"]: a for a in agent.status(store)["accounts"]}
check("S1 portal lists the account with its name", accts.get("acct-a", {}).get("name") == "Asha Account", str(accts))

# ---------------------------------------------------------------- S2: two accounts, three people — nobody invited twice
leads = [add_lead(f"connect-{i}", f"Firm{i}") for i in range(1, 4)]
for _ in range(4):
    run_agent(PROFILE_A, "--once"); skip_gap()
    run_agent(PROFILE_B, "--once"); skip_gap()
st = fake_state()
targets = [i["to"] for i in st["invites"]]
check("S2 every person got exactly one invitation", sorted(targets) == ["connect-1", "connect-2", "connect-3"], str(st["invites"]))
owners = {seq(l)["account"] for l in leads}
check("S2 work was shared between both accounts", owners == {"acct-a", "acct-b"}, str(owners))
check("S2 each invitation came from the lead's own account",
      all(next(i["from"] for i in st["invites"] if i["to"] == f"connect-{n}") == seq(l)["account"] for n, l in enumerate(leads, 1)))
check("S2 the note on the invitation is the A/B note with the first name",
      all(i["note"].startswith("Hi Connect") for i in st["invites"]), str([i["note"][:40] for i in st["invites"]]))
check("S2 status is waiting for acceptance", all(seq(l)["status"] == "waiting_for_acceptance" for l in leads))
c = agent.control(store, account="acct-a")["counts"]["connect_today"] + agent.control(store, account="acct-b")["counts"]["connect_today"]
check("S2 caps counted per account (3 requests in total)", c == 3, str(c))

# ---------------------------------------------------------------- S3: acceptance + follow-up from the right account only
first = leads[0]
own, other = seq(first)["account"], ({"acct-a", "acct-b"} - {seq(first)["account"]}).pop()
http("POST", f"/__accept?slug=connect-1&acct={own}", base=FAKE)
prof = {"acct-a": PROFILE_A, "acct-b": PROFILE_B}
run_agent(prof[other], "--once"); skip_gap()
check("S3 the other account does not mark someone else's connection accepted", seq(first)["status"] == "waiting_for_acceptance")
six_hours_pass()
run_agent(prof[own], "--once"); skip_gap()
check("S3 the owning account sees the acceptance", seq(first)["status"] == "messaging", seq(first).get("status"))
# make step 2 due now
store.update("tasks", lambda rows: [{**t, "due_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")}
                                    if t.get("lead_id") == first and t.get("kind") == "linkedin" and t.get("status", "open") == "open" else t for t in rows])
store.update_lead(first, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "last_sent_at": "2026-01-01T00:00:00+00:00"}})   # one touch a day: pretend it was yesterday
run_agent(prof[other], "--once"); skip_gap()
check("S3 the other account never messages the person", not fake_state()["messages"])
run_agent(prof[own], "--once"); skip_gap()
msgs = fake_state()["messages"]
check("S3 the owning account sends the follow-up message", len(msgs) == 1 and msgs[0]["from"] == own and "Thanks for connecting" in msgs[0]["text"], str(msgs))
check("S3 step 2 recorded as sent", 2 in seq(first).get("done", []), str(seq(first)))

# ---------------------------------------------------------------- S4: profile without a Connect button / already pending / Open Profile
f = add_lead("follow-1", "FollowCo"); p = add_lead("pending-1", "PendCo"); o = add_lead("open-1", "OpenCo")
run_until_idle(PROFILE_A)
check("S4 Follow-only profile is skipped with a reason, agent keeps running",
      any("no Connect button" in r["note"] for r in agent.log_rows(store)) and seq(f)["status"] == "waiting_to_connect")
check("S4 already-pending profile is recorded as sent, no second invite", seq(p)["status"] == "waiting_for_acceptance"
      and not any(i["to"] == "pending-1" for i in fake_state()["invites"]))
lead_o = next(l for l in store.read("leads") if l["id"] == o)
check("S4 Open Profile detected and stored", (lead_o.get("linkedin_profile") or {}).get("open_profile") is True, str(lead_o.get("linkedin_profile")))

# ---------------------------------------------------------------- S5: weekly invitation limit pauses only that account
lim = add_lead("limit-1", "LimitCo")
store.update_lead(lim, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "account": "acct-a"}})
code, out = run_agent(PROFILE_A, "--once"); skip_gap()
held = agent.paused(store).get("acct-a")
check("S5 weekly invitation limit stops account A at once", code == 3 and held is not None, out[-300:])
check("S5 account B keeps working", agent.control(store, account="acct-b")["active_now"] and agent.config(store)["enabled"])
http("PUT", "/api/linkedin-agent", {"resume_account": "acct-a"}, owner)
store.update_lead(lim, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "status": "stopped", "stopped": "sim"}})
store.update("tasks", lambda rows: [{**t, "status": "done", "done": True} if t.get("lead_id") == lim else t for t in rows])

# ---------------------------------------------------------------- S6: security checkpoint pauses only that account
cp = add_lead("checkpoint-1", "CheckCo")
store.update_lead(cp, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "account": "acct-b"}})
code, out = run_agent(PROFILE_B, "--once"); skip_gap()
check("S6 a security check pauses account B", code == 3 and agent.paused(store).get("acct-b") is not None, out[-300:])
check("S6 account A is not paused", agent.paused(store).get("acct-a") is None)
store.update_lead(cp, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "status": "stopped", "stopped": "sim"}})
store.update("tasks", lambda rows: [{**t, "status": "done", "done": True} if t.get("lead_id") == cp else t for t in rows])
http("PUT", "/api/linkedin-agent", {"resume_account": "acct-b"}, owner)

# ---------------------------------------------------------------- S7: LinkedIn will not allow a note
nn = add_lead("nonote-1", "NoNoteCo")
store.update_lead(nn, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "account": "acct-a"}})
run_agent(PROFILE_A, "--once"); skip_gap()
check("S7 without a note allowed, nothing is sent and it says why", not any(i["to"] == "nonote-1" for i in fake_state()["invites"])
      and any("will not let this account add a note" in r["note"] for r in agent.log_rows(store)))
run_agent(PROFILE_A, "--once", "--allow-no-note"); skip_gap()
check("S7 with --allow-no-note it sends without a note", any(i["to"] == "nonote-1" and i["note"] == "" for i in fake_state()["invites"]))

# ---------------------------------------------------------------- S8: someone logs the window in to another account
x = add_lead("connect-9", "SwitchCo")
login_profile(PROFILE_A, "acct-c")
code, out = run_agent(PROFILE_A, "--once"); skip_gap()
check("S8 the agent notices the new account and works as it", "acct-c" in out and seq(x).get("account") in ("acct-c", None), out[-300:])
check("S8 people taken by the old account stay with it", all(seq(l)["account"] in ("acct-a", "acct-b") for l in leads))

# ---------------------------------------------------------------- S9: logged out of LinkedIn
login_profile(PROFILE_B, "")      # signed out
code, out = run_agent(PROFILE_B, "--once", "--skip-login-check")
check("S9 a logged-out window stops safely (no clicks)", code == 3 and "login" in out.lower(), out[-300:])

# ---------------------------------------------------------------- S10: portal switch off / outside hours
agent.save_config(store, {"enabled": False})
code, out = run_agent(PROFILE_A, "--once")
check("S10 switch off: the agent does nothing", "switch is off" in out)
agent.save_config(store, {"enabled": True, "start_hour": 1, "end_hour": 2})
code, out = run_agent(PROFILE_A, "--once")
hour = datetime.now(timezone(timedelta(hours=5, minutes=30))).hour
check("S10 outside working hours: nothing happens", ("outside working hours" in out) or hour == 1)
agent.save_config(store, {"start_hour": 0, "end_hour": 24})

# ---------------------------------------------------------------- S11: caps per account
login_profile(PROFILE_B, "acct-b")
store.update("settings", lambda s: {**s, "linkedin_daily_connections": 1})
y = add_lead("connect-11", "CapCo")
code, out = run_agent(PROFILE_B, "--once"); skip_gap()   # B already used >=1 today in S2
check("S11 an account at its daily cap sends nothing more", not any(i["to"] == "connect-11" and i["from"] == "acct-b" for i in fake_state()["invites"]), out[-200:])

# ---------------------------------------------------------------- S12: other people's buttons on the page are never pressed
check("S12 no invitation or message ever went to someone else on the page (people you may know)",
      not any(i["to"] == "kamal-other" for i in fake_state()["invites"]) and not any(m["to"] == "kamal-other" for m in fake_state()["messages"]),
      str([i for i in fake_state()["invites"] if i["to"] == "kamal-other"]))

# ---------------------------------------------------------------- S13: they answered in the chat: the next step is not sent
login_profile(PROFILE_A, "acct-a")
store.update("settings", lambda s: {**s, "linkedin_daily_connections": 40})
agent.save_config(store, {"check_replies": False})               # only the read before sending is tested here
http("POST", f"/__reply?acct={own}&slug=connect-1&text=" + "Sounds+interesting%2C+call+me+Monday", base=FAKE)
store.update("tasks", lambda rows: [{**t, "due_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")}
                                    if t.get("lead_id") == first and t.get("kind") == "linkedin" and t.get("status", "open") == "open" else t for t in rows])
store.update_lead(first, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "last_sent_at": "2026-01-01T00:00:00+00:00"}})
before = len(fake_state()["messages"])
run_agent(prof[own], "--once"); skip_gap()
lead_1 = next(l for l in store.read("leads") if l["id"] == first)
check("S13 a person who answered in the chat is not sent the next step", len(fake_state()["messages"]) == before, str(fake_state()["messages"][before:]))
check("S13 the sequence stops as replied", seq(first).get("stopped") == "replied", str(seq(first)))
check("S13 their words reach the lead (Replies)", any(e.get("kind") == "reply" and "call me Monday" in (e.get("text") or "") for e in lead_1["timeline"]),
      str(lead_1["timeline"][-3:]))
check("S13 the owner gets a 'New reply' or 'Lead said yes' alert", any(n["kind"] in ("alert:reply", "alert:interested") and n.get("lead_id") == first
                                                                      for n in store.read("notifications")))

# ---------------------------------------------------------------- S14: acceptance raises an alert with the first message date
fl = add_lead("first-1", "FirstCo")
store.update_lead(fl, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "account": "acct-a"}})
agent.save_config(store, {"check_replies": True})
run_agent(PROFILE_A, "--once"); skip_gap()
check("S14 an already-connected person is marked accepted", seq(fl).get("status") == "messaging", str(seq(fl)))
check("S14 'Accepted on LinkedIn' alert with the first-message date",
      any(n["kind"] == "alert:li_accepted" and n.get("lead_id") == fl and "first message is scheduled" in n["text"] for n in store.read("notifications")))

# ---------------------------------------------------------------- S15: a reply between steps is seen from the messaging list
http("POST", "/__reply?acct=acct-a&slug=first-1&text=" + "Please+send+details+on+email", base=FAKE)
run_agent(PROFILE_A, "--once"); skip_gap()
lead_f = next(l for l in store.read("leads") if l["id"] == fl)
check("S15 the messaging list shows their reply and the sequence stops", seq(fl).get("stopped") == "replied", str(seq(fl)))
check("S15 the reply text is on the lead", any(e.get("kind") == "reply" and "send details" in (e.get("text") or "") for e in lead_f["timeline"]))
check("S15 'What it did lately' says who replied", any(r["kind"] == "replied" and "replied on LinkedIn" in r["note"] for r in agent.log_rows(store)))

# ---------------------------------------------------------------- S16: daily auto-start
csv = "company,contact_name,title,email,website\nAutoCo,Connect Lead,CHRO,auto@autoco.test,https://autoco.test\n"
http("POST", "/api/leads/import", {"csv": csv, "source": "sim"}, owner)
auto = next(l for l in store.read("leads") if l["company"] == "AutoCo")["id"]
store.update_lead(auto, lambda l: {**l, "linkedin_url": "https://www.linkedin.com/in/connect-77"})
run_agent(PROFILE_A, "--once"); skip_gap()
check("S16 auto-start off: nobody is started", not seq(auto))
http("PUT", "/api/linkedin/settings", {"linkedin_auto_start": True}, owner)
code, out16 = run_agent(PROFILE_A, "--once"); skip_gap()
check("S16 auto-start on: the suggested person is put on the sequence", seq(auto).get("auto_started") is True, str(seq(auto)))
check("S16 and the agent sends their connection request", any(i["to"] == "connect-77" for i in fake_state()["invites"]), out16[-600:])
check("S16 'What it did lately' records the automatic start", any(r["kind"] == "auto_start" for r in agent.log_rows(store)))

# ---------------------------------------------------------------- S17: note limit shown as a Premium offer: no hang, sent bare
up = add_lead("upsell-1", "UpsellCo")
store.update_lead(up, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "account": "acct-a"}})
agent.save_config(store, {"connect_without_note": False})
t0 = time.time(); code, out17 = run_agent(PROFILE_A, "--once"); skip_gap()
check("S17 switch off: nothing sent, it says why, and it does not hang", not any(i["to"] == "upsell-1" for i in fake_state()["invites"])
      and time.time() - t0 < 40 and any("will not let this account add a note" in r["note"] for r in agent.log_rows(store)), out17[-400:])
agent.save_config(store, {"connect_without_note": True})
code, out17 = run_agent(PROFILE_A, "--once"); skip_gap()
inv = [i for i in fake_state()["invites"] if i["to"] == "upsell-1"]
check("S17 switch on: the Premium offer is closed and the request goes without a note", len(inv) == 1 and inv[0]["note"] == "", out17[-500:])
check("S17 the log says it went without a note", any("without a note" in r["note"] and r.get("lead_id") == up for r in agent.log_rows(store)))

# ---------------------------------------------------------------- S18: a lead assigned to account B is never contacted by A
asg = add_lead("connect-18", "AssignCo")
code18 = http("PUT", f"/api/leads/{asg}/linkedin-account", {"account": "acct-b"}, owner)[0]
login_profile(PROFILE_B, "acct-b")
run_until_idle(PROFILE_A)
check("S18 account A never invites a person assigned to account B", code18 == 200
      and not any(i["to"] == "connect-18" for i in fake_state()["invites"]) and seq(asg).get("account") in (None, ""), str(seq(asg)))
run_until_idle(PROFILE_B)
inv18 = [i for i in fake_state()["invites"] if i["to"] == "connect-18"]
check("S18 account B sends the request and keeps the person", len(inv18) == 1 and inv18[0]["from"] == "acct-b"
      and seq(asg).get("account") == "acct-b", str(inv18) + str(seq(asg)))

# ---------------------------------------------------------------- done
fake.terminate()
server.should_exit = True
passed = sum(ok for _, ok, _ in RESULTS)
print(f"\n{passed}/{len(RESULTS)} scenario checks passed")
shutil.rmtree(WORK, ignore_errors=True)
sys.exit(0 if passed == len(RESULTS) else 1)
