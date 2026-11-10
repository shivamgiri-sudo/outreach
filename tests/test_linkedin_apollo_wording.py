"""The built-in LinkedIn sequence is word for word the Apollo sequences "BMI – LinkedIn Connection – CXO" and
"BMI – Executive Hiring Conversations", with the same spacing, and queued steps pick up the current wording."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import linkedin_seq as li
from app.main import create_app

APOLLO_CONNECT = ("Hi Asha, came across your profile and noticed your work building and scaling teams. "
                  "Always interesting to connect with people in talent and leadership. Thought I'd reach out.")
APOLLO_FIRST_MESSAGE = ("Thanks for connecting, Asha.\n\nSomething I've been thinking about lately: How often do business plans get "
                        "held back simply because the right people aren't available when they're needed?\n\nCritical positions "
                        "remaining open for weeks can put considerable pressure on existing teams and business timelines.\n\n"
                        "Is this something you've come across in your organisation?")
LEAD = {"id": "x1", "contact_name": "Asha Rao", "title": "Head of Talent Acquisition", "company": "Acme"}


def test_steps_and_spacing_match_apollo():
    assert [(s["kind"], s["after"], s["days"]) for s in li.STEPS] == [
        ("connect", "start", 0), ("message", "accepted", 0), ("message", "previous", 4), ("message", "previous", 4),
        ("message", "previous", 4), ("message", "previous", 4), ("message", "previous", 5)]
    assert li.CONNECT_VARIANTS["A"] == li.CONNECT_VARIANTS["B"] == li.STEPS[0]["text"]


def test_rendered_text_is_the_apollo_text_for_every_title_and_variant():
    for variant in ("A", "B"):
        lead = {**LEAD, "linkedin_seq": {"variant": variant}}
        assert li.render(li.STEPS[0], lead, {}) == APOLLO_CONNECT
    assert li.render(li.STEPS[1], {**LEAD, "title": "CHRO"}, {}) == APOLLO_FIRST_MESSAGE      # no role-based hook any more


def test_platform_message_mentions_the_case_study_only_when_one_was_offered():
    step5 = li.STEPS[4]
    assert "case study" not in li.render(step5, LEAD, {})
    assert li.render(step5, LEAD, {"case_study_url": "https://x.test/cs"}).startswith(
        "Thanks, Asha.\n\nThe thinking behind the case study is closely connected")


def test_queue_shows_current_wording_for_a_step_queued_earlier(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": "company,contact_name,title,email,linkedin_url\nAcme,Asha Rao,CHRO,asha@acme.com,https://www.linkedin.com/in/asha-rao\n"})
    store = c.app.state.store
    lead_id = store.read("leads")[0]["id"]
    li.start(store, lead_id)
    store.update("tasks", lambda rows: [{**t, "text": "old wording"} if t.get("kind") == "linkedin" else t for t in rows])
    item = li.queue(store, datetime.now(timezone.utc) + timedelta(minutes=1))["due"][0]
    assert item["message"] == APOLLO_CONNECT


def test_default_connect_note_fits_a_free_account():
    from app import linkedin_seq as li
    from app.linkedin_config import stand_in_length
    assert stand_in_length(li.STEPS[0]["text"]) <= li.FREE_NOTE_LIMIT
    assert stand_in_length(li.SHORT_CONNECT) <= li.FREE_NOTE_LIMIT
    short = li.render_short({"contact_name": "Priya Sharma", "company": "Acme"}, {})
    assert short.startswith("Hi Priya,") and len(short) <= li.FREE_NOTE_LIMIT
