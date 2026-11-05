"""The caller's 09:30 IST weekday summary, and campaigns run by the background timer at most once a day."""
from datetime import datetime, timezone

from app.autorun import reminder_tick, tick
from app.campaigns import run_due_campaigns
from app.digest import run_digest
from app.sequences import IST
from app.store import Store
from tests.campaign_fakes import ZETA_JOBS, FakeIO, client, create, run


def ist(day: int, hour: int, minute: int = 0) -> datetime:
    """A time in November 2026, given in IST (3 Nov 2026 is a Tuesday)."""
    return datetime(2026, 11, day, hour, minute, tzinfo=IST).astimezone(timezone.utc)


def seeded(tmp_path) -> Store:
    store = Store(tmp_path / "d.json")
    store.write("settings", {"caller_name": "Priya"})
    store.write("leads", [
        {"id": "l1", "company": "Acme Retail", "stage": "interested", "timeline": [],
         "consent": {"text": "Yes please", "at": ist(2, 15).isoformat(), "source": "email reply"}},
        {"id": "l2", "company": "Zeta Foods", "stage": "contacted", "timeline": [],
         "meetings": [{"id": "m1", "status": "scheduled", "start": ist(3, 15).isoformat(), "join_url": "https://meet.example/abc"},
                      {"id": "m2", "status": "scheduled", "start": ist(4, 15).isoformat()}]}])
    store.write("tasks", [
        {"id": "t1", "lead_id": "l1", "kind": "call", "title": "Call them", "status": "open", "owner": "Priya", "due_at": ist(3, 11).isoformat()},
        {"id": "t2", "lead_id": "l2", "kind": "follow_up", "title": "Send the sample", "status": "open", "owner": "Priya", "due_at": ist(1, 11).isoformat()},
        {"id": "t3", "lead_id": "l2", "kind": "call", "title": "Call them", "status": "open", "owner": "Rahul", "due_at": ist(3, 12).isoformat()},
        {"id": "t4", "lead_id": "l2", "kind": "call", "title": "Call them", "status": "done", "owner": "Priya", "due_at": ist(3, 10).isoformat()}])
    store.write("campaigns", [{"id": "c1", "name": "Pune support hiring", "runs": [
        {"started_at": ist(3, 7).isoformat(), "new": 4, "enrolled": 2}, {"started_at": ist(1, 7).isoformat(), "new": 9, "enrolled": 9}]}])
    return store


def test_digest_once_per_weekday_at_or_after_0930_ist(tmp_path):
    store = seeded(tmp_path)
    assert run_digest(store, ist(3, 9, 29)) is None
    sent = run_digest(store, ist(3, 9, 30))
    assert sent["to"] == "Priya" and sent["kind"] == "digest"
    assert sent["title"] == "Good morning Priya: 1 call(s) today, 1 overdue, 1 new yes, 1 meeting(s)"
    assert "Acme Retail" in sent["text"] and "Send the sample" in sent["text"] and "3:00 PM IST Zeta Foods https://meet.example/abc" in sent["text"]
    assert "Pune support hiring: 4 new, 2 enrolled" in sent["text"] and "9 new" not in sent["text"]
    assert run_digest(store, ist(3, 9, 45)) is None and run_digest(store, ist(3, 17, 0)) is None     # once a day
    assert store.read("settings")["last_digest_date"] == "2026-11-03"
    assert run_digest(store, ist(7, 10)) is None and run_digest(store, ist(8, 10)) is None          # Saturday, Sunday
    assert run_digest(store, ist(9, 9, 31)) is not None                                              # Monday
    assert len([n for n in store.read("notifications") if n["kind"] == "digest"]) == 2


def test_digest_goes_out_from_the_minute_timer_and_by_email_when_on(tmp_path):
    store = seeded(tmp_path)
    store.write("settings", {"notify_email": True, "caller_name": "Priya", "team": [{"name": "Priya", "email": "priya@team.example"}]})
    mails = []
    sender = lambda message: mails.append(message) or {"status": "sent"}
    assert reminder_tick(store, ist(3, 9, 0), sender)["digest"] is False
    assert reminder_tick(store, ist(3, 9, 31), sender)["digest"] is True
    assert reminder_tick(store, ist(3, 9, 32), sender)["digest"] is False
    digest_mails = [m for m in mails if "Good morning" in m["subject"]]
    assert len(digest_mails) == 1 and digest_mails[0]["to"] == "priya@team.example" and digest_mails[0]["kind"] == "internal"


def test_autorun_runs_a_running_campaign_at_most_once_a_day(tmp_path):
    io = FakeIO()
    c = client(tmp_path, io)
    store = c.app.state.store
    camp = create(c, sources={"careers_html": [{"url": "https://zeta-foods.in/careers", "html": ZETA_JOBS}]})
    transports = io.transports()
    assert run_due_campaigns(store, ist(3, 10), transports) == []                  # draft: never automatic
    c.post(f"/api/campaigns/{camp['id']}/resume")
    first = run_due_campaigns(store, ist(3, 10), transports)
    assert first == [{"campaign_id": camp["id"], "new": 1, "enrolled": 0}]
    assert run_due_campaigns(store, ist(3, 14), transports) == []                  # already ran today
    assert run_due_campaigns(store, ist(4, 20), transports) == []                  # outside business hours
    assert run_due_campaigns(store, ist(7, 10), transports) == []                  # Saturday
    sent = []
    out = tick(store, now=ist(5, 10), sender=lambda m: sent.append(m) or {"status": "dry_run"},
               campaign_runner=lambda now: run_due_campaigns(store, now, transports))
    assert out["campaign_runs"] == [{"campaign_id": camp["id"], "new": 0, "enrolled": 0}]
    runs = c.get(f"/api/campaigns/{camp['id']}/runs").json()
    assert [r["trigger"] for r in runs] == ["auto", "auto"]
    c.post(f"/api/campaigns/{camp['id']}/pause")
    assert run_due_campaigns(store, ist(6, 10), transports) == []                  # paused


def test_a_manual_run_today_counts_as_todays_run(tmp_path):
    io = FakeIO()
    c = client(tmp_path, io)
    camp = create(c, sources={"careers_html": [{"url": "https://zeta-foods.in/careers", "html": ZETA_JOBS}]})
    c.post(f"/api/campaigns/{camp['id']}/resume")
    run(c, camp["id"], now=ist(3, 9, 15).isoformat())
    assert run_due_campaigns(c.app.state.store, ist(3, 11), io.transports()) == []
    preview_only = create(c, name="Preview only", sources={"careers_html": [{"url": "https://zeta-foods.in/careers", "html": ZETA_JOBS}]})
    c.post(f"/api/campaigns/{preview_only['id']}/resume")
    run(c, preview_only["id"], dry_run=True, now=ist(3, 9, 20).isoformat())    # a preview does not use up the day
    assert [r["campaign_id"] for r in run_due_campaigns(c.app.state.store, ist(3, 11), io.transports())] == [preview_only["id"]]
