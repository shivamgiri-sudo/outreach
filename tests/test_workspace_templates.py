"""Templates per workspace: a workspace's admins change its sequences, snippets, LinkedIn steps / notes and flows; main's
are read-only for them (copy instead); callers only use them; owners change everything; a lead uses its own
workspace's templates; a workspace's flow never touches another workspace's (or main's) leads; every write is logged."""
from fastapi.testclient import TestClient

from app import flow_engine
from app.main import create_app
from app.workspace_guard import allowed_route
from tests.flow_helpers import FakeTransport, T0, chain, node, public_resolver

PASSWORD = "long-password-1"
MAIN_CSV = ("company,contact_name,title,email,phone,website,linkedin_url\n"
            "Acme,Ravi Kumar,HR Head,ravi@acme.com,98765 43210,https://acme.com,https://www.linkedin.com/in/ravi-kumar\n")
W1_CSV = ("company,contact_name,title,email,phone,website,linkedin_url\n"
          "Gamma,Om Das,HR Head,om@gamma.in,99887 76655,https://gamma.in,https://www.linkedin.com/in/om-das\n")
BODY = "{{greeting}}\n\nA note for {{company}}.\n\n{{sender}}\n\nUnsubscribe: {{unsub_link}}"
STEPS = [{"day": 0, "name": "Email 1", "subject": "Hello {{company}}", "body": BODY}]


def login(c, ho, email, role, spaces):
    r = c.post("/api/users", json={"email": email, "name": email.split("@")[0], "role": role, "password": PASSWORD, "workspaces": spaces}, headers=ho)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + c.post("/api/login", json={"email": email, "password": PASSWORD}).json()["token"]}


def setup(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123", channel_transport=FakeTransport(),
                              resolver=public_resolver), client=("203.0.113.9", 50000))
    ho = {"Authorization": "Bearer " + c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]}
    w1 = c.post("/api/workspaces", json={"name": "Client One"}, headers=ho).json()["id"]
    w2 = c.post("/api/workspaces", json={"name": "Client Two"}, headers=ho).json()["id"]
    c.post("/api/leads/import", json={"csv": MAIN_CSV, "source": "apollo"}, headers=ho)
    c.post("/api/leads/import", json={"csv": W1_CSV, "source": "apollo"}, headers={**ho, "X-Workspace": w1})
    users = {"admin": login(c, ho, "adm@example.com", "admin", [w1]), "caller": login(c, ho, "cal@example.com", "caller", [w1]),
             "admin2": login(c, ho, "adm2@example.com", "admin", [w2])}
    leads = {l["company"]: l for l in c.get("/api/leads", headers=ho).json()}
    assert (leads["Gamma"].get("workspace") or "main") == w1 and (leads["Acme"].get("workspace") or "main") == "main"
    return c, ho, users, w1, w2, leads


def audit_rows(c, action):
    return [r for r in c.app.state.store.read("audit") if r.get("action") == action]


def test_sequences_admin_edits_own_refused_for_main_and_other_workspaces(tmp_path):
    c, ho, u, w1, w2, _ = setup(tmp_path)
    ha = u["admin"]
    listed = c.get("/api/sequences", headers=ha).json()
    assert [s["id"] for s in listed] == ["default"] and listed[0]["can_edit"] is False
    mine = c.post("/api/sequences", json={"name": "W1 seq", "steps": STEPS}, headers=ha)
    assert mine.status_code == 200 and mine.json()["workspace_id"] == w1 and mine.json()["can_edit"] is True
    sid = mine.json()["id"]
    assert c.put(f"/api/sequences/{sid}", json={"name": "W1 seq v2"}, headers=ha).json()["name"] == "W1 seq v2"
    assert c.post(f"/api/sequences/{sid}/archive", json={"archived": True}, headers=ha).json()["archived"] is True
    assert c.post(f"/api/sequences/{sid}/archive", json={"archived": False}, headers=ha).status_code == 200
    refused = c.post("/api/sequences", json={"name": "x", "steps": STEPS, "workspace": "main"}, headers=ha)
    assert refused.status_code == 403 and "read-only" in refused.json()["detail"]
    assert c.post("/api/sequences", json={"name": "x", "steps": STEPS, "workspace": w2}, headers=ha).status_code == 403
    main_seq = c.post("/api/sequences", json={"name": "Main seq", "steps": STEPS}, headers=ho).json()
    assert main_seq["workspace_id"] == "main"
    r = c.put(f"/api/sequences/{main_seq['id']}", json={"name": "hijack"}, headers=ha)
    assert r.status_code == 403 and "Copy into my workspace" in r.json()["detail"]
    assert c.post(f"/api/sequences/{main_seq['id']}/archive", json={}, headers=ha).status_code == 403
    other = c.post("/api/sequences", json={"name": "W2 seq", "steps": STEPS, "workspace": w2}, headers=ho).json()
    assert c.put(f"/api/sequences/{other['id']}", json={"name": "x"}, headers=ha).status_code in (403, 404)
    assert other["id"] not in {s["id"] for s in c.get("/api/sequences", headers=ha).json()}
    assert c.put(f"/api/sequences/{other['id']}", json={"name": "x"}, headers=u["admin2"]).status_code == 200
    # callers only use them
    assert c.post("/api/sequences", json={"name": "x", "steps": STEPS}, headers=u["caller"]).status_code == 403
    assert c.put(f"/api/sequences/{sid}", json={"name": "x"}, headers=u["caller"]).status_code == 403
    assert {s["id"] for s in c.get("/api/sequences", headers=u["caller"]).json()} == {"default", sid, main_seq["id"]}
    # owners everywhere, with a workspace label
    assert c.put(f"/api/sequences/{sid}", json={"name": "Owner fixed"}, headers=ho).status_code == 200
    labels = {s["name"]: s["workspace_name"] for s in c.get("/api/sequences", headers=ho).json()}
    assert labels["Owner fixed"] == "Client One" and labels["x"] == "Client Two" and labels["Main seq"] == "Main"
    # copy from main into my workspace
    copy = c.post(f"/api/sequences/{main_seq['id']}/duplicate", json={}, headers=ha).json()
    assert copy["workspace_id"] == w1 and copy["can_edit"] is True and copy["name"] == "Copy of Main seq"
    builtin_copy = c.post("/api/sequences/default/duplicate", json={}, headers=ha).json()
    assert builtin_copy["workspace_id"] == w1
    assert {r["workspace"] for r in audit_rows(c, "sequence.create")} == {w1, "main", w2}
    assert any(r["workspace"] == w1 and r["by_email"] == "adm@example.com" for r in audit_rows(c, "sequence.copy"))
    assert any(r["workspace"] == w1 and "W1 seq v2" in r["detail"] for r in audit_rows(c, "sequence.change"))


def test_snippets_by_workspace(tmp_path):
    c, ho, u, w1, w2, _ = setup(tmp_path)
    ha = u["admin"]
    s = c.post("/api/snippets", json={"name": "W1 hello", "channel": "email", "body": "Hi {{first_name}}"}, headers=ha)
    assert s.status_code == 200 and s.json()["workspace_id"] == w1
    sid = s.json()["id"]
    assert c.put(f"/api/snippets/{sid}", json={"name": "W1 hello v2"}, headers=ha).json()["name"] == "W1 hello v2"
    main_snip = c.post("/api/snippets", json={"name": "Main hello", "channel": "email", "body": "Hi"}, headers=ho).json()
    assert c.put(f"/api/snippets/{main_snip['id']}", json={"name": "x"}, headers=ha).status_code == 403
    assert c.delete(f"/api/snippets/{main_snip['id']}", headers=ha).status_code == 403
    assert c.post("/api/snippets", json={"name": "x", "body": "Hi", "workspace": "main"}, headers=ha).status_code == 403
    assert c.post("/api/snippets", json={"name": "x", "body": "Hi"}, headers=u["caller"]).status_code == 403
    assert c.put(f"/api/snippets/{sid}", json={"name": "x"}, headers=u["caller"]).status_code == 403
    assert {r["name"] for r in c.get("/api/snippets", headers=u["caller"]).json()} == {"W1 hello v2", "Main hello"}
    assert c.get("/api/snippets", headers=u["admin2"]).json()[0]["name"] == "Main hello"
    assert c.put(f"/api/snippets/{sid}", json={"name": "Owner edit"}, headers=ho).status_code == 200
    assert c.delete(f"/api/snippets/{sid}", headers=ha).status_code == 200
    assert [r["workspace"] for r in audit_rows(c, "snippet.delete")] == [w1]
    assert any(r["workspace"] == w1 for r in audit_rows(c, "snippet.change"))


def w1_linkedin_steps(c, h):
    steps = c.get("/api/linkedin/sequence", headers=h).json()["steps"][:2]
    steps[0] = {**steps[0], "text": "Hi {first_name}, W1 hello. I work with teams that hire a lot and would like to connect."}
    steps[1] = {**steps[1], "text": "W1 message for {first_name} about hiring."}
    return steps


def test_linkedin_steps_and_notes_per_workspace(tmp_path):
    c, ho, u, w1, w2, leads = setup(tmp_path)
    ha = u["admin"]
    view = c.get("/api/linkedin/sequence", headers=ha).json()
    assert view["workspace"] == w1 and view["own"] is False and view["can_edit"] is True and view["builtin"] is True
    assert c.get("/api/linkedin/sequence", headers=u["caller"]).json()["can_edit"] is False
    saved = c.put("/api/linkedin/sequence", json={"steps": w1_linkedin_steps(c, ha),
                                                  "variant_b": "Hi {first_name}, W1 note B about teams and hiring. Happy to connect."}, headers=ha)
    assert saved.status_code == 200, saved.text
    assert saved.json()["own"] is True and len(saved.json()["steps"]) == 2
    main_view = c.get("/api/linkedin/sequence", headers=ho).json()
    assert main_view["builtin"] is True and len(main_view["steps"]) == 7 and "W1" not in main_view["steps"][0]["text"]
    r = c.put("/api/linkedin/sequence", json={"steps": w1_linkedin_steps(c, ha), "workspace": "main"}, headers=ha)
    assert r.status_code == 403 and "read-only" in r.json()["detail"]
    assert c.put("/api/linkedin/sequence", json={"steps": w1_linkedin_steps(c, ha), "workspace": w2}, headers=ha).status_code == 403
    assert c.put("/api/linkedin/sequence", json={"steps": w1_linkedin_steps(c, ha)}, headers=u["caller"]).status_code == 403
    assert c.put("/api/linkedin/variants", json={"A": "Hi {first_name}, caller note about teams here."}, headers=u["caller"]).status_code == 403
    # notes of W1 (main's stay)
    notes = c.put("/api/linkedin/variants", json={"A": "Hi {first_name}, W1 note A about teams and hiring. Happy to connect."}, headers=ha)
    assert notes.status_code == 200 and notes.json()["notes"]["A"]["text"].startswith("Hi {first_name}, W1 note A")
    assert "W1" not in c.get("/api/linkedin/variants", headers=ho).json()["notes"]["A"]["text"]
    assert any(r["workspace"] == w1 for r in audit_rows(c, "template.linkedin_notes"))
    assert any(r["workspace"] == w1 and r["by_email"] == "adm@example.com" for r in audit_rows(c, "template.linkedin_sequence"))
    # the lead in W1 gets W1's note; the main lead the built-in one
    w1_task = c.post(f"/api/leads/{leads['Gamma']['id']}/linkedin/start", headers=ha).json()["task"]
    assert "W1 note" in w1_task["text"]
    main_task = c.post(f"/api/leads/{leads['Acme']['id']}/linkedin/start", headers=ho).json()["task"]
    assert "W1" not in main_task["text"] and "came across your profile" in main_task["text"]
    assert c.post(f"/api/leads/{leads['Gamma']['id']}/linkedin/sent", json={"step": 1}, headers=ha).status_code == 200      # the request goes out first
    assert c.post(f"/api/leads/{leads['Gamma']['id']}/linkedin/accepted", headers=ha).json()["next_task"]["text"] == "W1 message for Om about hiring."
    # queue: only the workspace's leads
    due = c.get("/api/linkedin/queue", headers=ha).json()["due"]
    assert {d["company"] for d in due} == {"Gamma"}
    assert {d["company"] for d in c.get("/api/linkedin/queue", headers=ho).json()["due"]} == {"Gamma", "Acme"}
    # owner may edit W1's; reset goes back to main's; copy brings main's in again
    assert c.put("/api/linkedin/sequence", json={"steps": w1_linkedin_steps(c, ha), "workspace": w1}, headers=ho).status_code == 200
    back = c.post("/api/linkedin/sequence/reset", json={}, headers=ha).json()
    assert back["own"] is True                              # a W1 lead is part-way: it keeps its steps (main's become a new version)
    assert len(back["steps"]) == 7
    copied = c.post("/api/linkedin/sequence/copy", json={}, headers=u["admin2"]).json()
    assert copied["workspace"] == w2 and copied["own"] is True and len(copied["steps"]) == 7


def test_enrol_uses_the_workspace_default_sequence(tmp_path):
    c, ho, u, w1, w2, leads = setup(tmp_path)
    ha = u["admin"]
    sid = c.post("/api/sequences", json={"name": "W1 seq", "steps": STEPS}, headers=ha).json()["id"]
    assert c.post(f"/api/sequences/{sid}/make-default", json={}, headers=ha).json() == {"workspace": w1, "default_sequence": sid}
    assert c.post(f"/api/sequences/{sid}/make-default", json={"workspace": "main"}, headers=ha).status_code == 403
    assert {s["id"]: s["is_default"] for s in c.get("/api/sequences", headers=ha).json()}[sid] is True
    w1_lead = c.post(f"/api/leads/{leads['Gamma']['id']}/enroll", json={}, headers=ha).json()
    assert w1_lead["sequence"]["sequence_id"] == sid
    main_lead = c.post(f"/api/leads/{leads['Acme']['id']}/enroll", json={}, headers=ho).json()
    assert main_lead["sequence"]["sequence_id"] == "default"
    assert any(r["workspace"] == w1 for r in audit_rows(c, "sequence.default"))
    # a sequence of another workspace is refused for a lead
    w2_seq = c.post("/api/sequences", json={"name": "W2 seq", "steps": STEPS, "workspace": w2}, headers=ho).json()["id"]
    c.app.state.store.update_lead(leads["Gamma"]["id"], lambda l: {**l, "sequence": None})
    r = c.post(f"/api/leads/{leads['Gamma']['id']}/enroll", json={"sequence_id": w2_seq}, headers=ho)
    assert r.status_code == 400 and "another workspace" in r.json()["detail"]


def test_flows_by_workspace_never_touch_main_leads(tmp_path):
    c, ho, u, w1, w2, leads = setup(tmp_path)
    ha = u["admin"]
    nodes, edges = [node("t", "trigger.lead_added"), node("tag", "add_tag", tag="w1flow")], chain("t", "tag")
    made = c.post("/api/flows", json={"name": "W1 flow", "nodes": nodes, "edges": edges}, headers=ha)
    assert made.status_code == 200, made.text
    flow = made.json()
    assert flow["workspace_id"] == w1
    assert c.put(f"/api/flows/{flow['id']}", json={"name": "W1 flow v2"}, headers=ha).json()["name"] == "W1 flow v2"
    assert c.post(f"/api/flows/{flow['id']}/activate", headers=ha).status_code == 200
    main_flow = c.post("/api/flows", json={"name": "Main flow", "nodes": nodes, "edges": edges}, headers=ho).json()
    assert main_flow["workspace_id"] == "main"
    r = c.put(f"/api/flows/{main_flow['id']}", json={"name": "x"}, headers=ha)
    assert r.status_code == 403 and "read-only" in r.json()["detail"]
    assert c.post("/api/flows", json={"name": "x", "workspace": "main"}, headers=ha).status_code == 403
    assert c.post("/api/flows", json={"name": "x"}, headers=u["caller"]).status_code == 403
    assert {f["name"] for f in c.get("/api/flows", headers=u["caller"]).json()} == {"W1 flow v2", "Main flow"}
    assert c.get("/api/flows", headers=u["admin2"]).json()[0]["name"] == "Main flow"
    # enrolling: the main lead is refused (outside the admin's scope, and by the engine for anyone)
    out = c.post(f"/api/flows/{flow['id']}/enroll", json={"lead_ids": [leads["Acme"]["id"], leads["Gamma"]["id"]]}, headers=ho).json()
    assert out["enrolled"] == [leads["Gamma"]["id"]] and "another workspace" in out["refused"][0]["reason"]
    # new leads: the W1 flow only takes W1's; nothing ever runs on the main lead
    c.post("/api/leads/import", json={"csv": "company,contact_name,email\nDelta,Ana Roy,ana@delta.in\n", "source": "apollo"}, headers=ho)
    c.post("/api/flows/tick", json={"now": T0.isoformat()}, headers=ho)
    runs = [r for r in c.app.state.store.read("flow_runs") if r["flow_id"] == flow["id"]]
    ws_of = {l["id"]: (l.get("workspace") or "main") for l in c.app.state.store.read("leads")}
    assert runs and all(ws_of[r["lead_id"]] == w1 for r in runs)
    # a lead moved out of the workspace is stopped, not acted on
    c.app.state.store.update_lead(leads["Gamma"]["id"], lambda l: {**{k: v for k, v in l.items() if k != "workspace"}})
    stored = next(f for f in c.app.state.store.read("flows") if f["id"] == flow["id"])
    assert not flow_engine.takes(stored, {"workspace": "main"}) and flow_engine.takes(main_flow, {"workspace": w1})
    assert c.delete(f"/api/flows/{flow['id']}", headers=ha).status_code == 200
    assert {r["workspace"] for r in audit_rows(c, "flow.create")} == {w1, "main"}
    assert [r["workspace"] for r in audit_rows(c, "flow.delete")] == [w1]
    assert any(r["workspace"] == w1 for r in audit_rows(c, "flow.change"))


def test_limited_routes_pass_the_workspace_guard():
    for method, path in [("GET", "/api/linkedin/queue"), ("GET", "/api/linkedin/sequence"), ("PUT", "/api/linkedin/sequence"),
                         ("POST", "/api/linkedin/sequence/copy"), ("POST", "/api/linkedin/sequence/reset"), ("PUT", "/api/linkedin/variants"),
                         ("POST", "/api/sequences"), ("PUT", "/api/sequences/{seq_id}"), ("POST", "/api/sequences/{seq_id}/duplicate"),
                         ("POST", "/api/sequences/{seq_id}/make-default"), ("POST", "/api/snippets"), ("PUT", "/api/snippets/{snippet_id}"),
                         ("GET", "/api/flows"), ("POST", "/api/flows"), ("PUT", "/api/flows/{flow_id}"), ("POST", "/api/flows/{flow_id}/enroll")]:
        assert allowed_route(method, path), (method, path)
    for method, path in [("GET", "/api/linkedin/settings"), ("POST", "/api/flows/tick"), ("GET", "/api/flows/{flow_id}/webhook"),
                         ("POST", "/api/linkedin/results")]:
        assert not allowed_route(method, path), (method, path)


def test_main_only_setup_is_unchanged(tmp_path):
    """No workspace templates: owners see and edit the same global templates as before."""
    c, ho, u, w1, w2, leads = setup(tmp_path)
    assert c.put("/api/linkedin/sequence", json={"steps": w1_linkedin_steps(c, ho)}, headers=ho).json()["workspace"] == "main"
    assert "workspace_templates" not in c.app.state.store.read("settings")
    # the W1 lead falls back to main's steps
    task = c.post(f"/api/leads/{leads['Gamma']['id']}/linkedin/start", headers=ho).json()["task"]
    assert "W1 hello" in task["text"] or "W1 note" in task["text"] or "Hi Om" in task["text"]
    assert c.get("/api/settings", headers=u["admin"]).json().get("workspace_templates") is None
