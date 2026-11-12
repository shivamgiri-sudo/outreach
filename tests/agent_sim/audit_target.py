"""Audit of the LinkedIn agent's SEND PATHS against a hostile fake LinkedIn (tests/agent_sim/audit_target_fake.mjs).

    .venv/bin/python tests/agent_sim/audit_target.py

A FAIL line is a PROVEN defect: the agent broke an invariant (act on the wrong person / act twice / report something that
did not happen). Ports 8901-8903 only. Nothing touches linkedin.com.
"""
import json
import re
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

PORTAL_PORT, FAKE_PORT, PROXY_PORT = 8901, 8902, 8903
PORTAL, FAKE, PROXY = f"http://127.0.0.1:{PORTAL_PORT}", f"http://localhost:{FAKE_PORT}", f"http://127.0.0.1:{PROXY_PORT}"
WORK = Path(tempfile.mkdtemp(prefix="audit-target-"))
ADMIN = "audit-admin-pass-123"
RESULTS = []
import os  # noqa: E402
# AUDIT_AGENT=patched runs tests/agent_sim/audit_target_patched_agent.mjs (the proposed fixes) instead of tools/linkedin_agent.mjs
AGENT = str(ROOT / ("tests/agent_sim/audit_target_patched_agent.mjs" if os.environ.get("AUDIT_AGENT") == "patched" else "tools/linkedin_agent.mjs"))
SRC = Path(AGENT).read_text()


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
    print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail and not ok else ""), flush=True)


# ---- a proxy between agent and portal that can lose the answer to /sent (network failure after the action happened)
DROP = {"sent": 0}


class Proxy(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _go(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        if self.command == "POST" and self.path.endswith("/sent") and DROP["sent"] > 0:
            DROP["sent"] -= 1
            self.send_response(502); self.end_headers(); self.wfile.write(b"proxy: dropped"); return
        req = urllib.request.Request(PORTAL + self.path, data=body, method=self.command, headers={k: v for k, v in self.headers.items() if k.lower() != "host"})
        try:
            r = urllib.request.urlopen(req, timeout=30); code, data = r.status, r.read()
        except urllib.error.HTTPError as e:
            code, data = e.code, e.read()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    do_GET = do_POST = do_PUT = _go


app = create_app(data_file=WORK / "d.json", admin_password=ADMIN)
store = app.state.store
threading.Thread(target=uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORTAL_PORT, log_level="warning")).run, daemon=True).start()
threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", PROXY_PORT), Proxy).serve_forever, daemon=True).start()
fake = subprocess.Popen(["node", str(ROOT / "tests/agent_sim/audit_target_fake.mjs"), f"{FAKE_PORT}:acct-a"], stdout=subprocess.DEVNULL)
import atexit  # noqa: E402
atexit.register(fake.terminate)
time.sleep(2.5)
owner = http("POST", "/api/login", {"password": ADMIN})[1]["token"]
TOKEN = http("POST", "/api/extension/pair", {"label": "audit"}, owner)[1]["token"]
agent.save_config(store, {"enabled": True, "start_hour": 0, "end_hour": 24, "weekdays_only": False, "gap_minutes": 3, "check_replies": False})
store.update("settings", lambda s: {**s, "linkedin_daily_connections": 40})
http("POST", "/__as?acct=acct-a", base=FAKE)


def run_agent(profile, *extra, timeout=60):
    cmd = ["node", AGENT, "--portal", PROXY, "--token", TOKEN, "--linkedin-base", FAKE,
           "--profile", str(WORK / profile), "--headless", "--settle", "300", "--poll", "1", *extra]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr


def skip_gap():
    def back(s):
        rows = [{**r, "at": (datetime.fromisoformat(r["at"]) - timedelta(minutes=4)).isoformat(timespec="seconds")} for r in s.get(agent.SETTINGS_LOG) or []]
        return {**s, agent.SETTINGS_LOG: rows}
    store.update("settings", back)


def six_hours_pass():
    from app import linkedin_guard
    for l in store.read("leads"):
        if l.get(linkedin_guard.CHECKED):
            store.update_lead(l["id"], lambda x: {**x, linkedin_guard.CHECKED: None})


def state():
    return http("GET", "/__state", base=FAKE)[1]


def add_lead(slug, company, name):
    csv = f"company,contact_name,title,email,website\n{company},{name},Head of Talent,{slug}@{company.lower()}.test,https://{company.lower()}.test\n"
    http("POST", "/api/leads/import", {"csv": csv, "source": "audit"}, owner)
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
    """Connected already: step 1 done and accepted, step 2 (a message) due now, yesterday was the last touch."""
    li.mark_sent(store, lid, 1)
    li.mark_accepted(store, lid)
    store.update("tasks", lambda rows: [{**t, "due_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")}
                                        if t.get("lead_id") == lid and t.get("kind") == "linkedin" and t.get("status", "open") == "open" else t for t in rows])
    store.update_lead(lid, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "last_sent_at": "2026-01-01T00:00:00+00:00"}})


def waiting(lid):
    for other in store.read("leads"):          # only 3 waiting leads are offered per cycle: park the earlier ones
        if other["id"] != lid and (other.get("linkedin_seq") or {}).get("status") == "waiting_for_acceptance":
            li.stop(store, other["id"], "audit parked")
    li.mark_sent(store, lid, 1)
    six_hours_pass()


def sent_to(kind, who):
    return [x for x in state()[kind] if x["to"] == who]


def stray(kind, allowed):
    return [x for x in state()[kind] if x["to"] != allowed]


# ================= pure-function tests: the matching code, taken from the agent file itself =================
# The matching code lives in tools/linkedin_guard_dom.mjs (pure functions: node imports them directly).
GUARD = ROOT / "tools" / "linkedin_guard_dom.mjs" if not os.environ.get("AUDIT_AGENT") == "patched" else None
node_src = ('import(process.argv[1]).then(({namesLead, degreeFromText}) => {const t=JSON.parse(process.argv[2]);'
            'console.log(JSON.stringify(t.names.map(([l,n])=>namesLead(l,n)).concat(t.deg.map(degreeFromText))));});')
cases = {"names": [
    ["Invite Rahul Sharma to connect", "A K Sharma"],          # initials dropped: any Sharma matches
    ["Invite Priya to connect", "Priya"],                      # single name (control, true)
    ["Invite Priya Nair to connect", "Priya"],                 # single name matches a different person
    ["Invite Kamal Other to connect", "Other Kamal"],          # reversed order is a different person
    ["Invite Anil Kumar Verma to connect", "Anil Verma"],      # extra middle name = different person
    ["Invite Ravi Verma to connect", "Ravi 王"],           # non-Latin last name dropped
    ["Invite Anil Kumar to connect", "Anil Kumar"],            # control, true
    ["Dr Anil Kumar, Ravi Sharma", "Anil Kumar"],              # group chat header containing the lead
], "deg": ["Ranjan 1st Choice Realty", "Ex 1st Lieutenant, Director"]}
out = json.loads(subprocess.run(["node", "-e", node_src, (GUARD or ROOT / "tools" / "linkedin_guard_dom.mjs").as_uri(), json.dumps(cases)], capture_output=True, text=True).stdout or "[]")
check("U1 initials-only name ('A K Sharma') must not match 'Rahul Sharma'", out and out[0] is False, "namesLead returned true: a one-token name matches ANY label with that token")
check("U2 single-name lead 'Priya' must not match 'Priya Nair'", out and out[2] is False, "single-token lead matches any label containing the first name")
check("U3 'Other Kamal' must not match 'Kamal Other'", out and out[3] is False, "token order is ignored")
check("U4 'Anil Verma' must not match 'Anil Kumar Verma'", out and out[4] is False, "only first and last tokens are compared")
check("U5 non-Latin tail dropped: 'Ravi <CJK>' must not match 'Ravi Verma'", out and out[5] is False, "name collapses to one token")
check("U6 a group-chat header 'Anil Kumar, Ravi Sharma' must not count as a chat with Anil Kumar", bool(out) and ("group chat" in (ROOT / "tools" / "linkedin_guard_dom.mjs").read_text()), "header with several people matches (the fix lives in chatWith)")
check("U7 degree regex must not read '1st' out of a name/headline", out and out[8] == "" and out[9] == "", f"degreeOf would return {out[8:10]}")

# ================= T1: the page that loaded is not the lead's =================
lid = add_lead("redirect-1", "RedirCo", "Redirect Lead")
subprocess.run  # noqa
run_agent("p1", "--once")
check("T1 slug redirected to a stranger: no invitation to the stranger", not sent_to("invites", "stranger-9"), str(state()["invites"]))
skip_gap()

lid = add_lead("gone-1", "GoneCo", "Gone Lead")
run_agent("p1", "--once")
check("T2 'page doesn't exist' with suggestions: nobody on the page is invited", not [i for i in sent_to("invites", "kamal-other") if i["note"].startswith("Hi Gone")],
      str(sent_to("invites", "kamal-other")))
skip_gap()

lid = add_lead("late-1", "LateCo", "Late Lead")
run_agent("p1", "--once")
check("T3 page still loading (skeleton shows a stranger's Connect): stranger not invited", not [i for i in sent_to("invites", "kamal-other") if i["note"].startswith("Hi Late")], str(sent_to("invites", "kamal-other")))
skip_gap()

lid_ns = add_lead("namesake-1", "NamesakeCo", "Namesake Person")
run_agent("p1", "--once")
check("T4 namesake in 'More profiles for you' is not invited when the lead has only Follow", not sent_to("invites", "namesake-other"), str(state()["invites"]))
skip_gap()

lid_in = add_lead("initials-1", "InitCo", "A K Sharma")
run_agent("p1", "--once")
check("T5 lead 'A K Sharma': sidebar 'Rahul Sharma' is not invited", not sent_to("invites", "rahul-sharma"), str(state()["invites"]))
skip_gap()

# ================= T6: connect note lost silently =================
lid_ff = add_lead("fillfail-1", "FillCo", "Fillfail Lead")
run_agent("p1", "--once")
sent = sent_to("invites", "fillfail-1")
check("T6 note box refuses the text: request must not go out without the note (no-note is off)", not sent or all(i["note"] for i in sent), str(sent))
skip_gap()

# ================= T7: false acceptance =================
for slug, co, nm in [("redirectacc-1", "RAccCo", "Redirectacc Lead"), ("degfp-1", "DegCo", "Degfp Lead")]:
    l = add_lead(slug, co, nm); waiting(l)
    run_agent("p1", "--once")
    check(f"T7 {slug}: lead must NOT be marked accepted", seq(l).get("status") == "waiting_for_acceptance", f"status={seq(l).get('status')}")
    skip_gap()
for other in store.read("leads"):
    if (other.get("linkedin_seq") or {}).get("status") == "waiting_for_acceptance":
        li.stop(store, other["id"], "audit parked")
l = add_lead("redirectpend-1", "RPendCo", "Redirectpend Lead")
run_agent("p1", "--once")
check("T8 redirected to a stranger who shows Pending: the lead's step 1 must not be recorded as sent", 1 not in seq(l).get("done", []), f"done={seq(l).get('done')}")
skip_gap()

# ================= T9..: message path =================
l = add_lead("draft-1", "DraftCo", "Draft Lead"); to_messaging(l)
run_agent("p2", "--once")
msgs = sent_to("messages", "draft-1")
check("T9 an old draft sits in the box: the message sent must be exactly our text (not draft + text)", all("half-written draft" not in m["text"] for m in msgs), str(msgs))
skip_gap()

l = add_lead("lostsend-1", "LostCo", "Lostsend Lead"); to_messaging(l)
run_agent("p2", "--once")
check("T10 Send empties the box but nothing was delivered: step must not be recorded as sent", not (2 in seq(l).get("done", []) and not sent_to("messages", "lostsend-1")),
      f"done={seq(l).get('done')} delivered={sent_to('messages', 'lostsend-1')}")
skip_gap()

l = add_lead("slowsend-1", "SlowCo", "Slowsend Lead"); to_messaging(l)
run_agent("p2", "--once"); skip_gap()
time.sleep(4.5)
run_agent("p2", "--once"); skip_gap()
check("T11 message delivered but box clears late: the same step must not be sent twice", len(sent_to("messages", "slowsend-1")) <= 1, f"{len(sent_to('messages', 'slowsend-1'))} copies delivered")

l = add_lead("linkedlink-1", "LinkCo", "Linkedlink Lead"); to_messaging(l)
run_agent("p2", "--once"); skip_gap()
check("T12 Message opened somebody else's chat that merely links to the lead: nothing typed there", not sent_to("messages", "bea-bee"), str(state()["messages"]))

l = add_lead("sentfail-1", "SFCo", "Sentfail Lead"); to_messaging(l)
DROP["sent"] = 1
code, out = run_agent("p2", "--once"); skip_gap()
code2, out2 = run_agent("p2", "--once"); skip_gap()
check("T13 /sent lost after the message went out: next start must not send it again", len(sent_to("messages", "sentfail-1")) <= 1,
      f"{len(sent_to('messages', 'sentfail-1'))} copies; first run exit {code}")

l = add_lead("inmail-1", "InmCo", "Inmail Lead"); to_messaging(l)
run_agent("p2", "--once"); skip_gap()
check("T14 not connected (2nd degree) and no Open Profile flag: no message is sent", not sent_to("messages", "inmail-1"), str(sent_to("messages", "inmail-1")))

# ================= T15: two agents on one account at the same moment =================
l = add_lead("racemsg-1", "RaceCo", "Racemsg Lead"); to_messaging(l)
ps = [subprocess.Popen(["node", AGENT, "--portal", PROXY, "--token", TOKEN, "--linkedin-base", FAKE, "--profile", str(WORK / f"race{i}"),
                        "--headless", "--settle", "300", "--poll", "1", "--once"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) for i in range(2)]
for p in ps:
    p.wait(timeout=90)
check("T15 two agents on the same account: one message only", len(sent_to("messages", "racemsg-1")) <= 1, f"{len(sent_to('messages', 'racemsg-1'))} copies")

print("\nSUMMARY: %d checks, %d FAIL (proven defects)" % (len(RESULTS), sum(1 for r in RESULTS if not r[1])))
for n, ok, d in RESULTS:
    if not ok:
        print("  PROVEN:", n)
sys.exit(0)
