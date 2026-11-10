"""Finding a LinkedIn profile for a lead without a link: the acceptance rule, the lookup list, the agent routes and the switch."""
from datetime import datetime, timezone

import pytest

from app import linkedin_agent as agent, linkedin_finder as finder, linkedin_seq as li
from tests.test_extension_api import bearer, make, pair



def card(name, headline="", url=None, location="Mumbai"):
    slug = (url or name.lower().replace(" ", "-")) if not (url or "").startswith("http") else None
    return {"name": name, "headline": headline, "location": location, "url": url if (url or "").startswith("http") else f"https://www.linkedin.com/in/{slug}"}


LEAD = {"contact_name": "Ravi Kumar", "company": "Globex Pvt Ltd"}


@pytest.mark.parametrize("lead, cards, status, picked", [
    (LEAD, [card("Ravi Kumar", "Head of TA at Globex")], "found", "ravi-kumar"),                                     # exact + company
    (LEAD, [card("Ravi Kumar", "Engineer at Initech")], "ambiguous", None),                                           # exact, other company
    (LEAD, [card("Ravi Kumar", "Head of TA at Globex"), card("Ravi Kumar", "Analyst at Globex", "https://www.linkedin.com/in/ravi-k2")],
     "ambiguous", None),                                                                                              # two exact: never pick
    (LEAD, [card("Ravi Kumar", "Head of TA at Globex"), card("Ravi Kumar", "Analyst", "https://www.linkedin.com/in/ravi-k2")],
     "ambiguous", None),                                                                                              # two exact, one at company
    (LEAD, [], "not_found", None),                                                                                    # no cards
    (LEAD, [card("Priya Shah", "Globex")], "not_found", None),                                                        # nobody with that name
    (LEAD, [card("Ravi Kumar Singh", "Globex")], "ambiguous", None),                                                  # not exact
    ({"contact_name": "José  Álvarez", "company": "ACME Ltd."}, [card("Jose Alvarez", "Director, Acme")], "found", "jose-alvarez"),   # accents, case, suffix
    ({"contact_name": "Mr. Ravi KUMAR", "company": "Globex"}, [card("Ravi Kumar, MBA", "CHRO | globex private limited", "https://www.linkedin.com/in/ravi-mba")], "found", "ravi-mba"),
    ({"contact_name": "Ravi Kumar", "company": "Glob"}, [card("Ravi Kumar", "Head at Globex")], "ambiguous", None),    # a part of a word is no mention
    ({"contact_name": "Ravi Kumar", "company": "Pvt Ltd"}, [card("Ravi Kumar", "Pvt Ltd")], "ambiguous", None),       # a company that is only a suffix
])
def test_acceptance_rule(lead, cards, status, picked):
    out = finder.decide(lead, cards)
    assert out["status"] == status
    assert (out["pick"] or {}).get("url", "").rsplit("/", 1)[-1] == (picked or "")
    assert len(out["candidates"]) <= finder.MAX_CANDIDATES
    if status != "found":
        assert out["pick"] is None


def test_links_that_are_not_profiles_and_duplicates_are_dropped_and_five_are_kept():
    cards = [{"name": "Ravi Kumar", "headline": "x", "url": "https://evil.example/in/ravi"},
             {"name": "Ravi Kumar", "headline": "x", "url": "https://www.linkedin.com/company/globex"},
             {"name": "Ravi Kumar", "headline": "x", "url": "https://www.linkedin.com/in/r0?trk=abc"},
             {"name": "Ravi Kumar", "headline": "x", "url": "https://www.linkedin.com/in/r0"}] + [
        card("Ravi Kumar", "x", f"https://www.linkedin.com/in/r{i}") for i in range(1, 9)]
    out = finder.decide(LEAD, cards)
    assert out["status"] == "ambiguous" and len(out["candidates"]) == 5
    assert all(c["url"].startswith("https://www.linkedin.com/in/") for c in out["candidates"])
    assert [c["url"] for c in out["candidates"]].count("https://www.linkedin.com/in/r0") == 1


@pytest.fixture()
def rig(tmp_path):
    c, owner, acme, globex = make(tmp_path)
    store = c.app.state.store
    store.update_lead(globex, lambda l: {**l, "contact_name": "Ravi Kumar", "company": "Globex", "linkedin_url": ""})
    agent.save_config(store, {"enabled": True, "start_hour": 0, "end_hour": 24, "weekdays_only": False, "gap_minutes": 3})
    return c, owner, globex, store, pair(c, owner)["token"]


def lead_of(store, lid):
    return next(l for l in store.read("leads") if l["id"] == lid)


def test_start_without_a_link_queues_a_lookup_and_a_nameless_lead_is_refused(rig):
    c, owner, lid, store, _ = rig
    r = c.post(f"/api/leads/{lid}/linkedin/start", headers=bearer(owner))
    assert r.status_code == 200 and r.json()["lookup"] == "queued" and r.json()["task"] is None
    look = lead_of(store, lid)["linkedin_lookup"]
    assert look["status"] == "queued" and "keywords=Ravi+Kumar+Globex" in look["search_url"] and not lead_of(store, lid).get("linkedin_seq")
    store.update_lead(lid, lambda l: {**l, "linkedin_lookup": None, "contact_name": ""})
    r = c.post(f"/api/leads/{lid}/linkedin/start", headers=bearer(owner))
    assert r.status_code == 400 and "full name" in r.json()["detail"]
    store.update_lead(lid, lambda l: {**l, "contact_name": "Ravi Kumar", "company": ""})
    assert c.post(f"/api/leads/{lid}/linkedin/start", headers=bearer(owner)).status_code == 400


def test_switch_off_keeps_the_old_refusal(rig):
    c, owner, lid, store, _ = rig
    r = c.put("/api/linkedin-agent", json={"find_profiles": False}, headers=bearer(owner))
    assert r.status_code == 200 and r.json()["config"]["find_profiles"] is False
    r = c.post(f"/api/leads/{lid}/linkedin/start", headers=bearer(owner))
    assert r.status_code == 400 and r.json()["detail"] == "Add the person's LinkedIn profile link first."
    assert not lead_of(store, lid).get("linkedin_lookup")
    assert agent.control(store)["may_lookup"] is False
    assert agent.config(store)["find_profiles"] is False and agent.DEFAULTS["find_profiles"] is True     # default ON


def test_bulk_start_counts_started_looking_up_and_refused(rig):
    c, owner, lid, store, _ = rig
    ids = [l["id"] for l in store.read("leads")]
    other = next(i for i in ids if i != lid)                       # has a link: starts at once
    made = c.post("/api/lists", json={"name": "t", "lead_ids": ids}, headers=bearer(owner)).json()
    r = c.post(f"/api/lists/{made['id']}/start-linkedin", headers=bearer(owner)).json()
    assert (r["started"], r["looking_up"], r["refused"]) == (1, 1, []) and lead_of(store, other)["linkedin_seq"]
    store.update_lead(lid, lambda l: {**l, "linkedin_lookup": None, "contact_name": ""})                 # no name: refused with the reason
    r = c.post(f"/api/lists/{made['id']}/start-linkedin", headers=bearer(owner)).json()
    assert r["looking_up"] == 0 and r["refused"] and any("full name" in x["reason"] for x in r["refused"])


def test_agent_gets_one_lead_at_a_time_and_a_found_profile_starts_the_sequence(rig):
    c, owner, lid, store, tok = rig
    c.post(f"/api/leads/{lid}/linkedin/start", headers=bearer(owner))
    nxt = c.get("/api/extension/lookup/next", headers=bearer(tok)).json()
    assert nxt["lead"]["lead_id"] == lid and nxt["lead"]["name"] == "Ravi Kumar"
    assert c.get("/api/extension/lookup/next", headers=bearer(tok)).json()["lead"] is None        # handed out once
    cards = [{"name": "Ravi Kumar", "headline": "Head of Talent at Globex", "location": "Pune", "url": "https://www.linkedin.com/in/ravi-k?miniProfileUrn=x"}]
    r = c.post(f"/api/extension/leads/{lid}/lookup-result", json={"cards": cards}, headers=bearer(tok))
    assert r.status_code == 200 and r.json()["status"] == "found"
    lead = lead_of(store, lid)
    assert lead["linkedin_url"] == "https://www.linkedin.com/in/ravi-k" and lead["linkedin_seq"]["status"] == "waiting_to_connect"
    line = [t for t in lead["timeline"] if "profile" in t["text"] and "matched name and company" in t["text"]][0]
    assert line["origin"]["trigger"] == "linkedin_agent" and line["origin"]["type"] == "automatic"
    step1 = [t for t in store.read("tasks") if t.get("lead_id") == lid and t.get("kind") == "linkedin"]
    assert step1 and step1[0]["title"].startswith("LinkedIn step 1")
    log = [r for r in agent.log_rows(store) if r["kind"] == "lookup"]
    assert len(log) == 1 and agent.counts(store, datetime.now(timezone.utc))["lookup_today"] == 1


def test_ambiguous_waits_for_a_person_and_one_click_starts(rig):
    c, owner, lid, store, tok = rig
    c.post(f"/api/leads/{lid}/linkedin/start", headers=bearer(owner))
    cards = [card("Ravi Kumar", "Engineer at Initech"), card("Ravi Kumar", "Analyst", "https://www.linkedin.com/in/ravi-k2")]
    r = c.post(f"/api/extension/leads/{lid}/lookup-result", json={"cards": cards}, headers=bearer(tok))
    assert r.json()["status"] == "ambiguous"
    lead = lead_of(store, lid)
    assert not lead.get("linkedin_url") and not lead.get("linkedin_seq") and len(lead["linkedin_lookup"]["candidates"]) == 2
    q = c.get("/api/linkedin/queue", headers=bearer(owner)).json()
    assert [f["lead_id"] for f in q["finding"]] == [lid] and q["finding"][0]["status"] == "ambiguous"
    assert c.post(f"/api/leads/{lid}/linkedin/find", json={"action": "pick", "url": "https://www.linkedin.com/in/not-listed"}, headers=bearer(owner)).status_code == 400
    r = c.post(f"/api/leads/{lid}/linkedin/find", json={"action": "pick", "url": "https://www.linkedin.com/in/ravi-k2"}, headers=bearer(owner))
    assert r.status_code == 200 and r.json()["lead"]["linkedin_url"].endswith("/in/ravi-k2") and r.json()["lead"]["linkedin_seq"]
    assert c.get("/api/linkedin/queue", headers=bearer(owner)).json()["finding"] == []


def test_not_found_paste_link_and_skip(rig):
    c, owner, lid, store, tok = rig
    c.post(f"/api/leads/{lid}/linkedin/start", headers=bearer(owner))
    assert c.post(f"/api/extension/leads/{lid}/lookup-result", json={"cards": []}, headers=bearer(tok)).json()["status"] == "not_found"
    assert c.post(f"/api/leads/{lid}/linkedin/find", json={"action": "link", "url": "https://example.com/in/x"}, headers=bearer(owner)).status_code == 400
    assert c.post(f"/api/leads/{lid}/linkedin/find", json={"action": "skip"}, headers=bearer(owner)).json()["status"] == "manual"
    assert c.get("/api/linkedin/queue", headers=bearer(owner)).json()["finding"] == []
    r = c.post(f"/api/leads/{lid}/linkedin/find", json={"action": "link", "url": "https://www.linkedin.com/in/ravi-pasted"}, headers=bearer(owner))
    assert r.status_code == 200 and r.json()["lead"]["linkedin_seq"]
    assert c.post(f"/api/leads/{lid}/linkedin/find", json={"action": "skip"}, headers=bearer(owner)).status_code == 400    # already running


def test_unreadable_search_page_twice_hands_over_to_a_person(rig):
    c, owner, lid, store, tok = rig
    c.post(f"/api/leads/{lid}/linkedin/start", headers=bearer(owner))
    for expected in ("queued", "manual"):
        assert c.post(f"/api/extension/leads/{lid}/lookup-result", json={"cards": [], "error": "page did not load"}, headers=bearer(tok)).json()["status"] == expected


def test_lookup_honours_switch_hours_pause_kill_cap_and_gap(rig):
    c, owner, lid, store, tok = rig
    c.post(f"/api/leads/{lid}/linkedin/start", headers=bearer(owner))
    assert c.get("/api/extension/lookup/next", headers=bearer(tok)).json()["lead"]["lead_id"] == lid
    store.update_lead(lid, lambda l: {**l, "linkedin_lookup": {**l["linkedin_lookup"], "handed_at": None}})
    agent.save_config(store, {"lookups_per_day": 1})
    agent.record(store, {"kind": "lookup", "lead_id": lid})
    ctl = agent.control(store)
    assert ctl["may_lookup"] is False and ctl["lookups_left_today"] == 0 and ctl["wait_seconds"] > 0           # cap and the shared gap
    assert c.get("/api/extension/lookup/next", headers=bearer(tok)).json()["lead"] is None
    agent.save_config(store, {"lookups_per_day": 15, "enabled": False})
    assert agent.control(store)["may_lookup"] is False
    assert c.post(f"/api/extension/leads/{lid}/lookup-result", json={"cards": []}, headers=bearer(tok)).status_code == 409
    agent.save_config(store, {"enabled": True})
    c.put("/api/linkedin-agent/kill", json={"on": True}, headers=bearer(owner))
    assert agent.control(store)["may_lookup"] is False
    assert c.post(f"/api/extension/leads/{lid}/lookup-result", json={"cards": []}, headers=bearer(tok)).status_code == 409


def test_lookups_per_day_is_clamped_to_the_ceiling_and_range(rig):
    c, owner, lid, store, tok = rig
    assert agent.config(store)["lookups_per_day"] == 15
    with pytest.raises(ValueError):
        agent.save_config(store, {"lookups_per_day": 99})
    assert "lookups_per_day" in __import__("app.linkedin_scope", fromlist=["x"]).CEILINGS
