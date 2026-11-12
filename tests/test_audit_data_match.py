"""AUDIT (data side): /api/extension/match and queue can point the agent at the wrong person. FAIL = bug present."""
import pytest
from fastapi.testclient import TestClient

from app import linkedin_seq as li, extension_api as ext
from app.main import create_app

CSV = ("company,contact_name,title,email,linkedin_url\n"
       "Acme,Rahul Sharma,CHRO,rahul@acme.example,https://www.linkedin.com/in/rahul-a\n"
       "Acme,Priya Nair,CHRO,priya@acme.example,https://www.linkedin.com/in/priya-n\n"
       "Other,Meera Rao,Founder,m@o.example,https://www.linkedin.com/in/meera-rao\n")


@pytest.fixture
def rig(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV})
    store = c.app.state.store
    ids = {l["contact_name"]: l["id"] for l in store.read("leads")}
    tok = c.post("/api/extension/pair", json={"label": "a"}).json()["token"]
    return {"c": c, "store": store, "ids": ids, "h": {"Authorization": f"Bearer {tok}", "X-LinkedIn-Account": "li-a"}}


def lead(store, lid):
    return next(l for l in store.read("leads") if l["id"] == lid)


def test_match_name_company_fallback_never_returns_lead_with_a_different_profile(rig):
    """Rahul Sharma (Acme) is on /in/rahul-a. A DIFFERENT Rahul Sharma at Acme (/in/rahul-b) is open in the browser.
    The slug does not match, so find_lead falls back to name+company and returns Rahul A's lead -> A's message on B's profile."""
    store = rig["store"]
    found = ext.find_lead(store, "https://www.linkedin.com/in/rahul-b", "Rahul Sharma", "Acme")
    assert found is None, "name+company fallback matched a lead that already has a different /in/ slug"


def test_match_endpoint_same_name_other_profile_is_no_match(rig):
    c, h, ids, store = rig["c"], rig["h"], rig["ids"], rig["store"]
    li.start(store, ids["Rahul Sharma"])
    r = c.get("/api/extension/match", params={"profile_url": "https://www.linkedin.com/in/rahul-b", "name": "Rahul Sharma", "company": "Acme"}, headers=h).json()
    assert not r["matched"], "wrong-person: lead for rahul-a described for profile rahul-b"


def test_two_leads_same_slug_prefers_the_live_one_not_first_row(rig):
    """Two leads share one URL (wrong URL on Priya's row after a column shift). First row wins in find_lead, whatever its name."""
    c, h, ids, store = rig["c"], rig["h"], rig["ids"], rig["store"]
    store.update_lead(ids["Priya Nair"], lambda l: {**l, "linkedin_url": "https://www.linkedin.com/in/rahul-a"})   # Priya carries Rahul's URL
    store.update("leads", lambda rows: sorted(rows, key=lambda l: l["contact_name"] != "Priya Nair"))            # Priya's row first
    li.start(store, ids["Rahul Sharma"])
    # Priya's row is NOT on the sequence. The profile page belongs to 'Rahul Sharma'.
    r = c.get("/api/extension/match", params={"profile_url": "https://www.linkedin.com/in/rahul-a", "name": "Rahul Sharma"}, headers=h).json()
    assert r["matched"] and r["lead_id"] == ids["Rahul Sharma"], f"match returned {r['name']!r}: first row wins, name on the page is ignored"


def test_match_refuses_when_page_name_conflicts_with_lead_name(rig):
    """Slug matches but the name on the page is a different person (lead URL is wrong). Must not offer a message."""
    c, h, ids, store = rig["c"], rig["h"], rig["ids"], rig["store"]
    store.update_lead(ids["Priya Nair"], lambda l: {**l, "linkedin_url": "https://www.linkedin.com/in/rahul-a"})
    from datetime import datetime, timedelta, timezone
    old = datetime.now(timezone.utc) - timedelta(days=3)
    li.start(store, ids["Priya Nair"], now=old)
    li.mark_sent(store, ids["Priya Nair"], 1, now=old)
    li.mark_accepted(store, ids["Priya Nair"], now=old)
    store.update_lead(ids["Rahul Sharma"], lambda l: {**l, "linkedin_url": ""})
    r = c.get("/api/extension/match", params={"profile_url": "https://www.linkedin.com/in/rahul-a", "name": "Rahul Sharma", "company": "Acme"}, headers=h).json()
    assert not r.get("can_send"), "'Hi Priya' text offered on Rahul Sharma's page"


def test_queue_profile_url_is_always_an_in_profile(rig):
    """A lead whose link is a company / sales-navigator / search URL is queued with that URL as profile_url."""
    c, h, ids, store = rig["c"], rig["h"], rig["ids"], rig["store"]
    for url in ("https://www.linkedin.com/company/acme", "https://www.linkedin.com/sales/lead/ACwAAB123,NAME_SEARCH",
                "https://www.linkedin.com/search/results/people/?keywords=rahul"):
        store.update_lead(ids["Meera Rao"], lambda l: {**l, "linkedin_url": url, "linkedin_seq": None})
        store.update("tasks", lambda rows: [t for t in rows if t.get("lead_id") != ids["Meera Rao"]])
        try:
            li.start(store, ids["Meera Rao"])
        except ValueError:
            continue                                                            # refused at start = safe
        q = c.get("/api/extension/queue", headers=h).json()
        row = next((d for d in q["due"] if d["lead_id"] == ids["Meera Rao"]), None)
        assert row is None or row["profile_url"] == "", f"agent is sent to {row['profile_url']}"
        store.update_lead(ids["Meera Rao"], lambda l: {**l, "linkedin_seq": None})


def test_has_profile_link_accepts_only_person_profiles():
    for bad in ("https://www.linkedin.com/company/acme", "https://www.linkedin.com/sales/lead/ACw1", "https://www.linkedin.com/jobs/view/1",
                "https://www.linkedin.com/in/", "https://www.linkedin.com/feed/", "https://linkedin.com/"):
        assert not li.has_profile_link({"linkedin_url": bad}), bad


def test_describe_refuses_non_person_url_even_if_sequence_running(rig):
    c, h, ids, store = rig["c"], rig["h"], rig["ids"], rig["store"]
    li.start(store, ids["Meera Rao"])
    store.update_lead(ids["Meera Rao"], lambda l: {**l, "linkedin_url": "https://www.linkedin.com/company/other"})
    info = ext.describe(store, lead(store, ids["Meera Rao"]), "li-a")
    assert not info["can_send"], "send allowed with a company-page URL on the lead"


def test_message_without_open_task_respects_gap_days(rig):
    """The 4-day gap lives only in the task's due_at. If the task is closed/removed (Tasks page 'done'), the next step is sendable at once."""
    c, h, ids, store = rig["c"], rig["h"], rig["ids"], rig["store"]
    from datetime import datetime, timedelta, timezone
    lid = ids["Rahul Sharma"]
    li.start(store, lid)
    li.mark_sent(store, lid, 1)
    li.mark_accepted(store, lid)
    li.mark_sent(store, lid, 2, now=datetime.now(timezone.utc) - timedelta(days=1))   # step 3 due in 3 days
    store.update("tasks", lambda rows: [{**t, "status": "done", "done": True} if t.get("lead_id") == lid else t for t in rows])
    info = ext.describe(store, lead(store, lid), "li-a")
    assert not info["can_send"], "step 3 offered 1 day after step 2 (gap is 4 days) once its task was closed"
