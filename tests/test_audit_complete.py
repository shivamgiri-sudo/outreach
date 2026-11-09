"""Complete change log: settings diffs (secrets masked), deletions and undo, AI-drafted messages, the hash chain
(intact / edited row / deleted row / trimming), the consent record per lead and the audit pack."""
import io
import json
import zipfile

from fastapi.testclient import TestClient

from app import audit, audit_chain
from app.main import create_app
from tests.flow_helpers import CSV, FakeTransport, lead, make_client, store_of

AI_TEXT = "Hi Ravi, the first role is free and takes ten minutes to set up. Shall I send you the link?"


def owner_and_caller(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    owner = {"Authorization": "Bearer " + c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]}
    c.post("/api/users", json={"email": "meena@example.com", "name": "Meena", "role": "caller", "password": "long-password-1"}, headers=owner)
    caller = {"Authorization": "Bearer " + c.post("/api/login", json={"email": "meena@example.com", "password": "long-password-1"}).json()["token"]}
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"}, headers=owner)
    return c, owner, caller


def find(c, headers, company="Acme"):
    return next(l for l in c.get("/api/leads", headers=headers).json() if l["company"] == company)


def rows(c, action):
    return [r for r in store_of(c).read("audit") if r.get("action") == action]


# ---------------------------------------------------------------- settings diffs
def test_settings_changes_record_old_and_new_with_secrets_masked(tmp_path):
    c = make_client(tmp_path)
    c.put("/api/settings", json={"sender_name": "Priya", "daily_cap": 30})
    c.put("/api/settings", json={"sender_name": "Asha", "webhook_secret": "very-secret-abc-999"})
    row = rows(c, "settings.save")[-1]
    assert {"key": "sender_name", "old": "Priya", "new": "Asha"} in row["changes"]
    assert {"key": "webhook_secret", "old": "(empty)", "new": "(hidden, changed)"} in row["changes"]
    assert "daily_cap" not in row["detail"]                     # unchanged keys are not listed
    c.put("/api/settings", json={"sender_name": "Asha"})
    assert len(rows(c, "settings.save")) == 2                   # nothing changed, nothing written
    c.put("/api/integrations/llm", json={"mode": "api", "values": {"provider": "anthropic", "api_key": "sk-ant-secret-777"}})
    integ = rows(c, "integration.save")[-1]
    assert integ["target"] == "Connection: llm" and "mode: (empty) -> api" in integ["detail"] and "(hidden" in integ["detail"]
    c.put("/api/reports/sla-targets", json={"first_touch_hours": 6, "reply_hours": 3})
    sla = rows(c, "settings.change")[-1]
    assert "sla_first_touch_hours" in sla["detail"] and "-> 6" in sla["detail"]
    c.put("/api/linkedin-agent", json={"daily_messages": 10})
    agent = rows(c, "linkedin_agent.settings")[-1]
    assert {"key": "daily_messages", "old": "25", "new": "10"} in agent["changes"]
    dump = json.dumps(store_of(c).read("audit"))
    assert "very-secret-abc-999" not in dump and "sk-ant-secret-777" not in dump
    shown = c.get("/api/audit?action=settings").json()["items"][0]
    assert shown["changes"] and shown["seq"]


# ---------------------------------------------------------------- deletions and undo
def test_deletions_and_import_undo_are_recorded(tmp_path):
    c = make_client(tmp_path, csv="")
    imp = c.post("/api/import/commit", json={"csv": CSV, "source": "apollo"}).json()
    import_id = imp.get("import_id") or imp.get("id") or c.get("/api/import/history").json()[0]["id"]
    assert c.post(f"/api/import/{import_id}/undo").status_code == 200
    undo = rows(c, "import.undo")[-1]
    assert "2 lead(s) removed" in undo["detail"] and len(undo["lead_ids"]) == 2
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    ids = [l["id"] for l in c.get("/api/leads").json()]
    assert c.post("/api/leads/bulk", json={"lead_ids": ids, "action": "delete"}).json()["ok"] == 2
    bulk = rows(c, "bulk.delete")[-1]
    assert bulk["target"] == "2 lead(s)" and set(bulk["lead_ids"]) == set(ids) and "Acme" in bulk["detail"]
    r = c.post("/api/workflows", json={"name": "Note it", "trigger": {"type": "reply", "value": "interested"}, "action": {"type": "note", "value": "n"}})
    assert c.delete(f"/api/workflows/{r.json()['id']}").status_code == 200
    flow = c.post("/api/flows", json={"name": "Gone soon", "nodes": [], "edges": []})
    if flow.status_code == 200:
        assert c.delete(f"/api/flows/{flow.json()['id']}").status_code == 200
        assert rows(c, "flow.delete")[-1]["target"] == "Gone soon"
    assert rows(c, "workflow.delete")[-1]["target"] == "Note it"
    actions = {r["action"] for r in c.get("/api/audit?limit=200").json()["items"]}
    assert {"import.undo", "bulk.delete", "workflow.delete", "lead.delete"} <= actions


# ---------------------------------------------------------------- AI-drafted
def test_ai_drafted_messages_are_marked_with_who_approved(tmp_path):
    t = FakeTransport({"content": [{"type": "text", "text": AI_TEXT}]})
    c = make_client(tmp_path, ai_transport=t)
    c.put("/api/integrations/llm", json={"mode": "api", "values": {"provider": "anthropic", "api_key": "sk-ant-x"}})
    acme = lead(c)
    out = c.post(f"/api/leads/{acme['id']}/draft-reply", json={"incoming": "How much does it cost?"}).json()
    assert out["source"] == "ai" and out["ai_drafted"] is True
    assert rows(c, "ai.draft")[-1]["lead_id"] == acme["id"]
    edited = AI_TEXT.replace("Shall I", "Can I")                 # a person edits the draft a little, then sends it
    r = c.post(f"/api/leads/{acme['id']}/email-compose", json={"subject": "Pricing", "body": edited})
    assert r.status_code == 200, r.text
    sent = store_of(c).read("outbox")[-1]
    assert sent["ai_drafted"] is True and sent["ai_approved_by"] == "This computer"
    page = c.get("/api/outbox?page=1").json()["items"][0]
    assert page["ai_drafted"] is True and page["ai_approved_by"] == "This computer"
    event = store_of(c).read("leads")[0]["timeline"][-1] if store_of(c).read("leads")[0]["id"] == acme["id"] else \
        next(l for l in store_of(c).read("leads") if l["id"] == acme["id"])["timeline"][-1]
    assert event.get("ai_drafted") is True
    assert "approved and sent by This computer" in rows(c, "ai.sent")[-1]["detail"]
    c.post(f"/api/leads/{acme['id']}/email-compose", json={"subject": "Other", "body": "A completely different note I wrote myself today."})
    assert not store_of(c).read("outbox")[-1].get("ai_drafted")


def test_flow_email_from_ask_claude_step_is_marked_ai_drafted(tmp_path):
    from app import flow_actions
    token = flow_actions.CURRENT_VARS.set({"intro": AI_TEXT, flow_actions.AI_VARS: ["intro"]})
    try:
        assert flow_actions.ai_text("Hello {{company}}", "{{intro}}\n\nUnsubscribe: {{unsub_link}}")
        assert not flow_actions.ai_text("Hello {{company}}", "plain body")
    finally:
        flow_actions.CURRENT_VARS.reset(token)


# ---------------------------------------------------------------- hash chain
def test_hash_chain_ok_then_detects_an_edited_row(tmp_path):
    c = make_client(tmp_path)
    for n in (10, 20, 30):
        c.put("/api/settings", json={"daily_cap": n})
    s = store_of(c)
    chain = [r for r in s.read("audit") if audit_chain.chained(r)]
    assert [r["seq"] for r in chain] == list(range(1, len(chain) + 1)) and chain[0]["prev_hash"] == audit_chain.GENESIS
    assert all(b["prev_hash"] == a["hash"] for a, b in zip(chain, chain[1:]))
    ok = c.get("/api/audit/verify").json()
    assert ok["ok"] is True and ok["checked"] == len(chain)
    data = s.read("audit")
    target = next(i for i, r in enumerate(data) if r.get("action") == "settings.save")
    data[target] = {**data[target], "detail": "nothing to see here"}
    s.write("audit", data)
    bad = c.get("/api/audit/verify").json()
    assert bad["ok"] is False and bad["first_broken"]["seq"] == data[target]["seq"] and "changed" in bad["first_broken"]["problem"]


def test_hash_chain_detects_a_deleted_row_and_legacy_rows_are_not_checked(tmp_path):
    c = make_client(tmp_path)
    s = store_of(c)
    legacy = {"at": "2026-01-01T00:00:00+00:00", "by": "Old", "action": "settings.save", "target": "x", "detail": "legacy"}
    s.replace_all(lambda data: {**data, "audit": [legacy]})        # a row from before the chain (restores skip sealing)
    for n in (10, 20, 30, 40):
        c.put("/api/settings", json={"daily_cap": n})
    first = c.get("/api/audit/verify").json()
    assert first["ok"] is True and first["legacy_rows"] == 1 and "no hash" in first["note"]
    data = s.read("audit")
    victim = next(r for r in data if r.get("seq") == 2)
    s.write("audit", [r for r in data if r is not victim and r != victim])
    bad = c.get("/api/audit/verify").json()
    assert bad["ok"] is False and bad["missing_seq"] == [2] and "missing" in bad["message"]


def test_hash_chain_survives_trimming_by_the_size_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "MAX_AUDIT_ROWS", 5)
    c = make_client(tmp_path)
    for n in range(10, 22):
        audit.record(store_of(c), "Tester", "settings.change", "cap", f"to {n}")
    data = store_of(c).read("audit")
    assert audit_chain.is_anchor(data[0]) and len([r for r in data if audit_chain.chained(r)]) == 5
    res = c.get("/api/audit/verify").json()
    assert res["ok"] is True and res["trimmed"] >= 7 and res["anchor_seq"] == data[1]["seq"] - 1
    assert all(r["action"] != audit_chain.ANCHOR for r in c.get("/api/audit").json()["items"])


def test_verify_and_pack_are_owner_only(tmp_path):
    c, owner, caller = owner_and_caller(tmp_path)
    assert c.get("/api/audit/verify", headers=caller).status_code == 403
    assert c.get("/api/audit/pack?from=2026-01-01", headers=caller).status_code == 403
    assert c.get("/api/audit/verify", headers=owner).status_code == 200


# ---------------------------------------------------------------- consent record
def test_consent_view_lists_events_and_status_per_channel(tmp_path):
    c, owner, caller = owner_and_caller(tmp_path)
    acme = find(c, owner)
    assert c.post(f"/api/leads/{acme['id']}/consent", json={"text": "Yes on a call with Amit", "channel": "whatsapp"}, headers=caller).status_code == 200
    view = c.get(f"/api/leads/{acme['id']}/consent", headers=caller).json()
    assert view["status"]["whatsapp"]["status"] == "opted_in" and view["status"]["sms"]["status"] == "no_consent"
    given = view["events"][0]
    assert given["event"] == "given" and given["source_kind"] == "person" and given["by"] == "Meena" and given["channel"] == "whatsapp"
    c.post(f"/api/leads/{acme['id']}/reply", json={"text": "Please unsubscribe me"}, headers=caller)
    c.post("/api/leads/bulk", json={"lead_ids": [acme["id"]], "action": "do_not_contact"}, headers=owner)
    view = c.get(f"/api/leads/{acme['id']}/consent", headers=owner).json()
    assert all(s["status"] == "do_not_contact" for s in view["status"].values())
    kinds = [(e["event"], e["source_kind"]) for e in view["events"]]
    assert ("withdrawn", "reply") in kinds and ("do_not_contact", "person") in kinds
    ledger = store_of(c).read("consent_ledger")
    assert all({"source_kind", "by", "channel", "at", "source"} <= set(r) for r in ledger)


# ---------------------------------------------------------------- audit pack
def _zip(resp):
    return zipfile.ZipFile(io.BytesIO(resp.content))


def test_audit_pack_for_one_lead_and_for_a_range_respects_workspaces(tmp_path):
    c, owner, caller = owner_and_caller(tmp_path)
    s = store_of(c)
    acme, beta = find(c, owner, "Acme"), find(c, owner, "Beta")
    s.update("outbox", lambda rows: [*rows,
        {"lead_id": acme["id"], "to": "ravi@acme.com", "subject": "Hi", "body": "b", "status": "sent", "sent_at": "2026-11-02T04:00:00+00:00",
         "origin": {"kind": "sequence", "by": "Meena"}},
        {"lead_id": beta["id"], "to": "sam@beta.in", "subject": "Hi Beta", "body": "b", "status": "sent", "sent_at": "2026-11-02T05:00:00+00:00"}])
    c.post(f"/api/leads/{acme['id']}/consent", json={"text": "Yes by email", "channel": "email"}, headers=owner)
    r = c.get(f"/api/audit/pack?lead_id={acme['id']}", headers=owner)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = _zip(r)
    assert {"outbox.csv", "whatsapp.csv", "linkedin.csv", "change_log.csv", "consent.csv", "verify.json", "README.txt"} <= set(z.namelist())
    outbox = z.read("outbox.csv").decode("utf-8-sig")
    assert "origin_kind" in outbox and "sequence" in outbox and "Hi Beta" not in outbox
    assert "Yes by email" in z.read("consent.csv").decode("utf-8-sig")
    assert json.loads(z.read("verify.json"))["ok"] is True
    assert rows(c, "audit.pack")[-1]["lead_id"] == acme["id"]
    # a date range in a workspace that holds only Beta: Acme's rows stay out, and no login history (workspace selected)
    ws = c.post("/api/workspaces", json={"name": "Client B"}, headers=owner).json()
    c.post(f"/api/workspaces/{ws['id']}/move", json={"lead_ids": [beta["id"]]}, headers=owner)
    s.write("login_history", [{"at": "2026-11-02T03:00:00+00:00", "email": "meena@example.com", "ok": True}])
    scoped = _zip(c.get("/api/audit/pack?from=2026-11-01&to=2026-11-07", headers={**owner, "X-Workspace": ws["id"]}))
    text = scoped.read("outbox.csv").decode("utf-8-sig")
    assert "Hi Beta" in text and "ravi@acme.com" not in text and "login_history.csv" not in scoped.namelist()
    assert c.get(f"/api/audit/pack?lead_id={acme['id']}", headers={**owner, "X-Workspace": ws["id"]}).status_code == 404
    everything = _zip(c.get("/api/audit/pack?from=2026-11-01&to=2026-11-07", headers=owner))
    assert "meena@example.com" in everything.read("login_history.csv").decode("utf-8-sig")
    assert c.get("/api/audit/pack", headers=owner).status_code == 400
    assert c.get("/api/audit/pack?from=bad", headers=owner).status_code == 400
