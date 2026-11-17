"""Fixes from the end-to-end test of one-click connect: the SPA keeps DialHalo's return for CallTracker (node-run helper),
a migrated / manual link to the same DialHalo workspace is found (connect/self) and replaced, DELETE connect/self carries
the old receiver so DialHalo drops its webhook, and a refused key shows "Not authorised" until a test succeeds."""
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app import calltracker as ct, calltracker_connect as connect, calltracker_links as links
from tests.test_calltracker import KEY, SECRET, call_task
from tests.test_calltracker_connect import NEW_KEY, finish, r, start, base_r  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parent.parent
MAS = {"id": "ws_mas", "name": "MAS Callnet", "slug": "mas"}


def legacy(r, base="https://calls.example.com", public="https://calls.example.com"):
    """The single connection as it was before links, then migrated (manual, legacy, no DialHalo workspace)."""
    r.store.write("ct_connections", {"main": {"id": "legacyid0000000000ab", "workspace": "main", "base_url": base, "public_url": public,
                                              "api_key": KEY, "webhook_secret": SECRET, "enabled": True, "created_at": "2026-11-01T00:00:00+00:00"}})
    links.migrate(r.store)
    return "legacyid0000000000ab"


def connect_mas(r):
    r.fake.self_ws[f"Bearer {KEY}"] = r.fake.self_ws[f"Bearer {NEW_KEY}"] = MAS
    good = r.fake.__call__

    def exchange_mas(method, url, headers, body):
        status, out = good(method, url, headers, body)
        if url.endswith("/api/connect/exchange") and status == 200:
            out["data"]["workspace"] = MAS
        return status, out
    ct.TRANSPORT = exchange_mas
    out = start(r).json()
    res = finish(r, parse_qs(urlparse(out["url"]).query)["state"][0])
    ct.TRANSPORT = r.fake
    return out, res


# ---------------------------------------------------------------- B: the SPA keeps the return
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_connect_return_helper_under_node():
    hashes = ["#integrations/dialhalo/connected?tab=calltracker&dh_state=st1&dh_code=c1",
              "#integrations/dialhalo/connected?tab=calltracker&dh_state=st2&dh_error=access_denied",
              "#integrations?tab=calltracker", "", "#integrations?connected=google"]
    out = subprocess.run(["node", str(ROOT / "tests" / "dialhalo_return.mjs"), str(ROOT / "web/src/pages/integrations/dialhaloReturn.js")],
                         input=json.dumps(hashes), capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [{"state": "st1", "code": "c1", "error": ""}, {"state": "st2", "code": "", "error": "access_denied"}, None, None, None]


def test_integrations_reads_the_return_before_tidying_the_hash_and_passes_it_down():
    src = (ROOT / "web/src/pages/Integrations.jsx").read_text()
    read = src.index("readConnectReturn(window.location.hash)")
    assert read < src.index('replaceState(null, "", "#integrations")')          # first render, before the mount effect
    assert re.search(r"<CallTracker connectReturn=\{dhReturn\} />", src)
    page = (ROOT / "web/src/pages/integrations/CallTracker.jsx").read_text()
    assert "takeReturn(connectReturn ||" in page


# ---------------------------------------------------------------- C: the migrated link is replaced, not doubled
def test_one_click_connect_replaces_a_migrated_link_to_the_same_dialhalo_workspace(r):
    old = legacy(r)
    assert not links.get(r.store, old).get("dh_workspace")
    out, res = connect_mas(r)
    assert res.status_code == 200, res.text
    assert "replaced" in res.json()["message"]
    left = links.links_of(r.store, "main")
    assert [l["id"] for l in left] == [out["link_id"]] and left[0]["primary"] and left[0]["dh_workspace"]["id"] == "ws_mas"
    gets = [a for m, p, d, a in r.fake.requests if p == "/api/v1/connect/self" and m == "GET"]
    assert f"Bearer {KEY}" in gets                                                   # asked which workspace the old key is
    deletes = [(d, a) for m, p, d, a in r.fake.requests if p == "/api/v1/connect/self" and m == "DELETE"]
    assert deletes == [({"receiver": f"https://outreach.example.com/api/calltracker/hook/{old}"}, f"Bearer {KEY}")]


def test_a_link_to_another_dialhalo_workspace_is_kept(r):
    old = legacy(r)
    r.fake.self_ws[f"Bearer {KEY}"] = MAS
    out = start(r).json()
    finish(r, parse_qs(urlparse(out["url"]).query)["state"][0])                     # BMI: a different workspace
    assert sorted(l["id"] for l in links.links_of(r.store, "main")) == sorted([old, out["link_id"]])


def test_start_up_fills_the_dialhalo_workspace_of_old_links_best_effort(r):
    old = legacy(r)
    r.fake.self_ws[f"Bearer {KEY}"] = MAS
    assert connect.fill_missing_workspaces(r.store) == 1
    assert links.get(r.store, old)["dh_workspace"] == MAS
    assert connect.fill_missing_workspaces(r.store) == 0                            # nothing left to ask
    links.save(r.store, "main", old, {"dh_workspace": {}}, "t")
    r.fake.self_status = 404                                                         # older DialHalo: left as it is
    assert connect.fill_missing_workspaces(r.store) == 0 and not links.get(r.store, old)["dh_workspace"]
    ct.TRANSPORT = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))           # never raises
    try:
        assert connect.fill_missing_workspaces(r.store) == 0
    finally:
        ct.TRANSPORT = r.fake


# ---------------------------------------------------------------- D: the old receiver goes with the key
def test_disconnect_sends_the_receiver_and_404_names_the_webhook_to_delete(r):
    old = legacy(r)
    res = r.c.delete(f"/api/calltracker?workspace=main&link={old}", headers=r.h["owner"])
    receiver = f"https://outreach.example.com/api/calltracker/hook/{old}"
    assert res.status_code == 200 and "removed its webhook" in res.json()["message"]
    assert [d for m, p, d, a in r.fake.requests if m == "DELETE" and p == "/api/v1/connect/self"] == [{"receiver": receiver}]
    old = legacy(r)
    r.fake.self_status = 404
    msg = r.c.delete(f"/api/calltracker?workspace=main&link={old}", headers=r.h["owner"]).json()["message"]
    assert "revoke the key in DialHalo" in msg and f"delete the webhook to {receiver} in DialHalo Settings → Webhooks" in msg


# ---------------------------------------------------------------- E: a revoked key shows on the card
def test_refused_key_shows_not_authorised_until_a_test_succeeds(r):
    out = start(r).json()
    finish(r, parse_qs(urlparse(out["url"]).query)["state"][0])
    lid = out["link_id"]
    view = lambda: next(l for l in r.c.get("/api/calltracker", headers=r.h["owner"]).json()["links"] if l["id"] == lid)
    assert view()["auth_failed"] is None
    r.fake.fail = [401]
    assert not r.c.post("/api/calltracker/test", json={"workspace": "main", "link": lid}, headers=r.h["owner"]).json()["ok"]
    assert "refused" in view()["auth_failed"]["detail"]
    assert r.c.post("/api/calltracker/test", json={"workspace": "main", "link": lid}, headers=r.h["owner"]).json()["ok"]
    assert view()["auth_failed"] is None
    call_task(r, "Acme", owner="Ravi")                                               # a send refused by DialHalo
    r.fake.fail = [401]
    ct.process_due(r.store)
    assert view()["auth_failed"] and NEW_KEY not in json.dumps(view())
    r.c.post("/api/calltracker/retry", json={"workspace": "main"}, headers=r.h["owner"])
    ct.process_due(r.store)                                                          # a later send works: cleared
    assert view()["auth_failed"] is None
