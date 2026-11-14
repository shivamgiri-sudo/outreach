"""Merging duplicate leads: dry run, field fill, compliance, scoping, caps, every record re-pointed, undo."""
import copy
from datetime import datetime, timedelta, timezone

import pytest

from app import merge_flow
from tests.ownership_helpers import rig as make_rig

URL = "/api/lead-duplicates/merge"


@pytest.fixture
def r(tmp_path): return make_rig(tmp_path)


def mk(r, lid, company, ws="main", **kw):
    row = {"id": lid, "company": company, "contact_name": kw.pop("contact_name", ""), "title": "", "email": "", "phone": "", "website": "", "domain": "",
           "country": "", "source": "apollo", "stage": "new", "consent": None, "engagement": {"opens": 0, "clicks": 0, "replies": 0},
           "timeline": [{"at": "2026-01-02T00:00:00+00:00", "kind": "created", "text": f"Imported {lid}"}], "created_at": "2026-01-02T00:00:00+00:00",
           "dedupe_key": f"k-{lid}", "tags": [], "owner": "", "workspace": ws, **kw}
    r.store.update("leads", lambda rows: [*rows, row])
    return row


def get(r, lid): return next((l for l in r.store.read("leads") if l["id"] == lid), None)


def merge(r, keep, others, who="owner", dry=False, headers=None):
    return r.c.post(URL, json={"keep_id": keep, "merge_ids": others, "dry_run": dry}, headers=headers or r.h[who])


def dupes(r):
    keep = mk(r, "K1", "Dup Co", contact_name="Asha", email="asha@dup.in", phone="9811111111", tags=["a"], owner="Priya", stage="contacted",
              created_at="2026-03-01T00:00:00+00:00")
    other = mk(r, "D1", "Dup Co Pvt", contact_name="Asha", email="asha2@dup.in", phone="9822222222", title="Head", tags=["b", "a"], owner="Ravi",
               stage="engaged", created_at="2026-02-01T00:00:00+00:00", city="Pune")
    return keep, other


COLLS = [c for c, _, _ in merge_flow.REFERENCES]


def seed_refs(r, lid):
    for i, c in enumerate(COLLS):
        r.store.update(c, lambda rows, c=c: [*rows, {"id": f"{c}-{lid}", "lead_id": lid, "note": c}])
    r.store.update("settings", lambda s: {**s, "linkedin_agent_log": [*(s.get("linkedin_agent_log") or []), {"at": "x", "kind": "skip", "lead_id": lid}]})
    return r.store.read("lists")


def refs_to(r, lid):
    out = {c: [x for x in r.store.read(c) if x.get("lead_id") == lid] for c in COLLS}
    out["log"] = [x for x in r.store.read("settings").get("linkedin_agent_log") or [] if x.get("lead_id") == lid]
    return out


# ---------- dry run ----------
def test_dry_run_writes_nothing_and_reports_numbers(r):
    dupes(r); seed_refs(r, "D1")
    r.store.update("lists", lambda rows: [*rows, {"id": "L9", "name": "x", "lead_ids": ["D1", "K1"], "workspace": "main", "log": []}])
    before = r.store.snapshot()
    out = merge(r, "K1", ["D1"], dry=True)
    assert out.status_code == 200
    body = out.json()
    assert body["dry_run"] is True and r.store.snapshot() == before
    assert body["records"]["tasks"] == 1 and body["records"]["emails"] == 1 and body["records"]["lists"] == 1 and body["records"]["LinkedIn log rows"] == 1
    assert "title" in body["filled"] and "city" in body["filled"] and body["tags_added"] == ["b"] and "9822222222" in str(body["phones_added"])
    assert body["records_total"] == sum(body["records"].values()) and "summary" in body
    assert r.c.post(URL, json={"keep_id": "K1", "merge_ids": ["D1"]}, headers=r.h["owner"]).json()["dry_run"] is True      # default is a dry run


# ---------- the merge rules ----------
def test_fill_only_empties_union_tags_phones_owner_and_earliest_created(r):
    keep, other = dupes(r)
    out = merge(r, "K1", ["D1"]).json()
    m = get(r, "K1")
    assert out["merged"] == 1 and get(r, "D1") is None
    assert m["title"] == "Head" and m["city"] == "Pune"                      # empty in keep, filled
    assert m["contact_name"] == "Asha" and m["email"] == "asha@dup.in" and m["phone"] == "9811111111"    # never overwritten
    assert m["company"] == "Dup Co"
    assert m["tags"] == ["a", "b"] and set(m["phones"]) >= {"9811111111", "9822222222"}
    assert m["owner"] == "Priya" and m["created_at"] == "2026-02-01T00:00:00+00:00" and m["stage"] == "engaged"
    assert "D1" in m["merged_ids"]


def test_timeline_is_a_union_sorted_with_one_merge_event_naming_companies_only(r):
    keep, other = dupes(r)
    r.store.update_lead("D1", lambda l: {**l, "timeline": [*l["timeline"], {"at": "2026-02-05T00:00:00+00:00", "kind": "note", "text": "called"}]})
    merge(r, "K1", ["D1"])
    t = get(r, "K1")["timeline"]
    assert [e["at"] for e in t] == sorted(e["at"] for e in t)
    assert {"Imported K1", "Imported D1", "called"} <= {e["text"] for e in t}
    ev = [e for e in t if e["kind"] == "merge"]
    assert len(ev) == 1 and "Dup Co Pvt" in ev[0]["text"] and "@" not in ev[0]["text"] and "asha" not in ev[0]["text"].lower()


@pytest.mark.parametrize("exit_stage", ["unsubscribed", "disqualified", "not_now"])
def test_exit_stage_is_never_reversed(r, exit_stage):
    dupes(r)
    r.store.update_lead("D1", lambda l: {**l, "stage": exit_stage})
    r.store.update_lead("K1", lambda l: {**l, "stage": "customer"})
    out = merge(r, "K1", ["D1"]).json()
    assert get(r, "K1")["stage"] == exit_stage and out["stage"] == {"from": "customer", "to": exit_stage}
    # and the other way round: keep is the exit one
    mk(r, "D2", "Dup Three", contact_name="Asha", email="asha3@dup.in", stage="engaged")
    merge(r, "K1", ["D2"])
    assert get(r, "K1")["stage"] == exit_stage


def test_compliance_flags_survive_and_win(r):
    dupes(r)
    r.store.update_lead("D1", lambda l: {**l, "do_not_contact": True, "consent_withdrawn_at": "2026-02-10T00:00:00+00:00"})
    r.store.update_lead("K1", lambda l: {**l, "consent": {"channel": "email", "at": "2026-01-01"}})
    body = merge(r, "K1", ["D1"]).json()
    m = get(r, "K1")
    assert m["do_not_contact"] is True and m["consent"] is None and m["consent_withdrawn_at"] == "2026-02-10T00:00:00+00:00"
    assert any("do-not-contact" in c for c in body["compliance"])


def test_unsubscribed_carries_do_not_contact_but_a_bounced_address_does_not(r):
    dupes(r)
    r.store.update_lead("D1", lambda l: {**l, "stage": "unsubscribed"})
    merge(r, "K1", ["D1"])
    assert get(r, "K1")["stage"] == "unsubscribed" and get(r, "K1")["do_not_contact"] is True
    mk(r, "A1", "Sup Co", email="a@sup.in"); mk(r, "A2", "Sup Co", email="b@sup.in")
    r.store.update("suppressed", lambda rows: [*rows, "b@sup.in"])
    out = merge(r, "A1", ["A2"]).json()
    assert not get(r, "A1").get("do_not_contact") and not any("suppression" in c for c in out["compliance"])     # one bounce must not bury a good contact
    assert "b@sup.in" in r.store.read("suppressed")                                                               # the address itself stays blocked at send time


def test_every_collection_is_repointed_in_one_go(r):
    dupes(r)
    mk(r, "D2", "Dup Four", contact_name="Asha", email="asha4@dup.in")
    for lid in ("D1", "D2"):
        seed_refs(r, lid)
    r.store.update("lists", lambda rows: [*rows, {"id": "L9", "name": "x", "lead_ids": ["D1", "K1", "D2"], "workspace": "main", "log": []}])
    out = merge(r, "K1", ["D1", "D2"]).json()
    assert out["merged"] == 2
    for gone in ("D1", "D2"):
        assert all(not rows for rows in refs_to(r, gone).values())
        assert get(r, gone) is None
    mine = refs_to(r, "K1")
    assert all(len(rows) == 2 for rows in mine.values()), {k: len(v) for k, v in mine.items()}
    assert next(l for l in r.store.read("lists") if l["id"] == "L9")["lead_ids"] == ["K1"]
    assert next(a for a in r.store.read("audit") if a["action"] == "lead.merge")["undo_id"] == out["undo_id"]


def test_meetings_and_sequence_are_carried(r):
    dupes(r)
    r.store.update_lead("D1", lambda l: {**l, "meetings": [{"id": "m1", "title": "Demo"}], "linkedin_seq": {"step": 2}})
    merge(r, "K1", ["D1"])
    m = get(r, "K1")
    assert m["meetings"] == [{"id": "m1", "title": "Demo"}] and m["linkedin_seq"] == {"step": 2}


# ---------- who may merge ----------
def test_scoping_and_roles(r):
    dupes(r)
    assert merge(r, "K1", ["D1"], who="priya", dry=True).status_code == 403
    assert merge(r, "K1", ["D1"], who="view").status_code == 403
    assert merge(r, "K1", ["D1"], who="adm2").status_code == 404                    # admin of another workspace: not even visible
    assert get(r, "D1") is not None
    assert merge(r, "K1", ["D1"], who="adm", dry=True).status_code == 200
    # cross workspace refused
    out = merge(r, "K1", [r.other["id"]], who="owner")
    assert out.status_code == 400 and "different workspaces" in out.json()["detail"]
    assert get(r, r.other["id"]) is not None
    assert merge(r, "K1", [r.other["id"]], who="adm").status_code == 404           # adm cannot see the other workspace at all


def test_foreign_account_refused(r):
    dupes(r)
    acct = r.c.post("/api/workspaces", json={"name": "Acct Two"}, headers=r.h["owner"]).json()["id"]
    r.store.update("settings", lambda s: {**s, "workspaces": [{**w, "account_id": "acme2"} if w["id"] == acct else w for w in s["workspaces"]]})
    mk(r, "F1", "Foreign Co", ws=acct)
    out = merge(r, "K1", ["F1"])
    assert out.status_code == 400 and get(r, "F1") is not None


def test_caps_and_bad_input(r):
    dupes(r)
    ids = [mk(r, f"X{i}", f"Extra {i}", email=f"x{i}@e.in")["id"] for i in range(6)]
    out = merge(r, "K1", ids)
    assert out.status_code == 400 and "At most 5" in out.json()["detail"] and all(get(r, i) for i in ids)
    assert merge(r, "K1", ids[:5], dry=True).status_code == 200
    assert merge(r, "K1", []).status_code == 400
    assert merge(r, "K1", ["K1"]).status_code == 400
    assert merge(r, "K1", ["nope"]).status_code == 404


def test_merging_an_already_merged_lead_is_refused_in_words(r):
    dupes(r)
    merge(r, "K1", ["D1"])
    out = merge(r, "K1", ["D1"])
    assert out.status_code == 409 and "already merged into Dup Co" in out.json()["detail"]


def test_erased_person_is_not_resurrected(r):
    dupes(r)
    from app import privacy
    erased = privacy.hashes_for("asha2@dup.in", ["9822222222"])
    r.store.update("suppressed_hashes", lambda rows: [*rows, *erased])
    merge(r, "K1", ["D1"])
    m = get(r, "K1")
    assert "asha2@dup.in" not in str(m.get("emails") or []) and "9822222222" not in (m.get("phones") or [])
    # undo must not bring the erased lead back
    undo_id = next(a for a in r.store.read("audit") if a["action"] == "lead.merge")["undo_id"]
    out = r.c.post(f"/api/lead-duplicates/merge-undo/{undo_id}", headers=r.h["owner"])
    assert out.status_code == 200 and out.json()["restored"] == 0 and get(r, "D1") is None


# ---------- undo ----------
def test_undo_restores_exactly(r):
    keep, other = dupes(r)
    seed_refs(r, "D1")
    r.store.update("lists", lambda rows: [*rows, {"id": "L9", "name": "x", "lead_ids": ["D1", "K1"], "workspace": "main", "log": []}])
    before = {"leads": {i: get(r, i) for i in ("K1", "D1")}, "refs": refs_to(r, "D1"), "kref": refs_to(r, "K1"),
              "log": copy.deepcopy(r.store.read("settings").get("linkedin_agent_log")), "lists": r.store.read("lists")}
    undo_id = merge(r, "K1", ["D1"]).json()["undo_id"]
    assert get(r, "D1") is None
    out = r.c.post(f"/api/lead-duplicates/merge-undo/{undo_id}", headers=r.h["owner"])
    assert out.status_code == 200 and out.json()["restored"] == 1
    assert get(r, "K1") == before["leads"]["K1"] and get(r, "D1") == before["leads"]["D1"]
    assert refs_to(r, "D1") == before["refs"] and refs_to(r, "K1") == before["kref"]
    assert r.store.read("settings").get("linkedin_agent_log") == before["log"] and r.store.read("lists") == before["lists"]
    assert any(a["action"] == "lead.merge_undo" for a in r.store.read("audit"))
    again = r.c.post(f"/api/lead-duplicates/merge-undo/{undo_id}", headers=r.h["owner"])
    assert again.status_code == 409 and "already undone" in again.json()["detail"]


def test_undo_keeps_edits_made_since_on_the_kept_lead(r):
    dupes(r)
    undo_id = merge(r, "K1", ["D1"]).json()["undo_id"]
    r.store.update_lead("K1", lambda l: {**l, "title": "Director", "timeline": [*l["timeline"], {"at": "2026-12-01T00:00:00+00:00", "kind": "note", "text": "later"}]})
    out = r.c.post(f"/api/lead-duplicates/merge-undo/{undo_id}", headers=r.h["owner"]).json()
    m = get(r, "K1")
    assert m["title"] == "Director" and "title" in out["kept_lead_fields_left_alone"]
    assert any(e["text"] == "later" for e in m["timeline"]) and not any(e["kind"] == "merge" for e in m["timeline"])
    assert m["tags"] == ["a"] and get(r, "D1")["title"] == "Head"


def test_undo_refused_when_kept_lead_was_merged_away(r):
    dupes(r)
    mk(r, "K2", "Dup Top", contact_name="Asha", email="asha9@dup.in")
    undo_id = merge(r, "K1", ["D1"]).json()["undo_id"]
    merge(r, "K2", ["K1"])
    out = r.c.post(f"/api/lead-duplicates/merge-undo/{undo_id}", headers=r.h["owner"])
    assert out.status_code == 409 and get(r, "D1") is None


def test_undo_expired_wrong_user_and_missing(r):
    dupes(r)
    undo_id = merge(r, "K1", ["D1"]).json()["undo_id"]
    assert r.c.post(f"/api/lead-duplicates/merge-undo/{undo_id}", headers=r.h["priya"]).status_code == 403
    assert r.c.post(f"/api/lead-duplicates/merge-undo/{undo_id}", headers=r.h["adm2"]).status_code == 403
    assert r.c.post("/api/lead-duplicates/merge-undo/nope", headers=r.h["owner"]).status_code == 404
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec="seconds")

    def age(s):
        rows = {w: [{**x, "expires_at": old} for x in v] for w, v in s["merge_undo"].items()}
        return {**s, "merge_undo": rows}
    r.store.update("settings", age)
    out = r.c.post(f"/api/lead-duplicates/merge-undo/{undo_id}", headers=r.h["owner"])
    assert out.status_code == 410 and "24 hours" in out.json()["detail"] and get(r, "D1") is None


def test_maker_admin_can_undo_and_other_admin_of_workspace_too(r):
    dupes(r)
    undo_id = merge(r, "K1", ["D1"], who="adm").json()["undo_id"]
    assert r.c.post(f"/api/lead-duplicates/merge-undo/{undo_id}", headers=r.h["adm"]).status_code == 200
    assert get(r, "D1") is not None


def test_only_last_twenty_records_kept(r):
    dupes(r)
    for i in range(22):
        mk(r, f"P{i}", f"Pair {i}", email=f"p{i}@p.in"); mk(r, f"Q{i}", f"Pair {i}", email=f"q{i}@p.in")
        assert merge(r, f"P{i}", [f"Q{i}"]).status_code == 200
    assert len(r.store.read("settings")["merge_undo"]["main"]) == merge_flow.KEEP
