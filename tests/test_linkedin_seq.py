"""LinkedIn sequence: a person sends, the app schedules, personalises, waits for acceptance and stops on replies."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import linkedin_seq as li
from app.main import create_app

T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)
CSV = ("company,contact_name,title,email,phone,website,open_roles,company_size,country\n"
       "Acme,Ravi Kumar,Head of Talent Acquisition,ravi@acme.com,98765 43210,https://acme.com,12,200,IN\n")


def make(tmp_path, linkedin="https://www.linkedin.com/in/ravi-kumar-example"):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    lid = c.get("/api/leads").json()[0]["id"]
    if linkedin:
        c.app.state.store.update_lead(lid, lambda l: {**l, "linkedin_url": linkedin})
    return c, lid


def li_tasks(c, lid):
    return [t for t in c.app.state.store.read("tasks") if t.get("lead_id") == lid and t.get("kind") == "linkedin"]


def test_personalisation_and_note_limit():
    lead = {"contact_name": "Mr. RAVI KUMAR", "title": "Head of Talent Acquisition"}
    assert li.persona("Founder & CEO")[0] == "ceo_founder" and li.persona("CHRO")[0] == "chro"
    assert li.persona("Head of Talent Acquisition")[0] == "ta_head" and li.persona("Accountant")[0] == "general"
    note = li.render(li.STEPS[0], lead, {})
    assert note.startswith("Hi Ravi,") and len(note) <= li.NOTE_LIMIT
    msg2 = li.render(li.STEPS[1], lead, {})
    assert "recruiters spending more time processing applications" in msg2      # the TA-head hook


def test_start_needs_a_profile_link_and_never_restarts(tmp_path):
    c, lid = make(tmp_path, linkedin="")
    assert c.post(f"/api/leads/{lid}/linkedin/start").status_code == 400
    c.app.state.store.update_lead(lid, lambda l: {**l, "linkedin_url": "javascript:alert(1)"})
    assert c.post(f"/api/leads/{lid}/linkedin/start").status_code == 400
    c2, lid2 = make(tmp_path / "b")
    r = c2.post(f"/api/leads/{lid2}/linkedin/start").json()
    assert r["task"]["title"] == "LinkedIn step 1: send connection request" and r["task"]["owner"] == "Priya"
    assert c2.post(f"/api/leads/{lid2}/linkedin/start").status_code == 400


def test_messages_wait_for_acceptance_then_follow_the_rhythm(tmp_path):
    c, lid = make(tmp_path)
    store = c.app.state.store
    li.start(store, lid, T0)
    li.mark_sent(store, lid, 1, T0)
    assert [t for t in li_tasks(c, lid) if t["status"] == "open"] == []          # nothing until they accept
    acc = li.mark_accepted(store, lid, T0 + timedelta(days=1))
    assert acc["next_task"]["title"] == "LinkedIn step 2: send message"
    assert acc["next_task"]["due_at"] == (T0 + timedelta(days=3)).isoformat(timespec="seconds").replace("+00:00", "+00:00")
    nxt = li.mark_sent(store, lid, 2, T0 + timedelta(days=3))["next_task"]
    assert nxt["title"].startswith("LinkedIn step 3") and nxt["due_at"].startswith((T0 + timedelta(days=7)).date().isoformat())
    nxt = li.mark_sent(store, lid, 3, T0 + timedelta(days=7))["next_task"]
    assert nxt["title"].startswith("LinkedIn step 5")                            # step 4 skipped: no case-study link yet


def test_case_study_step_appears_once_a_real_link_is_set(tmp_path):
    c, lid = make(tmp_path)
    assert c.put("/api/linkedin/settings", json={"case_study_url": "http://x"}).status_code == 400
    c.put("/api/linkedin/settings", json={"case_study_url": "https://bookmyinterview.ai/case-study"})
    store = c.app.state.store
    li.start(store, lid, T0); li.mark_accepted(store, lid, T0)
    li.mark_sent(store, lid, 2, T0); nxt = li.mark_sent(store, lid, 3, T0)["next_task"]
    assert nxt["title"].startswith("LinkedIn step 4")
    branch = c.post(f"/api/leads/{lid}/linkedin/branch", json={"key": "case_study_yes"}).json()["text"]
    assert "https://bookmyinterview.ai/case-study" in branch and branch.startswith("Absolutely, Ravi.")


def test_any_reply_stops_linkedin_steps(tmp_path):
    c, lid = make(tmp_path)
    store = c.app.state.store
    li.start(store, lid, T0); li.mark_accepted(store, lid, T0)
    c.post(f"/api/leads/{lid}/reply", json={"text": "How much does it cost?"})       # replied by email
    lead = c.get(f"/api/leads/{lid}").json()
    assert lead["linkedin_seq"]["status"] == "stopped" and all(t["status"] == "done" for t in li_tasks(c, lid))


def test_linkedin_reply_is_read_like_any_reply(tmp_path):
    c, lid = make(tmp_path)
    store = c.app.state.store
    li.start(store, lid, T0); li.mark_accepted(store, lid, T0)
    r = c.post(f"/api/leads/{lid}/linkedin/replied", json={"text": "Yes, happy to talk next week"}).json()
    assert r["label"] == "interested" and r["lead"]["stage"] == "interested"
    assert any(t["kind"] == "call" for t in store.read("tasks"))                     # Priya gets the call task


def test_queue_shows_due_messages_and_suggestions_within_the_daily_limit(tmp_path):
    c, lid = make(tmp_path)
    c.put("/api/linkedin/settings", json={"linkedin_daily_connections": 5})
    q = c.get("/api/linkedin/queue").json()
    assert q["suggestions"][0]["lead_id"] == lid and q["daily_limit"] == 5
    c.post(f"/api/leads/{lid}/linkedin/start")
    q = c.get("/api/linkedin/queue").json()
    assert q["due"][0]["step"] == 1 and q["due"][0]["message"].startswith("Hi Ravi,") and q["suggestions"] == []
    c.post(f"/api/leads/{lid}/linkedin/sent", json={"step": 1})
    assert c.get("/api/linkedin/queue").json()["waiting_for_acceptance"][0]["lead_id"] == lid


def test_unsubscribed_leads_are_never_put_on_linkedin(tmp_path):
    c, lid = make(tmp_path)
    c.post(f"/u/{lid}")
    assert c.post(f"/api/leads/{lid}/linkedin/start").status_code == 400
