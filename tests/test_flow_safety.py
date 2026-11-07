"""Flow safety: no message to closed or suppressed leads, consent for WhatsApp/SMS, every email rule, stage rules, webhooks."""
from datetime import datetime, timedelta, timezone

import pytest

from tests.flow_helpers import (unsubscribe, T0, FakeTransport, chain, connect_email, edge, email_node, enroll, flow_outbox, go_live, lead,
                                make_client, make_flow, node, patch_lead, runs, store_of, tick, timeline_texts)

TRIGGER = node("t", "trigger.manual")
IST = timezone(timedelta(hours=5, minutes=30))
MESSAGE_NODES = {
    "email": email_node("msg"),
    "whatsapp": node("msg", "whatsapp", template="first_after_yes"),
    "sms": node("msg", "sms", text="Hi {{first_name}}, sample screen: {{demo_link}} Reply STOP to opt out."),
}


@pytest.fixture()
def c(tmp_path):
    return make_client(tmp_path)


def consent(c, lead_id):
    for channel in ("whatsapp", "sms"):                 # consent is per channel: WhatsApp and SMS each need a yes
        c.post(f"/api/leads/{lead_id}/consent", json={"text": f"Yes to {channel} on a call with Priya", "channel": channel})


def sent_anything(c, lead_id):
    store = store_of(c)
    return ([m for m in store.read("outbox") if m["lead_id"] == lead_id] + [m for m in store.read("whatsapp_outbox") if m["lead_id"] == lead_id]
            + [a for a in store.read("activities") if a["lead_id"] == lead_id and a["type"] == "sms"])


# ---------------------------------------------------------------- closed and suppressed leads, every channel
@pytest.mark.parametrize("channel", list(MESSAGE_NODES))
def test_unsubscribed_or_suppressed_leads_never_get_a_flow_message(c, channel):
    flow = make_flow(c, [TRIGGER, node("w", "wait", days=1, hours=0), MESSAGE_NODES[channel]], chain("t", "w", "msg"))
    acme, beta = lead(c)["id"], lead(c, "Beta")["id"]
    consent(c, acme)
    consent(c, beta)
    enroll(c, flow, acme, beta)
    tick(c, T0)
    unsubscribe(c, acme)                                                # unsubscribes mid-flow
    store_of(c).update("suppressed", lambda rows: [*rows, "sam@beta.in"])
    tick(c, T0 + timedelta(days=1, hours=1))
    assert sent_anything(c, acme) == [] and sent_anything(c, beta) == []
    assert {r["status"] for r in runs(c, flow)} == {"stopped"}
    refused = enroll(c, make_flow(c, [TRIGGER, MESSAGE_NODES[channel]], chain("t", "msg"), name="Again"), acme, beta)["refused"]
    assert len(refused) == 2 and all(r["reason"].startswith("Not started") for r in refused)


# ---------------------------------------------------------------- WhatsApp
def test_whatsapp_without_consent_is_refused_and_follows_the_error_path(c):
    nodes = [TRIGGER, node("wa", "whatsapp", template="first_after_yes"), node("call", "call_task", note="Ask for consent first")]
    flow = make_flow(c, nodes, [edge("t", "wa"), edge("wa", "call", "error")])
    acme = lead(c)["id"]
    enroll(c, flow, acme)
    tick(c)
    assert c.get("/api/whatsapp/outbox").json() == []
    assert any("Send WhatsApp — WhatsApp not sent: No recorded consent" in t for t in timeline_texts(c, acme))
    assert any(t["kind"] == "call" and "Ask for consent first" in t["text"] for t in store_of(c).read("tasks"))


def test_whatsapp_refusal_without_an_error_path_stops_with_a_note(c):
    flow = make_flow(c, [TRIGGER, node("wa", "whatsapp", template="first_after_yes")], chain("t", "wa"))
    acme = lead(c)["id"]
    enroll(c, flow, acme)
    tick(c)
    run = runs(c, flow)[0]
    assert run["status"] == "stopped" and "No recorded consent" in run["stop_reason"]
    assert any(t.startswith("Flow Test flow: stopped — WhatsApp not sent") for t in timeline_texts(c, acme))


def test_whatsapp_with_consent_goes_through_the_practice_outbox(c):
    flow = make_flow(c, [TRIGGER, node("wa", "whatsapp", template="first_after_yes")], chain("t", "wa"))
    acme = lead(c)["id"]
    consent(c, acme)
    enroll(c, flow, acme)
    tick(c)
    wa = c.get("/api/whatsapp/outbox").json()
    assert len(wa) == 1 and wa[0]["status"] == "dry_run" and "Reply STOP" in wa[0]["body"]
    assert any("WhatsApp first_after_yes — practice only" in t for t in timeline_texts(c, acme))


# ---------------------------------------------------------------- SMS
def test_sms_needs_consent_and_stays_practice_until_connected(c):
    flow = make_flow(c, [TRIGGER, MESSAGE_NODES["sms"]], chain("t", "msg"))
    acme, beta = lead(c)["id"], lead(c, "Beta")["id"]
    consent(c, acme)
    enroll(c, flow, acme, beta)
    tick(c)
    rows = [a for a in store_of(c).read("activities") if a["type"] == "sms"]
    assert [(r["lead_id"], r["outcome"]) for r in rows] == [(acme, "dry_run")]
    assert "no recorded SMS consent" in {r["lead_id"]: r["stop_reason"] for r in runs(c, flow)}[beta]


# ---------------------------------------------------------------- email rules
def test_email_waits_for_business_hours(c):
    flow = make_flow(c, [TRIGGER, email_node()], chain("t", "mail"))
    enroll(c, flow, lead(c)["id"])
    saturday = datetime(2026, 11, 7, 11, 0, tzinfo=IST)
    tick(c, saturday)
    run = runs(c, flow)[0]
    assert flow_outbox(c) == [] and "business hours" in run["deferred"]
    assert datetime.fromisoformat(run["wait_until"]) == datetime(2026, 11, 9, 9, 0, tzinfo=IST)
    tick(c, datetime(2026, 11, 9, 9, 5, tzinfo=IST))
    assert len(flow_outbox(c)) == 1


def test_email_daily_cap_is_shared_with_the_sequence(c):
    c.put("/api/settings", json={"daily_cap": 1})
    flow = make_flow(c, [TRIGGER, email_node()], chain("t", "mail"))
    acme, beta = lead(c)["id"], lead(c, "Beta")["id"]
    c.post(f"/api/leads/{beta}/enroll")
    c.post("/api/sequences/run", json={"now": T0.isoformat()})            # the sequence uses today's only send
    enroll(c, flow, acme)
    tick(c, T0)
    assert flow_outbox(c) == [] and "limit (1)" in runs(c, flow)[0]["deferred"]
    tick(c, T0 + timedelta(days=1))
    assert len(flow_outbox(c)) == 1


def test_one_email_per_company_domain_per_day_and_per_run_cap(tmp_path):
    csv = "company,contact_name,email,website\nAcme,Ravi,ravi@acme.com,https://acme.com\nAcme HR,Meena,meena@acme.com,https://acme.com\n"
    c = make_client(tmp_path, csv=csv + "".join(f"Co{i},P{i},p{i}@co{i}.in,https://co{i}.in\n" for i in range(6)))
    flow = make_flow(c, [TRIGGER, email_node()], chain("t", "mail"))
    ids = [l["id"] for l in c.get("/api/leads").json()]
    enroll(c, flow, *ids)
    tick(c, T0)
    first = flow_outbox(c)
    assert len(first) == 5                                               # default per-run cap
    tick(c, T0 + timedelta(minutes=1))
    tick(c, T0 + timedelta(minutes=2))
    sent = flow_outbox(c)
    assert sum(1 for m in sent if m["to"].endswith("@acme.com")) == 1     # one email per company a day
    assert len(sent) == 7
    acme_wait = [r for r in runs(c, flow) if r["deferred"]]
    assert len(acme_wait) == 1 and "one email per company" in acme_wait[0]["deferred"]


def test_practice_mode_by_default_with_idempotency_key_and_message_id(c):
    flow = make_flow(c, [TRIGGER, email_node()], chain("t", "mail"))
    acme = lead(c)["id"]
    enroll(c, flow, acme)
    tick(c, T0)
    row = flow_outbox(c)[0]
    run = runs(c, flow)[0]
    assert row["status"] == "dry_run" and row["idempotency_key"] == f"flow:{run['id']}:1:practice"
    assert row["message_id"].startswith("<") and row["message_id"].endswith(f".{acme}@outreach.local>")
    assert f"/u/{acme}" in row["body"] and row["step"] == "Flow Test flow: Send email"
    assert any("practice only, not sent" in t for t in timeline_texts(c, acme))
    assert c.get(f"/api/leads/{acme}").json()["stage"] == "new"           # practice never marks a lead contacted


def test_live_email_uses_the_real_sender_headers_and_marks_contacted(tmp_path):
    resend = FakeTransport(reply={"id": "re_123"})
    c = make_client(tmp_path, email_transport=resend)
    connect_email(c)
    go_live(c)
    flow = make_flow(c, [TRIGGER, email_node()], chain("t", "mail"))
    acme = lead(c)["id"]
    enroll(c, flow, acme)
    tick(c, T0)
    assert len(resend.calls) == 1
    call = resend.calls[0]
    assert call["headers"]["Idempotency-Key"].endswith(":live") and call["body"]["headers"]["Message-ID"].endswith("@mail.example.in>")
    assert call["body"]["headers"]["List-Unsubscribe"].startswith("<https://outreach.example.in/u/")
    assert flow_outbox(c)[0]["status"] == "sent" and c.get(f"/api/leads/{acme}").json()["stage"] == "contacted"


def test_a_provider_outage_pauses_sending_and_retries_later(tmp_path):
    c = make_client(tmp_path, email_transport=FakeTransport(error=OSError("timed out")))
    connect_email(c)
    go_live(c)
    flow = make_flow(c, [TRIGGER, email_node()], chain("t", "mail"))
    enroll(c, flow, lead(c)["id"])
    tick(c, T0)
    run = runs(c, flow)[0]
    assert run["status"] == "waiting" and run["claim"] is None and "provider" in run["deferred"]
    assert "timed out" in store_of(c).read("settings")["sending_paused"]["reason"]      # all sending paused for an hour
    tick(c, T0 + timedelta(minutes=30))
    assert len(flow_outbox(c)) == 1                                       # the failed attempt only; no retry inside the pause


def test_an_unfilled_placeholder_is_never_sent(c):
    c.put("/api/settings", json={"sender_name": ""})
    flow = make_flow(c, [TRIGGER, email_node()], chain("t", "mail"))
    enroll(c, flow, lead(c)["id"])
    tick(c, T0)
    assert flow_outbox(c) == [] and "placeholder" in runs(c, flow)[0]["stop_reason"]


def test_not_now_leads_are_not_emailed_by_flows(c):
    flow = make_flow(c, [TRIGGER, node("w", "wait", days=1, hours=0), email_node()], chain("t", "w", "mail"), stop_on_reply=False)
    acme = lead(c)["id"]
    enroll(c, flow, acme)
    tick(c, T0)
    patch_lead(c, acme, stage="not_now")
    tick(c, T0 + timedelta(days=2))
    assert flow_outbox(c) == [] and "not_now" in runs(c, flow)[0]["stop_reason"]


# ---------------------------------------------------------------- stage changes
@pytest.mark.parametrize("start,target,consented,moved", [
    ("new", "customer", False, False),           # can_move: no jumping to customer
    ("new", "interested", False, False),         # interested needs consent
    ("new", "interested", True, True),
    ("contacted", "not_now", False, True),
    ("disqualified", "new", False, False),       # closed leads stay closed (the run stops before the step)
])
def test_set_stage_respects_can_move(c, start, target, consented, moved):
    flow = make_flow(c, [TRIGGER, node("s", "set_stage", stage=target)], chain("t", "s"), stop_on_reply=False)
    acme = lead(c)["id"]
    if consented:
        consent(c, acme)
    store_of(c).update_lead(acme, lambda l: {**l, "stage": start})
    if start == "disqualified":
        assert enroll(c, flow, acme)["refused"]
        return
    enroll(c, flow, acme)
    tick(c)
    stage = c.get(f"/api/leads/{acme}").json()["stage"]
    assert (stage == target) is moved
    if not moved:
        assert "not allowed" in runs(c, flow)[0]["stop_reason"]


# ---------------------------------------------------------------- webhooks
def test_webhook_needs_a_public_https_address(tmp_path):
    hook = FakeTransport(reply={"ok": True})
    c = make_client(tmp_path, channel_transport=hook, resolver=lambda host: ["10.0.0.7"] if host == "inside.example.com" else ["93.184.216.34"])
    nodes = [TRIGGER, node("hook", "webhook", url="https://inside.example.com/hook"), node("tag", "add_tag", tag="hook-failed")]
    blocked = make_flow(c, nodes, [edge("t", "hook"), edge("hook", "tag", "error")], name="Inside")
    acme = lead(c)["id"]
    enroll(c, blocked, acme)
    tick(c)
    assert hook.calls == [] and "hook-failed" in c.get(f"/api/leads/{acme}").json()["tags"]
    ok = make_flow(c, [TRIGGER, node("hook", "webhook", url="https://hooks.example.com/flow")], chain("t", "hook"), name="Outside")
    enroll(c, ok, acme)
    tick(c)
    assert len(hook.calls) == 1 and hook.calls[0]["url"] == "https://hooks.example.com/flow"
    sent = hook.calls[0]["body"]
    assert sent["lead"]["company"] == "Acme" and "email" not in sent["lead"] and sent["flow"]["name"] == "Outside"
    r = c.post("/api/flows", json={"name": "Plain http", "nodes": [TRIGGER, node("hook", "webhook", url="http://hooks.example.com")],
                                   "edges": chain("t", "hook")})
    assert any("https://" in e["message"] for e in r.json()["errors"])
    assert c.post(f"/api/flows/{r.json()['id']}/activate").status_code == 400


# ---------------------------------------------------------------- task-style actions
def test_linkedin_call_meeting_notify_and_tag_actions(c):
    nodes = [TRIGGER, node("li", "linkedin_task", mode="start_sequence"),
             node("li_task", "linkedin_task", mode="task", message="Hi {{first_name}}, glad to connect."),
             node("call", "call_task", note="Call about the pilot"), node("meet", "meeting_link"),
             node("ping", "notify", message="Check this lead"), node("tag", "add_tag", tag="combo")]
    edges = [edge("t", "li"), edge("li", "li_task", "error"), edge("li_task", "call"), edge("call", "meet"),
             edge("meet", "ping"), edge("ping", "tag")]
    flow = make_flow(c, nodes, edges)
    acme = lead(c)["id"]
    enroll(c, flow, acme)
    tick(c, T0)
    tasks = [t for t in store_of(c).read("tasks") if t["lead_id"] == acme]
    kinds = {t["kind"]: t for t in tasks}
    assert kinds["linkedin"]["text"] == "Hi Ravi, glad to connect." and kinds["linkedin"]["title"].startswith("LinkedIn")
    assert kinds["call"]["owner"] == "Sales team" and kinds["call"]["due_at"]
    assert "no calendar is connected" in kinds["meeting"]["text"]
    assert any(n["text"] == "Check this lead" for n in c.get("/api/notifications").json())
    assert c.get(f"/api/leads/{acme}").json()["tags"] == ["combo"]
    texts = timeline_texts(c, acme)
    assert any("LinkedIn sequence not started" in t for t in texts) and runs(c, flow)[0]["status"] == "done"
    assert sum(1 for t in texts if t.startswith("Flow Test flow:")) >= 6       # every action leaves a timeline line
