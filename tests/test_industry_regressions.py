"""Focused regression tests for the bugs the industry scenarios found, plus the cross-industry safety rules."""
from datetime import timedelta

from tests.flow_helpers import edge, node
from tests.scenario_helpers import csv_of, ist, ist_text, run_path, world_factory  # noqa: F401

SIGN_OFF = "\n\nUnsubscribe: {{unsub_link}}"
LEADS = csv_of([
    ("Skyline Realty", "Neha Kapoor", "Sales Director", "neha@skylinerealty.in", "98111 22334", "https://skylinerealty.in", "Pune", "IN", 8, 120, "Sales Executive"),
    ("Ledgerly", "Manish Jain", "Founder", "manish@ledgerly.in", "98330 44556", "https://ledgerly.in", "Mumbai", "IN", 6, 45, "Customer Success Manager"),
    ("Vidya Coaching Centre", "Harish Kumar", "Founder", "harish@vidyacoaching.in", "", "https://vidyacoaching.in", "Patna", "IN", 4, 35, "Physics Faculty"),
])


def mail(nid, subject="Hello {{company}}", body="{{greeting}} A note for {{company}}." + SIGN_OFF):
    return node(nid, "email", subject=subject, body=body)


def errors_of(w, nodes, edges):
    r = w.c.post("/api/flows", json={"name": "check", "nodes": nodes, "edges": edges})
    return r.status_code, (r.json().get("errors") if r.status_code == 200 else r.json()["detail"])


# ---------------------------------------------------------------- bugs found (each fails on the code before the fix)
def test_cold_flow_runs_the_interested_branch_it_waited_for(world_factory):
    """Bug: the yes reply made the lead warm, and the cold-flow warm check stopped the run before the branch's step."""
    w = world_factory(ist(3, 10, 0), LEADS)
    flow = w.flow("cold", [node("t", "trigger.manual"), mail("m"), node("wr", "wait_for_reply", days=3),
                           node("tag", "add_tag", tag="said-yes")],
                  [edge("t", "m"), edge("m", "wr"), edge("wr", "tag", "interested")])
    w.enroll(flow, "Ledgerly")
    w.tick(ist(3, 10, 0))
    w.reply("Ledgerly", "Yes, interested.")
    w.tick(ist(3, 11, 0))
    run = w.runs(flow)["Ledgerly"]
    assert run["status"] == "done" and w.full("Ledgerly")["tags"] == ["said-yes"]


def test_cold_flow_still_stops_when_someone_else_makes_the_lead_warm(world_factory):
    w = world_factory(ist(3, 10, 0), LEADS)
    flow = w.flow("cold", [node("t", "trigger.manual"), mail("m"), node("w", "wait", days=2), mail("m2", subject="Again {{company}}")],
                  [edge("t", "m"), edge("m", "w"), edge("w", "m2")])
    w.enroll(flow, "Ledgerly")
    w.tick(ist(3, 10, 0))
    w.consent("Ledgerly")
    assert w.c.post(f"/api/leads/{w.id('Ledgerly')}/stage", json={"stage": "interested"}).status_code == 200
    w.tick(ist(5, 10, 0))
    run = w.runs(flow)["Ledgerly"]
    assert run["status"] == "stopped" and run["stop_reason"].startswith("the lead is warm") and len(w.emails()) == 1


def test_reply_triggered_flow_with_default_settings_runs_for_the_warm_lead(world_factory):
    """Bug: a 'they replied yes' flow keeps the default 'stop on reply' and was stopped at once (the lead is warm)."""
    w = world_factory(ist(3, 10, 0), LEADS)
    flow = w.flow("yes", [node("t", "trigger.reply", label="interested"), node("call", "call_task", note="Call {{first_name}}.")],
                  [edge("t", "call")], stop_on_reply=True)
    w.reply("Skyline Realty", "Yes please, interested.")
    w.tick(ist(3, 10, 5))
    assert w.runs(flow)["Skyline Realty"]["status"] == "done"
    assert any(t["kind"] == "call" and "Call Neha." in t["text"] for t in w.tasks("Skyline Realty"))


def test_a_flow_that_moves_the_lead_to_interested_keeps_going(world_factory):
    w = world_factory(ist(3, 10, 0), LEADS)
    w.consent("Ledgerly")
    flow = w.flow("promote", [node("t", "trigger.manual"), node("s", "set_stage", stage="interested"), node("tag", "add_tag", tag="promoted")],
                  [edge("t", "s"), edge("s", "tag")])
    w.enroll(flow, "Ledgerly")
    w.tick(ist(3, 10, 0))
    assert w.runs(flow)["Ledgerly"]["status"] == "done" and w.full("Ledgerly")["tags"] == ["promoted"]


def test_flow_whatsapp_and_sms_wait_for_business_hours(world_factory):
    """Bug: email waited for Mon-Fri 9-18 IST but a flow's WhatsApp / SMS went out at night and on Sundays."""
    w = world_factory(ist(7, 22, 0), LEADS)                      # Saturday 22:00 IST
    w.consent("Skyline Realty")
    r = w.c.post(f"/api/leads/{w.id('Skyline Realty')}/consent", json={"text": "Yes to SMS too", "channel": "sms"})   # SMS needs its own yes
    assert r.status_code == 200
    flow = w.flow("wa", [node("t", "trigger.manual"), node("wa", "whatsapp", template="pilot_offer"),
                         node("sms", "sms", text="{{greeting}} {{sender}} here. Reply STOP to opt out.")],
                  [edge("t", "wa"), edge("wa", "sms")])
    w.enroll(flow, "Skyline Realty")
    w.tick(ist(7, 22, 0))
    w.tick(ist(8, 12, 0))
    assert w.whatsapps() == [] and w.runs(flow)["Skyline Realty"]["status"] == "waiting"
    w.tick(ist(9, 9, 0))
    assert [ist_text(m["sent_at"]) for m in w.whatsapps()] == ["Mon 09 Nov 09:00"]
    sms = [a for a in w.store.read("activities") if a.get("type") == "sms"]
    assert len(sms) == 1 and sms[0]["at"].startswith("2026-11-09T03:30") and sms[0]["body"].startswith("Hi Neha,")


def test_team_notes_fill_placeholders_and_unknown_ones_are_refused(world_factory):
    """Bug: notify / call / meeting notes kept {{company}} literally (the AI triage template's notify showed it)."""
    w = world_factory(ist(3, 10, 0), LEADS)
    flow = w.flow("notes", [node("t", "trigger.manual"), node("n", "notify", message="Hot lead {{company}} ({{first_name}}, {{role}})"),
                            node("c", "call_task", note="Call {{first_name}} at {{company}}"),
                            node("mt", "meeting_link", note="Demo for {{company}}")],
                  [edge("t", "n"), edge("n", "c"), edge("c", "mt")])
    w.enroll(flow, "Ledgerly")
    w.tick(ist(3, 10, 0))
    assert [n["text"] for n in w.notifications("Ledgerly") if n["kind"] == "flow"] == ["Hot lead Ledgerly (Manish, customer success manager)"]
    texts = {t["kind"]: t["text"] for t in w.tasks("Ledgerly")}
    assert texts["call"] == "Flow notes: Call Manish at Ledgerly" and texts["meeting"].endswith("Demo for Ledgerly")
    _, errors = errors_of(w, [node("t", "trigger.manual"), node("n", "notify", message="Call {{phone}} now")], [edge("t", "n")])
    assert any("unknown placeholder(s): {{phone}}" in e["message"] for e in errors)
    _, errors = errors_of(w, [node("t", "trigger.manual"), node("c", "call_task", note="Ask about {{budget}}")], [edge("t", "c")])
    assert any("{{budget}}" in e["message"] for e in errors)


def test_set_fields_value_with_an_unknown_placeholder_is_refused(world_factory):
    """Bug: Set fields accepted {{city}}; the field then carried '{{city}}' and every later email was refused."""
    w = world_factory(ist(3, 10, 0), LEADS)
    nodes = [node("t", "trigger.manual"), node("sv", "set_vars", values=[{"name": "town", "value": "{{city}}"}]),
             mail("m", body="{{greeting}} in {{town}}." + SIGN_OFF)]
    _, errors = errors_of(w, nodes, [edge("t", "sv"), edge("sv", "m")])
    assert [e["node"] for e in errors] == ["sv"] and "{{city}}" in errors[0]["message"]
    ok = [node("t", "trigger.manual"), node("sv", "set_vars", values=[{"name": "a", "value": "{{company}}"}, {"name": "b", "value": "for {{a}}"}])]
    assert errors_of(w, ok, [edge("t", "sv")])[1] == []        # earlier fields can be used in later ones


def test_simulation_uses_fields_walks_repeats_and_applies_the_assumed_reply(world_factory):
    """Bugs: the dry run ignored Set fields in later emails (said 'would not email'), skipped Repeat rounds, and an
    assumed yes did not give the consent a real yes gives (so it predicted 'no WhatsApp')."""
    w = world_factory(ist(3, 10, 0), LEADS)
    w.consent("Ledgerly")                       # consent is per channel: the assumed email yes alone would not open WhatsApp
    flow = w.flow("sim", [node("t", "trigger.manual"), node("sv", "set_vars", values=[{"name": "pitch", "value": "an offer for {{company}}"}]),
                          mail("m", body="{{greeting}} {{pitch}}." + SIGN_OFF), node("lp", "loop", times=2), node("w", "wait", hours=4),
                          node("wr", "wait_for_reply", days=2), node("wa", "whatsapp", template="first_after_yes")],
                  [edge("t", "sv"), edge("sv", "m"), edge("m", "lp"), edge("lp", "w", "again"), edge("w", "lp"), edge("lp", "wr", "done"),
                   edge("wr", "wa", "interested")])
    sim = w.c.post(f"/api/flows/{flow['id']}/simulate", json={"lead_id": w.id("Ledgerly"), "assume_reply": "interested"}).json()
    assert [p["node"] for p in sim["path"][1:]] == ["sv", "m", "lp", "w", "lp", "w", "lp", "wr", "wa"]
    assert sim["path"][2]["result"].startswith("would email “Hello Ledgerly”") and sim["path"][-1]["ok"]
    assert sim["ended"] == "Reaches the end of the flow."
    w.enroll(flow, "Ledgerly")
    w.run_until(ist(3, 19, 0))
    w.reply("Ledgerly", "Yes, interested")
    w.run_until(ist(4, 10, 0))
    assert run_path(w.runs(flow)["Ledgerly"]) == [p["node"] for p in sim["path"][1:]]


def test_a_reply_that_changes_the_stage_starts_stage_changed_flows(world_factory):
    """Bug: replies moved leads to Engaged / Not now / Interested without telling 'stage changes' flows and rules."""
    w = world_factory(ist(3, 10, 0), LEADS)
    flow = w.flow("engaged", [node("t", "trigger.stage_changed", stage="engaged"), node("tag", "add_tag", tag="engaged-flow")],
                  [edge("t", "tag")], stop_on_reply=False)
    rule = w.c.post("/api/workflows", json={"name": "not now → note", "trigger": {"type": "stage_change", "value": "not_now"},
                                             "action": {"type": "note", "value": "Parked after a not-now reply"}})
    assert rule.status_code == 200
    w.reply("Ledgerly", "What does it cost?")
    w.reply("Skyline Realty", "Not right now, maybe next quarter.")
    w.tick(ist(3, 10, 5))
    assert w.full("Ledgerly")["tags"] == ["engaged-flow"] and "Skyline Realty" not in w.runs(flow)
    assert "Parked after a not-now reply" in w.timeline("Skyline Realty")


def test_a_turned_off_wait_does_not_count_as_the_wait_a_loop_needs(world_factory):
    """Bug: turning the loop's Wait off passed the 'loop needs a wait' check, so a lead could spin with no pause."""
    w = world_factory(ist(3, 10, 0), LEADS)
    nodes = [node("t", "trigger.manual"), node("lp", "loop", times=3), node("w", "wait", days=1, disabled=True),
             node("n", "notify", message="again")]
    status, detail = errors_of(w, nodes, [edge("t", "lp"), edge("lp", "w", "again"), edge("w", "n"), edge("n", "lp")])
    assert status == 400 and "loop with no Wait step" in detail


def test_flow_call_task_joins_the_open_call_task_with_the_chosen_owner(world_factory):
    """Bug: a yes reply opens a call task for the caller; the flow's call step for its chosen owner was silently
    dropped (one open call task per lead) while the run said the owner had a task."""
    w = world_factory(ist(3, 10, 0), LEADS)
    w.team("Priya Nair")
    flow = w.flow("yes", [node("t", "trigger.reply", label="interested"), node("c", "call_task", owner="Priya Nair", note="Pricing call with {{first_name}}")],
                  [edge("t", "c")], stop_on_reply=False)
    w.reply("Ledgerly", "Yes, interested")
    w.tick(ist(3, 10, 5))
    calls = [t for t in w.tasks("Ledgerly") if t["kind"] == "call"]
    assert len(calls) == 1 and calls[0]["owner"] == "Priya Nair" and calls[0]["text"].endswith("Flow yes: Pricing call with Manish")
    assert w.runs(flow)["Ledgerly"]["history"][-1]["result"].startswith("call task for Priya Nair (added to the open call task)")


def test_campaign_auto_enrol_uses_the_chosen_sequence(world_factory):
    """Gap: campaigns always started the built-in sequence; the wizard now picks a saved one and the server checks it."""
    w = world_factory(ist(3, 9, 0), LEADS)
    seq = w.c.post("/api/sequences", json={"name": "Realty", "steps": [{"day": 0, "name": "One", "subject": "Hi {{company}}",
                                                                        "body": "{{greeting}} hello." + SIGN_OFF}]}).json()
    camp = w.c.post("/api/campaigns", json={"name": "c", "sources": {"csv_import_ids": [w.id("Skyline Realty")]}, "enrich": {"crawler": False},
                                            "rules": {"min_score": 0, "auto_enrol": True, "sequence_id": seq["id"]}}).json()
    assert camp["rules"]["sequence_id"] == seq["id"]
    report = w.c.post(f"/api/campaigns/{camp['id']}/run", json={}).json()
    assert report["enrolled"] == 1 and w.full("Skyline Realty")["sequence"]["sequence_id"] == seq["id"]
    w.c.post(f"/api/sequences/{seq['id']}/archive", json={"archived": True})
    r = w.c.put(f"/api/campaigns/{camp['id']}", json={"rules": {"sequence_id": seq["id"]}})
    assert r.status_code == 400
    assert w.c.put(f"/api/campaigns/{camp['id']}", json={"rules": {"min_score": 5}}).status_code == 200   # other edits still save


# ---------------------------------------------------------------- safety rules across industries
def test_unsubscribe_stops_email_and_whatsapp_in_a_multichannel_flow(world_factory):
    w = world_factory(ist(3, 10, 0), LEADS)
    w.consent("Skyline Realty")
    flow = w.flow("multi", [node("t", "trigger.manual"), mail("m"), node("w", "wait", days=1), node("wa", "whatsapp", template="pilot_offer"),
                            mail("m2", subject="Again {{company}}")], [edge("t", "m"), edge("m", "w"), edge("w", "wa"), edge("wa", "m2")])
    w.enroll(flow, "Skyline Realty")
    w.tick(ist(3, 10, 0))
    w.unsubscribe("Skyline Realty")
    w.run_until(ist(6, 12, 0), every=timedelta(hours=3))
    run = w.runs(flow)["Skyline Realty"]
    assert run["status"] == "stopped" and "unsubscribed" in run["stop_reason"]
    assert len(w.emails()) == 1 and w.whatsapps() == [] and not w.full("Skyline Realty").get("consent")
    again = w.enroll(flow, "Skyline Realty")["refused"][0]["reason"]
    assert again == "Not started: lead is unsubscribed."


def test_a_reply_stops_a_cold_flow_and_out_of_office_does_not(world_factory):
    w = world_factory(ist(3, 10, 0), LEADS)
    flow = w.flow("cold", [node("t", "trigger.manual"), mail("m"), node("w", "wait", days=2), mail("m2", subject="Again {{company}}")],
                  [edge("t", "m"), edge("m", "w"), edge("w", "m2")])
    w.enroll(flow, "Ledgerly", "Skyline Realty")
    w.tick(ist(3, 10, 0))
    w.reply("Ledgerly", "Automatic reply: I am out of office until Monday")
    w.reply("Skyline Realty", "Please send more details on pricing")
    w.run_until(ist(5, 12, 0), every=timedelta(hours=2))
    runs = w.runs(flow)
    assert runs["Skyline Realty"]["status"] == "stopped" and runs["Skyline Realty"]["stop_reason"].startswith("they replied")
    assert runs["Ledgerly"]["status"] == "done" and len(w.emails("Ledgerly")) == 2 and len(w.emails("Skyline Realty")) == 1


def test_no_whatsapp_without_consent_error_path_or_stop(world_factory):
    w = world_factory(ist(3, 10, 0), LEADS)
    with_error = w.flow("wa-err", [node("t", "trigger.manual"), node("wa", "whatsapp", template="pilot_offer"), node("li", "linkedin_task", message="Hi {{first_name}}")],
                        [edge("t", "wa"), edge("wa", "li", "error")])
    no_error = w.flow("wa-stop", [node("t", "trigger.manual"), node("wa", "whatsapp", template="pilot_offer"), node("tag", "add_tag", tag="x")],
                      [edge("t", "wa"), edge("wa", "tag")])
    w.enroll(with_error, "Ledgerly", "Vidya Coaching Centre")
    w.enroll(no_error, "Ledgerly")
    w.tick(ist(3, 10, 0))
    assert w.whatsapps() == []
    a = w.runs(with_error)
    assert a["Ledgerly"]["status"] == "done" and "No recorded consent" in a["Ledgerly"]["history"][1]["result"]
    assert [t["text"] for t in w.tasks("Ledgerly") if t["kind"] == "linkedin"] == ["Hi Manish"]
    assert a["Vidya Coaching Centre"]["status"] == "done"
    b = w.runs(no_error)["Ledgerly"]
    assert b["status"] == "stopped" and "No recorded consent" in b["stop_reason"] and w.full("Ledgerly").get("tags") in (None, [])
    assert w.simulate(no_error, "Ledgerly") == ["wa"] == run_path(b)


def test_a_suppressed_phone_number_stops_the_run(world_factory):
    w = world_factory(ist(3, 10, 0), LEADS)
    w.consent("Skyline Realty")
    flow = w.flow("wa", [node("t", "trigger.manual"), node("w", "wait", hours=1), node("wa", "whatsapp", template="pilot_offer")],
                  [edge("t", "w"), edge("w", "wa")])
    w.enroll(flow, "Skyline Realty")
    w.tick(ist(3, 10, 0))
    w.store.update("suppressed", lambda rows: [*rows, w.full("Skyline Realty")["phone"]])
    w.tick(ist(3, 11, 30))
    run = w.runs(flow)["Skyline Realty"]
    assert run["status"] == "stopped" and "do-not-contact" in run["stop_reason"] and w.whatsapps() == []


def test_never_enrolled_twice_even_when_the_trigger_fires_again(world_factory):
    w = world_factory(ist(3, 10, 0), LEADS)
    flow = w.flow("q", [node("t", "trigger.reply", label="question"), node("tag", "add_tag", tag="q")], [edge("t", "tag")], stop_on_reply=False)
    w.reply("Ledgerly", "How much does it cost?")
    w.reply("Ledgerly", "Also, which cities do you cover?")
    assert len([r for r in w.c.get(f"/api/flows/{flow['id']}/runs").json()]) == 1


def test_loops_need_a_real_wait(world_factory):
    w = world_factory(ist(3, 10, 0), LEADS)
    base = [node("t", "trigger.manual"), node("lp", "loop", times=3), node("n", "notify", message="again")]
    status, detail = errors_of(w, base, [edge("t", "lp"), edge("lp", "n", "again"), edge("n", "lp")])
    assert status == 400 and "loop with no Wait step" in detail
    hours_only = [*base, node("w", "wait", days=0, hours=0, business_hours=True)]
    status, detail = errors_of(w, hours_only, [edge("t", "lp"), edge("lp", "w", "again"), edge("w", "n"), edge("n", "lp")])
    assert status == 400 and "loop with no Wait step" in detail
    ok = [*base, node("w", "wait", days=1)]
    assert errors_of(w, ok, [edge("t", "lp"), edge("lp", "w", "again"), edge("w", "n"), edge("n", "lp")])[1] == []


def test_email_step_for_a_lead_without_email_takes_the_error_path(world_factory):
    w = world_factory(ist(3, 10, 0), LEADS)
    w.store.update_lead(w.id("Vidya Coaching Centre"), lambda l: {**l, "email": None})
    flow = w.flow("e", [node("t", "trigger.manual"), mail("m"), node("call", "call_task", note="No email: call {{company}}")],
                  [edge("t", "m"), edge("m", "call", "error")])
    w.enroll(flow, "Vidya Coaching Centre")
    w.tick(ist(3, 10, 0))
    assert w.emails() == [] and [t["text"] for t in w.tasks("Vidya Coaching Centre") if t["kind"] == "call"] == [
        "Flow e: No email: call Vidya Coaching Centre"]
