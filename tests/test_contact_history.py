"""Contact history: every attempt (including unanswered calls) with time and who; counts; LinkedIn shown; one-off email."""
from tests.flow_helpers import lead, make_client, patch_lead, store_of


def test_unanswered_calls_and_linkedin_appear_in_history_and_summary(tmp_path):
    c = make_client(tmp_path)
    a = lead(c, "Acme")["id"]
    for outcome in ("no_answer", "busy", "no_answer", "connected"):
        assert c.post(f"/api/leads/{a}/activity", json={"type": "call", "outcome": outcome, "by": "Meena"}).status_code == 200
    patch_lead(c, a, timeline=[*lead(c, "Acme")["timeline"], {"at": "2026-11-06T06:00:00+00:00", "kind": "linkedin", "text": "Connection accepted"}])
    store_of(c).update("settings", lambda s: {**s, "linkedin_agent_log": [{"at": "2026-11-06T05:00:00+00:00", "kind": "connect", "lead_id": a, "note": "connection request to Ravi", "account": "acct-a"}]})
    items = c.get(f"/api/leads/{a}/activity").json()
    calls = [i for i in items if i["type"] == "call"]
    assert len(calls) == 4 and {i["status"] for i in calls} == {"no_answer", "busy", "connected"} and all(i["detail"] == "Meena" for i in calls)
    li = [i for i in items if i["type"] == "linkedin"]
    assert any("Connection accepted" in i["body"] for i in li) and any(i["detail"] == "acct-a" for i in li)
    s = c.get(f"/api/leads/{a}/history-summary").json()
    assert s["calls"]["attempts"] == 4 and s["calls"]["unanswered"] == 3 and s["calls"]["connected"] == 1
    assert s["calls"]["outcomes"]["no_answer"] == 2 and s["linkedin"]["last"] and s["total_touches"] >= 5


def test_one_off_email_is_sent_logged_and_safe(tmp_path):
    c = make_client(tmp_path)
    a = lead(c, "Acme")["id"]
    r = c.post(f"/api/leads/{a}/email-compose", json={"subject": "Quick question for {{company}}", "body": "{{greeting}}\n\nAre you hiring this month?"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "practice" and "Acme" in r.json()["subject"]
    m = [x for x in store_of(c).read("outbox") if x.get("step") == "One-off email"][-1]
    assert "Unsubscribe:" in m["body"] and m["manual"] is True and "{{" not in m["body"]
    assert lead(c, "Acme").get("sequence") is None                                   # the sequence is untouched
    assert c.post(f"/api/leads/{a}/email-compose", json={"subject": "Hi {{nope}}", "body": "x"}).status_code == 400
    assert c.post(f"/api/leads/{a}/email-compose", json={"subject": "", "body": "x"}).status_code == 400
    patch_lead(c, a, stage="unsubscribed")
    assert c.post(f"/api/leads/{a}/email-compose", json={"subject": "Hi", "body": "x"}).status_code == 400
    items = c.get(f"/api/leads/{a}/activity").json()
    assert any(i["type"] == "email" and i["title"].startswith("Quick question") for i in items)


def test_quick_paste_keeps_linkedin_links_city_and_sector(tmp_path):
    c = make_client(tmp_path, csv=None)
    csv = ("company,contact_name,email,Person Linkedin Url,city,industry\n"
           "Delta BPO,Asha Rao,asha@delta.test,linkedin.com/in/asha-rao-12?trk=x,Noida,BPO\n"
           "Bad Link Co,Ravi K,ravi@bad.test,https://example.com/in/ravi,Pune,IT\n")
    assert c.post("/api/leads/import", json={"csv": csv, "source": "apollo"}).json()["created"] == 2
    by = {l["company"]: l for l in c.get("/api/leads").json()}
    assert by["Delta BPO"]["linkedin_url"] == "https://www.linkedin.com/in/asha-rao-12"
    assert by["Delta BPO"]["city"] == "Noida" and by["Delta BPO"]["sector"] == "BPO"
    assert not by["Bad Link Co"].get("linkedin_url")
