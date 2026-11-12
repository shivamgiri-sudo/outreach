"""Audit: drive tools/linkedin_agent.mjs against a HOSTILE fake LinkedIn (tests/agent_sim/audit_ui_fake.mjs).

    .venv/bin/python tests/agent_sim/audit_ui.py [only-id-prefix ...]

Invariant per scenario: nothing is sent to anyone but the intended lead; no false "sent"/"accepted"; no loop or cap burn; a
human-readable reason is logged when nothing happened. A violated invariant is a FAIL (exit code 1). Ports 8921-8929 only.
Nothing here touches linkedin.com.  Each check id (U-..) is the finding id in audit-reports/ui.md.
"""
import csv
import io
import json
import os
import shutil
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

_B = int(os.environ.get("AUDIT_UI_PORT_BASE", "8921"))      # other runs at the same time: set a different base (4 consecutive ports)
PORTAL_PORT, PORT_A, PORT_H, PROXY_PORT = _B, _B + 1, _B + 2, _B + 3
# AUDIT_AGENT=patched runs tests/agent_sim/audit_ui_agent_patched.mjs (the agent with every proposed fix, built by audit_ui_make_patched.py)
# AUDIT_AGENT=head runs the audited original (git HEAD copy, built by audit_ui_make_patched.py); unset runs tools/linkedin_agent.mjs as it is now.
AGENT = ROOT / {"patched": "tests/agent_sim/audit_ui_agent_patched.mjs", "head": "tests/agent_sim/audit_ui_agent_head.mjs"}.get(os.environ.get("AUDIT_AGENT", ""), "tools/linkedin_agent.mjs")
PORTAL, FAKE_A, FAKE_H, PROXY = (f"http://127.0.0.1:{PORTAL_PORT}", f"http://localhost:{PORT_A}", f"http://localhost:{PORT_H}", f"http://127.0.0.1:{PROXY_PORT}")
WORK = Path(tempfile.mkdtemp(prefix="audit-ui-"))
ADMIN = "audit-admin-pass-123"
RESULTS = []
ONLY = sys.argv[1:]


def http(method, path, body=None, token=None, base=PORTAL):
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text.startswith(("{", "[")) else text)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def check(cid, name, ok, detail=""):
    RESULTS.append((cid, name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + f"{cid} {name}" + (f" -- {detail}" if detail and not ok else ""), flush=True)


def wanted(cid):
    return not ONLY or any(cid.startswith(o) for o in ONLY)


# ---------------------------------------------------------------- setup: portal in this process, fake LinkedIn, fault-injecting proxy
app = create_app(data_file=WORK / "d.json", admin_password=ADMIN)
store = app.state.store
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORTAL_PORT, log_level="warning"))
threading.Thread(target=server.run, daemon=True).start()
fake = subprocess.Popen(["node", str(ROOT / "tests/agent_sim/audit_ui_fake.mjs"), f"{PORT_A}:acct-a,{PORT_H}:acct-hi"], stdout=subprocess.DEVNULL)
import atexit  # noqa: E402
atexit.register(fake.terminate)
time.sleep(2.5)

FAIL_NEXT = {"sent": 0, "event": 0}      # the proxy answers 500 to the next N POST .../sent (after nothing reached the portal)
PROXY_LOG = []


class Proxy(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _go(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        if self.command == "POST" and self.path.endswith("/sent") and FAIL_NEXT["sent"] > 0:
            FAIL_NEXT["sent"] -= 1
            PROXY_LOG.append(("500", self.path))
            self.send_response(500); self.end_headers(); self.wfile.write(b"upstream error"); return
        req = urllib.request.Request(PORTAL + self.path, data=body, method=self.command, headers={k: v for k, v in self.headers.items() if k.lower() != "host"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read(); self.send_response(r.status)
        except urllib.error.HTTPError as e:
            data = e.read(); self.send_response(e.code)
        self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    do_GET = do_POST = do_PUT = _go


proxy_srv = ThreadingHTTPServer(("127.0.0.1", PROXY_PORT), Proxy)
threading.Thread(target=proxy_srv.serve_forever, daemon=True).start()

owner = http("POST", "/api/login", {"password": ADMIN})[1]["token"]
TOKEN = http("POST", "/api/extension/pair", {"label": "audit"}, owner)[1]["token"]
agent.save_config(store, {"enabled": True, "start_hour": 0, "end_hour": 24, "weekdays_only": False, "gap_minutes": 3, "check_replies": False,
                          "connect_without_note": False, "find_profiles": False, "daily_connections": 40, "weekly_connections": 100})
store.update("settings", lambda s: {**s, "linkedin_daily_connections": 40})
PROFILE_A, PROFILE_H = WORK / "prof-a", WORK / "prof-hi"
BASE = {PROFILE_A: FAKE_A, PROFILE_H: FAKE_H}
http("POST", "/__as?acct=acct-a", base=FAKE_A)
http("POST", "/__as?acct=acct-hi", base=FAKE_H)


def run_agent(profile, *extra, timeout=60, portal=PORTAL):
    cmd = ["node", str(AGENT), "--portal", portal, "--token", TOKEN, "--linkedin-base", BASE[profile],
           "--profile", str(profile), "--headless", "--settle", "300", "--poll", "1", *extra]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or b"").decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return 124, out
    return p.returncode, p.stdout + p.stderr


def run_for(profile, seconds, *extra, portal=PORTAL):
    """A long-running agent (several cycles, state kept in its memory), gap skipped every 1.5 s, then stopped with SIGTERM."""
    cmd = ["node", str(AGENT), "--portal", portal, "--token", TOKEN, "--linkedin-base", BASE[profile],
           "--profile", str(profile), "--headless", "--settle", "300", "--poll", "1", *extra]
    out = tempfile.TemporaryFile(mode="w+")
    p = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, text=True)
    end = time.time() + seconds
    while time.time() < end and p.poll() is None:
        time.sleep(1.5); skip_gap()
    code = p.poll()
    if code is None:
        p.send_signal(signal.SIGTERM)
        try: p.wait(15)
        except subprocess.TimeoutExpired: p.kill()
    out.seek(0)
    return (code if code is not None else "stopped"), out.read()


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


def fs():
    return http("GET", "/__state", base=FAKE_A)[1]


COUNTER = [0]


def fresh(**cfg):
    """Quiet portal: every earlier lead stopped, log cleared, accounts resumed, fake state reset."""
    http("POST", "/__reset", base=FAKE_A)
    store.update("tasks", lambda rows: [{**t, "status": "done", "done": True} if t.get("kind") == "linkedin" else t for t in rows])
    for l in store.read("leads"):
        if (l.get("linkedin_seq") or {}).get("status") not in (None, "stopped", "finished"):
            store.update_lead(l["id"], lambda x: {**x, "linkedin_seq": {**x["linkedin_seq"], "status": "stopped", "stopped": "audit"}})
    store.update("settings", lambda s: {**s, agent.SETTINGS_LOG: []})
    for acct in ("acct-a", "acct-hi"):
        http("PUT", "/api/linkedin-agent", {"resume_account": acct}, owner)
    agent.save_config(store, {"enabled": True, "check_replies": False, "connect_without_note": False, "find_profiles": False, **cfg})
    store.update("settings", lambda s: {k: v for k, v in s.items() if k != "linkedin_connect_variants"})


def add_lead(slug, name, acct="acct-a", company=None):
    COUNTER[0] += 1
    company = company or f"AuditCo{COUNTER[0]}"
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["company", "contact_name", "title", "email", "website"])
    w.writerow([company, name, "Head of Talent", f"{slug}@{company.lower()}.test", f"https://{company.lower()}.test"])
    http("POST", "/api/leads/import", {"csv": buf.getvalue(), "source": "audit"}, owner)
    lead = next(l for l in store.read("leads") if l["company"] == company)
    store.update_lead(lead["id"], lambda l: {**l, "linkedin_url": f"https://www.linkedin.com/in/{slug}"})
    li.start(store, lead["id"])
    store.update_lead(lead["id"], lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "account": acct}})
    return lead["id"]


def lead_of(lid):
    return next(l for l in store.read("leads") if l["id"] == lid)


def seq(lid):
    return lead_of(lid).get("linkedin_seq") or {}


def rows(lid=None, kind=None):
    return [r for r in agent.log_rows(store) if (lid is None or r.get("lead_id") == lid) and (kind is None or r["kind"] == kind)]


def notes(lid):
    return " | ".join(r["note"] for r in rows(lid))


def make_due(lid):
    store.update("tasks", lambda rs: [{**t, "due_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")}
                                      if t.get("lead_id") == lid and t.get("kind") == "linkedin" and t.get("status", "open") == "open" else t for t in rs])
    store.update_lead(lid, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "last_sent_at": "2026-01-01T00:00:00+00:00"}})


def cycles(profile, n=3, **kw):
    out, code = "", 0
    for _ in range(n):
        code, text = run_agent(profile, "--once", **kw); out += text; skip_gap()
        if "nothing due" in text or code not in (0,):
            break
    return code, out


def invites(slug=None):
    return [i for i in fs()["invites"] if slug is None or i["to"] == slug]


def msgs(slug=None):
    return [m for m in fs()["messages"] if slug is None or m["to"] == slug]


def to_messaging(lid, slug, name_acct="acct-a", profile=PROFILE_A, via_accept=False):
    """Bring a lead to 'step 2 due': connect (or already connected), optional accept, then make it due."""
    cycles(profile, 2)
    if via_accept:
        http("POST", f"/__accept?slug={slug}&acct={name_acct}", base=FAKE_A); six_hours_pass()
        run_agent(profile, "--once"); skip_gap()
    make_due(lid)


# ================================================================= scenarios
def sc_timing():
    # U01 slow hydration: the top card and the buttons appear 4 s after domcontentloaded
    if wanted("U01"):
        fresh(); lid = add_lead("slowhydr-1", "Slowhydr Lead")
        cycles(PROFILE_A, 2)
        sent = invites("slowhydr-1"); n = notes(lid)
        check("U01a", "slow hydration (4 s): the request goes out, or the reason says the page was not ready",
              bool(sent) or any(w in n.lower() for w in ("not loaded", "still loading", "did not finish loading", "not ready")), n[:200])
    # U02 acceptance missed when the card renders late
    if wanted("U02"):
        fresh(); lid = add_lead("slowfirst-1", "Slowfirst Lead")
        http("POST", "/__cfg?slug=slowfirst-1", {"degree": "2nd", "hydr": 0}, base=FAKE_A)
        cycles(PROFILE_A, 2)
        http("POST", "/__accept?slug=slowfirst-1&acct=acct-a", base=FAKE_A)
        http("POST", "/__cfg?slug=slowfirst-1", {"hydr": 4000}, base=FAKE_A)
        six_hours_pass(); run_agent(PROFILE_A, "--once")
        check("U02a", "slow hydration: an accepted connection is still detected as accepted", seq(lid).get("status") == "messaging",
              f"status={seq(lid).get('status')} profile={lead_of(lid).get('linkedin_profile')}")
        check("U02b", "slow hydration: a late card is not stored as 'profiled' with no degree (open_profile false for good)",
              not (lead_of(lid).get("linkedin_profile") or {}).get("degree") == "" , str(lead_of(lid).get("linkedin_profile")))
    # U03 skeleton / promo section first in <main>: topCard() is the wrong section
    if wanted("U03"):
        fresh(); lid = add_lead("skel-1", "Skel Lead")
        http("POST", "/__cfg?slug=skel-1", {"degree": "2nd"}, base=FAKE_A)
        cycles(PROFILE_A, 2)
        http("POST", "/__accept?slug=skel-1&acct=acct-a", base=FAKE_A); six_hours_pass(); run_agent(PROFILE_A, "--once")
        check("U03a", "a promo/skeleton <section> before the top card: acceptance is still seen", seq(lid).get("status") == "messaging", f"status={seq(lid).get('status')}")


def sc_names():
    if wanted("U04"):
        fresh(); lid = add_lead("credname-1", "Rahul Sharma")
        cycles(PROFILE_A, 2)
        check("U04a", "h1 'Rahul Sharma, MBA (He/Him) <emoji>': the request goes to this profile only", [i["to"] for i in invites()] == ["credname-1"], str(invites()))
        http("POST", "/__accept?slug=credname-1&acct=acct-a", base=FAKE_A); six_hours_pass(); run_agent(PROFILE_A, "--once"); make_due(lid)
        run_agent(PROFILE_A, "--once")
        check("U04b", "same profile: the follow-up goes into the chat with this person", [m["to"] for m in msgs()] == ["credname-1"], str(msgs())[:200])
    if wanted("U05"):
        fresh(); lid = add_lead("honorific-1", "Dr Rahul Sharma")
        cycles(PROFILE_A, 2)
        check("U05a", "lead 'Dr Rahul Sharma' (page: Rahul Sharma): the request is sent", bool(invites("honorific-1")), notes(lid)[:200])
        fresh(); lid = add_lead("honorific-2", "CA Amit Shah")
        http("POST", "/__cfg?slug=honorific-2", {"name": "Amit Shah"}, base=FAKE_A)
        cycles(PROFILE_A, 2)
        check("U05b", "lead 'CA Amit Shah' (honorific + abbreviation): the request is sent", bool(invites("honorific-2")), notes(lid)[:200])
    if wanted("U06"):
        fresh(); lid = add_lead("nonlatin-1", "राहुल शर्मा")
        cycles(PROFILE_A, 2)
        n = notes(lid)
        check("U06a", "non-Latin name: nothing sent to anyone else and a reason is logged", not [i for i in invites() if i["to"] != "nonlatin-1"] and bool(n), n[:200])
        check("U06b", "non-Latin name: the request is sent to the right profile (or the reason names the real cause)", bool(invites("nonlatin-1")) or "non-latin" in n.lower() or "script" in n.lower(), n[:200])
    if wanted("U07"):
        fresh(); lid = add_lead("single-1", "Priya")
        http("POST", "/__cfg?slug=single-1", {"primary": "follow"}, base=FAKE_A)      # no Connect on her own card; 'people you may know' shows Priya Nair
        cycles(PROFILE_A, 2)
        check("U07a", "single-name lead 'Priya' with only a namesake Connect on the page: nothing sent to Priya Nair", not invites("priya-nair-9"), str(invites()))
    if wanted("U08"):
        fresh(); lid = add_lead("namesake-1", "Amit Sharma")
        cycles(PROFILE_A, 2)
        check("U08a", "two people called Amit Sharma (Connect hidden under More on the lead's card): nothing sent to the namesake",
              not invites("amit-sharma-77"), f"{invites()} | {notes(lid)[:200]}")
        check("U08b", "namesake case: if an invite went out anywhere the log does not say 'nothing was sent'",
              not (invites() and "nothing was sent" in notes(lid)), f"{invites()} | {notes(lid)[:200]}")
    if wanted("U09"):
        fresh(); lid = add_lead("firstonly-1", "Rahul Sharma")
        cycles(PROFILE_A, 2)
        check("U09a", "aria-labels with the first name only ('Invite Rahul to connect'): request sent or reason names the label format", bool(invites("firstonly-1")), notes(lid)[:200])
    if wanted("U10"):
        fresh(); lid = add_lead("hindi-1", "Rahul Sharma", acct="acct-hi")
        _c, out_u10 = cycles(PROFILE_H, 2)
        n = " | ".join(r["note"] for r in agent.log_rows(store)) + " || " + out_u10
        check("U10a", "LinkedIn in Hindi: nothing is sent wrongly", not [i for i in invites() if i["to"] != "hindi-1"])
        check("U10b", "LinkedIn in Hindi: the reason tells the owner the page is not in English", any(w in n.lower() for w in ("language", "english", "hindi")), n[:200])
    if wanted("U11"):
        fresh(); lid = add_lead("member-1", "Rahul Sharma")
        cycles(PROFILE_A, 2)
        n = notes(lid)
        check("U11a", "private profile (h1 'LinkedIn Member'): nothing sent to anyone else, a reason is logged", not [i for i in invites() if i["to"] != "member-1"] and bool(n), n[:200])
    if wanted("U12"):
        fresh(); lid = add_lead("wrongperson-1", "Rahul Sharma")
        cycles(PROFILE_A, 2)
        check("U12a", "the link opens a DIFFERENT person (Sunita Rao); buttons carry no name: no request is sent to her", not invites("wrongperson-1"), f"{invites()}")
    if wanted("U13"):
        fresh(); lid = add_lead("redirect-1", "Rahul Sharma")
        cycles(PROFILE_A, 2)
        check("U13a", "the link redirects to another profile: no request is sent there", not invites("wrongperson-9"), f"{invites()}")
    if wanted("U14"):
        fresh(); lid = add_lead("nobadge-1", "Nobadge Lead")
        cycles(PROFILE_A, 2)
        st = seq(lid)
        check("U14a", "no degree badge + company chip '1st Choice Staffing': it is NOT reported as already connected / sent without an invite",
              bool(invites("nobadge-1")) or not (st.get("done") or st.get("status") in ("messaging", "waiting_for_acceptance")), f"status={st.get('status')} done={st.get('done')} invites={invites()}")


def sc_invite_box():
    if wanted("U15"):
        fresh(); lid = add_lead("instant-1", "Instant Lead")
        run_agent(PROFILE_A, "--once"); skip_gap()
        n1 = notes(lid)
        check("U15a", "Connect sends at once (no box): the log must not claim 'nothing was sent'", not (invites("instant-1") and "nothing was sent" in n1), f"{len(invites('instant-1'))} invites | {n1[:200]}")
        run_agent(PROFILE_A, "--once"); skip_gap()
        check("U15b", "after the retry exactly one invitation exists and it is recorded", len(invites("instant-1")) == 1 and 1 in seq(lid).get("done", []), f"{len(invites('instant-1'))} {seq(lid).get('done')}")
    if wanted("U16"):
        fresh(); a = add_lead("emailreq-1", "Emailreq Lead"); b = add_lead("connect-2", "Connect Lead")
        code, out = run_for(PROFILE_A, 25)
        check("U16a", "one profile that asks for the person's email must not switch off all other connection requests (second lead is still invited)",
              bool(invites("connect-2")), f"invites={[i['to'] for i in invites()]} | {notes(a)[:160]}")
        check("U16b", "the email-required box is not reported as 'monthly note limit'", "monthly limit" not in notes(a), notes(a)[:200])
    if wanted("U17"):
        fresh(); lid = add_lead("fillro-1", "Fillro Lead")
        cycles(PROFILE_A, 1)
        got = invites("fillro-1")
        check("U17a", "the note box is not editable for 6 s (fill fails): an EMPTY request is not sent when bare requests are not allowed",
              not (got and got[0]["note"] == ""), f"invites={got} | {notes(lid)[:200]}")
    if wanted("U18"):
        fresh(); lid = add_lead("drop-1", "Drop Lead")
        _c, out_u18 = cycles(PROFILE_A, 1)
        print("   [U18 out]", out_u18[-700:].replace(chr(10), " / "))
        check("U18a", "Send closes the box but LinkedIn shows an error toast and records nothing: the portal is NOT told 'sent'",
              not invites("drop-1") and 1 not in seq(lid).get("done", []), f"done={seq(lid).get('done')} status={seq(lid).get('status')} | {notes(lid)[:200]}")
    if wanted("U19"):
        fresh(); lid = add_lead("modal-1", "Modal Lead")
        views0 = fs()["views"].get("modal-1", 0)
        code, out = run_for(PROFILE_A, 22)
        views = fs()["views"].get("modal-1", 0) - views0
        check("U19a", "a Premium overlay covers the page: nothing sent, a reason is logged", not invites() and "covered" in notes(lid), notes(lid)[:200])
        check("U19b", "a lead that failed twice is not re-visited every cycle (profile page opened <= 4 times in 22 s)", views <= 4, f"profile opened {views} times, exit={code}")
        print("   [U19 exit/out]", code, out[-600:].replace(chr(10), " / "))
    if wanted("U20"):
        fresh(); lid = add_lead("softwall-1", "Softwall Lead")
        _c, out_u20 = cycles(PROFILE_A, 2)
        n = " | ".join(r["note"] for r in agent.log_rows(store)) + " || " + out_u20
        check("U20a", "logged-out profile (full-page 'Sign in' wall at the same URL): nothing sent", not invites())
        check("U20b", "...and the reason tells the owner LinkedIn is logged out / asking to sign in", any(w in n.lower() for w in ("sign in", "log in", "logged out", "login")), n[:300])
    if wanted("U21"):
        fresh(); lid = add_lead("wallmid-1", "Wallmid Lead")
        code, out = run_agent(PROFILE_A, "--once", timeout=40); skip_gap()
        code2, out2 = run_agent(PROFILE_A, "--once", timeout=30)
        allrows = " | ".join(r["note"] for r in agent.log_rows(store))
        check("U21a", "session expires on the Connect click: nothing sent and the owner is told to log in", not invites() and ("log in" in allrows.lower() or "login" in allrows.lower() or "login" in out2.lower()), f"{code}/{code2} {allrows[:200]} || {out2[-200:]}")
        http("POST", "/__as?acct=acct-a", base=FAKE_A)
    if wanted("U22"):
        fresh(); lid = add_lead("restricted-1", "Restricted Lead")
        cycles(PROFILE_A, 1)
        check("U22a", "page says 'Your account has been temporarily restricted': the agent stops, sends nothing", not invites(), f"invites={invites()}")
    if wanted("U23"):
        fresh(); lid = add_lead("falsepos-1", "Falsepos Lead")
        code, out = cycles(PROFILE_A, 2)
        check("U23a", "a lead whose headline says 'security verification' does not pause the whole account", agent.paused(store).get("acct-a") is None and bool(invites("falsepos-1")), f"paused={agent.paused(store).get('acct-a')} code={code} invites={invites()}")
        fresh(); lid = add_lead("first-3", "First Lead")
        http("POST", "/__inbox", {"items": [["Someone Else", "Someone: did you see the unusual activity on the Q3 report?"]]}, base=FAKE_A)
        agent.save_config(store, {"check_replies": True})
        code, out = run_agent(PROFILE_A, "--once")
        check("U23b", "an unrelated chat preview containing 'unusual activity' does not pause the account", agent.paused(store).get("acct-a") is None, f"code={code} {out[-200:]}")
        http("POST", "/__inbox", {"items": []}, base=FAKE_A)
        fresh(); agent.save_config(store, {"find_profiles": True, "lookups_per_day": 15})
        csvt = "company,contact_name,title,email,website\nFpco,Find Fp,Head of Talent,find.fp@fpco.test,https://fpco.test\n"
        http("POST", "/api/leads/import", {"csv": csvt, "source": "audit"}, owner)
        fl = next(l for l in store.read("leads") if l["company"] == "Fpco")["id"]
        http("POST", f"/api/leads/{fl}/linkedin/start", token=owner)
        code, out = run_agent(PROFILE_A, "--once")
        check("U23c", "a search result headline containing 'unusual activity' does not pause the account", agent.paused(store).get("acct-a") is None, f"code={code} {out[-200:]}")
    if wanted("U24"):
        fresh(); lid = add_lead("http999-1", "Ravi Menon")
        views0 = fs()["views"].get("http999-1", 0)
        code, out = run_for(PROFILE_A, 15)
        n = notes(lid); views = fs()["views"].get("http999-1", 0) - views0
        check("U24a", "LinkedIn answers HTTP 999 (blocked): the reason says LinkedIn refused, not 'no Connect button'", any(w in (n + out).lower() for w in ("999", "refus", "blocked", "denied")), n[:200])
        check("U24b", "HTTP 999: the agent does not keep hammering the same URL (<= 4 loads in 15 s)", views <= 4, f"{views} loads")
        print("   [U24 exit/out]", code, views, out[-600:].replace(chr(10), " / "), "NOTES", n)
    if wanted("U25"):
        fresh(); lid = add_lead("hang-1", "Hang Lead")
        code, out = run_agent(PROFILE_A, "--once", timeout=100)
        check("U25a", "one profile that never finishes loading (30 s goto timeout) does not kill the agent (exit code 1)", code != 1, f"exit={code} {out[-250:]}")
        check("U25b", "...and the lead gets a logged reason", bool(notes(lid)), notes(lid) or out[-200:])


def sc_sent_report():
    if wanted("U26"):
        fresh(); lid = add_lead("instant-26", "Instant Lead")
        http("POST", "/__cfg?slug=instant-26", {"dialog": "normal"}, base=FAKE_A)
        FAIL_NEXT["sent"] = 1
        code, out = run_agent(PROFILE_A, "--once", portal=PROXY); skip_gap()
        check("U26a", "portal answers 500 to /sent after the invitation went out: the agent survives (no exit 1)", code != 1, f"exit={code} {out[-200:]}")
        run_agent(PROFILE_A, "--once", portal=PROXY); skip_gap()
        check("U26b", "connect retry after /sent 500: exactly one invitation, and it is recorded", len(invites("instant-26")) == 1 and 1 in seq(lid).get("done", []), f"{len(invites('instant-26'))} {seq(lid).get('done')}")
        fresh(); lid = add_lead("first-26", "First Lead")
        cycles(PROFILE_A, 1); make_due(lid)
        FAIL_NEXT["sent"] = 1
        run_agent(PROFILE_A, "--once", portal=PROXY); skip_gap(); make_due(lid)
        run_agent(PROFILE_A, "--once", portal=PROXY); skip_gap()
        check("U26c", "message sent but /sent answered 500: the lead is NOT sent the same message again", sum(m["text"].count("Thanks for connecting") for m in msgs("first-26")) <= 1, f"{[m['text'][:30] for m in msgs('first-26')]}")
    if wanted("U27"):
        fresh(); lid = add_lead("nodeliver-1", "Nodeliver Lead")
        cycles(PROFILE_A, 1); make_due(lid); run_agent(PROFILE_A, "--once")
        check("U27a", "chat shows 'Not delivered. Retry' (box emptied, nothing recorded): step 2 is not reported as sent", 2 not in seq(lid).get("done", []), f"done={seq(lid).get('done')} | {notes(lid)[:200]}")
    if wanted("U28"):
        fresh(); lid = add_lead("slowsend-1", "Slowsend Lead")
        cycles(PROFILE_A, 1); make_due(lid)
        run_agent(PROFILE_A, "--once"); skip_gap(); time.sleep(3); make_due(lid)
        run_agent(PROFILE_A, "--once"); skip_gap(); time.sleep(3)
        check("U28a", "message takes 2.5 s to leave (slow network): it is sent once, not again on the retry", sum(m["text"].count("Thanks for connecting") for m in msgs("slowsend-1")) <= 1, f"{len(msgs('slowsend-1'))} messages | {notes(lid)[:300]}")
    if wanted("U29"):
        fresh(); lid = add_lead("slowthread-1", "Slowthread Lead")
        cycles(PROFILE_A, 1); make_due(lid)
        http("POST", "/__reply?acct=acct-a&slug=slowthread-1&text=Please+do+not+message+me+again", base=FAKE_A)
        run_agent(PROFILE_A, "--once")
        check("U29a", "they already answered but the chat history loads slowly: no follow-up is sent over their reply", not msgs("slowthread-1"), f"{msgs('slowthread-1')}")
    if wanted("U30"):
        fresh(); lid = add_lead("samefirst-1", "Asha Mehta")
        cycles(PROFILE_A, 1); make_due(lid); run_agent(PROFILE_A, "--once"); skip_gap()
        make_due(lid); run_agent(PROFILE_A, "--once"); skip_gap()
        check("U30a", "lead shares our account's first name (Asha / Asha Account): our own earlier message is not read as THEIR reply", seq(lid).get("stopped") != "replied" and len(msgs("samefirst-1")) >= 2,
              f"stopped={seq(lid).get('stopped')} sent={len(msgs('samefirst-1'))}")
    if wanted("U31"):
        fresh(); lid = add_lead("first-31", "First Lead")
        cycles(PROFILE_A, 1)
        http("POST", "/__cfg?slug=first-31", {"name": "First Lead"}, base=FAKE_A)
        # draft left behind by a failed first attempt must not be glued to the second message
        http("POST", "/__cfg?slug=first-31", {"sendDisabledFirst": True}, base=FAKE_A)
        make_due(lid); run_agent(PROFILE_A, "--once"); skip_gap(); make_due(lid); run_agent(PROFILE_A, "--once"); skip_gap()
        texts = [m["text"] for m in msgs("first-31")]
        check("U31a", "the first attempt could not press Send and left its text in the box: the retry sends the text ONCE", all(t.count("Thanks for connecting") <= 1 for t in texts) and bool(texts), f"{[len(t) for t in texts]} {[t.count('Thanks for connecting') for t in texts]} drafts={fs()['drafts']}")
    if wanted("U32"):
        for kind, label in (("enterkey", "Enter-to-send (no Send button)"), ("keyonly", "Send enabled only by real key events")):
            fresh(); lid = add_lead(f"{kind}-1", f"{kind.capitalize()} Lead")
            cycles(PROFILE_A, 1); make_due(lid); run_agent(PROFILE_A, "--once")
            check(f"U32-{kind}-a", f"{label}: sent once, or nothing sent and a reason is logged", len(msgs()) == 1 or (not msgs() and bool(notes(lid))), notes(lid)[:200])
            check(f"U32-{kind}-b", f"{label}: the typed text is removed from the chat box (no stale draft left)", not any((d or "").strip() for d in fs()["drafts"].values()) or len(msgs()) == 1, f"drafts={fs()['drafts']}")
    if wanted("U33"):
        fresh(); lid = add_lead("fullmsg-1", "Ravi Menon")
        http("POST", "/__cfg?slug=fullmsg-1", {"name": "Ravi Menon"}, base=FAKE_A)
        cycles(PROFILE_A, 1); make_due(lid); run_agent(PROFILE_A, "--once")
        n = notes(lid)
        check("U33a", "Message opens the full-screen messaging page instead of the overlay: nothing sent, reason mentions it", not msgs() and ("full-screen" in n.lower() or "messaging page" in n.lower() or "full screen" in n.lower()), n[:200])
    if wanted("U34"):
        fresh(); lid = add_lead("inmail-1", "Inmail Lead")
        cycles(PROFILE_A, 2)
        prof = lead_of(lid).get("linkedin_profile") or {}
        check("U34a", "2nd-degree profile with a Premium 'Message' button (InMail) is NOT stored as Open Profile", prof.get("open_profile") is not True, str(prof))


def sc_inbox_and_find():
    if wanted("U35"):
        fresh(); lid = add_lead("hindi-35", "Rahul Sharma", acct="acct-hi")
        store.update_lead(lid, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "status": "waiting_for_acceptance", "done": [1], "last_sent_at": "2026-01-01T00:00:00+00:00"}})
        http("POST", "/__inbox", {"items": [["Rahul Sharma", "आप: नमस्ते Rahul, जुड़ने के लिए धन्यवाद"]]}, base=FAKE_A)
        agent.save_config(store, {"check_replies": True})
        run_agent(PROFILE_H, "--once"); http("POST", "/__inbox", {"items": []}, base=FAKE_A)
        fake_task = [t for t in store.read("tasks") if t.get("lead_id") == lid and "possible LinkedIn reply" in (t.get("description") or t.get("title") or t.get("text") or str(t))]
        check("U35a", "LinkedIn in Hindi: OUR last message ('आप: ...') is not read as the lead's reply (no stop, no 'check a possible reply' task)", seq(lid).get("stopped") != "replied" and not fake_task, f"seq={seq(lid)} tasks={[str(t)[:120] for t in fake_task]}")
    if wanted("U36"):
        for q, label in (("Find Limit", "commercial-use-limit page"), ("Find Slow", "search cards that load after 4 s")):
            fresh(); agent.save_config(store, {"find_profiles": True, "lookups_per_day": 15})
            co = f"Lk{COUNTER[0]}x"; COUNTER[0] += 1
            http("POST", "/api/leads/import", {"csv": f"company,contact_name,title,email,website\n{co},{q},Head of Talent,{q.replace(' ', '.')}@{co}.test,https://{co}.test\n", "source": "audit"}, owner)
            lid = next(l for l in store.read("leads") if l["company"] == co)["id"]
            http("POST", f"/api/leads/{lid}/linkedin/start", token=owner)
            run_agent(PROFILE_A, "--once")
            st = (lead_of(lid).get("linkedin_lookup") or {}).get("status")
            check(f"U36-{q.split()[1].lower()}", f"people search shows {label}: the lead is NOT marked 'not_found'", st != "not_found", f"lookup status={st}")


def sc_note():
    if wanted("U37"):
        fresh()
        note = "Hi {first_name},\nI saw your post \U0001F680 about hiring and would love to connect.\n\nThanks!"
        store.update("settings", lambda s: {**s, "linkedin_connect_variants": {"A": note, "B": note}})
        lid = add_lead("connect-37", "Connect Lead")
        store.update_lead(lid, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "variant": "A"}})
        cycles(PROFILE_A, 1)
        got = invites("connect-37")
        check("U37a", "note with emoji + newlines arrives whole", bool(got) and "\U0001F680" in got[0]["note"] and "\n\n" in got[0]["note"], str(got))
        fresh()
        long = "Hi {first_name}, " + "x" * 320
        store.update("settings", lambda s: {**s, "linkedin_connect_variants": {"A": long, "B": long}})
        lid = add_lead("connect-38", "Connect Lead")
        store.update_lead(lid, lambda l: {**l, "linkedin_seq": {**l["linkedin_seq"], "variant": "A"}})
        cycles(PROFILE_A, 1)
        got = invites("connect-38")
        check("U37b", "a 320-character note is never typed in (sent whole, or refused with a reason)", not got or len(got[0]["note"]) <= 300, f"len={[len(i['note']) for i in got]} {notes(lid)[:200]}")
        print("   [U37b note]", [i["note"][:70] for i in got])
        store.update("settings", lambda s: {k: v for k, v in s.items() if k != "linkedin_connect_variants"})


for fn in (sc_timing, sc_names, sc_invite_box, sc_sent_report, sc_inbox_and_find, sc_note):
    try:
        fn()
    except Exception as e:  # a harness error must be visible, not hidden as a pass
        check("HARNESS-" + fn.__name__, "scenario group crashed", False, repr(e)[:300])

fake.terminate(); server.should_exit = True
passed = sum(ok for _, _, ok, _ in RESULTS)
print(f"\n{passed}/{len(RESULTS)} checks passed; FAILED: {[c for c, _, ok, _ in RESULTS if not ok]}")
shutil.rmtree(WORK, ignore_errors=True)
os._exit(0 if passed == len(RESULTS) else 1)
