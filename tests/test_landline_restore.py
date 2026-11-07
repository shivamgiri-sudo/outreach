"""Data hygiene > Restore landlines: dry run, apply (only empty phones), skips, workspaces, audit, undo, owner only,
and the ops script. All data is made up."""
import json

import pytest
from fastapi.testclient import TestClient

from app import landline_restore
from app.main import create_app
from app.privacy import hashes_for

URL = "/api/hygiene/restore-landlines"
T = "2026-11-01T09:00:00+00:00"


def lead(lead_id, **fields):
    return {"id": lead_id, "company": f"Co {lead_id}", "contact_name": "", "title": "", "email": None, "phone": "", "website": "",
            "domain": None, "country": "IN", "source": "google_places", "stage": "new", "consent": None,
            "engagement": {"opens": 0, "clicks": 0, "replies": 0}, "timeline": [{"at": T, "kind": "created", "text": "x"}],
            "created_at": T, "dedupe_key": f"id:{lead_id}", **fields}


LEADS = [
    lead("cp", company_phone="020 2612 3456", phone_type="business"),            # campaign/Places row: phone was dropped
    lead("ph", phones=["011-2345 6789"]),
    lead("cu", custom={"office_phone": "01123456780"}),
    lead("mc", merge_conflicts=[{"id": "x1", "field": "phone", "kept": "", "incoming": "022 2345 6789"}]),
    lead("has", phone="+919810012345", phone_type="mobile", company_phone="020 2612 9999"),   # phone set: never touched
    lead("dnc", company_phone="020 2612 1111", do_not_contact=True),
    lead("uns", company_phone="020 2612 2222", stage="unsubscribed"),
    lead("era", company_phone="020 2612 3333"),
    lead("bad", company_phone="2612 4444"),                                       # no STD code: not usable
    lead("ws2", company_phone="020 2612 5555", workspace="client-b"),
]


@pytest.fixture()
def app(tmp_path):
    a = create_app(data_file=tmp_path / "d.json")
    a.state.store.update("leads", lambda rows: [*rows, *json.loads(json.dumps(LEADS))])
    a.state.store.update("suppressed_hashes", lambda rows: [*rows, *hashes_for(None, ["020 2612 3333"])])
    return a


def leads_of(app):
    return {l["id"]: l for l in app.state.store.read("leads")}


def test_candidates_read_the_leads_own_data():
    assert landline_restore.candidates(LEADS[0]) == [("+912026123456", "company_phone", "020 2612 3456")]
    assert landline_restore.candidates(LEADS[1])[0][:2] == ("+911123456789", "phones")
    assert landline_restore.candidates(LEADS[2])[0][:2] == ("+911123456780", "custom:office_phone")
    assert landline_restore.candidates(LEADS[3])[0][:2] == ("+912223456789", "value to check")
    assert landline_restore.candidates(LEADS[8]) == []
    assert landline_restore.candidates(lead("x", custom={"notes": "011 2345 6789"})) == []        # not a phone field
    assert landline_restore.candidates(lead("y", custom={"n": "011 2345 6789"}), {"n": "phone"})[0][0] == "+911123456789"


def test_dry_run_apply_undo_in_the_selected_workspace(app):
    c = TestClient(app)
    before = leads_of(app)
    plan = c.get(URL, headers={"X-Workspace": "main"}).json()
    assert plan["dry_run"] and plan["would_change"] == 4 and plan["last_run"] is None
    assert {s["lead_id"] for s in plan["sample"]} == {"cp", "ph", "cu", "mc"}
    assert plan["skipped"] == {"do-not-contact": 1, "unsubscribed": 1, "erased": 1}
    assert leads_of(app) == before                                          # a dry run writes nothing

    done = c.post(URL, headers={"X-Workspace": "main"}).json()
    assert done["changed"] == 4 and done["last_run"]["changed"] == 4
    now = leads_of(app)
    assert now["cp"]["phone"] == "+912026123456" and now["cp"]["phone_type"] == "landline"
    assert now["cp"]["phone_source"] == "restored:company_phone" and now["cp"]["timeline"][-1]["kind"] == "landline_restored"
    assert now["mc"]["phone"] == "+912223456789"
    assert now["has"] == before["has"] and now["dnc"]["phone"] == "" and now["uns"]["phone"] == "" and now["era"]["phone"] == ""
    assert now["bad"]["phone"] == "" and now["ws2"]["phone"] == ""            # other workspace left alone
    assert c.get(URL, headers={"X-Workspace": "main"}).json()["would_change"] == 0      # nothing left; re-running is a no-op
    rows = [r for r in app.state.store.read("audit") if r.get("action", "").startswith("hygiene.restore_landlines")]
    assert rows[-1]["action"] == "hygiene.restore_landlines" and done["run_id"] in rows[-1]["detail"]

    app.state.store.update_lead("ph", lambda l: {**l, "phone": "+919822233344", "phone_type": "mobile"})   # edited since
    undone = c.post(f"{URL}/{done['run_id']}/undo").json()
    assert undone["reverted"] == 3 and undone["kept"] == 1 and undone["last_run"] is None
    after = leads_of(app)
    assert after["cp"]["phone"] == "" and after["cp"]["phone_type"] == "business" and "phone_source" not in after["cp"]
    assert after["ph"]["phone"] == "+919822233344"                          # a later edit is never undone
    assert after["cu"]["phone"] == "" and "phone_type" not in after["cu"]
    assert c.post(f"{URL}/{done['run_id']}/undo").status_code == 409
    assert c.post(f"{URL}/nope/undo").status_code == 404
    actions = [r.get("action") for r in app.state.store.read("audit")]
    assert "hygiene.restore_landlines_undo" in actions


def test_all_workspaces_scope_includes_other_workspaces(app):
    plan = TestClient(app).get(URL).json()
    assert plan["would_change"] == 5 and "ws2" in {s["lead_id"] for s in plan["sample"]}


def test_apply_never_overwrites_a_phone_set_after_the_dry_run(app):
    c = TestClient(app)
    assert c.get(URL).json()["would_change"] == 5
    app.state.store.update_lead("cp", lambda l: {**l, "phone": "+919811122233", "phone_type": "mobile"})
    assert c.post(URL).json()["changed"] == 4
    assert leads_of(app)["cp"]["phone"] == "+919811122233"


def test_only_the_owner_may_restore(tmp_path):
    a = create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123")
    c = TestClient(a, client=("203.0.113.9", 50000))
    h = lambda t: {"Authorization": f"Bearer {t}"}
    owner = c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    c.post("/api/users", json={"email": "meena@example.com", "name": "Meena", "role": "caller", "password": "long-password-1"}, headers=h(owner))
    meena = c.post("/api/login", json={"email": "meena@example.com", "password": "long-password-1"}).json()["token"]
    assert c.get(URL, headers=h(meena)).status_code == 403 and c.post(URL, headers=h(meena)).status_code == 403
    assert c.post(f"{URL}/x/undo", headers=h(meena)).status_code == 403
    assert c.get(URL, headers=h(owner)).status_code == 200


def test_ops_script_dry_run_apply_undo(tmp_path, capsys, monkeypatch):
    import importlib.util
    from pathlib import Path
    from app.store import Store
    monkeypatch.delenv("OUTREACH_DATABASE_URL", raising=False)
    path = tmp_path / "store.json"
    s = Store(path)
    s.update("leads", lambda rows: json.loads(json.dumps(LEADS)))
    spec = importlib.util.spec_from_file_location("restore_landlines", Path(__file__).resolve().parent.parent / "scripts" / "restore_landlines.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--data-file", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Dry run: 6 lead(s) would get" in out and "Nothing was changed" in out
    assert {l["id"]: l["phone"] for l in Store(path).read("leads")}["cp"] == ""
    assert mod.main(["--data-file", str(path), "--apply"]) == 0
    out = capsys.readouterr().out
    run_id = out.split("Run id ")[1].split()[0]
    saved = {l["id"]: l for l in Store(path).read("leads")}
    assert saved["cp"]["phone"] == "+912026123456" and saved["ws2"]["phone"] == "+912026125555" and saved["has"]["phone"] == "+919810012345"
    assert mod.main(["--data-file", str(path), "--undo", run_id]) == 0
    assert {l["id"]: l["phone"] for l in Store(path).read("leads")}["cp"] == ""
    assert mod.main(["--data-file", str(tmp_path / "missing.json")]) == 2
