"""Two-way call outcome sync with DialHalo (contract 2026-11-18), Outreach side: newer-wins per call, ignoring our own
echo (origin mcn_outreach), "Changed to X by <name> in DialHalo", the change-outcome edit queueing PUT
/api/v1/calls/:id/outcome on the link the call came from, calls logged in Outreach going to every active link as
POST /api/v1/leads/activity (no_lead is fine), the reverse mapping (opt_out preference, unmapped -> note only, flagged),
no outbound job from an incoming change, coalescing, and the queue's retries / failures. DialHalo is faked in memory."""
import json

import pytest

from app import calltracker as ct, calltracker_outcomes as oc
from tests.test_calltracker import KEY, ct_call, hook  # noqa: F401
from tests.test_calltracker_links import KEY2, SECRET2, TwoFake, drain, two_links, r as links_r  # noqa: F401
from tests.test_calltracker import r as base_r  # noqa: F401
from tests.ownership_helpers import lead

ACME, BETA = "9876543210", "9123456789"
T1, T2, T3 = "2026-11-18T05:00:00Z", "2026-11-18T06:00:00Z", "2026-11-18T07:00:00Z"


class SyncFake(TwoFake):
    """TwoFake plus the two new DialHalo endpoints. `answers` queues bodies for them (default: applied / stored)."""

    def __init__(self):
        super().__init__()
        self.answers = []

    def __call__(self, method, url, headers, body):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        new = (method == "PUT" and path.startswith("/api/v1/calls/") and path.endswith("/outcome")) or (method == "POST" and path == "/api/v1/leads/activity")
        if new and not self.fail:
            data = json.loads(body)
            self.requests.append((method, path, data, headers.get("Authorization")))
            if self.answers:
                status, answer = self.answers.pop(0)
                return status, answer
            if method == "PUT" and "outcome" not in data:          # like DialHalo: a note-only change
                return 200, {"data": {"applied": False, "reason": "unknown_outcome", "outcome": None}}
            if method == "PUT":
                return 200, {"data": {"applied": True, "outcome": {"id": 3, "name": data.get("outcome")} if data.get("outcome") else None}}
            return 200, {"data": {"stored": True}}
        return super().__call__(method, url, headers, body)


@pytest.fixture
def r(links_r, monkeypatch):
    fake = SyncFake()
    monkeypatch.setattr(ct, "TRANSPORT", fake)
    links_r.fake = fake
    return links_r


def sent(r, method=None, prefix=("/api/v1/calls/", "/api/v1/leads/activity")):
    return [(m, p, d, a) for m, p, d, a in r.fake.requests if p.startswith(prefix) and (method is None or m == method)
            and (p.endswith("/outcome") or p == "/api/v1/leads/activity")]


def sync_jobs(r, status=None):
    return [j for j in r.store.read("ct_outbox") if j["kind"] in ("outcome", "activity") and (status is None or j["status"] == status)]


def outcome_event(r, link, eid, call_id, changed_at, origin="portal", name="Interested", by=None, secret=None):
    data = {"callId": call_id, "outcomeName": name, "changedAt": changed_at, "origin": origin,
            "by": by or {"userId": "u7", "name": "Asha Portal", "email": "asha@ct.in"}}
    return hook(r, link, {"id": eid, "event": "call.outcome", "data": data}, **({"secret": secret} if secret else {}))


def dh_row(r, call_id):
    return next(a for a in r.store.read("activities") if a.get("source") == "calltracker" and a["ct"]["call_id"] == call_id)


def activity_view(r, company, activity_id):
    items = r.c.get(f"/api/leads/{r.lead[company]['id']}/activity", headers=r.h["owner"]).json()
    return next(i for i in items if i.get("id") == activity_id)


def change(r, company, activity_id, outcome, who="owner", **extra):
    return r.c.put(f"/api/leads/{r.lead[company]['id']}/activity/{activity_id}/outcome", json={"outcome": outcome, **extra}, headers=r.h[who])


# ---------------------------------------------------------------- incoming
def test_incoming_newer_wins_older_and_same_are_no_ops_and_text_names_the_person(r):
    a, _ = two_links(r)
    r.store.update_lead(r.lead["Beta Ltd"]["id"], lambda l: {**l, "stage": "contacted"})
    r.fake.calls[5] = ct_call(5, number=BETA, outcome=None)
    hook(r, a, {"id": "s1", "event": "calls.synced", "data": {"calls": [{"id": 5, "number_norm": BETA}]}})
    r.fake.calls[5] = ct_call(5, number=BETA, outcome="Interested")
    assert outcome_event(r, a, "o1", 5, T2).json()["applied"] == 1
    row = dh_row(r, 5)
    assert row["outcome"] == "interested" and row["ct"]["outcome_changed_at"] == "2026-11-18T06:00:00+00:00"
    assert row["ct"]["outcome_origin"] == "portal" and row["ct"]["outcome_by"] == "Asha Portal"
    texts = [e["text"] for e in lead(r, "Beta Ltd")["timeline"] if e["kind"] == "call_outcome"]
    assert texts == ["Changed to Interested by Asha Portal in DialHalo"]
    assert activity_view(r, "Beta Ltd", row["id"])["change_text"] == "Changed to Interested by Asha Portal in DialHalo"
    follow_ups = len([t for t in r.store.read("tasks") if t["title"] == "Follow up after the yes"])
    # an older change arriving late: nothing changes, no rules run again
    r.fake.calls[5] = ct_call(5, number=BETA, outcome="Not interested")
    outcome_event(r, a, "o2", 5, T1, name="Not interested")
    row = dh_row(r, 5)
    assert row["outcome"] == "interested" and row["ct"]["outcome_name"] == "Interested" and lead(r, "Beta Ltd")["stage"] == "interested"
    # the same value and time again (a second delivery with a new event id): no-op
    r.fake.calls[5] = ct_call(5, number=BETA, outcome="Interested")
    outcome_event(r, a, "o3", 5, T2)
    assert len([t for t in r.store.read("tasks") if t["title"] == "Follow up after the yes"]) == follow_ups
    assert len([e for e in lead(r, "Beta Ltd")["timeline"] if e["kind"] == "call_outcome"]) == 1
    # a newer one applies and runs the rules (stage, sequence) as today
    r.fake.calls[5] = ct_call(5, number=BETA, outcome="Not interested")
    outcome_event(r, a, "o4", 5, T3, origin="app", name="Not interested", by={"userId": "u1", "name": "Ravi CT", "email": "ravi@x.in"})
    row = dh_row(r, 5)
    assert row["outcome"] == "not_interested" and row["ct"]["outcome_origin"] == "app"
    assert lead(r, "Beta Ltd")["stage"] == "disqualified"
    assert "Changed to Not interested by Ravi in DialHalo" in [e["text"] for e in lead(r, "Beta Ltd")["timeline"]]   # the Outreach name of ravi@x.in
    assert not sync_jobs(r)                                                     # applying incoming changes never queues outbound


def test_incoming_with_origin_mcn_outreach_is_ignored(r):
    a, _ = two_links(r)
    r.fake.calls[6] = ct_call(6, number=ACME, outcome="Interested")
    before = len(r.fake.requests)
    res = outcome_event(r, a, "x1", 6, T2, origin="mcn_outreach")
    assert res.status_code == 200 and res.json()["applied"] == 0
    assert len(r.fake.requests) == before                                       # not even fetched
    assert not [x for x in r.store.read("activities") if x.get("source") == "calltracker"]
    assert not sync_jobs(r)


def test_older_dialhalo_without_changed_at_still_works_and_undated_refreshes_never_overwrite(r):
    a, _ = two_links(r)
    r.fake.calls[8] = ct_call(8, number=ACME, outcome="Interested")
    hook(r, a, {"id": "l1", "event": "call.outcome", "data": {"callId": 8}})       # today's payload: no changedAt
    row = dh_row(r, 8)
    assert row["outcome"] == "interested" and row["ct"]["outcome_changed_at"]
    r.fake.calls[8] = ct_call(8, number=ACME, outcome="Call back later", ai_summary="Later summary")
    hook(r, a, {"id": "l2", "event": "call.transcribed", "data": {"callId": 8}})    # a refresh, not an outcome change
    row = dh_row(r, 8)
    assert row["outcome"] == "interested" and "Later summary" in row["body"]


# ---------------------------------------------------------------- outbound: a DialHalo call changed in Outreach
def test_changing_a_dialhalo_call_queues_one_put_on_the_link_it_came_from(r):
    a, b = two_links(r)
    r.fake.calls[7] = ct_call(7, number=ACME, outcome="Interested")
    outcome_event(r, b, "b1", 7, T1, secret=SECRET2)
    row = dh_row(r, 7)
    assert row["ct"]["conn"] == b
    res = change(r, "Acme", row["id"], "call_back", note="Asked for Monday")
    assert res.status_code == 200 and res.json()["queued"] == 1
    res = change(r, "Acme", row["id"], "not_interested", note="Changed their mind")       # coalesced into the same job
    assert res.status_code == 200
    jobs = sync_jobs(r, "pending")
    assert len(jobs) == 1 and jobs[0]["kind"] == "outcome" and jobs[0]["link"] == b and jobs[0]["call_id"] == 7
    assert set(jobs[0]) >= {"activity_id", "lead_id"} and "phone" not in jobs[0] and "number" not in jobs[0]
    drain(r)
    calls = sent(r, "PUT")
    assert len(calls) == 1
    method, path, body, auth = calls[0]
    assert path == "/api/v1/calls/7/outcome" and auth == f"Bearer {KEY2}"
    info = dh_row(r, 7)["ct"]
    assert body == {"outcome": "not interested", "changedAt": oc.z_iso(ct.parse(info["outcome_changed_at"])), "note": "Changed their mind",
                    "by": {"email": info["outcome_by_email"], "name": info["outcome_by"]}, "origin": "mcn_outreach"}
    assert body["changedAt"].endswith("Z") and body["by"]["name"]
    view = activity_view(r, "Acme", row["id"])
    assert view["status"] == "not_interested" and view["dialhalo_call"] and view["change_text"].endswith("in Outreach")
    assert any("Call outcome changed to Not interested" in e["text"] for e in lead(r, "Acme")["timeline"])
    assert sync_jobs(r, "done") and not sync_jobs(r, "pending")
    card = r.c.get("/api/calltracker", headers=r.h["owner"]).json()
    assert {l["id"]: l for l in card["links"]}[b]["last_outcome"]["ok"]
    # DialHalo now emits our change back with origin mcn_outreach: ignored
    r.fake.calls[7] = ct_call(7, number=ACME, outcome="Not interested")
    assert outcome_event(r, b, "b2", 7, body["changedAt"], origin="mcn_outreach", secret=SECRET2).json()["applied"] == 0
    # a DialHalo refresh with an older change time does not overwrite the Outreach edit
    r.fake.calls[7] = ct_call(7, number=ACME, outcome="Interested")
    outcome_event(r, b, "b3", 7, T1, secret=SECRET2)
    assert dh_row(r, 7)["outcome"] == "not_interested"
    # a newer DialHalo change wins over the Outreach edit
    outcome_event(r, b, "b4", 7, "2030-01-01T00:00:00Z", secret=SECRET2)
    assert dh_row(r, 7)["outcome"] == "interested" and dh_row(r, 7)["ct"]["outcome_origin"] == "portal"
    assert activity_view(r, "Acme", row["id"])["change_text"] == "Changed to Interested by Asha Portal in DialHalo"
    assert len(sent(r, "PUT")) == 1 and not sync_jobs(r, "pending")               # and nothing was sent back


def test_reverse_mapping_prefers_no_opt_out_uses_opt_out_when_opted_out_and_flags_unmapped(r):
    a, _ = two_links(r)
    for cid, num, ref in ((1, ACME, "Acme"), (2, BETA, "Beta Ltd")):
        r.fake.calls[cid] = ct_call(cid, number=num, outcome="Interested")
        outcome_event(r, a, f"m{cid}", cid, T1)
    conn = ct.by_id(r.store, a)
    assert oc.reverse_name(conn, "not_interested", lead(r, "Acme")) == "not interested"
    r.store.update_lead(r.lead["Beta Ltd"]["id"], lambda l: {**l, "do_not_contact": True})
    assert oc.reverse_name(conn, "not_interested", lead(r, "Beta Ltd")) == "do not call"
    assert oc.reverse_name(conn, "busy", lead(r, "Acme")) is None
    change(r, "Beta Ltd", dh_row(r, 2)["id"], "not_interested")
    change(r, "Acme", dh_row(r, 1)["id"], "busy", note="Line busy twice")
    drain(r)
    bodies = {p: d for _, p, d, _ in sent(r, "PUT")}
    assert bodies["/api/v1/calls/2/outcome"]["outcome"] == "do not call"
    busy = bodies["/api/v1/calls/1/outcome"]
    assert "outcome" not in busy and busy["note"] == "Line busy twice\nOutcome in Outreach: Busy (no matching DialHalo outcome)"
    card = r.c.get("/api/calltracker", headers=r.h["owner"]).json()
    out = {l["id"]: l for l in card["links"]}[a]["unmapped_outgoing"]
    assert out and out[0]["name"] == "Busy" and out[0]["why"] == "no_rule" and out[0]["count"] == 1
    assert "not an outcome in DialHalo" in activity_view(r, "Acme", dh_row(r, 1)["id"])["sync_text"]


def test_dialhalo_unknown_outcome_and_older_answers_are_recorded_not_failed(r):
    a, _ = two_links(r)
    r.fake.calls[3] = ct_call(3, number=ACME, outcome="Interested")
    outcome_event(r, a, "u1", 3, T1)
    row = dh_row(r, 3)
    r.fake.answers = [(200, {"data": {"applied": False, "reason": "unknown_outcome"}})]
    change(r, "Acme", row["id"], "meeting_booked")
    drain(r)
    assert sync_jobs(r, "done") and not sync_jobs(r, "failed")
    card = r.c.get("/api/calltracker", headers=r.h["owner"]).json()
    out = {l["id"]: l for l in card["links"]}[a]["unmapped_outgoing"]
    assert out[0]["name"] == "converted" and out[0]["why"] == "unknown_in_dialhalo"
    r.fake.answers = [(200, {"data": {"applied": False, "reason": "older"}})]
    change(r, "Acme", row["id"], "interested")
    drain(r)
    assert activity_view(r, "Acme", row["id"])["sync_text"] == "DialHalo already had a newer outcome"


def test_outcome_job_retries_with_backoff_then_fails_and_404_fails_at_once(r):
    a, _ = two_links(r)
    r.fake.calls[4] = ct_call(4, number=ACME, outcome="Interested")
    outcome_event(r, a, "f1", 4, T1)
    drain(r)                                                                      # the Outreach match job goes first
    row = dh_row(r, 4)
    change(r, "Acme", row["id"], "not_interested")
    r.fake.fail = [503]
    assert ct.process_due(r.store) == 1
    job = sync_jobs(r)[0]
    assert job["status"] == "pending" and job["attempts"] == 1 and "busy or down" in job["error"] and not job.get("sending")
    assert ct.process_due(r.store) == 0                                           # backing off
    acme = lead(r, "Acme")
    assert not (acme.get("calltracker") or {}).get("status")                       # the push state of the lead is untouched
    r.store.update("ct_outbox", lambda jobs: [{**j, "next_at": "2000-01-01T00:00:00+00:00"} for j in jobs])
    r.fake.answers = [(404, {"message": "Call not found"})]
    ct.process_due(r.store)
    job = sync_jobs(r)[0]
    assert job["status"] == "failed" and "HTTP 404" in job["error"]
    assert any("outcome change was not sent" in e["text"] for e in lead(r, "Acme")["timeline"])
    assert ct.retry_failed(r.store, "main") == 1                                   # the card's Retry works for it
    drain(r)
    assert sync_jobs(r)[0]["status"] == "done"


# ---------------------------------------------------------------- outbound: calls logged in Outreach
def test_a_call_logged_in_outreach_goes_to_every_active_link_as_history_and_no_lead_is_fine(r):
    a, b = two_links(r)
    acme = r.lead["Acme"]["id"]
    res = r.c.post(f"/api/leads/{acme}/activity", json={"type": "call", "outcome": "interested", "notes": "Wants 5 roles",
                                                         "at": "2026-11-18T11:00:00+05:30"}, headers=r.h["owner"])
    assert res.status_code == 200
    row = res.json()["activity"]
    jobs = sync_jobs(r, "pending")
    assert sorted(j["link"] for j in jobs) == sorted([a, b]) and all(j["kind"] == "activity" and j["activity_id"] == row["id"] for j in jobs)
    r.fake.answers = [(200, {"data": {"stored": True}}), (200, {"data": {"stored": False, "reason": "no_lead"}})]
    drain(r)
    posts = sent(r, "POST")
    assert len(posts) == 2 and {x[3] for x in posts} == {f"Bearer {KEY}", f"Bearer {KEY2}"}
    body = posts[0][2]
    assert body == {"source": "mcn_outreach", "refId": row["id"], "number": "+919876543210", "externalId": acme, "kind": "call_logged",
                    "outcome": "interested", "note": "Wants 5 roles", "at": "2026-11-18T05:30:00Z", "by": body["by"]}
    assert set(body["by"]) == {"email", "name"} and body["by"]["name"]
    assert len(sync_jobs(r, "done")) == 2 and not sync_jobs(r, "failed")          # no_lead is not an error
    # changing its outcome re-sends the same entry (same refId) to every link, coalesced per link
    change(r, "Acme", row["id"], "busy")
    change(r, "Acme", row["id"], "no_answer", note="Rang out")
    assert len(sync_jobs(r, "pending")) == 2
    drain(r)
    again = sent(r, "POST")[2:]
    assert len(again) == 2 and all(x[2]["refId"] == row["id"] and "outcome" not in x[2] for x in again)
    assert again[0][2]["note"] == "Wants 5 roles\nRang out\nOutcome in Outreach: No answer (no matching DialHalo outcome)"
    view = activity_view(r, "Acme", row["id"])
    assert view["outcome_editable"] and not view["dialhalo_call"] and view["change_text"].endswith("in Outreach")


def test_call_desk_outcome_also_goes_as_history_and_nothing_without_a_link(r):
    acme = r.lead["Acme"]["id"]
    assert r.c.post(f"/api/leads/{acme}/call-outcome", json={"outcome": "no_answer"}, headers=r.h["owner"]).status_code == 200
    assert not sync_jobs(r)                                                       # no DialHalo link: nothing queued
    a, b = two_links(r)
    put_res = r.c.put("/api/calltracker", json={"workspace": "main", "link": b, "enabled": False}, headers=r.h["owner"])
    assert put_res.status_code == 200
    assert r.c.post(f"/api/leads/{acme}/call-outcome", json={"outcome": "connected", "notes": "ok"}, headers=r.h["owner"]).status_code == 200
    jobs = sync_jobs(r)
    assert [j["link"] for j in jobs] == [a]                                       # active links only
    drain(r)
    body = sent(r, "POST")[0][2]
    assert "outcome" not in body and body["note"] == "ok\nOutcome in Outreach: Connected (no matching DialHalo outcome)"   # no DialHalo rule for "connected"


def test_change_outcome_rules_roles_and_errors(r):
    two_links(r)
    acme = r.lead["Acme"]["id"]
    row = r.c.post(f"/api/leads/{acme}/activity", json={"type": "call", "outcome": "busy"}, headers=r.h["owner"]).json()["activity"]
    assert change(r, "Acme", row["id"], "nonsense").status_code == 400
    assert change(r, "Acme", "nope", "busy").status_code == 404
    assert change(r, "Acme", row["id"], "connected", who="view").status_code == 403
    assert change(r, "Acme", row["id"], "connected", who="ravi").status_code == 200       # a caller may
    other = r.c.put(f"/api/leads/{r.other['id']}/activity/{row['id']}/outcome", json={"outcome": "busy"}, headers=r.h["owner"])
    assert other.status_code == 404                                                # the call is not on that lead
    note = r.c.post(f"/api/leads/{acme}/activity", json={"type": "note", "notes": "x"}, headers=r.h["owner"]).json()["activity"]
    assert change(r, "Acme", note["id"], "busy").status_code == 404               # only calls
    n = len(sync_jobs(r))
    assert change(r, "Acme", row["id"], "connected").json()["queued"] == 0        # same value, nothing new: no-op
    assert len(sync_jobs(r)) == n


def test_erasing_a_person_drops_their_waiting_outcome_and_history_jobs(r):
    a, _ = two_links(r)
    acme = r.lead["Acme"]["id"]
    r.c.post(f"/api/leads/{acme}/activity", json={"type": "call", "outcome": "busy"}, headers=r.h["owner"])
    assert sync_jobs(r, "pending")
    from app import calltracker_erase
    calltracker_erase.on_erase(r.store, lead(r, "Acme"))
    assert not sync_jobs(r, "pending")


def test_an_edit_during_a_send_is_not_lost(r):
    a, _ = two_links(r)
    r.fake.calls[9] = ct_call(9, number=ACME, outcome="Interested")
    outcome_event(r, a, "d1", 9, T1)
    row = dh_row(r, 9)
    change(r, "Acme", row["id"], "call_back")
    real = ct.ct_request
    seen = []

    def edit_while_sending(conn, method, path, body=None):
        if path.endswith("/outcome") and not seen:
            seen.append(body["outcome"])
            change(r, "Acme", row["id"], "not_interested")                       # lands while the first job is out
        return real(conn, method, path, body)
    import app.calltracker as ctmod
    ctmod.ct_request = edit_while_sending
    try:
        drain(r)
    finally:
        ctmod.ct_request = real
    outs = [d["outcome"] for _, p, d, _ in sent(r, "PUT")]
    assert outs[0] == "call back later" and outs[-1] == "not interested" and len(outs) == 2
