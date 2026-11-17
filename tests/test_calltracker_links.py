"""Several DialHalo workspaces per Outreach workspace: migration of the single connection, calls and match results per
link, erase / do-not-call fan-out, lead sync modes (primary tie-break, owners_members by cached members, all_with_phone),
backfill per link, one note_calls per webhook, the Event-woken worker and the receiver port. DialHalo is faked."""
import json
import threading
import time

import pytest

from app import calltracker as ct, calltracker_events as ev, calltracker_links as links, calltracker_match as m, calltracker_sync as sync
from tests.test_calltracker import KEY, SECRET, FakeCT, call_task, connect, ct_call, hook, r as base_r  # noqa: F401
from tests.test_calltracker_match import PATH

KEY2 = "ctk_bmi_key_987654321"
SECRET2 = "whsec_bmi_secret_xyz"
ACME, BETA, CORA = "9876543210", "9123456789", "9000011111"


class TwoFake(FakeCT):
    """FakeCT that answers per API key: KEY = "MAS Callnet" (member Ravi), KEY2 = "BMI" (member Priya)."""

    def __init__(self):
        super().__init__()
        self.history = []

    def __call__(self, method, url, headers, body):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        auth = headers.get("Authorization")
        if not self.fail:
            if path == "/api/v1/members":
                self.requests.append((method, path, None, auth))
                who = {"name": "Priya BMI", "email": "priya@x.in", "active": 1} if auth == f"Bearer {KEY2}" else {"name": "Ravi CT", "email": "ravi@x.in", "active": 1}
                return 200, {"data": [who]}
            if path == PATH and method == "PUT":
                self.requests.append((method, path, json.loads(body), auth))
                return 200, {"data": {"saved": len(json.loads(body)["matches"])}}
            if path == "/api/v1/reports/history":
                self.requests.append((method, path, json.loads(body), auth))
                return 200, {"data": {"total": len(self.history), "rows": self.history}}
        return super().__call__(method, url, headers, body)


@pytest.fixture
def r(base_r, monkeypatch):
    fake = TwoFake()
    monkeypatch.setattr(ct, "TRANSPORT", fake)
    monkeypatch.setattr("app.calltracker_backfill.BACKGROUND", False)
    monkeypatch.setattr("app.calltracker_backfill.SLEEP", lambda s: None)
    base_r.fake = fake
    base_r.store.update("settings", lambda s: {**s, "public_base_url": "https://outreach.example.com"})
    return base_r


def two_links(r):
    """main -> MAS Callnet (A, the first, primary) and BMI (B, added with the manual form)."""
    assert connect(r).status_code == 200
    body = {"workspace": "main", "new": True, "base_url": "https://bmi.example.com", "public_url": "https://bmi.example.com",
            "api_key": KEY2, "webhook_secret": SECRET2}
    card = r.c.put("/api/calltracker", json=body, headers=r.h["owner"]).json()
    a, b = card["links"]
    assert a["primary"] and not b["primary"] and a["lead_sync"] == b["lead_sync"] == "call_tasks"
    return a["id"], b["id"]


def put(r, link, **changes):
    res = r.c.put("/api/calltracker", json={"workspace": "main", "link": link, **changes}, headers=r.h["owner"])
    assert res.status_code == 200, res.text
    return res.json()


def auths(r, path_start, method=None):
    return [a for meth, p, _, a in r.fake.requests if p.startswith(path_start) and (method is None or meth == method)]


def drain(r):
    while ct.process_due(r.store):
        pass


# ---------------------------------------------------------------- migration
def test_existing_single_connection_becomes_the_first_link_and_keeps_its_receiver(r):
    old = {"id": "legacy1234567890abcd", "workspace": "main", "base_url": "https://calls.example.com", "api_key": KEY,
           "webhook_secret": SECRET, "enabled": True, "created_at": "2026-11-01T00:00:00+00:00", "last_event": {"ok": True}}
    r.store.write("ct_connections", {"main": old})                 # the shape before links
    r.store.write("ct_backfill", {"main": {"status": "done", "days": 30}})
    r.store.write("ct_outbox", [{"id": "j1", "kind": "push", "ws": "main", "lead_id": r.lead["Acme"]["id"], "status": "pending", "attempts": 0}])
    assert links.migrate(r.store) == 1 and links.migrate(r.store) == 0                     # idempotent
    rows = r.store.read("ct_connections")
    assert list(rows) == ["legacy1234567890abcd"]
    link = rows["legacy1234567890abcd"]
    assert link["primary"] and link["lead_sync"] == "call_tasks" and link["legacy"] and link["api_key"] == KEY
    assert r.store.read("ct_backfill") == {"legacy1234567890abcd": {"status": "done", "days": 30}}
    assert r.store.read("ct_outbox")[0]["link"] == "legacy1234567890abcd"
    raw = r.store.path.read_text()
    assert KEY not in raw and SECRET not in raw                                            # still sealed at rest
    card = r.c.get("/api/calltracker", headers=r.h["owner"]).json()
    assert card["receiver"]["public"].endswith("/api/calltracker/hook/legacy1234567890abcd")
    assert hook(r, "legacy1234567890abcd", {"id": "e-old", "event": "test", "data": {}}).status_code == 200
    # a lead pushed before the migration belongs to the legacy link (no copy in another DialHalo workspace)
    r.set("Acme", calltracker={"lead_id": "ct-9"})
    acme = next(l for l in r.store.read("leads") if l["company"] == "Acme")
    assert links.pushed_link(r.store, acme, links.links_of(r.store, "main")) == "legacy1234567890abcd"


def test_lazy_migration_on_first_read(r):
    r.store.write("ct_connections", {"main": {"id": "lazyid", "workspace": "main", "base_url": "https://calls.example.com", "api_key": KEY}})
    assert ct.connection(r.store, "main")["id"] == "lazyid" and "main" not in r.store.read("ct_connections")


# ---------------------------------------------------------------- calls and match results per link
def test_call_from_link_b_is_matched_and_its_result_goes_to_b_only(r):
    a, b = two_links(r)
    r.fake.calls[7] = ct_call(7, number=ACME, outcome="Interested")
    res = hook(r, b, {"id": "e1", "event": "call.outcome", "data": {"callId": 7}}, secret=SECRET2)
    assert res.status_code == 200 and res.json()["applied"] == 1
    assert hook(r, b, {"id": "e2", "event": "test", "data": {}}, secret=SECRET).status_code == 401    # A's secret never opens B
    jobs = [j for j in r.store.read("ct_outbox") if j["kind"] == "match"]
    assert len(jobs) == 1 and jobs[0]["link"] == b
    drain(r)
    assert auths(r, PATH) == [f"Bearer {KEY2}"]
    row = next(x for x in r.store.read("activities") if x.get("source") == "calltracker")
    assert row["ct"]["conn"] == b
    card = r.c.get("/api/calltracker", headers=r.h["owner"]).json()
    by = {l["id"]: l for l in card["links"]}
    assert "signature" in by[b]["last_event"]["detail"] and by[a]["last_event"] is None and by[b]["last_match"]["ok"]   # the refused one, on B


def test_one_note_calls_per_webhook(r, monkeypatch):
    a, _ = two_links(r)
    seen = []
    real = m.note_calls
    monkeypatch.setattr(m, "note_calls", lambda store, target, found: (seen.append(dict(found)), real(store, target, found))[1])
    r.fake.calls[11] = ct_call(11, number=ACME)
    payload = {"id": "s1", "event": "calls.synced", "data": {"calls": [{"id": 11, "number_norm": ACME}, {"id": 12, "number_norm": "8111122222"}]}}
    assert hook(r, a, payload).status_code == 200
    assert len(seen) == 1 and set(seen[0]) == {ACME, "8111122222"}
    jobs = [j for j in r.store.read("ct_outbox") if j["kind"] == "match" and j["status"] == "pending"]
    assert len(jobs) == 1 and jobs[0]["link"] == a and set(jobs[0]["items"]) == {ACME, "8111122222"}


def test_cached_not_found_number_is_sent_as_matched_to_every_link_that_reported_it(r):
    a, b = two_links(r)
    m.queue_results(r.store, a, {"8222233333": None})
    m.queue_results(r.store, b, {"8222233333": None})
    assert r.store.read("ct_unmatched")["main"]["8222233333"]["links"] == sorted([a, b])
    drain(r)
    r.set("Cora Inc", phone="8222233333")
    m.recheck(r.store)
    pending = [j for j in r.store.read("ct_outbox") if j["kind"] == "match" and j["status"] == "pending"]
    assert sorted(j["link"] for j in pending) == sorted([a, b])
    assert all(j["items"]["8222233333"]["status"] == "matched" for j in pending)


# ---------------------------------------------------------------- erase and do-not-call fan out
def test_erase_and_do_not_call_go_to_every_link(r):
    a, b = two_links(r)
    acme = r.lead["Acme"]["id"]
    ct.on_opt_out(r.store, acme, "Opted out")
    dnc = [j for j in r.store.read("ct_outbox") if j["kind"] == "dnc"]
    assert sorted(j["link"] for j in dnc) == sorted([a, b])
    drain(r)
    assert sorted(auths(r, "/api/v1/dnc", "POST")) == sorted([f"Bearer {KEY}", f"Bearer {KEY2}"])
    r.fake.requests.clear()
    assert r.c.post(f"/api/leads/{acme}/erase", json={"confirm": True}, headers=r.h["owner"]).status_code == 200
    erase = [j for j in r.store.read("ct_outbox") if j["kind"] == "erase"]
    assert sorted(j["link"] for j in erase) == sorted([a, b])
    drain(r)
    assert sorted(auths(r, "/api/v1/leads/external/", "DELETE")) == sorted([f"Bearer {KEY}", f"Bearer {KEY2}"])


# ---------------------------------------------------------------- lead sync
def test_call_task_goes_to_one_link_primary_wins_and_a_pushed_lead_stays(r):
    a, b = two_links(r)
    call_task(r, "Acme", owner="Ravi")
    assert [j["link"] for j in r.store.read("ct_outbox") if j["kind"] == "push"] == [a]          # both qualify: the primary
    drain(r)
    put(r, b, primary=True)
    call_task(r, "Beta Ltd")
    call_task(r, "Acme")                                                                          # already in A: stays in A
    pushes = {j["lead_id"]: j["link"] for j in r.store.read("ct_outbox") if j["kind"] == "push" and j["status"] == "pending"}
    assert pushes == {r.lead["Beta Ltd"]["id"]: b, r.lead["Acme"]["id"]: a}
    put(r, a, lead_sync="off")
    put(r, b, lead_sync="off")
    r.store.write("ct_outbox", [])
    call_task(r, "Cora Inc")
    assert not r.store.read("ct_outbox")                                                          # off everywhere: nothing


def test_owners_members_uses_the_cached_members_list(r):
    a, b = two_links(r)
    put(r, a, lead_sync="off")
    card = put(r, b, lead_sync="owners_members")                                                  # refreshes members, sweeps
    pushes = [j for j in r.store.read("ct_outbox") if j["kind"] == "push"]
    assert [(j["lead_id"], j["link"]) for j in pushes] == [(r.lead["Beta Ltd"]["id"], b)]         # Priya's lead only
    by = {l["id"]: l for l in card["links"]}
    assert by[b]["pairing"]["checked"] and any(p["name"] == "Priya" and p["matched"] for p in by[b]["pairing"]["people"])
    r.fake.requests.clear()
    sync.sweep(r.store)                                                                           # cache fresh: no new members call
    assert not auths(r, "/api/v1/members") and len([j for j in r.store.read("ct_outbox") if j["kind"] == "push"]) == 1
    call_task(r, "Acme", owner="Ravi")                                                            # Ravi is not a BMI member
    assert len([j for j in r.store.read("ct_outbox") if j["kind"] == "push"]) == 1


def test_all_with_phone_sweeps_every_lead_with_a_phone_but_never_opted_out_ones(r):
    a, b = two_links(r)
    r.set("Cora Inc", phone="", phones=[])
    r.set("Beta Ltd", do_not_contact=True)
    put(r, a, lead_sync="all_with_phone")
    pushed = {j["lead_id"] for j in r.store.read("ct_outbox") if j["kind"] == "push"}
    assert r.lead["Acme"]["id"] in pushed and r.lead["Beta Ltd"]["id"] not in pushed and r.lead["Cora Inc"]["id"] not in pushed
    assert all(j["link"] == a for j in r.store.read("ct_outbox") if j["kind"] == "push")
    assert sync.sweep(r.store) == 0                                                               # already waiting: not twice
    assert not any(j["ws"] == r.w2 for j in r.store.read("ct_outbox"))                           # other workspaces untouched


def test_bad_lead_sync_value_and_several_links_need_a_link_id(r):
    two_links(r)
    assert r.c.put("/api/calltracker", json={"workspace": "main", "enabled": True}, headers=r.h["owner"]).status_code == 400
    assert r.c.delete("/api/calltracker?workspace=main", headers=r.h["owner"]).status_code == 400
    a = links.links_of(r.store, "main")[0]["id"]
    assert r.c.put("/api/calltracker", json={"workspace": "main", "link": a, "lead_sync": "everyone"}, headers=r.h["owner"]).status_code == 400
    assert r.c.put("/api/calltracker", json={"workspace": r.w2, "link": a, "enabled": True}, headers=r.h["owner"]).status_code == 404


# ---------------------------------------------------------------- backfill per link
def test_backfill_runs_per_link(r):
    a, b = two_links(r)
    r.fake.history = [{"id": 1, "client_number": ACME}, {"id": 2, "client_number": "8444455555"}]
    res = r.c.post("/api/calltracker/backfill", json={"workspace": "main", "link": b, "days": 7}, headers=r.h["owner"])
    assert res.status_code == 200, res.text
    assert auths(r, "/api/v1/reports/history") == [f"Bearer {KEY2}"]
    state = r.store.read("ct_backfill")
    assert state[b]["status"] == "done" and state[b]["matched"] == 1 and a not in state
    assert all(j["link"] == b for j in r.store.read("ct_outbox") if j["kind"] == "match")
    card = r.c.get("/api/calltracker", headers=r.h["owner"]).json()
    assert {l["id"]: l["backfill"].get("status") for l in card["links"]} == {a: None, b: "done"}


# ---------------------------------------------------------------- worker and receiver
def test_kick_wakes_the_idle_worker_at_once(r, monkeypatch):
    connect(r)
    monkeypatch.setattr(ct, "KICK", True)
    r.store.write("ct_unmatched", {"main": {"8000000001": {"number": "8000000001", "last_seen": "2026-11-17T00:00:00+00:00"}}})
    monkeypatch.setattr(m, "_last_recheck", {"at": time.time()})                                  # no re-check during the test
    ct.kick(r.store)                                                                              # idles: waits up to 60 s
    time.sleep(0.3)
    started = time.time()
    call_task(r, "Acme", owner="Ravi")
    while time.time() - started < 5 and not any(j["status"] == "done" for j in r.store.read("ct_outbox")):
        time.sleep(0.05)
    assert any(j["status"] == "done" for j in r.store.read("ct_outbox")) and time.time() - started < 3
    r.store.write("ct_unmatched", {})                                                             # let the worker stop
    ct._wake.set()
    for _ in range(100):
        if not ct._worker["running"]:
            break
        time.sleep(0.05)
    assert not ct._worker["running"]


def test_receiver_internal_port_comes_from_config(r, monkeypatch):
    connect(r)
    assert ":8000/api/calltracker/hook/" in r.c.get("/api/calltracker", headers=r.h["owner"]).json()["receiver"]["internal"]
    monkeypatch.setenv("OUTREACH_PORT", "8123")
    card = r.c.get("/api/calltracker", headers=r.h["owner"]).json()
    assert card["receiver"]["internal"].startswith("http://127.0.0.1:8123/api/calltracker/hook/")
    assert card["links"][0]["receiver"]["public"].startswith("https://outreach.example.com/api/calltracker/hook/")
    from app.routes_calltracker import internal_port
    monkeypatch.delenv("OUTREACH_PORT")

    class Req:
        scope = {"server": ("127.0.0.1", 9001)}
    assert internal_port(Req()) == 9001


def test_deleting_one_link_keeps_the_other_and_moves_primary(r):
    a, b = two_links(r)
    m.queue_results(r.store, a, {"8666677777": None})
    res = r.c.delete(f"/api/calltracker?workspace=main&link={a}", headers=r.h["owner"])
    assert res.status_code == 200 and "revoke the key" in res.json()["message"]                  # FakeCT: no connect/self (older)
    left = links.links_of(r.store, "main")
    assert [l["id"] for l in left] == [b] and left[0]["primary"]
    assert not [j for j in r.store.read("ct_outbox") if j.get("link") == a and j["status"] != "done"]
    assert not r.store.read("ct_unmatched")                                                       # only A had reported it
    assert ev.handle(r.store, a, "t=1,v1=" + "0" * 64, b"{}")[0] == 404
