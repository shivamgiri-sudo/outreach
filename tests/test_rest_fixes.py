"""Queue grace, link moves between leads, same-slug lookup, link heal, suppressed upload rows."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import extension_api as ext, linkedin_heal, linkedin_seq as li
from app.activities import now_utc
from app.lead_import import run_import
from app.lead_merge import merge_leads
from app.main import create_app

CSV = ("company,contact_name,title,email,linkedin_url\n"
       "Acme,Rahul Sharma,CHRO,rahul@acme.example,https://www.linkedin.com/in/rahul-sharma-1\n"
       "Acme,Priya Nair,CHRO,priya@acme.example,https://www.linkedin.com/in/ACoAABCDEF\n"
       "Zed,Rahul S,CHRO,rs@zed.example,\n")


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


# ---- 1. grace ----
def test_follow_up_with_a_gap_waits_for_its_real_due_time(rig):
    store, ids = rig["store"], rig["ids"]
    now = now_utc()
    lid = ids["Rahul Sharma"]
    li.start(store, lid, now=now - timedelta(days=5))
    li.mark_sent(store, lid, 1, now=now - timedelta(days=5))
    li.mark_accepted(store, lid, now=now - timedelta(days=5))
    li.mark_sent(store, lid, 2, now=now - timedelta(days=4, hours=6))          # step 3 due in 4 days: 6 h from now - 0 = ~3.75 d
    # make the open task due in 3.5 days (inside the old 12 h window? no: 2 h away from now is inside; 8 h is inside the old one)
    store.update("tasks", lambda rows: [{**t, "due_at": (now + timedelta(hours=8)).isoformat()} if t.get("lead_id") == lid and t.get("status", "open") == "open" else t for t in rows])
    assert all(d["lead_id"] != lid for d in li.queue(store, now)["due"]), "step 3 (4-day gap) offered 8 h early"
    store.update("tasks", lambda rows: [{**t, "due_at": (now - timedelta(minutes=1)).isoformat()} if t.get("lead_id") == lid and t.get("status", "open") == "open" else t for t in rows])
    assert any(d["lead_id"] == lid for d in li.queue(store, now)["due"])


def test_step_one_and_first_message_keep_the_grace():
    assert li.grace_for(li.STEPS[0]) == li.GRACE and li.grace_for(li.STEPS[1]) == li.GRACE
    assert li.grace_for(li.STEPS[2]).total_seconds() == 0


# ---- 2. link never moves to another person ----
def _l(i, name, url="", **kw):
    return {"id": i, "company": "Acme", "contact_name": name, "linkedin_url": url, "email": f"{i}@acme.example", "timeline": [],
            "stage": "new", "source": "x", **kw}


def test_merge_leads_does_not_copy_a_link_to_a_name_that_is_only_compatible():
    keep, other = _l("a", "Rahul Sharma"), _l("b", "Rahul S", "https://www.linkedin.com/in/rahul-s-99")
    merged = merge_leads(keep, [other], "2026-11-12T00:00:00+00:00")
    assert not merged.get("linkedin_url")
    assert any(c["field"] == "linkedin_url" for c in merged.get("merge_conflicts") or [])


def test_merge_leads_keeps_own_link_and_fills_for_an_exact_name():
    a, b = _l("a", "Rahul Sharma", "https://www.linkedin.com/in/r-1"), _l("b", "Rahul Sharma", "https://www.linkedin.com/in/r-2")
    assert merge_leads(a, [b], "2026-11-12T00:00:00+00:00")["linkedin_url"].endswith("r-1")
    c = merge_leads(_l("c", "Rahul Sharma"), [_l("d", "Dr Rahul Sharma", "https://www.linkedin.com/in/r-3")], "2026-11-12T00:00:00+00:00")
    assert c["linkedin_url"].endswith("r-3")


# ---- 3. same slug, first hit not visible ----
def test_find_lead_tries_every_same_slug_lead(rig):
    store, ids = rig["store"], rig["ids"]
    store.update_lead(ids["Rahul S"], lambda l: {**l, "linkedin_url": "https://www.linkedin.com/in/rahul-sharma-1", "name_unused": 1})
    li.start(store, ids["Rahul S"])
    seen = []
    first = ext.find_lead(store, "https://www.linkedin.com/in/rahul-sharma-1", "Rahul Sharma", allow=lambda l: seen.append(l["id"]) or l["id"] == ids["Rahul Sharma"])
    assert first and first["id"] == ids["Rahul Sharma"]


# ---- 4. link heal ----
def _heal(rig, lid, url="https://www.linkedin.com/in/priya-nair-hr", name="Priya Nair"):
    return rig["c"].post(f"/api/extension/leads/{lid}/profile", json={"observed_url": url, "observed_name": name}, headers=rig["h"]).json()


def test_aco_link_is_healed_when_the_name_matches_and_the_sequence_is_rebound(rig):
    store, lid = rig["store"], rig["ids"]["Priya Nair"]
    li.start(store, lid)
    out = _heal(rig, lid)
    assert out["healed"] and out["url"].endswith("/in/priya-nair-hr")
    row = lead(store, lid)
    assert row["linkedin_url"] == out["url"] and not li.target_changed(row)
    assert any("LinkedIn link updated to" in e.get("text", "") and "page name matched" in e["text"] for e in row["timeline"])


def test_heal_refuses_other_names_member_ids_and_a_namesake_holding_the_vanity(rig):
    store, ids = rig["store"], rig["ids"]
    lid = ids["Priya Nair"]
    assert not _heal(rig, lid, name="Priya Nairobi")["healed"] and not _heal(rig, lid, name="Priya")["healed"]
    assert not _heal(rig, lid, url="https://www.linkedin.com/in/ACoAAZZZ")["healed"]
    store.update_lead(ids["Rahul S"], lambda l: {**l, "linkedin_url": "https://www.linkedin.com/in/priya-nair-hr"})
    out = _heal(rig, lid)
    assert not out["healed"] and "already holds" in out["reason"]
    assert lead(store, lid)["linkedin_url"].endswith("ACoAABCDEF")


def test_heal_refuses_a_sequence_whose_link_was_edited(rig):
    store, lid = rig["store"], rig["ids"]["Priya Nair"]
    li.start(store, lid)
    store.update_lead(lid, lambda l: {**l, "linkedin_url": "https://www.linkedin.com/in/ACoAAOTHER"})
    assert not _heal(rig, lid)["healed"]


def test_name_matching_is_exact():
    assert linkedin_heal.name_matches_exactly("Dr. Priya Nair, MBA (She/Her)", "Priya Nair")
    assert not linkedin_heal.name_matches_exactly("Nair Priya", "Priya Nair")
    assert not linkedin_heal.name_matches_exactly("Priya", "Priya")


# ---- 6. suppressed new rows are kept, flagged, reported ----
def test_suppressed_new_row_is_kept_flagged_and_listed():
    leads = []
    table = [(2, {"company": "Dnc Co", "contact_name": "Dan Nox", "email": "dan@dnc.example"}),
             (3, {"company": "Ok Co", "contact_name": "Oka Yes", "email": "oka@ok.example"})]
    cols = [{"column": c, "field": f, "confidence": 1} for c, f in (("company", "company"), ("contact_name", "contact_name"), ("email", "email"))]
    out = run_import(leads, table, cols, "t", {"dan@dnc.example"}, {})
    flagged = [l for l in leads if l.get("suppressed")]
    assert len(leads) == 2 and len(flagged) == 1 and flagged[0]["sendable"] is False
    assert out["suppressed_kept"][0]["lead_id"] == flagged[0]["id"] and out["summary"]["suppressed_kept"] == 1
    assert "do-not-contact" in out["suppressed_kept"][0]["reason"]
    again = run_import(leads, table[:1], cols, "t", {"dan@dnc.example"}, {})          # now it exists: skipped as before, not duplicated
    assert len(leads) == 2 and again["skipped"]


def test_a_do_not_contact_lead_cannot_start_a_linkedin_sequence(rig):
    store, lid = rig["store"], rig["ids"]["Priya Nair"]
    store.update_lead(lid, lambda l: {**l, "sendable": False, "not_sendable_reason": "do not contact"})
    with pytest.raises(ValueError):
        li.start(store, lid)
