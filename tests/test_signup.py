"""Phase 3: self sign-up with super-admin approval, Google / Microsoft sign-in, magic links (app/signup.py, oidc.py,
magic_link.py, routes_signup.py, account_guard.py). Numbers refer to the plan's items."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import auth, email_domain, signup
from tests.signup_helpers import SUPER, FakeProvider, form, link_token, mails, make, signed_up
from tests.two_step_helpers import sign_in


# ---------------------------------------------------------------- 1. the sign-up form
def test_signup_sends_a_verification_link_and_keeps_consent_and_no_plain_password(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    cfg = c.get("/api/signup/config").json()
    assert cfg["signup"] and cfg["providers"] == [] and cfg["terms"]["dpa"]          # no OIDC buttons when not configured
    r = c.post("/api/signup", json=form(c))
    assert r.status_code == 200 and r.json()["message"] == signup.GENERIC
    assert "#verify?token=" in mails(outbox, "dev@deltafreight.example")[-1]["body"]
    raw = (tmp_path / "s.json").read_text()
    assert "delta-long-password-1" not in raw and "#verify?token=" not in raw          # hash only; link never stored
    row = c.app.state.store.read("settings")["signup_requests"][-1]
    assert row["consent"]["terms"] == signup.TERMS_VERSION and row["consent"]["ip"] == "203.0.113.9" and row["consent"]["at"]
    assert c.post("/api/signup", json=form(c, accept=False)).status_code == 400
    assert c.post("/api/signup", json=form(c, password="short")).status_code == 400


def test_no_enumeration_existing_address_gets_the_same_answer(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    r = c.post("/api/signup", json=form(c, email=SUPER, company="Sneaky"))
    assert r.status_code == 200 and r.json()["message"] == signup.GENERIC
    last = mails(outbox, SUPER)[-1]
    assert "already has a login" in last["body"] and "token=" not in last["body"]


def test_bot_protection_honeypot_fill_time_challenge_and_limits(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    monkeypatch.setattr(signup, "MIN_FILL_SECONDS", 4)
    assert c.post("/api/signup", json=form(c, website="http://spam")).json()["message"] == signup.GENERIC
    assert mails(outbox, "dev@deltafreight.example") == []                           # honeypot: silently dropped
    fast = c.post("/api/signup", json=form(c))                                         # sent at once: too fast
    assert fast.status_code == 400
    ch = fast.json()["detail"]["challenge"]
    a, b = [int(x) for x in ch["question"].replace("What is ", "").rstrip("?").split(" + ")]
    wrong = c.post("/api/signup", json=form(c, challenge_token=ch["token"], challenge_answer=str(a + b + 1)))
    assert wrong.status_code == 400 and "challenge" in wrong.json()["detail"]
    ok = c.post("/api/signup", json=form(c, challenge_token=ch["token"], challenge_answer=str(a + b)))
    assert ok.status_code == 200
    # a second sign-up from the same address within the hour: the sum is asked even when slow
    monkeypatch.setattr(signup, "MIN_FILL_SECONDS", 0)
    assert "challenge" in c.post("/api/signup", json=form(c, email="x@other.example", company="Other")).json()["detail"]
    # hard limit per address
    monkeypatch.setattr(signup, "PER_IP_HOUR", 1)
    assert c.post("/api/signup", json=form(c, email="y@third.example", company="Third")).status_code == 429
    # per email domain (another address, same company domain)
    c2 = TestClient(c.app, client=("198.51.100.7", 50000))
    monkeypatch.setattr(signup, "PER_DOMAIN_HOUR", 1)
    assert c2.post("/api/signup", json=form(c2, email="ops@deltafreight.example", company="Delta 2")).status_code == 429


def test_rate_limit_middleware_covers_signup(tmp_path, monkeypatch):
    from app import web_security
    assert web_security.limiter_for(web_security.make_limiters(), "/api/signup")[0] == "/api/signup"
    assert web_security.limiter_for(web_security.make_limiters(), "/api/oidc/google/start")[0] == "/api/oidc/"
    assert web_security.limiter_for(web_security.make_limiters(), "/api/magic-link")[0] == "/api/magic-link"


def test_email_domain_is_checked(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    monkeypatch.setattr(email_domain, "CHECK", True)
    monkeypatch.setattr(email_domain, "_lookup", lambda d: "no_domain")
    r = c.post("/api/signup", json=form(c, email="a@nosuchdomain-xyz.example"))
    assert r.status_code == 400 and "does not exist" in r.json()["detail"]


def test_verification_link_is_single_use_signed_and_expires(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    c.post("/api/signup", json=form(c))
    token = link_token(outbox, "dev@deltafreight.example")
    assert c.post("/api/signup/verify", json={"token": token[:-3] + "AAA"}).status_code == 400        # signature
    late = datetime.now(timezone.utc) + timedelta(hours=25)
    try:
        signup.verify(c.app.state.store, token, now=late)
        raise AssertionError("expired link accepted")
    except ValueError:
        pass
    assert c.post("/api/signup/verify", json={"token": token}).status_code == 200
    assert c.post("/api/signup/verify", json={"token": token}).status_code == 400                     # single use


# ---------------------------------------------------------------- 2. pending accounts
def test_verified_signup_is_a_pending_account_with_an_admin_and_a_requested_workspace(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    hp, aid, email = signed_up(c, outbox, email="dev@gmail.com")
    store = c.app.state.store
    row = next(a for a in store.read("settings")["accounts"] if a["id"] == aid)
    assert row["status"] == "pending" and "free email" in row["flags"] and row["signup"]["consent"]["dpa"]
    user = auth.find_user(store, email)
    assert user["role"] == "admin" and user["account_id"] == aid
    space = next(w for w in store.read("settings")["workspaces"] if w["account_id"] == aid)
    assert space["status"] == "requested" and user["workspaces"] == [space["id"]]
    page = c.get("/api/signup/pending", headers=hp).json()
    assert page["pending"] and page["account"] == "Delta Freight" and page["steps"][0]["done"]


def test_pending_account_sees_only_its_own_profile_and_everything_else_is_refused(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    hp, aid, email = signed_up(c, outbox)
    for path in ("/api/me", "/api/me/two-step", "/api/platform/status", "/api/signup/pending"):
        assert c.get(path, headers=hp).status_code == 200, path
    refused = [("get", "/api/leads"), ("get", "/api/dashboard"), ("post", "/api/leads/import"), ("get", "/api/integrations"),
               ("post", "/api/extension/pair"), ("get", "/api/outbox"), ("post", "/api/workspaces"), ("get", "/api/linkedin/queue"),
               ("post", "/api/sequences/run"), ("get", "/api/export/leads.csv"), ("get", "/api/users"), ("post", "/api/import/commit")]
    for method, path in refused:
        r = getattr(c, method)(path, headers=hp, **({"json": {}} if method == "post" else {}))
        assert r.status_code == 403 and r.json()["detail"] == "account pending approval", (path, r.status_code, r.text)
    # their own password still works, and they can sign in again (two-step optional)
    assert c.post("/api/me/password", json={"current": "delta-long-password-1", "new": "delta-long-password-2"}, headers=hp).status_code == 200
    again = c.post("/api/login", json={"email": email, "password": "delta-long-password-2"})
    assert again.status_code == 200 and again.json().get("token")
    assert c.post("/api/logout", headers={"Authorization": f"Bearer {again.json()['token']}"}).status_code == 200


# ---------------------------------------------------------------- 3. alerts to the super admins
def test_super_admins_get_a_bell_and_email_for_signups_and_workspace_requests(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    hp, aid, email = signed_up(c, outbox)
    store = c.app.state.store
    bells = [n for n in store.read("notifications") if n.get("kind") == "security:super_admin"]
    assert any("New sign-up: Delta Freight" in n["title"] for n in bells)
    sent = [m for m in store.read("transactional_outbox") if m["to"] == SUPER]
    assert any("New sign-up" in m["subject"] and "#setup/accounts" in m["body"] for m in sent)
    c.post(f"/api/console/accounts/{aid}/approve", headers=hs)
    hp = {"Authorization": f"Bearer {c.post('/api/login', json={'email': email, 'password': 'delta-long-password-1'}).json()['token']}"}
    assert c.post("/api/workspaces", json={"name": "Delta Two"}, headers=hp).json()["status"] == "requested"
    assert any("Workspace request: Delta Two" in n["title"] for n in store.read("notifications") if n.get("kind") == "security:super_admin")


# ---------------------------------------------------------------- 4. approve / reject / ask
def test_approve_lets_the_client_admin_work_and_emails_only_console_links(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    hp, aid, email = signed_up(c, outbox)
    assert c.post(f"/api/console/accounts/{aid}/approve", headers=hp).status_code == 403
    assert c.post(f"/api/console/accounts/{aid}/approve", headers=hs).status_code == 200
    note = mails(outbox, email)[-1]
    assert "approved" in note["body"] and "token=" not in note["body"]
    alerts = [m for m in c.app.state.store.read("transactional_outbox") if m["to"] == SUPER]
    assert alerts and all("approve?" not in m["body"] and "token=" not in m["body"] for m in alerts)   # never one-click approve
    hp = {"Authorization": f"Bearer {c.post('/api/login', json={'email': email, 'password': 'delta-long-password-1'}).json()['token']}"}
    assert c.get("/api/leads", headers=hp).status_code == 200
    imp = c.post("/api/leads/import", json={"csv": "company,contact_name,email\nZed Co,Zed,zed@zedco.example\n"}, headers=hp)
    assert imp.status_code == 200, imp.text
    assert [l["company"] for l in c.get("/api/leads", headers=hp).json()] == ["Zed Co"]


def test_reject_emails_the_reason_and_ask_shows_on_the_waiting_page(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    hp, aid, email = signed_up(c, outbox)
    assert c.post(f"/api/console/accounts/{aid}/ask", json={"message": ""}, headers=hs).status_code == 400
    assert c.post(f"/api/console/accounts/{aid}/ask", json={"message": "Your GST number please"}, headers=hs).status_code == 200
    assert "GST number" in mails(outbox, email)[-1]["body"]
    assert c.get("/api/signup/pending", headers=hp).json()["questions"][-1]["message"] == "Your GST number please"
    assert c.post(f"/api/console/accounts/{aid}/reject", json={"reason": ""}, headers=hs).status_code == 400
    assert c.post(f"/api/console/accounts/{aid}/reject", json={"reason": "Not a business address"}, headers=hs).status_code == 200
    assert "Not a business address" in mails(outbox, email)[-1]["body"]
    assert auth.find_user(c.app.state.store, email) is None
    assert c.get("/api/me", headers=hp).status_code == 401


# ---------------------------------------------------------------- 5. Google / Microsoft (OIDC)
def test_oidc_signs_in_an_existing_caller_by_verified_email(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    fake = FakeProvider(monkeypatch)
    assert c.get("/api/signup/config").json()["providers"] == [{"id": "google", "title": "Google"}]
    c.post("/api/users", json={"email": "cal@mcn.example", "role": "caller", "password": "long-password-1", "all_workspaces": True}, headers=ha)
    r, state = fake.run(c, email="cal@mcn.example", sub="g-cal")
    assert r.status_code == 200 and r.json()["token"], r.text
    assert c.get("/api/me", headers={"Authorization": f"Bearer {r.json()['token']}"}).json()["email"] == "cal@mcn.example"
    assert c.post("/api/oidc/callback", json={"code": "x", "state": state}).status_code == 400            # state is single use
    assert auth.find_user(c.app.state.store, "cal@mcn.example")["oidc"] == {"google": "g-cal"}
    r2, _ = fake.run(c, email="cal@mcn.example", sub="g-someone-else")                                     # linked: sub must match
    assert r2.status_code == 403
    assert any(call[1].endswith("/certs") for call in fake.calls)


def test_oidc_owner_and_super_admin_still_need_the_authenticator_code(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    fake = FakeProvider(monkeypatch)
    r, _ = fake.run(c, email=SUPER, sub="g-boss")
    assert r.status_code == 200 and r.json()["two_step"] == "code" and "token" not in r.json()


def test_oidc_rejects_bad_tokens(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    fake = FakeProvider(monkeypatch)
    c.post("/api/users", json={"email": "cal@mcn.example", "role": "caller", "password": "long-password-1", "all_workspaces": True}, headers=ha)
    assert fake.run(c, email="cal@mcn.example", email_verified=False)[0].status_code == 400
    assert fake.run(c, email="cal@mcn.example", aud="another-app")[0].status_code == 400
    assert fake.run(c, email="cal@mcn.example", iss="https://evil.example")[0].status_code == 400
    assert fake.run(c, email="cal@mcn.example", nonce="not-the-nonce")[0].status_code == 400
    assert fake.run(c, email="cal@mcn.example", exp=1000)[0].status_code == 400
    fake.forge = True
    bad = fake.run(c, email="cal@mcn.example")[0]
    assert bad.status_code == 400 and "signature" in bad.json()["detail"]
    fake.forge = False
    assert fake.run(c, email="nobody@nowhere.example")[0].status_code == 404
    assert c.post("/api/oidc/callback", json={"code": "x", "state": "forged-state-123"}).status_code == 400
    assert c.get("/api/oidc/microsoft/start").status_code == 404                                          # not configured


def test_oidc_signup_makes_a_pending_account_without_a_password(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    fake = FakeProvider(monkeypatch)
    r, _ = fake.run(c, intent="signup", email="ana@acmeparts.example", name="Ana", sub="g-ana")
    ticket = r.json()["signup"]["ticket"]
    assert c.post("/api/signup/oidc", json={"ticket": ticket, "company": "Acme Parts", "accept": False}).status_code == 400
    done = c.post("/api/signup/oidc", json={"ticket": ticket, "company": "Acme Parts", "accept": True})
    assert done.status_code == 200 and done.json()["token"]
    hp = {"Authorization": f"Bearer {done.json()['token']}"}
    assert c.get("/api/platform/status", headers=hp).json()["account_status"] == "pending"
    assert c.get("/api/leads", headers=hp).status_code == 403
    user = auth.find_user(c.app.state.store, "ana@acmeparts.example")
    assert user["oidc"] == {"google": "g-ana"} and user["password_hash"] == ""
    assert c.post("/api/login", json={"email": "ana@acmeparts.example", "password": ""}).status_code in (400, 401)


def test_microsoft_issuer_rule(monkeypatch):
    from app import oidc
    cfg = {"issuers": ("microsoft",), "tenant": "organizations", "client_id": "m"}
    good = {"iss": "https://login.microsoftonline.com/t-1/v2.0", "tid": "t-1"}
    assert oidc._issuer_ok(cfg, good) and not oidc._issuer_ok(cfg, {**good, "tid": "t-2"})
    assert not oidc._issuer_ok({**cfg, "tenant": "t-9"}, good)
    who = oidc.check_claims(cfg, {**good, "aud": "m", "exp": 2e9, "nonce": "n", "email": "A@x.com", "xms_edov": True}, "n", 1.7e9)
    assert who["email"] == "a@x.com"


# ---------------------------------------------------------------- 6. magic links
def test_magic_link_for_callers_only_single_use_and_rate_limited(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    c.post("/api/users", json={"email": "cal@mcn.example", "role": "caller", "password": "long-password-1", "all_workspaces": True}, headers=ha)
    c.post("/api/users", json={"email": "adm@mcn.example", "role": "admin", "password": "long-password-1", "workspaces": ["main"]}, headers=ha)
    same = c.post("/api/magic-link", json={"email": "nobody@x.example"}).json()["message"]
    for who in ("adm@mcn.example", SUPER):
        assert c.post("/api/magic-link", json={"email": who}).json()["message"] == same
        assert not any("#magic?token=" in m["body"] for m in mails(outbox, who))       # owners / admins / super admins never
    assert c.post("/api/magic-link", json={"email": "cal@mcn.example"}).json()["message"] == same
    token = link_token(outbox, "cal@mcn.example", "magic")
    r = c.post("/api/magic-link/verify", json={"token": token})
    assert r.status_code == 200 and r.json()["token"]
    assert c.post("/api/magic-link/verify", json={"token": token}).status_code == 400
    for _ in range(3):
        c.post("/api/magic-link", json={"email": "cal@mcn.example"})
    assert len([m for m in mails(outbox, "cal@mcn.example") if "#magic?token=" in m["body"]]) == 3     # 3 an hour
    from app import magic_link
    c.post("/api/magic-link", json={"email": "cal@mcn.example"})
    late = datetime.now(timezone.utc) + timedelta(minutes=16)
    tok = link_token(outbox, "cal@mcn.example", "magic")
    try:
        magic_link.use_link(c.app.state.store, tok, now=late)
        raise AssertionError("expired magic link accepted")
    except ValueError:
        pass


def test_magic_link_still_asks_for_two_step_when_on(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    c.post("/api/users", json={"email": "cal@mcn.example", "role": "caller", "password": "long-password-1", "all_workspaces": True}, headers=ha)
    c.put("/api/security/settings", json={"require_two_step": True}, headers=hs)
    c.post("/api/magic-link", json={"email": "cal@mcn.example"})
    r = c.post("/api/magic-link/verify", json={"token": link_token(outbox, "cal@mcn.example", "magic")})
    assert r.status_code == 200 and r.json()["two_step"] == "setup"
    assert sign_in(c, "cal@mcn.example", "long-password-1")


def test_public_signup_is_closed_until_a_super_admin_opens_it(tmp_path, monkeypatch):
    c, ha, hs, outbox = make(tmp_path, monkeypatch, open_signup=False)
    raw = {"company": "Delta Freight", "name": "Dev Patel", "email": "dev@deltafreight.example", "password": "delta-long-password-1",
           "accept": True, "ticket": "x"}
    assert c.get("/api/signup/config").json()["signup"] is False                  # no "Create an account" link on the sign-in page
    assert c.post("/api/signup", json=raw).status_code == 403
    assert c.get("/api/signup/form").status_code == 403
    assert mails(outbox, "dev@deltafreight.example") == []
    assert c.post("/api/users", json={"email": "plain@owner.example", "name": "Plain", "role": "owner", "password": "plain-owner-pass-1"}, headers=hs).status_code == 200
    plain = {"Authorization": f"Bearer {sign_in(c, 'plain@owner.example', 'plain-owner-pass-1')}"}
    assert c.put("/api/console/signup-open", json={"on": True}, headers=plain).status_code in (401, 403)   # only a super admin flips it
    assert c.put("/api/console/signup-open", json={"on": True}, headers=hs).json() == {"signup_open": True}
    assert c.get("/api/signup/config").json()["signup"] is True
    assert c.post("/api/signup", json=form(c)).status_code == 200
    assert c.put("/api/console/signup-open", json={"on": False}, headers=hs).json() == {"signup_open": False}
    assert c.post("/api/signup", json={**raw, "email": "other@deltafreight.example"}).status_code == 403
    assert any(r.get("action") == "platform.signup" for r in c.app.state.store.read("audit"))


def test_failed_magic_link_send_alerts_super_admins_once_an_hour(tmp_path, monkeypatch):
    from app import magic_link
    c, ha, hs, outbox = make(tmp_path, monkeypatch)
    c.post("/api/users", json={"email": "cal@mcn.example", "role": "caller", "password": "long-password-1", "all_workspaces": True}, headers=ha)
    store = c.app.state.store
    boom = lambda message: {"status": "failed", "error": "provider down"}
    for _ in range(2):
        assert magic_link.request_link(store, "cal@mcn.example", "http://x", boom)["status"] == "failed"
    alerts = [n for n in store.read("notifications") if n.get("kind") == "alert:magic_link_failed"]
    assert len(alerts) == 1 and alerts[0]["to"] == SUPER and "provider down" in alerts[0]["text"] and "cal@mcn.example" in alerts[0]["text"]
    assert "Owners, admins and super admins never get one" in magic_link.GENERIC
