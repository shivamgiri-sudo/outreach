"""Last audit gaps: AI-drafted LinkedIn connection notes are marked on the send record and in the lead plan, saved
Leads views keep the LinkedIn stage filter, and own-password changes (ok and wrong) are in the login history."""
from fastapi.testclient import TestClient

from app import ai, lead_ops, linkedin_seq as li, opener_lab
from app.main import create_app

CSV = "company,contact_name,title,email,linkedin_url\nAcme,Ravi Kumar,CHRO,ravi@acme.com,https://www.linkedin.com/in/ravi-kumar\n"


def make(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV})
    return c, c.app.state.store, c.app.state.store.read("leads")[0]["id"]


def test_ai_note_is_marked_on_the_send_and_in_the_plan(tmp_path, monkeypatch):
    c, store, lid = make(tmp_path)
    note = "Hi {first_name}, I enjoy swapping notes with people who build hiring teams. Would be glad to connect."
    monkeypatch.setattr(ai, "_llm_config", lambda s: {"provider": "x"})
    monkeypatch.setattr(ai, "_call_llm", lambda *a, **k: '{"notes": ["%s"]}' % note)
    assert c.post("/api/linkedin/variants/generate", json={}).json()["source"] == "claude"
    edited = note.replace("Would be glad", "Would be happy")                 # a light edit still counts as AI-drafted
    assert c.put("/api/linkedin/variants", json={"A": edited, "B": edited}).status_code == 200
    assert set(store.read("settings")["linkedin_connect_variants"]["ai"]) == {"A", "B"}
    li.start(store, lid)
    assert c.post(f"/api/leads/{lid}/linkedin/sent", json={"step": 1}).status_code == 200
    line = next(e for e in store.read("leads")[0]["timeline"] if e.get("text") == "LinkedIn step 1 sent")
    assert line["origin"]["ai_drafted"] is True and line["origin"]["ai_approved_by"]
    step1 = c.get(f"/api/leads/{lid}/plan").json()["linkedin"]["steps"][0]
    assert "AI-drafted note" in step1.get("note", "")
    assert c.put("/api/linkedin/variants", json={"A": li.STEPS[0]["text"]}).status_code == 200    # a person's own note: not AI
    assert set(store.read("settings")["linkedin_connect_variants"]["ai"]) == {"B"}
    assert opener_lab.ai_note(store.read("settings"), {"linkedin_seq": {"variant": "A"}}) == ""


def test_saved_view_keeps_the_linkedin_stage(tmp_path):
    assert lead_ops._clean_filters({"li_stage": "messaging"})["li_stage"] == "messaging"
    assert lead_ops._clean_filters({"li_stage": "nonsense"})["li_stage"] == ""


def test_own_password_change_is_in_the_login_history(tmp_path):
    c, store, _ = make(tmp_path)
    assert c.post("/api/users", json={"email": "rep@acme.com", "name": "Rep", "role": "owner", "password": "Rep-pass-123456"}).status_code in (200, 201)
    tok = c.post("/api/login", json={"email": "rep@acme.com", "password": "Rep-pass-123456"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert c.post("/api/me/password", json={"current": "wrong-one-xx", "new": "New-pass-654321"}, headers=h).status_code == 400
    assert c.post("/api/me/password", json={"current": "Rep-pass-123456", "new": "New-pass-654321"}, headers=h).status_code == 200
    rows = [(r["event"], r["result"]) for r in store.read("login_history") if r.get("email") == "rep@acme.com"]
    assert ("password_change", "failure") in rows and ("password_change", "success") in rows


def test_legacy_why_uses_the_enrolment_before_the_send():
    from app.provenance import _timeline_enrolment
    lead = {"timeline": [{"at": "2026-11-03T14:22:00+00:00", "kind": "sequence", "text": "Enrolled in email sequence"},
                         {"at": "2026-11-09T07:22:00+00:00", "kind": "sequence", "text": "Enrolled in email sequence", "by": "Later Person"}]}
    assert "03 Nov" in _timeline_enrolment(lead, "2026-11-09T06:14:00+00:00")
    assert "Later Person" in _timeline_enrolment(lead, "2026-11-10T06:14:00+00:00")
    assert _timeline_enrolment(lead, "2026-11-01T00:00:00+00:00") == ""
