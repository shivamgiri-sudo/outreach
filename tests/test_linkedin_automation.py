"""Fully automatic LinkedIn: daily auto-start, replies read from the messaging list, the accepted alert, and the lead's
communication plan (done / next / stopped / automatic)."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import linkedin_agent as agent, linkedin_seq as li
from app.main import create_app

CSV = ("company,contact_name,title,email,linkedin_url\n"
       "Acme,Ravi Kumar,CHRO,ravi@acme.com,https://www.linkedin.com/in/ravi-kumar\n"
       "Beta,Sita Rao,HR Head,sita@beta.in,https://www.linkedin.com/in/sita-rao\n"
       "Gamma,Mr. Anil Shah,CEO,anil@gamma.in,https://www.linkedin.com/in/anil-shah\n")


def make(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    store = c.app.state.store
    return c, store, {l["company"]: l["id"] for l in store.read("leads")}


def test_auto_start_only_when_switched_on_and_within_the_limit(tmp_path):
    c, store, ids = make(tmp_path)
    assert li.auto_start(store, 10) == []
    assert c.put("/api/linkedin/settings", json={"linkedin_auto_start": True}).json()["linkedin_auto_start"] is True
    assert any(r["action"] == "linkedin.settings" for r in store.read("audit"))
    assert len(li.auto_start(store, 2)) == 2                         # never more than the account may still send
    store.update("settings", lambda s: {**s, "linkedin_daily_connections": 2})
    assert li.auto_start(store, 10) == []                            # the daily limit counts everyone started today
    started = [l for l in store.read("leads") if l.get("linkedin_seq")]
    assert all(l["linkedin_seq"]["auto_started"] for l in started)
    assert all(any(e.get("auto") for e in l["timeline"]) for l in started)


def test_inbox_reply_stops_the_sequence_and_reaches_replies(tmp_path):
    c, store, ids = make(tmp_path)
    for lid in ids.values():
        li.start(store, lid)
        li.mark_sent(store, lid, 1)
    items = [{"name": "Anil Shah", "text": "Happy to talk next week", "from_them": True, "profile_url": "https://www.linkedin.com/in/anil-shah/"},
             {"name": "Ravi Kumar", "text": "Hi Ravi, came across…", "from_them": False},           # our own last message
             {"name": "Unknown Person", "text": "hello", "from_them": True}]
    found = li.match_inbox(store, "", items)
    assert [r["lead_id"] for r in found] == [ids["Gamma"]]            # "Mr. Anil Shah" matched on first + last name
    lead = next(l for l in store.read("leads") if l["id"] == ids["Gamma"])
    assert lead["linkedin_seq"]["stopped"] == "replied"
    assert any(e["kind"] == "reply" and "next week" in e["text"] for e in lead["timeline"])
    assert not [t for t in store.read("tasks") if t["lead_id"] == ids["Gamma"] and t["kind"] == "linkedin" and t.get("status", "open") == "open"]
    assert li.match_inbox(store, "", items) == []                     # already stopped: never twice


def test_a_name_shared_by_two_people_is_left_for_a_person(tmp_path):
    c, store, ids = make(tmp_path)
    store.update_lead(ids["Beta"], lambda l: {**l, "contact_name": "Ravi Kumar"})
    for lid in ids.values():
        li.start(store, lid)
    assert li.match_inbox(store, "", [{"name": "Ravi Kumar", "text": "yes", "from_them": True}]) == []


def test_accepted_by_the_agent_raises_an_alert_with_the_first_message_date(tmp_path):
    c, store, ids = make(tmp_path)
    li.start(store, ids["Acme"])
    li.mark_sent(store, ids["Acme"], 1)
    r = c.post(f"/api/extension/leads/{ids['Acme']}/accepted")
    assert r.status_code == 200
    n = [x for x in store.read("notifications") if x["kind"] == "alert:li_accepted"]
    assert n and "first message is scheduled" in n[0]["text"] and n[0]["title"] == "Accepted on LinkedIn: Acme"


def test_plan_shows_done_next_and_what_the_system_did(tmp_path):
    c, store, ids = make(tmp_path)
    now = datetime.now(timezone.utc)
    li.start(store, ids["Acme"], now - timedelta(days=2), auto=True)
    li.mark_sent(store, ids["Acme"], 1, now - timedelta(days=2))
    agent.record(store, {"kind": "connect", "lead_id": ids["Acme"], "note": "connection request to Ravi Kumar"}, now - timedelta(days=2))
    li.mark_accepted(store, ids["Acme"], now - timedelta(days=1))
    plan = c.get(f"/api/leads/{ids['Acme']}/plan").json()
    steps = plan["linkedin"]["steps"]
    assert steps[0]["state"] == "done" and steps[0]["by"] == "LinkedIn agent (automatic)"
    assert steps[1]["state"] == "next" and steps[1]["due_at"]
    assert steps[3]["state"] == "skipped"                              # case study: no link set
    assert plan["linkedin"]["auto_started"] is True and plan["linkedin"]["accepted_at"]
    assert any(a["who"] == "Auto-start" for a in plan["automatic"]) and any(a["who"] == "LinkedIn agent" for a in plan["automatic"])
    assert plan["follow_ups"] and plan["follow_ups"][0]["title"].startswith("LinkedIn step 2")
    assert plan["email"] is None or plan["email"]["steps"]
    assert c.get("/api/leads/nope/plan").status_code == 404
