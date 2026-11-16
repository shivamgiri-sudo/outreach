"""Integration Kit, Outreach side: bulk send (all numbers, owner by e-mail, idempotent, 500 chunks, opt-outs and other workspaces never sent),
auto-send, people matching with warnings, the user-wise calling report (+ CSV, DialHalo numbers merged), and the reconcile job (cursor,
idempotent with webhooks, workspace isolation). DialHalo is faked in memory."""
import json
from datetime import datetime, timedelta

import pytest

from app import calltracker as ct, calltracker_events as ev, calltracker_kit as kit
from tests.test_calltracker import FakeCT, KEY, SECRET, call_task, connect, conn_id, ct_call, hook, r as base_rig  # noqa: F401  (r = the shared rig fixture)


class FakeKit(FakeCT):
    def __init__(self):
        super().__init__()
        self.bulk = []               # every lead dict received by /leads/bulk, in order
        self.stats = {"members": []}
        self.feed = []               # call dicts the change feed serves, in order
        self.members = [{"name": "Ravi CT", "email": "ravi@x.in", "active": 1}, {"name": "Asha", "email": "asha@ct.in", "active": 1}]

    def __call__(self, method, url, headers, body):
        path = url.split("://", 1)[1].split("/", 1)[1]
        base = path.split("?")[0]
        if self.fail:
            self.requests.append((method, "/" + path, None, None))
            return self.fail.pop(0), {"message": "forced"}
        if base == "api/v1/leads/bulk" and method == "POST":
            data = json.loads(body)
            self.requests.append((method, "/" + path, data, headers.get("Authorization")))
            if self.fail:
                return self.fail.pop(0), {"message": "forced"}
            assert len(data["leads"]) <= 500
            saved, errors = [], []
            for i, l in enumerate(data["leads"]):
                if len(l.get("phones") or []) == 0:
                    errors.append({"index": i, "error": "phone is required"}); continue
                ref = l["externalId"]
                created = ref not in self.leads
                self.leads.setdefault(ref, {"id": f"ct-{len(self.leads) + 1}", "external_id": ref}).update(l)
                self.bulk.append(l)
                matched = l.get("ownerEmail") in {m["email"] for m in self.members}
                saved.append({"index": i, "id": self.leads[ref]["id"], "created": created, "owner_matched": matched if l.get("ownerEmail") else None, "external_id": ref})
            return 200, {"data": {"saved": saved, "errors": errors}}
        if base == "api/v1/members":
            self.requests.append((method, "/" + path, None, None))
            return 200, {"data": self.members}
        if base == "api/v1/stats/members":
            self.requests.append((method, "/" + path, None, None))
            return 200, {"data": self.stats}
        if base == "api/v1/sync/calls":
            self.requests.append((method, "/" + path, None, None))
            cursor = int(path.split("cursor=")[1]) if "cursor=" in path else 0
            page = self.feed[cursor:cursor + 2]                    # two per page: forces paging
            nxt = cursor + len(page)
            return 200, {"data": {"calls": page, "deleted": [], "next_cursor": str(nxt), "has_more": nxt < len(self.feed)}}
        return super().__call__(method, url, headers, body)


@pytest.fixture
def k(base_rig, monkeypatch):                    # noqa: F811
    fake = FakeKit()
    monkeypatch.setattr(ct, "TRANSPORT", fake)
    base_rig.fake = fake
    assert connect(base_rig).status_code == 200
    return base_rig


def leads_of(r, *companies):
    return [r.lead[c]["id"] for c in companies]


def test_bulk_send_all_numbers_owner_email_idempotent_and_scoped(k):
    k.set("Acme", phones=["98765 43210", "+91 98000 11111"]) if hasattr(k, "set") else None
    ids = leads_of(k, "Acme", "Beta Ltd")
    res = k.c.post("/api/calltracker/send", json={"lead_ids": ids}, headers=k.h["owner"])
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["sent"] == 2 and out["created"] == 2 and out["errors"] == []
    by_ref = {l["externalId"]: l for l in k.fake.bulk}
    acme = by_ref[k.lead["Acme"]["id"]]
    assert acme["phones"][0] == "9876543210" and len(acme["phones"]) == len(set(acme["phones"]))
    assert acme["ownerEmail"] == "ravi@x.in" and acme["sourceLabel"] == "MCN Outreach"
    top = [q for q in k.fake.requests if q[1] == "/api/v1/leads/bulk"][0][2]
    assert top["externalSource"] == "mcn_outreach" and top["existing"]["details"] == "update_blank_only"
    # the lead remembers it was sent
    assert ct.connection(k.store, "main") and k.store.read("leads")[0].get("calltracker") is not None
    # re-sending updates, never creates twins
    again = k.c.post("/api/calltracker/send", json={"lead_ids": ids}, headers=k.h["owner"]).json()
    assert again["created"] == 0 and again["updated"] == 2 and len(k.fake.leads) == 2
    # callers cannot send; another workspace's lead is never picked up through this workspace
    assert k.c.post("/api/calltracker/send", json={"lead_ids": ids}, headers=k.h["priya"]).status_code == 403
    other = [l["id"] for l in k.store.read("leads") if l.get("workspace") == k.w2]
    assert other and k.c.post("/api/calltracker/send", json={"lead_ids": other}, headers=k.h["owner"]).status_code == 400


def test_bulk_send_skips_opted_out_and_no_phone_and_chunks_at_500(k, monkeypatch):
    unsub = k.lead["Beta Ltd"]["id"]
    k.store.update_lead(unsub, lambda l: {**l, "stage": "unsubscribed"})
    k.store.update_lead(k.lead["Cora Inc"]["id"], lambda l: {**l, "phone": "", "phones": []})
    res = k.c.post("/api/calltracker/send", json={"lead_ids": leads_of(k, "Acme", "Beta Ltd", "Cora Inc")}, headers=k.h["owner"]).json()
    assert res["sent"] == 1 and res["requested"] == 2                      # unsubscribed never reaches the picker
    assert res["skipped"][0]["reason"] == "no phone number"
    assert all(l["externalId"] != unsub for l in k.fake.bulk)
    # 1200 leads -> three requests of at most 500
    rows = [{**k.lead["Acme"], "id": f"bulk-{i}", "phone": f"9{i:09d}", "phones": []} for i in range(1200)]
    monkeypatch.setattr(ct, "push_payload", lambda store, lead, base: {"name": "x", "company": "y", "note": "", "sourceLabel": "MCN Outreach", "url": "u", "city": "", "email": ""})
    monkeypatch.setattr(ct, "_lead_note", lambda *a, **kw: None)
    kit.send_leads(k.store, "main", rows, "http://localhost")
    sizes = [len(q[2]["leads"]) for q in k.fake.requests if q[1] == "/api/v1/leads/bulk"][1:]
    assert sizes == [500, 500, 200]


def test_bulk_failure_is_reported_not_silent(k):
    k.fake.fail = [500]
    res = k.c.post("/api/calltracker/send", json={"lead_ids": leads_of(k, "Acme")}, headers=k.h["owner"])
    assert res.status_code == 502 and "busy or down" in res.text


def test_auto_send_only_for_people_who_are_dialhalo_members(k):
    assert kit.auto_send(k.store, "main", "http://x")["sent"] == 0          # off by default
    assert k.c.put("/api/calltracker", json={"workspace": "main", "auto_send": True}, headers=k.h["owner"]).json()["connection"]["auto_send"] is True
    out = kit.auto_send(k.store, "main", "http://x")
    # Acme belongs to Ravi (a DialHalo member); Beta belongs to Priya (no DialHalo member): only Acme goes
    assert out["sent"] == 1 and {l["externalId"] for l in k.fake.bulk} == {k.lead["Acme"]["id"]}
    assert kit.auto_send(k.store, "main", "http://x")["sent"] == 0          # already sent: nothing again


def test_people_matching_warns_about_owners_with_leads_but_no_member(k):
    res = k.c.get("/api/calltracker/people", headers=k.h["owner"])
    assert res.status_code == 200, res.text
    p = res.json()
    by = {r["email"]: r for r in p["people"]}
    assert by["ravi@x.in"]["matched"] and by["ravi@x.in"]["leads"] == 1
    assert not by["priya@x.in"]["matched"] and by["priya@x.in"]["leads"] == 1
    assert [w["email"] for w in p["warnings"]] == ["priya@x.in"] and "no DialHalo member" in p["warnings"][0]["reason"]
    assert "asha@ct.in" in p["only_in_dialhalo"]
    assert k.c.get("/api/calltracker/people", headers=k.h["priya"]).status_code == 403


def _apply(k, cid, outcome, who_email="ravi@x.in", connected=True, ref=None, **extra):
    conn = ct.connection(k.store, "main")
    call = ct_call(cid, outcome=outcome, connected=connected, ref=ref or k.lead["Acme"]["id"], **extra)
    call["user"] = {"name": who_email.split("@")[0].title(), "email": who_email}
    assert ev.apply_call(k.store, conn, call)


def test_people_report_counts_per_person_merges_dialhalo_and_exports_csv(k):
    _apply(k, 1, "interested")
    _apply(k, 2, "not_interested", connected=True)
    _apply(k, 3, None, connected=False)
    _apply(k, 4, "converted", who_email="asha@ct.in", ref=k.lead["Acme"]["id"])
    k.fake.stats = {"members": [{"email": "ravi@x.in", "name": "Ravi", "calls": 9, "connected": 6, "talk_sec": 600, "lead_calls": 8, "unique_leads_called": 5},
                                {"email": "idle@ct.in", "name": "Idle", "calls": 0, "connected": 0, "talk_sec": 0, "lead_calls": 0, "unique_leads_called": 0}]}
    res = k.c.get("/api/calltracker/people-report?from=2026-11-01&to=2026-11-30", headers=k.h["owner"])
    assert res.status_code == 200, res.text
    rows = {r["email"]: r for r in res.json()["rows"]}
    assert rows["ravi@x.in"]["calls"] == 3 and rows["ravi@x.in"]["interested"] == 1 and rows["ravi@x.in"]["leads_called"] == 1
    assert rows["ravi@x.in"]["dialhalo_calls"] == 9 and rows["ravi@x.in"]["dialhalo_talk_minutes"] == 10.0
    assert rows["asha@ct.in"]["meetings"] == 1 and "dialhalo_calls" not in rows["asha@ct.in"]
    assert rows["idle@ct.in"]["calls"] == 0                                     # DialHalo-only person still listed
    # dates outside the range exclude the calls
    late = k.c.get("/api/calltracker/people-report?from=2027-01-01&to=2027-01-31", headers=k.h["owner"]).json()["rows"]
    assert all(r["calls"] == 0 for r in late)
    csv_res = k.c.get("/api/calltracker/people-report?from=2026-11-01&to=2026-11-30&format=csv", headers=k.h["owner"])
    assert csv_res.status_code == 200 and csv_res.text.splitlines()[0].startswith("name,email,calls") and "ravi@x.in" in csv_res.text
    # DialHalo down: the report still shows Outreach's side
    k.fake.fail = [500, 500, 500]
    assert k.c.get("/api/calltracker/people-report?from=2026-11-01&to=2026-11-30", headers=k.h["owner"]).status_code == 200


def test_csv_cannot_inject_formulas():
    out = kit.report_csv({"rows": [{"name": "=HYPERLINK(\"x\")", "email": "a@b.c", "calls": 1}]})
    assert "'=HYPERLINK" in out


def test_reconcile_fills_missed_calls_once_and_resumes_from_the_cursor(k):
    lead_id = k.lead["Acme"]["id"]
    k.c.post("/api/calltracker/send", json={"lead_ids": [lead_id]}, headers=k.h["owner"])
    refd = [ct_call(i, ref=lead_id, outcome="interested") for i in (11, 12, 13)]
    k.fake.calls = {c["id"]: c for c in refd}
    k.fake.feed = [{"id": 11, "number": "9876543210", "lead_id": "ct-1"}, {"id": 12, "number": "9876543210", "lead_id": "ct-1"},
                   {"id": 99, "number": "9111111111", "lead_id": None},                   # not an Outreach lead: ignored
                   {"id": 13, "number": "9876543210", "lead_id": "ct-1"}]
    out = k.c.post("/api/calltracker/reconcile", json={"workspace": "main"}, headers=k.h["owner"]).json()
    assert out["matched"] == 3 and out["applied"] == 3 and out["pages"] == 2
    acts = [a for a in k.store.read("activities") if a.get("source") == "calltracker"]
    assert len(acts) == 3
    # running again from the saved cursor adds nothing, and a webhook for a call reconcile already applied does not duplicate it
    again = k.c.post("/api/calltracker/reconcile", json={"workspace": "main"}, headers=k.h["owner"]).json()
    assert again["matched"] == 0
    conn = ct.connection(k.store, "main")
    ev.apply_call(k.store, conn, refd[0])
    assert len([a for a in k.store.read("activities") if a.get("source") == "calltracker"]) == 3
    assert ct.connection(k.store, "main")["sync_cursor"] == "4"


def test_reconcile_due_runs_only_stale_connections_and_records_failures(k):
    ran = kit.reconcile_due(k.store)
    assert ran == ["main"]
    assert kit.reconcile_due(k.store) == []                                           # ran just now
    later = ct.now_utc() + timedelta(hours=21)
    k.fake.fail = [500]
    assert kit.reconcile_due(k.store, now=later) == []                                # fails -> not counted, status noted
    assert ct.connection(k.store, "main")["last_reconcile_status"]["ok"] is False
    assert kit.reconcile_due(k.store, now=later) == ["main"]
