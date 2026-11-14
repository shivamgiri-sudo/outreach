"""Call Tracker integration: connection per workspace, push to the dial queue (idempotent, owner by email, retries),
signed webhooks (good / bad / stale / replay / duplicate), call -> lead matching, outcome mapping, task completion,
sequence stop, opt-outs both ways, workspace isolation and the funnel numbers. Call Tracker is faked in memory."""
import json
import time
from datetime import timedelta

import pytest

from app import calltracker as ct, calltracker_events as ev
from tests.ownership_helpers import lead, rig as make_rig

KEY = "ctk_test_key_123456789"
SECRET = "whsec_test_secret_abc"


class FakeCT:
    """Just enough of Call Tracker's /api/v1 for the integration, with a switch to fail."""

    def __init__(self):
        self.requests, self.leads, self.calls, self.dnc = [], {}, {}, []
        self.fail = []                     # statuses to answer with, one per request

    def __call__(self, method, url, headers, body):
        path = url.split("://", 1)[1].split("/", 1)[1]
        data = json.loads(body) if body else None
        self.requests.append((method, "/" + path, data, headers.get("Authorization")))
        if self.fail:
            return self.fail.pop(0), {"message": "forced"}
        if path.startswith("api/v1/leads/external/mcn_outreach/") and method == "PUT":
            ref = path.rsplit("/", 1)[1]
            created = ref not in self.leads
            row = self.leads.setdefault(ref, {"id": f"ct-{len(self.leads) + 1}", "external_id": ref})
            row.update(data)
            owner = {"id": "u1", "name": "Ravi CT", "email": data["ownerEmail"]} if data.get("ownerEmail") == "ravi@x.in" else {"id": "u9", "name": "Round Robin"}
            return (201 if created else 200), {"data": {"lead": row, "created": created, "owner": owner, "ownerMatched": owner["id"] == "u1", "dnc": False}}
        if path == "api/v1/members":
            return 200, {"data": [{"name": "Ravi CT", "email": "ravi@x.in", "active": 1}, {"name": "Asha", "email": "asha@ct.in", "active": 1}]}
        if path.startswith("api/v1/dnc") and method == "GET":
            return 200, {"data": []}
        if path == "api/v1/dnc" and method == "POST":
            self.dnc.append(data)
            return 200, {"data": {"added": True}}
        if path.startswith("api/v1/calls/"):
            call = self.calls.get(int(path.rsplit("/", 1)[1]))
            return (200, {"data": call}) if call else (404, {"message": "Call not found"})
        return 404, {}


@pytest.fixture
def r(tmp_path, monkeypatch):
    fake = FakeCT()
    monkeypatch.setattr(ct, "TRANSPORT", fake)
    monkeypatch.setattr(ct, "KICK", False)
    monkeypatch.setattr(ct, "RESOLVER", lambda host: ["93.184.216.34"])
    monkeypatch.setenv("OUTREACH_SECRET_KEY", "a-long-test-secret-key-for-sealing-values")
    rig = make_rig(tmp_path, mode_on=False)
    rig.fake = fake
    return rig


def connect(r, ws="main", who="owner", **extra):
    body = {"workspace": ws, "base_url": "https://calls.example.com", "public_url": "https://calls.example.com", "api_key": KEY,
            "webhook_secret": SECRET, **extra}
    return r.c.put("/api/calltracker", json=body, headers=r.h[who])


def hook(r, conn_id, payload, ts=None, secret=SECRET, sig=None):
    raw = json.dumps(payload).encode()
    ts = int(time.time()) if ts is None else ts
    header = f"t={ts},v1={sig or ev.sign(secret, ts, raw)}"
    return r.c.post(f"/api/calltracker/hook/{conn_id}", content=raw, headers={"X-CallTracker-Signature": header, "Content-Type": "application/json"})


def call_task(r, company, owner=None):
    body = {"lead_id": r.lead[company]["id"], "title": "Call them", "text": "Replied yes: wants a demo", "kind": "call",
            "due_at": "2026-12-01T10:00:00+05:30", **({"owner": owner} if owner else {})}
    res = r.c.post("/api/tasks", json=body, headers=r.h["owner"])
    assert res.status_code == 200, res.text
    return res.json()


def ct_call(cid, number="9876543210", outcome=None, connected=True, ref=None, **extra):
    return {"id": cid, "direction": "outgoing", "connected": connected, "number": number, "started_at": "2026-11-13 11:00:00",
            "duration_sec": 95 if connected else 0, "note": "Spoke to HR", "outcome": outcome, "callback_at": None,
            "user": {"name": "Ravi CT", "email": "ravi@x.in"}, "lead": ({"id": "ct-1", "external_source": "mcn_outreach", "external_id": ref} if ref else None),
            "has_recording": True, "ai_summary": "Wants a demo next week", "links": {"call": "http://127.0.0.1:8081/calls?from=2026-11-13&to=2026-11-13", "lead": None}, **extra}


def conn_id(r, ws="main"):
    return ct.connection(r.store, ws)["id"]


# ---------------------------------------------------------------- connection
def test_connect_masks_and_seals_secrets_and_audits_key_names_only(r):
    res = connect(r)
    assert res.status_code == 200, res.text
    card = res.json()
    assert card["connection"]["api_key_set"] and card["connection"]["webhook_secret_set"]
    assert KEY not in res.text and SECRET not in res.text
    assert card["receiver"]["public"].endswith(f"/api/calltracker/hook/{card['connection']['id']}")
    raw = r.store.path.read_text()
    assert KEY not in raw and SECRET not in raw and "enc:v1:" in raw          # sealed at rest
    row = [a for a in r.store.read("audit") if a["action"] == "calltracker.connect"][-1]
    assert "api_key" in row["detail"] and KEY not in json.dumps(row)
    # a later save without secrets keeps them
    assert r.c.put("/api/calltracker", json={"workspace": "main", "enabled": True}, headers=r.h["owner"]).json()["connection"]["api_key_set"]
    from app.backup import build_backup
    assert KEY not in json.dumps(build_backup(r.store)) and SECRET not in json.dumps(build_backup(r.store))


def test_url_rules_and_roles(r, monkeypatch):
    assert connect(r, base_url="http://127.0.0.1:8081").status_code == 400          # internal: only when the server owner allows it
    monkeypatch.setenv("OUTREACH_ALLOW_PRIVATE_HOSTS", "127.0.0.1:8081")
    assert connect(r, base_url="http://127.0.0.1:8081").status_code == 200
    assert connect(r, base_url="http://127.0.0.1:22").status_code == 400            # same host, other port: refused
    assert connect(r, base_url="ftp://calls.example.com").status_code == 400
    assert connect(r, api_key="not-a-key").status_code == 400
    assert connect(r, who="priya").status_code == 403                                # callers cannot connect
    assert r.c.get("/api/calltracker", headers=r.h["view"]).status_code == 403
    assert connect(r, ws="main", who="adm2").status_code == 403                      # a workspace admin: not main,
    assert connect(r, ws=r.w2, who="adm2").status_code == 200                        # only its own workspace
    assert r.c.get(f"/api/calltracker?workspace={r.w2}", headers=r.h["adm"]).status_code == 403
    assert r.c.delete(f"/api/calltracker?workspace={r.w2}", headers=r.h["adm2"]).status_code == 200
    assert any(a["action"] == "calltracker.disconnect" for a in r.store.read("audit"))


def test_test_connection_previews_owner_matching(r):
    connect(r)
    out = r.c.post("/api/calltracker/test", json={"workspace": "main"}, headers=r.h["owner"]).json()
    assert out["ok"] and out["members"] == 2
    assert {p["email"]: p["matched"] for p in out["owners"]}["ravi@x.in"] is True
    r.fake.fail = [401]
    bad = r.c.post("/api/calltracker/test", json={"workspace": "main"}, headers=r.h["owner"]).json()
    assert not bad["ok"] and "API key" in bad["message"] and KEY not in bad["message"]


# ---------------------------------------------------------------- push
def test_call_task_pushes_once_per_lead_idempotently_with_owner_by_email(r):
    connect(r)
    call_task(r, "Acme", owner="Ravi")
    jobs = r.store.read("ct_outbox")
    assert len(jobs) == 1 and jobs[0]["kind"] == "push"
    call_task(r, "Acme", owner="Ravi")                                               # second task before the push: merged
    assert len([j for j in r.store.read("ct_outbox") if j["status"] == "pending"]) == 1
    assert ct.process_due(r.store) == 1
    method, path, body, auth = r.fake.requests[-1]
    assert (method, path, auth) == ("PUT", f"/api/v1/leads/external/mcn_outreach/{r.lead['Acme']['id']}", f"Bearer {KEY}")
    assert body["ownerEmail"] == "ravi@x.in" and body["phone"] and body["sourceLabel"] == "MCN Outreach"
    assert "Replied yes" in body["note"] and body["followupAtMs"] and body["url"].endswith(f"#leads?lead={r.lead['Acme']['id']}")
    saved = lead(r, "Acme")
    assert saved["calltracker"]["lead_id"] == "ct-1" and saved["calltracker"]["owner_matched"]
    assert sum("Sent to Call Tracker" in e["text"] for e in saved["timeline"]) == 1
    call_task(r, "Acme")                                                             # a later task re-pushes the same lead
    ct.process_due(r.store)
    assert len(r.fake.leads) == 1 and len([q for q in r.fake.requests if q[0] == "PUT"]) == 2
    assert sum("Sent to Call Tracker" in e["text"] for e in lead(r, "Acme")["timeline"]) == 1


def test_unknown_owner_lets_call_tracker_distribute_and_unconnected_workspace_is_untouched(r):
    connect(r)
    call_task(r, "Cora Inc", owner="Someone Unknown")
    ct.process_due(r.store)
    assert "ownerEmail" not in r.fake.requests[-1][2]
    assert lead(r, "Cora Inc")["calltracker"]["owner"] == "Round Robin"
    r.c.post("/api/tasks", json={"lead_id": r.other["id"], "title": "Call", "kind": "call"}, headers={**r.h["owner"], "X-Workspace": r.w2})
    assert not [j for j in r.store.read("ct_outbox") if j.get("lead_id") == r.other["id"]]      # w2 has no connection


def test_retries_with_backoff_then_fails_visibly_and_retry_requeues(r):
    connect(r)
    call_task(r, "Acme", owner="Ravi")
    r.fake.fail = [503]
    ct.process_due(r.store)
    job = r.store.read("ct_outbox")[0]
    assert job["status"] == "pending" and job["attempts"] == 1 and "busy or down" in job["error"]
    assert ct.process_due(r.store) == 0                                              # not due yet (backs off)
    assert any("Retrying automatically" in e["text"] for e in lead(r, "Acme")["timeline"])
    later = ct.now_utc() + timedelta(minutes=2)
    assert ct.process_due(r.store, now=later) == 1 and r.store.read("ct_outbox")[0]["status"] == "done"
    # a refused key is not retried: failed at once, shown on the card, Retry puts it back
    call_task(r, "Beta Ltd")
    r.fake.fail = [401]
    ct.process_due(r.store)
    card = r.c.get("/api/calltracker?workspace=main", headers=r.h["owner"]).json()
    assert card["queue"]["failed"] == 1 and "refused the API key" in card["queue"]["failed_rows"][0]["error"]
    assert lead(r, "Beta Ltd")["calltracker"]["status"] == "failed"
    assert card["connection"]["last_push"]["ok"] is False
    assert r.c.post("/api/calltracker/retry", json={"workspace": "main"}, headers=r.h["owner"]).json()["requeued"] == 1
    ct.process_due(r.store)
    assert lead(r, "Beta Ltd")["calltracker"]["status"] == "ok"


def test_no_phone_fails_with_a_clear_reason(r):
    connect(r)
    r.set("Cora Inc", phone="", phones=[])
    call_task(r, "Cora Inc")
    ct.process_due(r.store)
    assert r.store.read("ct_outbox")[0]["status"] == "failed" and "no phone" in r.store.read("ct_outbox")[0]["error"]


# ---------------------------------------------------------------- webhook signature
def test_signature_good_bad_stale_replay_and_duplicate(r):
    connect(r)
    cid = conn_id(r)
    acme = r.lead["Acme"]["id"]
    r.fake.calls[7] = ct_call(7, ref=acme, outcome="Interested")
    payload = {"id": "evt-1", "event": "call.outcome", "createdAt": "x", "data": {"callId": 7}}
    assert hook(r, cid, payload, sig="0" * 64).status_code == 401
    assert hook(r, cid, payload, secret="whsec_wrong").status_code == 401
    assert hook(r, cid, payload, ts=int(time.time()) - 3600).status_code == 401
    assert hook(r, "nope", payload).status_code == 401                               # unknown receiver: not public at all
    ts = int(time.time())
    first = hook(r, cid, payload, ts=ts)
    assert first.status_code == 200 and first.json()["applied"] == 1
    assert hook(r, cid, payload, ts=ts).status_code == 409                           # same signature again: replay
    again = hook(r, cid, payload, ts=ts + 1)                                         # Call Tracker's retry: new signature
    assert again.status_code == 200 and again.json()["duplicate"]
    assert len([a for a in r.store.read("activities") if a.get("source") == "calltracker"]) == 1


def test_call_tracker_down_answers_503_so_it_retries(r):
    connect(r)
    r.fake.fail = [502]
    ts = int(time.time())
    res = hook(r, conn_id(r), {"id": "evt-2", "event": "call.outcome", "data": {"callId": 9}}, ts=ts)
    assert res.status_code == 503
    r.fake.calls[9] = ct_call(9, ref=r.lead["Acme"]["id"])
    retry = hook(r, conn_id(r), {"id": "evt-2", "event": "call.outcome", "data": {"callId": 9}}, ts=ts + 60)     # Call Tracker retries later
    assert retry.json()["applied"] == 1


# ---------------------------------------------------------------- calls on the lead
def test_call_on_timeline_closes_call_task_moves_stage_records_consent_and_follow_up(r):
    connect(r)
    acme = r.lead["Acme"]["id"]
    call_task(r, "Acme", owner="Ravi")
    r.fake.calls[1] = ct_call(1, ref=acme, outcome=None, connected=True)
    hook(r, conn_id(r), {"id": "e1", "event": "calls.synced", "data": {"calls": [{"id": 1, "number_norm": "9876543210"}]}})
    tasks = [t for t in r.store.read("tasks") if t.get("lead_id") == acme and t["kind"] == "call"]
    assert all(t["status"] == "open" for t in tasks)                                 # no outcome yet: task stays open
    r.fake.calls[1] = ct_call(1, ref=acme, outcome="Interested", callback_at="2099-01-02 11:00:00")
    hook(r, conn_id(r), {"id": "e2", "event": "call.outcome", "data": {"callId": 1}})
    rows = [a for a in r.store.read("activities") if a.get("source") == "calltracker"]
    assert len(rows) == 1                                                            # one call, updated in place
    a = rows[0]
    assert a["outcome"] == "interested" and a["direction"] == "out" and a["ct"]["duration_sec"] == 95 and a["ct"]["connected"]
    assert "AI summary: Wants a demo" in a["body"] and a["ct"]["links"]["call"].startswith("https://calls.example.com/calls?")
    assert all(t["status"] == "done" for t in r.store.read("tasks") if t.get("lead_id") == acme and t["kind"] == "call")
    saved = lead(r, "Acme")
    assert saved["stage"] == "interested" and saved["consent"]
    assert any(t["title"] == "Call back (Call Tracker)" and t["due_at"].startswith("2099-01-02") for t in r.store.read("tasks"))
    assert any(t["title"] == "Follow up after the yes" for t in r.store.read("tasks") if t.get("lead_id") == acme)
    hook(r, conn_id(r), {"id": "e3", "event": "call.transcribed", "data": {"callId": 1}})       # later events: no second call, no re-apply
    assert len([a for a in r.store.read("activities") if a.get("source") == "calltracker"]) == 1
    assert len([t for t in r.store.read("tasks") if t["title"] == "Call back (Call Tracker)"]) == 1
    # the call log counts it once
    log = r.c.get("/api/call-log", headers=r.h["owner"]).json()
    assert log["total"] == 1


def test_not_interested_and_wrong_number_stop_the_sequence(r):
    connect(r)
    beta, cora = r.lead["Beta Ltd"]["id"], r.lead["Cora Inc"]["id"]
    for lid in (beta, cora):
        r.store.update_lead(lid, lambda l: {**l, "stage": "contacted", "sequence": {"step": 1, "stopped": None, "history": []}})
    r.fake.calls[2] = ct_call(2, number="9123456789", ref=beta, outcome="Not interested")
    r.fake.calls[3] = ct_call(3, number="9000011111", ref=cora, outcome="Wrong number", connected=False)
    hook(r, conn_id(r), {"id": "e4", "event": "call.outcome", "data": {"callId": 2}})
    hook(r, conn_id(r), {"id": "e5", "event": "call.outcome", "data": {"callId": 3}})
    b, c = lead(r, "Beta Ltd"), lead(r, "Cora Inc")
    assert b["stage"] == "disqualified" and b["sequence"]["stopped"].startswith("calltracker:")
    assert c["sequence"]["stopped"] and c["phone_status"]["status"] == "wrong_number"
    assert c["do_not_contact"] and "tej@cora.io" in r.store.read("suppressed")       # default: wrong number suppresses the contact
    assert not r.fake.dnc                                                            # never echoed back to Call Tracker


def test_converted_books_a_meeting_and_mapping_is_editable_per_workspace(r):
    connect(r)
    acme = r.lead["Acme"]["id"]
    r.store.update_lead(acme, lambda l: {**l, "stage": "contacted"})
    r.fake.calls[4] = ct_call(4, ref=acme, outcome="Converted")
    hook(r, conn_id(r), {"id": "e6", "event": "call.outcome", "data": {"callId": 4}})
    assert lead(r, "Acme")["stage"] == "call_done"
    assert [a["outcome"] for a in r.store.read("activities") if a.get("source") == "calltracker"] == ["meeting_booked"]
    # custom: "Converted" now means trial, and an extra outcome name is understood
    mapping = {"converted": {"outcome": "meeting_booked", "stage": "trial"}, "demo fixed": {"outcome": "meeting_booked", "stage": "trial", "stop_sequence": True}}
    saved = connect(r, mapping=mapping).json()["connection"]
    assert saved["mapping_custom"] and saved["mapping"]["demo fixed"]["stage"] == "trial"
    r.fake.calls[5] = ct_call(5, ref=acme, outcome="Demo fixed")
    hook(r, conn_id(r), {"id": "e7", "event": "call.outcome", "data": {"callId": 5}})
    assert lead(r, "Acme")["stage"] == "trial"
    assert connect(r, reset_mapping=True).json()["connection"]["mapping"] == ct.DEFAULT_MAPPING


def test_matching_by_phone_and_workspace_isolation(r):
    connect(r)
    connect(r, ws=r.w2, webhook_secret="whsec_other_workspace")
    r.set("Other WS Co", id=r.other["id"], phone="98765 43210")                      # same number as Acme, other workspace
    r.fake.calls[6] = ct_call(6, number="+91 98765 43210", ref=None, outcome="Not interested")
    hook(r, conn_id(r), {"id": "e8", "event": "call.outcome", "data": {"callId": 6}})
    assert lead(r, "Acme")["stage"] == "disqualified"
    assert next(l for l in r.store.read("leads") if l["id"] == r.other["id"])["stage"] != "disqualified"
    # workspace A's connection pointing at B's lead id still never touches it
    r.fake.calls[8] = ct_call(8, number="000", ref=r.other["id"], outcome="Not interested")
    assert hook(r, conn_id(r), {"id": "e9", "event": "call.outcome", "data": {"callId": 8}}).json()["applied"] == 0
    # B's secret does not open A's receiver
    assert hook(r, conn_id(r), {"id": "e10", "event": "call.outcome", "data": {"callId": 6}}, secret="whsec_other_workspace").status_code == 401
    assert next(l for l in r.store.read("leads") if l["id"] == r.other["id"])["stage"] != "disqualified"


# ---------------------------------------------------------------- opt-outs both ways
def test_unsubscribe_sends_do_not_call_and_call_tracker_dnc_suppresses_without_echo(r):
    connect(r)
    acme = r.lead["Acme"]["id"]
    from app.unsubscribe import unsub_token
    assert r.c.post(f"/u/{unsub_token(r.store, acme)}").status_code == 200
    jobs = [j for j in r.store.read("ct_outbox") if j["kind"] == "dnc"]
    assert len(jobs) == 1 and jobs[0]["phone"] == "9876543210"
    ct.process_due(r.store)
    assert r.fake.dnc[-1]["phone"] == "9876543210" and r.fake.dnc[-1]["source"] == "mcn_outreach"
    assert lead(r, "Acme")["calltracker"]["dnc"]
    # Call Tracker -> Outreach: a number marked do-not-call there
    beta = r.lead["Beta Ltd"]["id"]
    res = hook(r, conn_id(r), {"id": "e11", "event": "dnc.added", "data": {"phone": "9123456789", "source": "outcome", "reason": "Asked on the call",
                                                                           "leads": [{"leadId": "ct-2", "externalId": beta, "externalSource": "mcn_outreach"}]}})
    assert res.json()["applied"] == 1
    b = lead(r, "Beta Ltd")
    assert b["stage"] == "unsubscribed" and b["do_not_contact"] and "sita@beta.in" in r.store.read("suppressed")
    assert any(row["event"] == "do_not_contact" and row["lead_id"] == beta for row in r.store.read("consent_ledger"))
    assert not [j for j in r.store.read("ct_outbox") if j["kind"] == "dnc" and j["lead_id"] == beta]      # not echoed back
    # our own request coming back is ignored; removal on their side never un-suppresses here
    assert hook(r, conn_id(r), {"id": "e12", "event": "dnc.added", "data": {"phone": "9876543210", "source": "mcn_outreach", "leads": []}}).json()["applied"] == 0
    hook(r, conn_id(r), {"id": "e13", "event": "dnc.removed", "data": {"phone": "9123456789", "source": "manual", "leads": []}})
    assert lead(r, "Beta Ltd")["stage"] == "unsubscribed"


def test_moving_to_unsubscribed_and_bulk_do_not_contact_queue_do_not_call(r):
    connect(r)
    r.c.post(f"/api/leads/{r.lead['Cora Inc']['id']}/stage", json={"stage": "unsubscribed"}, headers=r.h["owner"])
    assert r.c.post("/api/leads/bulk", json={"lead_ids": [r.lead["Beta Ltd"]["id"]], "action": "do_not_contact"}, headers=r.h["owner"]).json()["ok"] == 1
    phones = {j["phone"] for j in r.store.read("ct_outbox") if j["kind"] == "dnc"}
    assert phones == {"9000011111", "9123456789"}


# ---------------------------------------------------------------- funnel
def test_funnel_counts_real_calls_in_scope(r):
    connect(r)
    acme, beta = r.lead["Acme"]["id"], r.lead["Beta Ltd"]["id"]
    call_task(r, "Acme")
    ct.process_due(r.store)
    r.fake.calls[1] = ct_call(1, ref=acme, outcome="Converted")
    r.fake.calls[2] = ct_call(2, number="9123456789", ref=beta, outcome=None, connected=False)
    hook(r, conn_id(r), {"id": "f1", "event": "call.outcome", "data": {"callId": 1}})
    hook(r, conn_id(r), {"id": "f2", "event": "call.outcome", "data": {"callId": 2}})
    f = r.c.get("/api/calltracker/funnel?from=2026-11-13&to=2026-11-13", headers=r.h["owner"]).json()
    assert (f["calls"], f["called"], f["connected"], f["meeting_booked"]) == (2, 2, 1, 1)
    assert f["sent_total"] == 1
    assert r.c.get("/api/calltracker/funnel?from=2026-11-14&to=2026-11-14", headers=r.h["owner"]).json()["calls"] == 0
    other = r.c.get("/api/calltracker/funnel", headers={**r.h["adm2"], "X-Workspace": r.w2}).json()
    assert other["calls"] == 0                                                       # other workspace's numbers stay apart


def test_employer_replying_yes_puts_the_lead_in_the_dial_queue(r):
    connect(r)
    acme = r.lead["Acme"]["id"]
    res = r.c.post(f"/api/leads/{acme}/reply", json={"text": "Yes, interested. Please call me tomorrow."}, headers=r.h["owner"])
    assert res.status_code == 200, res.text
    assert any(t["kind"] == "call" and t.get("lead_id") == acme for t in r.store.read("tasks"))
    ct.process_due(r.store)
    put = [q for q in r.fake.requests if q[0] == "PUT"][-1]
    assert put[1].endswith(acme) and "Said yes" in put[2]["note"]
    assert lead(r, "Acme")["calltracker"]["status"] == "ok"
    # an unsubscribe reply goes to the do-not-call list
    r.c.post(f"/api/leads/{r.lead['Beta Ltd']['id']}/reply", json={"text": "Please unsubscribe me, stop emailing."}, headers=r.h["owner"])
    assert any(j["kind"] == "dnc" and j["phone"] == "9123456789" for j in r.store.read("ct_outbox"))


def test_workspace_delete_removes_its_connection(r):
    connect(r, ws=r.w2)
    assert ct.connection(r.store, r.w2)
    res = r.c.request("DELETE", f"/api/workspaces/{r.w2}", json={"confirm_name": "Client Two", "move_to": "main"}, headers=r.h["owner"])
    assert res.status_code == 200, res.text
    assert ct.connection(r.store, r.w2) is None


# ---------------------------------------------------------------- small fixes after the integration review
def test_no_push_for_a_lead_who_opted_out(r):
    """An unsubscribed / do-not-contact / consent-withdrawn lead is never sent to be dialled, at queue time or at run time."""
    connect(r)
    for company, change in (("Acme", {"stage": "unsubscribed"}), ("Beta Ltd", {"do_not_contact": True}), ("Cora Inc", {"consent_withdrawn_at": "2026-11-01T00:00:00+00:00"})):
        r.set(company, **change)
        call_task(r, company)
    assert [j for j in r.store.read("ct_outbox") if j["kind"] == "push"] == []        # nothing was even queued
    # queued first, opted out afterwards: refused when the job runs, with a plain note and no retry
    r.set("Acme", stage="new")
    call_task(r, "Acme")
    r.set("Acme", do_not_contact=True)
    ct.process_due(r.store)
    assert not [q for q in r.fake.requests if q[0] == "PUT"]
    job = next(j for j in r.store.read("ct_outbox") if j["kind"] == "push")
    assert job["status"] == "failed" and "do-not-contact" in job["error"] and job["attempts"] == 1


def test_funnel_connected_uses_the_one_narrow_definition(r):
    """A 95-second 'Not interested' call got through, but the Leaderboard does not count it as connected, and neither may the funnel."""
    connect(r)
    acme, beta = r.lead["Acme"]["id"], r.lead["Beta Ltd"]["id"]
    r.fake.calls[1] = ct_call(1, ref=acme, outcome="Not interested", connected=True)
    r.fake.calls[2] = ct_call(2, number="9123456789", ref=beta, outcome="Interested", connected=True)
    hook(r, conn_id(r), {"id": "g1", "event": "call.outcome", "data": {"callId": 1}})
    hook(r, conn_id(r), {"id": "g2", "event": "call.outcome", "data": {"callId": 2}})
    f = r.c.get("/api/calltracker/funnel?from=2026-11-13&to=2026-11-13", headers=r.h["owner"]).json()
    assert (f["calls"], f["connected"], f["interested"]) == (2, 1, 1)


def test_new_dialhalo_signature_header_is_accepted_as_well_as_the_old_one(r):
    connect(r)
    cid = conn_id(r)
    r.fake.calls[3] = ct_call(3, ref=r.lead["Acme"]["id"], outcome="Interested")
    raw = json.dumps({"id": "h1", "event": "call.outcome", "data": {"callId": 3}}).encode()
    ts = int(time.time())
    header = f"t={ts},v1={ev.sign(SECRET, ts, raw)}"
    new = r.c.post(f"/api/calltracker/hook/{cid}", content=raw, headers={"X-DialHalo-Signature": header, "Content-Type": "application/json"})
    assert new.status_code == 200 and new.json()["applied"] == 1
    assert r.c.post(f"/api/calltracker/hook/{cid}", content=raw, headers={"Content-Type": "application/json"}).status_code == 401   # no header still refused


def test_unmapped_outcome_names_are_remembered_and_shown_on_the_card(r):
    connect(r)
    acme, beta = r.lead["Acme"]["id"], r.lead["Beta Ltd"]["id"]
    r.fake.calls[1] = ct_call(1, ref=acme, outcome="Promise to pay", connected=True)
    r.fake.calls[2] = ct_call(2, number="9123456789", ref=beta, outcome="promise  to pay", connected=False)
    r.fake.calls[3] = ct_call(3, number="9000000003", ref=None, outcome="Interested", connected=True)          # mapped: not listed
    for i in (1, 2, 3):
        hook(r, conn_id(r), {"id": f"u{i}", "event": "call.outcome", "data": {"callId": i}})
    view = r.c.get("/api/calltracker", headers=r.h["owner"]).json()
    rows = (view.get("connection") or view).get("unmapped_outcomes") if isinstance(view, dict) else None
    assert rows and len(rows) == 1 and rows[0]["count"] == 2 and rows[0]["name"].lower().startswith("promise")
    assert rows[0]["counted_as"] in ("connected", "no_answer")
