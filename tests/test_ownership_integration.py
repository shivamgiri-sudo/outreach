"""Assignment rules + first-touch clock together (the real modules, no fakes): a new lead gets an owner, the clock starts,
alerts, moves it to the next team member and the stats see it. Also: the agent's lookup queue respects ownership."""
from datetime import datetime, timedelta, timezone

from app import assignment as A, first_touch as FT, lead_access, linkedin_finder
from tests.test_assignment_rules import W1, cfg, lead, mk_store, owner_of, put, team

IST = timezone(timedelta(hours=5, minutes=30))
MON_10 = datetime(2026, 11, 9, 10, 0, tzinfo=IST).astimezone(timezone.utc)      # a Monday, 10:00 IST


def test_new_lead_gets_owner_clock_alert_reassign_and_stats(tmp_path):
    store = mk_store(tmp_path, ("Ann", "Bob", "Adm"))
    store.update("settings", lambda s: {**s, "users": [*[u for u in s["users"] if u["name"] != "Adm"],
                                                         {"email": "adm@x.in", "name": "Adm", "role": "admin", "workspaces": [W1]}]})
    A.save_config(store, W1, cfg([team("t1", ["Ann", "Bob"], default=True)], first_touch={"alert_minutes": 30, "reassign_minutes": 120, "alert": True, "reassign": True}))
    put(store, [lead(1)])
    first = A.assign_new(store, next(l for l in store.read("leads") if l["id"] == "l1"))
    assert first in ("Ann", "Bob")
    started = next(l for l in store.read("leads") if l["id"] == "l1")
    assert started.get("first_touch_started") and started["owner_why"]["kind"]               # assignment started the real clock
    store.update_lead("l1", lambda l: {k: v for k, v in l.items() if k not in ("first_touch_started", "first_touch_due_at")})   # replay from a fixed Monday 10:00
    FT.start(store, "l1", MON_10)
    out = FT.tick(store, MON_10 + timedelta(minutes=31))
    assert out["alerted"] == 1
    out = FT.tick(store, MON_10 + timedelta(minutes=125))
    assert out["reassigned"] == 1
    moved = next(l for l in store.read("leads") if l["id"] == "l1")
    assert moved["owner"] != first and moved["reassigns"] == 1 and moved["reassign_log"][0]["from"] == first
    stats = A.stats(store, W1)
    assert stats["reassigned_total"] == 1 and any(m["name"] == first and m["reassigned_away"] == 1 for m in stats["members"])


def test_lookup_queue_skips_leads_the_agent_user_cannot_send_to(tmp_path):
    store = mk_store(tmp_path, ("Ann", "Bob"))
    store.update("settings", lambda s: {**s, "ownership_mode": {W1: True}})
    queued = {"status": "queued", "queued_at": "2026-11-10T08:00:00+00:00"}
    put(store, [lead(1, owner="Bob", linkedin_lookup=queued, contact_name="Bo B", company="X"),
                lead(2, owner="Ann", linkedin_lookup=queued, contact_name="An A", company="Y")])
    ann = {"role": "caller", "name": "Ann", "email": "ann@x.in", "workspaces": [W1]}
    got = linkedin_finder.next_for(store, "", user=ann)
    assert got["lead_id"] == "l2"
