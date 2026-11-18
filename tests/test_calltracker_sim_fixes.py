"""Fixes from the DialHalo <-> Outreach simulation: calls matched by the lead reference (masked numbers never matched or
stored), long "connection_paused" waits that use no attempts and clear on any success, a reconcile when a paused link
works again (harmless next to the deliveries DialHalo held), the call title following an outcome changed in Outreach,
and DialHalo's own outcome names (GET /api/v1/outcomes, cached per link). DialHalo is faked in memory."""
import pytest

from app import calltracker as ct, calltracker_links as links
from tests.test_calltracker import KEY, SECRET, ct_call, hook, r as base_r  # noqa: F401
from tests.test_calltracker_links import drain, two_links
from tests.test_calltracker_outcomes import T1, T2, change, dh_row, outcome_event, sent
from tests.test_calltracker_personal import PKEY, PSECRET, PersonalFake, personal, task

MASKED = "98xxxxxx06"


class SimFake(PersonalFake):
    def __init__(self):
        super().__init__()
        self.outcomes = None              # list -> GET /api/v1/outcomes answers it; None -> 404 (older DialHalo)
        self.feed = []                    # GET /api/v1/sync/calls rows
        self.paused = set()               # Authorization headers DialHalo answers 401 connection_paused for

    def __call__(self, method, url, headers, body):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        auth = headers.get("Authorization")
        if auth in self.paused:
            self.requests.append((method, path, None, auth))
            return 401, {"code": "connection_paused", "message": "Connection paused or removed"}
        if path == "/api/v1/outcomes" and self.outcomes is not None:
            self.requests.append((method, path, None, auth))
            return 200, {"success": True, "data": self.outcomes}
        if path.startswith("/api/v1/sync/calls"):
            self.requests.append((method, path, None, auth))
            return 200, {"data": {"calls": self.feed, "next_cursor": "c1", "has_more": False}}
        return super().__call__(method, url, headers, body)


@pytest.fixture
def r(base_r, monkeypatch):
    fake = SimFake()
    monkeypatch.setattr(ct, "TRANSPORT", fake)
    base_r.fake = fake
    base_r.store.update("settings", lambda s: {**s, "public_base_url": "https://outreach.example.com"})
    return base_r


def ref(r, company):
    return {"externalSource": "mcn_outreach", "externalId": r.lead[company]["id"]}


def lead_of(r, company):
    return next(l for l in r.store.read("leads") if l["company"] == company)


# ---------------------------------------------------------------- 1. lead reference first, masked numbers never
def test_masked_call_with_a_lead_ref_lands_on_the_right_lead(r):
    a, _ = two_links(r)
    beta = lead_of(r, "Beta Ltd")
    r.fake.calls[61] = ct_call(61, number=MASKED, outcome="Interested", lead=ref(r, "Beta Ltd"))
    assert outcome_event(r, a, "m1", 61, T1).json()["applied"] == 1
    row = dh_row(r, 61)
    assert row["lead_id"] == beta["id"] and row["to"] == beta["phone"]                 # the masked number is never stored
    assert MASKED not in str(r.store.read("ct_outbox")) and MASKED not in str(r.store.read("ct_unmatched"))
    # calls.synced items carry the reference too; a masked number with a reference is fetched and applied
    r.fake.calls[62] = ct_call(62, number=MASKED, outcome=None, lead=ref(r, "Cora Inc"))
    res = hook(r, a, {"id": "m2", "event": "calls.synced", "data": {"calls": [{"id": 62, "number_norm": MASKED, "lead": ref(r, "Cora Inc")},
                                                                               {"id": 63, "number_norm": "98xxxxxx11"}]}})
    assert res.json()["applied"] == 1 and dh_row(r, 62)["lead_id"] == r.lead["Cora Inc"]["id"]
    assert not [x for x in r.store.read("activities") if (x.get("ct") or {}).get("call_id") == 63]   # masked, no reference: never matched
    # the reference wins over a number that belongs to another lead
    r.fake.calls[64] = ct_call(64, number="9876543210", outcome=None, lead=ref(r, "Beta Ltd"))
    outcome_event(r, a, "m3", 64, T1)
    assert dh_row(r, 64)["lead_id"] == beta["id"]


def test_reconcile_feed_matches_by_reference(r):
    a, _ = two_links(r)
    r.fake.feed = [{"id": 71, "number": MASKED, "lead": ref(r, "Beta Ltd")}]
    r.fake.calls[71] = ct_call(71, number=MASKED, outcome=None, lead=ref(r, "Beta Ltd"))
    from app import calltracker_kit as kit
    out = kit.reconcile(r.store, links.get(r.store, a))
    assert out["applied"] == 1 and dh_row(r, 71)["lead_id"] == r.lead["Beta Ltd"]["id"]


# ---------------------------------------------------------------- 2. long pauses
def test_a_long_pause_uses_no_attempts_and_clears_on_any_success(r):
    p = personal(r)
    r.store.write("ct_outbox", [])
    task(r, "Acme")
    r.fake.paused.add(f"Bearer {PKEY}")
    for _ in range(40):                                        # 40 re-checks = 10 hours: never failed, no attempt used
        r.store.update("ct_outbox", lambda jobs: [{**j, "next_at": "2000-01-01T00:00:00+00:00"} for j in jobs])
        ct.process_due(r.store)
    job = next(j for j in r.store.read("ct_outbox") if j["kind"] == "push")
    assert job["status"] == "pending" and job["attempts"] == 0 and job["paused"]
    assert links.get(r.store, p["id"])["paused_in_dh"] and not links.get(r.store, p["id"]).get("auth_failed")
    # a status check that works clears the card at once (no need to wait for the next retry)
    r.fake.paused.clear()
    r.fake.scope = {f"Bearer {PKEY}": ("member", {"id": "u1", "name": "Ravi CT", "email": "ravi@x.in"})}
    assert r.c.post("/api/calltracker/test", json={"workspace": "main", "link": p["id"]}, headers=r.h["owner"]).status_code == 200
    assert not links.get(r.store, p["id"]).get("paused_in_dh")
    # a revoked key still shows "Not authorised" as today
    r.fake.fail = [401]
    r.store.update("ct_outbox", lambda jobs: [{**j, "next_at": "2000-01-01T00:00:00+00:00"} for j in jobs])
    ct.process_due(r.store)
    assert links.get(r.store, p["id"])["auth_failed"]


# ---------------------------------------------------------------- 3. calls made during a pause
def test_resuming_reconciles_the_feed_and_the_held_delivery_is_harmless(r):
    p = personal(r)
    r.store.write("ct_outbox", [])
    r.fake.paused.add(f"Bearer {PKEY}")
    task(r, "Acme")
    ct.process_due(r.store)
    assert links.get(r.store, p["id"])["paused_in_dh"]
    # a call made during the pause: in the feed, its webhook held by DialHalo
    r.fake.calls[81] = ct_call(81, number="9876543210", outcome="Interested", lead=ref(r, "Acme"))
    r.fake.feed = [{"id": 81, "number": "9876543210", "lead": ref(r, "Acme")}]
    r.fake.paused.clear()
    r.store.update("ct_outbox", lambda jobs: [{**j, "next_at": "2000-01-01T00:00:00+00:00"} for j in jobs])
    ct.process_due(r.store)                                     # the push works -> paused cleared -> reconcile
    assert any(path.startswith("/api/v1/sync/calls") and a == f"Bearer {PKEY}" for m, path, d, a in r.fake.requests)
    assert dh_row(r, 81)["lead_id"] == r.lead["Acme"]["id"]
    yes = len([t for t in r.store.read("tasks") if t["title"] == "Follow up after the yes"])
    assert yes == 1
    # the held delivery arrives afterwards: still one row, rules not run again
    data = {"callId": 81, "outcomeName": "Interested", "changedAt": T1, "origin": "app", "by": {"name": "Ravi CT", "email": "ravi@x.in"}}
    assert hook(r, p["id"], {"id": "held1", "event": "call.outcome", "data": data}, secret=PSECRET).status_code == 200
    assert len([x for x in r.store.read("activities") if (x.get("ct") or {}).get("call_id") == 81]) == 1
    assert len([t for t in r.store.read("tasks") if t["title"] == "Follow up after the yes"]) == yes


# ---------------------------------------------------------------- 4. the call title follows the outcome
def test_call_title_follows_an_outcome_changed_in_outreach(r):
    a, _ = two_links(r)
    r.fake.calls[91] = ct_call(91, number="9876543210", outcome="Not interested")
    outcome_event(r, a, "t1", 91, T1, name="Not interested")
    row = dh_row(r, 91)
    assert row["subject"].endswith("— Not interested")
    change(r, "Acme", row["id"], "interested")
    assert dh_row(r, 91)["subject"].endswith("— Interested") and "Not interested" not in dh_row(r, 91)["subject"]
    acme = r.lead["Acme"]["id"]
    logged = r.c.post(f"/api/leads/{acme}/activity", json={"type": "call", "outcome": "busy", "subject": "Call — Busy"}, headers=r.h["owner"]).json()["activity"]
    change(r, "Acme", logged["id"], "connected")
    assert next(x for x in r.store.read("activities") if x["id"] == logged["id"])["subject"] == "Call — Connected"


# ---------------------------------------------------------------- 5. DialHalo's own outcome names
def test_dialhalo_outcome_names_are_sent_from_the_cached_list(r):
    a, _ = two_links(r)
    r.fake.outcomes = [{"id": 1, "name": "Not Interested", "active": True}, {"id": 2, "name": "INTERESTED", "active": False},
                       {"id": 3, "name": "Interested", "active": True}]
    for cid in (101, 102):
        r.fake.calls[cid] = ct_call(cid, number="9876543210", outcome="Call back later")
        outcome_event(r, a, f"o{cid}", cid, T1, name="Call back later")
    drain(r)
    change(r, "Acme", dh_row(r, 101)["id"], "not_interested")
    change(r, "Acme", dh_row(r, 102)["id"], "interested")
    drain(r)
    names = {path: d.get("outcome") for m, path, d, a_ in sent(r, "PUT")}
    assert names == {"/api/v1/calls/101/outcome": "Not Interested", "/api/v1/calls/102/outcome": "Interested"}   # active one first
    assert len([1 for m, path, d, a_ in r.fake.requests if path == "/api/v1/outcomes"]) == 1                   # cached a day
    assert links.get(r.store, a)["dh_outcomes"]["rows"]
    # Test refreshes the list; an older DialHalo without the list falls back to the mapping key
    r.fake.outcomes = None
    r.c.post("/api/calltracker/test", json={"workspace": "main", "link": a}, headers=r.h["owner"])
    assert links.get(r.store, a)["dh_outcomes"]["rows"] == []
    change(r, "Acme", dh_row(r, 101)["id"], "interested")
    drain(r)
    assert [d.get("outcome") for m, path, d, a_ in sent(r, "PUT")][-1] == "interested"
