"""QA: LinkedIn sequence copy - every persona hook, every step and branch, the 300-character connection note.

LinkedIn messages are sent by a person; the app only writes them. Leads come from the messy QA fixture.
"""
import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import linkedin_seq as li
from app.main import create_app

FIX = Path(__file__).parent / "fixtures" / "templates-prompts-ai"
ROWS = [r for r in csv.DictReader((FIX / "messy_leads.csv").read_text(encoding="utf-8-sig").splitlines()) if r["email"]]
CASE = {"case_study_url": "https://bookmyinterview.ai/case-study"}


@pytest.mark.parametrize("title,key", [
    ("Founder & CEO", "ceo_founder"), ("Managing Director", "ceo_founder"), ("Owner", "ceo_founder"),
    ("Proprietor", "ceo_founder"), ("Director", "ceo_founder"), ("Co-founder", "ceo_founder"),
    ("CHRO", "chro"), ("Chief People Officer", "chro"), ("VP - Human Resources", "chro"), ("VP HR", "chro"),
    ("Head of HR", "chro"), ("HR Head", "chro"),
    # regression: these used to fall into the founder hook because 'director' was checked first
    ("Head - Human Resources", "chro"), ("HR Director", "chro"), ("Director - HR", "chro"), ("Director - Talent Acquisition", "ta_head"),
    ("Director of Recruitment", "recruitment_head"), ("Staffing Director", "recruitment_head"),
    ("Head of Talent Acquisition", "ta_head"), ("TA Lead", "ta_head"), ("Talent Acquisition Manager", "ta_head"),
    ("Recruitment Manager", "recruitment_head"), ("Sr. Recruiter", "recruitment_head"), ("Hiring Manager", "recruitment_head"),
    ("Business Head", "business_head"), ("General Manager", "business_head"), ("VP Sales", "business_head"),
    ("Accountant", "general"), ("", "general"), ("मालिक", "general"),
])
def test_persona_for_title(title, key):
    assert li.persona(title)[0] == key


def test_every_fixture_lead_gets_a_clean_message_for_every_step():
    hooks = {hook for _, _, hook in li.PERSONAS} | {li.DEFAULT_HOOK}
    seen = set()
    for row in ROWS:
        lead = {"contact_name": row["contact_name"], "title": row["title"]}
        seen.add(li.persona(row["title"])[0])
        for step in li.STEPS:
            text = li.render(step, lead, CASE)
            assert "{" not in text and "}" not in text and "Dr." not in text and "Mr." not in text
            if step["kind"] == "connect":
                assert len(text) <= li.NOTE_LIMIT and "bookmyinterview" not in text.lower()   # no pitch in the note
            if step["n"] == 2:
                assert any(h in text for h in hooks)
    assert seen == {"ceo_founder", "chro", "ta_head", "recruitment_head", "business_head", "general"}


def test_connection_note_is_cut_at_a_word_under_300_chars():
    long_name = "Venkata" + "ramanaswamy" * 12
    text = li.render(li.STEPS[0], {"contact_name": long_name}, {})
    assert len(text) <= li.NOTE_LIMIT and text.endswith("…") and not text[:-1].endswith(" ")
    plain = li.render(li.STEPS[0], {"contact_name": "Shri. RAMESH CHANDRA"}, {})
    assert plain.startswith("Hi Ramesh, ") and not plain.endswith("…")
    assert li.render(li.STEPS[0], {"contact_name": "HR Team"}, {}).startswith("Hi there, ")


def test_case_study_step_is_skipped_until_a_real_link_exists():
    assert 4 not in [s["n"] for s in li._active_steps({})]
    assert 4 in [s["n"] for s in li._active_steps(CASE)]


def client_with_lead(tmp_path, title="HR Director"):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    csv_text = ("company,contact_name,title,email,linkedin_url\n"
                f"KONARK BPO SERVICES OPC PVT LTD,Dr.Ravi Rao,{title},ravi.rao@konark-bpo-026.example,"
                "https://www.linkedin.com/in/qa-ravi-rao-026-example\n")
    c.post("/api/leads/import", json={"csv": csv_text, "source": "linkedin"})
    lid = c.get("/api/leads").json()[0]["id"]
    # Quick paste does not keep the profile link (see report), so set it the way the Leads drawer would.
    c.app.state.store.update_lead(lid, lambda l: {**l, "linkedin_url": "https://www.linkedin.com/in/qa-ravi-rao-026-example"})
    return c, lid


def test_branch_replies_fill_every_slot(tmp_path):
    c, lid = client_with_lead(tmp_path)
    url = f"/api/leads/{lid}/linkedin/branch"
    text = c.post(url, json={"key": "meeting_yes", "option_1": "Tue 4 pm", "option_2": "Wed 11 am"}).json()["text"]
    assert text.startswith("Thanks, Ravi.") and "Tue 4 pm or Wed 11 am" in text
    text = c.post(url, json={"key": "slot_confirmed", "date": "Wed 5 Nov", "time": "11:00 am IST"}).json()["text"]
    assert "Wed 5 Nov at 11:00 am IST" in text
    assert "BookMyInterview.ai" in c.post(url, json={"key": "what_does_it_do"}).json()["text"]
    assert c.post(url, json={"key": "case_study_yes"}).status_code == 400            # no link set yet
    assert c.post(url, json={"key": "nope"}).status_code == 400


@pytest.mark.parametrize("body,missing", [
    ({"key": "meeting_yes"}, "option 1 and option 2"),
    ({"key": "meeting_yes", "option_1": "Tue 4 pm", "option_2": "  "}, "option 2"),
    ({"key": "slot_confirmed", "date": "Wed 5 Nov"}, "time"),
])
def test_branch_with_empty_slots_is_refused(tmp_path, body, missing):
    """Regression: an empty slot produced 'Would  or  work for you?' ready to copy and send."""
    c, lid = client_with_lead(tmp_path)
    r = c.post(f"/api/leads/{lid}/linkedin/branch", json=body)
    assert r.status_code == 400 and missing in r.json()["detail"]


def test_steps_api_and_hr_director_hook(tmp_path):
    c, lid = client_with_lead(tmp_path, "HR Director")
    assert c.post(f"/api/leads/{lid}/linkedin/start").status_code == 200
    steps = c.get("/api/linkedin/steps").json()
    assert steps and all("text" in s or "purpose" in s for s in (steps if isinstance(steps, list) else steps["steps"]))
    lead = next(l for l in c.get("/api/leads").json() if l["id"] == lid)
    text = li.render(li.STEPS[1], lead, {})
    assert "maintain hiring quality" in text and text.startswith("Thanks for connecting, Ravi.")
