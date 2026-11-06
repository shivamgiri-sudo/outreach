"""The Today list: what to do next, most urgent first, each item linked to its lead."""
from fastapi.testclient import TestClient

from app.main import create_app

CSV = ("company,contact_name,email,phone,website,open_roles,company_size,country\n"
       "Acme,Ravi Kumar,ravi@acme.in,9876543210,https://acme.in,40,200,IN\n"
       "Beta,Sam Rao,sam@beta.in,9876500000,https://beta.in,2,50,IN\n")


def make(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    return c


def by_company(c, name):
    return next(l for l in c.get("/api/leads").json() if l["company"] == name)


def test_empty_app_says_what_to_do_first(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    today = c.get("/api/today").json()
    assert today["items"] == [] and "Import" in today["summary"]


def test_reply_needing_a_person_becomes_a_task_and_can_be_done(tmp_path):
    c = make(tmp_path)
    lid = by_company(c, "Beta")["id"]
    c.post(f"/api/leads/{lid}/reply", json={"text": "How much does it cost?"})
    items = c.get("/api/today").json()["items"]
    task = next(i for i in items if i["kind"] == "task")
    assert task["lead_id"] == lid and task["title"] == "Answer a question" and task["company"] == "Beta"
    assert c.post("/api/tasks/done", json={"task_index": task["task_index"]}).status_code == 200
    assert not any(i["kind"] == "task" for i in c.get("/api/today").json()["items"])


def test_yes_leads_appear_as_calls_until_a_call_is_logged(tmp_path):
    c = make(tmp_path)
    lid = by_company(c, "Acme")["id"]
    c.post(f"/api/leads/{lid}/reply", json={"text": "Yes please"})
    calls = [i for i in c.get("/api/today").json()["items"] if i["kind"] == "call"]
    assert calls and calls[0]["lead_id"] == lid and "PD" in calls[0]["detail"]
    c.post(f"/api/leads/{lid}/note", json={"text": "Call outcome: Call back later", "kind": "call"})
    assert not [i for i in c.get("/api/today").json()["items"] if i["kind"] == "call"]


def test_send_problems_come_first(tmp_path):
    c = make(tmp_path)
    lid = by_company(c, "Beta")["id"]
    c.post(f"/api/leads/{lid}/reply", json={"text": "How much does it cost?"})
    c.post(f"/api/leads/{by_company(c, 'Acme')['id']}/enroll")
    store = c.app.state.store
    store.update("leads", lambda rows: [{**r, "sequence": {**r["sequence"], "in_flight": {"day": 0}}} if r.get("sequence") else r for r in rows])
    items = c.get("/api/today").json()["items"]
    assert items[0]["kind"] == "problem" and "outbox" in items[0]["detail"].lower()


def test_summary_counts_the_day(tmp_path):
    c = make(tmp_path)
    c.post(f"/api/leads/{by_company(c, 'Acme')['id']}/reply", json={"text": "Yes please"})
    c.post(f"/api/leads/{by_company(c, 'Beta')['id']}/reply", json={"text": "How much does it cost?"})
    summary = c.get("/api/today").json()["summary"]
    assert "1 call" in summary and "1 reply" in summary


def test_task_without_a_lead_names_no_company(tmp_path):
    """QA sweep: a task typed on the Tasks page showed '· a lead' and an 'Open lead' button that opened nothing."""
    c = make(tmp_path)
    c.post("/api/tasks", json={"title": "Prepare Monday call notes", "due_at": "2026-11-06T06:00:00+00:00"})
    item = next(i for i in c.get("/api/today").json()["items"] if i["title"] == "Prepare Monday call notes")
    assert item["lead_id"] in (None, "") and item["company"] == ""
