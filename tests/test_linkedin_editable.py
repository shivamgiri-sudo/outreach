"""Editable LinkedIn sequence: defaults unchanged, step / persona editing with validation, versioning for leads part-way,
the 300-character connection note, A/B notes kept working, preview and owner-only saving."""
import copy
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import linkedin_config as cfg_mod, linkedin_seq as li
from app.main import create_app

T0 = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)
CSV = ("company,contact_name,title,email,phone,website,open_roles,company_size,country\n"
       "Acme,Ravi Kumar,Head of Talent Acquisition,ravi@acme.com,98765 43210,https://acme.com,12,200,IN\n"
       "Beta,Meena Shah,Founder,meena@beta.com,98765 43211,https://beta.com,3,50,IN\n")


def make(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    store = c.app.state.store
    ids = {l["company"]: l["id"] for l in c.get("/api/leads").json()}
    for i, lid in enumerate(ids.values()):
        store.update_lead(lid, lambda l, i=i: {**l, "linkedin_url": f"https://www.linkedin.com/in/person-{i}"})
    return c, ids


def edited_steps(c, **change):
    steps = copy.deepcopy(c.get("/api/linkedin/sequence").json()["steps"])
    for s in steps:
        s.pop("skipped", None)
    return steps


def test_defaults_are_the_built_in_steps_and_personas(tmp_path):
    c, _ = make(tmp_path)
    seq = c.get("/api/linkedin/sequence").json()
    assert seq["builtin"] is True and seq["version"] == 1
    assert [s["text"] for s in seq["steps"]] == [s["text"] for s in li.STEPS]
    assert [(p["key"], p["pattern"], p["hook"]) for p in seq["personas"]] == list(li.PERSONAS)
    assert seq["variant_b"] == li.CONNECT_VARIANTS["B"] and seq["note_limit"] == 300
    # the module-level behaviour is the same as before for callers that pass no settings
    assert li.persona("CHRO")[0] == "chro" and li.persona("Accountant") == ("general", li.DEFAULT_HOOK)
    assert li.render(li.STEPS[1], {"contact_name": "Ravi Kumar", "title": "CHRO"}, {}).startswith("Thanks for connecting, Ravi.")
    assert c.get("/api/linkedin/steps").json()[3]["skipped"] is True        # the case-study step still waits for a link


def test_edit_steps_and_personas_changes_the_messages(tmp_path):
    c, ids = make(tmp_path)
    steps = edited_steps(c)
    steps[0]["text"] = "Hi {first_name}, I work with property buyers in Pune. Happy to connect."
    steps[1]["text"] = "Thanks {first_name}. {hook} What is {company} planning this year?"
    body = {"steps": steps[:3], "default_hook": "General hook.",
            "personas": [{"key": "founder", "pattern": r"\bfounder\b", "hook": "Founder hook."}]}
    r = c.put("/api/linkedin/sequence", json=body)
    assert r.status_code == 200, r.text
    saved = c.get("/api/linkedin/sequence").json()
    assert len(saved["steps"]) == 3 and saved["builtin"] is False and saved["version"] == 1      # nobody in flight: same version
    store = c.app.state.store
    lead = next(l for l in store.read("leads") if l["id"] == ids["Beta"])
    settings = store.read("settings")
    assert li.persona("Founder", settings) == ("founder", "Founder hook.")
    assert li.render(li.current_steps(settings)[1], lead, settings) == "Thanks Meena. Founder hook. What is Beta planning this year?"
    # A/B: note A follows step 1, so the opener lab and the sequence agree
    assert c.get("/api/linkedin/variants").json()["notes"]["A"]["text"].startswith("Hi {first_name}, I work with property buyers")
    first = li.start(store, ids["Beta"], T0)["task"]
    assert first["text"].startswith("Hi Meena,")


def test_validation_messages(tmp_path):
    c, _ = make(tmp_path)
    steps = edited_steps(c)
    long_note = [{**steps[0], "text": "Hi {first_name}, " + "word " * 60}, *steps[1:]]
    r = c.put("/api/linkedin/sequence", json={"steps": long_note})
    assert r.status_code == 400 and "300" in r.json()["detail"]
    r = c.put("/api/linkedin/sequence", json={"steps": [steps[0], {**steps[1], "after": "previous"}]})
    assert r.status_code == 400 and "accepted" in r.json()["detail"]
    r = c.put("/api/linkedin/sequence", json={"steps": [steps[0], {**steps[1], "text": "Hi {firstname}"}]})
    assert r.status_code == 400 and "firstname" in r.json()["detail"]
    r = c.put("/api/linkedin/sequence", json={"steps": [steps[0], {**steps[1], "text": "Hi {{nope}}"}]})
    assert r.status_code == 400 and "nope" in r.json()["detail"]
    r = c.put("/api/linkedin/sequence", json={"steps": steps[:2], "personas": [{"key": "x", "pattern": "(", "hook": "h"}]})
    assert r.status_code == 400 and "pattern" in r.json()["detail"]
    r = c.put("/api/linkedin/sequence", json={"steps": [{**steps[0], "kind": "message"}]})
    assert r.status_code == 400
    ok = c.put("/api/linkedin/sequence", json={"steps": [steps[0], {**steps[1], "text": "Hi {first_name}, {{my_company}} {{custom.budget}}"}]})
    assert ok.status_code == 200, ok.text                     # brand and custom-field placeholders are allowed


def test_connect_note_limit_is_checked_with_a_long_name_and_truncated_when_rendered():
    text = "Hi {first_name}, " + "x" * 285
    assert cfg_mod.stand_in_length(text) > 300
    note = li.render({"n": 1, "kind": "connect", "text": text}, {"contact_name": "Venkataraghavan Iyer"}, {})
    assert len(note) <= li.NOTE_LIMIT


def test_leads_part_way_keep_their_version(tmp_path):
    c, ids = make(tmp_path)
    store = c.app.state.store
    li.start(store, ids["Acme"], T0)
    li.mark_sent(store, ids["Acme"], 1, T0)
    li.mark_accepted(store, ids["Acme"], T0 + timedelta(days=1))
    steps = edited_steps(c)
    new = [steps[0], {**steps[1], "days": 1, "text": "New step 2 for {first_name}"}, {**steps[2], "days": 1, "text": "New step 3"}]
    r = c.put("/api/linkedin/sequence", json={"steps": new}).json()
    assert r["new_version"] is True and r["version"] == 2
    # Acme started on version 1: its next step is the built-in step 3 on the built-in rhythm
    nxt = li.mark_sent(store, ids["Acme"], 2, T0 + timedelta(days=3))["next_task"]
    assert nxt["text"].startswith("Ravi, there's another interesting dimension") and nxt["due_at"].startswith("2026-11-10")
    # a new lead gets the new steps and version
    li.start(store, ids["Beta"], T0)
    beta = next(l for l in store.read("leads") if l["id"] == ids["Beta"])
    assert beta["linkedin_seq"]["steps_version"] == 2
    li.mark_sent(store, ids["Beta"], 1, T0)
    task = li.mark_accepted(store, ids["Beta"], T0 + timedelta(days=1))["next_task"]
    assert task["text"] == "New step 2 for Meena" and task["due_at"].startswith("2026-11-05")
    finished = li.mark_sent(store, ids["Beta"], 2, T0 + timedelta(days=2))
    li.mark_sent(store, ids["Beta"], 3, T0 + timedelta(days=3))
    assert next(l for l in store.read("leads") if l["id"] == ids["Beta"])["linkedin_seq"]["status"] == "finished"
    assert finished["next_task"]["text"] == "New step 3"


def test_wording_only_edit_keeps_the_version_and_reset(tmp_path):
    c, ids = make(tmp_path)
    store = c.app.state.store
    li.start(store, ids["Acme"], T0)
    steps = edited_steps(c)
    steps[2]["text"] = "Reworded step 3 for {first_name}"
    r = c.put("/api/linkedin/sequence", json={"steps": steps}).json()
    assert r["new_version"] is False and r["version"] == 1
    assert c.post("/api/linkedin/sequence/reset").status_code == 200
    assert c.get("/api/linkedin/sequence").json()["builtin"] is True


def test_ab_variants_still_split_and_saved_notes_win(tmp_path):
    c, ids = make(tmp_path)
    store = c.app.state.store
    r = c.put("/api/linkedin/variants", json={"B": "Hi {first_name}, note B for testing the split here. Would be good to connect."})
    assert r.status_code == 200
    lead = {"id": "x", "contact_name": "Ravi Kumar", "linkedin_seq": {"variant": "B"}}
    assert li.render(li.STEPS[0], lead, store.read("settings")).startswith("Hi Ravi, note B for testing")
    lead_a = {**lead, "linkedin_seq": {"variant": "A"}}
    assert li.render(li.STEPS[0], lead_a, store.read("settings")) == li.render(li.STEPS[0], {"contact_name": "Ravi Kumar"}, {})
    assert {li.pick_variant(str(i)) for i in range(20)} == {"A", "B"}


def test_preview_for_a_sample_lead(tmp_path):
    c, ids = make(tmp_path)
    r = c.post("/api/linkedin/sequence/preview", json={"text": "Hi {first_name}, at {company}. {hook}", "kind": "connect", "title": "CHRO"}).json()
    assert r["text"].startswith("Hi Venkataraghavan, at Example Realty") and r["persona"] == "chro" and r["limit"] == 300
    assert r["length"] == len(r["text"]) and r["problem"] == ""
    real = c.post("/api/linkedin/sequence/preview", json={"text": "Hi {first_name}", "lead_id": ids["Acme"]}).json()
    assert real["text"] == "Hi Ravi" and real["limit"] is None
    bad = c.post("/api/linkedin/sequence/preview", json={"text": "Hi {who}"}).json()
    assert "who" in bad["problem"]


def test_only_the_owner_can_change_the_sequence(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    h = lambda t: {"Authorization": f"Bearer {t}"}
    owner = c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    c.post("/api/users", json={"email": "ravi@example.com", "name": "Ravi", "role": "caller", "password": "long-password-1"}, headers=h(owner))
    ravi = c.post("/api/login", json={"email": "ravi@example.com", "password": "long-password-1"}).json()["token"]
    steps = c.get("/api/linkedin/sequence", headers=h(ravi)).json()["steps"]
    assert c.put("/api/linkedin/sequence", json={"steps": steps}, headers=h(ravi)).status_code == 403
    assert c.post("/api/linkedin/sequence/reset", headers=h(ravi)).status_code == 403
    assert c.put("/api/linkedin/sequence", json={"steps": steps}, headers=h(owner)).status_code == 200
