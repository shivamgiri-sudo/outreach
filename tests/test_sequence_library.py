"""Editable, multiple email sequences: CRUD + validation, versions, the runner on a custom sequence, A/B winner, snippets."""
from collections import Counter
from datetime import timedelta

from fastapi.testclient import TestClient

from app import sequence_library as lib
from app.main import create_app
from app.sequences import DEFAULT_SEQUENCE
from tests.flow_helpers import T0, lead, make_client, patch_lead, store_of

UNSUB = "\n\nNot relevant? Unsubscribe: {{unsub_link}}"
ADMIN = "admin-pass-123"


def two_steps(day2=3, subject1="Quick question for {{company}}"):
    return [{"day": 0, "name": "Opener", "subject": subject1, "body": "{{greeting}}\n\nA short note for {{company}}." + UNSUB},
            {"day": day2, "name": "Follow-up", "subject": "Following up", "body": "{{greeting}}\n\nJust checking in." + UNSUB}]


def create(c, steps=None, name="Founders"):
    r = c.post("/api/sequences", json={"name": name, "notes": "small companies", "steps": steps or two_steps()})
    assert r.status_code == 200, r.text
    return r.json()


def run(c, now):
    r = c.post("/api/sequences/run", json={"now": now.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


# ---------- CRUD and validation ----------
def test_default_is_listed_read_only_and_can_be_duplicated(tmp_path):
    c = make_client(tmp_path)
    listed = c.get("/api/sequences").json()
    assert listed[0]["id"] == "default" and listed[0]["builtin"] and len(listed[0]["steps"]) == len(DEFAULT_SEQUENCE)
    assert c.put("/api/sequences/default", json={"name": "x"}).status_code == 400
    assert c.post("/api/sequences/default/archive", json={"archived": True}).status_code == 400
    copy = c.post("/api/sequences/default/duplicate", json={}).json()
    assert copy["id"] != "default" and copy["name"] == "Copy of Default (6 emails)" and not copy.get("builtin")
    assert [s["day"] for s in copy["steps"]] == [s["day"] for s in DEFAULT_SEQUENCE]
    assert copy["steps"][0]["name"] == "Email 1 — compromised hiring" and "Read-only" not in copy["notes"]
    assert len(c.get("/api/sequences").json()) == 2


def test_validation_refuses_bad_sequences(tmp_path):
    c = make_client(tmp_path)
    bad = {
        "no unsubscribe": [{"day": 0, "subject": "Hi", "body": "Hello {{company}}"}],
        "first day not 0": [{"day": 1, "subject": "Hi", "body": "x" + UNSUB}],
        "days not increasing": two_steps(day2=0),
        "unknown placeholder": [{"day": 0, "subject": "Hi {{surname}}", "body": "x" + UNSUB}],
        "spaced placeholder": [{"day": 0, "subject": "Hi", "body": "{{ greeting }}" + UNSUB}],
        "subject too long": [{"day": 0, "subject": "x" * 201, "body": "x" + UNSUB}],
        "too many steps": [{"day": i, "subject": "s", "body": "b" + UNSUB} for i in range(11)],
        "no steps": [],
        "variant without unsub": [{"day": 0, "subject": "s", "body": "b" + UNSUB, "variants": [{"name": "B", "body": "no link"}]}],
        "variant named A": [{"day": 0, "subject": "s", "body": "b" + UNSUB, "variants": [{"name": "A", "subject": "t"}]}],
    }
    for why, steps in bad.items():
        r = c.post("/api/sequences", json={"name": why, "steps": steps})
        assert r.status_code == 400, why
    assert c.post("/api/sequences", json={"name": "", "steps": two_steps()}).status_code == 400
    assert len(c.get("/api/sequences").json()) == 1


def test_edit_archive_and_enrol_choice(tmp_path):
    c = make_client(tmp_path)
    seq = create(c)
    r = c.put(f"/api/sequences/{seq['id']}", json={"name": "Founders v2", "steps": two_steps(day2=4)})
    assert r.status_code == 200 and r.json()["name"] == "Founders v2" and r.json()["version"] == 1   # not in use: edited in place
    a, b = lead(c, "Acme")["id"], lead(c, "Beta")["id"]
    assert c.post(f"/api/leads/{a}/enroll", json={"sequence_id": "nope"}).status_code == 400
    assert c.post(f"/api/sequences/{seq['id']}/archive", json={"archived": True}).json()["archived"] is True
    assert seq["id"] not in [s["id"] for s in c.get("/api/sequences").json()]
    assert seq["id"] in [s["id"] for s in c.get("/api/sequences?archived=true").json()]
    assert c.post(f"/api/leads/{a}/enroll", json={"sequence_id": seq["id"]}).status_code == 400       # archived
    c.post(f"/api/sequences/{seq['id']}/archive", json={"archived": False})
    got = c.post(f"/api/leads/{a}/enroll", json={"sequence_id": seq["id"]}).json()
    assert got["sequence"]["sequence_id"] == seq["id"] and got["sequence"]["version"] == 1
    plain = c.post(f"/api/leads/{b}/enroll").json()                                                    # no body: the default
    assert plain["sequence"]["sequence_id"] == "default"
    assert c.get(f"/api/sequences/{seq['id']}").json()["in_use"] == 1


# ---------- versions ----------
def test_changing_days_of_a_sequence_in_use_makes_a_new_version(tmp_path):
    c = make_client(tmp_path)
    seq = create(c)
    a, b = lead(c, "Acme")["id"], lead(c, "Beta")["id"]
    c.post(f"/api/leads/{a}/enroll", json={"sequence_id": seq["id"]})
    wording = c.put(f"/api/sequences/{seq['id']}", json={"steps": two_steps(subject1="New words for {{company}}")}).json()
    assert wording["version"] == 1 and not wording["new_version"]                  # same days: same version
    changed = c.put(f"/api/sequences/{seq['id']}", json={"steps": two_steps(day2=6)}).json()
    assert changed["version"] == 2 and changed["new_version"] and changed["history"][0]["version"] == 1
    store = store_of(c)
    a_lead = next(l for l in store.read("leads") if l["id"] == a)
    assert [s["day"] for s in lib.steps_for(store, a_lead["sequence"])] == [0, 3]          # Acme keeps its days
    b_seq = c.post(f"/api/leads/{b}/enroll", json={"sequence_id": seq["id"]}).json()["sequence"]
    assert b_seq["version"] == 2 and [s["day"] for s in lib.steps_for(store, b_seq)] == [0, 6]


# ---------- the runner ----------
def test_runner_sends_the_leads_own_sequence_in_practice_mode(tmp_path):
    c = make_client(tmp_path)
    seq = create(c)
    a = lead(c, "Acme")["id"]
    c.post(f"/api/leads/{a}/enroll", json={"sequence_id": seq["id"]})
    assert run(c, T0)["sent"] == 1
    msg = store_of(c).read("outbox")[-1]
    assert msg["status"] == "dry_run" and msg["subject"] == "Quick question for Acme"
    assert msg["sequence_id"] == seq["id"] and msg["step_day"] == 0 and msg["variant"] == "A" and "/u/" in msg["body"]
    assert run(c, T0 + timedelta(days=1))["sent"] == 0                           # day 3 not due yet
    assert run(c, T0 + timedelta(days=3))["sent"] == 1
    assert store_of(c).read("outbox")[-1]["subject"] == "Following up"
    assert run(c, T0 + timedelta(days=9))["sent"] == 0                           # finished: only 2 steps
    lead_now = lead(c, "Acme")
    assert lead_now["sequence"]["practice_days"] == [0, 3] and lead_now["sequence"]["sent_days"] == []


def test_existing_leads_without_a_sequence_id_get_the_default_exactly(tmp_path):
    c = make_client(tmp_path)
    create(c)
    a = lead(c, "Acme")["id"]
    c.post(f"/api/leads/{a}/enroll")
    patch_lead(c, a, sequence={"enrolled_at": "2026-11-03T04:00:00+00:00", "sent_days": [], "stopped": None})   # an old lead
    assert run(c, T0)["sent"] == 1
    msg = store_of(c).read("outbox")[-1]
    assert msg["subject"] == DEFAULT_SEQUENCE[0]["subject"] and msg["step"] == DEFAULT_SEQUENCE[0]["name"]
    assert "sequence_id" not in msg and msg["variant"] == "default"


def test_email_now_uses_the_leads_sequence_and_missing_sequence_is_held(tmp_path):
    c = make_client(tmp_path)
    seq = create(c)
    a, b = lead(c, "Acme")["id"], lead(c, "Beta")["id"]
    c.post(f"/api/leads/{a}/enroll", json={"sequence_id": seq["id"]})
    r = c.post(f"/api/leads/{a}/email-now")
    assert r.status_code == 200 and r.json()["step"] == "Opener"
    c.post(f"/api/leads/{b}/enroll", json={"sequence_id": seq["id"]})
    store_of(c).write("sequences", [])                                                # gone (data restored from elsewhere)
    assert c.post(f"/api/leads/{b}/email-now").status_code == 400
    assert run(c, T0)["blocked"] >= 1
    assert "no longer exists" in lead(c, "Beta")["sequence"]["blocked"]


def test_runner_appends_unsubscribe_if_a_saved_step_lost_it(tmp_path):
    c = make_client(tmp_path)
    seq = create(c)
    store_of(c).update("sequences", lambda rows: [{**r, "steps": [{**r["steps"][0], "body": "{{greeting}} hi"}, r["steps"][1]]} for r in rows])
    a = lead(c, "Acme")["id"]
    c.post(f"/api/leads/{a}/enroll", json={"sequence_id": seq["id"]})
    run(c, T0)
    assert "/u/" in store_of(c).read("outbox")[-1]["body"]


# ---------- A/B ----------
def test_variants_split_evenly_and_stay_fixed_per_lead():
    step = {"day": 0, "subject": "s", "body": "b", "variants": [{"name": "B", "subject": "t"}]}
    picks = [lib.assign_variant(f"lead{i}", "seq1", step) for i in range(1000)]
    counts = Counter(picks)
    assert set(counts) == {"A", "B"} and 400 < counts["A"] < 600
    assert lib.assign_variant("lead7", "seq1", step) == picks[7]
    assert lib.assign_variant("lead7", "seq1", {**step, "winner": "B"}) == "B"
    chosen = lib.apply_variant({**step, "subject_no_role": "x"}, "B")
    assert chosen["subject"] == "t" and "subject_no_role" not in chosen and chosen["ab_variant"] == "B"


def test_winner_rule():
    cell = lambda sent, replies: {"sent": sent, "replies": replies}
    assert lib.pick_winner({"A": cell(29, 0), "B": cell(40, 9)}, ["A", "B"]) is None          # too few sends
    assert lib.pick_winner({"A": cell(30, 1), "B": cell(30, 2)}, ["A", "B"]) is None          # too few replies
    assert lib.pick_winner({"A": cell(40, 4), "B": cell(40, 5)}, ["A", "B"]) is None          # too close
    assert lib.pick_winner({"A": cell(40, 1), "B": cell(40, 6)}, ["A", "B"]) == "B"
    assert lib.pick_winner({"A": cell(50, 0), "B": cell(50, 3)}, ["A", "B"]) == "B"


def test_winner_is_saved_from_outbox_and_replies_and_then_only_it_is_sent(tmp_path):
    c = make_client(tmp_path)
    steps = two_steps()
    steps[0]["variants"] = [{"name": "B", "subject": "Another angle for {{company}}"}]
    seq = create(c, steps)
    store = store_of(c)
    leads, outbox = [], []
    for i in range(70):
        v = "A" if i < 35 else "B"
        replied = (v == "B" and i % 5 == 0) or (v == "A" and i == 0)
        timeline = [{"at": "2026-11-04T10:00:00+05:30", "kind": "reply", "text": "[question] (email, pasted) tell me more"}] if replied else []
        leads.append({"id": f"L{i}", "stage": "contacted", "timeline": timeline,
                      "sequence": {"sequence_id": seq["id"], "version": 1, "enrolled_at": "2026-11-03T04:00:00+00:00", "stopped": None}})
        outbox.append({"lead_id": f"L{i}", "to": f"x{i}@e.in", "status": "sent", "sequence_id": seq["id"], "step_day": 0,
                       "variant": v, "sent_at": "2026-11-03T10:00:00+05:30", "sent_at_ist_date": "2026-11-03"})
    store.write("leads", leads)
    store.write("outbox", outbox)
    stats = c.get(f"/api/sequences/{seq['id']}/stats").json()
    cells = {v["name"]: v for v in stats["steps"][0]["variants"]}
    assert cells["A"]["sent"] == 35 and cells["A"]["replies"] == 1 and cells["B"]["replies"] == 7
    assert lib.evaluate_winners(store) == [(seq["id"], 0, "B")]
    saved = lib.get_sequence(store, seq["id"])
    assert saved["steps"][0]["winner"] == "B"
    assert {lib.assign_variant(f"new{i}", seq["id"], saved["steps"][0]) for i in range(50)} == {"B"}
    assert lib.evaluate_winners(store) == []                                    # decided once


def test_ab_variant_is_sent_and_recorded(tmp_path):
    c = make_client(tmp_path)
    steps = two_steps()
    steps[0]["variants"] = [{"name": "B", "subject": "Another angle for {{company}}"}]
    seq = create(c, steps)
    a = lead(c, "Acme")["id"]
    c.post(f"/api/leads/{a}/enroll", json={"sequence_id": seq["id"]})
    run(c, T0)
    msg = store_of(c).read("outbox")[-1]
    expected = lib.assign_variant(a, seq["id"], steps[0])
    assert msg["variant"] == expected
    assert msg["subject"] == ("Another angle for Acme" if expected == "B" else "Quick question for Acme")


# ---------- previews ----------
def test_new_and_old_preview_routes(tmp_path):
    c = make_client(tmp_path)
    old = c.get("/api/sequence/0/preview")
    assert old.status_code == 200 and old.json()["subject"] == DEFAULT_SEQUENCE[0]["subject"]
    new = c.get("/api/sequences/default/steps/0/preview").json()
    assert new["subject"] == old.json()["subject"] and "<html" in new["html"].lower()
    steps = two_steps()
    steps[0]["variants"] = [{"name": "B", "subject": "Another angle for {{company}}"}]
    seq = create(c, steps)
    assert c.get(f"/api/sequences/{seq['id']}/steps/0/preview?variant=B").json()["subject"] == "Another angle for Acme Retail"
    assert c.get(f"/api/sequences/{seq['id']}/steps/5/preview").status_code == 404
    draft = c.post("/api/sequences/preview-draft", json={"step": {"subject": "Hi {{company}}", "body": "{{greeting}}" + UNSUB}}).json()
    assert draft["subject"] == "Hi Acme Retail" and draft["missing"] == []


# ---------- roles and snippets ----------
def _auth_client(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password=ADMIN), client=("203.0.113.9", 50000))
    owner = c.post("/api/login", json={"password": ADMIN}).json()["token"]
    tokens = {"owner": owner}
    for email, role in (("cal@x.in", "caller"), ("cal2@x.in", "caller"), ("view@x.in", "viewer")):
        r = c.post("/api/users", json={"email": email, "name": email.split("@")[0], "role": role, "password": "long-password-1"},
                   headers={"Authorization": f"Bearer {owner}"})
        assert r.status_code == 200, r.text
        tokens[email] = c.post("/api/login", json={"email": email, "password": "long-password-1"}).json()["token"]
    return c, {k: {"Authorization": f"Bearer {v}"} for k, v in tokens.items()}


def test_only_the_owner_edits_sequences_everyone_reads(tmp_path):
    c, h = _auth_client(tmp_path)
    body = {"name": "Mine", "steps": two_steps()}
    assert c.post("/api/sequences", json=body, headers=h["cal@x.in"]).status_code == 403
    seq = c.post("/api/sequences", json=body, headers=h["owner"]).json()
    assert c.put(f"/api/sequences/{seq['id']}", json={"name": "x"}, headers=h["cal@x.in"]).status_code == 403
    assert c.post(f"/api/sequences/{seq['id']}/duplicate", json={}, headers=h["cal@x.in"]).status_code == 403
    assert c.post(f"/api/sequences/{seq['id']}/archive", json={}, headers=h["cal@x.in"]).status_code == 403
    assert len(c.get("/api/sequences", headers=h["view@x.in"]).json()) == 2


def test_snippets_callers_make_their_own_owner_deletes_any(tmp_path):
    c, h = _auth_client(tmp_path)
    s = {"name": "Pricing reply", "channel": "email", "subject": "Pricing for {{company}}", "body": "{{greeting}} here it is."}
    assert c.post("/api/snippets", json=s, headers=h["view@x.in"]).status_code == 403
    mine = c.post("/api/snippets", json=s, headers=h["cal@x.in"]).json()
    assert mine["created_by_email"] == "cal@x.in"
    assert c.post("/api/snippets", json={**s, "channel": "fax"}, headers=h["cal@x.in"]).status_code == 400
    assert c.post("/api/snippets", json={**s, "body": "Hi {{nickname}}"}, headers=h["cal@x.in"]).status_code == 400
    wa = c.post("/api/snippets", json={"name": "WA hello", "channel": "whatsapp", "body": "Hi {{first_name}}"}, headers=h["cal2@x.in"]).json()
    assert wa["subject"] == ""
    assert [x["name"] for x in c.get("/api/snippets?channel=email", headers=h["view@x.in"]).json()] == ["Pricing reply"]
    assert c.put(f"/api/snippets/{mine['id']}", json={"name": "Taken"}, headers=h["cal2@x.in"]).status_code == 403
    assert c.delete(f"/api/snippets/{mine['id']}", headers=h["cal2@x.in"]).status_code == 403
    assert c.put(f"/api/snippets/{mine['id']}", json={"name": "Pricing v2"}, headers=h["cal@x.in"]).json()["name"] == "Pricing v2"
    assert c.delete(f"/api/snippets/{mine['id']}", headers=h["owner"]).status_code == 200
    assert c.delete(f"/api/snippets/{wa['id']}", headers=h["cal2@x.in"]).status_code == 200
    assert c.get("/api/snippets", headers=h["owner"]).json() == []
