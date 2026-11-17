"""One-click "Connect a DialHalo workspace" (PKCE-style): pending state, challenge, expiry, single use, the code exchange
(secrets sealed), access_denied and exchange failures, the callback redirect, Test via connect/self, disconnect (DELETE
self, 404 fallback) and who may connect. DialHalo is faked in memory; no network."""
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest

from app import calltracker as ct, calltracker_links as links
from tests.test_calltracker import FakeCT, r as base_r  # noqa: F401

NEW_KEY = "ctk_connected_key_1122334455"
NEW_SECRET = "whsec_connected_secret_99"


class ConnectFake(FakeCT):
    def __init__(self):
        super().__init__()
        self.exchange = None             # (status, body) to answer the exchange with
        self.codes = {"good-code": True}
        self.self_status = 200

    def __call__(self, method, url, headers, body):
        path = "/" + url.split("://", 1)[1].split("/", 1)[1]
        auth = headers.get("Authorization")
        if path == "/api/connect/exchange" and method == "POST":
            data = json.loads(body)
            self.requests.append((method, path, data, auth))
            if self.exchange:
                return self.exchange
            if not self.codes.pop(data["code"], None):
                return 400, {"message": "Invalid or expired code"}
            return 200, {"data": {"apiKey": NEW_KEY, "webhookSecret": NEW_SECRET, "webhookId": 41, "connectionId": "dc_7",
                                  "workspace": {"id": "ws_bmi", "name": "BMI", "slug": "bmi"}}}
        if path == "/api/v1/connect/self":
            self.requests.append((method, path, None, auth))
            if self.self_status != 200:
                return self.self_status, {"message": "nope"}
            if method == "DELETE":
                return 200, {"data": {"revoked": True}}
            return 200, {"data": {"workspace": {"id": "ws_bmi", "name": "BMI", "slug": "bmi"}, "connectionId": "dc_7", "scopes": ["leads"], "webhook": {"id": 41}}}
        return super().__call__(method, url, headers, body)


@pytest.fixture
def r(base_r, monkeypatch):
    fake = ConnectFake()
    monkeypatch.setattr(ct, "TRANSPORT", fake)
    base_r.fake = fake
    base_r.store.update("settings", lambda s: {**s, "public_base_url": "https://outreach.example.com"})
    return base_r


def start(r, who="owner", ws="main", url="https://calls.example.com"):
    return r.c.post("/api/calltracker/connect/start", json={"workspace": ws, "dialhalo_url": url}, headers=r.h[who])


def finish(r, state, code="good-code", who="owner", error=""):
    return r.c.post("/api/calltracker/connect/finish", json={"state": state, "code": code, "error": error}, headers=r.h[who])


def pending(r):
    return r.store.read("ct_pending")


def test_start_makes_a_pending_link_with_a_correct_challenge(r):
    res = start(r)
    assert res.status_code == 200, res.text
    out = res.json()
    url = urlparse(out["url"])
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://calls.example.com/connect/outreach"
    assert q["client"] == "mcn_outreach" and q["name"] == "Main"
    assert q["callback"] == "https://outreach.example.com/api/calltracker/connect/callback"
    assert q["receiver"] == f"https://outreach.example.com/api/calltracker/hook/{out['link_id']}"
    cb, rc = urlparse(q["callback"]), urlparse(q["receiver"])
    assert (cb.scheme, cb.netloc) == (rc.scheme, rc.netloc)                          # DialHalo checks scheme + host + port
    row = pending(r)[q["state"]]
    assert 43 <= len(row["verifier"]) <= 128 and row["workspace"] == "main" and row["link_id"] == out["link_id"]
    expected = base64.urlsafe_b64encode(hashlib.sha256(row["verifier"].encode()).digest()).decode().rstrip("=")
    assert q["challenge"] == row["challenge"] == expected and "=" not in q["challenge"]
    assert (ct.parse(row["expires_at"]) - ct.parse(row["created_at"])).total_seconds() == 600
    assert row["verifier"] not in r.store.path.read_text()                         # sealed at rest
    assert r.store.read("settings")["dialhalo_last_url"] == "https://calls.example.com"
    assert r.c.get("/api/calltracker", headers=r.h["owner"]).json()["connect"]["dialhalo_url"] == "https://calls.example.com"
    assert start(r, url="http://calls.example.com").status_code == 400             # the browser address must be https


def test_callback_only_redirects_to_the_app(r):
    res = r.c.get("/api/calltracker/connect/callback?code=abc&state=st1", follow_redirects=False)       # no login
    assert res.status_code == 303
    loc = res.headers["location"]
    assert loc.startswith("/#integrations/dialhalo/connected?") and "dh_code=abc" in loc and "dh_state=st1" in loc and "tab=calltracker" in loc
    denied = r.c.get("/api/calltracker/connect/callback?error=access_denied&state=st1", follow_redirects=False)
    assert "dh_error=access_denied" in denied.headers["location"] and "dh_code" not in denied.headers["location"]


def test_finish_exchanges_the_code_saves_sealed_secrets_and_tests(r):
    out = start(r).json()
    state = parse_qs(urlparse(out["url"]).query)["state"][0]
    res = finish(r, state)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["message"].startswith("Connected to BMI.") and body["link"] == out["link_id"]
    exchange = [d for meth, p, d, a in r.fake.requests if p == "/api/connect/exchange"]
    assert exchange == [{"code": "good-code", "verifier": exchange[0]["verifier"]}]
    assert [a for meth, p, d, a in r.fake.requests if p == "/api/connect/exchange"] == [None]   # no key on the exchange
    link = links.get(r.store, out["link_id"])
    assert link["api_key"] == NEW_KEY and link["webhook_secret"] == NEW_SECRET and link["via"] == "connect"
    assert link["dh_workspace"] == {"id": "ws_bmi", "name": "BMI", "slug": "bmi"} and link["primary"] and link["lead_sync"] == "call_tasks"
    raw = r.store.path.read_text()
    assert NEW_KEY not in raw and NEW_SECRET not in raw and NEW_KEY not in res.text
    assert link["last_test"]["ok"] and "BMI" in link["last_test"]["detail"] and link["members"]["rows"]
    assert not pending(r)                                                            # single use
    again = finish(r, state)
    assert again.status_code == 400 and "already used" in again.json()["detail"]
    audit = [a for a in r.store.read("audit") if a["action"] == "calltracker.connect"][-1]
    assert NEW_KEY not in json.dumps(audit) and "BMI" in audit["detail"]
    card = r.c.get("/api/calltracker", headers=r.h["owner"]).json()
    assert card["links"][0]["dh_workspace"]["name"] == "BMI" and card["links"][0]["pairing"]["checked"]


def test_expired_state_is_refused(r):
    state = parse_qs(urlparse(start(r).json()["url"]).query)["state"][0]
    r.store.update("ct_pending", lambda rows: {k: {**v, "expires_at": "2020-01-01T00:00:00+00:00"} for k, v in rows.items()})
    res = finish(r, state)
    assert res.status_code == 400 and "10 minutes" in res.json()["detail"]
    assert not links.links_of(r.store, "main") and not [p for p in r.fake.requests if p[1] == "/api/connect/exchange"]


def test_access_denied_and_exchange_failures_in_plain_words(r):
    state = parse_qs(urlparse(start(r).json()["url"]).query)["state"][0]
    res = finish(r, state, code="", error="access_denied")
    assert res.status_code == 400 and "Cancel or Deny" in res.json()["detail"] and not pending(r)
    state = parse_qs(urlparse(start(r).json()["url"]).query)["state"][0]
    res = finish(r, state, code="wrong-code")
    assert res.status_code == 400 and "did not accept the connection" in res.json()["detail"]
    state = parse_qs(urlparse(start(r).json()["url"]).query)["state"][0]
    r.fake.exchange = (503, {"message": "down"})
    res = finish(r, state)
    assert res.status_code == 400 and "could not finish connecting" in res.json()["detail"]
    assert not links.links_of(r.store, "main")
    assert finish(r, "no-such-state").status_code == 400


def test_only_the_starter_may_finish_and_roles(r):
    assert start(r, who="priya").status_code == 403                                 # a caller
    assert start(r, who="view").status_code == 403                                  # a viewer
    assert start(r, who="adm").status_code == 403                                   # a workspace admin: never main
    own = start(r, who="adm2", ws=r.w2)
    assert own.status_code == 200
    state = parse_qs(urlparse(own.json()["url"]).query)["state"][0]
    assert finish(r, state, who="view").status_code == 403
    assert finish(r, state, who="owner").status_code == 403                        # someone else's pending connect
    assert state in pending(r)                                                       # still there for its starter
    res = finish(r, state, who="adm2")
    assert res.status_code == 200 and links.links_of(r.store, r.w2)[0]["dh_workspace"]["name"] == "BMI"


def test_reconnecting_the_same_dialhalo_workspace_replaces_the_old_link(r):
    first = start(r).json()
    finish(r, parse_qs(urlparse(first["url"]).query)["state"][0])
    links.save(r.store, "main", first["link_id"], {"lead_sync": "all_with_phone"}, "t")
    r.fake.codes["good-code"] = True
    second = start(r).json()
    res = finish(r, parse_qs(urlparse(second["url"]).query)["state"][0])
    assert "replaced" in res.json()["message"]
    left = links.links_of(r.store, "main")
    assert [l["id"] for l in left] == [second["link_id"]] and left[0]["lead_sync"] == "all_with_phone" and left[0]["primary"]


def test_test_shows_the_dialhalo_workspace_and_falls_back_on_older_dialhalo(r):
    r.c.put("/api/calltracker", json={"workspace": "main", "base_url": "https://calls.example.com", "api_key": "ctk_manual_key_123456"},
            headers=r.h["owner"])
    r.fake.self_status = 404                                                         # an older DialHalo: base URL only
    old = r.c.post("/api/calltracker/test", json={"workspace": "main"}, headers=r.h["owner"]).json()
    assert old["ok"] and "DialHalo at https://calls.example.com" in old["message"] and "does not say which workspace" in old["message"]
    r.fake.self_status = 200
    out = r.c.post("/api/calltracker/test", json={"workspace": "main"}, headers=r.h["owner"]).json()
    assert out["ok"] and out["workspace"] == "BMI" and "DialHalo workspace BMI" in out["message"]
    assert links.links_of(r.store, "main")[0]["dh_workspace"]["name"] == "BMI"


def test_disconnect_tells_dialhalo_then_removes_and_404_falls_back(r):
    first = start(r).json()
    finish(r, parse_qs(urlparse(first["url"]).query)["state"][0])
    r.fake.requests.clear()
    res = r.c.delete(f"/api/calltracker?workspace=main&link={first['link_id']}", headers=r.h["owner"])
    assert res.status_code == 200 and "turned the key off" in res.json()["message"] and "BMI" in res.json()["message"]
    assert [(m, p, a) for m, p, d, a in r.fake.requests] == [("DELETE", "/api/v1/connect/self", f"Bearer {NEW_KEY}")]
    assert not links.links_of(r.store, "main")
    r.fake.codes["good-code"] = True
    second = start(r).json()
    finish(r, parse_qs(urlparse(second["url"]).query)["state"][0])
    r.fake.self_status = 404
    res = r.c.delete(f"/api/calltracker?workspace=main&link={second['link_id']}", headers=r.h["owner"])
    assert res.status_code == 200 and "revoke the key in DialHalo" in res.json()["message"] and not links.links_of(r.store, "main")
