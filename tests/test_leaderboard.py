"""Leaderboard API: counts, markers, de-dup, IST period edges, target maths, ranking, previous period, scoping, roles, validation."""
from datetime import datetime, timezone

import pytest

from app import leaderboard as lb
from app.activities import log_activity
from tests.ownership_helpers import rig as make_rig

WEEK = {"period": "custom", "from": "2026-10-05", "to": "2026-10-11"}      # Mon-Sun, long past, so every working day counts (6)


@pytest.fixture
def r(tmp_path): return make_rig(tmp_path)
@pytest.fixture
def off(tmp_path): return make_rig(tmp_path, mode_on=False)


def call(r, company, outcome, by, at):
    return log_activity(r.store, r.lead[company]["id"], "call", {"outcome": outcome, "by": by, "at": at})


def seed(r):
    call(r, "Acme", "connected", "Ravi", "2026-10-05T04:00:00+00:00")           # Mon 09:30 IST
    call(r, "Acme", "no_answer", "Ravi", "2026-10-05T05:00:00+00:00")
    call(r, "Acme", "meeting_booked", "Ravi", "2026-10-05T06:00:00+00:00")
    call(r, "Acme", "invalid_lead", "Ravi", "2026-10-06T06:00:00+00:00")          # a mark: not a call
    call(r, "Acme", "interested", "Ravi", "2026-10-11T18:00:00+00:00")          # Sun 23:30 IST: last minute of the week
    call(r, "Acme", "busy", "Ravi", "2026-10-11T19:00:00+00:00")                # Mon 00:30 IST: next week, outside
    call(r, "Acme", "connected", "Ravi", "2026-10-04T10:00:00+00:00")           # previous period (Sun 4 Oct)
    call(r, "Beta Ltd", "interested", "Priya", "2026-10-06T05:00:00+00:00")
    call(r, "Beta Ltd", "busy", "Priya", "2026-10-06T06:00:00+00:00")
    call(r, "Beta Ltd", "connected", "Priya", "2026-10-07T05:00:00+00:00")


def board(r, who="owner", **params):
    q = "&".join(f"{k}={v}" for k, v in {**WEEK, **params}.items())
    return r.c.get(f"/api/leaderboard?{q}", headers=r.h[who])


def by_name(d): return {x["caller"]: x for x in d["rows"]}


def test_counts_markers_and_dedup(off):
    seed(off)
    d = board(off).json()
    s = by_name(d)
    assert (s["Ravi"]["calls"], s["Ravi"]["connected"], s["Ravi"]["unanswered"], s["Ravi"]["interested"], s["Ravi"]["meetings"]) == (4, 3, 1, 1, 1)
    assert s["Ravi"]["marked"] == 1 and s["Ravi"]["connect_rate"] == 75.0           # the mark is not a call and not in the rate
    assert s["Ravi"]["active_days"] == 2 and s["Ravi"]["avg_per_day"] == 2.0
    assert (s["Priya"]["calls"], s["Priya"]["connected"], s["Priya"]["interested"], s["Priya"]["marked"]) == (3, 2, 1, 0)
    assert s["Priya"]["connect_rate"] == 66.7 and s["Priya"]["avg_per_day"] == 1.5
    assert d["totals"]["calls"] == 7 and d["totals"]["marked"] == 1 and d["totals"]["connected"] == 5


def test_period_edges_in_ist(off):
    seed(off)
    assert by_name(board(off, **{"from": "2026-10-11", "to": "2026-10-11"}).json())["Ravi"]["interested"] == 1      # 23:30 IST is still the 11th
    d = board(off, **{"from": "2026-10-12", "to": "2026-10-12"}).json()
    assert by_name(d)["Ravi"]["calls"] == 1 and len(d["rows"]) == 1                                                  # 00:30 IST is the 12th


def test_period_windows():
    sun = datetime(2026, 11, 15, 6, 30, tzinfo=timezone.utc)                       # Sunday noon IST
    w = lb.resolve("week", "", "", sun)
    assert (w["from"], w["to"], w["days"]) == ("2026-11-09", "2026-11-15", 7)
    assert lb.working_days(lb.date(2026, 11, 9), lb.date(2026, 11, 15), lb.date(2026, 11, 15)) == 6          # Sunday is not a working day
    mon = lb.resolve("week", "", "", datetime(2026, 11, 8, 19, 0, tzinfo=timezone.utc))       # 00:30 IST Monday 9th
    assert (mon["from"], mon["to"], mon["days"], mon["prev_from"], mon["prev_to"]) == ("2026-11-09", "2026-11-09", 1, "2026-11-08", "2026-11-08")
    edge = lb.resolve("month", "", "", datetime(2026, 11, 30, 19, 0, tzinfo=timezone.utc))    # 00:30 IST on 1 Dec
    assert (edge["from"], edge["to"], edge["prev_from"], edge["prev_to"]) == ("2026-12-01", "2026-12-01", "2026-11-30", "2026-11-30")
    wed = lb.resolve("week", "", "", datetime(2026, 11, 11, 6, 0, tzinfo=timezone.utc))
    assert (wed["days"], wed["prev_from"], wed["prev_to"]) == (3, "2026-11-06", "2026-11-08")
    y = lb.resolve("yesterday", "", "", sun)
    assert y["from"] == y["to"] == "2026-11-14"
    assert lb.working_days(lb.date(2026, 11, 9), lb.date(2026, 11, 15), lb.date(2026, 11, 10)) == 2       # days still to come are not owed


def test_target_attainment(off):
    seed(off)
    d = by_name(board(off).json())
    assert d["Ravi"]["target"] == 240 and d["Ravi"]["attainment"] == 2                    # team default 40 a day x 6 working days
    assert off.c.put("/api/my-day/targets", json={"scope": "priya@x.in", "targets": {"calls": 3}}, headers=off.h["owner"]).status_code == 200
    assert off.c.put("/api/my-day/targets", json={"scope": "ravi@x.in", "targets": {"calls": 0}}, headers=off.h["owner"]).status_code == 200
    d = board(off).json()
    s = by_name(d)
    assert s["Priya"]["daily_target"] == 3 and s["Priya"]["target"] == 18 and s["Priya"]["attainment"] == 17     # 3 of 18
    assert s["Ravi"]["attainment"] is None                                                # a zero target has no percentage
    assert off.c.put("/api/my-day/targets", json={"scope": "team", "targets": {"calls": 1}}, headers=off.h["owner"]).status_code == 200
    s = by_name(board(off).json())
    assert s["Ravi"]["attainment"] is None and s["Priya"]["target"] == 18                  # own target wins over the team default
    assert off.c.put("/api/my-day/targets", json={"scope": "ravi@x.in", "targets": {"calls": 1}}, headers=off.h["owner"]).status_code == 200
    assert by_name(board(off).json())["Ravi"]["attainment"] == 67                         # 4 of 6


def test_rank_by_and_ties(off):
    seed(off)
    d = board(off).json()
    assert [(x["caller"], x["rank"]) for x in d["rows"]] == [("Ravi", 1), ("Priya", 2)]
    d = board(off, rank_by="interested").json()                                           # 1 each: shared rank, name order
    assert [(x["caller"], x["rank"]) for x in d["rows"]] == [("Priya", 1), ("Ravi", 1)]
    d = board(off, rank_by="meetings").json()
    assert [(x["caller"], x["rank"]) for x in d["rows"]] == [("Ravi", 1), ("Priya", 2)]
    d = board(off, rank_by="connected").json()
    assert d["rank_by"] == "connected" and d["rows"][0]["caller"] == "Ravi"
    assert board(off, rank_by="nonsense").status_code == 400


def test_previous_period_delta(off):
    seed(off)
    d = board(off).json()
    assert d["previous"]["from"] == "2026-09-28" and d["previous"]["to"] == "2026-10-04" and d["previous"]["calls"] == 1
    s = by_name(d)
    assert (s["Ravi"]["calls_prev"], s["Ravi"]["calls_delta"], s["Priya"]["calls_delta"]) == (1, 3, 3)
    assert d["totals"]["calls_delta"] == 6


def test_empty_period_has_no_rows(off):
    seed(off)
    d = board(off, **{"from": "2026-01-05", "to": "2026-01-11"}).json()
    assert d["rows"] == [] and d["totals"]["calls"] == 0 and d["totals"]["attainment"] is None


def test_validation(off):
    seed(off)
    assert board(off, **{"from": "bad"}).status_code == 400
    assert board(off, **{"from": "2026-10-12", "to": "2026-10-05"}).status_code == 400
    assert off.c.get("/api/leaderboard?period=custom&from=2026-10-01", headers=off.h["owner"]).status_code == 400
    assert off.c.get("/api/leaderboard?period=forever", headers=off.h["owner"]).status_code == 400
    assert board(off, **{"from": "2026-07-01", "to": "2026-10-01"}).status_code == 400          # 93 days
    assert board(off, **{"from": "2026-07-02", "to": "2026-10-01"}).status_code == 200          # 92 days
    assert off.c.get("/api/leaderboard", headers=off.h["owner"]).status_code == 200            # defaults to today


def test_ownership_scoping_and_no_contact_details(r):
    seed(r)                                                       # Acme = Ravi's lead, Beta = Priya's
    d = board(r, "priya").json()
    assert list(by_name(d)) == ["Priya"] and d["totals"]["calls"] == 3
    assert [x["you"] for x in d["rows"]] == [True]
    text = board(r, "priya").text
    for needle in ("Acme", "acme.com", "Ravi Kumar", "98765 43210", "ravi@acme", "Beta", "sita@beta"):
        assert needle not in text
    assert list(by_name(board(r, "ravi").json())) == ["Ravi"]
    for who in ("adm", "owner"):
        assert set(by_name(board(r, who).json())) == {"Ravi", "Priya"}
    assert board(r, "view").json()["rows"] == []                  # a viewer with no team sees no leads, so no calls
    assert board(r, "owner").json()["rows"][0]["you"] is False


def test_ownership_off_everyone_sees_the_whole_board(off):
    seed(off)
    assert set(by_name(board(off, "priya").json())) == {"Ravi", "Priya"}
    assert [x["caller"] for x in board(off, "priya").json()["rows"] if x["you"]] == ["Priya"]
    text = board(off, "view").text
    for needle in ("Acme", "acme.com", "Ravi Kumar", "98765 43210", "ravi@acme", "@"):
        assert needle not in text                                 # names and numbers only


def test_workspace_isolation_and_roles(off):
    seed(off)
    log_activity(off.store, off.other["id"], "call", {"outcome": "connected", "by": "Zed", "at": "2026-10-06T07:00:00+00:00"})
    both = board(off).json()
    assert "Zed" in by_name(both)
    main = off.c.get("/api/leaderboard?" + "&".join(f"{k}={v}" for k, v in WEEK.items()), headers={**off.h["owner"], "X-Workspace": "main"}).json()
    assert "Zed" not in by_name(main)
    assert "Zed" not in board(off, "adm").text and list(by_name(board(off, "adm2").json())) == ["Zed"]
    for who in ("owner", "adm", "priya", "view"):
        assert board(off, who).status_code == 200
    assert off.c.get("/api/leaderboard").status_code in (401, 403)


def test_real_call_flow_counts_today(off):
    res = off.c.post(f"/api/leads/{off.lead['Acme']['id']}/call-outcome", json={"outcome": "connected", "notes": "x"}, headers=off.h["priya"])
    assert res.status_code == 200
    d = off.c.get("/api/leaderboard?period=today", headers=off.h["priya"]).json()
    s = by_name(d)
    assert s["Priya"]["calls"] == 1 and s["Priya"]["connected"] == 1 and s["Priya"]["you"] is True
    assert off.c.get("/api/leaderboard?period=month&rank_by=connected", headers=off.h["priya"]).status_code == 200
