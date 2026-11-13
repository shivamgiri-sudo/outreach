"""Lead ageing: thresholds, exclusions, saved-view key, the re-engage list (scope, cap), settings roles."""
from datetime import datetime, timedelta, timezone

import pytest

from app import contact_status, lead_ageing, lead_ops
from tests.ownership_helpers import rig as make_rig

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
CFG = {"untouched_days": 7, "stale_days": 14, "stuck_days": 21}


def iso(days): return (NOW - timedelta(days=days)).isoformat()


def mk(**kw):
    return {"id": "L1", "stage": "new", "created_at": iso(1), "timeline": [], **kw}


def age(lead, **contact):
    c = {"status": "never", "attempts": 0, "last_at": None, **contact}
    return lead_ageing.of(lead, c, CFG, NOW)


def test_untouched_threshold():
    assert age(mk(created_at=iso(6))) == ""
    assert age(mk(created_at=iso(7))) == "untouched"


def test_stale_needs_no_reply_and_threshold():
    l = mk(created_at=iso(30), timeline=[{"kind": "stage", "at": iso(2), "text": "x"}])
    assert age(l, status="tried", attempts=1, last_at=iso(13)) == ""
    assert age(l, status="tried", attempts=1, last_at=iso(14)) == "stale"
    assert age(l, status="replied", attempts=1, last_at=iso(40)) == ""


def test_stuck_uses_latest_stage_event_else_created():
    l = mk(created_at=iso(40), timeline=[{"kind": "stage", "at": iso(30)}, {"kind": "stage", "at": iso(5)}])
    assert age(l, status="replied", last_at=iso(1)) == ""
    l2 = mk(created_at=iso(40), timeline=[{"kind": "stage", "at": iso(22)}])
    assert age(l2, status="replied", last_at=iso(1)) == "stuck"
    assert age(mk(created_at=iso(21)), status="replied", last_at=iso(1)) == "stuck"
    assert lead_ageing.stage_since(l) == iso(5)


@pytest.mark.parametrize("extra", [{"stage": "customer"}, {"stage": "disqualified"}, {"stage": "not_now"}, {"do_not_contact": True},
                                   {"lead_quality": {"kind": "invalid"}}, {"lead_quality": {"kind": "consultant"}}])
def test_exclusions(extra):
    assert age(mk(created_at=iso(90), **extra)) == ""


def test_marked_status_excluded_even_without_quality_field():
    assert age(mk(created_at=iso(90)), status="invalid") == ""


def test_attempts_only_never_marks():
    """A marker activity (invalid lead call) is not an attempt, so a lead with only a mark still reads untouched."""
    from app.store import Store  # noqa: F401
    l = mk(created_at=iso(30), timeline=[{"kind": "call", "at": iso(1), "text": "Call — invalid lead"}])
    c = contact_status.of(l, {})
    assert c["status"] == "never" and lead_ageing.of(l, c, CFG, NOW) == "untouched"


def test_config_clean_and_defaults(tmp_path):
    r = make_rig(tmp_path)
    assert lead_ageing.config(r.store) == CFG
    with pytest.raises(lead_ageing.AgeingRefused, match="whole number"):
        lead_ageing.clean_config({"stale_days": "abc"})
    with pytest.raises(lead_ageing.AgeingRefused, match="between 1 and"):
        lead_ageing.clean_config({"stuck_days": 0})
    assert lead_ageing.clean_config({"stale_days": "10"}) == {"stale_days": 10}


def test_roles_get_put(tmp_path):
    r = make_rig(tmp_path)
    assert r.c.get("/api/lead-ageing", headers=r.h["priya"]).json()["stale_days"] == 14
    assert r.c.put("/api/lead-ageing", json={"stale_days": 5}, headers=r.h["priya"]).status_code == 403
    assert r.c.put("/api/lead-ageing", json={"stale_days": 5}, headers=r.h["adm"]).status_code == 403
    res = r.c.put("/api/lead-ageing", json={"stale_days": 5}, headers=r.h["owner"])
    assert res.status_code == 200 and res.json()["stale_days"] == 5 and res.json()["untouched_days"] == 7
    bad = r.c.put("/api/lead-ageing", json={"stale_days": "soon"}, headers=r.h["owner"])
    assert bad.status_code == 400 and "whole number" in bad.json()["detail"]
    assert r.c.put("/api/lead-ageing", json={"stale_days": 99999}, headers=r.h["owner"]).status_code == 400


def test_list_payload_has_ageing_and_saved_view_whitelist(tmp_path):
    r = make_rig(tmp_path, mode_on=False)
    old = iso(10)
    r.store.update("leads", lambda ls: [{**l, "created_at": old} for l in ls])
    rows = r.c.get("/api/leads", headers=r.h["owner"]).json()
    assert {l["ageing"] for l in rows if l["company"] in ("Acme", "Beta Ltd", "Cora Inc")} == {"untouched"}
    assert "ageing" in lead_ops.VIEW_FILTER_KEYS
    assert lead_ops._clean_filters({"ageing": "stale"})["ageing"] == "stale"
    assert lead_ops._clean_filters({"ageing": "evil"})["ageing"] == ""
    saved = r.c.post("/api/lead-views", json={"name": "Cold", "filters": {"ageing": "untouched", "bogus": 1}}, headers=r.h["priya"]).json()
    assert saved["filters"]["ageing"] == "untouched" and "bogus" not in saved["filters"]


def _age_all(r, days=10):
    r.store.update("leads", lambda ls: [{**l, "created_at": iso(days)} for l in ls])


def test_reengage_creates_list_changes_nothing_else(tmp_path):
    r = make_rig(tmp_path, mode_on=False)
    _age_all(r)
    before = {l["id"]: (l["stage"], l.get("owner"), l.get("sequence")) for l in r.store.read("leads")}
    ids = [l["id"] for l in r.c.get("/api/leads", headers=r.h["owner"]).json() if l["ageing"] and l["id"] != r.other["id"]]
    out = r.c.post("/api/lead-ageing/re-engage", json={"lead_ids": ids, "kind": "untouched"}, headers=r.h["owner"])
    assert out.status_code == 200, out.text
    j = out.json()
    assert j["notice"].startswith("Created Re-engage ") and f"{len(ids)} lead" in j["notice"] and j["count"] == len(ids)
    row = next(x for x in r.store.read("lists") if x["id"] == j["list_id"])
    assert set(row["lead_ids"]) == set(ids) and row["assigned_to"] == ""
    assert {l["id"]: (l["stage"], l.get("owner"), l.get("sequence")) for l in r.store.read("leads")} == before
    assert not r.store.read("outbox")


def test_reengage_skips_not_aged_and_refuses_empty(tmp_path):
    r = make_rig(tmp_path, mode_on=False)
    ids = [l["id"] for l in r.c.get("/api/leads", headers=r.h["owner"]).json()]      # all fresh (created now)
    res = r.c.post("/api/lead-ageing/re-engage", json={"lead_ids": ids}, headers=r.h["owner"])
    assert res.status_code == 400 and "no list was made" in res.json()["detail"]
    assert r.c.post("/api/lead-ageing/re-engage", json={"lead_ids": []}, headers=r.h["owner"]).status_code == 400
    assert r.c.post("/api/lead-ageing/re-engage", json={"lead_ids": ids, "kind": "bad"}, headers=r.h["owner"]).status_code == 400
    assert r.store.read("lists") == []


def test_reengage_cap(tmp_path, monkeypatch):
    r = make_rig(tmp_path, mode_on=False)
    _age_all(r)
    monkeypatch.setattr(lead_ageing, "REENGAGE_MAX", 2)
    ids = [l["id"] for l in r.c.get("/api/leads", headers=r.h["owner"]).json() if l["ageing"]]
    assert len(ids) >= 3
    j = r.c.post("/api/lead-ageing/re-engage", json={"lead_ids": ids}, headers=r.h["owner"]).json()
    assert j["count"] == 2 and j["capped"] and "at most 2" in j["notice"]


def test_reengage_respects_ownership_and_workspace(tmp_path):
    r = make_rig(tmp_path)                     # ownership on: Priya owns only Beta Ltd
    _age_all(r)
    r.store.update_lead(r.other["id"], lambda l: {**l, "created_at": iso(10)})
    everything = [r.lead[c]["id"] for c in ("Acme", "Beta Ltd", "Cora Inc")] + [r.other["id"]]
    j = r.c.post("/api/lead-ageing/re-engage", json={"lead_ids": everything}, headers=r.h["priya"]).json()
    row = next(x for x in r.store.read("lists") if x["id"] == j["list_id"])
    assert row["lead_ids"] == [r.lead["Beta Ltd"]["id"]]                 # not Ravi's, not another workspace's
    assert row["assigned_to"] == "Priya"                                  # a member's own list stays visible to them
    j2 = r.c.post("/api/lead-ageing/re-engage", json={"lead_ids": everything}, headers=r.h["adm"]).json()
    row2 = next(x for x in r.store.read("lists") if x["id"] == j2["list_id"])
    assert set(row2["lead_ids"]) == {r.lead[c]["id"] for c in ("Acme", "Beta Ltd", "Cora Inc")} and r.other["id"] not in row2["lead_ids"]
    assert row2["assigned_to"] == ""
    assert r.c.post("/api/lead-ageing/re-engage", json={"lead_ids": [r.lead["Acme"]["id"]]}, headers=r.h["priya"]).status_code == 400
    assert r.c.post("/api/lead-ageing/re-engage", json={"lead_ids": everything}, headers=r.h["view"]).status_code == 403
