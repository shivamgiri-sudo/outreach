"""OPS audit of the LinkedIn agent: audit trail, wrong-send detection, kill switch, counters, tenant isolation, dry run.
A FAILING test here = the bug / gap is present today (written as the behaviour the owner needs)."""
import json
from datetime import timedelta

import pytest

from app import linkedin_agent as agent, linkedin_seq as li
from tests.test_extension_api import bearer, make, pair, URL
from tests.test_linkedin_agent import TUE_11
from tests.test_linkedin_per_account import rig as tenant_rig  # noqa: F401  (fixture)

ACCT = "mcn-sales"


@pytest.fixture()
def one(tmp_path):
    c, owner, acme, globex = make(tmp_path)
    store = c.app.state.store
    agent.save_config(store, {"enabled": True, "weekdays_only": False, "start_hour": 0, "end_hour": 24})
    li.start(store, acme)
    tok = pair(c, owner)["token"]
    h = {**bearer(tok), "X-LinkedIn-Account": ACCT}
    return c, store, acme, h, owner


def _lead(store, lid):
    return next(l for l in store.read("leads") if l["id"] == lid)


# ---- (1) audit trail -------------------------------------------------------------------------------------------------
def test_ops01_sent_stores_exact_text_target_and_observed(one):
    c, store, acme, h, _ = one
    text = c.get("/api/extension/match", params={"profile_url": URL}, headers=h).json()["message"]["text"]
    r = c.post(f"/api/extension/leads/{acme}/sent", json={"step": 1, "text_sent": text, "observed_name": "Ravi Kumar",
                                                           "observed_url": URL, "chat_title": ""}, headers=h)
    assert r.status_code == 200
    tl = [t for t in _lead(store, acme)["timeline"] if "step 1 sent" in t["text"]][-1]
    blob = json.dumps(tl)
    assert text[:40] in blob, "the timeline must keep the exact text that was sent"
    assert "observed" in blob and URL in blob, "the timeline must keep what the agent actually saw (name + url)"


def test_ops02_agent_log_row_names_target_and_url(one):
    c, store, acme, h, _ = one
    c.post("/api/extension/agent/event", json={"kind": "message", "lead_id": acme, "note": "message to Ravi Kumar", "verified": True,
                                               "observed_name": "Someone Else", "observed_url": "https://www.linkedin.com/in/other"}, headers=h)
    row = agent.log_rows(store)[-1]
    assert row.get("observed_name") == "Someone Else" and row.get("target_url"), "log row has no observed/target fields"


def test_ops03_log_is_a_400_row_ring_buffer_across_all_accounts(one):
    c, store, acme, h, _ = one
    for i in range(450):
        agent.record(store, {"kind": "lookup", "lead_id": f"x{i}", "account": ACCT}, now=TUE_11 + timedelta(seconds=i))
    agent.record(store, {"kind": "message", "lead_id": "FIRST", "account": ACCT}, now=TUE_11 - timedelta(hours=1))
    for i in range(450):
        agent.record(store, {"kind": "lookup", "lead_id": f"y{i}", "account": ACCT}, now=TUE_11 + timedelta(minutes=10, seconds=i))
    assert any(r["lead_id"] == "FIRST" for r in agent.log_rows(store)), "a send record vanished from the durable log after 400 rows"


# ---- (2) wrong-send auto-pause ---------------------------------------------------------------------------------------
def test_ops04_mismatch_between_observed_and_lead_name_pauses_the_account(one):
    c, store, acme, h, _ = one
    c.post("/api/extension/agent/event", json={"kind": "message", "lead_id": acme, "note": "message to Ravi Kumar", "verified": True,
                                               "observed_name": "Meera Rao", "chat_title": "Meera Rao"}, headers=h)
    assert agent.paused(store).get(ACCT), "a message whose chat title is another person must auto-pause the account"


# ---- (3) kill switch races -------------------------------------------------------------------------------------------
def test_ops05_claim_refused_while_account_paused(one):
    c, store, acme, h, _ = one
    agent.record(store, {"kind": "error", "fatal": True, "note": "checkpoint", "account": ACCT})
    r = c.post(f"/api/extension/leads/{acme}/claim", headers=h)
    assert r.status_code == 409, "claim (the last server call before the click) ignores the pause"


def test_ops06_claim_refused_when_owner_switch_is_off(one):
    c, store, acme, h, _ = one
    agent.save_config(store, {"enabled": False})
    assert c.post(f"/api/extension/leads/{acme}/claim", headers=h).status_code == 409


def test_ops07_claim_refused_when_platform_kill_is_on(one):
    c, store, acme, h, owner = one
    assert c.put("/api/linkedin-agent/kill", json={"on": True}, headers=bearer(owner)).status_code == 200
    assert c.post(f"/api/extension/leads/{acme}/claim", headers=h).status_code == 409


def test_ops08_claim_refused_after_stop_linkedin_for_the_lead(one):
    c, store, acme, h, _ = one
    li.stop(store, acme, "stopped by a person")                    # owner pressed 'stop LinkedIn outreach' after the queue fetch
    assert c.post(f"/api/extension/leads/{acme}/claim", headers=h).status_code == 409


def test_ops09_claim_refused_over_cap_or_inside_gap(one):
    c, store, acme, h, _ = one
    agent.save_config(store, {"gap_minutes": 60})
    agent.record(store, {"kind": "connect", "lead_id": "z", "account": ACCT})            # an action just happened
    assert c.post(f"/api/extension/leads/{acme}/claim", headers=h).status_code == 409, "claim does not enforce the gap"


# ---- (4) counters ----------------------------------------------------------------------------------------------------
def test_ops10_sent_without_event_still_counts_toward_caps(one):
    c, store, acme, h, _ = one
    assert c.post(f"/api/extension/leads/{acme}/sent", json={"step": 1}, headers=h).status_code == 200      # agent crashed before /event
    assert agent.control(store, account=ACCT)["counts"]["connect_today"] == 1, "real send not counted: caps/gap can be exceeded"


def test_ops11_duplicate_event_counted_once(one):
    c, store, acme, h, _ = one
    for _ in range(2):
        c.post("/api/extension/agent/event", json={"kind": "connect", "lead_id": acme, "verified": True}, headers=h)
    assert agent.control(store, account=ACCT)["counts"]["connect_today"] == 1


def test_ops12_sent_twice_does_not_duplicate_timeline_or_tasks(one):
    c, store, acme, h, _ = one
    c.post(f"/api/extension/leads/{acme}/sent", json={"step": 1}, headers=h)
    c.post(f"/api/extension/leads/{acme}/sent", json={"step": 1}, headers=h)
    assert sum("step 1 sent" in t["text"] for t in _lead(store, acme)["timeline"]) == 1


def test_ops13_default_hours_match_mon_sat_10_to_19(tmp_path):
    c, owner, acme, _ = make(tmp_path)
    cfg = agent.config(c.app.state.store)
    assert (cfg["start_hour"], cfg["end_hour"], cfg["weekdays_only"]) == (10, 19, False), f"defaults are {cfg['start_hour']}-{cfg['end_hour']} weekdays_only={cfg['weekdays_only']}"


def test_ops14_day_resets_at_ist_midnight_and_week_is_rolling(one):
    c, store, acme, h, _ = one
    from datetime import datetime, timezone
    sun_late = datetime(2026, 11, 8, 23, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))       # Sunday 23:30 IST
    agent.record(store, {"kind": "connect", "lead_id": "a", "account": ACCT}, now=sun_late.astimezone(timezone.utc))
    mon = datetime(2026, 11, 9, 0, 5, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    cc = agent.counts(store, mon.astimezone(timezone.utc), ACCT)
    assert cc["connect_today"] == 0 and cc["connect_week"] == 1                                    # new IST day; the week is rolling (LinkedIn's), so Sunday still counts


# ---- (5) failure visibility ------------------------------------------------------------------------------------------
def test_ops15_there_is_a_per_lead_why_not_sent_route(one):
    c, store, acme, h, owner = one
    r = c.get(f"/api/linkedin-agent/lead/{acme}/why", headers=bearer(owner))
    assert r.status_code == 200 and r.json().get("reasons")


def test_ops16_skip_reasons_reach_the_portal(one):
    c, store, acme, h, owner = one
    c.post("/api/extension/agent/event", json={"kind": "error", "lead_id": acme, "note": "skip Ravi: Already sent today"}, headers=h)
    # the agent only console.logs `skip <name>: <reason>` for can_send=false; nothing is posted. Portal can derive it:
    r = c.get(f"/api/linkedin-agent/lead/{acme}/why", headers=bearer(owner))
    assert r.status_code == 200


# ---- (6) notifications -----------------------------------------------------------------------------------------------
def test_ops17_unconfirmed_send_raises_an_immediate_alert(one):
    c, store, acme, h, _ = one
    n0 = len(store.read("notifications"))
    c.post("/api/extension/agent/event", json={"kind": "connect", "lead_id": acme, "verified": False, "note": "NOT confirmed"}, headers=h)
    assert len(store.read("notifications")) > n0, "an unconfirmed send only shows in the hourly digest, never as an alert"


def test_ops17b_non_fatal_errors_never_alert(one):
    c, store, acme, h, _ = one
    n0 = len(store.read("notifications"))
    for _ in range(5):
        c.post("/api/extension/agent/event", json={"kind": "error", "lead_id": acme, "note": "send button timed out"}, headers=h)
    assert len(store.read("notifications")) > n0, "5 failed sends in a row raised nothing (needs a streak alert)"


# ---- (7) tenant isolation --------------------------------------------------------------------------------------------
def _pair_b(tenant_rig):
    r = tenant_rig["c"].post("/api/extension/pair", json={"label": "b"}, headers=bearer(tenant_rig["hb"]))
    return {**bearer(r.json()["token"]), "X-LinkedIn-Account": "bravo-acct"}


@pytest.mark.parametrize("route,body", [("sent", {"step": 1}), ("accepted", None), ("replied", {"text": "hi"}), ("profile", {"open_profile": True, "degree": "1st"})])
def test_ops18_b_token_cannot_write_to_an_mcn_lead(tenant_rig, route, body):
    r = tenant_rig
    hdr = _pair_b(r)
    lead = _lead(r["store"], r["a_lead"])
    before = json.dumps(lead, sort_keys=True)
    res = r["c"].post(f"/api/extension/leads/{r['a_lead']}/{route}", json=body, headers=hdr) if body is not None else r["c"].post(f"/api/extension/leads/{r['a_lead']}/{route}", headers=hdr)
    assert res.status_code in (403, 404, 409) and json.dumps(_lead(r["store"], r["a_lead"]), sort_keys=True) == before, (route, res.status_code, res.text[:120])


def test_ops19_b_token_cannot_claim_an_mcn_lead(tenant_rig):
    r = tenant_rig
    assert r["c"].post(f"/api/extension/leads/{r['a_lead']}/claim", headers=_pair_b(r)).status_code in (403, 404, 409)


# ---- (8) dry run -----------------------------------------------------------------------------------------------------
def test_ops20_agent_dry_run_must_not_post_profile_side_effects():
    src = open("tools/linkedin_agent.mjs").read()
    line = next(l for l in src.splitlines() if "await reportProfile(page, item.lead_id" in l)
    assert "DRY" in line, "reportProfile (POST /profile writes linkedin_profile + a timeline line) runs in DRY mode"


def test_ops21_dry_run_is_visible_in_portal(one):
    c, store, acme, h, owner = one
    c.post("/api/extension/agent/event", json={"kind": "started", "state": "running", "note": "started in dry-run mode"}, headers=h)
    st = c.get("/api/linkedin-agent", headers=bearer(owner)).json()
    assert "dry" in json.dumps({k: st[k] for k in ("state", "accounts", "control")}).lower(), "portal shows a dry-run agent as 'running'"


# ---- (6b) watch ------------------------------------------------------------------------------------------------------
def test_ops22_watch_covers_client_accounts(tenant_rig):
    from app import linkedin_watch as watch
    r = tenant_rig
    assert "bravo-acct" or True
    agent.heartbeat(r["store"], account="bravo-acct", owner=r["b"])
    agent.record(r["store"], {"kind": "connect", "lead_id": r["b_lead"], "account": "bravo-acct", "verified": True}, now=TUE_11, owner=r["b"])
    assert r["b"] in watch._owners(r["store"]), "watch._owners reads settings['linkedin_log'] but the log key is 'linkedin_agent_log': client accounts get no digest / offline alert"


def test_ops23_offline_alert_not_sent_while_switch_is_off_or_at_night(one):
    from app import linkedin_watch as watch
    c, store, acme, h, _ = one
    agent.save_config(store, {"enabled": False})
    n0 = len(store.read("notifications"))
    watch.tick(store, TUE_11)
    assert len(store.read("notifications")) == n0, "owner switched the agent off on purpose, yet 'agent is not running' alert fired"


def test_ops24_weekly_cap_is_a_rolling_7_days_not_a_monday_reset(one):
    from datetime import datetime, timezone
    c, store, acme, h, _ = one
    ist = timezone(timedelta(hours=5, minutes=30))
    agent.save_config(store, {"weekly_connections": 5, "gap_minutes": 3, "weekdays_only": False})
    store.update("settings", lambda s: {**s, "linkedin_daily_connections": 40})
    sat = datetime(2026, 11, 7, 11, 0, tzinfo=ist)
    for i in range(5):
        agent.record(store, {"kind": "connect", "lead_id": f"s{i}", "account": ACCT}, now=(sat + timedelta(minutes=5 * i)).astimezone(timezone.utc))
    mon = datetime(2026, 11, 9, 11, 0, tzinfo=ist).astimezone(timezone.utc)          # 2 days later: LinkedIn's limit is still hit
    assert not agent.control(store, mon, account=ACCT)["may_connect"], "weekly cap resets on Monday: 5 sent Sat + 5 more Mon = 10 in 3 days"
