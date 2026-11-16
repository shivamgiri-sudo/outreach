"""Erasing a person in Outreach also erases them in Call Tracker (DialHalo) and blocks their number there: the queued
job, what it carries, retries and visible failure, no duplicates, delete paths, workspace isolation, re-import."""
import json
from datetime import timedelta

from app import calltracker as ct
from tests.test_calltracker import connect, call_task, r  # noqa: F401  (r is the fixture)

ACME_PHONE, BETA_PHONE = "9876543210", "9123456789"


def erase(r, company, who="owner", extra=None):
    return r.c.post(f"/api/leads/{r.lead[company]['id']}/erase", json={"confirm": True}, headers={**r.h[who], **(extra or {})})


def jobs(r, kind="erase"):
    return [j for j in r.store.read("ct_outbox") if j["kind"] == kind]


def push_and_run(r, company):
    call_task(r, company)
    ct.process_due(r.store)
    assert r.fake.leads, "the lead should be in Call Tracker"


def test_erase_queues_one_job_and_call_tracker_gets_delete_and_dnc_per_phone(r):
    connect(r)
    push_and_run(r, "Acme")
    ref = r.lead["Acme"]["id"]
    r.set("Acme", phones=["+91 98765 43210", "80000 22222"])
    assert erase(r, "Acme").status_code == 200
    job = jobs(r)[0]
    assert len(jobs(r)) == 1 and job["lead_id"] == ref and job["ws"] == "main" and job["status"] == "pending"
    assert sorted(job["phones"]) == ["8000022222", ACME_PHONE]
    assert ct.process_due(r.store) == 1
    assert ref in r.fake.erased
    sent = [(m, p) for m, p, *_ in r.fake.requests]
    assert ("DELETE", f"/api/v1/leads/external/mcn_outreach/{ref}") in sent
    assert sorted(d["phone"] for d in r.fake.dnc if d["reason"] == "Erased at the source") == ["8000022222", ACME_PHONE]
    assert {ACME_PHONE, "8000022222"} <= r.fake.blocked
    assert all(d["source"] == "mcn_outreach" for d in r.fake.dnc)
    done = jobs(r)[0]
    assert done["status"] == "done" and "erased in Call Tracker" in done["result"]


def test_job_has_no_name_or_email_and_phones_are_cleared_when_done(r):
    connect(r)
    push_and_run(r, "Acme")
    erase(r, "Acme")
    pending = json.dumps(jobs(r)[0])
    for pii in ("Acme", "Ravi Kumar", "ravi@acme.com", "HR Head", "acme.com"):
        assert pii not in pending
    ct.process_due(r.store)
    outbox = json.dumps(r.store.read("ct_outbox"))
    assert ACME_PHONE not in outbox and jobs(r)[0]["phones"] == [] and jobs(r)[0]["phones_cleared"]
    assert not [j for j in r.store.read("ct_outbox") if j.get("phone")]       # the earlier push / do-not-call jobs hold no number either
    assert "ravi@acme.com" not in outbox


def test_waiting_push_and_dnc_jobs_of_the_person_are_dropped_and_finished_ones_wiped(r):
    connect(r)
    acme = r.lead["Acme"]["id"]
    call_task(r, "Acme")
    ct.on_opt_out(r.store, acme, "Opted out")
    assert {j["kind"] for j in r.store.read("ct_outbox")} == {"push", "dnc"}
    erase(r, "Acme")
    assert {j["kind"] for j in r.store.read("ct_outbox")} == {"erase"}         # nothing left that would send them to be dialled
    assert ACME_PHONE not in json.dumps([j for j in r.store.read("ct_outbox") if j["kind"] != "erase"])


def test_no_connection_queues_nothing(r):
    assert erase(r, "Acme").status_code == 200
    assert r.store.read("ct_outbox") == []
    connect(r, ws=r.w2)                                                    # a connection for another workspace does not count
    assert erase(r, "Beta Ltd").status_code == 200 and r.store.read("ct_outbox") == []
    r.c.put("/api/calltracker", json={"workspace": "main", "enabled": False}, headers=r.h["owner"])
    connect(r, enabled=False)
    assert erase(r, "Cora Inc").status_code == 200 and r.store.read("ct_outbox") == []


def test_call_tracker_down_retries_then_fails_visibly_notifies_admins_and_retry_works(r):
    connect(r)
    push_and_run(r, "Acme")
    ref = r.lead["Acme"]["id"]
    erase(r, "Acme")
    r.fake.fail = [503] * 200
    ct.process_due(r.store)
    job = jobs(r)[0]
    assert job["status"] == "pending" and job["attempts"] == 1 and "busy or down" in job["error"] and job["phones"]      # still waiting, phones kept
    assert ref in r.fake.leads
    clock = ct.now_utc()
    for _ in range(ct.MAX_ATTEMPTS):
        clock += timedelta(hours=13)
        ct.process_due(r.store, now=clock)
    job = jobs(r)[0]
    assert job["status"] == "failed" and job["attempts"] == ct.MAX_ATTEMPTS and job["phones"] == []
    card = r.c.get("/api/calltracker?workspace=main", headers=r.h["owner"]).json()
    row = card["queue"]["failed_rows"][0]
    assert card["queue"]["failed"] == 1 and row["kind"] == "erase" and row["lead_id"] == ref
    note = [n for n in r.store.read("notifications") if n.get("kind") == "alert:calltracker_erase_failed"]
    assert note and "may still be dialled" in note[0]["text"] and ACME_PHONE not in json.dumps(note) and "Acme" not in json.dumps(note)
    # Call Tracker is back: Retry puts it in the queue again and the person is removed
    r.fake.fail = []
    assert r.c.post("/api/calltracker/retry", json={"workspace": "main"}, headers=r.h["owner"]).json()["requeued"] == 1
    ct.process_due(r.store)
    assert jobs(r)[0]["status"] == "done" and ref in r.fake.erased and ACME_PHONE in r.fake.blocked


def test_an_old_call_tracker_without_the_delete_endpoint_fails_visibly_but_still_blocks_the_number(r):
    connect(r)
    push_and_run(r, "Acme")
    erase(r, "Acme")
    r.fake.old = True
    ct.process_due(r.store)
    job = jobs(r)[0]
    assert job["status"] == "failed" and "too old to erase" in job["error"]            # a 404 is not retried
    assert ACME_PHONE in r.fake.blocked and r.fake.leads                                # the belt-and-braces do-not-call went through
    assert [n for n in r.store.read("notifications") if n.get("kind") == "alert:calltracker_erase_failed"]


def test_erase_twice_queues_one_job(r):
    connect(r)
    push_and_run(r, "Acme")
    assert erase(r, "Acme").status_code == 200
    assert erase(r, "Acme").status_code == 404                                      # already gone: nothing new queued
    assert len(jobs(r)) == 1
    lead = dict(r.lead["Acme"])                                                     # and the queue itself refuses a duplicate
    from app import calltracker_erase
    assert calltracker_erase.on_erase(r.store, lead)["id"] == jobs(r)[0]["id"] and len(jobs(r)) == 1


def test_bulk_delete_erases_only_leads_that_were_sent_to_call_tracker(r):
    connect(r)
    push_and_run(r, "Acme")
    res = r.c.post("/api/leads/bulk", json={"lead_ids": [r.lead["Acme"]["id"], r.lead["Beta Ltd"]["id"]], "action": "delete"}, headers=r.h["owner"])
    assert res.json()["ok"] == 2
    queued = jobs(r)
    assert [j["lead_id"] for j in queued] == [r.lead["Acme"]["id"]]                 # Beta Ltd never went to Call Tracker: its number is not blocked
    ct.process_due(r.store)
    assert r.lead["Acme"]["id"] in r.fake.erased and BETA_PHONE not in r.fake.blocked and ACME_PHONE in r.fake.blocked


def test_workspace_delete_with_leads_erases_them_before_the_connection_goes(r):
    connect(r, ws=r.w2)
    r.set("Other WS Co", id=r.other["id"], phone="9555500000")
    r.c.post("/api/tasks", json={"lead_id": r.other["id"], "title": "Call", "kind": "call"}, headers={**r.h["owner"], "X-Workspace": r.w2})
    ct.process_due(r.store)
    assert r.fake.leads
    res = r.c.request("DELETE", f"/api/workspaces/{r.w2}", json={"confirm_name": "Client Two", "delete_leads": True}, headers=r.h["owner"])
    assert res.status_code == 200, res.text
    assert r.other["id"] in r.fake.erased and "9555500000" in r.fake.blocked
    assert ct.connection(r.store, r.w2) is None and not [j for j in r.store.read("ct_outbox") if j.get("status") != "done"]


def test_workspaces_are_isolated(r):
    connect(r)
    connect(r, ws=r.w2, base_url="https://other.example.com")
    other = ct.connection(r.store, r.w2)
    r.set("Other WS Co", id=r.other["id"], phone="9555500000")
    seen = []
    base = r.fake
    ct.TRANSPORT = lambda method, url, headers, body: (seen.append(url), base(method, url, headers, body))[1]
    try:
        assert erase(r, "Acme").status_code == 200                                 # a Main lead
        assert jobs(r)[0]["ws"] == "main" and not [j for j in jobs(r) if j["ws"] == r.w2]
        ct.process_due(r.store)
        assert seen and all(u.startswith("https://calls.example.com/") for u in seen)
        assert other["base_url"] == "https://other.example.com"
        # erasing the other workspace's lead uses only its own connection
        seen.clear()
        res = r.c.post(f"/api/leads/{r.other['id']}/erase", json={"confirm": True}, headers={**r.h["owner"], "X-Workspace": r.w2})
        assert res.status_code == 200, res.text
        ct.process_due(r.store)
        assert seen and all(u.startswith("https://other.example.com/") for u in seen)
    finally:
        ct.TRANSPORT = base


def test_an_erased_person_who_is_imported_again_is_refused_and_never_pushed(r):
    connect(r)
    push_and_run(r, "Acme")
    erase(r, "Acme")
    ct.process_due(r.store)
    before = len(r.fake.requests)
    csv = "company,contact_name,title,email,phone,website\nAcme,Ravi Kumar,HR Head,ravi@acme.com,98765 43210,https://acme.com\n"
    r.c.post("/api/leads/import", json={"csv": csv, "source": "apollo"}, headers=r.h["owner"])
    assert not [l for l in r.c.get("/api/leads", headers=r.h["owner"]).json() if l["company"] == "Acme"]
    assert [j for j in r.store.read("ct_outbox") if j["kind"] == "push" and j["status"] == "pending"] == []
    ct.process_due(r.store)
    assert len(r.fake.requests) == before and not [q for q in r.fake.requests[before:] if q[0] == "PUT"]
