"""Match past calls (Integrations -> DialHalo): paging through DialHalo's call history, one result per number through the
match job, the call cap, progress, continuing after a failure, re-runs, a too-old DialHalo, and who may start it."""
from app import calltracker as ct, calltracker_backfill as bf
from tests.test_calltracker import connect
from tests.test_calltracker import conn_id
from tests.test_calltracker_match import ACME, BETA, PATH, base_r, match_jobs, r  # noqa: F401  (fixtures)

HISTORY = "/api/v1/reports/history"


def rows(n, start=0):
    return [{"id": 100000 + i, "call_type": "outgoing", "client_number": f"72{(start + i) % 100000:08d}", "lead_id": None} for i in range(n)]


def start(r, days=30, who="owner", ws="main"):
    return r.c.post("/api/calltracker/backfill", json={"workspace": ws, "days": days}, headers=r.h[who])


def history_calls(r):
    return [d for meth, p, d, _ in r.fake.requests if p == HISTORY]




def test_pages_through_history_dedupes_numbers_and_sends_through_the_match_job(r):
    connect(r)
    r.fake.history = [{"id": 1, "client_number": ACME}, {"id": 2, "client_number": ACME}, {"id": 3, "client_number": "98******10"},
                      *rows(1100), {"id": 4, "client_number": BETA}]
    res = start(r)
    assert res.status_code == 200, res.text
    pages = history_calls(r)
    assert [p["page"] for p in pages] == [1, 2, 3] and all(p["pageSize"] == 500 and p["dateField"] == "call" for p in pages)
    assert pages[0]["from"] <= pages[0]["to"]
    state = r.c.get("/api/calltracker", headers=r.h["owner"]).json()["match"]["backfill"]
    assert state["status"] == "done" and state["calls"] == 1104 and state["numbers"] == 1102
    assert state["matched"] == 2 and state["not_found"] == 1100 and state["skipped"] == 1
    assert "seen" not in state
    assert sum(len(j["items"]) for j in match_jobs(r)) == 1102
    while ct.process_due(r.store):
        pass
    assert r.fake.matches[ACME]["status"] == "matched" and r.fake.matches[ACME]["externalId"] == r.lead["Acme"]["id"]
    assert all(len(d["matches"]) <= 500 for meth, p, d, _ in r.fake.requests if p == PATH)
    assert not r.store.read("ct_backfill")[conn_id(r)].get("seen")         # keyed by the DialHalo link               # numbers are not kept after the run


def test_rerun_is_idempotent(r):
    connect(r)
    r.fake.history = [{"id": 1, "client_number": ACME}, *rows(20)]
    start(r)
    while ct.process_due(r.store):
        pass
    first = dict(r.fake.matches)
    start(r)
    assert sum(len(j["items"]) for j in match_jobs(r, "pending")) == 21      # one entry per number again, no duplicates
    while ct.process_due(r.store):
        pass
    assert r.fake.matches == first


def test_call_cap_stops_the_run(r, monkeypatch):
    connect(r)
    monkeypatch.setattr(bf, "MAX_CALLS", 600)
    r.fake.history = rows(1600)
    start(r)
    state = bf.state(r.store, "main")
    assert state["status"] == "done" and state["calls"] == 600 and state["capped"] and "limit" in state["error"]
    assert len(history_calls(r)) == 2


def test_failure_saves_progress_and_a_new_start_continues(r):
    connect(r)
    r.fake.history = rows(1200)
    real = r.fake
    count = {"n": 0}

    def flaky(method, url, headers, body):
        if url.endswith(HISTORY):
            count["n"] += 1
            if count["n"] >= 2:
                return 503, {"message": "down"}
        return real(method, url, headers, body)
    ct.TRANSPORT = flaky
    start(r)
    ct.TRANSPORT = real
    state = bf.state(r.store, "main")
    assert state["status"] == "failed" and state["page"] == 1 and state["calls"] == 500 and "busy or down" in state["error"]
    assert count["n"] == 1 + bf.PAGE_TRIES
    start(r)                                                                 # same days: continues from page 2
    state = bf.state(r.store, "main")
    assert state["status"] == "done" and state["calls"] == 1200 and state["numbers"] == 1200
    assert [p["page"] for p in history_calls(r)] == [1, 2, 3]
    start(r, days=7)                                                         # other days: a fresh run
    assert bf.state(r.store, "main")["days"] == 7 and history_calls(r)[-1]["page"] == 3 and history_calls(r)[-3]["page"] == 1


def test_interrupted_run_shows_stopped(r):
    connect(r)
    r.store.update("ct_backfill", lambda rows: {**rows, conn_id(r): {"status": "running", "days": 30, "page": 2}})
    card = r.c.get("/api/calltracker", headers=r.h["owner"]).json()
    assert card["match"]["backfill"]["status"] == "stopped" and "restarted" in card["match"]["backfill"]["error"]


def test_too_old_dialhalo_gets_a_clear_message(r):
    connect(r)
    real = r.fake
    ct.TRANSPORT = lambda method, url, headers, body: (404, {"message": "Not found"}) if url.endswith(HISTORY) else real(method, url, headers, body)
    start(r)
    ct.TRANSPORT = real
    state = bf.state(r.store, "main")
    assert state["status"] == "failed" and "Update DialHalo" in state["error"]


def test_who_may_start_it_and_input_checks(r):
    assert start(r).status_code == 400                                       # not connected yet
    connect(r)
    connect(r, ws=r.w2)
    r.fake.history = rows(3)
    assert start(r, who="priya").status_code == 403
    assert start(r, who="view").status_code == 403
    assert start(r, who="adm").status_code == 403                            # a workspace admin never runs it for main
    assert start(r, who="adm2", ws=r.w2).status_code == 200                  # but does for its own workspace
    assert start(r, days=45).status_code == 400
    assert any(a["action"] == "calltracker.backfill" for a in r.store.read("audit"))
    bf._running.add(conn_id(r))
    try:
        assert start(r).status_code == 409                                   # one run per link at a time
    finally:
        bf._running.discard(conn_id(r))
