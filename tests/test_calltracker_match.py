"""Outreach match for DialHalo: every dialled number is reported as matched (with a small label) or not_found through one
coalescing "match" job per workspace; a not-found cache re-checked against later leads; erasure covers match-only leads."""
import pytest

from app import calltracker as ct, calltracker_erase as erase, calltracker_match as m
from tests.test_calltracker import FakeCT, connect, conn_id, ct_call, hook, r as base_r  # noqa: F401

ACME, BETA, CORA = "9876543210", "9123456789", "9000011111"
PATH = "/api/v1/external-matches/mcn_outreach"


class MatchFake(FakeCT):
    """FakeCT plus DialHalo's external-matches endpoint (one row per number, like the real one) and call history."""

    def __init__(self):
        super().__init__()
        self.matches, self.history, self.match_old = {}, [], False

    def __call__(self, method, url, headers, body):
        import json
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        if path == PATH and method == "PUT" and not self.fail:
            data = json.loads(body)
            self.requests.append((method, path, data, headers.get("Authorization")))
            if self.match_old:
                return 404, {"message": "Not found"}
            for row in data["matches"]:
                self.matches[row["number"]] = row
            return 200, {"success": True, "data": {"saved": len(data["matches"]), "skipped": 0}}
        if path == "/api/v1/reports/history" and method == "POST" and not self.fail:
            data = json.loads(body)
            self.requests.append((method, path, data, headers.get("Authorization")))
            start = (data["page"] - 1) * data["pageSize"]
            return 200, {"success": True, "data": {"total": len(self.history), "page": data["page"], "pageSize": data["pageSize"],
                                                   "rows": self.history[start:start + data["pageSize"]]}}
        return super().__call__(method, url, headers, body)


@pytest.fixture
def r(base_r, monkeypatch):
    fake = MatchFake()
    monkeypatch.setattr(ct, "TRANSPORT", fake)
    monkeypatch.setattr("app.calltracker_backfill.BACKGROUND", False)
    monkeypatch.setattr("app.calltracker_backfill.SLEEP", lambda s: None)
    base_r.fake = fake
    base_r.store.update("settings", lambda s: {**s, "public_base_url": "https://outreach.example.com"})
    return base_r


def match_jobs(r, status=None):
    return [j for j in r.store.read("ct_outbox") if j["kind"] == "match" and (status is None or j["status"] == status)]


def puts(r):
    return [d for meth, p, d, _ in r.fake.requests if p == PATH and meth == "PUT"]


# ---------------------------------------------------------------- queue
def test_many_results_coalesce_into_one_job_and_split_at_500(r):
    connect(r)
    for i in range(30):
        m.queue_results(r.store, "main", {f"70000{i:05d}": None})
    assert len(match_jobs(r)) == 1 and len(match_jobs(r)[0]["items"]) == 30
    m.queue_results(r.store, "main", {f"71000{i:05d}": None for i in range(1170)})
    sizes = sorted(len(j["items"]) for j in match_jobs(r, "pending"))
    assert sizes == [200, 500, 500]
    acme = next(l for l in r.store.read("leads") if l["company"] == "Acme")
    m.queue_results(r.store, "main", {"7000000003": acme})              # a number already waiting: replaced, not added
    assert sum(len(j["items"]) for j in match_jobs(r)) == 1200
    assert any(j["items"].get("7000000003", {}).get("status") == "matched" for j in match_jobs(r))
    while ct.process_due(r.store):
        pass
    assert [len(b["matches"]) for b in puts(r)] == [500, 500, 200]
    assert all(len(b["matches"]) <= 500 for b in puts(r))
    done = match_jobs(r, "done")
    assert len(done) == 3 and all(j["items"] == {} and j["sent"] for j in done)        # finished jobs keep counts, not numbers


def test_payload_shape_exactly_per_contract(r):
    connect(r)
    r.set("Acme", stage="unsubscribed", do_not_contact=True)                # suppressed people are still "matched"
    acme = next(l for l in r.store.read("leads") if l["company"] == "Acme")
    m.queue_results(r.store, "main", {ACME: acme, "8888800000": None})
    ct.process_due(r.store)
    body = puts(r)[0]
    assert set(body) == {"matches"}
    by = {row["number"]: row for row in body["matches"]}
    assert by[ACME] == {"number": ACME, "status": "matched", "externalId": acme["id"],
                        "label": {"name": "Ravi Kumar", "company": "Acme", "stage": "Unsubscribed",
                                  "url": f"https://outreach.example.com/#leads?lead={acme['id']}"}}
    assert by["8888800000"] == {"number": "8888800000", "status": "not_found"}
    assert "ravi@acme.com" not in str(body)                                 # data minimisation: no email, no notes
    conn = ct.public_view(r.store, "main")
    assert conn["last_match"]["ok"] and "1 matched" in conn["last_match"]["detail"]
    assert not any(e.get("kind") == "calltracker" for e in acme["timeline"])


def test_url_left_out_when_outreach_has_no_public_address(r, monkeypatch):
    connect(r)
    r.store.update("settings", lambda s: {**s, "public_base_url": ""})
    monkeypatch.delenv("OUTREACH_BASE_URL", raising=False)
    acme = next(l for l in r.store.read("leads") if l["company"] == "Acme")
    assert "url" not in m.result_for(r.store, acme)["label"]


def test_masked_numbers_are_never_sent(r):
    connect(r)
    assert m.queue_results(r.store, "main", {"98******10": None, "": None}) == 0
    assert not match_jobs(r)


def test_results_arriving_while_a_job_sends_go_to_a_new_job(r):
    connect(r)
    m.queue_results(r.store, "main", {"7000000001": None})
    real = r.fake

    def transport(method, url, headers, body):
        if url.endswith(PATH):
            m.queue_results(r.store, "main", {"7000000002": None})          # a webhook lands mid-send
        return real(method, url, headers, body)
    ct.TRANSPORT = transport
    ct.process_due(r.store)
    ct.TRANSPORT = real
    pending = match_jobs(r, "pending")
    assert len(pending) == 1 and list(pending[0]["items"]) == ["7000000002"]
    ct.process_due(r.store)
    assert set(real.matches) == {"7000000001", "7000000002"}


def test_old_dialhalo_404_fails_the_job_with_a_clear_message_and_no_lead_notes(r):
    connect(r)
    r.fake.match_old = True
    acme = next(l for l in r.store.read("leads") if l["company"] == "Acme")
    m.queue_results(r.store, "main", {ACME: acme})
    ct.process_due(r.store)
    job = match_jobs(r)[0]
    assert job["status"] == "failed" and "Update DialHalo" in job["error"] and job["items"]
    assert not job.get("sending")
    assert "Update DialHalo" in ct.public_view(r.store, "main")["last_match"]["detail"]
    lead = next(l for l in r.store.read("leads") if l["company"] == "Acme")
    assert not (lead.get("calltracker") or {}).get("status") and not any(e.get("kind") == "calltracker" for e in lead["timeline"])
    assert not [n for n in r.store.read("notifications") if "calltracker" in str(n.get("kind"))]
    r.fake.match_old = False
    assert r.c.post("/api/calltracker/retry", json={"workspace": "main"}, headers=r.h["owner"]).json()["requeued"] == 1
    ct.process_due(r.store)
    assert match_jobs(r)[0]["status"] == "done" and ACME in r.fake.matches


# ---------------------------------------------------------------- receiver
def test_receiver_reports_matched_and_not_found(r):
    connect(r)
    acme = r.lead["Acme"]["id"]
    r.fake.calls[1] = ct_call(1, number=ACME)                                # matched by phone
    r.fake.calls[2] = ct_call(2, number="8111122222")                        # nobody in Outreach
    hook(r, conn_id(r), {"id": "m1", "event": "call.outcome", "data": {"callId": 1}})
    hook(r, conn_id(r), {"id": "m2", "event": "call.outcome", "data": {"callId": 2}})
    hook(r, conn_id(r), {"id": "m3", "event": "calls.synced", "data": {"calls": [{"id": 3, "number_norm": "8333344444"}]}})
    jobs = match_jobs(r)
    assert len(jobs) == 1                                                    # one coalesced job for the day
    items = jobs[0]["items"]
    assert items[ACME]["status"] == "matched" and items[ACME]["externalId"] == acme
    assert items["8111122222"] == {"status": "not_found"} and items["8333344444"] == {"status": "not_found"}
    assert not any(p == "/api/v1/calls/3" for _, p, *_ in r.fake.requests)  # unmatched synced calls are not fetched
    assert set(r.store.read("ct_unmatched")["main"]) == {"8111122222", "8333344444"}


def test_workspace_isolation_by_connection(r):
    connect(r)
    connect(r, ws=r.w2)
    r.set("Other WS Co", id=r.other["id"], phone="8555566666")
    r.fake.calls[5] = ct_call(5, number="8555566666")
    hook(r, conn_id(r, "main"), {"id": "w1", "event": "call.outcome", "data": {"callId": 5}})
    main = match_jobs(r)
    assert len(main) == 1 and main[0]["ws"] == "main" and main[0]["items"]["8555566666"] == {"status": "not_found"}
    hook(r, conn_id(r, r.w2), {"id": "w2", "event": "call.outcome", "data": {"callId": 5}})
    w2 = [j for j in match_jobs(r) if j["ws"] == r.w2]
    assert w2[0]["items"]["8555566666"]["externalId"] == r.other["id"]
    assert "8555566666" not in (r.store.read("ct_unmatched").get(r.w2) or {})


# ---------------------------------------------------------------- not-found cache
def test_cached_number_matched_when_a_lead_later_has_it(r):
    connect(r)
    m.queue_results(r.store, "main", {"8777788888": None})
    ct.process_due(r.store)
    assert r.fake.matches["8777788888"]["status"] == "not_found"
    assert m.tick(r.store, now=10_000_000) is True                         # nobody has it yet: stays cached
    r.set("Cora Inc", phones=["+91 87777 88888"])                            # an edit adds the number
    assert m.tick(r.store, now=10_000_100) is True                           # inside the re-check interval: nothing yet
    assert m.tick(r.store, now=10_000_000 + m.RECHECK_SECONDS + 1) is False  # re-checked: matched, cache empty
    ct.process_due(r.store)
    row = r.fake.matches["8777788888"]
    assert row["status"] == "matched" and row["externalId"] == r.lead["Cora Inc"]["id"] and row["label"]["company"] == "Cora Inc"
    assert not r.store.read("ct_unmatched")


def test_cache_is_bounded_and_cleared_on_disconnect(r, monkeypatch):
    connect(r)
    monkeypatch.setattr(m, "CACHE_MAX", 50)
    m.queue_results(r.store, "main", {f"86000{i:05d}": None for i in range(80)})
    cache = r.store.read("ct_unmatched")["main"]
    assert len(cache) == 50
    r.c.delete("/api/calltracker?workspace=main", headers=r.h["owner"])
    assert not r.store.read("ct_unmatched") and not match_jobs(r, "pending")


# ---------------------------------------------------------------- erasure
def test_erase_covers_leads_that_only_had_a_match(r):
    connect(r)
    acme = next(l for l in r.store.read("leads") if l["company"] == "Acme")
    beta = next(l for l in r.store.read("leads") if l["company"] == "Beta Ltd")
    assert not erase.known_to_calltracker(r.store, acme)
    m.queue_results(r.store, "main", {ACME: acme, BETA: beta})
    assert erase.known_to_calltracker(r.store, beta)                         # waiting match counts too
    ct.process_due(r.store)
    acme = next(l for l in r.store.read("leads") if l["company"] == "Acme")
    assert acme["calltracker"]["match_sent_at"] and not acme["calltracker"].get("lead_id")
    job = erase.on_erase(r.store, acme, only_known=True)                     # a bulk delete: never pushed, but DialHalo has a label
    assert job and job["kind"] == "erase" and ACME in job["phones"]
    ct.process_due(r.store)
    assert ("DELETE", f"/api/v1/leads/external/mcn_outreach/{acme['id']}") in [(meth, p) for meth, p, *_ in r.fake.requests]
    cora = next(l for l in r.store.read("leads") if l["company"] == "Cora Inc")
    assert erase.on_erase(r.store, cora, only_known=True) is None            # never sent at all: skipped


def test_erase_drops_the_persons_waiting_match_entries(r):
    connect(r)
    acme = next(l for l in r.store.read("leads") if l["company"] == "Acme")
    m.queue_results(r.store, "main", {ACME: acme, "8999900000": None})
    erase.on_erase(r.store, acme)
    items = match_jobs(r)[0]["items"]
    assert ACME not in items and "8999900000" in items


def test_a_newer_result_replaces_the_number_in_a_failed_job(r):
    connect(r)
    r.fake.match_old = True
    m.queue_results(r.store, "main", {"8123400000": None, "8123400001": None})
    ct.process_due(r.store)
    acme = next(l for l in r.store.read("leads") if l["company"] == "Acme")
    m.queue_results(r.store, "main", {"8123400000": acme})
    failed, pending = match_jobs(r, "failed")[0], match_jobs(r, "pending")[0]
    assert list(failed["items"]) == ["8123400001"] and pending["items"]["8123400000"]["status"] == "matched"
