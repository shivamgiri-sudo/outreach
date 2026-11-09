"""Super admin (OUTREACH_SUPER_ADMIN): only they manage owner logins; their login cannot be removed or demoted.
Phase 1 hardening (app/super_admin.py): comma-separated super admins, mandatory two-step login for owners and super
admins, no email-only reset for super admins, sealed recovery codes, a local-only server admin password sign-in,
super-admin alerts and change-log rows."""
import secrets
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import auth, password_reset
from app.main import create_app
from tests.two_step_helpers import fresh_code, sign_in

PW = "long-password-1"
PUBLIC = ("203.0.113.9", 50000)
_IPS = iter(range(10, 250))


def anon(c):
    """A client from a new address (the sign-in rate limit is per address and frozen in tests)."""
    return TestClient(c.app, client=(f"198.51.100.{next(_IPS)}", 1))


def rig(tmp_path, monkeypatch, supers="boss@example.com", **kwargs):
    monkeypatch.setenv("OUTREACH_SUPER_ADMIN", supers)
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123", **kwargs), client=PUBLIC)
    h = lambda t: {"Authorization": f"Bearer {t}"}
    admin = c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    assert c.post("/api/users", json={"email": "boss@example.com", "name": "Boss", "role": "owner", "password": PW}, headers=h(admin)).status_code == 200
    login = lambda e: sign_in(anon(c), e, PW)
    return c, h, login


def add(c, h, token, email, role):
    r = c.post("/api/users", json={"email": email, "name": email.split("@")[0], "role": role, "password": PW}, headers=h(token))
    assert r.status_code == 200, r.text


def store_of(c):
    return c.app.state.store


def bells(c, kind="security:super_admin"):
    return [n for n in store_of(c).read("notifications") if n.get("kind") == kind]


def actions(c):
    return [r["action"] for r in store_of(c).read("audit")]


def test_super_admin_rules(tmp_path, monkeypatch):
    c, h, login = rig(tmp_path, monkeypatch)
    boss = login("boss@example.com")
    assert c.get("/api/me", headers=h(boss)).json()["super_admin"] is True
    assert c.post("/api/users", json={"email": "own@example.com", "name": "Owner Two", "role": "owner", "password": PW}, headers=h(boss)).status_code == 200
    own = login("own@example.com")
    me = c.get("/api/me", headers=h(own)).json()
    assert me["role"] == "owner" and me["super_admin"] is False
    # the second owner has full data access ...
    assert c.get("/api/leads", headers=h(own)).status_code == 200 and c.get("/api/users", headers=h(own)).status_code == 200
    # ... and may add callers, but not owners, and cannot touch the super admin
    assert c.post("/api/users", json={"email": "cal@example.com", "role": "caller", "password": PW}, headers=h(own)).status_code == 200
    assert c.post("/api/users", json={"email": "own3@example.com", "role": "owner", "password": PW}, headers=h(own)).status_code == 403
    assert c.put("/api/users/cal@example.com", json={"role": "owner"}, headers=h(own)).status_code == 403
    assert c.delete("/api/users/boss@example.com", headers=h(own)).status_code == 403
    assert c.put("/api/users/boss@example.com", json={"password": "another-pass-99"}, headers=h(own)).status_code == 403
    assert c.delete("/api/users/own@example.com", headers=h(own)).status_code == 403
    # the super admin manages owners, but cannot remove or demote themselves
    assert c.put("/api/users/boss@example.com", json={"role": "caller"}, headers=h(boss)).status_code == 403
    assert c.delete("/api/users/boss@example.com", headers=h(boss)).status_code == 403
    assert c.delete("/api/users/own@example.com", headers=h(boss)).status_code == 200
    users = {u["email"]: u for u in c.get("/api/users", headers=h(boss)).json()}
    assert users["boss@example.com"]["super_admin"] is True and "own@example.com" not in users


def test_without_a_super_admin_every_owner_manages_logins(tmp_path, monkeypatch):
    monkeypatch.delenv("OUTREACH_SUPER_ADMIN", raising=False)
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=PUBLIC)
    admin = c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    c.post("/api/users", json={"email": "a@example.com", "role": "owner", "password": PW}, headers={"Authorization": f"Bearer {admin}"})
    t = sign_in(c, "a@example.com", PW)
    assert c.post("/api/users", json={"email": "b@example.com", "role": "owner", "password": PW}, headers={"Authorization": f"Bearer {t}"}).status_code == 200


# ---------- 1. a comma-separated list ----------
def test_comma_separated_super_admins(tmp_path, monkeypatch):
    c, h, login = rig(tmp_path, monkeypatch, supers=" Boss@Example.com ,second@example.com,, ")
    assert auth.super_admins() == {"boss@example.com", "second@example.com"}
    assert auth.is_super_admin({"email": "SECOND@example.com", "role": "caller"}) is True
    assert auth.is_super_admin({"email": "own@example.com", "role": "owner"}) is False
    boss = login("boss@example.com")
    add(c, h, boss, "second@example.com", "owner")
    second = login("second@example.com")
    assert c.get("/api/me", headers=h(second)).json()["super_admin"] is True
    add(c, h, second, "own@example.com", "owner")             # the second super admin manages owners too
    monkeypatch.setenv("OUTREACH_SUPER_ADMIN", "boss@example.com")      # one email still works
    assert auth.super_admins() == {"boss@example.com"}


# ---------- 2. two-step login is mandatory for owners and super admins ----------
def test_owner_and_super_admin_must_set_up_two_step_before_a_token(tmp_path, monkeypatch):
    c, h, login = rig(tmp_path, monkeypatch)
    first = anon(c).post("/api/login", json={"email": "boss@example.com", "password": PW}).json()
    assert "token" not in first and first["two_step"] == "setup"          # existing super admin: guided into setup
    boss = login("boss@example.com")
    again = anon(c).post("/api/login", json={"email": "boss@example.com", "password": PW}).json()
    assert again["two_step"] == "code" and "token" not in again           # now it asks for the code
    add(c, h, boss, "own@example.com", "owner")
    add(c, h, boss, "cal@example.com", "caller")
    assert anon(c).post("/api/login", json={"email": "own@example.com", "password": PW}).json()["two_step"] == "setup"
    assert anon(c).post("/api/login", json={"email": "cal@example.com", "password": PW}).json().get("token")   # callers: optional
    # a caller promoted to owner is asked at the next sign-in
    assert c.put("/api/users/cal@example.com", json={"role": "owner"}, headers=h(boss)).status_code == 200
    assert anon(c).post("/api/login", json={"email": "cal@example.com", "password": PW}).json()["two_step"] == "setup"
    # owners cannot switch it off, even with the right password and code
    own = login("own@example.com")
    off = c.post("/api/me/two-step/off", json={"password": PW, "code": fresh_code(c, "own@example.com")}, headers=h(own))
    assert off.status_code == 400 and "must keep" in off.json()["detail"]


# ---------- 3. password reset ----------
class _Tokens:
    """Known reset tokens: each new link gets the next one; str(_TOKENS) is the newest."""
    n = 0

    def __str__(self):
        return f"known-reset-token-{self.n:04d}-" + "x" * 20


_TOKENS = _Tokens()


def fixed_tokens(monkeypatch):
    def make(_n):
        _TOKENS.n += 1
        return str(_TOKENS)
    monkeypatch.setattr(password_reset, "secrets", SimpleNamespace(token_urlsafe=make, compare_digest=secrets.compare_digest))


def resets_for(c, email):
    return [r for r in store_of(c).read("settings").get("password_resets") or [] if r["email"] == email]


def test_super_admin_gets_no_email_only_reset_link(tmp_path, monkeypatch):
    c, h, login = rig(tmp_path, monkeypatch)
    login("boss@example.com")
    fixed_tokens(monkeypatch)
    plain = c.post("/api/password/forgot", json={"email": "boss@example.com"}).json()
    nobody = c.post("/api/password/forgot", json={"email": "nobody@example.com"}).json()
    assert plain == nobody == {"message": password_reset.GENERIC}                    # no enumeration
    assert resets_for(c, "boss@example.com") == [] and "user.reset_withheld" in actions(c)
    # the recovery reset sends a link, which needs a code too
    assert c.post("/api/password/forgot", json={"email": "boss@example.com", "recovery": True}).json() == plain
    assert len(resets_for(c, "boss@example.com")) == 1
    anon_c = TestClient(c.app, client=PUBLIC)
    assert anon_c.post("/api/password/reset/info", json={"token": str(_TOKENS)}).json() == {"valid": True, "needs_code": True, "super_admin": True}
    no_code = anon_c.post("/api/password/reset", json={"token": str(_TOKENS), "password": "brand-new-pass-9"})
    assert no_code.status_code == 400 and "code" in no_code.json()["detail"]
    assert anon_c.post("/api/password/reset", json={"token": str(_TOKENS), "password": "brand-new-pass-9", "code": "000000"}).status_code == 400
    ok = anon_c.post("/api/password/reset", json={"token": str(_TOKENS), "password": "brand-new-pass-9", "code": fresh_code(c, "boss@example.com")})
    assert ok.status_code == 200
    assert auth.check_password(auth.find_user(store_of(c), "boss@example.com")["password_hash"], "brand-new-pass-9")


def test_signed_in_super_admin_changes_own_password(tmp_path, monkeypatch):
    c, h, login = rig(tmp_path, monkeypatch)
    boss = login("boss@example.com")
    assert c.post("/api/me/password", json={"current": PW, "new": "brand-new-pass-9"}, headers=h(boss)).status_code == 200


def test_owner_reset_needs_link_and_code_callers_keep_link_only(tmp_path, monkeypatch):
    c, h, login = rig(tmp_path, monkeypatch)
    boss = login("boss@example.com")
    add(c, h, boss, "own@example.com", "owner")
    add(c, h, boss, "cal@example.com", "caller")
    add(c, h, boss, "cal2@example.com", "caller")
    login("own@example.com")                                                      # sets two-step up
    fixed_tokens(monkeypatch)
    anon_c = TestClient(c.app, client=PUBLIC)
    # owner: the link alone is refused; the link + the code works
    c.post("/api/password/forgot", json={"email": "own@example.com"})
    assert anon_c.post("/api/password/reset", json={"token": str(_TOKENS), "password": "brand-new-pass-9"}).status_code == 400
    assert anon_c.post("/api/password/reset", json={"token": str(_TOKENS), "password": "brand-new-pass-9", "code": fresh_code(c, "own@example.com")}).status_code == 200
    # caller without two-step: the link alone, as before
    c.post("/api/password/forgot", json={"email": "cal@example.com"})
    assert anon_c.post("/api/password/reset/info", json={"token": str(_TOKENS)}).json()["needs_code"] is False
    assert anon_c.post("/api/password/reset", json={"token": str(_TOKENS), "password": "brand-new-pass-9"}).status_code == 200
    # caller who turned two-step on: the code is needed too
    cal2 = sign_in(c, "cal2@example.com", PW)
    from app import totp
    secret = c.post("/api/me/two-step/start", headers=h(cal2)).json()["secret"]
    assert c.post("/api/me/two-step/confirm", json={"code": totp.code_at(secret)}, headers=h(cal2)).status_code == 200
    c.post("/api/password/forgot", json={"email": "cal2@example.com"})
    assert anon_c.post("/api/password/reset", json={"token": str(_TOKENS), "password": "brand-new-pass-9"}).status_code == 400
    assert anon_c.post("/api/password/reset", json={"token": str(_TOKENS), "password": "brand-new-pass-9",
                                                  "code": totp.code_at(secret, __import__("time").time() + 30)}).status_code == 200


def test_wrong_codes_void_the_link(tmp_path, monkeypatch):
    c, h, login = rig(tmp_path, monkeypatch)
    login("boss@example.com")
    fixed_tokens(monkeypatch)
    c.post("/api/password/forgot", json={"email": "boss@example.com", "recovery": True})
    anon_c = TestClient(c.app, client=PUBLIC)
    for _ in range(password_reset.MAX_CODE_TRIES):
        assert anon_c.post("/api/password/reset", json={"token": str(_TOKENS), "password": "brand-new-pass-9", "code": "111111"}).status_code == 400
    late = anon_c.post("/api/password/reset", json={"token": str(_TOKENS), "password": "brand-new-pass-9", "code": fresh_code(c, "boss@example.com")})
    assert late.status_code == 400 and "expired" in late.json()["detail"]


# ---------- 4. sealed recovery codes ----------
def test_sealed_recovery_codes_reset_password_and_two_step(tmp_path, monkeypatch):
    c, h, login = rig(tmp_path, monkeypatch)
    boss = login("boss@example.com")
    add(c, h, boss, "own@example.com", "owner")
    own = login("own@example.com")
    assert c.post("/api/super-admin/recovery-codes", json={"password": PW, "code": "1"}, headers=h(own)).status_code == 403
    assert c.get("/api/super-admin/status", headers=h(own)).status_code == 403
    assert c.post("/api/super-admin/recovery-codes", json={"password": "wrong-password-1", "code": "000000"}, headers=h(boss)).status_code == 400
    assert c.post("/api/super-admin/recovery-codes", json={"password": PW, "code": ""}, headers=h(boss)).status_code == 400   # 2FA code too
    first = c.post("/api/super-admin/recovery-codes", json={"password": PW, "code": fresh_code(c, "boss@example.com")}, headers=h(boss)).json()
    assert len(first["recovery_codes"]) == 10 and len(set(first["recovery_codes"])) == 10 and first["recovery"]["left"] == 10
    row = auth.find_user(store_of(c), "boss@example.com")
    assert all(code not in str(row) for code in first["recovery_codes"])          # kept hashed only
    from app import totp
    real_step = totp.step_of
    monkeypatch.setattr(totp, "step_of", lambda now=None: real_step(now) + 3)      # a minute and a half later
    second = c.post("/api/super-admin/recovery-codes", json={"password": PW, "code": fresh_code(c, "boss@example.com")}, headers=h(boss)).json()
    assert set(second["recovery_codes"]).isdisjoint(first["recovery_codes"])
    fixed_tokens(monkeypatch)
    anon_c = TestClient(c.app, client=PUBLIC)
    c.post("/api/password/forgot", json={"email": "boss@example.com", "recovery": True})
    old = anon_c.post("/api/password/reset", json={"token": str(_TOKENS), "password": "brand-new-pass-9", "code": first["recovery_codes"][0]})
    assert old.status_code == 400                                                 # regenerated: old codes are void
    used = second["recovery_codes"][0]
    assert anon_c.post("/api/password/reset", json={"token": str(_TOKENS), "password": "brand-new-pass-9", "code": used}).status_code == 200
    row = auth.find_user(store_of(c), "boss@example.com")
    assert not row.get("two_step_on") and len(row["super_recovery"]) == 9        # two-step reset; the code is used up
    assert auth.check_password(row["password_hash"], "brand-new-pass-9")
    assert anon(c).post("/api/login", json={"email": "boss@example.com", "password": "brand-new-pass-9"}).json()["two_step"] == "setup"
    c.post("/api/password/forgot", json={"email": "boss@example.com", "recovery": True})
    assert anon_c.post("/api/password/reset", json={"token": str(_TOKENS), "password": "another-pass-99", "code": used}).status_code == 400   # once only
    assert {"super_admin.recovery_codes", "super_admin.recovery_used"} <= set(actions(c))
    titles = [n["title"] for n in bells(c)]
    assert any("recovery codes made" in t for t in titles) and any("recovery code used" in t for t in titles)


def test_owners_cannot_reset_a_super_admins_two_step(tmp_path, monkeypatch):
    c, h, login = rig(tmp_path, monkeypatch, supers="boss@example.com,second@example.com")
    boss = login("boss@example.com")
    add(c, h, boss, "second@example.com", "owner")
    add(c, h, boss, "own@example.com", "owner")
    add(c, h, boss, "own2@example.com", "owner")
    second, own = login("second@example.com"), login("own@example.com")
    login("own2@example.com")
    assert c.post("/api/security/two-step/boss@example.com/reset", headers=h(own)).status_code == 403
    assert c.post("/api/security/two-step/boss@example.com/reset", headers=h(boss)).status_code == 403     # not their own
    assert c.post("/api/security/two-step/own2@example.com/reset", headers=h(own)).status_code == 403      # owners: super admin only
    assert c.post("/api/security/two-step/boss@example.com/reset", headers=h(second)).status_code == 200   # another super admin
    assert c.post("/api/security/two-step/own2@example.com/reset", headers=h(boss)).status_code == 200
    assert actions(c).count("user.two_step_reset") == 2
    assert sum(1 for n in bells(c) if n["title"].startswith("Two-step login reset")) >= 4           # 2 resets x 2 super admins


# ---------- 5. the server admin password sign-in ----------
def test_admin_password_login_is_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTREACH_ADMIN_PASSWORD", "admin-pass-123")
    monkeypatch.delenv("OUTREACH_ADMIN_LOGIN", raising=False)
    app = create_app(data_file=tmp_path / "a.json")
    local = TestClient(app, client=("127.0.0.1", 5000), base_url="http://127.0.0.1:8000")
    r = local.post("/api/login", json={"password": "admin-pass-123"})
    assert r.status_code == 403 and "switched off" in r.json()["detail"]
    assert app.state.store.read("login_history")[-1]["result"] == "failure"


def test_admin_password_login_only_from_the_server_itself(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTREACH_ADMIN_LOGIN", "1")
    monkeypatch.setenv("OUTREACH_ADMIN_PASSWORD", "admin-pass-123")
    monkeypatch.setenv("OUTREACH_SUPER_ADMIN", "boss@example.com")
    app = create_app(data_file=tmp_path / "a.json")
    local = TestClient(app, client=("127.0.0.1", 5000), base_url="http://127.0.0.1:8000")
    public = TestClient(app, client=PUBLIC, base_url="https://outreach.example.in")
    assert public.post("/api/login", json={"password": "admin-pass-123"}).status_code == 403
    # through the local Caddy: the peer is 127.0.0.1 but X-Forwarded-For says it came from outside
    proxied = local.post("/api/login", json={"password": "admin-pass-123"}, headers={"X-Forwarded-For": "198.51.100.7"})
    assert proxied.status_code == 403
    spoofed = local.post("/api/login", json={"password": "admin-pass-123"}, headers={"X-Forwarded-For": "127.0.0.1"})
    assert spoofed.status_code == 403
    assert TestClient(app, client=("127.0.0.1", 5000), base_url="https://outreach.example.in").post(
        "/api/login", json={"password": "admin-pass-123"}).status_code == 403      # public Host name
    ok = local.post("/api/login", json={"password": "admin-pass-123"})
    assert ok.status_code == 200
    token = ok.json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    assert local.get("/api/users", headers=hdr).status_code == 200
    assert local.get("/api/users", headers={**hdr, "X-Forwarded-For": "198.51.100.7"}).status_code == 401   # the token stays local
    assert public.get("/api/users", headers=hdr).status_code == 401
    assert "super_admin.admin_login" in [r["action"] for r in app.state.store.read("audit")]
    assert any(n["title"] == "Server admin password used" and n["to"] == "boss@example.com"
               for n in app.state.store.read("notifications"))
    assert app.state.store.read("login_history")[-1]["result"] == "success"


def test_explicit_admin_password_keeps_test_mode_and_admin_login_override(tmp_path, monkeypatch):
    monkeypatch.delenv("OUTREACH_ADMIN_LOGIN", raising=False)
    app = create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123")
    assert TestClient(app, client=PUBLIC).post("/api/login", json={"password": "admin-pass-123"}).status_code == 200
    off = create_app(data_file=tmp_path / "b.json", admin_password="admin-pass-123", admin_login="off")
    assert TestClient(off, client=("127.0.0.1", 5000)).post("/api/login", json={"password": "admin-pass-123"}).status_code == 403


# ---------- 6 + 7. alerts and the change log ----------
def test_failed_super_admin_sign_ins_alert_after_three(tmp_path, monkeypatch):
    c, h, login = rig(tmp_path, monkeypatch)
    login("boss@example.com")
    other = TestClient(c.app, client=("198.51.100.20", 1))
    for i in range(2):
        assert other.post("/api/login", json={"email": "boss@example.com", "password": f"wrong-password-{i}"}).status_code == 401
    assert not [n for n in bells(c) if "Failed super admin" in n["title"]]
    assert other.post("/api/login", json={"email": "boss@example.com", "password": "wrong-password-3"}).status_code == 401
    alerts = [n for n in bells(c) if "Failed super admin" in n["title"]]
    assert len(alerts) == 1 and alerts[0]["to"] == "Boss"
    assert "super_admin.failed_logins" in actions(c)
    other.post("/api/login", json={"email": "boss@example.com", "password": "wrong-password-4"})
    assert len([n for n in bells(c) if "Failed super admin" in n["title"]]) == 1              # one burst, one alert
    # super admin alert emails go to the super admin's address
    assert any(r["to"] == "boss@example.com" and "Failed super admin" in r["subject"] for r in store_of(c).read("transactional_outbox"))


def test_owner_login_changes_alert_super_admins_and_are_in_the_change_log(tmp_path, monkeypatch):
    c, h, login = rig(tmp_path, monkeypatch)
    boss = login("boss@example.com")
    add(c, h, boss, "own@example.com", "owner")
    add(c, h, boss, "cal@example.com", "caller")
    assert c.put("/api/users/cal@example.com", json={"role": "owner"}, headers=h(boss)).status_code == 200
    assert c.delete("/api/users/own@example.com", headers=h(boss)).status_code == 200
    titles = [n["title"] for n in bells(c)]
    assert "Owner login added: own@example.com" in titles
    assert "Owner login changed: cal@example.com" in titles
    assert "Owner login removed: own@example.com" in titles
    assert not any("cal@example.com" in t and "added" in t for t in titles)                 # adding a caller: no alert
    assert {"user.add", "user.change", "user.remove"} <= set(actions(c))
    assert c.get("/api/audit/verify", headers=h(boss)).json().get("ok") is True              # the hash chain holds


def test_status_lists_owners_super_admins_and_admin_login(tmp_path, monkeypatch):
    c, h, login = rig(tmp_path, monkeypatch, supers="boss@example.com,later@example.com")
    boss = login("boss@example.com")
    add(c, h, boss, "own@example.com", "owner")
    add(c, h, boss, "cal@example.com", "caller")
    s = c.get("/api/super-admin/status", headers=h(boss)).json()
    assert s["super_admins"] == ["boss@example.com", "later@example.com"]
    owners = {o["email"]: o for o in s["owners"]}
    assert set(owners) == {"boss@example.com", "own@example.com"}
    assert owners["boss@example.com"]["two_step_on"] is True and owners["own@example.com"]["two_step_on"] is False
    assert s["recovery"] == {"left": 0, "made_at": None, "available": True}
    assert s["admin_login"] == {"password_set": True, "mode": "any", "on": True, "local_only": False}
