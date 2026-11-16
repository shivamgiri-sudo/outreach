"""Re-verify stale addresses (app/email_gate.py fresh-check rule, app/email_recheck.py, routes): the 90-day boundary, the
setting, never-checked leads, later sequence steps, the owner's count and "Re-check now (max 500)". No network."""
from datetime import datetime, timedelta, timezone

from app import email_gate, routes_email_gate
from tests.ownership_helpers import rig
from tests.test_email_gate import T0, ZB, Sender, Verifier, lead_of, live_run, make

NOW = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)


def dated(email, status, days_ago, now=NOW):
    return {"email": email, "status": status, "syntax_ok": True, "at": (now - timedelta(days=days_ago)).isoformat(timespec="seconds")}


def lead(check=None, sent=(0,)):
    out = {"email": "a@x.in", "stage": "contacted", "sequence": {"sent_days": list(sent), "stopped": None}}
    return {**out, "email_check": check} if check else out


def test_90_day_boundary_and_undated_checks():
    assert not email_gate.is_stale(lead(dated("a@x.in", "valid", 90)), 90, NOW)
    assert email_gate.is_stale(lead(dated("a@x.in", "valid", 91)), 90, NOW)
    assert email_gate.is_stale(lead(), 90, NOW)                                         # never checked
    assert email_gate.is_stale(lead(dated("old@x.in", "valid", 1)), 90, NOW)            # made for another address
    undated = lead({"email": "a@x.in", "status": "valid"})
    assert not email_gate.is_stale(undated, 90, NOW) and email_gate.is_stale(undated, 90, NOW, undated=True)


def test_first_email_waits_for_a_fresh_check_and_setting_off_sends():
    old = lead(dated("a@x.in", "valid", 120), sent=())
    assert email_gate.decision(old, True, stale_days=90, now=NOW) == ("check", email_gate.STALE_NOTE)
    assert email_gate.decision(old, False, stale_days=90, now=NOW)[0] == "hold"
    assert email_gate.decision(old, True, stale_days=None, now=NOW)[0] == "send"         # rule switched off
    assert email_gate.decision(lead(dated("a@x.in", "valid", 30), sent=()), True, stale_days=90, now=NOW)[0] == "send"
    assert email_gate.decision(lead(dated("a@x.in", "invalid", 400), sent=()), True, stale_days=90, now=NOW)[0] == "block"


def test_later_steps_wait_only_when_a_verifier_can_check():
    gate = {"ready": True, "stale_days": 90}
    assert email_gate.needs_recheck(lead(dated("a@x.in", "valid", 100)), gate, NOW)
    assert email_gate.needs_recheck(lead(), gate, NOW)                                  # never checked
    assert not email_gate.needs_recheck(lead(dated("a@x.in", "valid", 10)), gate, NOW)
    assert not email_gate.needs_recheck(lead(dated("a@x.in", "valid", 100)), {**gate, "ready": False}, NOW)
    assert not email_gate.needs_recheck(lead(dated("a@x.in", "valid", 100)), {**gate, "stale_days": None}, NOW)


def test_settings_default_on_90_and_clamped(tmp_path):
    c, store = make(tmp_path, n=1)
    cfg = email_gate.settings_of(store)
    assert (cfg["recheck_enabled"], cfg["recheck_days"]) == (True, 90)
    store.update("settings", lambda s: {**s, "email_gate": {"enabled": True, "recheck_days": 3}})
    assert email_gate.settings_of(store)["recheck_days"] == email_gate.RECHECK_MIN_DAYS
    assert email_gate.for_run(store, True)["stale_days"] == email_gate.RECHECK_MIN_DAYS


def test_runner_holds_a_stale_later_step_then_sends_after_the_check(tmp_path):
    v = Verifier()
    c, store = make(tmp_path, n=1, verifier=True, v=v)
    lid = lead_of(store)["id"]
    store.update_lead(lid, lambda l: {**l, "email_check": dated(l["email"], "valid", 1, T0)})
    sender = Sender()
    live_run(store, sender)
    assert sender.to == ["p0@co0.in"]                                                   # first email: fresh check
    later = T0 + timedelta(days=95)                                                     # the check is now 96 days old
    store.update_lead(lid, lambda l: {**l, "email_check": dated(l["email"], "valid", 96, later)})
    out = live_run(store, sender, later)
    assert out.get("held") == 1 and sender.to == ["p0@co0.in"]
    assert lead_of(store)["sequence"]["blocked"] == email_gate.STALE_NOTE
    assert email_gate.check_pending(store, {"get": v.get}, now=later)["checked"] == 1    # the timer re-checks it
    store.update_lead(lid, lambda l: {**l, "email_check": {**l["email_check"], "at": later.isoformat()}})
    live_run(store, sender, later + timedelta(minutes=20))
    assert sender.to == ["p0@co0.in", "p0@co0.in"] and "blocked" not in lead_of(store)["sequence"]


def test_rule_off_keeps_old_behaviour_for_later_steps(tmp_path):
    c, store = make(tmp_path, n=1, verifier=True)
    lid = lead_of(store)["id"]
    store.update_lead(lid, lambda l: {**l, "email_check": dated(l["email"], "valid", 1, T0)})
    sender = Sender()
    live_run(store, sender)
    store.update("settings", lambda s: {**s, "email_gate": {**s["email_gate"], "recheck_enabled": False}})
    later = T0 + timedelta(days=95)
    store.update_lead(lid, lambda l: {**l, "email_check": dated(l["email"], "valid", 200, later)})
    live_run(store, sender, later)
    assert sender.to == ["p0@co0.in", "p0@co0.in"]


def test_owner_count_and_recheck_now_with_the_builtin_checker(tmp_path, monkeypatch):
    monkeypatch.setattr(routes_email_gate, "BACKGROUND", False)
    r = rig(tmp_path)
    ids = [l["id"] for l in r.store.read("leads")]
    r.store.update_lead(ids[0], lambda l: {**l, "email_check": dated(l["email"], "valid", 2, datetime.now(timezone.utc))})
    body = r.c.get("/api/email-gate", headers=r.h["owner"]).json()
    assert (body["recheck_enabled"], body["recheck_days"]) == (True, 90)
    with_email = [l for l in r.store.read("leads") if l.get("email") and l.get("stage") not in ("unsubscribed", "disqualified")]
    assert body["counts"]["stale"] == len(with_email) - 1
    assert r.c.post("/api/email-gate/recheck-stale", headers=r.h["priya"]).status_code == 403
    out = r.c.post("/api/email-gate/recheck-stale", headers=r.h["owner"]).json()
    assert out["status"] == "done" and out["result"]["checked"] == len(with_email) - 1 and out["result"]["error"] == ""
    assert r.c.get("/api/email-gate", headers=r.h["owner"]).json()["counts"]["stale"] == 0
    assert r.c.get("/api/email-gate/recheck-stale", headers=r.h["owner"]).json()["status"] == "done"


def test_recheck_now_is_capped_and_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(email_gate, "RECHECK_MAX", 1)
    monkeypatch.setattr(routes_email_gate, "BACKGROUND", False)
    r = rig(tmp_path)
    out = r.c.post("/api/email-gate/recheck-stale", headers=r.h["adm2"]).json()          # the second workspace: one lead
    assert out["result"]["checked"] == 1
    checked = [l for l in r.store.read("leads") if (l.get("email_check") or {}).get("at")]
    assert len(checked) == 1 and checked[0].get("workspace") not in (None, "", "main")


def test_save_settings_keeps_and_changes_the_rule(tmp_path):
    r = rig(tmp_path)
    put = lambda body: r.c.put("/api/email-gate", json=body, headers=r.h["owner"])
    assert put({"enabled": True, "recheck_days": 30}).json()["recheck_days"] == 30
    assert put({"enabled": True}).json()["recheck_days"] == 30                          # not sent: kept
    assert put({"enabled": True, "recheck_enabled": False}).json()["recheck_enabled"] is False
    assert put({"enabled": True, "recheck_days": 2}).status_code == 422
    assert r.c.put("/api/email-gate", json={"enabled": True, "recheck_days": 60}, headers=r.h["adm"]).status_code == 403
    assert ZB                                                                           # the paid verifier fixture stays importable
