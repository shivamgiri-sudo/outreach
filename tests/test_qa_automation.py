"""Regressions found by the browser QA sweep of flows, campaigns, lists, tasks and LinkedIn (area: automation)."""
from app.flow_actions import custom_facts, evaluate


def test_if_on_a_custom_field_explains_itself_without_the_internal_prefix():
    facts = custom_facts({"custom": {"industry_vertical": "Healthcare", "monthly_hiring_budget": 80000}})
    ok, why = evaluate({"field": "custom:industry_vertical", "op": "eq", "value": "Real estate"}, facts)
    assert (ok, why) == (False, "industry vertical is Healthcare")
    ok, why = evaluate({"field": "custom:monthly_hiring_budget", "op": "gt", "value": "100000"}, facts)
    assert (ok, why) == (False, "monthly hiring budget is 80000")
    assert evaluate({"field": "open_roles", "op": "gt", "value": "3"}, {"open_roles": 8})[1] == "open roles is 8"


def test_a_daily_flow_switched_on_after_its_hour_waits_for_the_next_day(tmp_path):
    # QA sweep: a "daily at 10:00 IST" flow switched on at 17:30 started leads at 17:30 straight away.
    from datetime import timedelta
    from tests.flow_helpers import T0, chain, make_client, make_flow, node, store_of, tick
    c = make_client(tmp_path)
    flow = make_flow(c, [node("t", "trigger.schedule", when="daily", hour=10, limit=5), node("tag", "add_tag", tag="s")], chain("t", "tag"))
    on_at = T0 + timedelta(hours=7)                                          # Tuesday 17:30 IST
    store_of(c).update("flows", lambda fs: [{**f, "activated_at": on_at.isoformat()} for f in fs])
    started = lambda: [r for r in store_of(c).read("flow_runs") if r["flow_id"] == flow["id"]]
    tick(c, on_at + timedelta(minutes=5))
    assert started() == []
    tick(c, on_at + timedelta(hours=17))                                     # Wednesday 10:30 IST
    assert len(started()) >= 1


def test_simple_rule_task_and_note_fill_in_the_lead_details(tmp_path):
    # QA sweep: a rule "Send pricing to {{company}}" created a task that literally said {{company}}.
    from tests.flow_helpers import lead, make_client, store_of
    c = make_client(tmp_path)
    for name, action in (("pricing", {"type": "task", "value": "Send pricing to {{company}} ({{first_name}})"}),
                         ("log", {"type": "note", "value": "{{company}} is engaged; {{unknown}} stays"})):
        assert c.post("/api/workflows", json={"name": name, "trigger": {"type": "stage_change", "value": "engaged"}, "action": action}).status_code == 200
    who = lead(c)
    assert c.post(f"/api/leads/{who['id']}/stage", json={"stage": "engaged"}).status_code == 200
    first = who["contact_name"].split()[0]
    tasks = [t for t in store_of(c).read("tasks") if t.get("lead_id") == who["id"]]
    assert any(t["text"] == f"Send pricing to {who['company']} ({first})" for t in tasks)
    assert any(e["text"] == f"{who['company']} is engaged; {{{{unknown}}}} stays" for e in lead(c)["timeline"])
