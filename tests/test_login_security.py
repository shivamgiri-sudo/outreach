"""Login history, the failed sign-in alert, and two-step login (TOTP, recovery codes, owner reset, require for everyone)."""
import hashlib
import itertools
import time

import pytest
from fastapi.testclient import TestClient

from app import login_log, totp
from app.main import create_app

ADMIN = "admin-pass-123"
PW = "long-password-1"
_ips = itertools.count(10)


def fresh(app):
    """A client from a new address, so the per-address sign-in limits of one test step never spill into the next."""
    return TestClient(app, client=(f"198.51.100.{next(_ips) % 250}", 50000), headers={"User-Agent": "pytest-browser/1.0"})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def app(tmp_path):
    return create_app(data_file=tmp_path / "d.json", admin_password=ADMIN)


def login(app, email="", password=ADMIN):
    return fresh(app).post("/api/login", json={"email": email, "password": password})


def owner_token(app):
    return login(app).json()["token"]


def add_user(app, owner, email, role="caller", workspaces=()):
    r = fresh(app).post("/api/users", json={"email": email, "name": email.split("@")[0], "role": role, "password": PW,
                                            "workspaces": list(workspaces)}, headers=bearer(owner))
    assert r.status_code == 200, r.text


def rows(app):
    return app.state.store.read("login_history")


# ---------- TOTP math ----------
@pytest.mark.parametrize("t,expected", [(59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
                                        (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130")])
def test_totp_matches_the_rfc_6238_sha1_vectors(t, expected):
    assert totp.hotp(b"12345678901234567890", t // 30, digits=8, digest=hashlib.sha1) == expected


def test_totp_verify_accepts_one_step_of_drift_and_never_the_same_step_twice():
    secret = totp.new_secret()
    now = 1_700_000_000
    code = totp.code_at(secret, now)
    step = totp.verify(secret, code, now)
    assert step == now // 30
    assert totp.verify(secret, totp.code_at(secret, now - 30), now) == step - 1
    assert totp.verify(secret, totp.code_at(secret, now - 90), now) is None
    assert totp.verify(secret, code, now, last_step=step) is None          # replay refused
    assert totp.verify(secret, "12345", now) is None and totp.verify(secret, "abcdef", now) is None
    uri = totp.otpauth_uri(secret, "a@example.com", "Outreach OS")
    assert uri.startswith("otpauth://totp/Outreach%20OS:a@example.com?secret=" + secret) and "digits=6" in uri and "period=30" in uri


# ---------- login history ----------
def test_history_records_success_failure_logout_with_ip_and_agent(app):
    owner = owner_token(app)
    add_user(app, owner, "ann@example.com")
    assert login(app, "ann@example.com", "wrong-password-x").status_code == 401
    assert login(app, "nobody@example.com", "wrong-password-x").status_code == 401
    token = login(app, "ann@example.com", PW).json()["token"]
    assert fresh(app).post("/api/logout", headers=bearer(token)).status_code == 200
    got = [(r["event"], r["login"], r["result"], r["reason"]) for r in rows(app)]
    assert got == [("login", "admin password", "success", ""), ("login", "ann@example.com", "failure", "wrong password"),
                   ("login", "nobody@example.com", "failure", "no login with this email"), ("login", "ann@example.com", "success", ""),
                   ("logout", "ann@example.com", "success", "")]
    r = rows(app)[1]
    assert r["ip"].startswith("198.51.100.") and r["agent"] == "pytest-browser/1.0" and r["two_step"] is False and r["at"].endswith("+00:00")
    listed = fresh(app).get("/api/security/logins?result=failure", headers=bearer(owner)).json()
    assert listed["total"] == 2 and listed["items"][0]["login"] == "nobody@example.com"
    assert fresh(app).get("/api/security/logins?person=ann", headers=bearer(owner)).json()["total"] == 3


def test_history_is_capped(app, monkeypatch):
    monkeypatch.setattr(login_log, "MAX_ROWS", 3)
    for _ in range(5):
        login_log.record(app.state.store, None, "login", "x@example.com", "failure", "test")
    assert len(rows(app)) == 3


def test_password_reset_is_logged(app):
    owner = owner_token(app)
    add_user(app, owner, "ann@example.com")
    assert fresh(app).post("/api/password/reset", json={"token": "not-a-real-token-123", "password": "new-password-123"}).status_code == 400
    assert rows(app)[-1]["event"] == "password_reset" and rows(app)[-1]["result"] == "failure"


def test_workspace_admin_sees_only_its_own_workspaces_logins_and_callers_see_none(app):
    owner = owner_token(app)
    c = fresh(app)
    w1 = c.post("/api/workspaces", json={"name": "Client One"}, headers=bearer(owner)).json()["id"]
    w2 = c.post("/api/workspaces", json={"name": "Client Two"}, headers=bearer(owner)).json()["id"]
    add_user(app, owner, "boss@example.com", "admin", [w1])
    add_user(app, owner, "one@example.com", "caller", [w1])
    add_user(app, owner, "two@example.com", "caller", [w2])
    caller = login(app, "one@example.com", PW).json()["token"]
    login(app, "two@example.com", PW)
    login(app, "two@example.com", "wrong-password-x")
    boss = login(app, "boss@example.com", PW).json()["token"]
    seen = fresh(app).get("/api/security/logins", headers=bearer(boss))
    assert seen.status_code == 200, seen.text
    assert {r["login"] for r in seen.json()["items"]} == {"one@example.com", "boss@example.com"}
    assert set(seen.json()["people"]) == {"one@example.com", "boss@example.com"}
    assert fresh(app).get("/api/security/logins", headers=bearer(caller)).status_code == 403
    every = fresh(app).get("/api/security/logins", headers=bearer(owner)).json()
    assert {"admin password", "two@example.com"} <= {r["login"] for r in every["items"]}
    # the owner-only switches stay owner-only for a workspace admin
    assert fresh(app).get("/api/security/settings", headers=bearer(boss)).status_code == 403
    assert fresh(app).put("/api/security/settings", json={"require_two_step": True}, headers=bearer(boss)).status_code == 403


# ---------- the failed sign-in alert ----------
def test_five_failures_in_a_row_alert_the_owners_once_per_burst(app):
    owner = owner_token(app)
    add_user(app, owner, "own@example.com", "owner")
    add_user(app, owner, "ann@example.com")
    store = app.state.store
    alerts = lambda: [n for n in store.read("notifications") if n.get("kind") == "security:failed_logins"]
    for i in range(4):
        assert login(app, "ann@example.com", f"wrong-password-{i}").status_code == 401
    assert alerts() == []
    login(app, "ann@example.com", "wrong-password-5")
    assert len(alerts()) == 1 and alerts()[0]["to"] == "own" and "ann@example.com" in alerts()[0]["title"]
    assert any(m["to"] == "own@example.com" and "failed sign-ins" in m["subject"] for m in store.read("transactional_outbox"))
    for i in range(3):                                      # the same burst goes on: no second alert
        login(app, "ann@example.com", f"wrong-again-{i}")
    assert len(alerts()) == 1
    login(app, "ann@example.com", PW)                       # a success ends the burst; a new one alerts again
    for i in range(5):
        login(app, "ann@example.com", f"wrong-later-{i}")
    assert len(alerts()) == 2
    # a caller's bell never shows them
    caller = login(app, "ann@example.com", PW).json()["token"]
    assert not [n for n in fresh(app).get("/api/notifications", headers=bearer(caller)).json() if n.get("kind", "").startswith("security:")]
    assert [n for n in fresh(app).get("/api/notifications", headers=bearer(owner)).json() if n.get("kind", "").startswith("security:")]


def test_failures_spread_over_more_than_fifteen_minutes_do_not_alert(app):
    from datetime import datetime, timedelta, timezone
    owner = owner_token(app)
    add_user(app, owner, "ann@example.com")
    start = datetime.now(timezone.utc) - timedelta(minutes=40)
    for i in range(5):
        login_log.record(app.state.store, None, "login", "ann@example.com", "failure", "wrong password", now=start + timedelta(minutes=i * 8))
    assert not [n for n in app.state.store.read("notifications") if n.get("kind") == "security:failed_logins"]


def test_the_login_limit_still_applies(app):
    c = fresh(app)
    codes = [c.post("/api/login", json={"password": f"wrong-{i}"}).status_code for i in range(7)]
    assert codes[:5] == [401] * 5 and 429 in codes[5:]


# ---------- two-step login ----------
def enable(app, token):
    c = fresh(app)
    started = c.post("/api/me/two-step/start", headers=bearer(token)).json()
    assert started["uri"].startswith("otpauth://totp/")
    assert c.post("/api/me/two-step/confirm", json={"code": "000000" if totp.code_at(started["secret"]) != "000000" else "111111"},
                  headers=bearer(token)).status_code == 400
    done = c.post("/api/me/two-step/confirm", json={"code": totp.code_at(started["secret"])}, headers=bearer(token)).json()
    assert done["on"] is True and len(done["recovery_codes"]) == 8 and done["recovery_left"] == 8
    return started["secret"], done["recovery_codes"]


def test_enable_then_sign_in_with_a_code_and_recovery_codes_work_once(app):
    owner = owner_token(app)
    add_user(app, owner, "ann@example.com")
    token = login(app, "ann@example.com", PW).json()["token"]
    secret, recovery = enable(app, token)
    user = next(u for u in app.state.store.read("settings")["users"] if u["email"] == "ann@example.com")
    assert recovery[0] not in str(user) and user["two_step_on"] is True
    first = login(app, "ann@example.com", PW).json()
    assert first["two_step"] == "code" and "token" not in first
    assert rows(app)[-1]["result"] == "pending"
    c = fresh(app)
    assert c.post("/api/login/code", json={"ticket": first["ticket"], "code": "12345x"}).status_code == 401
    assert c.post("/api/login/code", json={"ticket": "forged.ticket", "code": totp.code_at(secret)}).status_code == 401
    ok = c.post("/api/login/code", json={"ticket": first["ticket"], "code": totp.code_at(secret, time.time() + 30)})
    assert ok.status_code == 200 and ok.json()["token"]
    assert fresh(app).get("/api/me", headers=bearer(ok.json()["token"])).json()["email"] == "ann@example.com"
    assert rows(app)[-1]["result"] == "success" and rows(app)[-1]["two_step"] is True
    # the same code again is refused (one use per 30-second step)
    again = login(app, "ann@example.com", PW).json()
    assert fresh(app).post("/api/login/code", json={"ticket": again["ticket"], "code": totp.code_at(secret, time.time() + 30)}).status_code == 401
    # a recovery code works once
    assert fresh(app).post("/api/login/code", json={"ticket": again["ticket"], "code": recovery[0].upper()}).status_code == 200
    third = login(app, "ann@example.com", PW).json()
    assert fresh(app).post("/api/login/code", json={"ticket": third["ticket"], "code": recovery[0]}).status_code == 401
    assert fresh(app).get("/api/me/two-step", headers=bearer(token)).json()["recovery_left"] == 7
    # a code ticket cannot be used for the setup step
    assert fresh(app).post("/api/login/setup/start", json={"ticket": third["ticket"]}).status_code == 401


def test_too_many_wrong_codes_lock_that_login_for_a_while(app):
    owner = owner_token(app)
    add_user(app, owner, "ann@example.com")
    secret, _ = enable(app, login(app, "ann@example.com", PW).json()["token"])
    for i in range(5):
        ticket = login(app, "ann@example.com", PW).json()["ticket"]
        assert fresh(app).post("/api/login/code", json={"ticket": ticket, "code": "000000" if i % 2 else "999999"}).status_code in (401,)
    ticket = login(app, "ann@example.com", PW).json()["ticket"]
    assert fresh(app).post("/api/login/code", json={"ticket": ticket, "code": totp.code_at(secret)}).status_code == 429


def test_a_password_change_voids_a_waiting_ticket(app):
    owner = owner_token(app)
    add_user(app, owner, "ann@example.com")
    secret, _ = enable(app, login(app, "ann@example.com", PW).json()["token"])
    ticket = login(app, "ann@example.com", PW).json()["ticket"]
    fresh(app).put("/api/users/ann@example.com", json={"password": "another-password-1"}, headers=bearer(owner))
    assert fresh(app).post("/api/login/code", json={"ticket": ticket, "code": totp.code_at(secret)}).status_code == 401


def test_turning_it_off_needs_password_and_code(app):
    owner = owner_token(app)
    add_user(app, owner, "ann@example.com")
    token = login(app, "ann@example.com", PW).json()["token"]
    secret, _ = enable(app, token)
    assert fresh(app).post("/api/me/two-step/off", json={"password": "wrong-password", "code": totp.code_at(secret)}, headers=bearer(token)).status_code == 400
    r = fresh(app).post("/api/me/two-step/off", json={"password": PW, "code": totp.code_at(secret, time.time() + 30)}, headers=bearer(token))
    assert r.status_code == 200 and r.json()["on"] is False
    assert "token" in login(app, "ann@example.com", PW).json()


def test_owner_reset_is_audited_and_lets_the_person_sign_in_with_the_password(app):
    owner = owner_token(app)
    add_user(app, owner, "ann@example.com")
    enable(app, login(app, "ann@example.com", PW).json()["token"])
    people = fresh(app).get("/api/security/settings", headers=bearer(owner)).json()["people"]
    assert next(p for p in people if p["email"] == "ann@example.com")["two_step_on"] is True
    assert fresh(app).post("/api/security/two-step/ann@example.com/reset", headers=bearer(owner)).status_code == 200
    assert any(r.get("action") == "user.two_step_reset" and r.get("target") == "ann@example.com" for r in app.state.store.read("audit"))
    assert "token" in login(app, "ann@example.com", PW).json()


def test_a_caller_cannot_reset_or_require(app):
    owner = owner_token(app)
    add_user(app, owner, "ann@example.com")
    add_user(app, owner, "bob@example.com")
    bob = login(app, "bob@example.com", PW).json()["token"]
    assert fresh(app).post("/api/security/two-step/ann@example.com/reset", headers=bearer(bob)).status_code == 403
    assert fresh(app).put("/api/security/settings", json={"require_two_step": True}, headers=bearer(bob)).status_code == 403


def test_require_for_everyone_walks_a_login_without_it_through_setup(app):
    owner = owner_token(app)
    add_user(app, owner, "ann@example.com")
    r = fresh(app).put("/api/security/settings", json={"require_two_step": True}, headers=bearer(owner))
    assert r.status_code == 200 and r.json()["require_two_step"] is True and r.json()["admin_password_login"] is True
    first = login(app, "ann@example.com", PW).json()
    assert first["two_step"] == "setup" and "token" not in first
    c = fresh(app)
    assert c.post("/api/login/code", json={"ticket": first["ticket"], "code": "123456"}).status_code == 401   # wrong kind
    started = c.post("/api/login/setup/start", json={"ticket": first["ticket"]}).json()
    assert c.post("/api/login/setup/confirm", json={"ticket": first["ticket"], "code": "abc"}).status_code == 401
    done = c.post("/api/login/setup/confirm", json={"ticket": first["ticket"], "code": totp.code_at(started["secret"])})
    assert done.status_code == 200 and done.json()["token"] and len(done.json()["recovery_codes"]) == 8
    token = done.json()["token"]
    # now on, it cannot be switched off while required
    assert fresh(app).post("/api/me/two-step/off", json={"password": PW, "code": "000000"}, headers=bearer(token)).status_code == 400
    assert login(app, "ann@example.com", PW).json()["two_step"] == "code"
    # the admin password sign-in is not covered (the server's break-glass key)
    assert "token" in login(app).json()
    assert fresh(app).post("/api/me/two-step/start", headers=bearer(owner)).status_code == 400
    assert fresh(app).get("/api/me/two-step", headers=bearer(owner)).json()["available"] is False


def test_secret_is_encrypted_when_the_server_key_is_set(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTREACH_SECRET_KEY", "a-long-random-test-key-for-two-step-login")
    app = create_app(data_file=tmp_path / "d.json", admin_password=ADMIN)
    owner = owner_token(app)
    add_user(app, owner, "ann@example.com")
    token = login(app, "ann@example.com", PW).json()["token"]
    secret, _ = enable(app, token)
    user = next(u for u in app.state.store.read("settings")["users"] if u["email"] == "ann@example.com")
    assert user["two_step_secret"].startswith("enc:v1:") and secret not in user["two_step_secret"]
    ticket = login(app, "ann@example.com", PW).json()["ticket"]
    assert fresh(app).post("/api/login/code", json={"ticket": ticket, "code": totp.code_at(secret, time.time() + 30)}).status_code == 200


def test_setup_returns_a_qr_code_drawn_locally():
    """The setup screen shows a QR code for Google Authenticator; it is drawn by the server, never by an outside service."""
    import base64
    from app import two_step
    uri = "otpauth://totp/MCN%20Outreach:a%40b.c?secret=JBSWY3DPEHPK3PXP&issuer=MCN%20Outreach"
    qr = two_step.qr_data_uri(uri)
    assert qr.startswith("data:image/svg+xml;base64,")
    svg = base64.b64decode(qr.split(",", 1)[1]).decode()
    assert svg.startswith("<svg") and "http" not in svg.replace("http://www.w3.org/2000/svg", "")
