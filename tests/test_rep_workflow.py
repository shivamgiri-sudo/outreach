"""Rep workflow: My day, the call queue and call outcomes, the call script, reply triage, stage moves with a reason,
consent that moves a lead to Interested, and the WhatsApp mode."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import create_app

CSV = ("company,contact_name,email,phone,website,open_roles,company_size,country\n"
       "Acme,Ravi Kumar,ravi@acme.com,98765 43210,https://acme.com,12,200,IN\n"
       "Beta Ltd,Sita Rao,sita@beta.in,91234 56789,https://beta.in,3,40,IN\n")
PW = "s3cret-pass"


def make(tmp_path, password=None):
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password=password))
    if password:
        c.headers["Authorization"] = "Bearer " + c.post("/api/login", json={"password": PW}).json()["token"]
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    return c


def lead_named(c, company):
    return next(l for l in c.get("/api/leads").json() if l["company"] == company)


def as_user(c, email, role="caller", name="Priya"):
    """Add a login and return headers for it (the client stays signed in as the admin)."""
    c.post("/api/users", json={"email": email, "name": name, "role": role, "password": "long-password-1"})
    token = c.post("/api/login", json={"email": email, "password": "long-password-1"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def soon(minutes=90):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def due_now(c, lid):
    """Tasks made on a weekend are due the next business morning; tests that check "due today" move them to now."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    c.app.state.store.update("tasks", lambda rows: [{**t, "due_at": now} if t.get("lead_id") == lid and t.get("kind") == "call" else t for t in rows])


def give_consent(c, lid, channel="call", **kw):
    return c.post(f"/api/leads/{lid}/consent", json={"text": "Yes, call me — on the phone with Priya", "channel": channel, **kw})


# ---------------------------------------------------------------- call outcomes
def test_call_outcomes_log_activity_by_signed_in_user(tmp_path):
    c = make(tmp_path, PW)
    priya = as_user(c, "priya@mcn.in")
    lid = lead_named(c, "Acme")["id"]
    r = c.post(f"/api/leads/{lid}/call-outcome", json={"outcome": "no_answer", "notes": "rang out"}, headers=priya)
    assert r.status_code == 200, r.text
    assert r.json()["activity"]["by"] == "Priya" and r.json()["activity"]["outcome"] == "no_answer"
    trail = c.get(f"/api/leads/{lid}/activity").json()
    call = next(i for i in trail if i["type"] == "call")
    assert call["status"] == "no_answer" and call["detail"] == "Priya"
    assert c.post(f"/api/leads/{lid}/call-outcome", json={"outcome": "maybe"}, headers=priya).status_code == 400


def test_call_back_needs_a_time_and_creates_a_task_for_me(tmp_path):
    c = make(tmp_path, PW)
    priya = as_user(c, "priya@mcn.in")
    lid = lead_named(c, "Acme")["id"]
    assert c.post(f"/api/leads/{lid}/call-outcome", json={"outcome": "call_back"}, headers=priya).status_code == 400
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assert c.post(f"/api/leads/{lid}/call-outcome", json={"outcome": "call_back", "callback_at": past}, headers=priya).status_code == 400
    r = c.post(f"/api/leads/{lid}/call-outcome", json={"outcome": "call_back", "callback_at": soon(), "notes": "after lunch"}, headers=priya).json()
    task = r["task"]
    assert task["kind"] == "call" and task["source"] == "callback" and task["owner"] == "Priya" and task["title"] == "Call back"
    board = c.get("/api/tasks/board?owner=Priya").json()
    assert any(t["id"] == task["id"] for t in board["today"] + board["upcoming"] + board["overdue"])


def test_interested_records_consent_moves_stage_and_adds_follow_up(tmp_path):
    c = make(tmp_path, PW)
    priya = as_user(c, "priya@mcn.in")
    lid = lead_named(c, "Acme")["id"]
    r = c.post(f"/api/leads/{lid}/call-outcome", json={"outcome": "interested", "notes": "wants 3 roles"}, headers=priya).json()
    assert r["stage"] == "interested" and r["consent_recorded"] is True
    assert r["lead"]["consent"]["channel"] == "call" and "Priya" in r["lead"]["consent"]["text"]
    assert r["task"]["kind"] == "follow_up" and r["task"]["owner"] == "Priya"
    ledger = c.get(f"/api/leads/{lid}/export")
    assert ledger.status_code == 200


def test_not_interested_disqualifies_and_wrong_number_flags_phone(tmp_path):
    c = make(tmp_path, PW)
    acme, beta = lead_named(c, "Acme")["id"], lead_named(c, "Beta Ltd")["id"]
    r = c.post(f"/api/leads/{acme}/call-outcome", json={"outcome": "not_interested"}).json()
    assert r["stage"] == "disqualified" and r["lead"]["stage"] == "disqualified"
    r = c.post(f"/api/leads/{beta}/call-outcome", json={"outcome": "wrong_number"}).json()
    assert r["phone_flagged"] and r["lead"]["phone_status"]["status"] == "wrong_number"
    # neither is in the call queue any more, even with consent
    give_consent(c, beta)
    ids = [q["lead_id"] for q in c.get("/api/call-queue").json()["queue"]]
    assert acme not in ids and beta not in ids


def test_inactive_number_leaves_the_queue_and_disconnected_counts_as_unanswered(tmp_path):
    c = make(tmp_path, PW)
    acme, beta = lead_named(c, "Acme")["id"], lead_named(c, "Beta Ltd")["id"]
    r = c.post(f"/api/leads/{beta}/call-outcome", json={"outcome": "inactive_number", "notes": "switched off twice"}).json()
    assert r["phone_flagged"] and r["lead"]["phone_status"]["status"] == "inactive_number"
    give_consent(c, beta)
    assert beta not in [q["lead_id"] for q in c.get("/api/call-queue").json()["queue"]]
    r = c.post(f"/api/leads/{acme}/call-outcome", json={"outcome": "call_disconnected", "notes": "line dropped after hello"})
    assert r.status_code == 200, r.text
    summary = c.get(f"/api/leads/{acme}/history-summary").json()
    assert summary["calls"]["unanswered"] == 1 and summary["calls"]["outcomes"] == {"call_disconnected": 1}
    timeline = c.get(f"/api/leads/{acme}/activity").json()
    assert "line dropped after hello" in str(timeline)              # the remark is saved with the call


def test_call_queue_orders_callbacks_then_yes_then_consent(tmp_path):
    c = make(tmp_path, PW)
    acme, beta = lead_named(c, "Acme")["id"], lead_named(c, "Beta Ltd")["id"]
    assert c.get("/api/call-queue").json()["queue"] == []        # nobody said yes, no tasks, no consent
    give_consent(c, beta)                                           # consent only
    give_consent(c, acme, move_to_interested=True)                  # said yes + call task
    due_now(c, acme)
    q = c.get("/api/call-queue").json()["queue"]
    assert [x["lead_id"] for x in q] == [acme, beta]
    assert q[0]["task_id"] and "Call task" in q[0]["reason"] and q[1]["reason"] == "Has a phone and consent"
    assert q[0]["next"]["action"] == "call" and q[0]["phone_ok"]
    c.post(f"/api/leads/{beta}/call-outcome", json={"outcome": "call_back", "callback_at": soon(1)})
    q = c.get("/api/call-queue").json()["queue"]
    assert q[0]["lead_id"] == beta and q[0]["reason"].startswith("Call back")
    by = {x["lead_id"]: x for x in q}
    assert by[beta]["attempts"] == 1 and by[beta]["last_outcome"] == "call_back" and by[beta]["last_attempt_at"]
    assert by[acme]["attempts"] == 0 and by[acme]["last_outcome"] == ""         # never called: the "Never called" group


# ---------------------------------------------------------------- my day
def test_my_day_for_signed_in_caller(tmp_path):
    c = make(tmp_path, PW)
    priya = as_user(c, "priya@mcn.in")
    c.put("/api/team", json={"team": [{"name": "Priya", "email": "priya@mcn.in", "role": "caller"}, {"name": "Amit", "email": "amit@mcn.in"}]})
    acme, beta = lead_named(c, "Acme")["id"], lead_named(c, "Beta Ltd")["id"]
    c.post("/api/tasks", json={"lead_id": acme, "title": "Send deck", "owner": "Priya", "due_at": soon(30)})
    c.post("/api/tasks", json={"lead_id": beta, "title": "Amit's job", "owner": "Amit", "due_at": soon(30)})
    c.post(f"/api/leads/{beta}/call-outcome", json={"outcome": "call_back", "callback_at": soon(20)}, headers=priya)
    c.post(f"/api/leads/{acme}/call-outcome", json={"outcome": "connected"}, headers=priya)
    c.post(f"/api/leads/{acme}/reply", json={"text": "How much does it cost?"})
    day = c.get("/api/my-day", headers=priya)
    assert day.status_code == 200, day.text
    d = day.json()
    assert d["me"]["name"] == "Priya"
    titles = [t["title"] for t in d["tasks"]]
    assert "Send deck" in titles and "Amit's job" not in titles
    assert [t["lead_id"] for t in d["callbacks"]] == [beta]
    assert d["callbacks"][0]["next"]["action"] == "call"
    assert d["today"]["calls"] == 2 and d["today"]["connects"] == 2      # asked to call back counts as a connect
    assert d["week"]["calls"] >= 2 and d["targets"]["calls"] == 40
    assert any(r["lead_id"] == acme for r in d["replies"])
    assert d["replies"][0]["next"]["action"] in ("email", "call")
    # Amit sees his own task, not Priya's
    amit = as_user(c, "amit@mcn.in", name="Amit")
    a = c.get("/api/my-day", headers=amit).json()
    assert [t["title"] for t in a["tasks"]] == ["Amit's job"] and a["callbacks"] == [] and a["today"]["calls"] == 0


def test_targets_personal_and_team_defaults(tmp_path):
    c = make(tmp_path, PW)
    priya = as_user(c, "priya@mcn.in")
    r = c.put("/api/my-day/targets", json={"scope": "me", "targets": {"calls": 60}}, headers=priya)
    assert r.status_code == 200 and r.json()["targets"]["calls"] == 60 and r.json()["personal"]
    assert c.put("/api/my-day/targets", json={"scope": "team", "targets": {"calls": 50}}, headers=priya).status_code == 403
    assert c.put("/api/my-day/targets", json={"scope": "me", "targets": {"calls": -1}}, headers=priya).status_code == 400
    assert c.put("/api/my-day/targets", json={"scope": "team", "targets": {"calls": 50, "emails": 30}}).status_code == 200
    d = c.get("/api/my-day", headers=priya).json()
    assert d["targets"]["calls"] == 60 and d["targets"]["emails"] == 30 and d["team_defaults"]["calls"] == 50


def test_my_day_without_login_uses_team_caller(tmp_path):
    c = make(tmp_path)
    c.put("/api/team", json={"caller_name": "Priya"})
    lid = lead_named(c, "Acme")["id"]
    give_consent(c, lid, move_to_interested=True)                   # call task goes to the caller (Priya)
    due_now(c, lid)
    d = c.get("/api/my-day").json()
    assert d["me"]["name"] == "Priya" and [t["lead_id"] for t in d["calls_due"]] == [lid]


# ---------------------------------------------------------------- call script
def test_call_script_default_editable_by_owner_only(tmp_path):
    c = make(tmp_path, PW)
    s = c.get("/api/call-script").json()
    text = " ".join(x["text"] for x in s["script"]).lower()
    assert "beta" not in text and "no case studies" not in text and not s["custom"]
    priya = as_user(c, "priya@mcn.in")
    body = {"script": [{"title": "Opener", "text": "Hi {{first_name}}"}], "objections": [{"q": "Busy", "a": "Call later?"}]}
    assert c.put("/api/call-script", json=body, headers=priya).status_code == 403
    assert c.put("/api/call-script", json={"script": [], "objections": []}).status_code == 400
    r = c.put("/api/call-script", json=body).json()
    assert r["custom"] and r["script"] == body["script"] and r["objections"] == body["objections"]
    assert c.get("/api/call-script", headers=priya).json()["script"][0]["text"] == "Hi {{first_name}}"
    assert c.delete("/api/call-script").json()["custom"] is False


# ---------------------------------------------------------------- reply triage
def _reply(c, lid, text="Interested, call me tomorrow"):
    c.post(f"/api/leads/{lid}/reply", json={"text": text})
    return next(r for r in c.get("/api/reply-inbox").json()["replies"] if r["lead_id"] == lid)


def test_reply_handled_unhandled_read_and_label(tmp_path):
    c = make(tmp_path, PW)
    lid = lead_named(c, "Acme")["id"]
    row = _reply(c, lid, "What does it cost?")
    assert row["key"] and row["needs_action"] and not row["read"] and not row["handled"]
    act = lambda **kw: c.post("/api/reply-inbox/action", json={"key": row["key"], **kw})
    assert act(action="read").json()["state"]["read"] is True
    assert act(action="unread").json()["state"]["read"] is False
    r = act(action="handled").json()
    assert r["state"]["handled"] and r["state"]["handled_by"] == "Sales team"      # the admin password login acts as the team caller
    assert c.get("/api/reply-inbox?only_action=true").json()["replies"] == []
    act(action="unhandled")
    assert c.get("/api/reply-inbox?only_action=true").json()["replies"][0]["key"] == row["key"]
    r = act(action="label", label="interested")
    again = next(x for x in c.get("/api/reply-inbox").json()["replies"] if x["key"] == row["key"])
    assert again["label"] == "interested" and again["label_overridden"] and again["guessed_label"] == "question"
    assert act(action="label", label="nonsense").status_code == 400
    assert act(action="explode").status_code == 400
    assert c.post("/api/reply-inbox/action", json={"key": "nope|x", "action": "read"}).status_code == 404


def test_reply_assign_and_snooze_creates_task(tmp_path):
    c = make(tmp_path, PW)
    c.put("/api/team", json={"team": [{"name": "Priya", "email": "priya@mcn.in"}]})
    lid = lead_named(c, "Acme")["id"]
    row = _reply(c, lid, "Can you send details?")
    assert c.post("/api/reply-inbox/action", json={"key": row["key"], "action": "assign", "assignee": "Stranger"}).status_code == 400
    r = c.post("/api/reply-inbox/action", json={"key": row["key"], "action": "assign", "assignee": "Priya"}).json()
    assert r["state"]["assignee"] == "Priya"
    assert c.get("/api/reply-inbox?assignee=__unassigned").json()["replies"] == []
    until = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    r = c.post("/api/reply-inbox/action", json={"key": row["key"], "action": "snooze", "until": until}).json()
    assert r["task"]["owner"] == "Priya" and r["task"]["due_at"]
    assert c.get("/api/reply-inbox?only_action=true").json()["replies"] == []      # snoozed: not now
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert c.post("/api/reply-inbox/action", json={"key": row["key"], "action": "snooze", "until": past}).status_code == 400


def test_thread_lists_in_and_out_in_order(tmp_path):
    c = make(tmp_path, PW)
    lid = lead_named(c, "Acme")["id"]
    c.post(f"/api/leads/{lid}/email-compose", json={"subject": "Screening for your roles", "body": "{{greeting}}\n\nQuick idea."})
    row = _reply(c, lid, "Sounds good, what is the price?")
    assert row["subject"] == "Screening for your roles"
    t = c.get(f"/api/leads/{lid}/thread").json()
    dirs = [m["direction"] for m in t["messages"]]
    assert dirs[0] == "out" and "in" in dirs
    assert t["messages"] == sorted(t["messages"], key=lambda m: m["at"])
    assert c.get("/api/leads/nope/thread").status_code == 404


# ---------------------------------------------------------------- stage moves
def test_stage_back_move_needs_reason_and_skip_needs_owner_force(tmp_path):
    c = make(tmp_path, PW)
    priya = as_user(c, "priya@mcn.in")
    lid = lead_named(c, "Acme")["id"]
    assert c.post(f"/api/leads/{lid}/stage", json={"stage": "verified"}, headers=priya).status_code == 200
    r = c.post(f"/api/leads/{lid}/stage", json={"stage": "new"}, headers=priya)
    assert r.status_code == 400 and "reason" in r.json()["detail"]
    r = c.post(f"/api/leads/{lid}/stage", json={"stage": "new", "reason": "verified by mistake"}, headers=priya)
    assert r.status_code == 200 and r.json()["stage"] == "new"
    event = r.json()["timeline"][-1]
    assert event["by"] == "Priya" and "verified by mistake" in event["text"] and event["reason"] == "verified by mistake"
    # skipping steps: a caller cannot, an owner can with force + reason
    assert c.post(f"/api/leads/{lid}/stage", json={"stage": "call_done", "reason": "x", "force": True}, headers=priya).status_code == 400
    assert c.post(f"/api/leads/{lid}/stage", json={"stage": "call_done", "force": True}).status_code == 400
    assert c.post(f"/api/leads/{lid}/stage", json={"stage": "call_done", "reason": "met at expo", "force": True}).status_code == 200
    # disqualified and back
    assert c.post(f"/api/leads/{lid}/stage", json={"stage": "disqualified"}, headers=priya).status_code == 200
    assert c.post(f"/api/leads/{lid}/stage", json={"stage": "contacted", "reason": "they called back"}, headers=priya).status_code == 200
    # interested always needs consent, even forced
    assert c.post(f"/api/leads/{lid}/stage", json={"stage": "interested", "reason": "x", "force": True}).status_code == 400
    # unsubscribed is final
    c.post(f"/api/leads/{lid}/stage", json={"stage": "unsubscribed"})
    assert c.post(f"/api/leads/{lid}/stage", json={"stage": "new", "reason": "oops", "force": True}).status_code == 400


def test_stage_options_reflect_rules(tmp_path):
    c = make(tmp_path, PW)
    priya = as_user(c, "priya@mcn.in")
    lid = lead_named(c, "Acme")["id"]
    c.post(f"/api/leads/{lid}/stage", json={"stage": "verified"})
    opts = {o["stage"]: o for o in c.get(f"/api/leads/{lid}/stage-options", headers=priya).json()["options"]}
    assert opts["enriched"]["rule"] == "yes" and opts["new"]["rule"] == "reason" and opts["disqualified"]["allowed"]
    assert opts["interested"]["rule"] == "no" and opts["trial"]["rule"] == "yes"
    assert opts["customer"]["rule"] == "owner" and not opts["customer"]["allowed"]
    owner_opts = {o["stage"]: o for o in c.get(f"/api/leads/{lid}/stage-options").json()["options"]}
    assert owner_opts["customer"]["allowed"]


# ---------------------------------------------------------------- consent
def test_consent_can_move_to_interested_and_create_call_task(tmp_path):
    c = make(tmp_path, PW)
    lid = lead_named(c, "Acme")["id"]
    r = give_consent(c, lid, move_to_interested=True)
    assert r.status_code == 200 and r.json()["stage"] == "interested" and r.json()["consent"]["channel"] == "call"
    tasks = c.get("/api/tasks/board").json()
    calls = [t for b in ("overdue", "today", "upcoming") for t in tasks[b] if t["lead_id"] == lid and t["kind"] == "call"]
    assert len(calls) == 1
    other = lead_named(c, "Beta Ltd")["id"]
    r = give_consent(c, other, channel="email")                    # without the flag: consent only
    assert r.json()["stage"] == "new" and r.json()["consent"]


def test_whatsapp_status_reports_practice_mode(tmp_path):
    c = make(tmp_path)
    s = c.get("/api/whatsapp/status").json()
    assert s["mode"] == "practice" and s["connected"] is False


def test_tasks_for_an_owned_lead_go_to_its_owner(tmp_path):
    from app.activities import add_task, caller_name
    from tests.flow_helpers import lead, make_client, patch_lead, store_of
    c = make_client(tmp_path)
    a = lead(c, "Acme")["id"]
    store = store_of(c)
    patch_lead(c, a, owner="Meena")
    assert add_task(store, a, "call", "ring", owner=caller_name(store))["owner"] == "Meena"
    assert add_task(store, a, "follow_up", "x")["owner"] == "Meena"
    assert add_task(store, a, "review", "y", owner="Ravi")["owner"] == "Ravi"         # a named person stays


# ---------------------------------------------------------------- invalid / consultant leads marked on a call
def test_invalid_and_consultant_outcomes_mark_the_lead(tmp_path):
    c = make(tmp_path, PW)
    acme, beta = lead_named(c, "Acme")["id"], lead_named(c, "Beta Ltd")["id"]
    give_consent(c, acme)
    give_consent(c, beta)
    assert {acme, beta} <= {q["lead_id"] for q in c.get("/api/call-queue").json()["queue"]}
    r = c.post(f"/api/leads/{acme}/call-outcome", json={"outcome": "invalid_lead", "notes": "no such company"})
    assert r.status_code == 200, r.text
    r = c.post(f"/api/leads/{beta}/call-outcome", json={"outcome": "consultant_lead", "notes": "staffing agency"})
    assert r.status_code == 200, r.text
    a, b = lead_named(c, "Acme"), lead_named(c, "Beta Ltd")
    assert a["lead_quality"]["kind"] == "invalid" and a["lead_quality"]["note"] == "no such company" and a["stage"] == "disqualified"
    assert b["lead_quality"]["kind"] == "consultant" and b["stage"] != "disqualified"            # a consultant keeps their stage
    assert any(e["kind"] == "quality" for e in a["timeline"]) and any(e["kind"] == "quality" for e in b["timeline"])
    assert not ({acme, beta} & {q["lead_id"] for q in c.get("/api/call-queue").json()["queue"]})   # both leave the call queue
    summary = c.get(f"/api/leads/{beta}/history-summary").json()
    assert summary["calls"]["outcomes"] == {"consultant_lead": 1}
    assert c.post(f"/api/leads/{beta}/call-outcome", json={"outcome": "bogus"}).status_code == 400


def test_call_disconnected_can_offer_a_quick_callback(tmp_path):
    c = make(tmp_path, PW)
    acme = lead_named(c, "Acme")["id"]
    plain = c.post(f"/api/leads/{acme}/call-outcome", json={"outcome": "call_disconnected", "notes": "line dropped"})
    assert plain.status_code == 200 and plain.json()["task"] is None                    # no time given: no task, as before
    r = c.post(f"/api/leads/{acme}/call-outcome", json={"outcome": "call_disconnected", "notes": "dropped after hello", "callback_at": soon(15)})
    assert r.status_code == 200, r.text
    task = r.json()["task"]
    assert task and task["title"] == "Call back (call dropped)" and task["kind"] == "call"
    assert c.post(f"/api/leads/{acme}/call-outcome", json={"outcome": "call_disconnected", "callback_at": "2020-01-01T00:00:00+00:00"}).status_code in (400, 422)
    assert c.get(f"/api/leads/{acme}/history-summary").json()["calls"]["outcomes"]["call_disconnected"] == 2     # the refused past-time call was not logged
