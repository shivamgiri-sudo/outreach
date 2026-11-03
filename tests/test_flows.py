"""Flow builder: validation, templates, versioning, simulation and the API around flows."""
from datetime import timedelta

import pytest

from app.flow_templates import TEMPLATES
from app.flows import validate_graph
from tests.flow_helpers import (T0, chain, edge, email_node, enroll, flow_outbox, lead, make_client, make_flow, node,
                                runs, store_of, tick, timeline_texts)

TRIGGER = node("t", "trigger.manual")


def messages(problems, node_id=None):
    return [p["message"] for p in problems if node_id is None or p["node"] == node_id]


@pytest.fixture()
def c(tmp_path):
    return make_client(tmp_path)


# ---------------------------------------------------------------- validation
def test_missing_trigger_is_reported():
    problems = validate_graph([email_node()], [])
    assert any("Add a trigger" in m for m in messages(problems))


def test_two_triggers_and_unreachable_steps_are_reported():
    nodes = [TRIGGER, node("t2", "trigger.reply", label="interested"), email_node(), node("orphan", "add_tag", tag="x")]
    problems = validate_graph(nodes, chain("t", "mail"))
    assert messages(problems, "t2") and "one trigger" in messages(problems, "t2")[0]
    assert any("not connected to the trigger" in m for m in messages(problems, "orphan"))


def test_dangling_edge_is_refused_on_save(c):
    r = c.post("/api/flows", json={"name": "x", "nodes": [TRIGGER], "edges": [edge("t", "ghost")]})
    assert r.status_code == 400 and "dangling" in r.json()["detail"]


def test_unknown_step_type_is_refused(c):
    r = c.post("/api/flows", json={"name": "x", "nodes": [TRIGGER, node("n", "teleport")], "edges": []})
    assert r.status_code == 400 and "Unknown step type" in r.json()["detail"]


def test_loop_without_wait_is_refused_and_allowed_with_a_wait(c):
    check = node("check", "if", field="score", op="gt", value=50)
    nodes = [TRIGGER, check, node("tag", "add_tag", tag="looped")]
    edges = [edge("t", "check"), edge("check", "tag", "yes"), edge("tag", "check")]
    r = c.post("/api/flows", json={"name": "loop", "nodes": nodes, "edges": edges})
    assert r.status_code == 400 and "loop with no Wait" in r.json()["detail"]
    zero_wait = node("w", "wait", days=0, hours=0, business_hours=True)      # a zero-length wait does not count
    edges2 = [edge("t", "check"), edge("check", "tag", "yes"), edge("tag", "w"), edge("w", "check")]
    assert c.post("/api/flows", json={"name": "loop", "nodes": [*nodes, zero_wait], "edges": edges2}).status_code == 400
    real_wait = node("w", "wait", days=1, hours=0)
    r = c.post("/api/flows", json={"name": "loop", "nodes": [*nodes, real_wait], "edges": edges2})
    assert r.status_code == 200, r.text
    assert r.json()["errors"] == []


@pytest.mark.parametrize("bad,expected", [
    (node("n", "email", subject="Hi", body="No unsubscribe here"), "{{unsub_link}}"),
    (node("n", "email", subject="", body="x {{unsub_link}}"), "subject"),
    (node("n", "email", subject="Hi {{surname}}", body="x {{unsub_link}}"), "unknown placeholder"),
    (node("n", "if", field="city", op="gt", value="Pune"), "only work with numbers"),
    (node("n", "if", field="planet", op="eq", value="x"), "Pick what to check"),
    (node("n", "if", field="score", op="gt", value="lots"), "Type a number"),
    (node("n", "wait", days=0, hours=0), "how long to wait"),
    (node("n", "wait", days=90), "0–60"),
    (node("n", "wait_for_reply", days=0), "1 to 60 days"),
    (node("n", "split", percent_a=100), "between 1 and 99"),
    (node("n", "whatsapp", template="hello_free_text"), "approved WhatsApp template"),
    (node("n", "sms", text=""), "SMS text"),
    (node("n", "set_stage", stage="vip"), "stage"),
    (node("n", "add_tag", tag=""), "tag"),
    (node("n", "notify", message="hi", to="Nobody Known"), "not in your team"),
    (node("n", "webhook", url="http://hooks.example.com/x"), "https://"),
    (node("n", "webhook", url="https://127.0.0.1/hook"), "private or internal"),
    (node("n", "webhook", url="https://user:pw@hooks.example.com/x"), "username or password"),
    (node("n", "linkedin_task", mode="task", message=""), "LinkedIn message"),
])
def test_bad_step_settings_have_clear_messages(bad, expected):
    problems = validate_graph([TRIGGER, bad], chain("t", "n"), team_names={"Priya"})
    found = messages(problems, "n")
    assert any(expected.lower() in m.lower() for m in found), found


@pytest.mark.parametrize("trigger,expected", [
    (node("t", "trigger.reply", label="maybe"), "kind of reply"),
    (node("t", "trigger.stage_changed", stage="moon"), "stage"),
    (node("t", "trigger.activation_event", event="clicked"), "product activity"),
    (node("t", "trigger.no_reply_days", days=0), "1 to 90"),
])
def test_bad_trigger_settings_are_reported(trigger, expected):
    problems = validate_graph([trigger, node("n", "add_tag", tag="x")], chain("t", "n"))
    assert any(expected in m for m in messages(problems, "t"))


def test_edge_labels_must_match_the_step_outputs():
    nodes = [TRIGGER, node("check", "if", field="has_email", op="eq", value="true"), node("tag", "add_tag", tag="x")]
    problems = validate_graph(nodes, [edge("t", "check"), edge("check", "tag", "next")])
    assert any("has no 'next' output" in m for m in messages(problems, "check"))
    problems = validate_graph(nodes, [edge("t", "check"), edge("check", "tag", "yes"), edge("check", "tag", "yes")])
    assert any("connected twice" in m for m in messages(problems))


def test_activate_refuses_an_incomplete_flow(c):
    flow = c.post("/api/flows", json={"name": "Blank"}).json()
    assert flow["nodes"][0]["type"] == "trigger.manual" and flow["status"] == "draft"
    r = c.post(f"/api/flows/{flow['id']}/activate")
    assert r.status_code == 400 and "Connect the trigger" in r.json()["detail"]


def test_validate_endpoint_checks_unsaved_edits(c):
    flow = c.post("/api/flows", json={"name": "Blank"}).json()
    nodes = [*flow["nodes"], node("mail", "email", subject="", body="hi")]
    r = c.post(f"/api/flows/{flow['id']}/validate", json={"nodes": nodes, "edges": [edge("trigger", "mail")]}).json()
    assert r["ok"] is False
    assert {p["node"] for p in r["errors"]} == {"mail"}


# ---------------------------------------------------------------- templates
@pytest.mark.parametrize("key", list(TEMPLATES))
def test_templates_produce_valid_flows_that_activate(c, key):
    flow = c.post(f"/api/flows/templates/{key}").json()
    assert flow["status"] == "draft" and flow["errors"] == [], flow["errors"]
    assert c.post(f"/api/flows/{flow['id']}/activate").status_code == 200


def test_cold_email_template_matches_the_sequence_timing(c):
    flow = c.post("/api/flows/templates/cold_email").json()
    types = [n["type"] for n in flow["nodes"]]
    assert types.count("email") == 4
    assert [n["config"]["days"] for n in flow["nodes"] if n["type"] == "wait"] == [3, 4, 7]
    assert flow["settings"]["stop_on_reply"] is True
    assert c.post("/api/flows/templates/nope").status_code == 404


def test_cold_template_sends_four_emails_on_days_0_3_7_14(c):
    flow = c.post("/api/flows/templates/cold_email").json()
    c.post(f"/api/flows/{flow['id']}/activate")
    acme = lead(c)["id"]
    assert enroll(c, flow, acme)["enrolled"] == [acme]
    for day in (0, 3, 7, 14):
        tick(c, T0 + timedelta(days=day))
    sent = sorted((m for m in flow_outbox(c) if m["lead_id"] == acme), key=lambda m: m["sent_at"])
    assert [m["sent_at"][:10] for m in sent] == ["2026-11-03", "2026-11-06", "2026-11-10", "2026-11-17"]
    assert all(m["status"] == "dry_run" and "/u/" in m["body"] for m in sent)
    assert runs(c, flow)[0]["status"] == "done"


# ---------------------------------------------------------------- versioning
def test_editing_an_active_flow_makes_a_new_version_and_running_leads_finish_on_theirs(c):
    nodes = [TRIGGER, node("w", "wait", days=1, hours=0), email_node(subject="Version one for {{company}}")]
    flow = make_flow(c, nodes, chain("t", "w", "mail"))
    acme, beta = lead(c)["id"], lead(c, "Beta")["id"]
    enroll(c, flow, acme)
    tick(c, T0)
    moved = [{**n, "x": 500} for n in nodes]
    assert c.put(f"/api/flows/{flow['id']}", json={"nodes": moved, "edges": chain("t", "w", "mail")}).json()["version"] == 1
    edited = [TRIGGER, node("w", "wait", days=1, hours=0), email_node(subject="Version two for {{company}}")]
    updated = c.put(f"/api/flows/{flow['id']}", json={"nodes": edited, "edges": chain("t", "w", "mail")}).json()
    assert updated["version"] == 2 and updated["status"] == "active"
    enroll(c, flow, beta)
    tick(c, T0 + timedelta(days=1, hours=1))          # Acme's wait (version 1) is over; Beta starts waiting (version 2)
    tick(c, T0 + timedelta(days=2, hours=2))
    subjects = {m["lead_id"]: m["subject"] for m in flow_outbox(c)}
    assert subjects == {acme: "Version one for Acme", beta: "Version two for Beta"}
    assert {r["lead_id"]: r["flow_version"] for r in runs(c, flow)} == {acme: 1, beta: 2}


def test_an_active_flow_cannot_be_saved_broken(c):
    flow = make_flow(c, [TRIGGER, email_node()], chain("t", "mail"))
    r = c.put(f"/api/flows/{flow['id']}", json={"nodes": [TRIGGER, email_node(body="no unsubscribe")], "edges": chain("t", "mail")})
    assert r.status_code == 400 and "Pause it" in r.json()["detail"]
    c.post(f"/api/flows/{flow['id']}/pause")
    r = c.put(f"/api/flows/{flow['id']}", json={"nodes": [TRIGGER, email_node(body="no unsubscribe")], "edges": chain("t", "mail")})
    assert r.status_code == 200 and r.json()["errors"]


# ---------------------------------------------------------------- simulation
def test_simulate_shows_the_path_and_writes_nothing(c, tmp_path):
    nodes = [TRIGGER, node("check", "if", field="open_roles", op="gt", value=5), email_node(),
             node("reply", "wait_for_reply", days=3), node("yes", "add_tag", tag="said-yes"), node("cold", "add_tag", tag="cold")]
    edges = [edge("t", "check"), edge("check", "mail", "yes"), edge("check", "cold", "no"), edge("mail", "reply"),
             edge("reply", "yes", "interested")]
    flow = make_flow(c, nodes, edges)
    before = (tmp_path / "d.json").read_bytes()
    acme, beta = lead(c)["id"], lead(c, "Beta")["id"]
    result = c.post(f"/api/flows/{flow['id']}/simulate", json={"lead_id": acme}).json()
    assert [p["node"] for p in result["path"]] == ["t", "check", "mail", "reply"]
    assert result["path"][1]["label"] == "yes" and "open roles is 12" in result["path"][1]["result"]
    assert "practice only" in result["path"][2]["result"]
    yes = c.post(f"/api/flows/{flow['id']}/simulate", json={"lead_id": acme, "assume_reply": "interested"}).json()
    assert [p["node"] for p in yes["path"]][-1] == "yes"
    other = c.post(f"/api/flows/{flow['id']}/simulate", json={"lead_id": beta}).json()
    assert [p["node"] for p in other["path"]] == ["t", "check", "cold"]
    assert (tmp_path / "d.json").read_bytes() == before
    assert c.post(f"/api/flows/{flow['id']}/simulate", json={"lead_id": "nope"}).status_code == 404


def test_simulate_reports_refusals_for_closed_leads_and_missing_consent(c):
    flow = make_flow(c, [TRIGGER, node("wa", "whatsapp", template="first_after_yes")], chain("t", "wa"))
    acme = lead(c)["id"]
    path = c.post(f"/api/flows/{flow['id']}/simulate", json={"lead_id": acme}).json()["path"]
    assert path[-1]["ok"] is False and "no recorded consent" in path[-1]["result"]
    c.post(f"/u/{acme}")
    result = c.post(f"/api/flows/{flow['id']}/simulate", json={"lead_id": acme}).json()
    assert "Would not start" in result["ended"]


# ---------------------------------------------------------------- API around runs
def test_lead_panel_lists_runs_and_a_run_can_be_stopped(c):
    flow = make_flow(c, [TRIGGER, node("w", "wait", days=2, hours=0), email_node()], chain("t", "w", "mail"), name="Nurture")
    acme = lead(c)["id"]
    enroll(c, flow, acme)
    tick(c, T0)
    shown = c.get(f"/api/leads/{acme}/flows").json()
    assert shown[0]["flow_name"] == "Nurture" and shown[0]["current_title"] == "Wait" and shown[0]["status"] == "waiting"
    stopped = c.post(f"/api/flow-runs/{shown[0]['id']}/stop").json()
    assert stopped["status"] == "stopped"
    tick(c, T0 + timedelta(days=3))
    assert flow_outbox(c) == []
    assert any(t.startswith("Flow Nurture: stopped — stopped by a person") for t in timeline_texts(c, acme))
    assert c.post("/api/flow-runs/missing/stop").status_code == 404


def test_list_catalog_and_delete(c):
    flow = make_flow(c, [TRIGGER, node("w", "wait", days=2, hours=0)], chain("t", "w"), name="Short")
    enroll(c, flow, lead(c)["id"])
    listed = c.get("/api/flows").json()
    assert listed[0]["name"] == "Short" and listed[0]["stats"]["active_leads"] == 1 and listed[0]["status"] == "active"
    catalog = c.get("/api/flows/catalog").json()
    assert "trigger.reply" in catalog["node_types"] and "first_after_yes" in catalog["whatsapp_templates"]
    assert catalog["node_types"]["if"]["outputs"] == ["yes", "no"]
    gone = c.delete(f"/api/flows/{flow['id']}").json()
    assert gone["runs_stopped"] == 1
    assert c.get(f"/api/flows/{flow['id']}").status_code == 404
    assert all(r["status"] == "stopped" for r in store_of(c).read("flow_runs"))
