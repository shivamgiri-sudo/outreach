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
