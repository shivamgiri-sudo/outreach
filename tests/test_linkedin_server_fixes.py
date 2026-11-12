"""Server-side fixes from the LinkedIn audit that the audit tests do not cover: wrong-send stop, lease, claim rules,
old-agent compatibility, why-not-sent, config days, lead merge keys."""
import json
from datetime import timedelta

from app import linkedin_agent as agent, linkedin_guard as guard, linkedin_seq as li, linkedin_why as why
from app.lead_merge import _li_key
from tests.test_audit_ops_linkedin import ACCT, _lead, one  # noqa: F401  (fixture)
from tests.test_extension_api import bearer, URL


def test_old_agent_sent_with_only_step_still_works_and_is_unverified(one):
    c, store, acme, h, _ = one
    assert c.post(f"/api/extension/leads/{acme}/sent", json={"step": 1}, headers=h).status_code == 200
    sent = _lead(store, acme)["linkedin_sends"][-1] if _lead(store, acme).get("linkedin_sends") else None
    row = agent.log_rows(store)[-1]
    assert row["kind"] == "connect" and row["step"] == 1 and not row.get("mismatch")
    assert sent and sent["unverified"] is True and sent["account"] == ACCT          # nothing observed: recorded as 'not verified', not rejected
    assert not agent.paused(store)


def test_wrong_person_on_sent_pauses_account_stops_lead_and_alerts(one):
    c, store, acme, h, _ = one
    n0 = len(store.read("notifications"))
    r = c.post(f"/api/extension/leads/{acme}/sent", json={"step": 1, "observed_name": "Meera Rao", "observed_url": "https://www.linkedin.com/in/meera-rao"}, headers=h)
    assert r.status_code == 409
    lead = _lead(store, acme)
    assert agent.paused(store).get(ACCT, {}).get("wrong_send")
    assert lead["linkedin_seq"]["status"] == "stopped" and lead["linkedin_seq"]["stopped"] == "wrong send flagged"
    assert 1 not in lead["linkedin_seq"]["done"] and lead.get("linkedin_wrong_send")
    assert len(store.read("notifications")) > n0
    row = agent.log_rows(store)[-1]
    assert row["mismatch"] is True and "profile shows" in row["mismatch_why"]
    assert c.post(f"/api/extension/leads/{acme}/claim", headers=h).status_code == 409


def test_verified_send_keeps_text_and_what_was_seen(one):
    c, store, acme, h, _ = one
    name = _lead(store, acme)["contact_name"]
    r = c.post(f"/api/extension/leads/{acme}/sent", json={"step": 1, "text_sent": "hello", "observed_name": f"{name} (He/Him)", "observed_url": URL}, headers=h)
    assert r.status_code == 200 and r.json()["verified"] is True
    sent = _lead(store, acme)["linkedin_sends"][-1]
    assert sent["text_sent"] == "hello" and sent["account"] == ACCT and not sent.get("mismatch")


def test_sent_retry_is_refused_and_counted_once(one):
    c, store, acme, h, _ = one
    assert c.post(f"/api/extension/leads/{acme}/sent", json={"step": 1}, headers=h).status_code == 200
    assert c.post(f"/api/extension/leads/{acme}/sent", json={"step": 1}, headers=h).status_code == 409
    assert agent.control(store, account=ACCT)["counts"]["connect_today"] == 1


def test_sent_then_event_is_one_counted_row(one):
    c, store, acme, h, _ = one
    c.post(f"/api/extension/leads/{acme}/sent", json={"step": 1}, headers=h)
    c.post("/api/extension/agent/event", json={"kind": "connect", "lead_id": acme, "verified": True, "step": 1}, headers=h)
    rows = [r for r in agent.log_rows(store) if r["kind"] == "connect"]
    assert len(rows) == 1 and rows[0]["verified"] is True


def test_claim_needs_a_linkedin_account(one):
    c, store, acme, h, _ = one
    bare = {k: v for k, v in h.items() if k.lower() != "x-linkedin-account"}
    assert c.post(f"/api/extension/leads/{acme}/claim", headers=bare).status_code == 409


def test_two_agent_windows_cannot_both_claim_one_person(one):
    c, store, acme, h, _ = one
    assert c.post(f"/api/extension/leads/{acme}/claim", headers={**h, "X-Agent-Instance": "win-1"}).status_code == 200
    agent.record(store, {"kind": "lookup", "lead_id": "z", "account": ACCT}, now=li.now_utc() - timedelta(hours=1))
    assert c.post(f"/api/extension/leads/{acme}/claim", headers={**h, "X-Agent-Instance": "win-2"}).status_code == 409
    assert c.post(f"/api/extension/leads/{acme}/claim", headers={**h, "X-Agent-Instance": "win-1"}).status_code == 200      # the same window may retry
    assert guard.lease_busy(_lead(store, acme), "win-2")
    guard.clear(store, acme)
    assert not guard.lease_busy(_lead(store, acme), "win-2")


def test_dry_profile_writes_nothing_and_portal_shows_dry(one):
    c, store, acme, h, owner = one
    before = json.dumps(_lead(store, acme), sort_keys=True)
    assert c.post(f"/api/extension/leads/{acme}/profile", json={"open_profile": True, "degree": "2nd", "dry": True}, headers=h).json()["dry"] is True
    assert json.dumps(_lead(store, acme), sort_keys=True) == before
    c.post("/api/extension/agent/event", json={"kind": "started", "note": "started in dry-run mode"}, headers=h)
    c.get("/api/extension/agent", headers=h)                                       # heartbeat keeps it dry
    assert c.get("/api/linkedin-agent", headers=bearer(owner)).json()["dry"] is True


def test_replied_and_profile_refuse_another_linkedin_account(one):
    c, store, acme, h, _ = one
    c.post(f"/api/extension/leads/{acme}/claim", headers=h)
    other = {**h, "X-LinkedIn-Account": "someone-else"}
    assert c.post(f"/api/extension/leads/{acme}/replied", json={"text": "x"}, headers=other).status_code == 409
    assert c.post(f"/api/extension/leads/{acme}/profile", json={"degree": "1st"}, headers=other).status_code == 409
    assert _lead(store, acme)["linkedin_seq"]["status"] != "stopped"


def test_manual_pause_of_one_account(one):
    c, store, acme, h, owner = one
    c.get("/api/extension/agent", headers=h)
    assert c.put("/api/linkedin-agent", json={"pause_account": ACCT}, headers=bearer(owner)).status_code == 200
    assert agent.paused(store).get(ACCT)
    assert c.post(f"/api/extension/leads/{acme}/claim", headers=h).status_code == 409


def test_send_rows_survive_a_burst_of_other_rows(one):
    c, store, acme, h, _ = one
    agent.record(store, {"kind": "connect", "lead_id": "keep", "account": ACCT})
    for i in range(500):
        agent.record(store, {"kind": "lookup", "lead_id": f"x{i}", "account": ACCT})
    assert any(r["lead_id"] == "keep" for r in agent.log_rows(store))


def test_unconfirmed_send_alert_is_rate_limited(one):
    c, store, acme, h, _ = one
    n0 = len(store.read("notifications"))
    for _ in range(3):
        c.post("/api/extension/agent/event", json={"kind": "connect", "lead_id": acme, "verified": False}, headers=h)
    assert len(store.read("notifications")) == n0 + 1


def test_why_lists_reasons_and_stale_invites(one):
    c, store, acme, h, owner = one
    li.mark_sent(store, acme, 1, now=li.now_utc() - timedelta(days=40))
    codes = {r["code"] for r in why.reasons(store, _lead(store, acme))}
    assert "stale_invite" in codes
    body = c.get(f"/api/linkedin-agent/lead/{acme}/why", headers=bearer(owner)).json()
    assert any(r["code"] == "stale_invite" for r in body["reasons"])
    assert acme in [s["lead_id"] for s in c.get("/api/linkedin-agent/stuck", headers=bearer(owner)).json()["stuck"]]


def test_config_days_default_and_owner_set_values_untouched(one):
    c, store, acme, h, _ = one
    store.update("settings", lambda s: {**s, "linkedin_agent": {"start_hour": 8, "end_hour": 20, "weekdays_only": True, "enabled": True}})
    cfg = agent.config(store)
    assert (cfg["start_hour"], cfg["end_hour"], cfg["days"]) == (8, 20, [0, 1, 2, 3, 4])          # the owner's saved values stay
    store.update("settings", lambda s: {**s, "linkedin_agent": {}})
    assert agent.config(store)["days"] == [0, 1, 2, 3, 4, 5]


def test_watch_no_offline_alert_outside_hours(one):
    from app import linkedin_watch as watch
    c, store, acme, h, _ = one
    agent.save_config(store, {"start_hour": 10, "end_hour": 11})
    n0 = len(store.read("notifications"))
    late = li.now_utc().replace(hour=20, minute=0)                    # 01:30 IST, outside 10-11
    watch.tick(store, late)
    assert len(store.read("notifications")) == n0


def test_li_key_ignores_scheme_www_locale_and_query():
    assert _li_key("http://in.linkedin.com/in/Rahul-A/?utm=1") == _li_key("https://www.linkedin.com/in/rahul-a")
