"""Flow engine: waits in IST business hours, branching, sticky splits, replies, idempotency, stops, triggers, autorun."""
import threading
from datetime import datetime, timedelta, timezone

import pytest

from app import autorun
from app.flow_actions import next_business_time, split_variant
from tests.flow_helpers import (unsubscribe, T0, chain, edge, email_node, enroll, flow_outbox, lead, make_client, make_flow, node,
                                patch_lead, runs, store_of, tick, timeline_texts)

TRIGGER = node("t", "trigger.manual")
IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture()
def c(tmp_path):
    return make_client(tmp_path)


def tags(c, lead_id):
    return c.get(f"/api/leads/{lead_id}").json().get("tags") or []


# ---------------------------------------------------------------- waits
def test_next_business_time_in_ist():
    friday_evening = datetime(2026, 11, 6, 17, 30, tzinfo=IST)
    assert next_business_time(friday_evening) == friday_evening
    assert next_business_time(datetime(2026, 11, 6, 18, 0, tzinfo=IST)) == datetime(2026, 11, 9, 9, 0, tzinfo=IST)
    assert next_business_time(datetime(2026, 11, 4, 7, 15, tzinfo=IST)) == datetime(2026, 11, 4, 9, 0, tzinfo=IST)
    assert next_business_time(datetime(2026, 11, 7, 11, 0, tzinfo=IST)) == datetime(2026, 11, 9, 9, 0, tzinfo=IST)


def test_wait_until_business_hours_moves_a_friday_evening_wait_to_monday_morning(c):
    flow = make_flow(c, [TRIGGER, node("w", "wait", days=0, hours=2, business_hours=True), node("tag", "add_tag", tag="done")],
                     chain("t", "w", "tag"))
    acme = lead(c)["id"]
    enroll(c, flow, acme)
    friday = datetime(2026, 11, 6, 17, 0, tzinfo=IST)
    tick(c, friday)
    run = runs(c, flow)[0]
    assert run["status"] == "waiting" and datetime.fromisoformat(run["wait_until"]) == datetime(2026, 11, 9, 9, 0, tzinfo=IST)
    tick(c, datetime(2026, 11, 8, 23, 0, tzinfo=IST))
    assert tags(c, acme) == []
    tick(c, datetime(2026, 11, 9, 9, 1, tzinfo=IST))
    assert tags(c, acme) == ["done"] and runs(c, flow)[0]["status"] == "done"


def test_plain_wait_in_hours(c):
    flow = make_flow(c, [TRIGGER, node("w", "wait", days=0, hours=3), node("tag", "add_tag", tag="later")], chain("t", "w", "tag"))
    acme = lead(c)["id"]
    enroll(c, flow, acme)
    tick(c, T0)
    tick(c, T0 + timedelta(hours=2, minutes=59))
    assert tags(c, acme) == []
    tick(c, T0 + timedelta(hours=3))
    assert tags(c, acme) == ["later"]


# ---------------------------------------------------------------- branching
def test_if_sends_leads_down_yes_or_no(c):
    nodes = [TRIGGER, node("check", "if", field="open_roles", op="gt", value=5),
             node("big", "add_tag", tag="big-hirer"), node("small", "add_tag", tag="small-hirer")]
    flow = make_flow(c, nodes, [edge("t", "check"), edge("check", "big", "yes"), edge("check", "small", "no")])
    acme, beta = lead(c)["id"], lead(c, "Beta")["id"]
    enroll(c, flow, acme, beta)
    tick(c)
    assert tags(c, acme) == ["big-hirer"] and tags(c, beta) == ["small-hirer"]
    history = {r["lead_id"]: [h["result"] for h in r["history"]] for r in runs(c, flow)}
    assert "yes — open roles is 12" in history[acme]


@pytest.mark.parametrize("field,op,value,expected", [
    ("city", "eq", "pune", True), ("city", "in", "Delhi, Pune", True), ("city", "ne", "Pune", False),
    ("title", "contains", "hr", True), ("has_phone", "eq", "true", True), ("has_consent", "eq", "true", False),
    ("tag", "contains", "vip", True), ("tag", "exists", "", True), ("country", "exists", "", True), ("band", "eq", "cold", False),
])
def test_if_operators(c, field, op, value, expected):
    flow = make_flow(c, [TRIGGER, node("check", "if", field=field, op=op, value=value), node("y", "add_tag", tag="y"),
                         node("n", "add_tag", tag="n")], [edge("t", "check"), edge("check", "y", "yes"), edge("check", "n", "no")])
    acme = lead(c)["id"]
    patch_lead(c, acme, city="Pune", tags=["VIP"])
    enroll(c, flow, acme)
    tick(c)
    assert ("y" in tags(c, acme)) is expected


def test_split_is_sticky_and_recorded(c):
    nodes = [TRIGGER, node("ab", "split", percent_a=50), node("a", "add_tag", tag="variant-a"), node("b", "add_tag", tag="variant-b")]
    flow = make_flow(c, nodes, [edge("t", "ab"), edge("ab", "a", "a"), edge("ab", "b", "b")])
    acme = lead(c)["id"]
    expected = split_variant(acme, "ab", 50)
    assert all(split_variant(acme, "ab", 50) == expected for _ in range(5))
    assert len({split_variant(f"lead{i}", "ab", 50) for i in range(40)}) == 2
    simulated = c.post(f"/api/flows/{flow['id']}/simulate", json={"lead_id": acme}).json()["path"][1]["label"]
    enroll(c, flow, acme)
    tick(c)
    run = runs(c, flow)[0]
    assert run["variants"] == {"ab": expected} == {"ab": simulated}
    assert tags(c, acme) == [f"variant-{expected}"]
    assert c.get(f"/api/flows/{flow['id']}").json()["stats"]["split"]["ab"][expected] == 1


# ---------------------------------------------------------------- replies
def reply_flow(c, stop_on_reply=True):
    nodes = [TRIGGER, node("wr", "wait_for_reply", days=3), node("q", "add_tag", tag="question"),
             node("yes", "add_tag", tag="interested"), node("quiet", "add_tag", tag="no-reply")]
    edges = [edge("t", "wr"), edge("wr", "q", "question"), edge("wr", "yes", "interested"), edge("wr", "quiet", "timeout")]
    return make_flow(c, nodes, edges, stop_on_reply=stop_on_reply)


def test_wait_for_reply_routes_by_label_and_by_timeout(c):
    flow = reply_flow(c)
    acme, beta = lead(c)["id"], lead(c, "Beta")["id"]
    enroll(c, flow, acme, beta)
    tick(c, T0)
    c.post(f"/api/leads/{acme}/reply", json={"text": "How much does it cost?"})
    tick(c, T0 + timedelta(hours=1))
    assert tags(c, acme) == ["question"] and tags(c, beta) == []
    tick(c, T0 + timedelta(days=3, minutes=1))
    assert tags(c, beta) == ["no-reply"]
    assert {r["lead_id"]: r["status"] for r in runs(c, flow)} == {acme: "done", beta: "done"}


def test_out_of_office_does_not_count_as_a_reply(c):
    flow = reply_flow(c)
    acme = lead(c)["id"]
    enroll(c, flow, acme)
    tick(c, T0)
    c.post(f"/api/leads/{acme}/reply", json={"text": "Automatic reply: I am out of office until Monday"})
    tick(c, T0 + timedelta(hours=1))
    assert runs(c, flow)[0]["status"] == "waiting" and tags(c, acme) == []


def test_a_reply_stops_the_run_unless_the_flow_waits_for_it(c):
    nodes = [TRIGGER, email_node("m1"), node("w", "wait", days=3, hours=0), email_node("m2", subject="Second for {{company}}")]
    flow = make_flow(c, nodes, chain("t", "m1", "w", "m2"))
    acme = lead(c)["id"]
    enroll(c, flow, acme)
    tick(c, T0)
    c.post(f"/api/leads/{acme}/reply", json={"text": "ok thanks"})
    tick(c, T0 + timedelta(days=4))
    assert len(flow_outbox(c)) == 1
    run = runs(c, flow)[0]
    assert run["status"] == "stopped" and "they replied" in run["stop_reason"]
    assert any("Flow Test flow: stopped — they replied" in t for t in timeline_texts(c, acme))


def test_keep_going_on_replies_when_the_flow_says_so(c):
    nodes = [TRIGGER, node("w", "wait", days=1, hours=0), node("tag", "add_tag", tag="continued")]
    flow = make_flow(c, nodes, chain("t", "w", "tag"), stop_on_reply=False)
    acme = lead(c)["id"]
    enroll(c, flow, acme)
    tick(c, T0)
    c.post(f"/api/leads/{acme}/reply", json={"text": "How much does it cost?"})
    tick(c, T0 + timedelta(days=1, minutes=1))
    assert tags(c, acme) == ["continued"]


# ---------------------------------------------------------------- idempotency and limits
def test_two_ticks_do_one_action(c):
    flow = make_flow(c, [TRIGGER, email_node(), node("tag", "add_tag", tag="x")], chain("t", "mail", "tag"))
    enroll(c, flow, lead(c)["id"])
    tick(c, T0)
    tick(c, T0)
    assert len(flow_outbox(c)) == 1
    assert [h["node"] for h in runs(c, flow)[0]["history"]].count("mail") == 1


def test_parallel_ticks_never_double_send(c):
    flow = make_flow(c, [TRIGGER, email_node()], chain("t", "mail"))
    enroll(c, flow, lead(c)["id"])
    threads = [threading.Thread(target=lambda: tick(c, T0)) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(flow_outbox(c)) == 1


def test_an_interrupted_step_is_never_repeated(c):
    flow = make_flow(c, [TRIGGER, email_node()], chain("t", "mail"))
    enroll(c, flow, lead(c)["id"])
    store = store_of(c)
    store.update("flow_runs", lambda rows: [{**r, "claim": {"step": r["step"], "node": "mail", "at": T0.isoformat()}} for r in rows])
    tick(c, T0)
    run = runs(c, flow)[0]
    assert flow_outbox(c) == [] and run["status"] == "error" and "interrupted" in run["stop_reason"]


def test_never_enrolled_twice_in_the_same_flow(c):
    flow = make_flow(c, [TRIGGER, node("tag", "add_tag", tag="x")], chain("t", "tag"))
    acme = lead(c)["id"]
    assert enroll(c, flow, acme)["enrolled"] == [acme]
    tick(c)
    again = enroll(c, flow, acme)
    assert again["enrolled"] == [] and "never starts twice" in again["refused"][0]["reason"]


def test_drafts_and_paused_flows_take_no_leads_and_do_not_advance(c):
    nodes = [TRIGGER, node("w", "wait", days=1, hours=0), node("tag", "add_tag", tag="x")]
    draft = make_flow(c, nodes, chain("t", "w", "tag"), activate=False)
    assert "Activate the flow first" in enroll(c, draft, lead(c)["id"])["refused"][0]["reason"]
    flow = make_flow(c, nodes, chain("t", "w", "tag"), name="Paused")
    acme = lead(c)["id"]
    enroll(c, flow, acme)
    tick(c, T0)
    c.post(f"/api/flows/{flow['id']}/pause")
    tick(c, T0 + timedelta(days=2))
    assert tags(c, acme) == []
    c.post(f"/api/flows/{flow['id']}/activate")
    tick(c, T0 + timedelta(days=2))
    assert tags(c, acme) == ["x"]


def test_an_idle_tick_writes_nothing(c, tmp_path):
    flow = make_flow(c, [TRIGGER, node("w", "wait", days=2, hours=0), node("tag", "add_tag", tag="x")], chain("t", "w", "tag"))
    enroll(c, flow, lead(c)["id"])
    tick(c, T0)
    before = (tmp_path / "d.json").read_bytes()
    tick(c, T0 + timedelta(hours=5))
    assert (tmp_path / "d.json").read_bytes() == before


def test_step_limit_stops_runaway_runs(c, monkeypatch):
    from app import flow_engine
    monkeypatch.setattr(flow_engine, "MAX_STEPS_PER_RUN", 2)
    flow = make_flow(c, [TRIGGER, node("a", "add_tag", tag="a"), node("b", "add_tag", tag="b"), node("d", "add_tag", tag="d")],
                     chain("t", "a", "b", "d"))
    enroll(c, flow, lead(c)["id"])
    tick(c)
    run = runs(c, flow)[0]
    assert run["status"] == "error" and "steps" in run["stop_reason"]


# ---------------------------------------------------------------- stops
def test_unsubscribe_and_bounce_stop_runs_before_any_message(c):
    nodes = [TRIGGER, node("w", "wait", days=1, hours=0), email_node()]
    flow = make_flow(c, nodes, chain("t", "w", "mail"))
    acme, beta = lead(c)["id"], lead(c, "Beta")["id"]
    enroll(c, flow, acme, beta)
    tick(c, T0)
    unsubscribe(c, acme)
    store_of(c).update("suppressed", lambda rows: [*rows, "sam@beta.in"])        # a bounce suppresses the address
    tick(c, T0 + timedelta(days=1, hours=1))
    assert flow_outbox(c) == []
    reasons = {r["lead_id"]: r["stop_reason"] for r in runs(c, flow)}
    assert "unsubscribed" in reasons[acme] and "do-not-contact" in reasons[beta]


def test_a_cold_flow_stops_when_the_lead_turns_warm(c):
    flow = make_flow(c, [TRIGGER, node("w", "wait", days=1, hours=0), email_node()], chain("t", "w", "mail"))
    acme = lead(c)["id"]
    enroll(c, flow, acme)
    tick(c, T0)
    patch_lead(c, acme, stage="trial")
    tick(c, T0 + timedelta(days=2))
    assert flow_outbox(c) == [] and "warm" in runs(c, flow)[0]["stop_reason"]


# ---------------------------------------------------------------- triggers
def test_reply_trigger_starts_the_yes_template(c):
    flow = c.post("/api/flows/templates/yes_whatsapp_call").json()
    c.post(f"/api/flows/{flow['id']}/activate")
    acme = lead(c)["id"]
    c.post(f"/api/leads/{acme}/reply", json={"text": "Yes please send it"})
    assert runs(c, flow)[0]["lead_id"] == acme
    tick(c, T0)
    wa = c.get("/api/whatsapp/outbox").json()
    assert [m["template"] for m in wa if m["lead_id"] == acme] == ["first_after_yes"]
    call_tasks = [t for t in store_of(c).read("tasks") if t["lead_id"] == acme and t["kind"] == "call" and t["status"] == "open"]
    assert len(call_tasks) == 1                                    # the flow reuses the yes-reply call task, no duplicate
    tick(c, T0 + timedelta(days=2, hours=1))
    notes = c.get("/api/notifications").json()
    assert any(n["title"].startswith("Flow Yes → WhatsApp + representative call") for n in notes)
    assert runs(c, flow)[0]["status"] == "done"


def test_stage_and_activation_triggers(c):
    stage_flow = make_flow(c, [node("t", "trigger.stage_changed", stage="not_now"), node("tag", "add_tag", tag="paused")],
                           chain("t", "tag"), name="On not now", stop_on_reply=False)
    product = make_flow(c, [node("t", "trigger.activation_event", event="role_published"), node("tag", "add_tag", tag="published")],
                        chain("t", "tag"), name="On publish", stop_on_reply=False)
    acme, beta = lead(c)["id"], lead(c, "Beta")["id"]
    c.post(f"/api/leads/{acme}/stage", json={"stage": "not_now"})
    c.post("/api/activation/event", json={"lead_id": beta, "event": "role_published"})
    assert [r["lead_id"] for r in runs(c, stage_flow)] == [acme]
    assert [r["lead_id"] for r in runs(c, product)] == [beta]


def test_lead_added_trigger_with_filters_and_only_new_leads(c):
    flow = make_flow(c, [node("t", "trigger.lead_added", source="sheet"), node("tag", "add_tag", tag="welcome")],
                     chain("t", "tag"))
    c.post("/api/leads/import", json={"csv": "company,email,website\nGamma,g@gamma.in,https://gamma.in\n", "source": "sheet"})
    c.post("/api/leads/import", json={"csv": "company,email,website\nDelta,d@delta.in,https://delta.in\n", "source": "apollo"})
    tick(c)
    started = {r["lead_id"] for r in runs(c, flow)}
    assert started == {lead(c, "Gamma")["id"]}                    # Acme / Beta were there before; Delta is from Apollo


def test_meeting_and_no_reply_triggers(c):
    meet = make_flow(c, [node("t", "trigger.meeting_booked"), node("tag", "add_tag", tag="met")], chain("t", "tag"), name="Met",
                     stop_on_reply=False)
    quiet = make_flow(c, [node("t", "trigger.no_reply_days", days=5), node("tag", "add_tag", tag="quiet")], chain("t", "tag"),
                      name="Quiet")
    acme, beta = lead(c)["id"], lead(c, "Beta")["id"]
    now_text = datetime.now(timezone.utc).isoformat(timespec="seconds")
    patch_lead(c, acme, meetings=[{"id": "m1", "status": "scheduled", "start": "2026-11-10T11:00:00+05:30", "created_at": now_text}])
    store_of(c).update("outbox", lambda rows: [*rows, {"lead_id": beta, "to": "sam@beta.in", "status": "dry_run",
                                                        "sent_at": T0.isoformat(), "sent_at_ist_date": "2026-11-03"}])
    tick(c, T0 + timedelta(days=4))
    assert [r["lead_id"] for r in runs(c, meet)] == [acme] and runs(c, quiet) == []
    tick(c, T0 + timedelta(days=5, minutes=1))
    assert [r["lead_id"] for r in runs(c, quiet)] == [beta]


# ---------------------------------------------------------------- autorun
def test_autorun_flow_tick_advances_runs(c):
    flow = make_flow(c, [TRIGGER, email_node()], chain("t", "mail"))
    enroll(c, flow, lead(c)["id"])
    sent = []
    result = autorun.flow_tick(store_of(c), T0, sender=lambda m: sent.append(m) or {"status": "dry_run"})
    assert result["emails"] == 1 and len(sent) == 1 and sent[0]["flow_id"] == flow["id"]


def test_the_minute_loop_calls_the_flow_tick(monkeypatch, c):
    called = threading.Event()
    monkeypatch.setattr(autorun, "REMINDER_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(autorun, "reminder_tick", lambda store: None)
    monkeypatch.setattr(autorun, "flow_tick", lambda store, sender=None: called.set())
    stop = autorun.start_background(store_of(c), sender=lambda m: {"status": "dry_run"}, interval_seconds=3600)
    try:
        assert called.wait(2)
    finally:
        stop.set()
