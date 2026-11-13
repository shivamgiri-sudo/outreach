"""Bulk actions with undo: hand over a person's leads (dry run, roles, scoping), tags / stage / list moves, caps, undo."""
import copy
from datetime import datetime, timedelta, timezone

import pytest

from app import bulk_undo, lead_ops
from tests.ownership_helpers import lead, new_list, rig as make_rig


@pytest.fixture
def r(tmp_path): return make_rig(tmp_path)


def lid(r, company): return r.lead[company]["id"]


def add_leads(r, n, owner="Ravi", ws="main", stage="new"):
    base = next(l for l in r.store.read("leads") if l["company"] == "Acme")
    rows = [{**copy.deepcopy(base), "id": f"x{ws}{owner}{stage}{i}", "company": f"Gen {owner} {i}", "owner": owner, "workspace": ws, "stage": stage,
             "dedupe_key": f"gen-{ws}-{owner}-{stage}-{i}", "email": f"g{ws}{owner}{stage}{i}@gen.io", "timeline": [], "tags": []} for i in range(n)]
    r.store.update("leads", lambda old: [*old, *rows])
    return [x["id"] for x in rows]


def owner_of(r, lead_id): return next(l for l in r.store.read("leads") if l["id"] == lead_id)["owner"]


def handover(r, who="adm", dry=False, **kw):
    body = {"from_owner": "Ravi", "to_owner": "Priya", "dry_run": dry, **kw}
    return r.c.post("/api/leads/bulk-reassign", json=body, headers=r.h[who])


def bells(r, to): return [n for n in r.store.read("notifications") if n.get("to") == to and n.get("kind") == "reassigned"]


# ---------- hand over everything one person owns ----------
def test_handover_moves_all_and_writes_audit_undo_bells(r):
    ids = add_leads(r, 8)
    out = handover(r).json()
    assert out["moved"] == 9 and out["dry_run"] is False and out["undo_id"]       # Acme + 8
    assert owner_of(r, lid(r, "Acme")) == "Priya" and all(owner_of(r, i) == "Priya" for i in ids)
    assert owner_of(r, lid(r, "Beta Ltd")) == "Priya" and owner_of(r, r.other["id"]) == "Priya"
    row = [a for a in r.store.read("audit") if a["action"] == "bulk.reassign"][-1]
    assert row["by"] == "Adm" and row["moved"] == 9 and row["undo_id"] == out["undo_id"] and row["at"] and "Ravi" in row["target"] and "Priya" in row["target"]
    texts = [n["text"] for n in bells(r, "Priya")]
    assert any("9 leads moved from Ravi to Priya" in t for t in texts)               # many leads: one summary per person
    assert bells(r, "Ravi") and bells(r, "Adm") == [] or bells(r, "Adm")             # old owner told (manager is the admin themself or another admin)
    assert any(e["kind"] == "owner" and "hand-over" in e["text"] for e in lead(r, "Acme")["timeline"])


def test_few_leads_use_the_per_lead_bell(r):
    assert handover(r).json()["moved"] == 1
    assert any("Acme" in n["text"] for n in bells(r, "Priya")) and any("Acme" in n["text"] for n in bells(r, "Ravi"))


def test_dry_run_writes_nothing(r):
    add_leads(r, 4)
    before = (r.store.read("leads"), r.store.read("notifications"), r.store.read("audit"), r.store.read("settings").get("bulk_undo"))
    out = handover(r, dry=True).json()
    assert out["dry_run"] is True and out["will_move"] == 5 and out["matched"] == 5 and len(out["sample"]) == 5 and out["by_stage"] == {"new": 5}
    assert (r.store.read("leads"), r.store.read("notifications"), r.store.read("audit"), r.store.read("settings").get("bulk_undo")) == before


def test_dry_run_is_the_default(r):
    out = r.c.post("/api/leads/bulk-reassign", json={"from_owner": "Ravi", "to_owner": "Priya"}, headers=r.h["adm"]).json()
    assert out["dry_run"] is True and owner_of(r, lid(r, "Acme")) == "Ravi"


def test_stage_and_untouched_filters(r):
    a = add_leads(r, 2, stage="contacted")
    b = add_leads(r, 2, stage="new")
    r.store.update_lead(b[0], lambda l: {**l, "timeline": [{"kind": "note", "text": "called", "at": "2026-01-01T00:00:00+00:00", "by": "Ravi"}]})
    assert handover(r, dry=True, stage="contacted").json()["will_move"] == 2
    assert handover(r, dry=True, only_untouched=True).json()["will_move"] == 4      # Acme + b[1] + the two contacted; b[0] was touched
    handover(r, stage="contacted")
    assert all(owner_of(r, i) == "Priya" for i in a) and all(owner_of(r, i) == "Ravi" for i in b)


def test_first_touch_clock_is_left_as_manual_reassign_leaves_it(r):
    acme = lid(r, "Acme")
    stamp = {"first_touch_started": "2026-01-01T00:00:00+00:00", "first_touch_due_at": "2026-01-01T01:00:00+00:00", "first_touch_at": None}
    r.store.update_lead(acme, lambda l: {**l, **stamp})
    handover(r)
    after = lead(r, "Acme")
    assert after["owner"] == "Priya" and {k: after[k] for k in stamp} == stamp


def test_roles_refused(r):
    for who in ("priya", "ravi", "view"):
        assert handover(r, who=who).status_code == 403
    assert r.c.post("/api/leads/bulk-reassign", json={"from_owner": "Ravi", "to_owner": "Priya"}).status_code in (401, 403)
    assert owner_of(r, lid(r, "Acme")) == "Ravi"
    assert handover(r, who="owner").status_code == 200


def test_member_cannot_touch_others_leads_via_bulk_or_undo(r):
    out = r.c.post("/api/leads/bulk", json={"lead_ids": [lid(r, "Acme")], "action": "owner", "value": "Priya"}, headers=r.h["priya"]).json()
    assert out["ok"] == 0 and owner_of(r, lid(r, "Acme")) == "Ravi"
    mine = r.c.post("/api/leads/bulk", json={"lead_ids": [lid(r, "Beta Ltd")], "action": "tag_add", "value": "hot"}, headers=r.h["priya"]).json()
    assert mine["ok"] == 1


def test_workspace_limited_admin_cannot_cross_workspace(r):
    add_leads(r, 2, owner="Priya", ws=r.w2)
    out = r.c.post("/api/leads/bulk-reassign", json={"from_owner": "Priya", "to_owner": "Adm2", "dry_run": False}, headers=r.h["adm2"]).json()
    assert out["moved"] == 3                                                       # Other WS Co + 2 generated; nothing in main
    assert owner_of(r, lid(r, "Beta Ltd")) == "Priya"                              # Main lead untouched
    # the Main admin cannot take over Client Two's lead
    again = r.c.post("/api/leads/bulk-reassign", json={"from_owner": "Adm2", "to_owner": "Adm", "dry_run": False}, headers=r.h["adm"]).json()
    assert again["moved"] == 0
    assert owner_of(r, r.other["id"]) == "Adm2"
    # and the Main admin cannot hand a Main lead to someone who has no access to Main
    refused = r.c.post("/api/leads/bulk-reassign", json={"from_owner": "Priya", "to_owner": "Adm2", "dry_run": False}, headers=r.h["adm"])
    assert refused.status_code == 400 or refused.json()["moved"] == 0
    assert owner_of(r, lid(r, "Beta Ltd")) == "Priya"


def test_new_owner_must_be_allowed_in_the_workspace_and_able_to_work(r):
    assert handover(r, to_owner="Nobody Here").status_code == 400
    bad = r.c.post("/api/leads/bulk-reassign", json={"from_owner": "Ravi", "to_owner": "View", "dry_run": False}, headers=r.h["adm"]).json()
    assert bad["moved"] == 0 and any("view-only" in x["reason"] for x in bad["reasons"])
    assert owner_of(r, lid(r, "Acme")) == "Ravi"
    assert handover(r, to_owner="Ravi").status_code == 400                          # same person
    assert handover(r, from_owner="").status_code in (400, 422)
    assert handover(r, stage="nonsense").status_code == 400


def test_cap_refuses_in_plain_words(r, monkeypatch):
    add_leads(r, 6)
    monkeypatch.setattr(lead_ops, "BULK_MAX", 5)
    res = handover(r)
    assert res.status_code == 400 and "at most 5" in res.json()["detail"] and "7 leads" in res.json()["detail"]
    assert owner_of(r, lid(r, "Acme")) == "Ravi"
    assert handover(r, dry=True).status_code == 400
    too_many = r.c.post("/api/leads/bulk", json={"lead_ids": [str(i) for i in range(5001)], "action": "tag_add", "value": "x"}, headers=r.h["adm"])
    assert too_many.status_code in (400, 422)


# ---------- tags, stage, lists ----------
def test_bulk_tags_stage_and_list_with_reasons_and_undo(r):
    acme, beta = lid(r, "Acme"), lid(r, "Beta Ltd")
    t = r.c.post("/api/leads/bulk", json={"lead_ids": [acme, beta], "action": "tag_add", "value": "Nov"}, headers=r.h["adm"]).json()
    assert t["ok"] == 2 and t["undo_id"] and lead(r, "Acme")["tags"] == ["Nov"]
    again = r.c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "tag_add", "value": "nov"}, headers=r.h["adm"]).json()
    assert again["ok"] == 0 and again["reasons"][0]["reason"] == "already tagged" and again["undo_id"] is None
    rm = r.c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "tag_remove", "value": "Nov"}, headers=r.h["adm"]).json()
    assert rm["ok"] == 1 and lead(r, "Acme")["tags"] == []
    mv = r.c.post("/api/leads/bulk", json={"lead_ids": [acme, beta], "action": "stage", "value": "contacted"}, headers=r.h["adm"]).json()
    assert mv["ok"] == 0 and any("cannot move from new to contacted" in x["reason"] for x in mv["reasons"])   # new → contacted skips verified/enriched
    ok = r.c.post("/api/leads/bulk", json={"lead_ids": [acme, beta], "action": "stage", "value": "verified"}, headers=r.h["adm"]).json()
    assert ok["ok"] == 2 and lead(r, "Acme")["stage"] == "verified"
    row = new_list(r, "Nov batch", ["Cora Inc"])
    la = r.c.post("/api/leads/bulk", json={"lead_ids": [acme, r.other["id"]], "action": "list_add", "value": row["id"]}, headers=r.h["adm"]).json()
    assert la["ok"] == 1 and la["reasons"][0]["reason"] == "not found" and la["undo_id"]
    assert acme in [x for x in r.store.read("lists")[0]["lead_ids"]]
    dup = r.c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "list_add", "value": row["id"]}, headers=r.h["adm"]).json()
    assert dup["ok"] == 0 and dup["reasons"][0]["reason"] == "already in this list"
    undo = r.c.post(f"/api/leads/bulk-undo/{la['undo_id']}", headers=r.h["adm"]).json()
    assert undo["restored"] == 1 and acme not in r.store.read("lists")[0]["lead_ids"]
    assert r.c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "list_add", "value": "nope"}, headers=r.h["adm"]).status_code == 400
    audits = {a["action"] for a in r.store.read("audit")}
    assert {"bulk.tag_add", "bulk.stage", "bulk.list_add", "bulk.undo"} <= audits


def test_stage_never_unsuppresses_and_unsubscribed_is_not_undoable(r):
    acme, beta = lid(r, "Acme"), lid(r, "Beta Ltd")
    r.store.update_lead(acme, lambda l: {**l, "stage": "unsubscribed"})
    out = r.c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "stage", "value": "new"}, headers=r.h["adm"]).json()
    assert out["ok"] == 0 and lead(r, "Acme")["stage"] == "unsubscribed"
    unsub = r.c.post("/api/leads/bulk", json={"lead_ids": [beta], "action": "stage", "value": "unsubscribed"}, headers=r.h["adm"]).json()
    assert unsub["ok"] == 1 and unsub["undo_id"] is None                          # nothing recorded: it cannot be reversed here
    forced = r.c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "stage", "value": "contacted", "force": True}, headers=r.h["owner"]).json()
    assert forced["ok"] == 0 and lead(r, "Acme")["stage"] == "unsubscribed"


# ---------- undo ----------
def test_undo_restores_and_skips_changed_since(r):
    ids = add_leads(r, 3)
    out = handover(r).json()
    r.store.update_lead(ids[0], lambda l: {**l, "owner": "Adm"})                  # someone moved it since
    res = r.c.post(f"/api/leads/bulk-undo/{out['undo_id']}", headers=r.h["adm"]).json()
    assert res["restored"] == 3 and res["skipped"] == 1 and "owner changed since" in res["reasons"][0]["reason"]
    assert owner_of(r, ids[0]) == "Adm" and owner_of(r, ids[1]) == "Ravi" and owner_of(r, lid(r, "Acme")) == "Ravi"
    assert any("undo" in e["text"] for e in lead(r, "Acme")["timeline"])
    assert r.c.post(f"/api/leads/bulk-undo/{out['undo_id']}", headers=r.h["adm"]).status_code == 409      # only once


def test_undo_tags_and_stage_skip_changed(r):
    acme, beta = lid(r, "Acme"), lid(r, "Beta Ltd")
    t = r.c.post("/api/leads/bulk", json={"lead_ids": [acme, beta], "action": "tag_add", "value": "Nov"}, headers=r.h["adm"]).json()
    r.c.post(f"/api/leads/{beta}/tags", json={"add": ["extra"]}, headers=r.h["adm"])
    u = r.c.post(f"/api/leads/bulk-undo/{t['undo_id']}", headers=r.h["adm"]).json()
    assert u["restored"] == 1 and u["skipped"] == 1 and lead(r, "Acme")["tags"] == [] and set(lead(r, "Beta Ltd")["tags"]) == {"Nov", "extra"}
    s = r.c.post("/api/leads/bulk", json={"lead_ids": [acme, beta], "action": "stage", "value": "verified"}, headers=r.h["adm"]).json()
    r.store.update_lead(beta, lambda l: {**l, "stage": "enriched"})
    u = r.c.post(f"/api/leads/bulk-undo/{s['undo_id']}", headers=r.h["adm"]).json()
    assert u["restored"] == 1 and lead(r, "Acme")["stage"] == "new" and lead(r, "Beta Ltd")["stage"] == "enriched"


def test_undo_expiry_and_who_may_undo(r):
    acme = lid(r, "Acme")
    mine = r.c.post("/api/leads/bulk", json={"lead_ids": [lid(r, "Beta Ltd")], "action": "tag_add", "value": "mine"}, headers=r.h["priya"]).json()["undo_id"]
    assert r.c.post(f"/api/leads/bulk-undo/{mine}", headers=r.h["ravi"]).status_code == 403          # another member
    assert r.c.post(f"/api/leads/bulk-undo/{mine}", headers=r.h["view"]).status_code == 403
    assert r.c.get("/api/bulk-undo", headers=r.h["ravi"]).json() == []
    assert [x["id"] for x in r.c.get("/api/bulk-undo", headers=r.h["priya"]).json()] == [mine]
    assert r.c.post("/api/leads/bulk-undo/doesnotexist", headers=r.h["adm"]).status_code == 404
    assert r.c.post(f"/api/leads/bulk-undo/{mine}", headers=r.h["priya"]).status_code == 200         # the maker
    out = handover(r).json()["undo_id"]
    assert r.c.post(f"/api/leads/bulk-undo/{out}", headers=r.h["ravi"]).status_code == 403
    assert r.c.post(f"/api/leads/bulk-undo/{out}", headers=r.h["adm2"]).status_code == 403           # admin of another workspace
    old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(timespec="seconds")
    r.store.update("settings", lambda s: {**s, "bulk_undo": {w: [{**x, "expires_at": old} for x in rows] for w, rows in s["bulk_undo"].items()}})
    gone = r.c.post(f"/api/leads/bulk-undo/{out}", headers=r.h["adm"])
    assert gone.status_code == 410 and "24 hours" in gone.json()["detail"] and owner_of(r, acme) == "Priya"


def test_undo_keeps_last_twenty_per_workspace(r):
    acme = lid(r, "Acme")
    ids = [r.c.post("/api/leads/bulk", json={"lead_ids": [acme], "action": "tag_add", "value": f"t{i}"}, headers=r.h["adm"]).json()["undo_id"] for i in range(23)]
    kept = [x["id"] for x in r.store.read("settings")["bulk_undo"]["main"]]
    assert kept == ids[-20:]
    assert r.c.post(f"/api/leads/bulk-undo/{ids[0]}", headers=r.h["adm"]).status_code == 404


def test_undo_does_not_clobber_sender_engine_changes(r):
    ids = add_leads(r, 2)
    out = handover(r).json()
    # the sender engine saves a send and a reply on one of the leads after the hand-over
    r.store.patch_leads({ids[0]: {"set": {"last_sent_at": "2026-02-02T00:00:00+00:00"}, "events": [{"kind": "email", "text": "sent step 2", "at": "2026-02-02T00:00:00+00:00"}]}})
    r.store.update_lead(ids[1], lambda l: {**l, "reply_count": 3, "sequence": {"step": 2}})
    r.c.post(f"/api/leads/bulk-undo/{out['undo_id']}", headers=r.h["adm"])
    a, b = (next(l for l in r.store.read("leads") if l["id"] == i) for i in ids)
    assert a["owner"] == "Ravi" and a.get("last_sent_at") == "2026-02-02T00:00:00+00:00" and any(e["text"] == "sent step 2" for e in a["timeline"])
    assert b["owner"] == "Ravi" and b["reply_count"] == 3 and b["sequence"] == {"step": 2}


def test_handover_keeps_changes_made_in_between(r):
    ids = add_leads(r, 2)
    r.store.update_lead(ids[0], lambda l: {**l, "reply_count": 7})
    handover(r)
    assert next(l for l in r.store.read("leads") if l["id"] == ids[0])["reply_count"] == 7


def test_bulk_owner_action_records_undo_and_tells(r):
    acme, cora = lid(r, "Acme"), lid(r, "Cora Inc")
    out = r.c.post("/api/leads/bulk", json={"lead_ids": [acme, cora], "action": "owner", "value": "Priya"}, headers=r.h["adm"]).json()
    assert out["ok"] == 2 and out["undo_id"]
    assert any("Acme" in n["text"] for n in bells(r, "Priya")) and bells(r, "Ravi")
    res = r.c.post(f"/api/leads/bulk-undo/{out['undo_id']}", headers=r.h["adm"]).json()
    assert res["restored"] == 2 and owner_of(r, acme) == "Ravi" and owner_of(r, cora) == ""
