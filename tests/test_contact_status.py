"""One rule for "has anyone tried this lead": python (prospects) and JS (Leads page) must agree; saved views keep the filter."""
import json
import subprocess
from pathlib import Path

import pytest

from app import contact_status as cs, prospect_db as P, prospect_ingest as ingest, prospect_search as S
from tests.ownership_helpers import rig as make_rig

ROOT = Path(__file__).resolve().parent.parent
CASES = [
    ([], "never"),
    ([{"kind": "created"}, {"kind": "consent"}, {"kind": "stage"}], "never"),
    ([{"kind": "call", "text": "Call — no answer: x"}], "tried"),
    ([{"kind": "email", "text": "sent"}, {"kind": "reply", "text": "[auto_reply] out of office"}], "tried"),
    ([{"kind": "email", "text": "sent"}, {"kind": "reply", "text": "[bounce] 550"}], "tried"),
    ([{"kind": "call", "text": "Call — connected: spoke"}], "connected"),
    ([{"kind": "call", "text": "Call — call back: tomorrow"}], "connected"),
    ([{"kind": "call", "text": "Call — no answer"}, {"kind": "reply", "text": "yes please call"}], "replied"),
    ([{"kind": "linkedin", "text": "message"}, {"kind": "whatsapp", "text": "hi"}], "tried"),
]


def test_python_rule():
    for events, want in CASES:
        assert cs.of({"timeline": events})["status"] == want, events
    assert cs.of({"timeline": CASES[7][0]})["attempts"] == 1
    assert cs.of({})["status"] == "never"


def test_js_rule_matches_python(tmp_path):
    script = tmp_path / "t.mjs"
    script.write_text(f'import {{ contactOf, isFresh }} from "{ROOT}/web/src/contactStatus.js";\n'
                      f'const cases = {json.dumps([c[0] for c in CASES])};\n'
                      'console.log(JSON.stringify(cases.map((t) => contactOf({ timeline: t }).status)));\n'
                      'console.log(JSON.stringify([isFresh({ timeline: [], phone: "1" }), isFresh({ timeline: [], phone: "1", do_not_contact: true }), '
                      'isFresh({ timeline: [], stage: "disqualified", phone: "1" }), isFresh({ timeline: [] }), isFresh({ timeline: [{ kind: "call", text: "x" }], email: "a@b.c" })]));\n',
                      encoding="utf-8")
    (tmp_path / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    out = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got, fresh = [json.loads(l) for l in out.stdout.strip().splitlines()]
    assert got == [c[1] for c in CASES]
    assert fresh == [True, False, False, False, False]


@pytest.fixture(autouse=True)
def no_sync(monkeypatch):
    monkeypatch.setattr(ingest, "sync_soon", lambda store, every_seconds=120.0: False)


def test_saved_view_keeps_contact_filter(tmp_path):
    r = make_rig(tmp_path, mode_on=False)
    saved = r.c.post("/api/lead-views", json={"name": "Fresh", "filters": {"contact": "fresh"}}, headers=r.h["owner"])
    assert saved.status_code == 200, saved.text
    assert saved.json()["filters"]["contact"] == "fresh"
    bad = r.c.post("/api/lead-views", json={"name": "Bad", "filters": {"contact": "<script>"}}, headers=r.h["owner"]).json()
    assert bad["filters"]["contact"] == ""


def test_prospect_rows_carry_status_of_their_lead(tmp_path):
    r = make_rig(tmp_path, mode_on=False)
    acme = r.lead["Acme"]["id"]
    r.store.update_lead(acme, lambda l: {**l, "timeline": [*l.get("timeline", []), {"at": "2026-11-12T10:00:00+00:00", "kind": "call", "text": "Call — no answer"}]})
    ingest.sync_crm(r.store, force=True)
    P.upsert_person(r.store, {"company_domain": "pool.example", "full_name": "Pool Person", "email": "pool@pool.example"}, "apollo")
    rows = {p["full_name"]: p for p in S.search_people(r.store, {}, "relevance", 1, 50)["results"]}
    assert rows["Ravi Kumar"]["crm_status"] == "tried" and rows["Sita Rao"]["crm_status"] == "never"
    assert rows["Pool Person"]["crm_status"] == "" and not rows["Pool Person"]["in_crm"]


def test_leads_list_reads_outbox_activities_and_replies(tmp_path):
    """The Leads page's status must come from every place an attempt is recorded, not only the timeline (a live bug: leads
    with sent emails showed as never contacted)."""
    r = make_rig(tmp_path, mode_on=False)
    ids = {c: r.lead[c]["id"] for c in ("Acme", "Beta Ltd", "Cora Inc")}
    r.store.update("outbox", lambda rows: [*rows,
        {"id": "m1", "lead_id": ids["Acme"], "status": "sent", "sent_at": "2026-11-10T10:00:00+00:00", "subject": "hi", "to": "x@y.z"},
        {"id": "m2", "lead_id": ids["Cora Inc"], "status": "dry_run", "sent_at": "2026-11-10T10:00:00+00:00", "subject": "practice", "to": "x@y.z"}])
    r.store.update("activities", lambda rows: [*rows, {"id": "a1", "lead_id": ids["Beta Ltd"], "type": "call", "direction": "out", "at": "2026-11-11T10:00:00+00:00",
                                                       "outcome": "connected", "by": "Priya"}])
    by = {l["company"]: l for l in r.c.get("/api/leads", headers=r.h["owner"]).json()}
    assert by["Acme"]["contact_status"] == "tried" and by["Acme"]["contact_attempts"] == 1
    assert by["Beta Ltd"]["contact_status"] == "connected"
    assert by["Cora Inc"]["contact_status"] == "never" and by["Cora Inc"]["contact_attempts"] == 0     # a practice run is not an attempt
    r.store.update("inbox", lambda rows: [*rows, {"id": "i1", "lead_id": ids["Acme"], "channel": "email", "label": "interested", "text": "yes", "received_at": "2026-11-12T10:00:00+00:00"}])
    r.store.update("inbox", lambda rows: [*rows, {"id": "i2", "lead_id": ids["Cora Inc"], "channel": "email", "label": "auto_reply", "text": "ooo", "received_at": "2026-11-12T10:00:00+00:00"}])
    by = {l["company"]: l for l in r.c.get("/api/leads", headers=r.h["owner"]).json()}
    assert by["Acme"]["contact_status"] == "replied" and by["Cora Inc"]["contact_status"] == "never"


def test_disconnected_is_not_connected_and_one_attempt_counts_once():
    drop = {"timeline": [{"kind": "call", "at": "2026-11-12T10:00:00+00:00", "text": "Call — call disconnected: line dropped"}]}
    assert cs.of(drop)["status"] == "tried"
    both = {"id": "L1", "timeline": [{"kind": "call", "at": "2026-11-12T10:00:00+00:00", "text": "Call — no answer: x"}]}
    idx = {"L1": {"tries": [("call", "2026-11-12T10:00:05+00:00")], "reply": False, "reached": False}}        # same call, different second
    assert cs.of(both, idx)["attempts"] == 1
    two = {"id": "L1", "timeline": [{"kind": "call", "at": "2026-11-12T10:00:00+00:00", "text": "Call — no answer"}]}
    idx2 = {"L1": {"tries": [("call", "a"), ("call", "b")], "reply": False, "reached": False}}
    assert cs.of(two, idx2)["attempts"] == 2


def test_marked_leads_are_their_own_bucket_and_not_an_attempt():
    marked = {"id": "L9", "lead_quality": {"kind": "invalid", "by": "P"},
              "timeline": [{"kind": "call", "at": "2026-11-12T10:00:00+00:00", "text": "Call — invalid lead: no such firm"}]}
    idx = {"L9": {"tries": [], "reply": False, "reached": False}}
    got = cs.of(marked, idx)
    assert got["status"] == "invalid" and got["attempts"] == 0
    cons = {"id": "L8", "lead_quality": {"kind": "consultant", "by": "P"}, "timeline": [{"kind": "email", "at": "2026-11-01T10:00:00+00:00", "text": "sent"}]}
    assert cs.of(cons)["status"] == "consultant" and cs.of(cons)["attempts"] == 1       # a real earlier email still counts as an attempt


def test_lead_quality_report_by_source_and_reason():
    from app import lead_quality as lq
    leads = [{"source": "apollo"}, {"source": "apollo", "lead_quality": {"kind": "invalid", "by": "Priya", "reason": "fake"}},
             {"source": "apollo", "lead_quality": {"kind": "consultant", "by": "Amit"}}, {"source": "naukri"}, {"source": "naukri"}]
    rep = lq.report(leads)
    by = {s["source"]: s for s in rep["sources"]}
    assert by["apollo"]["leads"] == 3 and by["apollo"]["invalid_rate"] == 33.3 and by["apollo"]["consultant_rate"] == 33.3
    assert by["naukri"]["invalid"] == 0 and rep["sources"][0]["source"] == "apollo"     # worst source first
    assert rep["total"]["leads"] == 5 and rep["reasons"] == [{"reason": "fake", "count": 1}]
    assert {c["caller"] for c in rep["callers"]} == {"Priya", "Amit"}


def test_lead_quality_endpoint_is_scoped_like_the_leads(tmp_path):
    r = make_rig(tmp_path, mode_on=True)
    r.set("Acme", lead_quality={"kind": "invalid", "by": "Ravi", "reason": "fake"})                      # Ravi's lead
    owner = r.c.get("/api/lead-quality", headers=r.h["owner"]).json()
    assert owner["total"]["leads"] == 4 and owner["total"]["invalid"] == 1
    priya = r.c.get("/api/lead-quality", headers=r.h["priya"]).json()
    assert priya["total"]["invalid"] == 0 and priya["total"]["leads"] == 2        # only her own two leads (Beta and Other WS Co): not Ravi's Acme, not the unowned Cora


# ---------------------------------------------------------------- weekly review, manager restore, outlier flag
def _mark(c, lead_id, outcome, headers=None, **kw):
    return c.post(f"/api/leads/{lead_id}/call-outcome", json={"outcome": outcome, "notes": "n", **kw}, headers=headers or {})


def test_manager_can_restore_a_marked_lead(tmp_path):
    r = make_rig(tmp_path, mode_on=False)
    acme = r.lead["Acme"]["id"]
    assert r.c.post(f"/api/leads/{acme}/consent", json={"text": "Yes call me", "channel": "call"}, headers=r.h["owner"]).status_code == 200
    stage_before = next(l for l in r.store.read("leads") if l["id"] == acme)["stage"]
    assert _mark(r.c, acme, "invalid_lead", r.h["priya"], reason="fake").status_code == 200
    lead = next(l for l in r.store.read("leads") if l["id"] == acme)
    assert lead["stage"] == "disqualified" and lead["lead_quality"]["prev_stage"] == stage_before
    assert r.c.post(f"/api/leads/{acme}/quality-restore", headers=r.h["priya"]).status_code == 403          # a caller cannot undo a mark
    assert r.c.post(f"/api/leads/{acme}/quality-restore", headers=r.h["view"]).status_code == 403
    done = r.c.post(f"/api/leads/{acme}/quality-restore", headers=r.h["owner"])
    assert done.status_code == 200 and done.json() == {"lead_id": acme, "stage": stage_before, "was": "invalid"}
    lead = next(l for l in r.store.read("leads") if l["id"] == acme)
    assert "lead_quality" not in lead and lead["stage"] == stage_before
    assert any(e["kind"] == "quality" and "restored" in e["text"] for e in lead["timeline"])
    assert any(a.get("action") == "lead.quality_restore" for a in r.store.read("audit"))
    assert r.c.post(f"/api/leads/{acme}/quality-restore", headers=r.h["owner"]).status_code == 400           # nothing left to restore
    assert r.c.post("/api/leads/nope/quality-restore", headers=r.h["owner"]).status_code == 404


def test_restore_keeps_a_stage_the_lead_moved_to_since(tmp_path):
    r = make_rig(tmp_path, mode_on=False)
    beta = r.lead["Beta Ltd"]["id"]
    r.c.post(f"/api/leads/{beta}/consent", json={"text": "Yes call me", "channel": "call"}, headers=r.h["owner"])
    _mark(r.c, beta, "consultant_lead", r.h["priya"])
    out = r.c.post(f"/api/leads/{beta}/quality-restore", headers=r.h["owner"]).json()
    assert out["was"] == "consultant" and out["stage"] != "disqualified"                                   # a consultant never changed stage


def test_review_list_and_scope(tmp_path):
    r = make_rig(tmp_path, mode_on=True)
    r.set("Acme", lead_quality={"kind": "invalid", "by": "Ravi", "reason": "fake", "at": "2099-01-01T00:00:00+00:00"})
    r.set("Beta Ltd", lead_quality={"kind": "consultant", "by": "Priya", "at": "2000-01-01T00:00:00+00:00"})        # long ago: not this week
    got = r.c.get("/api/lead-quality/review", headers=r.h["owner"]).json()
    assert [x["company"] for x in got["rows"]] == ["Acme"] and got["rows"][0]["reason"] == "fake"
    assert r.c.get("/api/lead-quality/review", headers=r.h["priya"]).status_code == 403                    # managers only
    assert r.c.get("/api/lead-quality/review?days=90", headers=r.h["owner"]).status_code == 200


def test_outlier_flag():
    from app import lead_quality as lq
    callers = [{"caller": "Heavy", "marked": 12}, {"caller": "Normal", "marked": 2}, {"caller": "Few", "marked": 1}]
    got = {c["caller"]: c for c in lq.flag_outliers(callers, {"Heavy": 40, "Normal": 100, "Few": 8})}
    assert got["Heavy"]["outlier"] and got["Heavy"]["rate"] == 30.0
    assert not got["Normal"]["outlier"] and not got["Few"]["outlier"]          # Few has too few calls to judge
    assert lq.flag_outliers([{"caller": "X", "marked": 1}], {})[0]["outlier"] is False
