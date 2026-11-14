"""Built-in email checker (app/email_checker.py): every layer, history rules, precedence over paid verifiers, and the gate end to end.
DNS and the SMTP step are injected: no network."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import email_checker as ec, email_gate
from app.email_checker_data import DISPOSABLE, POPULAR
from tests.test_email_gate import ZB, Sender, Verifier, live_run, lead_of, make

NOW = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)
OK = lambda domain: "ok"
H = lambda **kw: {"bounced": set(kw.get("bounced", ())), "delivered": set(kw.get("delivered", ())), "domains": kw.get("domains", {})}


def run(email, history=None, dns=OK, **kw):
    return ec.check(email, history or H(), dns=dns, **kw)


# ---------- layers ----------
def test_good_company_address_is_valid():
    r = run("priya@acme.in")
    assert r["status"] == "valid" and r["provider"] == "builtin" and r["reasons"] == []


@pytest.mark.parametrize("bad", ["", "nope", "a@b", "a b@c.com", "@x.com"])
def test_bad_syntax_is_invalid(bad):
    r = run(bad)
    assert r["status"] == "invalid" and "not a valid email" in r["reasons"][0]


def test_disposable_list_has_real_services_and_is_a_data_module():
    assert len(DISPOSABLE) >= 300
    for d in ("mailinator.com", "guerrillamail.com", "yopmail.com", "10minutemail.com", "temp-mail.org", "trashmail.com"):
        assert d in DISPOSABLE
    assert not DISPOSABLE & set(POPULAR) and "gmail.com" not in DISPOSABLE
    r = run("x@mailinator.com")
    assert r["status"] == "invalid" and "throwaway" in r["reasons"][0]


@pytest.mark.parametrize("typed,fix", [
    ("gmial.com", "gmail.com"), ("gmai.com", "gmail.com"), ("yahooo.com", "yahoo.com"), ("hotmial.com", "hotmail.com"),
    ("outlok.com", "outlook.com"), ("rediffmal.com", "rediffmail.com"), ("gmail.con", "gmail.com"),
    ("gmaiil.com", "gmail.com"), ("outlookk.com", "outlook.com"), ("rediffmaill.com", "rediffmail.com"),   # by edit distance, not the table
])
def test_typo_domains_are_invalid_with_a_suggestion(typed, fix):
    r = run(f"ravi@{typed}")
    assert r["status"] == "invalid" and r["reasons"] == [f"looks like a typo of {fix}"] and r["suggest"] == f"ravi@{fix}"


@pytest.mark.parametrize("domain", ["gmail.com", "mi.com", "life.com", "acme.in", "tcs.com", "mail.com", "zoho.in", "icloud.com"])
def test_real_domains_are_not_typos(domain):
    assert ec.typo_of(domain) is None


def test_edit_distance():
    assert ec.edit_distance("gmail.com", "gmial.com") == 2 and ec.edit_distance("a", "a") == 0 and ec.edit_distance("abc", "abd") == 1


def test_domain_dns_problems_invalid_unknown_is_unverified():
    assert run("a@nothere.in", dns=lambda d: "no_domain")["status"] == "invalid"
    assert "no mail server" in run("a@x.in", dns=lambda d: "no_mail")["reasons"][0]
    r = run("a@slow.in", dns=lambda d: "unknown")
    assert r["status"] == "unverified" and "tried again" in r["reasons"][-1]          # never blocks, never invalid


def test_unknown_dns_does_not_hide_a_risky_role():
    assert run("info@slow.in", dns=lambda d: "unknown")["status"] == "risky"


def test_role_address_is_risky_unless_delivered_before():
    for local in ("info", "hr", "admin", "sales"):
        r = run(f"{local}@acme.in")
        assert r["status"] == "risky" and "shared mailbox" in r["reasons"][0]
    r = run("info@acme.in", H(delivered=["info@acme.in"]))
    assert r["status"] == "valid" and r["confidence"] == "high"


def test_history_address_bounced_is_invalid_even_if_role_or_delivered():
    assert run("a@acme.in", H(bounced=["a@acme.in"]))["status"] == "invalid"
    assert run("a@acme.in", H(bounced=["a@acme.in"], delivered=["a@acme.in"]))["status"] == "invalid"


def test_domain_with_three_bounces_and_no_delivery_is_risky():
    r = run("new@bad.in", H(domains={"bad.in": {"bounces": 3, "delivered": 0}}))
    assert r["status"] == "risky" and "bounced 3 times" in r["reasons"][0]
    assert run("new@bad.in", H(domains={"bad.in": {"bounces": 2, "delivered": 0}}))["status"] == "valid"
    assert run("new@bad.in", H(domains={"bad.in": {"bounces": 3, "delivered": 1}}))["status"] == "valid"


def test_domain_with_deliveries_and_no_bounce_raises_confidence_but_stays_valid():
    r = run("new@good.in", H(domains={"good.in": {"bounces": 0, "delivered": 2}}))
    assert r["status"] == "valid" and r["confidence"] == "high" and "delivered 2 emails" in r["reasons"][0]
    assert "confidence" not in run("new@good.in", H(domains={"good.in": {"bounces": 0, "delivered": 1}}))


def test_good_history_never_rescues_a_bad_layer():
    h = H(domains={"gmial.com": {"bounces": 0, "delivered": 5}, "mailinator.com": {"bounces": 0, "delivered": 5}})
    assert run("a@gmial.com", h)["status"] == "invalid" and run("a@mailinator.com", h)["status"] == "invalid"
    assert run("info@acme.in", H(domains={"acme.in": {"bounces": 0, "delivered": 9}}))["status"] == "risky"


def test_personal_mailbox_reason_only_for_company_leads():
    assert run("ravi@gmail.com")["reasons"] == ["personal mailbox"] and run("ravi@gmail.com")["status"] == "valid"
    assert run("ravi@gmail.com", company_lead=False)["reasons"] == []


def test_smtp_step_only_when_given_and_catch_all_said_honestly():
    assert not any("catch-all" in x for x in run("a@acme.in")["reasons"])               # no SMTP: nothing claimed about catch-all
    assert any("catch-all" in x for x in run("a@acme.in", smtp=lambda e: "catch_all")["reasons"])
    assert run("a@acme.in", smtp=lambda e: "rejected")["status"] == "invalid"
    r = run("a@acme.in", smtp=lambda e: "confirmed")
    assert r["status"] == "valid" and r["confidence"] == "high"
    assert run("a@acme.in", smtp=lambda e: "unknown_blocked")["status"] == "valid"       # not an answer: ignored


def test_smtp_step_is_off_unless_port_25_connects():
    calls = []
    ec._PORT.update(at=0.0, open=False)
    assert ec.smtp_step("me@send.in", open_test=lambda: calls.append(1) or False) is None
    assert ec.smtp_step("me@send.in", open_test=lambda: calls.append(1) or True) is None          # cached "closed" for an hour
    assert len(calls) == 1
    ec._PORT.update(at=0.0, open=False)
    assert ec.smtp_step("", open_test=lambda: True) is None                                        # no sender address: skipped
    ec._PORT.update(at=0.0, open=False)
    assert callable(ec.smtp_step("me@send.in", open_test=lambda: True))
    ec._PORT.update(at=0.0, open=False)
    assert ec.port_25_usable(open_test=lambda: 1 / 0) is False                                     # an error is "closed"
    ec._PORT.update(at=0.0, open=False)


# ---------- history from the app's own data ----------
def test_build_history_from_events_and_outbox(tmp_path):
    c = TestClient(__import__("app.main", fromlist=["create_app"]).create_app(data_file=tmp_path / "d.json"))
    store = c.app.state.store
    old = (NOW - timedelta(days=120)).isoformat()
    new = (NOW - timedelta(days=5)).isoformat()
    store.update("delivery_events", lambda r: [
        {"key": "1", "type": "bounce", "email": "a@bad.in", "at": new}, {"key": "2", "type": "bounce", "email": "b@bad.in", "at": new},
        {"key": "3", "type": "bounce", "email": "c@bad.in", "at": new}, {"key": "4", "type": "bounce", "email": "old@bad.in", "at": old},
        {"key": "5", "type": "delivered", "email": "d@good.in", "at": new, "provider_id": "p1"}, {"key": "6", "type": "complaint", "email": "z@x.in", "at": new}])
    store.update("outbox", lambda r: [{"to": "e@good.in", "delivered_at": new, "provider_id": "p2"}, {"to": "d@good.in", "delivered_at": new, "provider_id": "p1"}])
    h = ec.build_history(store, NOW)
    assert h["bounced"] == {"a@bad.in", "b@bad.in", "c@bad.in", "old@bad.in"} and h["delivered"] == {"d@good.in", "e@good.in"}
    assert h["domains"]["bad.in"] == {"bounces": 3, "delivered": 0}                  # the 120-day-old bounce is outside the window
    assert h["domains"]["good.in"] == {"bounces": 0, "delivered": 2}                 # p1 counted once
    assert run("f@bad.in", h)["status"] == "risky" and run("old@bad.in", h)["status"] == "invalid"


# ---------- gate integration ----------
def builtin(store, on=True):
    store.update("settings", lambda s: {**s, "email_gate": {**s.get("email_gate", {}), "builtin": on}})


def set_email(store, n, email):
    store.update_lead(lead_of(store, n)["id"], lambda l: {**l, "email": email})


def test_builtin_is_off_by_default_and_keeps_the_no_verifier_hold(tmp_path):
    c, store = make(tmp_path)
    assert email_gate.builtin_on(store) is False and email_gate.verifier(store)["connected"] is False
    sender = Sender()
    assert live_run(store, sender)["sent"] == 0 and not sender.to


def test_verifier_object_and_route_say_builtin(tmp_path):
    c, store = make(tmp_path)
    builtin(store)
    info = email_gate.verifier(store)
    assert info["connected"] and info["supported"] and info["builtin"] and info["provider"] == "builtin"
    body = c.get("/api/email-gate").json()
    assert body["verifier"] == {"connected": True, "provider": "builtin", "supported": True, "builtin": True} and body["builtin"] is True
    c.put("/api/email-gate", json={"enabled": True})                  # saving without 'builtin' keeps the choice
    assert email_gate.builtin_on(store) is True
    c.put("/api/email-gate", json={"enabled": True, "builtin": False})
    assert email_gate.builtin_on(store) is False and "builtin" not in c.get("/api/email-gate").json()["verifier"]


def test_paid_verifier_takes_precedence(tmp_path):
    v = Verifier({"p1@co1.in": "invalid"})
    c, store = make(tmp_path, verifier=True, v=v)
    builtin(store)
    info = email_gate.verifier(store)
    assert info["provider"] == "zerobounce" and not info.get("builtin")
    out = email_gate.check_pending(store, {"get": v.get})
    assert out["checked"] == 2 and out["invalid"] == 1 and v.calls and lead_of(store, 1)["email_check"]["provider"] == "zerobounce"
    assert "reasons" not in lead_of(store, 0)["email_check"]


def test_check_writes_the_shape_the_gate_reads(tmp_path):
    c, store = make(tmp_path)
    builtin(store)
    out = email_gate.check_pending(store, {})
    assert out == {"checked": 2, "valid": 2, "invalid": 0, "risky": 0, "unverified": 0, "error": ""}
    chk = lead_of(store, 0)["email_check"]
    assert chk["status"] == "valid" and chk["provider"] == "builtin" and chk["email"] == lead_of(store, 0)["email"]
    assert chk["reasons"] == [] and chk["syntax_ok"] is True and chk["at"] and chk["business"] is True and chk["role"] is False
    assert email_gate._check_status(lead_of(store, 0)) == "valid"
    assert email_gate.counts(store, store.read("leads"))["valid"] == 2


def test_gate_end_to_end_good_sent_typo_blocked_role_held_until_approved(tmp_path):
    c, store = make(tmp_path, n=3)
    builtin(store)
    set_email(store, 1, "boss@gmial.com")
    set_email(store, 2, "info@co2.in")
    sender = Sender()
    email_gate.check_pending(store, {})
    assert lead_of(store, 1)["email_check"]["status"] == "invalid" and lead_of(store, 1)["email_check"]["suggest"] == "boss@gmail.com"
    assert lead_of(store, 2)["email_check"]["status"] == "risky"
    live_run(store, sender)
    assert sender.to == ["p0@co0.in"]                                      # the good one only: typo blocked, role address held
    lead = lead_of(store, 2)
    assert c.post(f"/api/leads/{lead['id']}/email-approve").status_code == 200
    live_run(store, sender, NOW + timedelta(days=1))
    assert "info@co2.in" in sender.to and "boss@gmial.com" not in sender.to


def test_never_checked_lead_with_good_domain_is_checked_by_the_timer_and_sent(tmp_path):
    from app.autorun import tick
    c, store = make(tmp_path)
    builtin(store)
    sender = Sender()
    out = tick(store, NOW, sender=sender, gate_runner=lambda: email_gate.check_pending(store, {}))
    assert out["email_checks"]["valid"] == 2 and sorted(sender.to) == ["p0@co0.in", "p1@co1.in"]


def test_dns_unknown_leaves_lead_unchecked_and_holds_it(tmp_path, monkeypatch):
    from app import email_domain
    monkeypatch.setattr(email_domain, "CHECK", True)
    monkeypatch.setattr(email_domain, "status_of_domain", lambda d: "unknown")
    c, store = make(tmp_path)
    builtin(store)
    out = email_gate.check_pending(store, {})
    assert out["unverified"] == 2 and out["valid"] == 0 and "email_check" not in lead_of(store, 0)
    sender = Sender()
    assert live_run(store, sender)["sent"] == 0


def test_check_now_route_uses_the_builtin_checker(tmp_path):
    c, store = make(tmp_path)
    builtin(store)
    body = c.post("/api/email-gate/check-now").json()
    assert body["checked"] == 2 and body["valid"] == 2 and body["error"] == ""
