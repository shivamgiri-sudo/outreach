"""QA (security): tokens, the role matrix over every route, unauthenticated access, secrets never returned.

Every route is enumerated from the live app, so a route added later is covered without editing this file.
No network: the app runs in-process through TestClient; provider transports are never reached.
"""
import base64
import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import PUBLIC_API_PATHS, PUBLIC_GET_PATHS, create_app
from app.routes_linkedin_runner import PUBLIC_PATHS as RUNNER_PUBLIC_PATHS
from app.routes_auth import needed_role
from tests.export_fakes import SECRETS, seed

ADMIN = "qa-admin-password-01"
USER_PW = "qa-user-password-01"
READ = ("GET", "HEAD", "OPTIONS")
PEER = ("198.51.100.20", 41000)                   # a remote address (not the local proxy)


# ---------- helpers ----------
def make(tmp_path, password=ADMIN, peer=PEER, **kw):
    return TestClient(create_app(data_file=tmp_path / "store.json", admin_password=password, **kw), client=peer)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def login(c, password=ADMIN, email=""):
    r = c.post("/api/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def team(c):
    """owner (admin password), plus a caller and a viewer login; returns their tokens."""
    owner = login(c)
    for email, role in (("ritu.caller@qa-team.example", "caller"), ("vikram.viewer@qa-team.example", "viewer")):
        r = c.post("/api/users", json={"email": email, "name": email.split("@")[0], "role": role, "password": USER_PW},
                   headers=bearer(owner))
        assert r.status_code == 200, r.text
    return {"owner": owner, "caller": login(c, USER_PW, "ritu.caller@qa-team.example"),
            "viewer": login(c, USER_PW, "vikram.viewer@qa-team.example")}


def all_routes(app):
    """(method, full path template) for every HTTP route, including routers FastAPI includes lazily."""
    def walk(routes, prefix=""):
        for r in routes:
            if hasattr(r, "original_router"):
                yield from walk(r.original_router.routes, prefix + (getattr(getattr(r, "include_context", None), "prefix", "") or ""))
            elif getattr(r, "methods", None):
                for m in sorted(r.methods):
                    yield m, prefix + r.path
    return sorted(set(walk(app.routes)))


def concrete(path):
    return re.sub(r"\{dataset\}", "leads", re.sub(r"\{(?!dataset)[^}]+\}", "qa-x", path))


def call(c, method, path, token=None, **kw):
    headers = {**(bearer(token) if token else {}), **kw.pop("headers", {})}
    return c.request(method, concrete(path), headers=headers, **({"json": {}} if method not in READ else {}), **kw)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def claims_of(token):
    body = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


# ---------- the route list itself ----------
def test_route_enumeration_finds_the_whole_api(tmp_path):
    routes = all_routes(make(tmp_path).app)
    writes = [r for r in routes if r[0] not in READ]
    assert len(routes) > 120 and len(writes) > 80                    # sanity: lazily included routers are walked
    assert ("POST", "/api/leads/{lead_id}/erase") in routes and ("POST", "/api/campaigns/{campaign_id}/run") in routes


# ---------- role matrix over every route ----------
def test_viewer_gets_403_on_every_mutating_api_route(tmp_path):
    c = make(tmp_path)
    viewer = team(c)["viewer"]
    leaked = []
    for method, path in all_routes(c.app):
        if method in READ or not path.startswith("/api/") or path in ("/api/login", "/api/logout") or path in RUNNER_PUBLIC_PATHS:
            continue                          # office-runner pairing: no session at all, it checks its one-time code itself
        r = call(c, method, path, viewer)
        if r.status_code != 403:
            leaked.append((method, path, r.status_code))
    assert leaked == []
    assert c.post("/api/logout", headers=bearer(viewer)).status_code == 200       # the one write a viewer may do


def test_caller_gets_403_on_every_owner_route(tmp_path):
    c = make(tmp_path)
    caller = team(c)["caller"]
    leaked = []
    for method, path in all_routes(c.app):
        if path.startswith("/api/") and needed_role(method, path) == "owner":
            r = call(c, method, path, caller)
            if r.status_code != 403:
                leaked.append((method, path, r.status_code))
    assert leaked == []


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_callers_and_viewers_cannot_download_any_csv_or_json_export(tmp_path, method):
    """README and PRD: callers get "no ... CSV exports"; viewers are read-only and below callers. Every dataset, including
    the ones the export module did not mark owner-only (tasks, activity, meetings, linkedin_queue carry names, emails,
    join links and LinkedIn profiles)."""
    c = make(tmp_path)
    seed(c.app.state.store)                       # first: it rewrites settings (server secret)
    tokens = team(c)
    datasets = [d["dataset"] for d in c.get("/api/export", headers=bearer(tokens["owner"])).json()]
    assert {"tasks", "activity", "meetings", "linkedin_queue"} <= set(datasets)
    for dataset in datasets:
        for suffix in ("csv", "json") if method == "GET" else ("csv",):
            url = f"/api/export/{dataset}.{suffix}"
            assert c.request(method, url, headers=bearer(tokens["owner"]), json={} if method == "POST" else None).status_code in (200, 400)   # 400: a filter is required
            for who in ("caller", "viewer"):
                r = c.request(method, url, headers=bearer(tokens[who]), json={} if method == "POST" else None)
                assert r.status_code == 403, (who, url, r.status_code)
                assert "@" not in r.text and "text/csv" not in r.headers.get("content-type", "")


def test_owner_routes_listed_in_the_docs_are_refused_to_callers(tmp_path):
    """PRD 'Login and roles': settings, integrations, live switches, sending runs, users, DPDP, backups."""
    c = make(tmp_path)
    t = team(c)
    lead = c.post("/api/leads/import", json={"csv": "company,email\nQA Foods Pvt. Ltd.,hr@qa-foods.example\n"},
                  headers=bearer(t["caller"])).json()
    lead_id = c.get("/api/leads", headers=bearer(t["caller"])).json()[0]["id"]
    for method, path, body in (("put", "/api/settings", {"daily_cap": 5}), ("put", "/api/channels/live", {"live_sms": True}),
                               ("post", "/api/sequences/run", {}), ("post", "/api/sending/resume", {}),
                               ("get", f"/api/leads/{lead_id}/export", None), ("post", f"/api/leads/{lead_id}/erase", {"confirm": True}),
                               ("get", "/api/backup", None), ("get", "/api/compliance/consent-ledger", None),
                               ("post", "/api/users", {"email": "x@qa.example", "role": "owner", "password": "p" * 12})):
        r = getattr(c, method)(path, headers=bearer(t["caller"]), **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (path, r.status_code)
    assert lead["created"] == 1 and c.get(f"/api/leads/{lead_id}", headers=bearer(t["owner"])).status_code == 200


def test_role_changes_and_removal_apply_to_live_tokens_immediately(tmp_path):
    c = make(tmp_path)
    t = team(c)
    caller = t["caller"]
    assert c.post("/api/tasks", json={"title": "Call back Mr. SHARMA"}, headers=bearer(caller)).status_code == 200
    c.put("/api/users/ritu.caller@qa-team.example", json={"role": "viewer"}, headers=bearer(t["owner"]))
    assert c.post("/api/tasks", json={"title": "again"}, headers=bearer(caller)).status_code == 403   # demoted, same token
    c.put("/api/users/ritu.caller@qa-team.example", json={"role": "owner"}, headers=bearer(t["owner"]))
    assert c.get("/api/users", headers=bearer(caller)).status_code == 200                             # promoted, same token
    c.delete("/api/users/ritu.caller@qa-team.example", headers=bearer(t["owner"]))
    assert c.get("/api/leads", headers=bearer(caller)).status_code == 401                             # removed: signed out


def test_a_user_cannot_escalate_through_their_own_record(tmp_path):
    c = make(tmp_path)
    t = team(c)
    for who in ("caller", "viewer"):
        for method, path, body in (("put", "/api/users/ritu.caller@qa-team.example", {"role": "owner"}),
                                   ("post", "/api/users", {"email": "evil@qa.example", "role": "owner", "password": "p" * 12}),
                                   ("delete", "/api/users/vikram.viewer@qa-team.example", None)):
            r = getattr(c, method)(path, headers=bearer(t[who]), **({"json": body} if body is not None else {}))
            assert r.status_code == 403, (who, path)
    roles = {u["email"]: u["role"] for u in c.get("/api/users", headers=bearer(t["owner"])).json()}
    assert roles == {"ritu.caller@qa-team.example": "caller", "vikram.viewer@qa-team.example": "viewer"}


# ---------- unauthenticated access ----------
def test_every_api_route_needs_a_login_except_the_documented_public_ones(tmp_path):
    c = make(tmp_path)
    team(c)
    open_routes = []
    for method, path in all_routes(c.app):
        if not path.startswith("/api/") or method == "OPTIONS" or path in PUBLIC_API_PATHS or (method == "GET" and path in PUBLIC_GET_PATHS):
            continue
        if path in RUNNER_PUBLIC_PATHS:
            continue                          # checked below: each refuses without its one-time code / runner token
        for headers in ({}, {"Authorization": "Bearer "}, {"Authorization": "Basic YWRtaW46YWRtaW4="}, {"X-Webhook-Secret": ""}):
            r = call(c, method, path, headers=headers)
            if r.status_code != 401:
                open_routes.append((method, path, headers, r.status_code))
    assert open_routes == []
    assert set(PUBLIC_API_PATHS) == {"/api/login", "/api/auth/status"}
    assert set(PUBLIC_GET_PATHS) == {"/api/brand/public", "/api/brand/logo"}      # read-only brand for sign-in page and mail apps
    # Office runner (LinkedIn on an office computer): pairing, installers and bundle need the 15-minute code or the runner token.
    assert set(RUNNER_PUBLIC_PATHS) == {"/api/linkedin-runner/pair", "/api/linkedin-runner/install.ps1", "/api/linkedin-runner/install.sh",
                                        "/api/linkedin-runner/bundle.tar.gz", "/api/linkedin-runner/bundle.zip"}
    assert c.post("/api/linkedin-runner/pair", json={"code": "ZZZZZZZZ"}).status_code in (400, 403)
    assert c.get("/api/linkedin-runner/bundle.tar.gz").status_code == 403 and c.get("/api/linkedin-runner/bundle.zip").status_code == 403
    for kind in ("ps1", "sh"):
        text = c.get(f"/api/linkedin-runner/install.{kind}").text
        assert "expired" in text and "npm" not in text and "mcnr_" not in text
    assert c.get("/api/auth/status").json() == {"auth_required": True}


@pytest.mark.parametrize("path", ["/api//leads", "/api/leads/", "/API/leads", "/api/%6ceads", "/api/./leads", "/api/leads%2F",
                                  "/api/leads?token=x", "/api/leads;x=1", "//api/leads"])
def test_path_tricks_do_not_reach_data_without_a_login(tmp_path, path):
    c = make(tmp_path)
    owner = team(c)["owner"]
    c.post("/api/leads/import", json={"csv": "company,email\nQA Retail,hr@qa-retail.example\n"}, headers=bearer(owner))
    r = c.get(path)
    assert "hr@qa-retail.example" not in r.text and r.status_code in (401, 404, 405, 307)


def test_no_password_install_refuses_remote_and_proxied_callers(tmp_path):
    remote = make(tmp_path, password="", peer=("203.0.113.50", 5000))
    assert remote.get("/api/leads").status_code == 403
    local = make(tmp_path, password="", peer=("127.0.0.1", 5000))
    assert local.get("/api/leads").status_code == 200
    for headers in ({"X-Forwarded-For": "203.0.113.50"}, {"Forwarded": "for=203.0.113.50"}, {"X-Real-IP": "203.0.113.50"},
                    {"Host": "outreach.qa-company.example"}):
        assert local.get("/api/leads", headers=headers).status_code == 403, headers


# ---------- tokens ----------
def test_forged_and_malformed_tokens_are_refused(tmp_path):
    c = make(tmp_path)
    t = team(c)
    token = t["viewer"]
    prefix, body, sig = token.split(".")
    claims = claims_of(token)
    forged = {
        "owner subject, old signature": f"{prefix}.{b64(json.dumps({**claims, 'sub': 'admin'}).encode())}.{sig}",
        "v1 prefix": f"v1.{body}.{sig}",
        "unsigned": f"{prefix}.{body}.",
        "alg none jwt": "eyJhbGciOiJub25lIn0." + b64(json.dumps({"sub": "admin"}).encode()) + ".",
        "four parts": token + ".x",
        "body not json": f"{prefix}.{b64(b'nope')}.{sig}",
        "exp as string": f"{prefix}.{b64(json.dumps({**claims, 'exp': 'never'}).encode())}.{sig}",
        "huge": "v2." + "A" * 20000 + ".x",
        "lowercase scheme": None,
    }
    for name, bad in forged.items():
        headers = {"Authorization": f"bearer {token}"} if bad is None else bearer(bad)
        assert c.get("/api/leads", headers=headers).status_code == 401, name
    assert c.get("/api/leads", headers={"Authorization": f"Bearer {token[:-1]}é".encode("utf-8")}).status_code == 401
    assert c.get(f"/api/leads?token={token}").status_code == 401                 # tokens only travel in the header
    assert c.get("/api/leads", headers=bearer(token)).status_code == 200


def test_a_validly_signed_token_with_a_wrong_password_fingerprint_or_unknown_subject_is_refused(tmp_path):
    c = make(tmp_path)
    store = c.app.state.store
    team(c)
    for principal in ({"sub": "admin", "pf": "0" * 20}, {"sub": "user:ghost@qa-team.example", "pf": "0" * 20},
                      {"sub": "local", "pf": "0" * 20}):
        assert c.get("/api/leads", headers=bearer(auth.issue_token(store, principal))).status_code == 401, principal


def test_future_dated_tokens_beyond_clock_skew_are_refused(tmp_path):
    c = make(tmp_path)
    store = c.app.state.store
    principal = auth.subject_principal(store, ADMIN, auth.ADMIN_SUBJECT)
    assert c.get("/api/leads", headers=bearer(auth.issue_token(store, principal, now=time.time() + 30))).status_code == 200
    assert c.get("/api/leads", headers=bearer(auth.issue_token(store, principal, now=time.time() + 600))).status_code == 401


def test_three_day_shift_with_renewals_then_logout_kills_the_whole_chain(tmp_path):
    """Time travel over three days with auth's now= parameter: a token renewed every ~7 h stays valid; an unrenewed one
    dies at 12 h; signing out from the newest copy revokes every older copy of that sign-in, but not other sign-ins."""
    c = make(tmp_path)
    store = c.app.state.store
    principal = auth.subject_principal(store, ADMIN, auth.ADMIN_SUBJECT)
    t0 = time.time()
    first = auth.issue_token(store, principal, now=t0)
    other_session = auth.issue_token(store, principal, now=t0)
    chain, current, moment = [first], first, t0
    while moment < t0 + 3 * 86400:
        moment += 7 * 3600
        user = auth.authenticate(store, ADMIN, current, now=moment)
        assert user is not None, (moment - t0) / 3600
        renewed = auth.renewal(store, user, now=moment)
        assert renewed and claims_of(renewed)["sid"] == claims_of(first)["sid"]
        chain.append(renewed)
        current = renewed
    assert auth.authenticate(store, ADMIN, first, now=t0 + 13 * 3600) is None            # never renewed: expired
    auth.revoke(store, auth.authenticate(store, ADMIN, current, now=moment)["claims"], now=moment)
    assert all(auth.authenticate(store, ADMIN, tok, now=moment + 60) is None for tok in chain)
    assert auth.authenticate(store, ADMIN, other_session, now=t0 + 60) is not None       # a different sign-in is untouched


def test_logout_response_never_hands_back_a_renewed_token(tmp_path):
    c = make(tmp_path)
    store = c.app.state.store
    principal = auth.subject_principal(store, ADMIN, auth.ADMIN_SUBJECT)
    ageing = auth.issue_token(store, principal, now=time.time() - 8 * 3600)
    r = c.post("/api/logout", headers=bearer(ageing))
    assert r.status_code == 200 and "x-renewed-token" not in r.headers
    assert c.get("/api/leads", headers=bearer(ageing)).status_code == 401


def test_password_change_signs_out_only_that_person(tmp_path):
    c = make(tmp_path)
    t = team(c)
    owner, caller, viewer = t["owner"], t["caller"], t["viewer"]
    r = c.put("/api/users/ritu.caller@qa-team.example", json={"password": "a-brand-new-password-2"}, headers=bearer(owner))
    assert r.status_code == 200 and r.json()["password_changed_at"]
    assert c.get("/api/leads", headers=bearer(caller)).status_code == 401
    assert c.get("/api/leads", headers=bearer(viewer)).status_code == 200 and c.get("/api/leads", headers=bearer(owner)).status_code == 200
    assert c.post("/api/login", json={"email": "ritu.caller@qa-team.example", "password": USER_PW}).status_code == 401
    assert login(c, "a-brand-new-password-2", "RITU.CALLER@qa-team.example ")
    c.put("/api/users/ritu.caller@qa-team.example", json={"name": "Ritu S."}, headers=bearer(owner))   # a rename keeps sessions
    assert c.get("/api/leads", headers=bearer(viewer)).status_code == 200


def test_login_refuses_oversized_input_and_keeps_messages_generic(tmp_path):
    team(make(tmp_path))
    c = make(tmp_path, peer=("198.51.100.77", 1))           # its own login budget (5 a minute per address)
    assert c.post("/api/login", json={"password": "x" * 1001}).status_code == 400
    assert c.post("/api/login", json={"email": "a" * 400 + "@qa.example", "password": "x"}).status_code == 400
    unknown = c.post("/api/login", json={"email": "nobody@qa-team.example", "password": "wrong-password-1"})
    wrong = c.post("/api/login", json={"email": "ritu.caller@qa-team.example", "password": "wrong-password-1"})
    assert unknown.status_code == wrong.status_code == 401 and unknown.json() == wrong.json()   # no user enumeration


# ---------- secrets never come back ----------
def test_no_get_route_returns_a_saved_secret(tmp_path):
    """Seed secrets in every place the app keeps them, then GET every route (path ids filled with real ids)."""
    c = make(tmp_path)
    store = c.app.state.store
    seed(store)                                   # first: it rewrites settings (server secret)
    owner = team(c)["owner"]
    hashes = [u["password_hash"] for u in auth.users(store)]
    secret_values = [*SECRETS[:7], *hashes, store.read("settings")["server_secret"]]
    ids = {"lead_id": "l1", "campaign_id": "c1", "dataset": "tasks"}
    checked = 0
    for method, path in all_routes(c.app):
        if method != "GET" or "/api/webhooks/" in path or "/oauth/" in path:
            continue
        url = re.sub(r"\{(\w+)\}", lambda m: ids.get(m.group(1), "qa-x"), path)
        text = c.get(url, headers=bearer(owner)).text
        checked += 1
        assert not [s for s in secret_values if s in text], url
        assert "password_hash" not in text, url
    assert checked > 60
