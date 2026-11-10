"""Speed-to-lead clock: alert, reassign, working hours, switches, isolation (assignment module faked)."""
import sys
import types
from datetime import datetime, timezone

import pytest

from app import first_touch as ft
from app.sequences import IST
from app.store import Store


def ist(day, hour, minute=0):
    """Nov 2026; 2 Nov is a Monday, 7 Nov a Saturday, 8 Nov a Sunday."""
    return datetime(2026, 11, day, hour, minute, tzinfo=IST).astimezone(timezone.utc)


CFG = {"on": True, "first_touch": {"alert_minutes": 30, "reassign_minutes": 120, "alert": True, "reassign": True}}


@pytest.fixture
def fake(monkeypatch):
    state = {"cfg": {"main": dict(CFG), "other": dict(CFG)}, "teams": {"Asha": "t1", "Bo": "t1", "Cy": "t1", "Zed": "t2"},
             "order": ["Asha", "Bo", "Cy"], "manager": "Mgr"}
    mod = types.ModuleType("app.assignment")

    def config(store, ws):
        return state["cfg"].get(ws, {"on": False})

    def next_member(store, ws, team_id, exclude=()):
        return next((m for m in state["order"] if m not in exclude), "")
    mod.config = config
    mod.next_member = next_member
    mod.team_of_owner = lambda store, ws, owner: state["teams"].get(owner, "")
    mod.manager_of = lambda store, ws, owner: state["manager"]
    import app as app_pkg
    monkeypatch.setitem(sys.modules, "app.assignment", mod)
    monkeypatch.setattr(app_pkg, "assignment", mod, raising=False)         # `from . import assignment` reads the package attribute
    return state


def make(tmp_path, **lead):
    store = Store(tmp_path / "d.json")
    base = {"id": "l1", "company": "Acme", "stage": "new", "timeline": [], "owner": "Asha", "workspace": "main"}
    store.write("leads", [{**base, **lead}])
    store.write("notifications", [])
    return store


def lead(store, lid="l1"):
    return next(l for l in store.read("leads") if l["id"] == lid)


def bells(store, kind="first_touch"):
    return [n for n in store.read("notifications") if n["kind"] == kind]


def test_working_minutes_skip_nights_and_sundays():
    assert ft.working_minutes(ist(2, 10), ist(2, 10, 45)) == 45
    assert ft.working_minutes(ist(2, 18, 30), ist(3, 10, 30)) == 60          # 30 left on Mon + 30 on Tue
    assert ft.working_minutes(ist(2, 8), ist(2, 10)) == 0                     # before opening
    assert ft.working_minutes(ist(7, 18), ist(9, 11)) == 60 + 60              # Sat 18-19, Sun off, Mon 10-11
    assert ft.working_minutes(ist(8, 12), ist(8, 15)) == 0


def test_working_hours_override():
    hours = {"days": [0, 1, 2, 3, 4, 5, 6], "start": "09:00", "end": "10:00"}
    assert ft.working_minutes(ist(8, 8), ist(8, 12), hours) == 60


def test_start_sets_clock_and_is_idempotent(tmp_path, fake):
    store = make(tmp_path)
    ft.start(store, "l1", now=ist(2, 10))
    l = lead(store)
    assert l["first_touch_at"] is None and l["reassigns"] == 0 and l["first_touch_started"]
    assert l["first_touch_due_at"]
    store.update_lead("l1", lambda x: {**x, "reassigns": 1})
    ft.start(store, "l1", now=ist(2, 11))
    assert lead(store)["reassigns"] == 1 and lead(store)["first_touch_started"] == l["first_touch_started"]


def test_alert_once(tmp_path, fake):
    store = make(tmp_path)
    ft.start(store, "l1", now=ist(2, 10))
    ft.tick(store, ist(2, 10, 29))
    assert bells(store) == []
    ft.tick(store, ist(2, 10, 31))
    ft.tick(store, ist(2, 10, 50))
    b = bells(store)
    assert len(b) == 1 and b[0]["to"] == "Asha" and b[0]["lead_id"] == "l1"
    assert lead(store)["owner"] == "Asha"


def test_reassign_once_and_restart_clock(tmp_path, fake):
    store = make(tmp_path)
    ft.start(store, "l1", now=ist(2, 10))
    ft.tick(store, ist(2, 12, 5))
    l = lead(store)
    assert l["owner"] == "Bo" and l["reassigns"] == 1
    assert any("no first touch in time" in e["text"] for e in l["timeline"])
    tos = sorted(n["to"] for n in bells(store))
    assert "Asha" in tos and "Mgr" in tos
    ft.tick(store, ist(2, 12, 10))                                         # same moment: no second move
    assert lead(store)["owner"] == "Bo" and lead(store)["reassigns"] == 1
    ft.tick(store, ist(2, 14, 4))                                          # clock restarted at 12:05: 119 min
    assert lead(store)["owner"] == "Bo"
    ft.tick(store, ist(2, 14, 6))
    assert lead(store)["owner"] == "Cy"


def test_max_two_moves_then_stuck(tmp_path, fake):
    store = make(tmp_path)
    ft.start(store, "l1", now=ist(2, 10))
    ft.tick(store, ist(2, 12, 1))
    ft.tick(store, ist(2, 14, 2))
    l = lead(store)
    assert l["owner"] == "Cy" and l["reassigns"] == 2 and not l.get("first_touch_stuck")
    ft.tick(store, ist(2, 16, 3))
    l = lead(store)
    assert l["owner"] == "Cy" and l["first_touch_stuck"] is True
    n = len(bells(store))
    ft.tick(store, ist(2, 18, 30))
    assert len(bells(store)) == n and lead(store)["reassigns"] == 2


def test_weekend_and_night_do_not_count(tmp_path, fake):
    store = make(tmp_path)
    ft.start(store, "l1", now=ist(7, 18, 30))           # Sat 18:30
    ft.tick(store, ist(9, 9))                           # Mon 09:00: only 30 working minutes -> alert, no move
    assert len(bells(store)) == 1 and lead(store)["owner"] == "Asha"
    ft.tick(store, ist(9, 11, 29))                      # 30 + 89 = 119
    assert lead(store)["owner"] == "Asha"
    ft.tick(store, ist(9, 11, 31))
    assert lead(store)["owner"] == "Bo"


def test_touched_by_owner_only(tmp_path, fake):
    store = make(tmp_path)
    ft.start(store, "l1", now=ist(2, 10))
    assert ft.touched(store, "l1", "email", "Bo") is False
    assert lead(store)["first_touch_at"] is None
    assert ft.touched(store, "l1", "stage", "Asha") is False
    assert ft.touched(store, "l1", "email", "Asha") is True
    ft.tick(store, ist(2, 13))
    assert lead(store)["owner"] == "Asha" and bells(store) == []


def test_store_update_stamps_touch_from_timeline(tmp_path, fake):
    store = make(tmp_path)
    ft.start(store, "l1", now=ist(2, 10))
    add = lambda kind, by: store.update_lead("l1", lambda x: {**x, "timeline": [*x["timeline"], {"at": "2026-11-02T05:00:00+00:00", "kind": kind, "text": "t", "by": by}]})
    add("stage", "Asha")
    add("note", "Bo")
    assert lead(store)["first_touch_at"] is None
    add("call", "Asha")
    assert lead(store)["first_touch_at"]


def test_skips(tmp_path, fake):
    for extra in ({"do_not_contact": True}, {"stage": "unsubscribed"}, {"stage": "disqualified"}, {"owner": ""}):
        store = make(tmp_path, **extra)
        ft.start(store, "l1", now=ist(2, 10))
        ft.tick(store, ist(2, 15))
        assert lead(store)["owner"] == extra.get("owner", "Asha")
        assert bells(store) == [], extra


def test_switches(tmp_path, fake):
    store = make(tmp_path)
    fake["cfg"]["main"]["first_touch"]["alert"] = False
    ft.start(store, "l1", now=ist(2, 10))
    ft.tick(store, ist(2, 10, 40))
    assert bells(store) == []
    fake["cfg"]["main"]["first_touch"]["reassign"] = False
    ft.tick(store, ist(2, 13))
    assert lead(store)["owner"] == "Asha"
    fake["cfg"]["main"]["first_touch"].update(alert=True, reassign=False)
    ft.tick(store, ist(2, 13))
    assert len(bells(store)) == 1 and lead(store)["owner"] == "Asha"
    fake["cfg"]["main"]["first_touch"]["reassign"] = True
    fake["cfg"]["main"]["on"] = False
    ft.tick(store, ist(2, 15))
    assert lead(store)["owner"] == "Asha" and len(bells(store)) == 1


def test_off_workspace_unchanged(tmp_path, fake):
    store = make(tmp_path, workspace="off")
    ft.start(store, "l1", now=ist(2, 10))
    assert "first_touch_started" not in lead(store)
    store.update_lead("l1", lambda x: {**x, "first_touch_started": ist(2, 10).isoformat(), "first_touch_at": None, "reassigns": 0})
    before = lead(store)
    ft.tick(store, ist(2, 16))
    assert lead(store) == before and store.read("notifications") == []


def test_workspace_isolation_and_sequences_untouched(tmp_path, fake):
    store = make(tmp_path, linkedin_seq={"step": 2, "account": "li-1"}, sequence={"step": 1})
    rows = store.read("leads")
    rows.append({**rows[0], "id": "l2", "workspace": "other", "owner": "Zed"})
    store.write("leads", rows)
    seen = []
    sys.modules["app.assignment"].next_member = lambda s, ws, t, exclude=(): seen.append(ws) or ("Bo" if ws == "main" else "Kai")
    ft.start(store, "l1", now=ist(2, 10))
    ft.start(store, "l2", now=ist(2, 10))
    before = lead(store)
    ft.tick(store, ist(2, 12, 5))
    l1, l2 = lead(store), lead(store, "l2")
    assert l1["owner"] == "Bo" and l2["owner"] == "Kai"
    assert seen == ["main", "other"]
    assert l1["linkedin_seq"] == before["linkedin_seq"] and l1["sequence"] == before["sequence"]


def test_reminder_tick_runs_first_touch(tmp_path, fake):
    from app.autorun import reminder_tick
    store = make(tmp_path)
    ft.start(store, "l1", now=ist(2, 10))
    reminder_tick(store, ist(2, 10, 40), sender=lambda m: {"status": "dry_run"})
    assert len(bells(store)) == 1
