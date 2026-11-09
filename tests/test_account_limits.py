"""Phase 5, items 1 and 6: every account limit is enforced in one place (app/account_limits.py) with a clear message,
Mas Callnet and blank limits are never blocked, 80% / 100% alerts come once per period to the account's admins and the
super admins, usage is shown to the account's admins and in the console, and limit changes and alerts are audited."""
from datetime import datetime, timedelta, timezone

import pytest

from app import account_limits as al, accounts
from tests.limits_helpers import BOSS, CSV2, FAKE_AI, audit_rows, bearer, lead_of, mails_to, make_rig, notes_for, set_limits
from tests.signup_helpers import make as signup_make, signed_up

MSG = "Your account reached its {words} of {n}; ask the platform admin to raise it."


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return make_rig(tmp_path, monkeypatch, ai=True)


# ---------------------------------------------------------------- imports a day: every import path
def test_imports_per_day_stops_every_import_path_with_a_clear_message(rig):
    c, hb, ho = rig["c"], rig["hb"], rig["ho"]
    set_limits(rig, imports_per_day=3)
    assert c.post("/api/leads/import", json={"csv": CSV2}, headers=hb).status_code == 200            # quick paste: 2 of 3
    full = MSG.format(words="daily imports limit", n=3)
    r = c.post("/api/leads/import", json={"csv": CSV2.replace("Alpha", "Gamma").replace("Beta", "Delta")}, headers=hb)
    assert r.status_code == 429 and r.json()["detail"] == full
    one = "company,contact_name,email\nEps Co,Eve,eve@eps.example\n"
    two = "company,contact_name,email\nZed Co,Zed,zed@zed.example\nYam Co,Yan,yan@yam.example\n"
    assert c.post("/api/import/commit", json={"csv": two, "source": "upload"}, headers=hb).status_code == 429       # smart import / Excel / CSV
    assert c.post("/api/import/commit", json={"csv": one, "source": "upload"}, headers=hb).status_code == 200      # 3 of 3
    assert c.post("/api/import/commit", json={"csv": one.replace("Eps", "Phi").replace("eve@", "phi@"), "source": "upload"}, headers=hb).status_code == 429
    # (sheet import, prospect import and add-to-leads are owner pages: a client login is refused them before any limit)
    assert c.post("/api/leads/import-sheet", json={"tab": "leads", "csv": two}, headers=hb).status_code == 403
    assert al.used(rig["store"], rig["b"], "imports_per_day") == 3
    # Mas Callnet (the owner) is never blocked, and neither is an account whose limit is blank
    assert c.post("/api/leads/import", json={"csv": two + one}, headers=ho).status_code == 200
    assert al.check(rig["store"], accounts.MCN, "imports_per_day", 10 ** 6) == 0


def test_a_blank_limit_and_a_new_day_do_not_block(rig):
    c, hb, store, b = rig["c"], rig["hb"], rig["store"], rig["b"]
    rows = "company,contact_name,email\n" + "".join(f"Co{i},P{i},p{i}@co{i}.example\n" for i in range(30))
    assert c.post("/api/leads/import", json={"csv": rows}, headers=hb).status_code == 200          # no limits set: unlimited
    assert al.used(store, b, "imports_per_day") == 30 and al.summary(store, b)[3]["state"] == "unlimited"
    set_limits(rig, imports_per_day=5)
    assert c.post("/api/leads/import", json={"csv": CSV2.replace("Alpha", "G")}, headers=hb).status_code == 429     # 30 already today
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1, hours=6)
    assert al.check(store, b, "imports_per_day", 2, now=tomorrow) == 2                           # a new day starts at zero


# ---------------------------------------------------------------- lookup credits
def test_enrich_credits_stop_paid_lookups(rig, monkeypatch):
    from app import find_enrich as fe
    c, store, b = rig["c"], rig["store"], rig["b"]
    set_limits(rig, enrich_credits=2)
    accounts.set_current(b, True)                                   # what a client login's request sets
    try:
        meter = fe.make_meter(store)
        assert meter.account == b and meter.can_spend("apollo")
        meter.record("apollo", "email", True, "a.example")
        meter.record("hunter", "email", False, "b.example")           # a paid call that found nothing still costs a credit
        meter.record("prospect_db", "email", True, "c.example", cached=True)          # the cache is free
        assert al.used(store, b, "enrich_credits") == 2
        assert not meter.can_spend("apollo") and meter.blocked
        assert meter.message() == MSG.format(words="monthly lookup credits limit", n=2)
        assert meter.can_spend("prospect_db")                           # free sources never count
    finally:
        accounts.set_current(None, False)
    # the campaign enrichment loop checks the same counter before each paid call
    accounts.set_current(b, True)
    try:
        assert al.room(store, al.current_account(), "enrich_credits") == MSG.format(words="monthly lookup credits limit", n=2)
    finally:
        accounts.set_current(None, False)
    mcn = fe.make_meter(store)                                       # Mas Callnet's meter has no account: only its own budget
    assert mcn.account is None and mcn.can_spend("apollo")


# ---------------------------------------------------------------- AI credits
def test_ai_credits_stop_reply_drafts_and_other_ai_calls(rig):
    c, hb, ho = rig["c"], rig["hb"], rig["ho"]
    set_limits(rig, ai_credits=2)
    lid = lead_of(rig)[0]
    for _ in range(2):
        r = c.post(f"/api/leads/{lid}/draft-reply", json={"incoming": "Can you share pricing for this please?"}, headers=hb)
        assert r.status_code == 200 and r.json()["source"] == "ai"
    r = c.post(f"/api/leads/{lid}/draft-reply", json={"incoming": "And what about discounts please?"}, headers=hb).json()
    assert r["source"] == "template" and MSG.format(words="monthly AI credits limit", n=2) in r["note"]
    assert len(rig["ai_calls"]) == 2                                   # the third call never reached the AI provider
    from app import opener_lab
    out = opener_lab.candidates(rig["store"], "hello")                 # Mas Callnet's call: not charged to anyone
    assert out["source"] in ("claude", "built_in")
    # the owner (Mas Callnet) keeps drafting without a limit
    mcn_lead = lead_of(rig, "company,contact_name,email\nMain Co,Mo,mo@main.example\n", headers=ho)[-1]
    assert all(c.post(f"/api/leads/{mcn_lead}/draft-reply", json={"incoming": "Can you share pricing for this please?"}, headers=ho).json()["source"] == "ai"
               for _ in range(4))


def test_ai_label_and_flow_calls_are_charged_to_the_leads_account(rig):
    from app import ai
    store, b = rig["store"], rig["b"]
    set_limits(rig, ai_credits=1)
    seen = []
    transport = lambda url, headers, body: seen.append(1) or {"content": [{"type": "text", "text": '{"label": "question"}'}]}
    assert ai.ai_classify(store, "hmm okay then", transport, account=b) == "question"
    assert ai.ai_classify(store, "hmm okay then", transport, account=b) is None            # used up: the rule-based label is used
    assert len(seen) == 1 and al.used(store, b, "ai_credits") == 1
    assert ai.ai_classify(store, "hmm okay then", transport) == "question"                  # no account (Mas Callnet): free


# ---------------------------------------------------------------- emails + WhatsApp + flows share the daily sends
def test_daily_sends_count_emails_whatsapp_and_flows_together(tmp_path, monkeypatch):
    rig = make_rig(tmp_path, monkeypatch, limits={"daily_sends": 3}, grants=["sending_domain", "whatsapp"])
    c, hb, store, b = rig["c"], rig["hb"], rig["store"], rig["b"]
    lid = lead_of(rig)[0]
    consent = {"text": "said yes", "at": "2026-11-01T00:00:00+00:00", "source": "test", "channel": "any"}
    store.update_lead(lid, lambda l: {**l, "phone": "+919876543210", "consent": consent})
    mail = {"subject": "Hello there", "body": "Hi, a short note for you."}
    assert c.post(f"/api/leads/{lid}/email-compose", json=mail, headers=hb).status_code == 200            # 1: a one-off email
    assert c.post(f"/api/leads/{lid}/whatsapp", json={"template": "generic_follow_up"}, headers=hb).status_code == 200      # 2: WhatsApp
    assert al.used(store, b, "daily_sends") == 2
    al.record(store, b, "daily_sends", 1)                                                          # 3: a flow / sequence send
    full = MSG.format(words="daily sending limit", n=3)
    r = c.post(f"/api/leads/{lid}/email-compose", json=mail, headers=hb)
    assert r.status_code == 400 and r.json()["detail"] == full
    r = c.post(f"/api/leads/{lid}/whatsapp", json={"template": "generic_follow_up"}, headers=hb)
    assert r.status_code == 400 and r.json()["detail"] == full
    # a sequence run skips the account instead of sending
    from datetime import datetime as dt
    c.post(f"/api/leads/{lid}/enroll", headers=hb)
    out = c.post("/api/sequences/run", json={"now": dt(2026, 11, 3, 5, 0, tzinfo=timezone.utc).isoformat()}, headers=rig["ho"]).json()
    assert not out.get("other_accounts_sent")
    assert al.used(store, b, "daily_sends") == 3
    assert al.check(store, accounts.MCN, "daily_sends", 500) == 0                                   # Mas Callnet: never counted or blocked


# ---------------------------------------------------------------- logins and workspaces
def test_seats_and_workspaces_are_enforced_on_create_invite_and_approval(rig):
    c, hb, ho, b = rig["c"], rig["hb"], rig["ho"], rig["b"]
    set_limits(rig, seats=2, workspaces=2)
    r = c.post("/api/users", json={"email": "third@bravo.com", "role": "caller", "password": "long-password-1", "workspaces": [rig["ws"]]}, headers=hb)
    assert r.status_code == 403 and r.json()["detail"] == MSG.format(words="logins limit", n=2)
    one = c.post("/api/workspaces", json={"name": "Bravo Two"}, headers=hb)                      # a request counts toward the limit
    assert one.status_code == 200 and one.json()["status"] == "requested"
    r = c.post("/api/workspaces", json={"name": "Bravo Three"}, headers=hb)
    assert r.status_code == 403 and r.json()["detail"] == MSG.format(words="workspaces limit", n=2)
    r = c.post("/api/workspaces", json={"name": "Bravo Owner Made", "account_id": b}, headers=ho)  # the owner is held to the account's limit too
    assert r.status_code == 403
    set_limits(rig, seats=2, workspaces=1)                                                         # lowered after the request was made
    r = c.post(f"/api/workspaces/{one.json()['id']}/approve", headers=ho)
    assert r.status_code == 403 and "workspaces limit of 1" in r.json()["detail"]
    assert all(r.get("account_id") == b for r in audit_rows(rig["store"], "account.limits"))


def test_signup_approval_opens_only_as_many_workspaces_as_the_limit_allows(tmp_path, monkeypatch):
    c, ha, hs, outbox = signup_make(tmp_path, monkeypatch)
    hp, aid, email = signed_up(c, outbox)
    assert c.put(f"/api/accounts/{aid}", json={"limits": {"workspaces": 0}}, headers=hs).status_code == 200
    assert c.post(f"/api/console/accounts/{aid}/approve", headers=hs).status_code == 200
    spaces = [w for w in c.app.state.store.read("settings")["workspaces"] if w["account_id"] == aid]
    assert accounts.find(c.app.state.store, aid)["status"] == "active" and [w.get("status") for w in spaces] == ["requested"]


# ---------------------------------------------------------------- thresholds: once per period, to the right people
def test_80_and_100_percent_alert_once_per_period_to_admins_and_super_admins(rig):
    store, b = rig["store"], rig["b"]
    set_limits(rig, ai_credits=10)
    al.record(store, b, "ai_credits", 7)
    assert not notes_for(store, b)                                    # 70%: nothing
    al.record(store, b, "ai_credits", 1)                              # 80%
    al.record(store, b, "ai_credits", 1)                              # still above 80%: no second alert
    mine = [n for n in notes_for(store, b) if n["kind"] == "limit:limit_80"]
    assert len(mine) == 1 and mine[0]["to"] == "Bravo Admin" and "80%" in mine[0]["title"]
    assert [n["to"] for n in store.read("notifications") if n["kind"] == "security:super_admin" and "80%" in n["title"]] == [BOSS]
    limit_mails = lambda to: [m for m in mails_to(store, to) if "80%" in m["subject"]]
    assert len(limit_mails("admin@bravo.com")) == 1 and len(limit_mails(BOSS)) == 1
    assert not [n for n in store.read("notifications") if n["to"] == "Sales team"]                 # not Mas Callnet's staff
    al.record(store, b, "ai_credits", 1)                              # 100%
    with pytest.raises(al.LimitReached) as hit:
        al.check(store, b, "ai_credits", 1)
        al.check(store, b, "ai_credits", 1)
    assert hit.value.status_code == 429 and hit.value.message == MSG.format(words="monthly AI credits limit", n=10)
    with pytest.raises(al.LimitReached):
        al.check(store, b, "ai_credits", 1)                           # refused again: no third alert
    full = [n for n in notes_for(store, b) if n["kind"] == "limit:limit_100"]
    assert len(full) == 1 and len(notes_for(store, b)) == 2
    assert len(audit_rows(store, "limit.threshold", b)) == 2           # recorded in the change log with the account
    from app import abuse_alerts
    assert [a["kind"] for a in abuse_alerts.feed(store)] == ["limit_100", "limit_80"]
    next_month = datetime.now(timezone.utc) + timedelta(days=40)
    al.record(store, b, "ai_credits", 9, now=next_month)              # a new month: the same limit alerts again
    assert len([n for n in notes_for(store, b) if n["kind"] == "limit:limit_80"]) == 2


def test_usage_is_kept_in_memory_and_written_rarely(rig, monkeypatch):
    store, b = rig["store"], rig["b"]
    writes = []
    real = store.update
    monkeypatch.setattr(store, "update", lambda key, change: (writes.append(key), real(key, change))[1])
    for _ in range(15):
        al.record(store, b, "imports_per_day", 1)
    assert writes.count("settings") <= 2                                # first use of the period, not every count
    al.flush(store)
    assert store.read("settings")["account_usage"][b]["d"]["imports_per_day"] == 15


# ---------------------------------------------------------------- what the pages show
def test_usage_card_for_admins_and_the_console(tmp_path, monkeypatch):
    rig = make_rig(tmp_path, monkeypatch, limits={"ai_credits": 4, "seats": 5})
    c, hb, hc, ho, store, b = rig["c"], rig["hb"], rig["hc"], rig["ho"], rig["store"], rig["b"]
    al.record(store, b, "ai_credits", 4)
    card = c.get("/api/account/usage", headers=hb).json()
    ai = next(r for r in card["limits"] if r["kind"] == "ai_credits")
    assert card["account"]["id"] == b and (ai["used"], ai["limit"], ai["pct"], ai["state"]) == (4, 4, 100, "full")
    assert next(r for r in card["limits"] if r["kind"] == "seats")["used"] == 2
    assert c.get("/api/account/usage", headers=hc).status_code == 403                    # a caller does not see it
    assert c.get(f"/api/account/usage?account_id={b}", headers=ho).status_code == 200      # the platform owner may
    c2 = c.get("/api/account/usage", headers=bearer(c.post("/api/login", json={"email": "admin@bravo.com", "password": "long-password-1"}).json()["token"]))
    assert c2.status_code == 200


def test_console_account_rows_carry_usage_and_alerts(tmp_path, monkeypatch):
    c, ha, hs, outbox = signup_make(tmp_path, monkeypatch)
    hp, aid, email = signed_up(c, outbox)
    c.post(f"/api/console/accounts/{aid}/approve", headers=hs)
    c.put(f"/api/accounts/{aid}", json={"limits": {"imports_per_day": 2}}, headers=hs)
    al.record(c.app.state.store, aid, "imports_per_day", 2)
    data = c.get("/api/console/accounts", headers=hs).json()
    row = next(a for a in data["accounts"] if a["id"] == aid)
    assert next(u for u in row["usage"] if u["kind"] == "imports_per_day")["pct"] == 100
    assert [a["kind"] for a in data["alerts"]][:2] == ["limit_100", "limit_80"][:len(data["alerts"][:2])]
    assert c.get("/api/console/alerts", headers=hs).json()["alerts"] and c.get("/api/console/alerts", headers=ha).status_code in (200, 403)


# ---------------------------------------------------------------- audit
def test_limit_changes_are_in_the_change_log_with_the_account(rig):
    set_limits(rig, ai_credits=9, seats=4)
    rows = audit_rows(rig["store"], "account.limits", rig["b"])
    assert rows and "ai_credits" in rows[-1]["detail"] and rows[-1]["account_id"] == rig["b"]
