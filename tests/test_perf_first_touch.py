"""Speed-to-lead scales: a 20,000-lead store (generated here, not a fixture) must tick and update fast."""
import time
from datetime import datetime, timedelta, timezone

from app import first_touch as ft
from app.store import Store

N = 20000


def big_store(tmp_path):
    store = Store(tmp_path / "big.json")
    now = datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)
    leads = []
    for i in range(N):
        lead = {"id": f"l{i}", "company": f"Co {i}", "stage": "new", "timeline": [{"at": now.isoformat(), "kind": "created", "text": "x"}] * 3,
                "owner": f"u{i % 20}", "workspace": "main", "email": f"p{i}@co{i}.com"}
        if i % 10 == 0:                                      # one in ten has a running first-touch clock
            lead.update(first_touch_started=now.isoformat(), first_touch_due_at=(now + timedelta(minutes=30)).isoformat())
        leads.append(lead)
    store.write("leads", leads)
    store.write("notifications", [])
    return store, now


def test_tick_and_update_lead_stay_fast_on_20000_leads(tmp_path, monkeypatch):
    monkeypatch.setattr(ft, "_cfg", lambda store, ws: {"on": False, **ft.DEFAULTS})        # clock config off: tick only scans
    store, now = big_store(tmp_path)
    t = time.perf_counter()
    ft.tick(store, now + timedelta(hours=1))
    tick_s = time.perf_counter() - t
    assert tick_s < 3.0, f"tick took {tick_s:.2f}s"
    monkeypatch.setattr(ft, "_cfg", lambda store, ws: {"on": True, **ft.DEFAULTS})         # clocks on, 2,000 running, none due yet
    t = time.perf_counter()
    assert ft.tick(store, now + timedelta(minutes=1)) == {"alerted": 0, "reassigned": 0, "stuck": 0}
    tick_on_s = time.perf_counter() - t
    assert tick_on_s < 3.0, f"tick with clocks on took {tick_on_s:.2f}s"
    # the first-touch hook itself: stamp() on a lead with a running clock and one on a lead without
    old = store.read("leads")[0]
    new = {**old, "timeline": [*old["timeline"], {"kind": "note", "at": now.isoformat(), "by": old["owner"], "text": "n"}]}
    t = time.perf_counter()
    for _ in range(2000):
        ft.stamp(old, new)
    hook_ms = (time.perf_counter() - t) / 2000 * 1000
    assert hook_ms < 5, f"stamp hook {hook_ms:.3f}ms per update"
    t = time.perf_counter()
    store.update_lead("l19999", lambda l: {**l, "company": "Renamed"})
    assert time.perf_counter() - t < 5.0
