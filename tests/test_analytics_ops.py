"""Analytics over a period, campaign outcomes, outbox paging, the Send-for-real switch, the audit trail and system health."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import analytics_range, system_health
from app.main import create_app
from tests.flow_helpers import CSV, IDENTITY, connect_email, lead, make_client, patch_lead, store_of

FROM, TO = "2026-11-01", "2026-11-07"          # previous period: 2026-10-25 .. 2026-10-31 (IST days)


def ev(at, kind, text="", by=None):
    return {"at": at, "kind": kind, "text": text, **({"by": by} if by else {})}


def outbox_row(lead_id, at, step="Email 1", variant="A", status="sent", **extra):
    return {"lead_id": lead_id, "to": f"{lead_id}@x.in", "subject": f"Hi {step}", "body": f"Hello\n\nThis is {step}.\n\nUnsubscribe: https://x/u/1",
            "step": step, "variant": variant, "status": status, "sent_at": at, "sent_at_ist_date": at[:10], "idempotency_key": f"{lead_id}:{step}:{at}", **extra}


def seeded(tmp_path):
    """Acme: contacted + replied + interested + meeting in the period (campaign Pilot). Beta: contacted in the previous period."""
    c = make_client(tmp_path)
    s = store_of(c)
    a, b = lead(c, "Acme"), lead(c, "Beta")
    s.update("campaigns", lambda rows: [*rows, {"id": "camp1", "name": "Pilot", "status": "draft", "runs": [], "stats": {}}])
    patch_lead(c, a["id"], campaign_id="camp1", created_at="2026-11-01T04:00:00+00:00", stage="call_done",
               engagement={"opens": 2, "clicks": 0, "replies": 1}, sequence={"enrolled_at": "2026-11-01T04:00:00+00:00", "sent_days": [0, 3], "stopped": None},
               timeline=[ev("2026-11-02T04:00:00+00:00", "email", "Email 1 — sent"), ev("2026-11-04T04:00:00+00:00", "email", "Email 2 — sent"),
                         ev("2026-11-04T10:00:00+00:00", "reply", "[interested] yes"),
                         ev("2026-11-05T04:00:00+00:00", "stage", "engaged → interested (by Meena)", by="Meena"),
                         ev("2026-11-06T04:00:00+00:00", "stage", "interested → call_done (by Meena)", by="Meena"),
                         ev("2026-11-06T05:00:00+00:00", "note", "called", by="Meena")])
    patch_lead(c, b["id"], created_at="2026-10-26T04:00:00+00:00", stage="contacted",
               timeline=[ev("2026-10-27T04:00:00+00:00", "email", "Email 1 — sent")])
    s.update("outbox", lambda rows: [*rows, outbox_row(b["id"], "2026-10-27T04:00:00+00:00"),
                                     outbox_row(a["id"], "2026-11-02T04:00:00+00:00"),
                                     outbox_row(a["id"], "2026-11-04T04:00:00+00:00", step="Email 2", variant="B"),
                                     outbox_row(a["id"], "2026-11-05T04:00:00+00:00", step="One-off email", variant="manual", manual=True, by="Meena"),
                                     outbox_row(b["id"], "2026-11-03T04:00:00+00:00", step="Email 2", status="dry_run")])
    return c, a, b


def owner_and_caller(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    owner = {"Authorization": "Bearer " + c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]}
    c.post("/api/users", json={"email": "meena@example.com", "name": "Meena", "role": "caller", "password": "long-password-1"}, headers=owner)
    caller = {"Authorization": "Bearer " + c.post("/api/login", json={"email": "meena@example.com", "password": "long-password-1"}).json()["token"]}
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=owner)
    return c, owner, caller


# ---------------------------------------------------------------- periods
def test_range_parsing_days_custom_previous_and_errors():
    now = datetime(2026, 11, 6, 20, 0, tzinfo=timezone.utc)          # already 7 Nov in IST
    r = analytics_range.parse_range(days=7, now=now)
    assert (r["from"], r["to"], r["prev_from"], r["prev_to"]) == ("2026-11-01", "2026-11-07", "2026-10-25", "2026-10-31")
    r = analytics_range.parse_range(FROM, TO, now=now)
    assert r["days"] == 7 and r["start"].isoformat() == "2026-11-01T00:00:00+05:30" and r["end"].isoformat() == "2026-11-08T00:00:00+05:30"
    for bad in (("2026-11-07", "2026-11-01"), ("nope", ""), ("2020-01-01", "2026-11-01")):
        with pytest.raises(analytics_range.RangeError):
            analytics_range.parse_range(*bad, now=now)


def test_api_period_errors_are_400(tmp_path):
    c = make_client(tmp_path)
    assert c.get("/api/analytics?from=2026-11-07&to=2026-11-01").status_code == 400
    assert c.get("/api/intelligence?days=0").status_code == 400
    assert c.get("/api/insights?from=bad").status_code == 400


# ---------------------------------------------------------------- analytics
def test_overview_kpis_with_delta_against_previous_period(tmp_path):
    c, a, b = seeded(tmp_path)
    k = c.get(f"/api/analytics/overview?from={FROM}&to={TO}").json()["kpis"]
    assert k["contacted"] == {"value": 1, "previous": 1, "delta": 0, "delta_pct": 0.0}
    assert k["emails_sent"]["value"] == 3 and k["emails_sent"]["previous"] == 1 and k["emails_sent"]["delta_pct"] == 200.0
    assert k["replied"]["value"] == 1 and k["replied"]["previous"] == 0 and k["replied"]["delta_pct"] is None
    assert k["interested"]["value"] == 1 and k["meetings"]["value"] == 1 and k["new_leads"] == {"value": 1, "previous": 1, "delta": 0, "delta_pct": 0.0}
    assert k["opened"]["value"] == 1 and k["open_rate"]["value"] == 100.0


def test_funnel_by_campaign_step_variant_source_and_velocity(tmp_path):
    c, a, b = seeded(tmp_path)
    d = c.get(f"/api/analytics?from={FROM}&to={TO}").json()
    f = d["funnel"]
    assert f["steps"] == ["contacted", "replied", "interested", "meeting", "customer"]
    assert {k: f["overall"][k] for k in f["steps"]} == {"contacted": 1, "replied": 1, "interested": 1, "meeting": 1, "customer": 0}
    assert f["by"]["campaign"][0]["name"] == "Pilot" and f["by"]["campaign"][0]["rates"]["replied"] == 100.0
    assert f["by"]["step"][0]["name"] == "Email 2" and f["by"]["variant"][0]["name"] == "B"     # the email before the reply
    assert f["by"]["source"][0]["name"] == "apollo"
    steps = {(s["from"], s["to"]): s for s in f["velocity"]["steps"]}
    assert steps[("contacted", "replied")]["median_days"] == 2.2 and steps[("interested", "meeting")]["median_days"] == 1.0
    assert {s["stage"]: s["median_days"] for s in f["velocity"]["stages"]} == {"interested": 1.0}
    assert d["team"][0]["who"] == "Meena" and d["team"][0]["stage_changes"] == 2 and d["team"][0]["by_kind"]["emails_by_hand"] == 1
    # the previous week: only Beta was contacted, nothing further
    prev = c.get("/api/analytics?from=2026-10-25&to=2026-10-31").json()["funnel"]["overall"]
    assert prev["contacted"] == 1 and prev["replied"] == 0


def test_intelligence_and_insights_honour_the_period(tmp_path):
    c, a, b = seeded(tmp_path)
    plain = c.get("/api/intelligence").json()
    assert "range" not in plain
    d = c.get(f"/api/intelligence?from={FROM}&to={TO}").json()
    assert d["range"]["from"] == FROM and d["range"]["prev_to"] == "2026-10-31"
    assert d["channels"]["email"]["enrolled"] == 1 and d["team"][0]["who"] == "Meena"
    old = c.get("/api/intelligence?from=2026-01-01&to=2026-01-07").json()
    assert old["channels"]["email"]["enrolled"] == 0 and old["team"] == []
    ins = c.get(f"/api/insights?from={FROM}&to={TO}").json()
    # the practice (dry_run) row is not an email sent: Insights agrees with the Analytics overview (3)
    assert ins["totals"]["leads"] == 1 and ins["totals"]["emails_sent"] == 3 and ins["range"]["days"] == 7


def test_analytics_respect_workspace_scope(tmp_path):
    c, a, b = seeded(tmp_path)
    patch_lead(c, a["id"], workspace="client-x")
    k = c.get(f"/api/analytics/overview?from={FROM}&to={TO}", headers={"X-Workspace": "main"}).json()["kpis"]
    assert k["replied"]["value"] == 0 and k["emails_sent"]["value"] == 0
    k = c.get(f"/api/analytics/overview?from={FROM}&to={TO}", headers={"X-Workspace": "client-x"}).json()["kpis"]
    assert k["replied"]["value"] == 1


# ---------------------------------------------------------------- campaigns
def test_campaign_outcomes_on_list_and_detail(tmp_path):
    c, a, b = seeded(tmp_path)
    row = next(x for x in c.get("/api/campaigns").json() if x["id"] == "camp1")
    o = row["outcomes"]
    assert (o["leads"], o["emails_sent"], o["leads_emailed"], o["opened"], o["replied"], o["interested"], o["meetings"]) == (1, 3, 1, 1, 1, 1, 1)
    assert o["rates"] == {"open": 100.0, "reply": 100.0, "interested": 100.0, "meeting": 100.0}
    assert c.get("/api/campaigns/camp1").json()["outcomes"] == o


def test_campaign_leads_paged_and_filtered(tmp_path):
    c, a, b = seeded(tmp_path)
    patch_lead(c, b["id"], campaign_id="camp1")
    assert isinstance(c.get("/api/campaigns/camp1/leads").json(), list)          # older callers
    p = c.get("/api/campaigns/camp1/leads?page=1&size=1").json()
    assert p["total"] == 2 and p["pages"] == 2 and len(p["items"]) == 1 and set(p["stages"]) == {"call_done", "contacted"}
    assert [x["company"] for x in c.get("/api/campaigns/camp1/leads?page=1&stage=contacted").json()["items"]] == ["Beta"]
    assert [x["company"] for x in c.get("/api/campaigns/camp1/leads?page=1&q=acme").json()["items"]] == ["Acme"]
    assert [x["company"] for x in c.get("/api/campaigns/camp1/leads?page=1&state=replied").json()["items"]] == ["Acme"]
    assert c.get("/api/campaigns/camp1/leads?page=2&size=1&sort=company").json()["items"][0]["company"] == "Beta"


# ---------------------------------------------------------------- outbox
def test_outbox_server_paging_and_filters(tmp_path):
    c, a, b = seeded(tmp_path)
    assert len(c.get("/api/outbox").json()) == 5                                   # older callers: every row
    p = c.get("/api/outbox?page=1&size=2").json()
    assert p["total"] == 5 and p["pages"] == 3 and len(p["items"]) == 2 and p["items"][0]["step"] == "One-off email"
    assert "body" not in p["items"][0] and p["items"][0]["company"] == "Acme" and p["items"][1]["company"] == "Acme"
    q = lambda s: c.get("/api/outbox?page=1&" + s).json()
    assert q("status=practice")["total"] == 1 and q("status=sent")["total"] == 4
    assert q("step=Email%202")["total"] == 2 and q("kind=manual")["total"] == 1 and q("kind=sequence")["total"] == 4
    assert q(f"from={FROM}&to=2026-11-04")["total"] == 3 and q("campaign_id=camp1")["total"] == 3
    assert q("q=acme")["total"] == 3 and q("campaign_id=camp1")["items"][0]["campaign"] == "Pilot"
    assert set(p["steps"]) == {"Email 1", "Email 2", "One-off email"}


def test_outbox_message_full_body_and_designed_html(tmp_path):
    c, a, b = seeded(tmp_path)
    key = c.get("/api/outbox?page=1&kind=manual").json()["items"][0]["key"]
    m = c.get("/api/outbox/message", params={"key": key}).json()
    assert "This is One-off email." in m["body"] and m["html"].lstrip().lower().startswith("<!doctype html") and "One-off email" in m["html"]
    assert m["lead_id"] == a["id"] and m["manual"] is True
    assert c.get("/api/outbox/message", params={"key": "nope"}).status_code == 404


# ---------------------------------------------------------------- send for real
def test_live_sending_switch_needs_typed_phrase_and_is_audited(tmp_path):
    c = make_client(tmp_path)
    connect_email(c)
    c.put("/api/settings", json={"public_base_url": "https://outreach.example.in", **IDENTITY})
    assert c.get("/api/settings/live-sending").json()["on"] is False
    r = c.post("/api/settings/live-sending", json={"on": True, "confirm": "yes"})
    assert r.status_code == 400 and "SEND FOR REAL" in r.json()["detail"]
    r = c.post("/api/settings/live-sending", json={"on": True, "confirm": "send for real"})
    assert r.status_code == 200 and r.json()["on"] is True and r.json()["changed"]["by"] == "This computer"
    assert r.json()["changed"]["on"] is True and r.json()["changed"]["at"]
    r = c.post("/api/settings/live-sending", json={"on": False})                    # off: one click, no phrase
    assert r.json()["on"] is False and r.json()["changed"]["on"] is False
    actions = [x["action"] for x in c.get("/api/audit").json()["items"]]
    assert actions[:2] == ["live_sending.off", "live_sending.on"]
    assert "system_status" not in c.get("/api/settings").json()


def test_live_sending_blocked_until_ready_and_owner_only(tmp_path):
    c = make_client(tmp_path)
    r = c.post("/api/settings/live-sending", json={"on": True, "confirm": "SEND FOR REAL"})
    assert r.status_code == 400 and "stays off" in r.json()["detail"]
    c2, owner, caller = owner_and_caller(tmp_path)
    assert c2.post("/api/settings/live-sending", json={"on": False}, headers=caller).status_code == 403
    assert c2.get("/api/settings/live-sending", headers=caller).status_code == 200


def test_settings_save_audits_old_and_new_values_but_never_secrets(tmp_path):
    c = make_client(tmp_path)
    c.put("/api/settings", json={"sender_name": "Priya", "webhook_secret": "super-secret-value-123", "daily_cap": 30})
    row = c.get("/api/audit?action=settings").json()["items"][0]
    assert row["action"] == "settings.save" and "webhook_secret: (empty) -> (hidden, changed)" in row["detail"]
    assert "sender_name: (empty) -> Priya" in row["detail"]
    assert "super-secret-value-123" not in str(store_of(c).read("audit"))


# ---------------------------------------------------------------- audit trail
def test_audit_rows_for_each_action_type(tmp_path):
    c, owner, caller = owner_and_caller(tmp_path)
    s = store_of(c)
    acme = next(l for l in c.get("/api/leads", headers=owner).json() if l["company"] == "Acme")
    beta = next(l for l in c.get("/api/leads", headers=owner).json() if l["company"] == "Beta")
    assert c.post(f"/api/leads/{acme['id']}/stage", json={"stage": "verified"}, headers=caller).status_code == 200
    c.patch(f"/api/leads/{acme['id']}", json={"title": "Head of Talent"}, headers=owner)
    lst = c.post("/api/lists", json={"name": "Noida", "lead_ids": [acme["id"]]}, headers=owner).json()
    c.post(f"/api/lists/{lst['id']}/start-email", json={}, headers=owner)
    c.post(f"/api/lists/{lst['id']}/start-linkedin", json={}, headers=owner)
    c.delete(f"/api/lists/{lst['id']}", headers=owner)
    ws = c.post("/api/workspaces", json={"name": "Client X"}, headers=owner).json()
    c.post(f"/api/workspaces/{ws['id']}/move", json={"lead_ids": [beta["id"]]}, headers=owner)
    c.post(f"/api/workspaces/main/move", json={"lead_ids": [beta["id"]]}, headers=owner)
    c.request("DELETE", f"/api/workspaces/{ws['id']}", json={"confirm_name": "Client X"}, headers=owner)
    c.put("/api/users/meena@example.com", json={"role": "viewer"}, headers=owner)
    c.post("/api/users", json={"email": "ravi@example.com", "name": "Ravi", "role": "caller", "password": "long-password-2",
                                   "all_workspaces": True}, headers=owner)
    c.delete("/api/users/ravi@example.com", headers=owner)
    c.put("/api/linkedin-agent", json={"enabled": True}, headers=owner)
    c.put("/api/linkedin-agent", json={"enabled": False, "daily_messages": 10}, headers=owner)
    camp = c.post("/api/campaigns", json={"name": "Pilot"}, headers=owner).json()
    c.delete(f"/api/campaigns/{camp['id']}", headers=owner)
    assert c.post(f"/api/leads/{beta['id']}/erase", json={"confirm": True}, headers=owner).status_code == 200
    items = c.get("/api/audit?limit=100", headers=owner).json()["items"]
    actions = {r["action"] for r in items}
    assert {"stage.change", "contact.edit", "bulk.list_email", "bulk.list_linkedin", "list.delete", "workspace.create",
            "bulk.workspace_move", "workspace.delete", "user.change", "user.add", "user.remove", "linkedin_agent.on",
            "linkedin_agent.off", "linkedin_agent.settings", "campaign.delete", "lead.erase"} <= actions
    stage = next(r for r in items if r["action"] == "stage.change")
    assert stage["by"] == "Meena" and stage["target"] == "Acme" and "new → verified" in stage["detail"]
    assert all(set(r) >= {"at", "by", "action", "target", "detail"} for r in items)
    erase = next(r for r in items if r["action"] == "lead.erase")
    assert erase["target"].startswith("erased:") and "Beta" not in str(erase) and "sam@beta.in" not in str(s.read("audit"))
    assert next(r for r in items if r["action"] == "user.change")["detail"] == "role caller -> viewer"
    # the older contact-edit views keep working and see only contact edits
    log = c.get("/api/change-log", headers=owner).json()
    assert len(log) == 1 and log[0]["field"] == "title"
    assert c.get(f"/api/leads/{acme['id']}/changes", headers=owner).json()[0]["label"] == "Title"
    assert c.get("/api/intelligence", headers=owner).json()["quality"]["manual_corrections"] == 1


def test_audit_filters_csv_and_owner_only(tmp_path):
    c, owner, caller = owner_and_caller(tmp_path)
    c.put("/api/settings", json={"daily_cap": 25}, headers=owner)
    c.post("/api/workspaces", json={"name": "Client Y"}, headers=owner)
    assert c.get("/api/audit", headers=caller).status_code == 403
    assert c.get("/api/audit.csv", headers=caller).status_code == 403
    assert c.get("/api/system/health", headers=caller).status_code == 403
    d = c.get("/api/audit?action=workspace", headers=owner).json()
    assert d["total"] == 1 and d["items"][0]["target"] == "Client Y" and "workspace.create" in d["actions"]
    assert c.get("/api/audit?by=nobody", headers=owner).json()["total"] == 0
    assert c.get("/api/audit?q=client%20y", headers=owner).json()["total"] == 1
    assert c.get("/api/audit?from=2000-01-01&to=2000-01-02", headers=owner).json()["total"] == 0
    csv_text = c.get("/api/audit.csv?action=settings", headers=owner)
    assert csv_text.status_code == 200 and csv_text.headers["content-type"].startswith("text/csv")
    assert "settings.save" in csv_text.text and "IST" in csv_text.text and "Client Y" not in csv_text.text


# ---------------------------------------------------------------- health
def test_system_health_reports_jobs_queue_store_and_version(tmp_path, monkeypatch):
    c, a, b = seeded(tmp_path)
    s = store_of(c)
    monkeypatch.setenv("OUTREACH_GIT_COMMIT", "abc1234")
    system_health.mark(s, "imap_poll", True, "2 processed")
    system_health.mark(s, "autorun", False, "failed: OSError")
    patch_lead(c, a["id"], sequence={"enrolled_at": "2026-11-01T04:00:00+00:00", "sent_days": [0], "stopped": None, "in_flight": {"day": 3}})
    s.update("settings", lambda x: {**x, "sending_paused": {"reason": "too many bounces"}, "linkedin_agent_accounts": {"me": {"at": "2026-11-06T04:00:00+00:00"}}})
    c.post("/api/sequences/run", json={})
    h = c.get("/api/system/health").json()
    assert h["version"] == {"commit": "abc1234", "from": "OUTREACH_GIT_COMMIT"} and h["store"]["kind"] == "JSON file"
    assert h["jobs"]["imap_poll"]["ok"] is True and h["jobs"]["imap_poll"]["summary"] == "2 processed"
    assert h["jobs"]["autorun"]["ok"] is False and h["jobs"]["autorun"]["failures"] == 1
    assert h["jobs"]["sequence_run"]["summary"].startswith("by hand")
    assert h["queue"]["in_flight"] == 1 and h["queue"]["active_sequences"] == 1 and h["queue"]["outbox_rows"] == 5
    assert h["sending"]["paused"]["reason"] == "too many bounces" and h["ok"] is False
    assert h["linkedin_agent"]["accounts"][0]["account"] == "me" and h["linkedin_agent"]["accounts"][0]["last_seen"]
    assert set(h["backups"]) >= {"latest_at", "age_seconds", "count"}


def test_health_marks_are_throttled_but_failures_always_saved(tmp_path):
    c = make_client(tmp_path)
    s = store_of(c)
    t0 = datetime(2026, 11, 6, 4, 0, tzinfo=timezone.utc)
    system_health.mark(s, "reminders", True, now=t0)
    system_health.mark(s, "reminders", True, now=t0.replace(minute=1))
    assert s.read("settings")["system_status"]["reminders"]["at"] == t0.isoformat(timespec="seconds")     # not rewritten after 1 min
    assert c.get("/api/system/health").json()["jobs"]["reminders"]["at"] == t0.replace(minute=1).isoformat(timespec="seconds")
    system_health.mark(s, "reminders", False, "boom", now=t0.replace(minute=2))
    assert s.read("settings")["system_status"]["reminders"]["ok"] is False


def test_autorun_tick_marks_health(tmp_path):
    from app.autorun import tick
    c = make_client(tmp_path)
    s = store_of(c)
    tick(s, now=datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc), sender=lambda m: {"status": "dry_run"})
    status = s.read("settings")["system_status"]
    assert status["autorun"]["ok"] is True and "sequence_run" in status


# ---------------------------------------------------------------- lists
def test_list_rates_and_csv_export(tmp_path):
    c, a, b = seeded(tmp_path)
    patch_lead(c, b["id"], sequence={"enrolled_at": "2026-11-01T04:00:00+00:00", "sent_days": [0], "stopped": None},
               linkedin_seq={"done": ["connect"], "accepted_at": "2026-11-03T04:00:00+00:00"})
    lst = c.post("/api/lists", json={"name": "Both", "lead_ids": [a["id"], b["id"]]}).json()
    stats = next(r for r in c.get("/api/lists").json() if r["id"] == lst["id"])["stats"]
    assert (stats["open_rate"], stats["reply_rate"], stats["accept_rate"]) == (50.0, 50.0, 100.0)
    r = c.get("/api/export/lists.csv")
    assert r.status_code == 200 and "open_rate" in r.text.splitlines()[0] and "Both" in r.text


def test_outbox_send_now_row_stays_a_sequence_email(tmp_path):
    # QA sweep: "Send email now" marks its row manual (a person chose it); the Outbox listed it as "one-off" and the
    # "Sequence only" filter hid it.
    c, a, b = seeded(tmp_path)
    store_of(c).update("outbox", lambda rows: [*rows, outbox_row(a["id"], "2026-11-05T06:00:00+00:00", step="Email 3", variant="A",
                                                               manual=True, by="Meena")])
    q = lambda s: c.get("/api/outbox?page=1&" + s).json()
    assert q("kind=manual")["total"] == 1 and q("kind=sequence")["total"] == 5
    row = next(x for x in q("step=Email%203")["items"])
    assert row["manual"] is False and row["by"] == "Meena"


def test_out_of_office_is_not_a_reply_anywhere(tmp_path):
    """QA sweep: an out-of-office pasted on a lead counted as 'Replied' on the Dashboard/Analytics (7) and made the
    company an 'Engaged account', while Insights and Intelligence said 6. Practice emails counted as 'sent' on Insights
    (15) but not on the Dashboard (0)."""
    c = TestClient(create_app(data_file=tmp_path / "s.json"))
    c.post("/api/leads/import", json={"csv": "company,email,website\nTele Hub,ravi@telehub.in,telehub.in\nAcme,asha@acme.in,acme.in\n",
                                      "source": "manual"})
    ids = {l["company"]: l["id"] for l in c.get("/api/leads").json()}
    c.post(f"/api/leads/{ids['Tele Hub']}/reply", json={"text": "I am out of office until 15 November."})
    c.post(f"/api/leads/{ids['Acme']}/reply", json={"text": "Not interested, thanks."})
    over = c.get("/api/analytics/overview?days=7").json()
    assert over["kpis"]["replied"]["value"] == 1
    comp = {x["name"]: x for x in c.get("/api/companies").json()["items"]}
    assert comp["Tele Hub"]["replies"] == 0 and comp["Tele Hub"]["engaged"] is False
    assert comp["Acme"]["replies"] == 1 and comp["Acme"]["engaged"] is False       # everyone there said no
    assert c.get("/api/intelligence").json()["timing"]["replies_measured"] == 1


def test_pasted_unsubscribe_reply_counts_as_unsubscribed(tmp_path):
    """QA sweep: 'Please remove me from your list' pasted on a lead moved it to Unsubscribed, but the Analytics
    overview said Unsubscribed 0 (only the unsubscribe link was counted)."""
    c = TestClient(create_app(data_file=tmp_path / "s.json"))
    c.post("/api/leads/import", json={"csv": "company,email,website\nSundaram,ravi@sundaram.in,sundaram.in\n", "source": "manual"})
    lid = c.get("/api/leads").json()[0]["id"]
    c.post(f"/api/leads/{lid}/reply", json={"text": "Please remove me from your list and do not email again."})
    assert c.get(f"/api/leads/{lid}").json()["stage"] == "unsubscribed"
    assert c.get("/api/analytics/overview?days=7").json()["kpis"]["unsubscribed"]["value"] == 1
