"""Verify-before-the-first-email gate (app/email_gate.py): decision table, runner integration, checks, routes."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import campaign_sources as src, email_gate
from app.autorun import start_background, tick
from app.main import create_app
from app.sequence_runner import run_due
from tests.flow_helpers import connect_email, go_live
from tests.ownership_helpers import rig

T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)      # Tuesday 10:30 IST
ZB = {"email_verifier": {"mode": "api", "values": {"provider": "zerobounce", "api_key": "zk"}}}


class Sender:
    def __init__(self):
        self.to = []

    def __call__(self, message):
        self.to.append(message["to"])
        return {"status": "sent", "provider": "fake"}


class Verifier:
    """A fake ZeroBounce 'get' transport: verdicts by address; counts calls."""

    def __init__(self, verdicts=None, error=None):
        self.verdicts, self.error, self.calls = verdicts or {}, error, []

    def get(self, url, headers):
        self.calls.append(url)
        if self.error:
            return {"error": self.error}
        email = url.split("email=")[1].split("&")[0].replace("%40", "@")
        return {"status": self.verdicts.get(email, "valid")}


def csv_for(n):
    rows = ["company,contact_name,email,website,open_roles,company_size,country"]
    rows += [f"Co{i},Person {i},p{i}@co{i}.in,https://co{i}.in,5,100,IN" for i in range(n)]
    return "\n".join(rows) + "\n"


def make(tmp_path, n=2, gate=True, verifier=False, v=None):
    c = TestClient(create_app(data_file=tmp_path / "d.json", campaign_transports={"get": (v or Verifier()).get}))
    c.post("/api/leads/import", json={"csv": csv_for(n), "source": "apollo"})
    store = c.app.state.store
    connect_email(c)
    go_live(c)
    store.update("settings", lambda s: {**s, "email_gate": {"enabled": gate}})
    if verifier:
        store.update("integrations", lambda i: {**i, **ZB})
    for lead in c.get("/api/leads").json():
        assert c.post(f"/api/leads/{lead['id']}/enroll").status_code == 200
    return c, store


def lead_of(store, n=0):
    return next(l for l in store.read("leads") if l["company"] == f"Co{n}")


def live_run(store, sender, at=T0):
    return run_due(store, at, "http://x", sender, ignore_hours=True)


def check(email, status, approved=False):
    c = {"email": email, "status": status, "syntax_ok": True}
    return {**c, "approved": True} if approved else c


# ---------- decision table ----------
@pytest.mark.parametrize("lead,ready,expected", [
    ({"email": "a@x.in", "email_check": check("a@x.in", "valid")}, False, "send"),
    ({"email": "info@x.in", "email_check": check("info@x.in", "valid")}, False, "send"),        # role address, valid
    ({"email": "a@x.in", "email_check": check("a@x.in", "invalid")}, True, "block"),
    ({"email": "a@x.in", "email_check": check("a@x.in", "risky")}, True, "hold"),
    ({"email": "a@x.in", "email_check": check("a@x.in", "risky", approved=True)}, True, "send"),
    ({"email": "a@x.in", "email_check": check("a@x.in", "unverified")}, True, "check"),
    ({"email": "a@x.in"}, True, "check"),
    ({"email": "a@x.in", "email_check": check("old@x.in", "valid")}, True, "check"),             # a check for another address
    ({"email": "a@x.in", "email_check": check("old@x.in", "valid")}, False, "hold"),
    ({"email": "a@x.in"}, False, "hold"),
])
def test_decision_table(lead, ready, expected):
    what, why = email_gate.decision(lead, ready)
    assert what == expected and why
    if expected == "hold" and "email_check" not in lead:
        assert why == "no email verifier is connected"


def test_approve_risky_setting_lets_risky_through():
    assert email_gate.decision({"email": "a@x.in", "email_check": check("a@x.in", "risky")}, True, approve_risky=True)[0] == "send"


def test_production_default_is_on_without_the_env_var(monkeypatch, tmp_path):
    monkeypatch.delenv("OUTREACH_EMAIL_GATE_DEFAULT", raising=False)
    c, store = make(tmp_path, gate=True)
    store.update("settings", lambda s: {k: v for k, v in s.items() if k != "email_gate"})        # nothing saved: the default decides
    assert email_gate.settings_of(store)["enabled"] is True
    sender = Sender()
    assert live_run(store, sender)["sent"] == 0 and sender.to == []
    monkeypatch.setenv("OUTREACH_EMAIL_GATE_DEFAULT", "0")
    assert email_gate.settings_of(store)["enabled"] is False


# ---------- runner ----------
def test_never_checked_lead_is_held_in_live_mode_and_gets_a_plain_note(tmp_path):
    c, store = make(tmp_path)
    sender = Sender()
    out = live_run(store, sender)
    assert out["sent"] == 0 and out["held"] == 2 and sender.to == []
    assert lead_of(store)["sequence"]["blocked"] == "no email verifier is connected"
    assert live_run(store, sender, T0 + timedelta(minutes=20))["held"] == 2           # looked at again every run


def test_practice_run_is_not_gated(tmp_path):
    c, store = make(tmp_path)
    store.update("settings", lambda s: {**s, "live_sending": False})
    assert live_run(store, Sender())["mode"] == "practice"
    assert lead_of(store)["sequence"]["practice_days"] == [0]


def test_toggle_off_restores_todays_behaviour(tmp_path):
    c, store = make(tmp_path, gate=False)
    sender = Sender()
    assert live_run(store, sender)["sent"] == 2 and "held" not in live_run(store, sender)


def test_valid_and_invalid_checks(tmp_path):
    c, store = make(tmp_path)
    for n, status in ((0, "valid"), (1, "invalid")):
        store.update_lead(lead_of(store, n)["id"], lambda l, s=status: {**l, "email_check": check(l["email"], s)})
    sender = Sender()
    out = live_run(store, sender)
    assert sender.to == ["p0@co0.in"] and out["blocked"] == 1


def test_mid_sequence_lead_keeps_its_sequence(tmp_path):
    c, store = make(tmp_path)
    lid = lead_of(store)["id"]
    store.update_lead(lid, lambda l: {**l, "email_check": check(l["email"], "valid")})
    sender = Sender()
    live_run(store, sender)
    assert lead_of(store)["sequence"]["sent_days"] == [0]
    store.update_lead(lid, lambda l: {**l, "email_check": check(l["email"], "risky")})       # later turns doubtful: no sudden stop
    later = T0 + timedelta(days=5)
    live_run(store, sender, later)
    assert sender.to.count("p0@co0.in") == 2


def test_held_lead_is_sent_once_valid_and_note_is_cleared(tmp_path):
    c, store = make(tmp_path, n=1)
    sender = Sender()
    live_run(store, sender)
    assert lead_of(store)["sequence"]["blocked"]
    store.update_lead(lead_of(store)["id"], lambda l: {**l, "email_check": check(l["email"], "valid")})
    assert live_run(store, sender, T0 + timedelta(minutes=20))["sent"] == 1
    assert "blocked" not in lead_of(store)["sequence"]


def test_note_cleared_even_if_the_send_waits(tmp_path):
    c, store = make(tmp_path, n=1)
    live_run(store, Sender())
    store.update_lead(lead_of(store)["id"], lambda l: {**l, "email_check": check(l["email"], "valid")})
    live_run(store, Sender(), T0 + timedelta(minutes=20))
    assert "blocked" not in lead_of(store)["sequence"]


def test_held_leads_do_not_use_the_cap(tmp_path):
    c, store = make(tmp_path, n=8)
    for n in (6, 7):                                                  # only the last two are checked; the rest are held
        store.update_lead(lead_of(store, n)["id"], lambda l: {**l, "email_check": check(l["email"], "valid")})
    sender = Sender()
    out = live_run(store, sender)                                     # per-run cap is 5: held leads in front must not eat it
    assert out["sent"] == 2 and out["held"] == 6


def test_held_note_is_written_only_when_it_changes(tmp_path, monkeypatch):
    c, store = make(tmp_path, n=1)
    live_run(store, Sender())
    from app import sequence_runner
    calls = []
    monkeypatch.setattr(sequence_runner, "_set_sequence", lambda *a, **k: calls.append(k))
    live_run(store, Sender(), T0 + timedelta(minutes=20))
    assert calls == []


# ---------- checking ----------
def test_check_pending_writes_the_campaign_shape_and_lets_leads_go(tmp_path):
    v = Verifier({"p1@co1.in": "invalid"})
    c, store = make(tmp_path, verifier=True, v=v)
    out = email_gate.check_pending(store, {"get": v.get})
    assert out["checked"] == 2 and out["valid"] == 1 and out["invalid"] == 1 and out["error"] == ""
    ec = lead_of(store)["email_check"]
    assert set(ec) == {"email", "syntax_ok", "business", "role", "provider", "at", "status"} and ec["status"] == "valid"
    assert ec["provider"] == "zerobounce" and ec["email"] == "p0@co0.in"
    sender = Sender()
    live_run(store, sender)
    assert sender.to == ["p0@co0.in"]
    assert email_gate.check_pending(store, {"get": v.get})["checked"] == 0           # already checked: no second paid call


def test_check_pending_respects_limit_and_skips_started_leads(tmp_path):
    v = Verifier()
    c, store = make(tmp_path, n=5, verifier=True, v=v)
    assert email_gate.check_pending(store, {"get": v.get}, limit=2)["checked"] == 2
    assert len(v.calls) == 2
    store.update_lead(lead_of(store, 4)["id"], lambda l: {**l, "sequence": {**l["sequence"], "sent_days": [0]}})
    assert email_gate.check_pending(store, {"get": v.get})["checked"] == 2           # 5 - 2 done - 1 already emailed


def test_rejected_key_stops_with_a_plain_message_and_does_not_loop(tmp_path):
    v = Verifier(error="Invalid API key")
    c, store = make(tmp_path, n=4, verifier=True, v=v)
    out = email_gate.check_pending(store, {"get": v.get})
    assert len(v.calls) == 1 and "Invalid API key" in out["error"] and out["error"].startswith("Stopped")
    assert store.read("settings")["email_gate_status"]["error"] == out["error"]
    assert lead_of(store).get("email_check") is None


def test_no_verifier_gives_a_message_not_a_crash(tmp_path):
    c, store = make(tmp_path)
    assert email_gate.check_pending(store, {"get": Verifier().get})["error"] == "no email verifier is connected"


# ---------- background hook ----------
def test_gate_runner_is_additive_and_runs_before_the_sends(tmp_path):
    c, store = make(tmp_path)
    stop = start_background(store, Sender(), interval_seconds=10 ** 6)      # works without a gate_runner
    stop.set()
    seen = []
    sender = Sender()
    out = tick(store, T0, sender, gate_runner=lambda: seen.append(1) or {"checked": 0})
    assert seen == [1] and out["email_checks"] == {"checked": 0}
    out = tick(store, T0, sender, gate_runner=lambda: 1 / 0)               # a failing check never stops the sends
    assert "email_checks" not in out and "sent" in out
    assert "email_checks" not in tick(store, T0, sender)


# ---------- routes ----------
def test_routes_roles_scope_and_no_personal_data(tmp_path):
    r = rig(tmp_path)
    r.store.update("settings", lambda s: {**s, "email_gate": {"enabled": True}})
    for who in ("owner", "adm", "priya", "view"):
        assert r.c.get("/api/email-gate", headers=r.h[who]).status_code == 200
    body = r.c.get("/api/email-gate", headers=r.h["owner"]).json()
    assert body["enabled"] is True and body["verifier"] == {"connected": False, "provider": "", "supported": False}
    assert set(body["counts"]) == {"waiting_for_check", "valid", "invalid", "risky", "unverified", "held"}
    assert "@" not in str(body)
    assert body["counts"]["unverified"] == 4                                                          # three in Main, one in the second workspace
    adm = r.c.get("/api/email-gate", headers=r.h["adm2"]).json()["counts"]["unverified"]
    assert adm == 1                                                                                   # the second workspace has one lead
    assert r.c.put("/api/email-gate", json={"enabled": False}, headers=r.h["adm"]).status_code == 403
    assert r.c.put("/api/email-gate", json={"enabled": False}, headers=r.h["priya"]).status_code == 403
    assert r.c.put("/api/email-gate", json={"enabled": False, "approve_risky": True}, headers=r.h["owner"]).json()["enabled"] is False
    assert email_gate.settings_of(r.store) == {"enabled": False, "approve_risky": True}
    assert r.c.post("/api/email-gate/check-now", headers=r.h["priya"]).status_code == 403
    assert r.c.post("/api/email-gate/check-now", headers=r.h["view"]).status_code == 403
    out = r.c.post("/api/email-gate/check-now", headers=r.h["adm"])
    assert out.status_code == 200 and out.json()["error"] == "no email verifier is connected"


def test_check_now_runs_the_checks(tmp_path):
    v = Verifier()
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password="admin-pass-123", campaign_transports={"get": v.get}),
                   client=("203.0.113.9", 50000))
    tok = c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    c.post("/api/leads/import", json={"csv": csv_for(3), "source": "apollo"}, headers=h)
    store = c.app.state.store
    store.update("integrations", lambda i: {**i, **ZB})
    for lead in c.get("/api/leads", headers=h).json():
        c.post(f"/api/leads/{lead['id']}/enroll", headers=h)
    out = c.post("/api/email-gate/check-now", headers=h).json()
    assert out["checked"] == 3 and out["valid"] == 3
    body = c.get("/api/email-gate", headers=h).json()
    assert body["counts"]["valid"] == 3 and body["verifier"]["supported"] and body["last_run"]


def test_approve_risky_refuses_invalid_foreign_and_unneeded(tmp_path):
    r = rig(tmp_path)
    acme, beta, other = (r.lead["Acme"]["id"], r.lead["Beta Ltd"]["id"], r.other["id"])
    r.store.update_lead(acme, lambda l: {**l, "email_check": check(l["email"], "risky")})
    r.store.update_lead(beta, lambda l: {**l, "email_check": check(l["email"], "invalid")})
    r.store.update_lead(other, lambda l: {**l, "email_check": check(l["email"], "risky")})
    url = lambda i: f"/api/leads/{i}/email-approve"
    assert r.c.post(url(acme), headers=r.h["priya"]).status_code == 403                 # a caller cannot
    assert r.c.post(url(acme), headers=r.h["view"]).status_code == 403
    bad = r.c.post(url(beta), headers=r.h["adm"])
    assert bad.status_code == 409 and "failed a mailbox check" in bad.json()["detail"]
    assert r.c.post(url(other), headers=r.h["adm"]).status_code == 404                  # another workspace's lead
    assert r.c.post(url(r.lead["Cora Inc"]["id"]), headers=r.h["adm"]).status_code == 409   # never checked: nothing to approve
    ok = r.c.post(url(acme), headers=r.h["adm"])
    assert ok.status_code == 200
    got = next(l for l in r.store.read("leads") if l["id"] == acme)
    assert got["email_check"]["approved"] is True and got["email_check"]["approved_by"]
    assert any("approved" in e["text"] for e in got["timeline"])
    assert any(a["action"] == "email_gate.approve" for a in r.store.read("audit"))
    assert email_gate.decision(got, True)[0] == "send"
