"""AUDIT (data side): lead edits / manual routes / inbox matching / rendering / finder. FAIL = bug present."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import linkedin_seq as li, linkedin_finder as finder, extension_api as ext, audit as lead_ops
from app.main import create_app

CSV = ("company,contact_name,title,email,linkedin_url\n"
       "Acme,Rahul Sharma,CHRO,rahul@acme.example,https://www.linkedin.com/in/rahul-a\n"
       "Zeta,Priya Nair,CHRO,priya@zeta.example,https://www.linkedin.com/in/priya-n\n")
ACTOR = {"name": "t", "email": "t@x", "role": "owner"}
OLD = datetime.now(timezone.utc) - timedelta(days=3)


@pytest.fixture
def rig(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV})
    store = c.app.state.store
    ids = {l["contact_name"]: l["id"] for l in store.read("leads")}
    return c, store, ids


def lead(store, lid):
    return next(l for l in store.read("leads") if l["id"] == lid)


def open_tasks(store, lid):
    return [t for t in store.read("tasks") if t.get("lead_id") == lid and t.get("kind") == "linkedin" and t.get("status", "open") == "open"]


def connected(store, lid):
    li.start(store, lid, now=OLD); li.mark_sent(store, lid, 1, now=OLD); li.mark_accepted(store, lid, now=OLD)


# ---- (2) URL validation on edit
@pytest.mark.parametrize("url", ["https://www.linkedin.com/company/acme", "https://www.linkedin.com/sales/lead/ACwAAB1,NAME_SEARCH",
                                 "https://www.linkedin.com/search/results/people/?keywords=rahul", "https://www.linkedin.com/jobs/view/123"])
def test_edit_contact_rejects_non_person_linkedin_url(rig, url):
    c, store, ids = rig
    try:
        out = lead_ops.edit_contact(store, ids["Rahul Sharma"], {"linkedin_url": url}, ACTOR)
    except lead_ops.EditRefused:
        return
    assert lead(store, ids["Rahul Sharma"])["linkedin_url"] != url, "non-/in/ LinkedIn link saved as the person's profile"


# ---- (4) lead edited while on the sequence
def test_changing_linkedin_url_on_running_sequence_does_not_carry_state_to_new_person(rig):
    c, store, ids = rig
    lid = ids["Rahul Sharma"]
    connected(store, lid)                                     # Rahul A accepted; step 2 task queued
    lead_ops.edit_contact(store, lid, {"linkedin_url": "https://www.linkedin.com/in/rahul-b", "contact_name": "Rahul Verma"}, ACTOR)
    seq = lead(store, lid).get("linkedin_seq") or {}
    assert not seq.get("accepted_at") and seq.get("status") != "messaging", "new person inherits 'connected'; step 2 goes out without a connection request"


def test_name_change_while_queued_does_not_rewrite_text_for_old_profile(rig):
    """Queue renders 'Hi <new name>' but still points at the OLD profile URL."""
    c, store, ids = rig
    lid = ids["Rahul Sharma"]
    li.start(store, lid)
    lead_ops.edit_contact(store, lid, {"contact_name": "Priya Menon"}, ACTOR)
    row = next((d for d in li.queue(store)["due"] if d["lead_id"] == lid), None)      # None = reset, nothing queued: also safe
    assert row is None or not ("Priya" in row["message"] and row["linkedin_url"].endswith("rahul-a")), "Hi Priya queued against rahul-a's profile"


# ---- (7) manual routes: no step / state validation
def test_manual_sent_must_be_the_next_step(rig):
    c, store, ids = rig
    lid = ids["Rahul Sharma"]
    connected(store, lid)                                     # step 2 is next
    r = c.post(f"/api/leads/{lid}/linkedin/sent", json={"step": 6})
    assert r.status_code >= 400 or 2 in (lead(store, lid)["linkedin_seq"]["done"]), f"step 6 recorded; steps 2-5 are now skipped: {lead(store, lid)['linkedin_seq']['done']}"


def test_manual_sent_message_step_before_acceptance_is_refused(rig):
    c, store, ids = rig
    lid = ids["Rahul Sharma"]
    li.start(store, lid, now=OLD); li.mark_sent(store, lid, 1, now=OLD)       # waiting_for_acceptance
    r = c.post(f"/api/leads/{lid}/linkedin/sent", json={"step": 2})
    assert r.status_code >= 400, "a message step recorded while the connection is still pending"
    assert lead(store, lid)["linkedin_seq"]["status"] == "waiting_for_acceptance"


def test_manual_accepted_before_connect_request_sent_is_refused(rig):
    c, store, ids = rig
    lid = ids["Rahul Sharma"]
    li.start(store, lid)                                       # nothing sent yet
    r = c.post(f"/api/leads/{lid}/linkedin/accepted")
    assert r.status_code >= 400 and not lead(store, lid)["linkedin_seq"].get("accepted_at"), "accepted with no request sent; step 2 queued"


def test_accepted_twice_does_not_duplicate_tasks_or_rewind(rig):
    c, store, ids = rig
    lid = ids["Rahul Sharma"]
    connected(store, lid)
    li.mark_sent(store, lid, 2, now=OLD)
    li.mark_sent(store, lid, 3, now=OLD)                       # step 4 skipped (no case study) -> step 5 queued
    c.post(f"/api/leads/{lid}/linkedin/accepted")
    c.post(f"/api/leads/{lid}/linkedin/accepted")
    assert len(open_tasks(store, lid)) <= 1, [t["title"] for t in open_tasks(store, lid)]


def test_mark_sent_twice_is_idempotent(rig):
    c, store, ids = rig
    lid = ids["Rahul Sharma"]
    connected(store, lid)
    li.mark_sent(store, lid, 2, now=OLD)
    li.mark_sent(store, lid, 2, now=OLD)
    assert len(open_tasks(store, lid)) <= 1 and len([e for e in lead(store, lid)["timeline"] if e["text"] == "LinkedIn step 2 sent"]) == 1


# ---- (10) inbox matching
def _active(store, lid):
    li.start(store, lid, now=OLD); li.mark_sent(store, lid, 1, now=OLD)


def test_inbox_name_only_match_does_not_stop_sequence_of_a_namesake(rig):
    """A stranger called 'Rahul Sharma' writes to us; the only lead on the sequence with that name is a different Rahul Sharma."""
    c, store, ids = rig
    _active(store, ids["Rahul Sharma"])
    out = li.match_inbox(store, "li-a", [{"name": "Rahul Sharma", "from_them": True, "text": "Who are you? Not interested."}])
    assert out == [], "reply from an unrelated namesake attributed to the lead; sequence stopped"


def test_inbox_blank_or_non_latin_name_never_matches_a_lead(rig):
    c, store, ids = rig
    store.update_lead(ids["Priya Nair"], lambda l: {**l, "contact_name": ""})
    _active(store, ids["Priya Nair"])
    out = li.match_inbox(store, "li-a", [{"name": "राहुल शर्मा", "from_them": True, "text": "hello"}, {"name": "😀", "from_them": True, "text": "x"}])
    assert out == [], "non-Latin/emoji/blank inbox name collapses to key '' and matches the lead with no name"


def test_inbox_short_name_variant_matches_nothing_wrongly(rig):
    c, store, ids = rig
    store.update_lead(ids["Rahul Sharma"], lambda l: {**l, "contact_name": "Rahul"})
    _active(store, ids["Rahul Sharma"])
    out = li.match_inbox(store, "li-a", [{"name": "Rahul Gupta", "from_them": True, "text": "no"}])
    assert out == [], "single-word lead name 'Rahul' matched 'Rahul Gupta'" if out else True


# ---- (5) personalisation
@pytest.mark.parametrize("name,expect", [("Sharma, Rahul", "Rahul"), ("Dr. Rahul Sharma", "Rahul"), ("RAHUL SHARMA", "Rahul"),
                                         ("Rahul S.", "Rahul"), ("Mr Rahul", "Rahul")])
def test_first_name_extraction(name, expect):
    text = li.render(li.STEPS[0], {"contact_name": name, "company": "Acme"}, {})
    assert text.startswith(f"Hi {expect},"), text[:30]


@pytest.mark.parametrize("name", ["", "   ", "Rahul😀", "राहुल शर्मा", "Sales Team", "Info"])
def test_odd_names_fall_back_cleanly(name):
    text = li.render(li.STEPS[0], {"contact_name": name, "company": "Acme"}, {})
    assert "{" not in text and "😀" not in text and not text.startswith("Hi ,"), text[:40]


# ---- (3) finder auto-accept
def test_finder_company_prefix_is_not_a_match():
    lead_ = {"contact_name": "Ravi Kumar", "company": "Tata"}
    out = finder.decide(lead_, [{"name": "Ravi Kumar", "headline": "Manager at Tata Motors", "url": "https://www.linkedin.com/in/ravi-tm"}])
    assert out["status"] != "found", "company 'Tata' auto-accepted a Tata Motors employee"


def test_finder_previous_employer_in_headline_is_not_a_match():
    lead_ = {"contact_name": "Ravi Kumar", "company": "Infosys"}
    out = finder.decide(lead_, [{"name": "Ravi Kumar", "headline": "Ex-Infosys | Director at Wipro", "url": "https://www.linkedin.com/in/ravi-w"}])
    assert out["status"] != "found", "headline mentions Infosys only as a former employer"


def test_finder_single_exact_card_on_first_page_with_common_name_is_not_auto_accepted_when_title_disagrees():
    lead_ = {"contact_name": "Amit Shah", "company": "Acme", "title": "Chief Human Resources Officer"}
    out = finder.decide(lead_, [{"name": "Amit Shah", "headline": "Intern at Acme", "url": "https://www.linkedin.com/in/amit-intern"}])
    assert out["status"] != "found", "intern auto-accepted as the CHRO"
