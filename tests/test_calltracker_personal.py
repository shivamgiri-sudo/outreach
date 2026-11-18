"""Personal DialHalo connections (personal-connect-contract.md, Outreach side): scope and member from connect/self at finish
and on Test, several personal links per DialHalo workspace, lead sync coerced to off / call_tasks / owners_members,
routing to a personal link only for leads its member owns (a workspace link to the same DialHalo workspace wins),
one call row when a workspace link and a personal link both deliver the same call, outcome / history routing,
do-not-call / send-now per-row skips (not_yours), and the personal_connection (403) / connection_paused (401) codes. DialHalo is faked in memory."""
from urllib.parse import parse_qs, urlparse

import pytest

from app import calltracker as ct, calltracker_links as links
from tests.test_calltracker import KEY, SECRET, connect, ct_call, hook, r as base_r  # noqa: F401
from tests.test_calltracker_connect import ConnectFake, NEW_KEY, finish, start
from tests.test_calltracker_links import drain
from tests.test_calltracker_outcomes import SyncFake, T1, T2, change, dh_row, sent, sync_jobs

ACME = "9876543210"
PKEY, PSECRET = "ctk_personal_ravi_123456", "whsec_personal_ravi_1"
PKEY2, PSECRET2 = "ctk_personal_asha_123456", "whsec_personal_asha_1"
MAS = {"id": "ws_mas", "name": "MAS Callnet", "slug": "mas"}
RAVI = {"id": "u1", "name": "Ravi CT", "email": "ravi@x.in"}
ASHA = {"id": "u2", "name": "Asha", "email": "asha@ct.in"}


class PersonalFake(SyncFake, ConnectFake):
    """SyncFake (outcome / history endpoints) + ConnectFake (exchange, connect/self) with scope per key."""

    def __init__(self):
        SyncFake.__init__(self)
        self.exchange, self.codes, self.self_status, self.self_ws = None, {"good-code": True}, 200, {}
        self.scope = {}                    # Authorization -> (scope, member) connect/self answers with

    def __call__(self, method, url, headers, body):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        auth = headers.get("Authorization")
        if path == "/api/v1/connect/self" and method == "GET" and auth in self.scope:
            self.requests.append((method, path, None, auth))
            scope, member = self.scope[auth]
            return 200, {"data": {"workspace": {"id": "ws_bmi", "name": "BMI", "slug": "bmi"}, "connectionId": "dc_9", "scopes": ["leads"], "scope": scope, "member": member}}
        return SyncFake.__call__(self, method, url, headers, body)


@pytest.fixture
def r(base_r, monkeypatch):
    fake = PersonalFake()
    monkeypatch.setattr(ct, "TRANSPORT", fake)
    base_r.fake = fake
    base_r.store.update("settings", lambda s: {**s, "public_base_url": "https://outreach.example.com"})
    return base_r


def personal(r, key=PKEY, secret=PSECRET, member=RAVI, lead_sync="call_tasks", dh=MAS):
    return links.save(r.store, "main", None, {"base_url": "https://calls.example.com", "public_url": "https://calls.example.com", "api_key": key,
                                              "webhook_secret": secret, "dh_workspace": dh, "dh_scope": "member", "dh_member": member,
                                              "lead_sync": lead_sync, "via": "connect"}, "test")


def workspace_link(r, dh=MAS):
    assert connect(r, new=True).status_code == 200
    link = next(l for l in links.links_of(r.store, "main") if l.get("api_key") == KEY)
    return links.save(r.store, "main", link["id"], {"dh_workspace": dh}, "test")


def task(r, company):
    res = r.c.post("/api/tasks", json={"lead_id": r.lead[company]["id"], "title": "Call them", "kind": "call"}, headers=r.h["owner"])
    assert res.status_code == 200
    return [j for j in r.store.read("ct_outbox") if j["kind"] == "push" and j["lead_id"] == r.lead[company]["id"]]


# ---------------------------------------------------------------- finish / refresh
def test_finish_stores_scope_and_member_and_two_members_get_two_links(r):
    r.fake.scope = {f"Bearer {NEW_KEY}": ("member", {**RAVI, "email": "Ravi@X.in"})}
    state = parse_qs(urlparse(start(r).json()["url"]).query)["state"][0]
    res = finish(r, state)
    assert res.status_code == 200, res.text
    assert "Personal connection: only Ravi CT's calls" in res.json()["message"]
    link = links.get(r.store, res.json()["link"])
    assert link["dh_scope"] == "member" and link["dh_member"] == {"id": "u1", "name": "Ravi CT", "email": "ravi@x.in"}
    assert not [p for m, p, d, a in r.fake.requests if p.startswith("/api/v1/dnc")]   # a personal key never reads do-not-call
    card = r.c.get("/api/calltracker", headers=r.h["owner"]).json()
    view = next(l for l in card["links"] if l["id"] == link["id"])
    assert view["dh_scope"] == "member" and view["dh_member"]["name"] == "Ravi CT"
    # another member connects their own calls: a second personal link, the first stays
    r.fake.codes["good-code"] = True
    r.fake.scope = {f"Bearer {NEW_KEY}": ("member", ASHA)}
    state = parse_qs(urlparse(start(r).json()["url"]).query)["state"][0]
    second = finish(r, state).json()["link"]
    assert {l["id"] for l in links.links_of(r.store, "main")} == {link["id"], second}
    # the same member again replaces only their own link
    r.fake.codes["good-code"] = True
    state = parse_qs(urlparse(start(r).json()["url"]).query)["state"][0]
    third = finish(r, state)
    assert third.status_code == 200, third.text
    third = third.json()
    assert "earlier link to the same DialHalo workspace was replaced" in third["message"] and {l["id"] for l in links.links_of(r.store, "main")} == {link["id"], third["link"]}
    # refresh (Test) keeps the scope current; a missing scope means whole workspace
    r.fake.scope = {f"Bearer {NEW_KEY}": (None, None)}
    assert r.c.post("/api/calltracker/test", json={"workspace": "main", "link": link["id"]}, headers=r.h["owner"]).status_code == 200
    assert links.get(r.store, link["id"])["dh_scope"] == "workspace"


def test_older_dialhalo_without_scope_is_a_workspace_link(r):
    state = parse_qs(urlparse(start(r).json()["url"]).query)["state"][0]
    link = links.get(r.store, finish(r, state).json()["link"])
    assert link["dh_scope"] == "workspace" and not links.is_personal(link)


def test_personal_lead_sync_is_coerced_to_owners_members(r):
    link = personal(r, lead_sync="all_with_phone")
    assert link["lead_sync"] == "owners_members"
    res = r.c.put("/api/calltracker", json={"workspace": "main", "link": link["id"], "lead_sync": "all_with_phone"}, headers=r.h["owner"])
    assert res.status_code == 200
    assert links.get(r.store, link["id"])["lead_sync"] == "owners_members"
    r.c.put("/api/calltracker", json={"workspace": "main", "link": link["id"], "lead_sync": "off"}, headers=r.h["owner"])
    assert links.get(r.store, link["id"])["lead_sync"] == "off"


# ---------------------------------------------------------------- routing
def test_routing_owner_paired_not_paired_and_workspace_link_wins(r):
    p = personal(r)                                           # Ravi's own calls; Acme is owned by Ravi, Beta by Priya
    acme_jobs = task(r, "Acme")
    assert [j["link"] for j in acme_jobs] == [p["id"]]
    assert task(r, "Beta Ltd") == []                          # owner not paired: never to Ravi's personal link
    owners = personal(r, PKEY2, PSECRET2, ASHA, lead_sync="owners_members")
    assert links.route(r.store, next(l for l in r.store.read("leads") if l["company"] == "Beta Ltd")) is None
    assert owners["lead_sync"] == "owners_members"
    w = workspace_link(r)                                     # a workspace link to the same DialHalo workspace wins
    r.store.write("ct_outbox", [])
    assert [j["link"] for j in task(r, "Acme")] == [w["id"]]
    assert [j["link"] for j in task(r, "Beta Ltd")] == [w["id"]]
    other = personal(r, "ctk_personal_other_9999", "whsec_o", RAVI, dh={"id": "ws_other", "name": "Other", "slug": "o"})
    lead = next(l for l in r.store.read("leads") if l["company"] == "Acme")
    assert links.qualifies(r.store, other, lead, call_task=True) and not links.shadowed(other, links.active_links(r.store, "main"))


def test_do_not_call_goes_to_a_personal_link_only_without_a_workspace_link_and_per_row_skips(r):
    p = personal(r)
    ct.on_opt_out(r.store, r.lead["Acme"]["id"], "Unsubscribed")
    assert {j["link"] for j in r.store.read("ct_outbox") if j["kind"] == "dnc"} == {p["id"]}
    real = r.fake.__class__.__call__

    def skip_dnc(self, method, url, headers, body):
        if url.endswith("/api/v1/dnc") and method == "POST":
            self.requests.append((method, "/api/v1/dnc", None, headers.get("Authorization")))
            return 200, {"data": {"added": False, "skipped": True, "reason": "not_yours"}}
        return real(self, method, url, headers, body)
    r.fake.__class__.__call__ = skip_dnc
    try:
        drain(r)
    finally:
        r.fake.__class__.__call__ = real
    job = next(j for j in r.store.read("ct_outbox") if j["kind"] == "dnc")
    assert job["status"] == "done" and "not_yours" in job["result"]
    assert not (next(l for l in r.store.read("leads") if l["company"] == "Acme").get("calltracker") or {}).get("dnc")
    w = workspace_link(r)
    r.store.write("ct_outbox", [])
    ct.on_opt_out(r.store, r.lead["Beta Ltd"]["id"], "Unsubscribed")
    assert {j["link"] for j in r.store.read("ct_outbox") if j["kind"] == "dnc"} == {w["id"]}


def test_send_now_on_a_personal_link_reports_not_yours_rows(r, monkeypatch):
    p = personal(r)
    from app import calltracker_kit as kit
    acme, beta = (next(l for l in r.store.read("leads") if l["company"] == c) for c in ("Acme", "Beta Ltd"))
    monkeypatch.setattr(kit, "_request", lambda store, link, method, path, body=None: {
        "saved": [{"index": 0, "id": "dh-1", "created": True}, {"index": 1, "skipped": True, "reason": "not_yours"}]})
    out = kit.send_leads(r.store, "main", [acme, beta], "https://x", links.get(r.store, p["id"]))
    assert out["sent"] == 1 and out["skipped"] == [{"lead_id": beta["id"], "reason": "not_yours"}]


def test_personal_connection_403_skips_the_job_for_good_and_paused_401_backs_off(r):
    p = personal(r)
    r.store.write("ct_outbox", [])
    task(r, "Acme")
    real = r.fake.__class__.__call__
    answers = []

    def coded(self, method, url, headers, body):
        if answers and "/leads/external/" in url:
            status, code = answers.pop(0)
            self.requests.append((method, url, None, headers.get("Authorization")))
            return status, {"code": code, "message": "x"}
        return real(self, method, url, headers, body)
    r.fake.__class__.__call__ = coded
    try:
        answers.append((401, "connection_paused"))
        ct.process_due(r.store)
        job = next(j for j in r.store.read("ct_outbox") if j["kind"] == "push")
        link = links.get(r.store, p["id"])
        assert job["status"] == "pending" and job["attempts"] == 1 and "Paused in DialHalo" in job["error"]
        assert link.get("paused_in_dh") and not link.get("auth_failed")
        view = next(l for l in r.c.get("/api/calltracker", headers=r.h["owner"]).json()["links"] if l["id"] == p["id"])
        assert view["paused_in_dh"] and not view["auth_failed"]
        r.store.update("ct_outbox", lambda jobs: [{**j, "next_at": "2000-01-01T00:00:00+00:00"} for j in jobs])
        answers.append((403, "personal_connection"))
        ct.process_due(r.store)
        job = next(j for j in r.store.read("ct_outbox") if j["kind"] == "push")
        link = links.get(r.store, p["id"])
        assert job["status"] == "done" and job["result"].startswith("skipped:")             # not retried
        assert link.get("personal_refused") and not link.get("auth_failed")
        assert ct.process_due(r.store) == 0
        # a later success clears "Paused in DialHalo"
        links.note_auth(r.store, p["id"], False)
        assert not links.get(r.store, p["id"]).get("paused_in_dh")
    finally:
        r.fake.__class__.__call__ = real


# ---------------------------------------------------------------- the same call from two links
@pytest.mark.parametrize("first", ["personal", "workspace"])
def test_same_call_from_workspace_and_personal_link_is_recorded_once(r, first):
    p, w = personal(r), workspace_link(r)
    r.fake.calls[31] = ct_call(31, number=ACME, outcome="Interested")
    order = [(p["id"], PSECRET), (w["id"], SECRET)] if first == "personal" else [(w["id"], SECRET), (p["id"], PSECRET)]
    for i, (lid, secret) in enumerate(order):
        data = {"callId": 31, "outcomeName": "Interested", "changedAt": T1, "origin": "app", "by": RAVI}
        assert hook(r, lid, {"id": f"d{i}", "event": "call.outcome", "data": data}, secret=secret).status_code == 200
    rows = [a for a in r.store.read("activities") if a.get("source") == "calltracker"]
    assert len(rows) == 1 and rows[0]["ct"]["conn"] == w["id"] and rows[0]["ct"]["dh_ws"] == "ws_mas"
    assert len([t for t in r.store.read("tasks") if t["title"] == "Follow up after the yes"]) == 1     # rules ran once
    assert len([e for e in next(l for l in r.store.read("leads") if l["company"] == "Acme")["timeline"] if e["kind"] == "call"]) == 1
    other = personal(r, "ctk_personal_other_9999", "whsec_o", RAVI, dh={"id": "ws_other", "name": "Other", "slug": "o"})
    hook(r, other["id"], {"id": "d9", "event": "call.outcome", "data": {"callId": 31, "changedAt": T2, "origin": "app"}}, secret="whsec_o")
    assert len([a for a in r.store.read("activities") if a.get("source") == "calltracker"]) == 2      # another DialHalo workspace: its own call


# ---------------------------------------------------------------- outbound to the right link
def test_outcome_and_history_go_to_the_right_link(r):
    p = personal(r)
    r.fake.calls[41] = ct_call(41, number=ACME, outcome="Interested")
    hook(r, p["id"], {"id": "o1", "event": "call.outcome", "data": {"callId": 41, "changedAt": T1, "origin": "app"}}, secret=PSECRET)
    drain(r)
    row = dh_row(r, 41)
    change(r, "Acme", row["id"], "not_interested")
    drain(r)
    assert [a for m, path, d, a in sent(r, "PUT")] == [f"Bearer {PKEY}"]           # personal only: back to the link it came from
    # a call logged by hand: to Ravi's personal link for his lead, not for Priya's
    r.c.post(f"/api/leads/{r.lead['Acme']['id']}/activity", json={"type": "call", "outcome": "interested"}, headers=r.h["owner"])
    r.c.post(f"/api/leads/{r.lead['Beta Ltd']['id']}/activity", json={"type": "call", "outcome": "interested"}, headers=r.h["owner"])
    hist = sync_jobs(r, "pending")
    assert [(j["link"], j["lead_id"]) for j in hist] == [(p["id"], r.lead["Acme"]["id"])]
    drain(r)
    # with a workspace link to the same DialHalo workspace, it wins for outcomes and history
    w = workspace_link(r)
    change(r, "Acme", row["id"], "interested")
    r.c.post(f"/api/leads/{r.lead['Acme']['id']}/activity", json={"type": "call", "outcome": "busy"}, headers=r.h["owner"])
    assert {j["link"] for j in sync_jobs(r, "pending")} == {w["id"]}
    drain(r)
    assert [a for m, path, d, a in sent(r, "PUT")][-1] == f"Bearer {KEY}"
