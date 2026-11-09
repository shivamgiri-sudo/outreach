"""LinkedIn agent per client account (app/linkedin_scope.py): an admin of account B runs only B's agent, LinkedIn
accounts, cloud slots and office computer; caps are clamped to the owner's ceilings; an agent token of one account never
sees or claims another account's lead; the owner's kill switch stops every account; callers only read; every change is
in the audit trail with its account; a LinkedIn pause alerts B's admins and the super admins."""
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import linkedin_agent as agent, linkedin_cloud as cloud, linkedin_seq as li
from app.main import create_app
from tests.test_linkedin_agent import TUE_11

ADMIN = "admin-pass-123"
MAIN_CSV = "company,contact_name,title,email,website\nAcme,Ravi Kumar,HR Head,ravi@acme.com,https://acme.com\n"
B_CSV = "company,contact_name,title,email,website\nBeta Ltd,Sita Rao,HR,sita@beta.in,https://beta.in\n"
URL_A, URL_B = "https://www.linkedin.com/in/ravi-kumar-a", "https://www.linkedin.com/in/sita-rao-b"


def bearer(t, **extra):
    return {"Authorization": f"Bearer {t}", **extra}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTREACH_CLOUD_LI_DIR", str(tmp_path / "cloud"))
    monkeypatch.delenv("OUTREACH_OFFICE_LI_MAX", raising=False)
    monkeypatch.delenv("OUTREACH_SUPER_ADMIN", raising=False)
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password=ADMIN), client=("127.0.0.1", 50000))
    ho = c.post("/api/login", json={"password": ADMIN}).json()["token"]
    c.post("/api/leads/import", json={"csv": MAIN_CSV, "source": "apollo"}, headers=bearer(ho))
    b = c.post("/api/accounts", json={"name": "Bravo Corp", "limits": {"linkedin_daily_connections_max": 5,
                                                                        "linkedin_weekly_connections_max": 30,
                                                                        "linkedin_daily_messages_max": 10}}, headers=bearer(ho)).json()
    ws = c.post("/api/workspaces", json={"name": "Bravo Sales", "account_id": b["id"]}, headers=bearer(ho)).json()
    for email, role in (("admin@bravo.com", "admin"), ("caller@bravo.com", "caller")):
        r = c.post("/api/users", json={"email": email, "name": email.split("@")[0].title(), "role": role, "password": "long-password-1",
                                       "workspaces": [ws["id"]], "account_id": b["id"]}, headers=bearer(ho))
        assert r.status_code == 200, r.text
    hb = c.post("/api/login", json={"email": "admin@bravo.com", "password": "long-password-1"}).json()["token"]
    hc = c.post("/api/login", json={"email": "caller@bravo.com", "password": "long-password-1"}).json()["token"]
    assert c.post("/api/leads/import", json={"csv": B_CSV, "source": "apollo"}, headers=bearer(hb, **{"X-Workspace": ws["id"]})).status_code == 200
    store = c.app.state.store
    ids = {l["company"]: l["id"] for l in store.read("leads")}
    store.update_lead(ids["Acme"], lambda l: {**l, "linkedin_url": URL_A})
    store.update_lead(ids["Beta Ltd"], lambda l: {**l, "linkedin_url": URL_B})
    li.start(store, ids["Acme"])
    li.start(store, ids["Beta Ltd"])
    return {"c": c, "ho": ho, "hb": hb, "hc": hc, "b": b["id"], "ws": ws["id"], "store": store, "a_lead": ids["Acme"], "b_lead": ids["Beta Ltd"],
            "base": tmp_path / "cloud"}


def token_of(base, slot_id):
    return next(r["token"] for r in json.loads((base / "slots.json").read_text())["slots"] if r["slot_id"] == slot_id)


def test_admin_of_b_manages_only_b_linkedin_accounts_slots_and_settings(rig):
    c, store, b = rig["c"], rig["store"], rig["b"]
    agent.heartbeat(store, account="mcn-sales", name="MCN Sales")                     # Mas Callnet's LinkedIn account
    agent.heartbeat(store, account="bravo-li", name="Bravo LI", owner=b)               # B's
    st = c.get("/api/linkedin-agent", headers=bearer(rig["hb"])).json()
    assert st["account_id"] == b and st["can_manage"] and [a["account"] for a in st["accounts"]] == ["bravo-li"]
    assert st["ceilings"] == {"daily_connections": 5, "weekly_connections": 30, "daily_messages": 10}
    # B's settings are B's own: the global (mcn) config does not move
    r = c.put("/api/linkedin-agent", json={"enabled": True, "start_hour": 8, "gap_minutes": 9}, headers=bearer(rig["hb"]))
    assert r.status_code == 200, r.text
    assert agent.config(store, b)["enabled"] is True and agent.config(store, b)["gap_minutes"] == 9
    assert agent.config(store)["enabled"] is False and agent.config(store)["gap_minutes"] == agent.DEFAULTS["gap_minutes"]
    # resume / merge of Mas Callnet's LinkedIn account: refused
    assert c.put("/api/linkedin-agent", json={"resume_account": "mcn-sales"}, headers=bearer(rig["hb"])).status_code == 400
    assert c.put("/api/linkedin-agent", json={"merge_from": "mcn-sales", "merge_into": "bravo-li"}, headers=bearer(rig["hb"])).status_code == 400
    # slots: B's admin adds one for B; the owner's mcn slot is invisible and untouchable for B
    mine = c.post("/api/linkedin-cloud/slots", json={"label": "Bravo slot"}, headers=bearer(rig["hb"]))
    assert mine.status_code == 200, mine.text
    theirs = c.post("/api/linkedin-cloud/slots", json={"label": "MCN slot"}, headers=bearer(rig["ho"], **{"X-Account": "mcn"})).json()
    mcn_slot = next(s for s in theirs["slots"] if s["label"] == "MCN slot")
    assert mcn_slot["account_id"] == "mcn"
    seen = c.get("/api/linkedin-cloud", headers=bearer(rig["hb"])).json()
    assert [s["label"] for s in seen["slots"]] == ["Bravo slot"] and seen["slots"][0]["account_id"] == b
    for method, path in (("put", f"/api/linkedin-cloud/slots/{mcn_slot['id']}"), ("delete", f"/api/linkedin-cloud/slots/{mcn_slot['id']}"),
                         ("post", f"/api/linkedin-cloud/slots/{mcn_slot['id']}/restart"), ("post", f"/api/linkedin-cloud/slots/{mcn_slot['id']}/logout")):
        kwargs = {"json": {"enabled": False}} if method == "put" else {}
        assert getattr(c, method)(path, headers=bearer(rig["hb"]), **kwargs).status_code == 404, path
    assert c.put("/api/linkedin-cloud/where", json={"where": "server"}, headers=bearer(rig["hb"])).status_code == 403
    # office computer pairing: B's own code; B's runner gets only B's slots
    code = c.post("/api/linkedin-cloud/runner/pair", json={}, headers=bearer(rig["hb"])).json()["code"]
    paired = c.post("/api/linkedin-runner/pair", json={"code": code, "hostname": "bravo-pc"}).json()
    run_slots = c.get("/api/linkedin-runner/slots", headers=bearer(paired["token"])).json()["slots"]
    assert [s["label"] for s in run_slots] == ["Bravo slot"]
    assert c.get("/api/linkedin-cloud", headers=bearer(rig["hb"])).json()["runner"]["hostname"] == "bravo-pc"
    assert c.get("/api/linkedin-cloud", headers=bearer(rig["ho"], **{"X-Account": "mcn"})).json()["runner"] is None   # mcn untouched
    assert c.delete(f"/api/linkedin-cloud/runner/{paired['runner_id']}", headers=bearer(rig["ho"])).status_code == 200      # owners: any
    # owners see every account with labels
    allv = c.get("/api/linkedin-cloud?account=all", headers=bearer(rig["ho"])).json()
    assert {s["account_name"] for s in allv["slots"]} == {"Bravo Corp", "Mas Callnet"} and allv["account_id"] is None
    over = c.get("/api/linkedin-agent?account=all", headers=bearer(rig["ho"])).json()
    assert {a["account"]: a["account_name"] for a in over["accounts"]} == {"bravo-li": "Bravo Corp", "mcn-sales": "Mas Callnet"}
    # the stage board / pickers of B list only B's LinkedIn accounts
    assert [a["account"] for a in c.get("/api/linkedin/accounts", headers=bearer(rig["hb"])).json()["accounts"]] == ["bravo-li"]
    assert "mcn-sales" not in c.get("/api/linkedin/stages", headers=bearer(rig["hb"])).json()["accounts"]


def test_caps_are_clamped_to_the_owner_ceiling(rig):
    c, store, b = rig["c"], rig["store"], rig["b"]
    r = c.put("/api/linkedin-agent", json={"daily_connections": 30, "weekly_connections": 90, "daily_messages": 50}, headers=bearer(rig["hb"]))
    assert r.status_code == 200, r.text
    assert r.json()["config"]["daily_connections"] == 5 and r.json()["config"]["weekly_connections"] == 30
    assert r.json()["config"]["daily_messages"] == 10 and set(r.json()["clamped"]) == {"daily_connections", "weekly_connections", "daily_messages"}
    agent.save_config(store, {"enabled": True, "gap_minutes": 3}, b)
    for i in range(5):
        agent.record(store, {"kind": "connect", "lead_id": f"x{i}", "account": "bravo-li"}, now=TUE_11 + timedelta(minutes=4 * i), owner=b)
    assert agent.control(store, TUE_11 + timedelta(minutes=30), "bravo-li", b)["connections_left_today"] == 0
    # the owner lowers the ceiling: already saved values follow it
    c.put(f"/api/accounts/{b}", json={"limits": {"linkedin_daily_messages_max": 4}}, headers=bearer(rig["ho"]))
    assert agent.config(store, b)["daily_messages"] == 4
    assert c.get("/api/linkedin-agent", headers=bearer(rig["hb"])).json()["ceilings"]["daily_messages"] == 4


def test_queue_and_claim_never_cross_accounts(rig):
    c, store, b = rig["c"], rig["store"], rig["b"]
    tb = c.post("/api/extension/pair", json={"label": "bravo laptop"}, headers=bearer(rig["hb"])).json()["token"]
    ta = c.post("/api/extension/pair", json={"label": "mcn laptop"}, headers=bearer(rig["ho"])).json()["token"]
    hb = bearer(tb, **{"X-LinkedIn-Account": "bravo-li"})
    ha = bearer(ta, **{"X-LinkedIn-Account": "mcn-li"})
    assert [d["lead_id"] for d in c.get("/api/extension/queue", headers=hb).json()["due"]] == [rig["b_lead"]]
    assert [d["lead_id"] for d in c.get("/api/extension/queue", headers=ha).json()["due"]] == [rig["a_lead"]]   # even an owner's token
    assert c.post(f"/api/extension/leads/{rig['a_lead']}/claim", headers=hb).status_code == 404
    assert c.post(f"/api/extension/leads/{rig['b_lead']}/claim", headers=ha).status_code == 404
    assert c.post(f"/api/extension/leads/{rig['b_lead']}/claim", headers=hb).json()["account"] == "bravo-li"
    assert c.get("/api/extension/match", params={"profile_url": URL_A}, headers=hb).json()["matched"] is False
    # a cloud slot an owner made for B works for B only
    slot = c.post(f"/api/linkedin-cloud/slots?account={b}", json={"label": "B by owner"}, headers=bearer(rig["ho"])).json()["slots"][0]
    ts = bearer(token_of(rig["base"], slot["id"]), **{"X-LinkedIn-Account": "bravo-li"})
    assert [d["lead_id"] for d in c.get("/api/extension/queue", headers=ts).json()["due"]] == [rig["b_lead"]]
    # a LinkedIn account of B cannot be driven by a Mas Callnet token, and the reverse
    c.get("/api/extension/agent", headers=hb)                                     # B's agent reports bravo-li first
    assert c.get("/api/extension/agent", headers=bearer(ta, **{"X-LinkedIn-Account": "bravo-li"})).status_code == 403
    assert c.get("/api/extension/queue", headers=bearer(ta, **{"X-LinkedIn-Account": "bravo-li"})).status_code == 403
    assert agent.owner_of_slug(store, "bravo-li") == b
    # a LinkedIn account of B cannot be picked for a Mas Callnet lead
    r = c.post("/api/linkedin/assign", json={"lead_ids": [rig["a_lead"]], "account": "bravo-li"}, headers=bearer(rig["ho"]))
    assert r.json()["ok"] == 0 and "another client account" in r.json()["results"][0]["reason"]


def test_owner_kill_switch_stops_every_account(rig):
    c, store, b = rig["c"], rig["store"], rig["b"]
    agent.save_config(store, {"enabled": True}, b)
    agent.save_config(store, {"enabled": True})
    assert agent.control(store, TUE_11, owner=b)["active_now"] and agent.control(store, TUE_11)["active_now"]
    assert c.put("/api/linkedin-agent/kill", json={"on": True}, headers=bearer(rig["hb"])).status_code == 403
    r = c.put("/api/linkedin-agent/kill", json={"on": True}, headers=bearer(rig["ho"]))
    assert r.status_code == 200 and r.json()["kill"]["on"] is True
    for owner in (b, "mcn"):
        ctl = agent.control(store, TUE_11, owner=owner)
        assert not ctl["active_now"] and not ctl["may_connect"] and "platform admin" in ctl["why_not"]
    assert c.get("/api/linkedin-agent", headers=bearer(rig["hb"])).json()["kill"]["on"] is True
    c.put("/api/linkedin-agent/kill", json={"on": False}, headers=bearer(rig["ho"]))
    assert agent.control(store, TUE_11, owner=b)["active_now"]


def test_caller_gets_a_read_only_status(rig):
    c = rig["c"]
    st = c.get("/api/linkedin-agent", headers=bearer(rig["hc"]))
    assert st.status_code == 200 and st.json()["can_manage"] is False and st.json()["log"] == [] and st.json()["accounts"] == []
    assert set(st.json()["config"]) == {"enabled"}
    assert c.put("/api/linkedin-agent", json={"enabled": True}, headers=bearer(rig["hc"])).status_code == 403
    assert c.get("/api/linkedin-cloud", headers=bearer(rig["hc"])).status_code == 403
    assert c.post("/api/linkedin-cloud/slots", json={"label": "x"}, headers=bearer(rig["hc"])).status_code == 403
    assert c.post("/api/linkedin-cloud/runner/pair", json={}, headers=bearer(rig["hc"])).status_code == 403


def test_every_change_is_audited_with_its_account(rig):
    c, store, b = rig["c"], rig["store"], rig["b"]
    c.put("/api/linkedin-agent", json={"enabled": True}, headers=bearer(rig["hb"]))
    c.put("/api/linkedin-agent", json={"daily_messages": 99, "gap_minutes": 12}, headers=bearer(rig["hb"]))
    sid = c.post("/api/linkedin-cloud/slots", json={"label": "Bravo slot"}, headers=bearer(rig["hb"])).json()["slots"][0]["id"]
    c.post(f"/api/linkedin-cloud/slots/{sid}/login", json={"email": "a@bravo.com", "password": "secret-pass"}, headers=bearer(rig["hb"]))
    c.post("/api/linkedin-cloud/runner/pair", json={}, headers=bearer(rig["hb"]))
    c.put("/api/linkedin-agent/kill", json={"on": True}, headers=bearer(rig["ho"]))
    rows = {r["action"]: r for r in store.read("audit")}
    for action in ("linkedin_agent.on", "linkedin_agent.settings", "linkedin_cloud.add", "linkedin_cloud.login", "linkedin_runner.code"):
        assert rows[action]["account_id"] == b, action
    assert "ceiling" in rows["linkedin_agent.settings"]["detail"]
    assert rows["linkedin_agent.kill_on"]["account_id"] == "mcn"
    assert "secret-pass" not in json.dumps(store.read("audit"))
    trail = c.get("/api/audit", headers=bearer(rig["ho"])).status_code
    assert trail == 200


def test_a_pause_alerts_b_admins_and_the_super_admins(rig, monkeypatch):
    c, store, b = rig["c"], rig["store"], rig["b"]
    monkeypatch.setenv("OUTREACH_SUPER_ADMIN", "boss@mcn.com")
    agent.heartbeat(store, account="bravo-li", owner=b)
    tb = c.post("/api/extension/pair", json={"label": "bravo laptop"}, headers=bearer(rig["hb"])).json()["token"]
    r = c.post("/api/extension/agent/event", json={"kind": "error", "fatal": True, "note": "weekly invitation limit"},
               headers=bearer(tb, **{"X-LinkedIn-Account": "bravo-li"}))
    assert r.status_code == 200, r.text
    assert "bravo-li" in agent.paused(store)
    notes = [n for n in store.read("notifications") if "bravo-li" in n["title"]]
    assert {n["to"] for n in notes} == {"Admin", "boss@mcn.com"}
    mine = next(n for n in notes if n["to"] == "Admin")
    assert mine["account_id"] == b and "weekly invitation limit" in mine["text"]
    assert not any(n["to"] == (store.read("settings").get("linkedin_owner") or "Sales team") for n in notes)   # not mcn's rep
    # B's admin resumes it; the log row belongs to B
    assert c.put("/api/linkedin-agent", json={"resume_account": "bravo-li"}, headers=bearer(rig["hb"])).status_code == 200
    assert "bravo-li" not in agent.paused(store)
    assert all((r.get("account_id") or "mcn") == b for r in agent.log_rows(store) if r.get("account") == "bravo-li")
    assert c.get("/api/linkedin-agent", headers=bearer(rig["ho"], **{"X-Account": "mcn"})).json()["log"] == []


def test_slot_tokens_carry_the_account(rig):
    c, store, b = rig["c"], rig["store"], rig["b"]
    sid = c.post("/api/linkedin-cloud/slots", json={"label": "Bravo slot"}, headers=bearer(rig["hb"])).json()["slots"][0]["id"]
    s = cloud.slot(store, sid)
    tok = next(t for t in store.read("settings")["extension_tokens"] if t["id"] == s["token_id"])
    assert s["account_id"] == b and tok["account_id"] == b
