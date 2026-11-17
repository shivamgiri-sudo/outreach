"""Bounce meter per sending mailbox (app/sender_health.py): 7/30-day windows, warn/pause thresholds, minimum sends,
per-mailbox pause when more than one mailbox sends, the single-mailbox global pause unchanged, and the owner's resume."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import inbound, sender_health
from app.main import create_app
from app.sequence_runner import sending_paused
from tests.flow_helpers import connect_email

NOW = datetime.now(timezone.utc)
CURRENT = "hello@mail.example.in"          # the address connect_email sets
OLD = "old@mail.example.in"


def at(days_ago, minute=0):
    return (NOW - timedelta(days=days_ago, minutes=minute)).isoformat(timespec="seconds")


def make(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    connect_email(c)
    return c, c.app.state.store


def seed(store, sender, n, bounced=0, days_ago=1, prefix="x", stamp=True):
    """n sent rows from `sender` to n addresses; the first `bounced` of them bounce an hour later."""
    rows, events = [], []
    for i in range(n):
        to = f"{prefix}{i}@{sender.split('@')[0]}-co.in"
        rows.append({"lead_id": f"L{prefix}{i}", "to": to, "status": "sent", "sent_at": at(days_ago, 60), "step": "Email 1",
                     **({"from_email": sender} if stamp else {})})
        if i < bounced:
            events.append({"key": f"bounce:{to}", "type": "bounce", "email": to, "at": at(days_ago)})
    store.update("outbox", lambda r: [*r, *rows])
    store.update("delivery_events", lambda r: [*r, *events])
    return [e["email"] for e in events]


def row(rows, sender):
    return next(r for r in rows if r["sender"] == sender)


def test_windows_and_attribution(tmp_path):
    c, store = make(tmp_path)
    seed(store, CURRENT, 10, bounced=1, days_ago=2, prefix="a")
    seed(store, CURRENT, 10, bounced=2, days_ago=20, prefix="b")
    seed(store, OLD, 4, bounced=0, days_ago=3, prefix="c")
    seed(store, OLD, 5, bounced=5, days_ago=40, prefix="d")                      # outside both windows
    t = sender_health.tally(store, NOW)
    assert (t[CURRENT]["sent_7"], t[CURRENT]["bounced_7"], t[CURRENT]["sent_30"], t[CURRENT]["bounced_30"]) == (10, 1, 20, 3)
    assert (t[OLD]["sent_7"], t[OLD]["bounced_30"]) == (4, 0)
    rows = sender_health.meter(store, NOW)
    cur = row(rows, CURRENT)
    assert cur["current"] and cur["rate_7"] == 0.1 and cur["rate_30"] == 0.15 and cur["level"] == "amber"   # 10 sends: under the minimum, so not red
    assert row(rows, OLD)["level"] == "green"


def test_unstamped_rows_count_for_the_current_address(tmp_path):
    c, store = make(tmp_path)
    seed(store, CURRENT, 5, stamp=False)
    assert list(sender_health.tally(store, NOW)) == [CURRENT]


def test_levels_warn_at_2_and_red_above_5_with_minimum_sends():
    base = {"sent_7": 100, "sent_30": 100, "complaints_7": 0}
    assert sender_health.level({**base, "rate_7": 0.019, "rate_30": 0.01}, False) == "green"
    assert sender_health.level({**base, "rate_7": 0.02, "rate_30": 0.02}, False) == "amber"
    assert sender_health.level({**base, "rate_7": 0.051, "rate_30": 0.03}, False) == "red"
    assert sender_health.level({**base, "sent_7": 49, "rate_7": 0.2, "rate_30": 0.2}, False) == "amber"     # under 50 sends
    assert sender_health.level({**base, "rate_7": 0.0, "rate_30": 0.0}, True) == "red"                     # paused
    assert sender_health.level({"sent_7": 0, "sent_30": 0, "complaints_7": 0, "rate_7": 0, "rate_30": 0}, False) == "none"


def test_two_mailboxes_pause_only_the_bad_one_and_the_owner_resumes_it(tmp_path):
    c, store = make(tmp_path)
    seed(store, OLD, 400, bounced=1, prefix="o")                                  # 0.25%: fine, and keeps the blended rate under 2%
    seed(store, CURRENT, 60, bounced=3, prefix="n")                               # exactly 5%: not above it yet
    assert sender_health.multi_mailbox(store, NOW) and sender_health.pause_unhealthy(store, NOW) is None
    out = inbound.process_delivery_event(store, {"type": "bounce", "email": "n3@hello-co.in", "provider_id": "p2", "at": at(0)}, NOW)
    assert CURRENT in out["paused"] and OLD not in out["paused"]
    settings = store.read("settings")
    assert "sending_paused" not in settings and list(settings["mailbox_paused"]) == [CURRENT]
    assert sending_paused(store, NOW)["mailbox"] == CURRENT                       # the runner, send-now and flows stop for it
    rows = c.get("/api/deliverability").json()                                     # no leads seen here: the meter is cut to visible leads
    assert rows["by_sender"] == [] and rows["sender_thresholds"]["pause_rate"] == 0.05
    meter = {r["sender"]: r for r in sender_health.meter(store, NOW)}
    assert meter[CURRENT]["level"] == "red" and meter[CURRENT]["paused"]["scope"] == "mailbox" and meter[OLD]["paused"] is None
    assert c.post("/api/deliverability/senders/resume", json={"sender": OLD}).status_code == 404
    r = c.post("/api/deliverability/senders/resume", json={"sender": CURRENT.upper()})
    assert r.status_code == 200 and r.json()["resumed"] == "mailbox" and sending_paused(store, NOW) is None
    assert any(a.get("action") == "deliverability.resume_sender" for a in store.read("audit"))


def test_minimum_sends_before_a_mailbox_pauses(tmp_path):
    c, store = make(tmp_path)
    seed(store, OLD, 30, prefix="o")
    seed(store, CURRENT, 40, bounced=8, prefix="n")                               # 20%, but only 40 sends in 7 days
    assert sender_health.multi_mailbox(store, NOW)
    assert sender_health.pause_unhealthy(store, NOW) is None and not store.read("settings").get("mailbox_paused")


def test_old_mailbox_bounces_do_not_pause_the_new_one(tmp_path):
    c, store = make(tmp_path)
    seed(store, OLD, 60, bounced=6, prefix="o")                                   # 10% on the old address
    seed(store, CURRENT, 60, prefix="n")
    assert "old@" in sender_health.pause_unhealthy(store, NOW)
    assert list(store.read("settings")["mailbox_paused"]) == [OLD] and sending_paused(store, NOW) is None


def test_single_mailbox_keeps_the_global_pause_at_2_percent(tmp_path):
    c, store = make(tmp_path)
    seed(store, CURRENT, 60, bounced=1, prefix="n", stamp=False)                  # older rows without the stamp: one mailbox
    out = inbound.process_delivery_event(store, {"type": "bounce", "email": "n1@hello-co.in", "provider_id": "q", "at": at(0)}, NOW)
    assert not sender_health.multi_mailbox(store, NOW)
    assert out["paused"] and "Outbox page" in out["paused"]                       # 2/60 = 3.3% > 2%: the platform's pause, as before
    settings = store.read("settings")
    assert settings["sending_paused"]["resume_at"] is None and not settings.get("mailbox_paused")
    meter = sender_health.meter(store, NOW)
    assert meter[0]["paused"]["scope"] == "all" and meter[0]["level"] == "red"
    r = c.post("/api/deliverability/senders/resume", json={"sender": CURRENT})     # resume from the meter lifts the global pause
    assert r.json()["resumed"] == "all" and "sending_paused" not in store.read("settings")


def test_outbox_resume_also_lifts_the_mailbox_in_use(tmp_path):
    c, store = make(tmp_path)
    store.update("settings", lambda s: {**s, "mailbox_paused": {CURRENT: {"reason": "x", "at": at(0)}, OLD: {"reason": "y", "at": at(0)}}})
    assert sending_paused(store, NOW)
    c.post("/api/sending/resume")
    assert sending_paused(store, NOW) is None and list(store.read("settings")["mailbox_paused"]) == [OLD]


def test_sent_messages_carry_their_mailbox(tmp_path):
    from app.sequence_runner import run_due
    from tests.flow_helpers import go_live
    from tests.test_email_gate import T0, Sender
    c, store = make(tmp_path)
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nAcme,Ravi,ravi@acme.com\n", "source": "apollo"})
    go_live(c)
    lead = c.get("/api/leads").json()[0]
    c.post(f"/api/leads/{lead['id']}/enroll")
    assert run_due(store, T0, "", Sender())["sent"] == 1
    assert store.read("outbox")[-1]["from_email"] == CURRENT


def test_several_mailboxes_still_get_the_blended_2_percent_pause(tmp_path):
    c, store = make(tmp_path)
    seed(store, OLD, 60, bounced=1, prefix="o")                                   # 1.7%
    seed(store, CURRENT, 60, bounced=2, prefix="n")                               # 3.3%: under the 5% per-mailbox limit
    out = inbound.process_delivery_event(store, {"type": "bounce", "email": "n2@hello-co.in", "provider_id": "pz", "at": at(0)}, NOW)
    assert sender_health.multi_mailbox(store, NOW)
    assert out["paused"] and "sending_paused" in store.read("settings")          # 4 of 120 = 3.3% blended: the global pause holds all mail
