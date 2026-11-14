#!/usr/bin/env python3
"""Local round trip between MCN Outreach and DialHalo (not part of CI; needs Node, Docker MySQL and this repo's venv).

    DIALHALO_REPO=/path/to/call-tracker  EVIDENCE_DIR=/tmp/roundtrip  python tests/e2e/dialhalo_roundtrip.py

What it proves, each as PASS/FAIL with the evidence saved to EVIDENCE_DIR/evidence.json (secrets are never written):
  a  a call task in Outreach puts the lead in the owner's DialHalo dial queue (with the link back), once
  b  a phone call + outcome in DialHalo shows on the Outreach lead (call, stage move) via the signed webhook
  c  "Do not call" in DialHalo -> Outreach contact unsubscribed; Outreach do-not-contact -> DialHalo list + dialling blocked
  d  "Wrong number" (default mapping): the phone is marked invalid; no do-not-contact, no suppression, no DNC echo
  e  delivery retry: DialHalo -> Outreach webhook after Outreach was stopped; Outreach -> DialHalo push after DialHalo was stopped
Everything runs on 127.0.0.1 with throw-away data: DialHalo on :8300 (database ct_rt on the local MySQL container),
Outreach on :8301 (temp data file, email in practice mode). It never touches a live server.
"""
import atexit
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
DIALHALO = Path(os.environ.get("DIALHALO_REPO", HERE.parent.parent / "call-tracker")).resolve()
EVIDENCE = Path(os.environ.get("EVIDENCE_DIR", tempfile.mkdtemp(prefix="roundtrip-")))
CT_PORT, OUT_PORT = int(os.environ.get("CT_PORT", 8300)), int(os.environ.get("OUT_PORT", 8301))
CT, OUT = f"http://127.0.0.1:{CT_PORT}", f"http://127.0.0.1:{OUT_PORT}"
DB = "ct_rt"
ADMIN_PW = "rt-" + secrets.token_hex(8)
CT_JWT = secrets.token_hex(24)
TMP = Path(tempfile.mkdtemp(prefix="outreach-rt-"))
STATE: dict = {}
procs: dict = {}
results: list = []
evidence: dict = {}


def say(msg):
    print(msg, flush=True)


def call(method, url, body=None, token=None, headers=None, timeout=30):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, json.dumps(body).encode() if body is not None else None, h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw[:300]}


def data(resp):
    status, body = resp
    assert 200 <= status < 300, f"HTTP {status}: {str(body)[:300]}"
    return body.get("data", body) if isinstance(body, dict) else body


def rows_of(x):
    """The list inside an API answer ({items|rows|leads|...: [...]} or the list itself)."""
    if isinstance(x, list):
        return x
    if isinstance(x, dict):
        for k in ("items", "rows", "leads", "data", "numbers", "entries"):
            if isinstance(x.get(k), list):
                return x[k]
        return next((v for v in x.values() if isinstance(v, list)), [])
    return []


def wait_for(fn, what, seconds=60, every=1.0):
    end = time.time() + seconds
    last = None
    while time.time() < end:
        try:
            last = fn()
            if last:
                return last
        except Exception as exc:                 # noqa: BLE001 - keep polling
            last = exc
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what} (last: {last})")


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    say(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))
    return ok


# ------------------------------------------------------------------ processes
def mysql_root_password():
    return os.environ.get("DB_PASSWORD") or subprocess.check_output(["docker", "exec", "ct-mysql", "printenv", "MYSQL_ROOT_PASSWORD"], text=True).strip()


def start_dialhalo():
    pw = mysql_root_password()
    subprocess.run(["docker", "exec", "ct-mysql", "mysql", f"-uroot", f"-p{pw}", "-e",
                    f"DROP DATABASE IF EXISTS {DB}; CREATE DATABASE {DB} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"],
                   check=True, capture_output=True)
    env = {**os.environ, "PORT": str(CT_PORT), "DB_HOST": "127.0.0.1", "DB_PORT": "3399", "DB_USER": "root", "DB_PASSWORD": pw, "DB_NAME": DB,
           "JWT_SECRET": CT_JWT, "WEB_DIST": str(DIALHALO / "web" / "dist"), "WEBHOOK_ALLOW_PRIVATE_HOSTS": f"127.0.0.1:{OUT_PORT}"}
    log = open(EVIDENCE / "dialhalo.log", "ab")
    procs["ct"] = subprocess.Popen(["node", "dist/index.js"], cwd=DIALHALO / "server", env=env, stdout=log, stderr=log)
    wait_for(lambda: procs["ct"].poll() is None and call("GET", f"{CT}/api/health")[0] == 200, "DialHalo to start (is the port free?)", 60)


def stop(name):
    p = procs.pop(name, None)
    if p:
        p.terminate()
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()


def start_outreach():
    env = {**os.environ, "OUTREACH_DATA_FILE": str(TMP / "store.json"), "OUTREACH_ADMIN_PASSWORD": ADMIN_PW, "OUTREACH_ADMIN_LOGIN": "1",
           "OUTREACH_ALLOW_PRIVATE_HOSTS": f"127.0.0.1:{CT_PORT}", "OUTREACH_BASE_URL": OUT, "OUTREACH_SECRET_KEY": "rt" + "a" * 40,
           "OUTREACH_EMAIL_DOMAIN_CHECK": "0", "OUTREACH_IGNORE_BUSINESS_HOURS": "1", "OUTREACH_DEV_OUTBOX": str(TMP / "outbox.txt")}
    env.pop("OUTREACH_DATABASE_URL", None)
    log = open(EVIDENCE / "outreach.log", "ab")
    procs["out"] = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(OUT_PORT), "--workers", "1"],
                                    cwd=HERE, env=env, stdout=log, stderr=log)
    wait_for(lambda: procs["out"].poll() is None and call("GET", f"{OUT}/api/auth/status")[0] == 200, "Outreach to start (is the port free?)", 60)


def cleanup():
    for n in list(procs):
        stop(n)
    shutil.rmtree(TMP, ignore_errors=True)
    # evidence never holds secrets: only response snippets we chose to store
    (EVIDENCE / "evidence.json").write_text(json.dumps({"results": [{"check": n, "pass": ok} for n, ok, _ in results], "evidence": evidence}, indent=2, default=str))


atexit.register(cleanup)


# ------------------------------------------------------------------ the scenario
def main():
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    for name in ("dialhalo.log", "outreach.log"):
        (EVIDENCE / name).write_text("")
    say(f"evidence -> {EVIDENCE}")
    start_dialhalo()
    start_outreach()
    suffix = secrets.token_hex(3)

    # --- DialHalo: organisation, an agent with a paired phone, API key and webhook
    owner_pw = "owner-" + secrets.token_hex(6)
    ct_owner = data(call("POST", f"{CT}/api/auth/setup", {"orgName": "Round Trip Co", "name": "Olive Owner", "email": "owner@rt.test", "password": owner_pw}))["token"]
    ravi = data(call("POST", f"{CT}/api/members", {"name": "Ravi RT", "email": "ravi@rt.test", "password": "ravi-" + secrets.token_hex(6), "role": "agent"}, ct_owner))
    code = data(call("POST", f"{CT}/api/call-tracking/pairing-codes", {"userId": ravi["id"]}, ct_owner))["code"]
    dev = data(call("POST", f"{CT}/api/call-tracking/device/pair", {"code": code, "consentVersion": "2026-11"}))["deviceToken"]
    outcomes = {o["name"].lower(): o["id"] for o in data(call("GET", f"{CT}/api/call-tracking/device/config", token=dev))["outcomes"]}
    evidence["dialhalo_outcomes"] = sorted(outcomes)
    key = data(call("POST", f"{CT}/api/integrations/api-keys", {"name": "MCN Outreach", "scopes": ["read", "leads:write", "ai:read"], "allowedIps": "127.0.0.1"}, ct_owner))["key"]

    # --- Outreach: sign in, two people (one matches a DialHalo member, one does not), connect the card
    out = data(call("POST", f"{OUT}/api/login", {"password": ADMIN_PW}))["token"]
    for name, email in (("Ravi RT", "ravi@rt.test"), ("Zed Unmatched", "zed@rt.test")):
        data(call("POST", f"{OUT}/api/users", {"email": email, "name": name, "role": "caller", "password": "pw-" + secrets.token_hex(6), "workspaces": [], "all_workspaces": True}, out))
    card = data(call("PUT", f"{OUT}/api/calltracker", {"workspace": "main", "base_url": CT, "public_url": "https://dialhalo.example.test", "api_key": key, "enabled": True}, out))
    conn_id = card["connection"]["id"]
    hook = data(call("POST", f"{CT}/api/integrations/webhooks", {"url": f"{OUT}/api/calltracker/hook/{conn_id}", "events": ["call.outcome", "calls.synced", "call.transcribed", "dnc.added", "dnc.removed"]}, ct_owner))
    wh_secret = hook["secret"]
    STATE.update(ct_owner=ct_owner, hook_id=hook["id"], key=key)
    data(call("PUT", f"{OUT}/api/calltracker", {"workspace": "main", "webhook_secret": wh_secret}, out))
    test = call("POST", f"{OUT}/api/calltracker/test", {"workspace": "main"}, out)[1]
    evidence["test_connection"] = {k: test.get(k) for k in ("ok", "message", "members", "unmatched")}
    say("== setup")
    check("Test connection says Connected", test.get("ok"), test.get("message"))
    unmatched = {u["email"]: u for u in test.get("unmatched", [])}
    check("Unmatched people listed with a reason (zed has no DialHalo member)", "zed@rt.test" in unmatched and unmatched["zed@rt.test"].get("reason"), str(unmatched))
    check("Matched person (ravi) is not in the unmatched list", "ravi@rt.test" not in unmatched)

    def new_lead(company, phone, email):
        csv = f"company,contact_name,title,email,phone,website\n{company},Contact {company},HR Head,{email},{phone},https://{company.lower().replace(' ', '')}.example.com\n"
        data(call("POST", f"{OUT}/api/leads/import", {"csv": csv, "source": "roundtrip"}, out))
        leads = call("GET", f"{OUT}/api/leads", token=out)[1]
        lead = next(l for l in leads if l["company"] == company)
        return lead["id"]

    def call_task(lead_id, owner="Ravi RT"):
        return data(call("POST", f"{OUT}/api/tasks", {"lead_id": lead_id, "title": "Call them", "text": "Replied yes: wants a demo", "kind": "call",
                                                      "due_at": "2099-12-01T10:00:00+05:30", "owner": owner}, out))

    def ct_lead_of(lead_id):
        st, body = call("GET", f"{CT}/api/v1/leads/external/mcn_outreach/{lead_id}", token=key)
        return body.get("data") if st == 200 else None

    def outreach_lead(lead_id):
        return call("GET", f"{OUT}/api/leads/{lead_id}", token=out)[1]

    def phone_call(number, outcome_name, duration=42, cid=None, note="round trip"):
        cid = cid or f"rt-{secrets.token_hex(4)}"
        data(call("POST", f"{CT}/api/call-tracking/device/sync", {"calls": [{"id": cid, "number": number, "type": "OUTGOING", "date": int((time.time() - 90) * 1000), "duration": duration}]}, dev))
        oid = outcomes[outcome_name.lower()]
        data(call("POST", f"{CT}/api/call-tracking/device/outcome", {"callId": cid, "outcomeId": oid, "note": note}, dev))
        return cid

    # --- a: Outreach -> DialHalo
    say("== a  Outreach lead -> DialHalo dial queue")
    a_id = new_lead(f"Alpha {suffix}", "9876500001", f"a{suffix}@alpha-rt.test")
    STATE["a_id"] = a_id
    call_task(a_id)
    a_ct = wait_for(lambda: ct_lead_of(a_id), "the lead to reach DialHalo", 60)
    evidence["a_dialhalo_lead"] = {k: a_ct.get(k) for k in ("id", "name", "phone", "external_source", "external_id", "external_url", "status")}
    check("a: lead exists in DialHalo with the Outreach reference", a_ct.get("external_source") == "mcn_outreach" and a_ct.get("external_id") == a_id)
    queue = data(call("GET", f"{CT}/api/call-tracking/device/leads", token=dev))
    items = rows_of(queue)
    check("a: lead is in Ravi's phone queue", any(str(i.get("id")) == str(a_ct.get("id")) for i in items), str(len(items)))
    call_task(a_id)                                  # a second call task for the same lead: same DialHalo lead, not a new one
    time.sleep(5)
    again = ct_lead_of(a_id)
    check("a: pushing again updates the same DialHalo lead (no duplicate)", again and again.get("id") == a_ct.get("id"))

    # --- b: DialHalo -> Outreach
    say("== b  DialHalo call + outcome -> Outreach lead")
    b_cid = phone_call("9876500001", "Interested", note="Wants a demo next week")
    def got_b():
        l = outreach_lead(a_id)
        acts = rows_of(call("GET", f"{OUT}/api/leads/{a_id}/activity", token=out)[1])
        tl = [f"{a.get('type')}: {a.get('title')} - {str(a.get('body') or '')[:90]}" for a in acts]
        has_call = any(a.get("type") == "call" and "DialHalo" in str(a.get("title") or a.get("subject") or a.get("link_label") or "") for a in acts)
        return (l, tl, has_call) if has_call else None
    lead_b, tl_b, _ = wait_for(got_b, "the call to appear on the Outreach lead", 60)
    evidence["b_timeline"] = tl_b[-6:]
    check("b: call logged on the Outreach lead", True)
    check("b: Interested outcome moved the stage forward", lead_b.get("stage") == "interested", lead_b.get("stage"))

    # --- d: Wrong number (default mapping)
    say("== d  'Wrong number' default: phone invalid only")
    d_id = new_lead(f"Delta {suffix}", "9876500004", f"d{suffix}@delta-rt.test")
    call_task(d_id)
    wait_for(lambda: ct_lead_of(d_id), "lead D in DialHalo", 60)
    phone_call("9876500004", "Wrong number", duration=0)
    d = wait_for(lambda: (lambda l: l if (l.get("phone_status") or {}).get("status") == "wrong_number" else None)(outreach_lead(d_id)), "phone flag on lead D", 60)
    evidence["d_lead"] = {k: d.get(k) for k in ("stage", "phone", "phone_status", "do_not_contact", "sequence")}
    check("d: phone marked wrong_number", (d.get("phone_status") or {}).get("number") == d.get("phone"))
    check("d: person is NOT on the do-not-contact list", not d.get("do_not_contact"))
    check("d: lead stage unchanged (not unsubscribed)", d.get("stage") != "unsubscribed", d.get("stage"))
    dnc = data(call("GET", f"{CT}/api/v1/dnc?limit=200", token=key))
    dnc_items = rows_of(dnc)
    check("d: number was not echoed to DialHalo's do-not-call list", not any("9876500004" in json.dumps(i) for i in dnc_items))

    # --- c: do not call both ways
    say("== c  do-not-call both ways")
    c_id = new_lead(f"Gamma {suffix}", "9876500003", f"c{suffix}@gamma-rt.test")
    call_task(c_id)
    wait_for(lambda: ct_lead_of(c_id), "lead C in DialHalo", 60)
    phone_call("9876500003", "Do not call", duration=20)
    c = wait_for(lambda: (lambda l: l if l.get("stage") == "unsubscribed" or l.get("do_not_contact") else None)(outreach_lead(c_id)), "lead C unsubscribed in Outreach", 60)
    evidence["c_forward_lead"] = {k: c.get(k) for k in ("stage", "do_not_contact")}
    check("c: DialHalo 'Do not call' -> Outreach contact unsubscribed / do-not-contact", True)
    dnc = data(call("GET", f"{CT}/api/v1/dnc?limit=200", token=key))
    dnc_items = rows_of(dnc)
    check("c: number is on the DialHalo do-not-call list (set by the outcome)", any("9876500003" in json.dumps(i) for i in dnc_items))
    # reverse
    r_id = new_lead(f"Rho {suffix}", "9876500005", f"r{suffix}@rho-rt.test")
    call_task(r_id)
    r_ct = wait_for(lambda: ct_lead_of(r_id), "lead R in DialHalo", 60)
    data(call("POST", f"{OUT}/api/leads/bulk", {"lead_ids": [r_id], "action": "do_not_contact"}, out))
    def dnc_has_r():
        d2 = data(call("GET", f"{CT}/api/v1/dnc?limit=200", token=key))
        its = rows_of(d2)
        return any("9876500005" in json.dumps(i) for i in its)
    wait_for(dnc_has_r, "Outreach do-not-contact to reach DialHalo", 60)
    check("c: Outreach do-not-contact -> number on DialHalo's do-not-call list", True)
    st, body = call("POST", f"{CT}/api/call-tracking/device/leads/{r_ct['id']}/dial", {"purpose": "call"}, dev)
    evidence["c_dial_after_dnc"] = {"status": st, "message": (body.get("message") if isinstance(body, dict) else None)}
    check("c: dialling that lead from the phone is refused", st == 403, f"HTTP {st}")

    # --- e: retries
    say("== e  delivery retry")
    e_id = new_lead(f"Eps {suffix}", "9876500006", f"e{suffix}@eps-rt.test")
    call_task(e_id)
    wait_for(lambda: ct_lead_of(e_id), "lead E in DialHalo", 60)
    stop("out")
    phone_call("9876500006", "Interested", note="while Outreach was down")
    time.sleep(4)
    deliveries = data(call("GET", f"{CT}/api/integrations/webhooks/{hook['id']}/deliveries", token=ct_owner))
    dl = rows_of(deliveries)
    pending = [x for x in dl if x.get("status") == "pending" and (x.get("attempts") or 0) >= 1]
    evidence["e_failed_attempts"] = [{k: x.get(k) for k in ("event", "status", "attempts", "last_error")} for x in pending[:3]]
    check("e: DialHalo kept the delivery as pending after Outreach refused it (attempts >= 1)", bool(pending), str(dl[:2]))
    start_outreach()
    out = data(call("POST", f"{OUT}/api/login", {"password": ADMIN_PW}))["token"]
    for x in pending:                        # do not wait out the 1-minute back-off: use the same Redeliver button the portal has
        call("POST", f"{CT}/api/integrations/deliveries/{x['id']}/redeliver", {}, ct_owner)
    def got_e():
        l = outreach_lead(e_id)
        return l if l.get("stage") == "interested" else None
    wait_for(got_e, "the retried delivery to reach the restarted Outreach", 90)
    check("e: after Outreach restarted the delivery arrived and the lead moved to interested", True)
    # other direction: DialHalo down, Outreach pushes, DialHalo back, retry
    f_id = new_lead(f"Phi {suffix}", "9876500007", f"f{suffix}@phi-rt.test")
    stop("ct")
    call_task(f_id)
    time.sleep(4)
    q = call("GET", f"{OUT}/api/calltracker", token=out)[1]
    evidence["e_outreach_queue_while_dialhalo_down"] = q.get("queue")
    start_dialhalo_again = start_dialhalo_keep_db()
    call("POST", f"{OUT}/api/calltracker/retry", {"workspace": "main"}, out)
    wait_for(lambda: ct_lead_of(f_id), "the queued push to arrive after DialHalo came back", 120, 2)
    check("e: Outreach's queued push reached DialHalo after it came back (Retry failed / back-off)", True)

    if os.environ.get("SCREENSHOTS") == "1":
        shots = EVIDENCE / "screenshots"
        shots.mkdir(exist_ok=True)
        ui_port = OUT_PORT + 1
        procs["ui"] = subprocess.Popen(["node", str(HERE / "tests" / "e2e" / "ui_proxy.cjs")], env={**os.environ, "UI_PORT": str(ui_port), "API_PORT": str(OUT_PORT), "DIST": str(HERE / "web" / "dist")})
        wait_for(lambda: urllib.request.urlopen(f"http://127.0.0.1:{ui_port}/", timeout=5).status == 200, "the UI proxy", 20)
        env = {**os.environ, "OUT": f"http://127.0.0.1:{ui_port}", "CT": CT, "OUT_TOKEN": out, "A_ID": a_id, "D_ID": d_id, "CT_EMAIL": "owner@rt.test", "CT_PASSWORD": owner_pw, "SHOTS": str(shots),
               "PLAYWRIGHT_CORE": os.environ.get("PLAYWRIGHT_CORE", "playwright-core"), "CHROMIUM": os.environ.get("CHROMIUM", "")}
        done = subprocess.run(["node", str(HERE / "tests" / "e2e" / "screenshots.cjs")], env=env, capture_output=True, text=True)
        check("screenshots taken", done.returncode == 0, (done.stderr or done.stdout)[-300:])

    failed = [n for n, ok, _ in results if not ok]
    say(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


def start_dialhalo_keep_db():
    """Restart DialHalo on the SAME database (the scenario needs its data)."""
    pw = mysql_root_password()
    env = {**os.environ, "PORT": str(CT_PORT), "DB_HOST": "127.0.0.1", "DB_PORT": "3399", "DB_USER": "root", "DB_PASSWORD": pw, "DB_NAME": DB,
           "JWT_SECRET": CT_JWT, "WEB_DIST": str(DIALHALO / "web" / "dist"), "WEBHOOK_ALLOW_PRIVATE_HOSTS": f"127.0.0.1:{OUT_PORT}"}
    log = open(EVIDENCE / "dialhalo.log", "ab")
    procs["ct"] = subprocess.Popen(["node", "dist/index.js"], cwd=DIALHALO / "server", env=env, stdout=log, stderr=log)
    wait_for(lambda: procs["ct"].poll() is None and call("GET", f"{CT}/api/health")[0] == 200, "DialHalo to start again", 60)
    return True


def diagnose():
    """On failure: what the Outreach queue and both logs say (no secrets: the card never returns keys)."""
    try:
        tok = data(call("POST", f"{OUT}/api/login", {"password": ADMIN_PW}))["token"]
        q = call("GET", f"{OUT}/api/calltracker", token=tok)[1]
        print("outreach queue:", json.dumps(q.get("queue"), default=str)[:900])
        print("outreach last_event / last_push:", json.dumps({k: (q.get("connection") or {}).get(k) for k in ("last_event", "last_push")}, default=str)[:600])
        if STATE.get("a_id"):
            st2, acts = call("GET", f"{OUT}/api/leads/{STATE['a_id']}/activity", token=tok)
            print("outreach activity:", st2, json.dumps(acts, default=str)[:700])
        if STATE.get("key"):
            st, body = call("GET", f"{CT}/api/v1/calls/1", token=STATE["key"])
            print("dialhalo call 1:", st, json.dumps(body, default=str)[:900])
    except Exception as exc:               # noqa: BLE001
        print("could not read the Outreach queue:", exc)
    try:
        dl = rows_of(data(call("GET", f"{CT}/api/integrations/webhooks/{STATE['hook_id']}/deliveries", token=STATE["ct_owner"])))
        print("dialhalo deliveries:", json.dumps([{k: d.get(k) for k in ("event", "status", "attempts", "last_status_code", "last_error")} for d in dl[:6]], default=str)[:900])
    except Exception as exc:               # noqa: BLE001
        print("could not read DialHalo deliveries:", exc)
    for name in ("dialhalo.log", "outreach.log"):
        try:
            print(f"--- {name} (tail)")
            print("\n".join((EVIDENCE / name).read_text(errors="replace").splitlines()[-12:]))
        except OSError:
            pass


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print("ASSERTION:", exc)
        diagnose()
        sys.exit(2)
