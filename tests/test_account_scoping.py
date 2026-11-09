"""Phase 5, items 2, 3 and 4: alerts, response-time digests, reports, reminders and links stay inside the account they are
about; a client account's WhatsApp needs its own provider or a grant (secrets sealed, never shown back); the LinkedIn
daily number and pause notices are the account's own."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import accounts, alerts, digest, linkedin_agent as agent, linkedin_seq as li, reminders, sla, whatsapp
from app.activities import add_task
from app.channels import config as hub
from tests.limits_helpers import BOSS, bearer, lead_of, make_rig, mails_to

MAIN_CSV = "company,contact_name,email\nMain Co,Mo,mo@main.example\n"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = make_rig(tmp_path, monkeypatch)
    r["main_lead"] = lead_of(r, MAIN_CSV, headers=r["ho"])[-1]
    r["b_lead"] = lead_of(r)[0]
    return r


def ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


# ---------------------------------------------------------------- alerts and managers per account
def test_alerts_go_only_to_the_leads_own_account(rig):
    store, c, ho, hb = rig["store"], rig["c"], rig["ho"], rig["hb"]
    store.update("settings", lambda s: {**s, "alert_managers": ["Boss"]})                           # Mas Callnet's own managers
    c.put(f"/api/accounts/{rig['b']}/settings", json={"alert_managers": ["Bravo Caller", "Someone Else"]}, headers=ho)
    assert alerts.send(store, "interested", rig["b_lead"], "They said yes.", managers=True) == ["Bravo Admin", "Bravo Caller"]
    assert alerts.send(store, "interested", rig["main_lead"], "They said yes.", managers=True) == ["Sales team", "Boss"]
    to = {n["to"]: n for n in store.read("notifications") if n["kind"] == "alert:interested"}
    assert to["Bravo Admin"]["account_id"] == rig["b"] and to["Bravo Caller"]["account_id"] == rig["b"] and "account_id" not in to["Boss"]
    assert [m["to"] for m in mails_to(store, "admin@bravo.com")] == ["admin@bravo.com"]
    # the client's own owner named on the lead gets it; an outsider's name on a client lead is dropped
    store.update_lead(rig["b_lead"], lambda l: {**l, "owner": "Sales team"})
    assert alerts.recipients(store, next(l for l in store.read("leads") if l["id"] == rig["b_lead"]), True) == ["Bravo Caller"]
    # a client's alerts can be switched off for the account without touching Mas Callnet's
    c.put(f"/api/accounts/{rig['b']}/settings", json={"alerts_email": False}, headers=ho)
    before = len(mails_to(store, "admin@bravo.com"))
    store.update_lead(rig["b_lead"], lambda l: {k: v for k, v in l.items() if k != "owner"})
    alerts.send(store, "reply", rig["b_lead"], "A reply.")
    assert len(mails_to(store, "admin@bravo.com")) == before


def test_links_use_the_accounts_public_address_else_the_platforms(rig):
    store, c, ho = rig["store"], rig["c"], rig["ho"]
    c.put("/api/settings", json={"public_base_url": "https://platform.example"}, headers=ho)
    alerts.send(store, "reply", rig["b_lead"], "A reply.")
    assert "https://platform.example/#leads" in mails_to(store, "admin@bravo.com")[-1]["body"]
    assert c.put(f"/api/accounts/{rig['b']}/settings", json={"public_base_url": "ftp://nope"}, headers=ho).status_code == 400
    c.put(f"/api/accounts/{rig['b']}/settings", json={"public_base_url": "https://crm.bravo.example/"}, headers=ho)
    store.update("notifications", lambda rows: [])
    alerts.send(store, "click", rig["b_lead"], "A click.")
    assert "https://crm.bravo.example/#leads" in mails_to(store, "admin@bravo.com")[-1]["body"]
    alerts.send(store, "reply", rig["main_lead"], "A reply.")
    assert not any("bravo.example" in m["body"] for m in store.read("transactional_outbox") if m["lead_id"] == rig["main_lead"])
    # an account's admin can set who hears and the address, but only for its own account
    got = rig["c"].put("/api/account/alerts", json={"alert_managers": ["Bravo Caller"], "alerts_email": True}, headers=rig["hb"])
    assert got.status_code == 200 and got.json()["alert_managers"] == ["Bravo Caller"]
    assert rig["c"].put("/api/account/alerts", json={"alert_managers": ["x"]}, headers=rig["hc"]).status_code == 403
    assert rig["c"].put("/api/account/alerts?account_id=mcn", json={"alert_managers": ["x"]}, headers=ho).status_code == 400


def test_response_time_digests_split_per_account(rig):
    store = rig["store"]
    store.update("leads", lambda rows: [{**l, "created_at": ago(30)} for l in rows])
    store.update("settings", lambda s: {**s, "alert_managers": ["Boss"]})
    assert sla.check(store) == 3
    got = {n["to"]: n for n in store.read("notifications") if n["kind"] == "alert:sla"}
    assert set(got) == {"Sales team", "Boss", "Bravo Admin"}                       # nobody crosses over
    assert got["Sales team"]["title"].endswith("Main Co") and got["Sales team"]["lead_id"] == rig["main_lead"] and "account_id" not in got["Sales team"]
    mine = got["Bravo Admin"]
    assert mine["title"].endswith("2 leads") and "Alpha" in mine["text"] and "Beta" in mine["text"] and "Main Co" not in mine["text"]
    assert mine["account_id"] == rig["b"]


def test_reminders_and_the_morning_summary_stay_in_their_account(rig):
    store = rig["store"]
    now = datetime.now(timezone.utc)
    add_task(store, rig["b_lead"], "call", "Call Ann", due_at=now - timedelta(minutes=5), owner=None, title="Call Ann")
    add_task(store, rig["main_lead"], "call", "Call Mo", due_at=now - timedelta(minutes=5), owner=None, title="Call Mo")
    reminders._task_reminders(store, now, None)
    got = {n["to"]: n["title"] for n in store.read("notifications") if n["kind"] == "task_due"}
    assert "Call Ann" in got["Bravo Admin"] and "Call Mo" in got["Sales team"] and len(got) == 2
    data = digest.build_digest(store, now)
    assert rig["b_lead"] not in data["leads"] and rig["main_lead"] in data["leads"]
    assert all(t.get("lead_id") != rig["b_lead"] for t in data["calls_today"] + data["overdue"])


def test_reports_are_the_accounts_own_and_their_email_uses_its_address_and_name(rig):
    c, ho, hb, store, b = rig["c"], rig["ho"], rig["hb"], rig["store"], rig["b"]
    c.post(f"/api/leads/{rig['b_lead']}/call-outcome", json={"outcome": "interested", "notes": "x"}, headers=hb)
    c.post(f"/api/leads/{rig['main_lead']}/call-outcome", json={"outcome": "no_answer", "notes": "y"}, headers=ho)
    mine = c.get("/api/reports/daily?days=1", headers=hb).json()
    assert mine["totals"]["calls"] == 1 and mine["totals"]["interested"] == 1 and {r["company"] for r in mine["log"]} <= {"Alpha Co", "Beta Co"}
    everything = c.get("/api/reports/daily?days=1", headers=ho).json()
    assert everything["totals"]["calls"] == 2                                          # owners keep the platform-wide view ...
    only_b = c.get("/api/reports/daily?days=1", headers=bearer(ho["Authorization"].split()[1], **{"X-Account": b})).json()
    assert only_b["totals"]["calls"] == 1                                              # ... with an account filter
    c.put(f"/api/accounts/{b}/settings", json={"public_base_url": "https://crm.bravo.example"}, headers=ho)
    sent = c.post("/api/reports/email", json={"days": 1}, headers=hb).json()
    assert sent["link"].startswith("https://crm.bravo.example/api/reports/file/") and "Bravo Corp" in sent["html"] and "MCN Outreach" not in sent["html"]
    csv = c.get(sent["link"].replace("https://crm.bravo.example", "")).text
    assert "Alpha Co" in csv and "Main Co" not in csv


# ---------------------------------------------------------------- WhatsApp per account
def wa_ready(rig, lead_id, phone="+919876543210"):
    rig["c"].put(f"/api/accounts/{rig['b']}/settings", json={"sender_name": "Bea Bravo"}, headers=rig["ho"])
    rig["store"].update("settings", lambda s: {**s, "sender_name": "Mo Main"})
    consent = {"text": "yes", "at": "2026-11-01T00:00:00+00:00", "source": "t", "channel": "any"}
    rig["store"].update_lead(lead_id, lambda l: {**l, "phone": phone, "consent": consent})


def platform_whatsapp(store):
    hub.save_provider(store, "whatsapp", "meta_cloud", {"phone_number_id": "111222333", "access_token": "platform-token-9999"})
    store.update("settings", lambda s: {**s, "live_whatsapp": True})


def test_whatsapp_is_held_without_a_provider_or_grant_and_uses_the_platform_with_a_grant(rig):
    c, ho, store, b = rig["c"], rig["ho"], rig["store"], rig["b"]
    platform_whatsapp(store)
    wa_ready(rig, rig["b_lead"])
    r = c.post(f"/api/leads/{rig['b_lead']}/whatsapp", json={"template": "generic_follow_up"}, headers=rig["hb"])
    assert r.status_code == 400 and r.json()["detail"] == accounts.HOLD_WHATSAPP and "own WhatsApp provider" in accounts.HOLD_WHATSAPP
    c.put(f"/api/accounts/{b}", json={"integrations_granted": ["whatsapp"]}, headers=ho)
    seen = []
    transport = lambda url, headers, body, **kw: seen.append((url, headers)) or {"messages": [{"id": "wamid.1"}]}
    row = whatsapp.send(store, rig["b_lead"], "generic_follow_up", "", datetime.now(timezone.utc), "https://x.example", transport=transport)
    assert seen and "111222333" in seen[0][0] and "platform-token-9999" in seen[0][1]["Authorization"]
    assert row["timeline"][-1]["kind"] == "whatsapp"


def test_a_client_can_use_its_own_whatsapp_provider_and_its_keys_are_sealed_and_never_shown(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTREACH_SECRET_KEY", "test-seal-key-for-whatsapp-accounts")
    rig = make_rig(tmp_path, monkeypatch)
    c, hb, hc, ho, store, b = rig["c"], rig["hb"], rig["hc"], rig["ho"], rig["store"], rig["b"]
    platform_whatsapp(store)
    lid = lead_of(rig)[0]
    wa_ready(rig, lid)
    main = lead_of(rig, MAIN_CSV, headers=ho)[-1]
    wa_ready(rig, main)
    saved = c.put("/api/account/whatsapp", json={"provider": "meta_cloud", "values": {"phone_number_id": "999888777", "access_token": "client-secret-token-4321"}}, headers=hb)
    assert saved.status_code == 200 and saved.json()["configured"] and saved.json()["values"]["access_token"] == "••••••••"
    assert "client-secret-token-4321" not in json.dumps(c.get("/api/account/whatsapp", headers=hb).json())          # never shown back
    assert "client-secret-token-4321" not in (tmp_path / "d.json").read_text() and "client-secret-token-4321" not in json.dumps(c.get("/api/accounts", headers=ho).json())
    assert store.read("settings")["account_settings"][b]["whatsapp"]["values"]["access_token"] == "client-secret-token-4321"      # (in memory, plain)
    assert c.put("/api/account/whatsapp", json={"provider": "meta_cloud", "values": {"phone_number_id": "999888777", "access_token": "••••••••"}},
                 headers=hb).status_code == 200                                                                    # a masked secret keeps the saved one
    assert store.read("settings")["account_settings"][b]["whatsapp"]["values"]["access_token"] == "client-secret-token-4321"
    assert c.put("/api/account/whatsapp", json={"provider": "meta_cloud", "values": {}}, headers=hc).status_code == 403
    assert c.put("/api/account/whatsapp", json={"provider": "gupshup", "values": {}}, headers=hb).status_code == 400            # a provider needs its required fields
    assert accounts.hold_reason(store, b, "whatsapp") is None                                                     # held no more
    seen = []
    transport = lambda url, headers, body, **kw: seen.append((url, headers)) or {"messages": [{"id": "wamid.2"}]}
    now = datetime.now(timezone.utc)
    whatsapp.send(store, lid, "generic_follow_up", "", now, "https://x.example", transport=transport)
    assert not seen                                                                                                 # its own live switch is off: practice
    assert c.put("/api/account/whatsapp", json={"provider": "meta_cloud", "values": {}, "live": True}, headers=hb).status_code == 200
    whatsapp.send(store, lid, "generic_follow_up", "", now, "https://x.example", transport=transport)
    assert "999888777" in seen[0][0] and "client-secret-token-4321" in seen[0][1]["Authorization"] and "platform" not in seen[0][1]["Authorization"]
    whatsapp.send(store, main, "generic_follow_up", "", now, "https://x.example", transport=transport)               # Mas Callnet: unchanged
    assert "111222333" in seen[1][0] and "platform-token-9999" in seen[1][1]["Authorization"]
    assert c.delete("/api/account/whatsapp", headers=hb).status_code == 200 and accounts.hold_reason(store, b, "whatsapp") == accounts.HOLD_WHATSAPP
    assert [r for r in store.read("audit") if r["action"] == "account.whatsapp" and r["account_id"] == b]
    assert "client-secret-token-4321" not in json.dumps(store.read("audit"))


# ---------------------------------------------------------------- LinkedIn
def test_suggested_people_use_the_accounts_own_daily_number(rig):
    c, store, b = rig["c"], rig["store"], rig["b"]
    store.update("settings", lambda s: {**s, "linkedin_daily_connections": 17})
    agent.save_config(store, {"daily_connections": 3}, b)
    for i, lead in enumerate(l for l in store.read("leads") if l["id"] != rig["main_lead"]):
        store.update_lead(lead["id"], lambda l, i=i: {**l, "linkedin_url": f"https://www.linkedin.com/in/person-{i}"})
    li.start(store, rig["b_lead"])
    mine = c.get("/api/linkedin/queue", headers=rig["hb"]).json()
    assert mine["daily_limit"] == 3 and mine["started_today"] == 1 and len(mine["suggestions"]) <= 2
    store.update_lead(rig["main_lead"], lambda l: {**l, "linkedin_url": "https://www.linkedin.com/in/main-mo"})
    theirs = c.get("/api/linkedin/queue", headers=rig["ho"]).json()
    assert theirs["daily_limit"] == 17 and theirs["started_today"] == 0                    # Mas Callnet: its own number and its own starts
    task = next(t for t in store.read("tasks") if t["lead_id"] == rig["b_lead"] and t["kind"] == "linkedin")
    assert task["owner"] == "Bravo Admin"                                                  # the client's task is not Mas Callnet's rep's


def test_a_linkedin_pause_goes_to_the_accounts_admins_and_super_admins_not_mas_callnets_rep(rig):
    store, b = rig["store"], rig["b"]
    store.update("settings", lambda s: {**s, "linkedin_owner": "Rep Rani"})
    agent.record(store, {"kind": "error", "fatal": True, "note": "checkpoint", "account": "bravo-li"}, owner=b)
    to = {n["to"] for n in store.read("notifications") if "bravo-li" in n["title"]}
    assert to == {"Bravo Admin", BOSS}
    agent.record(store, {"kind": "error", "fatal": True, "note": "captcha", "account": "mcn-li"})
    mcn = {n["to"] for n in store.read("notifications") if "mcn-li" in n["title"]}
    assert "Rep Rani" in mcn and "Bravo Admin" not in mcn
