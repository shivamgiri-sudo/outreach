""""Why was this sent?": every outbound row records who (a person) or which rule (the System) sent it, who started the
lead on its sequence / flow, the mode and the settings that allowed it; old rows are labelled, never guessed; the
Outbox filters, the internal emails list and the CSV export carry it, owner/admin only and workspace-scoped."""
import csv
import io
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import linkedin_seq, provenance
from app.campaigns import _enrol
from app.main import create_app
from app.outbox_view import search
from app.routes_extension import _agent_origin
from tests.flow_helpers import T0, chain, email_node, lead, make_client, make_flow, node, patch_lead, store_of, tick
from tests.flow_helpers import enroll as flow_enroll

RUN_AT = "2026-11-03T05:30:00+00:00"           # Tuesday 11:00 IST
PASSWORD = "admin-pass-123"


def outbox(c):
    return store_of(c).read("outbox")


def test_scheduled_sequence_step_is_automatic_with_rule_enrolment_and_settings(tmp_path):
    c = make_client(tmp_path)
    acme = lead(c)["id"]
    assert c.post(f"/api/leads/{acme}/enroll").status_code == 200
    seq = store_of(c).read("leads")
    state = next(l for l in seq if l["id"] == acme)["sequence"]
    assert state["enrolled_by"] == "This computer" and state["enrolled_via"] == "by hand"
    sent = c.post("/api/sequences/run", json={"now": RUN_AT}).json()
    assert sent["sent"] >= 1, sent
    row = next(m for m in outbox(c) if m["lead_id"] == acme)
    o = row["origin"]
    assert o["type"] == "automatic" and o["trigger"] == "sequence" and o["actor"]["name"] == "System"
    assert o["rule"]["sequence_id"] == "default" and o["rule"]["step"] == row["step"] and "day" in o["rule"]
    assert o["enrolment"]["by"] == "This computer" and o["enrolment"]["via"] == "by hand" and o["enrolment"]["at"]
    assert o["mode"] == "practice" and o["settings"]["live_sending"] is False
    assert o["settings"]["daily_cap"] and o["settings"]["track_email"] is True and "Mon–Fri" in o["settings"]["business_hours"]
    assert o["settings"]["consent_basis"].startswith("cold email rule")
    item = c.get("/api/outbox", params={"page": 1, "origin": "automatic"}).json()["items"][0]
    assert item["origin_type"] == "automatic" and item["origin_label"] == "Automatic"
    assert any("Put on it by This computer, by hand" in line for line in item["why"])
    assert c.get("/api/outbox", params={"page": 1, "origin": "manual"}).json()["total"] == 0


def test_send_now_and_one_off_are_manual_with_the_person(tmp_path):
    c = make_client(tmp_path)
    acme, beta = lead(c)["id"], lead(c, "Beta")["id"]
    c.post(f"/api/leads/{acme}/enroll")
    assert c.post(f"/api/leads/{acme}/email-now").status_code == 200
    now_row = next(m for m in outbox(c) if m["lead_id"] == acme)
    assert now_row["origin"]["type"] == "manual" and now_row["origin"]["trigger"] == "send_now"
    assert now_row["origin"]["actor"]["name"] == "This computer" and now_row["origin"]["enrolment"]["via"] == "by hand"
    assert "not applied" in now_row["origin"]["settings"]["business_hours"]
    r = c.post(f"/api/leads/{beta}/email-compose", json={"subject": "Hi {{company}}", "body": "A short note."})
    assert r.status_code == 200, r.text
    one = next(m for m in outbox(c) if m["lead_id"] == beta)
    assert one["origin"]["type"] == "manual" and one["origin"]["trigger"] == "one_off" and one["origin"]["enrolment"] is None
    page = c.get("/api/outbox", params={"page": 1, "origin": "manual"}).json()
    assert page["total"] == 2 and all(i["origin_type"] == "manual" for i in page["items"])
    assert c.get("/api/outbox", params={"page": 1, "kind": "by_person"}).json()["total"] == 2


def test_flow_email_is_automatic_with_flow_rule_and_who_enrolled(tmp_path):
    c = make_client(tmp_path)
    flow = make_flow(c, [node("t", "trigger.manual"), email_node()], chain("t", "mail"), name="Welcome")
    acme = lead(c)["id"]
    assert flow_enroll(c, flow, acme)["enrolled"] == [acme]
    run = store_of(c).read("flow_runs")[0]
    assert run["enrolled_by"] == "This computer" and run["enrolled_via"] == "by hand"
    tick(c, T0)
    row = next(m for m in outbox(c) if m.get("flow_id"))
    o = row["origin"]
    assert o["type"] == "automatic" and o["trigger"] == "flow"
    assert o["rule"]["flow_id"] == flow["id"] and o["rule"]["flow_name"] == "Welcome" and o["rule"]["node_id"] == "mail"
    assert o["enrolment"]["by"] == "This computer" and o["enrolment"]["via"] == "by hand"


def test_flow_trigger_enrolment_is_the_system(tmp_path):
    from app.flow_engine import enroll
    c = make_client(tmp_path)
    flow = make_flow(c, [node("t", "trigger.manual"), email_node()], chain("t", "mail"))
    acme = lead(c)["id"]
    enroll(store_of(c), flow, acme, T0, why="reply: interested")
    run = store_of(c).read("flow_runs")[0]
    assert run["enrolled_by"] == "System" and run["enrolled_via"] == "flow trigger" and run["enrolled_detail"] == "reply: interested"


def test_campaign_bulk_and_list_enrolment_are_attributed(tmp_path):
    c = make_client(tmp_path)
    store = store_of(c)
    acme, beta = lead(c)["id"], lead(c, "Beta")["id"]
    report = {"enrolled": 0, "errors": []}
    _enrol(store, {"id": "camp1", "name": "Spring", "rules": {}}, [acme], report)
    assert report["enrolled"] == 1
    s = next(l for l in store.read("leads") if l["id"] == acme)["sequence"]
    assert s["enrolled_by"] == "System" and s["enrolled_via"] == "campaign auto-enrol" and s["enrolled_campaign_id"] == "camp1"
    r = c.post("/api/leads/bulk", json={"lead_ids": [beta], "action": "enroll"})
    assert r.status_code == 200, r.text
    s = next(l for l in store.read("leads") if l["id"] == beta)["sequence"]
    assert s["enrolled_by"] == "This computer" and s["enrolled_via"] == "bulk action"
    c.post("/api/sequences/run", json={"now": RUN_AT})
    camp_row = next(m for m in outbox(c) if m["lead_id"] == acme)
    assert camp_row["origin"]["enrolment"]["via"] == "campaign auto-enrol" and camp_row["origin"]["rule"]["campaign_id"] == "camp1"


def test_list_enrolment_records_the_person(tmp_path):
    from app import lists
    c = make_client(tmp_path)
    store = store_of(c)
    acme = lead(c)["id"]
    made = lists.create(store, "Hot", [acme], "Asha", "test")
    lists.start_email(store, made["id"], "Asha")
    s = next(l for l in store.read("leads") if l["id"] == acme)["sequence"]
    assert s["enrolled_by"] == "Asha" and s["enrolled_via"] == "bulk action" and s["enrolled_detail"] == "list Hot"


def test_whatsapp_by_hand_and_by_flow(tmp_path):
    from app.whatsapp import send
    c = make_client(tmp_path)
    acme = lead(c)["id"]
    patch_lead(c, acme, consent={"channel": "whatsapp", "at": "2026-11-01T00:00:00+00:00"})
    r = c.post(f"/api/leads/{acme}/whatsapp", json={"template": "first_after_yes"})
    assert r.status_code == 200, r.text
    row = store_of(c).read("whatsapp_outbox")[-1]
    assert row["origin"]["type"] == "manual" and row["origin"]["trigger"] == "whatsapp_by_hand"
    assert row["origin"]["settings"]["consent_basis"] == "recorded yes for WhatsApp" and row["origin"]["mode"] == "practice"
    send(store_of(c), acme, "first_after_yes", "", T0, "http://x", origin={"trigger": "whatsapp_auto", "rule": {"flow_id": "f1", "flow_name": "Nurture"}})
    auto = store_of(c).read("whatsapp_outbox")[-1]
    assert auto["origin"]["type"] == "automatic" and auto["origin"]["actor"]["name"] == "System"
    assert provenance.short_who(auto) == "System (flow Nurture)"
    detail = {i.get("detail") for i in c.get(f"/api/leads/{acme}/activity").json() if i["type"] == "whatsapp"}
    assert "System (flow Nurture)" in detail and any(d and d.startswith("by ") for d in detail)


def test_linkedin_by_hand_and_by_agent(tmp_path):
    c = make_client(tmp_path)
    store = store_of(c)
    acme = lead(c)["id"]
    patch_lead(c, acme, linkedin_url="https://www.linkedin.com/in/ravi")
    assert c.post(f"/api/leads/{acme}/linkedin/start").status_code == 200
    state = next(l for l in store.read("leads") if l["id"] == acme)["linkedin_seq"]
    assert state["enrolled_by"] == "This computer" and state["enrolled_via"] == "by hand"
    r = c.post(f"/api/leads/{acme}/linkedin/sent", json={"step": 1})
    assert r.status_code == 200, r.text
    ev = [e for e in r.json()["lead"]["timeline"] if e.get("text") == "LinkedIn step 1 sent"][-1]
    assert ev["origin"]["type"] == "manual" and ev["origin"]["trigger"] == "linkedin_by_hand" and ev["by"] == "This computer"
    assert ev["origin"]["enrolment"]["via"] == "by hand"
    patch_lead(c, acme, linkedin_seq={**state, "accepted_at": "2026-11-03T05:00:00+00:00", "status": "messaging", "done": [1]})
    request = SimpleNamespace(state=SimpleNamespace(user={"kind": "extension", "name": "Chrome", "email": "asha@example.com"}))
    fresh = next(l for l in store.read("leads") if l["id"] == acme)
    origin = _agent_origin(store, fresh, 2, "asha-li", request)
    out = linkedin_seq.mark_sent(store, acme, 2, origin=origin)
    ev = [e for e in out["lead"]["timeline"] if e.get("text") == "LinkedIn step 2 sent"][-1]
    assert ev["origin"]["type"] == "automatic" and ev["origin"]["trigger"] == "linkedin_agent"
    assert ev["origin"]["actor"] == {"name": "System", "email": "", "account": "asha-li"} and ev["origin"]["rule"]["account"] == "asha-li"


def test_calls_record_the_person(tmp_path):
    c = make_client(tmp_path)
    acme = lead(c)["id"]
    r = c.post(f"/api/leads/{acme}/activity", json={"type": "call", "outcome": "connected", "notes": "Spoke"})
    assert r.status_code == 200, r.text
    row = r.json()["activity"]
    assert row["origin"]["type"] == "manual" and row["origin"]["trigger"] == "call_logged" and row["origin"]["actor"]["name"]


def test_internal_emails_are_listed_with_why(tmp_path):
    from app.reminders import _log_transactional
    c = make_client(tmp_path)
    store = store_of(c)
    _log_transactional(store, "asha@example.com", "[Outreach] Hot lead", "text", "internal", None, {"status": "sent"})
    store.update("transactional_outbox", lambda rows: [*rows, {"id": "old1", "to": "x@example.com", "subject": "Old", "kind": "internal",
                                                             "status": "sent", "at": "2026-10-01T10:00:00+05:30"}])
    got = c.get("/api/outbox/internal").json()
    assert got["total"] == 2
    old, new = got["items"][0], got["items"][1]
    assert old["origin_type"] == "unknown" and old["why"][0].startswith(provenance.LEGACY_LABEL)
    assert new["origin_type"] == "automatic" and new["kind_label"] == "Alert" and "automatic alert" in new["why"][0]


def test_password_reset_is_logged_without_the_link(tmp_path):
    from app import auth
    from app.password_reset import request_reset
    c = make_client(tmp_path)
    store = store_of(c)
    store.update("settings", lambda s: s)
    auth_user = {"email": "asha@example.com", "name": "Asha", "role": "caller"}
    original = auth.find_user
    auth.find_user = lambda st, email: auth_user if email == "asha@example.com" else None
    try:
        request_reset(store, "asha@example.com", "https://x", lambda m: {"status": "sent"})
    finally:
        auth.find_user = original
    row = store.read("transactional_outbox")[-1]
    assert row["kind"] == "password_reset" and "token" not in row["body"] and row["origin"]["trigger"] == "password_reset"


def test_old_rows_are_labelled_and_never_guessed(tmp_path):
    c = make_client(tmp_path)
    store = store_of(c)
    acme, beta = lead(c)["id"], lead(c, "Beta")["id"]
    store.update_lead(acme, lambda l: {**l, "timeline": [*l["timeline"], {"at": "2026-10-01T05:00:00+00:00", "kind": "sequence",
                                                                           "text": "Enrolled in email sequence by campaign Old"}]})
    store.update("outbox", lambda rows: [*rows,
        {"lead_id": acme, "to": "ravi@acme.com", "subject": "Old A", "step": "Email 1", "status": "sent", "sent_at": "2026-10-02T05:00:00+00:00"},
        {"lead_id": beta, "to": "sam@beta.in", "subject": "Old B", "step": "One-off email", "variant": "manual", "manual": True,
         "by": "Asha", "status": "sent", "sent_at": "2026-10-03T05:00:00+00:00"}])
    items = {i["subject"]: i for i in search(store, page=1)["items"]}
    a, b = items["Old A"], items["Old B"]
    assert a["origin_type"] == "unknown" and a["origin_legacy"] and a["origin_label"] == "Before tracking"
    assert a["why"][0] == provenance.LEGACY_LABEL + "." and any("Enrolled in email sequence by campaign Old" in x for x in a["why"])
    assert not any("System" in x for x in a["why"])
    assert b["origin_type"] == "manual" and b["origin_actor"] == "Asha" and any("sent by Asha" in x for x in b["why"])
    assert search(store, page=1, origin="unknown")["total"] == 1


def _owner_setup(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password=PASSWORD), client=("203.0.113.9", 50000))
    ho = {"Authorization": f"Bearer {c.post('/api/login', json={'password': PASSWORD}).json()['token']}"}
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nAcme,Ravi,ravi@acme.com\nBeta,Sam,sam@beta.in\n",
                                      "source": "apollo"}, headers=ho)
    return c, ho


def test_export_has_provenance_columns_is_audited_and_scoped(tmp_path):
    c, ho = _owner_setup(tmp_path)
    leads = {l["company"]: l["id"] for l in c.get("/api/leads", headers=ho).json()}
    for lid in leads.values():
        assert c.post(f"/api/leads/{lid}/enroll", headers=ho).status_code == 200
    c.post("/api/sequences/run", json={"now": RUN_AT}, headers=ho)
    c.post("/api/sequences/run", json={"now": "2026-11-03T05:31:00+00:00"}, headers=ho)
    w = c.post("/api/workspaces", json={"name": "Client One"}, headers=ho).json()
    assert c.post(f"/api/workspaces/{w['id']}/move", json={"lead_ids": [leads["Acme"]]}, headers=ho).status_code == 200
    c.post("/api/users", json={"email": "boss@one.example", "name": "Boss", "role": "admin", "password": "long-password-1",
                               "workspaces": [w["id"]]}, headers=ho)
    c.post("/api/users", json={"email": "cal@one.example", "name": "Cal", "role": "caller", "password": "long-password-1"}, headers=ho)
    ha = {"Authorization": f"Bearer {c.post('/api/login', json={'email': 'boss@one.example', 'password': 'long-password-1'}).json()['token']}"}
    hc = {"Authorization": f"Bearer {c.post('/api/login', json={'email': 'cal@one.example', 'password': 'long-password-1'}).json()['token']}"}

    r = c.get("/api/outbox/export.csv", params={"origin": "automatic"}, headers=ho)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))
    for col in ("origin_type", "trigger", "actor_name", "rule_sequence", "rule_step", "enrolled_by", "enrolled_via", "mode",
                "live_sending", "daily_cap", "business_hours", "track_email", "consent_basis", "legacy", "why"):
        assert col in rows[0], col
    assert len(rows) == 2 and {x["origin_type"] for x in rows} == {"automatic"} and {x["trigger"] for x in rows} == {"sequence"}
    assert {x["enrolled_via"] for x in rows} == {"by hand"} and all(x["why"] for x in rows)
    logged = [a for a in c.app.state.store.read("audit") if a.get("action") == "export.download" and a.get("target") == "outbox-audit.csv"]
    assert logged and logged[-1]["detail"].startswith("2 rows") and "origin=automatic" in logged[-1]["detail"]

    scoped = list(csv.DictReader(io.StringIO(c.get("/api/outbox/export.csv", headers=ha).text.lstrip("﻿"))))
    assert [x["company"] for x in scoped] == ["Acme"]
    assert c.get("/api/outbox/export.csv", params={"origin": "manual"}, headers=ho).text.count("\n") == 1     # header only
    assert c.get("/api/outbox/export.csv", headers=hc).status_code == 403
    assert c.get("/api/outbox/internal", headers=hc).status_code == 403
    assert c.get("/api/outbox/internal", headers=ha).status_code == 200
