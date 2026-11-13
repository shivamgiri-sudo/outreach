"""Best time to call: reach rate by IST hour, sample thresholds and ownership scoping."""
from datetime import datetime, timedelta, timezone

from app import best_time as bt
from tests.ownership_helpers import rig

IST = timezone(timedelta(hours=5, minutes=30))


def act(lead_id, hour, outcome, day=10):
    at = datetime(2026, 11, day, hour, 15, tzinfo=IST).astimezone(timezone.utc).isoformat()
    return {"type": "call", "lead_id": lead_id, "outcome": outcome, "at": at}


def sample(lead="L1"):
    rows = []
    for _ in range(8):                               # 11:xx reaches 6 of 8
        rows += [act(lead, 11, "connected")] * 1
    rows = [act(lead, 11, "connected")] * 6 + [act(lead, 11, "no_answer")] * 2
    rows += [act(lead, 12, "interested")] * 5 + [act(lead, 12, "busy")] * 3
    rows += [act(lead, 15, "no_answer")] * 9 + [act(lead, 15, "connected")]
    rows += [act(lead, 9, "no_answer")] * 4             # too few for a bucket
    rows += [act(lead, 11, "invalid_lead")] * 5          # verdicts are not calls
    return rows


def test_not_enough_calls():
    out = bt.compute([{"id": "L1"}], sample()[:10])
    assert out["enough"] is False and out["best"] == [] and out["window"] is None and out["sample"] == 10


def test_names_best_window():
    out = bt.compute([{"id": "L1"}], sample())
    assert out["sample"] == 8 + 8 + 10 + 4 and out["enough"]
    by = {r["hour"]: r for r in out["hours"]}
    assert by[11] == {"hour": 11, "attempts": 8, "reached": 6, "rate": 0.75}
    assert by[12]["rate"] == 0.625 and by[15]["rate"] == 0.1
    assert out["best"] == [11] and out["window"] == {"from": 11, "to": 12}     # 12:00 is under 90% of the top rate
    assert 9 not in out["best"]
    assert out["weekdays"][0]["weekday"] == "Tuesday"


def test_other_leads_activity_is_ignored():
    out = bt.compute([{"id": "L2"}], sample())
    assert out["sample"] == 0


def test_route_is_scoped_by_ownership(tmp_path):
    r = rig(tmp_path)
    ravi = r.lead["Acme"]["id"]
    beta = r.lead["Beta Ltd"]["id"]
    r.store.update("activities", lambda rows: [*rows, *[act(ravi, 11, "connected") for _ in range(40)], *[act(beta, 14, "no_answer") for _ in range(3)]])
    owner = r.c.get("/api/best-time", headers=r.h["owner"]).json()
    assert owner["sample"] == 43 and owner["enough"]
    priya = r.c.get("/api/best-time", headers=r.h["priya"])
    assert priya.status_code == 200
    assert priya.json()["sample"] == 3 and priya.json()["enough"] is False and priya.json()["best"] == []
    view = r.c.get("/api/best-time", headers=r.h["view"])
    assert view.status_code == 200
    for needle in ("acme.com", "Ravi Kumar", "98765 43210", "ravi@acme"):
        assert needle not in priya.text
