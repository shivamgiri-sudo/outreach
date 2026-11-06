"""Auth v2: server-secret tokens, renewal, logout, password binding, users and roles, login limits."""
import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.main import create_app

ADMIN = "admin-pass-123"


def client(tmp_path, password=ADMIN, ip=("203.0.113.9", 50000)):
    return TestClient(create_app(data_file=tmp_path / "d.json", admin_password=password), client=ip)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def login(c, password=ADMIN, email=""):
    r = c.post("/api/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def add_user(c, owner_token, email, role, password="long-password-1"):
    r = c.post("/api/users", json={"email": email, "name": email.split("@")[0], "role": role, "password": password},
               headers=bearer(owner_token))
    assert r.status_code == 200, r.text
    return r.json()


# ---------- tokens ----------
def test_token_is_signed_with_the_server_secret_not_the_password(tmp_path):
    c = client(tmp_path)
    token = login(c)
    prefix, body, sig = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    assert prefix == "v2" and {"sub", "sid", "jti", "iat", "exp", "pf"} <= set(claims)
    assert claims["exp"] - claims["iat"] == 12 * 3600
    # changing the server secret (a new install) makes every token useless, whatever the password
    c.app.state.store.update("settings", lambda s: {**s, "server_secret": "f" * 64})
    assert c.get("/api/leads", headers=bearer(token)).status_code == 401


def test_tampered_tokens_are_refused(tmp_path):
    c = client(tmp_path)
    token = login(c)
    prefix, body, sig = token.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    forged_body = base64.urlsafe_b64encode(json.dumps({**claims, "exp": claims["exp"] + 999999}).encode()).decode().rstrip("=")
    for bad in (f"{prefix}.{forged_body}.{sig}", f"{prefix}.{body}.{sig[:-2]}AA", token + "x", "forged.token", ""):
        assert c.get("/api/leads", headers=bearer(bad)).status_code == 401
    assert c.get("/api/leads", headers=bearer(token)).status_code == 200


def test_expired_token_is_refused(tmp_path):
    c = client(tmp_path)
    store = c.app.state.store
    principal = auth.subject_principal(store, ADMIN, auth.ADMIN_SUBJECT)
    old = auth.issue_token(store, principal, now=time.time() - 13 * 3600)
    assert c.get("/api/leads", headers=bearer(old)).status_code == 401


def test_sliding_renewal_after_half_the_lifetime(tmp_path):
    c = client(tmp_path)
    store = c.app.state.store
    principal = auth.subject_principal(store, ADMIN, auth.ADMIN_SUBJECT)
    fresh = c.get("/api/leads", headers=bearer(login(c)))
    assert fresh.status_code == 200 and "x-renewed-token" not in fresh.headers
    ageing = auth.issue_token(store, principal, now=time.time() - 7 * 3600)
    r = c.get("/api/leads", headers=bearer(ageing))
    renewed = r.headers.get("x-renewed-token")
    assert r.status_code == 200 and renewed and renewed != ageing
    assert c.get("/api/leads", headers=bearer(renewed)).status_code == 200
    assert "X-Renewed-Token" in c.app.user_middleware[-1].kwargs.get("expose_headers", [])   # CORS lets the UI read it


def test_logout_revokes_the_token_and_its_renewed_copies(tmp_path):
    c = client(tmp_path)
    store = c.app.state.store
    principal = auth.subject_principal(store, ADMIN, auth.ADMIN_SUBJECT)
    ageing = auth.issue_token(store, principal, now=time.time() - 7 * 3600)
    renewed = c.get("/api/leads", headers=bearer(ageing)).headers["x-renewed-token"]
    assert c.post("/api/logout", headers=bearer(renewed)).json() == {"ok": True}
    assert c.get("/api/leads", headers=bearer(renewed)).status_code == 401
    assert c.get("/api/leads", headers=bearer(ageing)).status_code == 401       # same session
    other = login(c)                                                           # a new sign-in still works
    assert c.get("/api/leads", headers=bearer(other)).status_code == 200


def test_revoked_list_prunes_expired_entries(tmp_path):
    c = client(tmp_path)
    store = c.app.state.store
    store.write("revoked_tokens", [{"id": "jti:old", "exp": 10}, {"id": "sid:old", "exp": 10}])
    token = login(c)
    c.post("/api/logout", headers=bearer(token))
    ids = [r["id"] for r in store.read("revoked_tokens")]
    assert "jti:old" not in ids and len(ids) == 2


def test_changing_the_admin_password_signs_everyone_out(tmp_path):
    first = client(tmp_path, password="first-password-1")
    token = login(first, "first-password-1")
    assert first.get("/api/leads", headers=bearer(token)).status_code == 200
    second = client(tmp_path, password="second-password-2")         # restarted with a new OUTREACH_ADMIN_PASSWORD
    assert second.get("/api/leads", headers=bearer(token)).status_code == 401


def test_no_api_returns_the_server_secret_or_password_hashes(tmp_path):
    c = client(tmp_path)
    owner = login(c)
    add_user(c, owner, "priya@example.com", "caller")
    settings = c.app.state.store.read("settings")
    secret, pw_hash = settings["server_secret"], settings["users"][0]["password_hash"]
    for path in ("/api/settings", "/api/offer", "/api/growth", "/api/channels", "/api/users", "/api/me", "/api/integrations"):
        text = c.get(path, headers=bearer(owner)).text
        assert secret not in text and pw_hash not in text and "password_hash" not in text, path


# ---------- users and roles ----------
def test_users_log_in_with_email_and_get_their_role(tmp_path):
    c = client(tmp_path)
    owner = login(c)
    user = add_user(c, owner, "Priya@Example.com", "caller")
    assert user == {"email": "priya@example.com", "name": "Priya", "role": "caller", "created_at": user["created_at"],
                    "password_changed_at": None, "can_edit_contacts": True, "workspaces": []}
    r = c.post("/api/login", json={"email": "priya@example.com", "password": "long-password-1"})
    assert r.status_code == 200 and r.json()["role"] == "caller"
    me = c.get("/api/me", headers=bearer(r.json()["token"])).json()
    assert me["role"] == "caller" and me["email"] == "priya@example.com"
    assert c.post("/api/login", json={"email": "priya@example.com", "password": "wrong-password"}).status_code == 401
    assert c.post("/api/login", json={"email": "nobody@example.com", "password": "long-password-1"}).status_code == 401
    assert c.get("/api/me", headers=bearer(owner)).json()["role"] == "owner"     # admin password = owner


def test_user_rules_short_password_duplicates_and_bad_role(tmp_path):
    c = client(tmp_path)
    owner = login(c)
    assert c.post("/api/users", json={"email": "a@example.com", "role": "caller", "password": "short"},
                  headers=bearer(owner)).status_code == 400
    assert c.post("/api/users", json={"email": "a@example.com", "role": "boss", "password": "long-password-1"},
                  headers=bearer(owner)).status_code == 400
    add_user(c, owner, "a@example.com", "viewer")
    assert c.post("/api/users", json={"email": "a@example.com", "role": "caller", "password": "long-password-1"},
                  headers=bearer(owner)).status_code == 409


def test_password_reset_role_change_and_removal_take_effect_at_once(tmp_path):
    c = client(tmp_path)
    owner = login(c)
    add_user(c, owner, "sam@example.com", "caller")
    token = login(c, "long-password-1", "sam@example.com")
    assert c.post("/api/leads/import", json={"csv": "company,email\nA,a@a.in\n"}, headers=bearer(token)).status_code == 200
    c.put("/api/users/sam@example.com", json={"role": "viewer"}, headers=bearer(owner))
    assert c.post("/api/leads/import", json={"csv": "company,email\nB,b@b.in\n"}, headers=bearer(token)).status_code == 403
    assert c.put("/api/users/sam@example.com", json={"password": "tiny"}, headers=bearer(owner)).status_code == 400
    c.put("/api/users/sam@example.com", json={"password": "another-long-pass"}, headers=bearer(owner))
    assert c.get("/api/leads", headers=bearer(token)).status_code == 401          # old password's tokens stop working
    newer = login(c, "another-long-pass", "sam@example.com")
    c.delete("/api/users/sam@example.com", headers=bearer(owner))
    assert c.get("/api/leads", headers=bearer(newer)).status_code == 401


def test_without_admin_password_the_first_login_must_be_an_owner_and_one_owner_stays(tmp_path):
    local = TestClient(create_app(data_file=tmp_path / "d.json", admin_password=""))
    assert local.post("/api/users", json={"email": "c@example.com", "role": "caller", "password": "long-password-1"}).status_code == 400
    assert local.post("/api/users", json={"email": "o@example.com", "role": "owner", "password": "long-password-1"}).status_code == 200
    assert local.get("/api/auth/status").json() == {"auth_required": True}       # a login now exists, so sign-in is on
    assert local.get("/api/leads").status_code == 401
    token = login(local, "long-password-1", "o@example.com")
    assert local.delete("/api/users/o@example.com", headers=bearer(token)).status_code == 400
    assert local.put("/api/users/o@example.com", json={"role": "viewer"}, headers=bearer(token)).status_code == 400


@pytest.mark.parametrize("method,path,body,caller_ok", [
    ("put", "/api/settings", {"daily_cap": 20}, False),
    ("put", "/api/offer", {"trial_days": 20}, False),
    ("put", "/api/channels/live", {"live_sms": True}, False),
    ("put", "/api/integrations/apollo", {"mode": "manual", "values": {}}, False),
    ("post", "/api/workflows/starter", None, False),
    ("post", "/api/sequences/run", {}, False),
    ("get", "/api/enrich/apollo-export", None, False),
    ("get", "/api/channels/webhooks", None, False),
    ("get", "/api/users", None, False),
    ("post", "/api/leads/import", {"csv": "company,email\nA,a@a.in\n"}, True),
    ("post", "/api/tasks", {"title": "Call back"}, True),
])
def test_roles_matrix(tmp_path, method, path, body, caller_ok):
    c = client(tmp_path)
    owner = login(c)
    add_user(c, owner, "caller@example.com", "caller")
    add_user(c, owner, "viewer@example.com", "viewer")
    caller = login(c, "long-password-1", "caller@example.com")
    viewer = login(c, "long-password-1", "viewer@example.com")
    send = lambda token: getattr(c, method)(path, headers=bearer(token), **({"json": body} if body is not None else {}))
    assert send(owner).status_code < 400
    assert (send(caller).status_code < 400) == caller_ok
    assert send(viewer).status_code == 403           # every row is a change or owner-only


def test_viewers_can_read_and_sign_out_but_not_write(tmp_path):
    c = client(tmp_path)
    owner = login(c)
    add_user(c, owner, "v@example.com", "viewer")
    viewer = login(c, "long-password-1", "v@example.com")
    assert c.get("/api/leads", headers=bearer(viewer)).status_code == 200
    assert c.get("/api/dashboard", headers=bearer(viewer)).status_code == 200
    r = c.post("/api/stories", json={"title": "x"}, headers=bearer(viewer))
    assert r.status_code == 403 and "viewer" in r.json()["detail"]
    assert c.post("/api/logout", headers=bearer(viewer)).status_code == 200


# ---------- login limits ----------
def test_login_lockout_uses_the_forwarded_address_only_from_the_local_proxy(tmp_path):
    app = create_app(data_file=tmp_path / "d.json", admin_password=ADMIN)
    proxy = TestClient(app, client=("127.0.0.1", 1))
    for _ in range(5):
        proxy.post("/api/login", json={"password": "wrong"}, headers={"X-Forwarded-For": "203.0.113.5"})
    assert proxy.post("/api/login", json={"password": ADMIN}, headers={"X-Forwarded-For": "203.0.113.5"}).status_code == 429
    assert proxy.post("/api/login", json={"password": ADMIN}, headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 200
    # a remote caller cannot dodge the lockout by inventing X-Forwarded-For values
    remote = TestClient(app, client=("192.0.2.44", 1))
    for i in range(5):
        remote.post("/api/login", json={"password": "wrong"}, headers={"X-Forwarded-For": f"10.0.0.{i}"})
    assert remote.post("/api/login", json={"password": ADMIN}, headers={"X-Forwarded-For": "10.9.9.9"}).status_code == 429


def test_login_limiter_memory_is_bounded():
    limiter = auth.LoginLimiter(max_callers=3)
    for i in range(50):
        limiter.record_failure(f"198.51.100.{i}")
    assert len(limiter) == 3 and limiter.locked("198.51.100.49") is False


def test_scrypt_hashes_are_salted_and_checked_in_constant_time_shape():
    one, two = auth.hash_password("long-password-1"), auth.hash_password("long-password-1")
    assert one != two and one.startswith("scrypt$16384$8$1$")
    assert auth.check_password(one, "long-password-1") and not auth.check_password(one, "long-password-2")
    assert not auth.check_password("", "anything") and not auth.check_password("garbage", "x")
