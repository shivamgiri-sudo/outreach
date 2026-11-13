"""Call log page API: counts, de-dup, IST day range, filters, CSV safety, ownership / workspace scoping, roles."""
import csv
import io

import pytest

from app.activities import log_activity
from tests.ownership_helpers import rig as make_rig


@pytest.fixture
def r(tmp_path): return make_rig(tmp_path)
@pytest.fixture
def off(tmp_path): return make_rig(tmp_path, mode_on=False)


def lid(r, company): return r.lead[company]["id"]


def call(r, company, outcome, by, at, notes=""):
    return log_activity(r.store, lid(r, company), "call", {"outcome": outcome, "by": by, "at": at, "notes": notes})


def get(r, who, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return r.c.get(f"/api/call-log?{q}", headers=r.h[who])


def seed(r):
    call(r, "Acme", "connected", "Ravi", "2026-11-10T04:00:00+00:00", "talked")            # 09:30 IST on the 10th
    call(r, "Acme", "no_answer", "Ravi", "2026-11-10T18:00:00+00:00")                       # 23:30 IST on the 10th
    call(r, "Acme", "meeting_booked", "Ravi", "2026-11-10T19:00:00+00:00")                  # 00:30 IST on the 11th
    call(r, "Beta Ltd", "interested", "Priya", "2026-11-11T05:00:00+00:00", "=HYPERLINK(\"x\")")
    call(r, "Beta Ltd", "busy", "Priya", "2026-11-11T06:00:00+00:00")


def test_counts_and_summary(off):
    seed(off)
    d = get(off, "owner").json()
    assert d["total"] == 5 and len(d["rows"]) == 5
    s = {x["caller"]: x for x in d["summary"]}
    assert s["Ravi"] == {"caller": "Ravi", "calls": 3, "connected": 2, "unanswered": 1, "interested": 0, "meetings": 1, "connect_rate": 66.7}
    assert s["Priya"]["interested"] == 1 and s["Priya"]["unanswered"] == 1 and s["Priya"]["connect_rate"] == 50.0
    assert d["rows"][0]["at"] > d["rows"][-1]["at"]                                          # newest first


def test_dedup_activity_and_timeline(off):
    seed(off)
    # an older call that only exists on the timeline (no activity row) is kept once
    off.store.update_lead(lid(off, "Beta Ltd"), lambda l: {**l, "timeline": [*l["timeline"], {"at": "2026-11-09T05:00:00+00:00", "kind": "call", "text": "Call — wrong number: old note", "by": "Sita"}]})
    d = get(off, "owner").json()
    assert d["total"] == 6                                                                   # 5 logged (each also on the timeline) + 1 legacy
    legacy = [x for x in d["rows"] if x["by"] == "Sita"][0]
    assert legacy["outcome"] == "wrong_number" and legacy["notes"] == "old note"


def test_ist_day_range_inclusive(off):
    seed(off)
    assert get(off, "owner", **{"from": "2026-11-10", "to": "2026-11-10"}).json()["total"] == 2
    assert get(off, "owner", **{"from": "2026-11-11", "to": "2026-11-11"}).json()["total"] == 3       # 00:30 IST call lands on the 11th
    assert get(off, "owner", **{"from": "2026-11-10", "to": "2026-11-11"}).json()["total"] == 5
    assert get(off, "owner", **{"from": "2026-11-12"}).json()["total"] == 0
    assert get(off, "owner", **{"from": "bad"}).status_code == 400
    assert get(off, "owner", **{"from": "2026-11-12", "to": "2026-11-10"}).status_code == 400


def test_filters_and_paging(off):
    seed(off)
    assert get(off, "owner", by="priya").json()["total"] == 2
    d = get(off, "owner", outcome="connected").json()
    assert d["total"] == 1 and len(d["summary"]) == 2                                        # summary still compares callers
    assert get(off, "owner", q="beta").json()["total"] == 2
    assert get(off, "owner", q="talked").json()["total"] == 1
    d = get(off, "owner", size=2, page=2).json()
    assert d["total"] == 5 and len(d["rows"]) == 2
    assert get(off, "owner", size=9999).json()["size"] == 200
    assert get(off, "owner", channel="nope").status_code == 400


def test_other_channels(off):
    seed(off)
    log_activity(off.store, lid(off, "Acme"), "email", {"by": "Ravi", "at": "2026-11-10T05:00:00+00:00", "subject": "Hello"})
    assert get(off, "owner").json()["total"] == 5
    d = get(off, "owner", channel="email").json()
    assert d["total"] == 1 and d["rows"][0]["channel"] == "email"
    assert get(off, "owner", channel="all").json()["total"] == 6
    assert sum(s["calls"] for s in get(off, "owner", channel="all").json()["summary"]) == 5


def test_csv_formula_safe_audited_and_owner_only(off):
    seed(off)
    res = off.c.get("/api/call-log/export?by=Priya", headers=off.h["owner"])
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(res.text)))
    assert rows[0][:3] == ["at", "caller", "channel"] and len(rows) == 3
    assert "'=HYPERLINK" in res.text and ",=HYPERLINK" not in res.text
    assert any(a.get("action") == "call_log.export" for a in off.store.read("audit"))
    for who in ("priya", "view", "adm"):
        assert off.c.get("/api/call-log/export", headers=off.h[who]).status_code == 403


def test_ownership_scoping(r):
    seed(r)                                                   # Acme = Ravi's, Beta = Priya's
    d = get(r, "priya").json()
    assert d["total"] == 2 and {x["company"] for x in d["rows"]} == {"Beta Ltd"}
    assert [s["caller"] for s in d["summary"]] == ["Priya"]
    text = get(r, "priya").text
    for needle in ("Acme", "acme.com", "Ravi Kumar", "98765 43210", "ravi@acme"):
        assert needle not in text
    assert get(r, "ravi").json()["total"] == 3
    assert get(r, "adm").json()["total"] == 5                 # workspace admin sees all
    assert get(r, "owner").json()["total"] == 5
    assert get(r, "view").json()["total"] == 0                # viewer with no team sees nothing
    assert r.c.get("/api/call-log/export", headers=r.h["owner"]).status_code == 200


def test_ownership_off_caller_sees_workspace(off):
    seed(off)
    assert get(off, "priya").json()["total"] == 5


def test_workspace_isolation(off):
    seed(off)
    log_activity(off.store, off.other["id"], "call", {"outcome": "connected", "by": "Zed", "at": "2026-11-11T07:00:00+00:00"})
    main_rows = get(off, "owner").json()
    assert main_rows["total"] == 6                                       # owner with no workspace picked sees every workspace
    assert off.c.get("/api/call-log", headers={**off.h["owner"], "X-Workspace": "main"}).json()["total"] == 5
    only_w2 = off.c.get("/api/call-log", headers={**off.h["owner"], "X-Workspace": off.w2}).json()
    assert only_w2["total"] == 1 and only_w2["rows"][0]["company"] == "Other WS Co"
    assert off.c.get("/api/call-log", headers={**off.h["adm2"]}).json()["total"] == 1
    assert "Other WS Co" not in get(off, "adm").text


def test_roles_can_read(off):
    seed(off)
    for who in ("owner", "adm", "priya", "view"):
        assert get(off, who).status_code == 200
    assert off.c.get("/api/call-log").status_code in (401, 403)


def test_real_outcome_flow_is_logged(off):
    res = off.c.post(f"/api/leads/{lid(off, 'Acme')}/call-outcome", json={"outcome": "connected", "notes": "via api"}, headers=off.h["priya"])
    assert res.status_code == 200
    d = get(off, "priya").json()
    assert d["total"] == 1 and d["rows"][0]["outcome"] == "connected" and d["rows"][0]["notes"] == "via api"
