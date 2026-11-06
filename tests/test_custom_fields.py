"""Custom lead fields: definitions (CRUD + validation), drawer edits with audit, smart import and quick paste mapping,
CSV export, {{custom.<key>}} placeholders with a safe fallback, and flow If / Switch conditions."""
import csv
import io

from fastapi.testclient import TestClient

from app import flow_actions as act, flows, lead_fields
from app.main import create_app
from app.sequences import lead_variables, render

FIELDS = [
    {"label": "Budget (₹ lakh)", "type": "number", "show_in_table": True, "filterable": True},
    {"key": "property_type", "label": "Property type", "type": "select", "options": ["Flat", "Villa", "Plot"], "filterable": True},
    {"key": "visit_date", "label": "Site visit", "type": "date"},
    {"key": "brochure", "label": "Brochure link", "type": "url"},
]
CSV = ("company,contact_name,email,phone,Budget (₹ lakh),Property type,Site visit\n"
       "Acme,Ravi Kumar,ravi@acme.com,98765 43210,\"1,20\",villa,20/11/2026\n"
       "Beta,Meena Shah,meena@beta.com,98765 43211,abc,Castle,\n")


def client(tmp_path, fields=FIELDS):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    if fields:
        assert c.put("/api/lead-fields", json={"fields": fields}).status_code == 200
    return c


def by_company(c):
    return {l["company"]: l for l in c.get("/api/leads").json()}


def test_field_definitions_are_validated_and_saved(tmp_path):
    c = client(tmp_path)
    got = c.get("/api/lead-fields").json()
    assert [f["key"] for f in got["fields"]] == ["budget_lakh", "property_type", "visit_date", "brochure"]
    assert got["fields"][0]["show_in_table"] is True and "select" in got["types"]
    for bad, why in ([{"label": ""}], "name"), ([{"label": "A", "key": "1x"}], "start with a letter"), \
                    ([{"label": "Email"}], "built-in"), ([{"label": "A"}, {"label": "A"}], "twice"), \
                    ([{"label": "A", "type": "colour"}], "type"), ([{"label": "A", "type": "select"}], "option"):
        r = c.put("/api/lead-fields", json={"fields": bad})
        assert r.status_code == 400 and why in r.json()["detail"], (bad, r.json())
    assert c.put("/api/lead-fields", json={"fields": []}).json() == {"fields": []}


def test_values_are_coerced_by_type():
    f = lambda t, **k: {"key": "x", "label": "X", "type": t, **k}
    assert lead_fields.coerce(f("number"), "₹ 1,250.50") == 1250.5 and lead_fields.coerce(f("number"), "12") == 12
    assert lead_fields.coerce(f("date"), "20/11/2026") == "2026-11-20"
    assert lead_fields.coerce(f("select", options=["Flat", "Villa"]), "villa") == "Villa"
    assert lead_fields.coerce(f("url"), "example.com/a") == "https://example.com/a"
    assert lead_fields.coerce(f("email"), "A@B.COM") == "a@b.com" and lead_fields.coerce(f("text"), "  ") == ""
    for kind, value in (("number", "abc"), ("date", "soon"), ("email", "nope"), ("phone", "12"), ("url", "nota link")):
        try:
            lead_fields.coerce(f(kind), value)
            raise AssertionError(kind)
        except lead_fields.EditRefused:
            pass


def test_drawer_edit_is_validated_and_audited(tmp_path):
    c = client(tmp_path)
    c.post("/api/leads/import", json={"csv": "company,email\nAcme,ravi@acme.com\n", "source": "apollo"})
    lid = by_company(c)["Acme"]["id"]
    r = c.patch(f"/api/leads/{lid}/custom", json={"values": {"budget_lakh": "85", "property_type": "flat"}, "reason": "call"})
    assert r.status_code == 200 and r.json()["custom"] == {"budget_lakh": 85, "property_type": "Flat"}
    assert sorted(r.json()["changed"]) == ["custom.budget_lakh", "custom.property_type"]
    log = c.get(f"/api/leads/{lid}/changes").json()
    assert {(row["label"], row["old"], row["new"]) for row in log} == {("Budget (₹ lakh)", "", "85"), ("Property type", "", "Flat")}
    assert "Budget (₹ lakh): (empty) → 85" in c.get(f"/api/leads/{lid}").json()["timeline"][-1]["text"]
    assert c.patch(f"/api/leads/{lid}/custom", json={"values": {"budget_lakh": "lots"}}).status_code == 400
    assert c.patch(f"/api/leads/{lid}/custom", json={"values": {"unknown": "1"}}).status_code == 400
    assert c.patch(f"/api/leads/{lid}/custom", json={"values": {"budget_lakh": "85"}}).json()["changed"] == []
    cleared = c.patch(f"/api/leads/{lid}/custom", json={"values": {"budget_lakh": ""}}).json()
    assert cleared["custom"] == {"property_type": "Flat"}
    assert c.patch("/api/leads/nope/custom", json={"values": {"budget_lakh": "1"}}).status_code == 404


def test_caller_without_edit_rights_cannot_change_custom_values(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    h = lambda t: {"Authorization": f"Bearer {t}"}
    owner = c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    c.post("/api/users", json={"email": "ravi@example.com", "name": "Ravi", "role": "caller", "password": "long-password-1"}, headers=h(owner))
    assert c.put("/api/lead-fields", json={"fields": FIELDS}, headers=h(owner)).status_code == 200
    c.post("/api/leads/import", json={"csv": "company,email\nAcme,ravi@acme.com\n", "source": "apollo"}, headers=h(owner))
    lid = c.get("/api/leads", headers=h(owner)).json()[0]["id"]
    ravi = c.post("/api/login", json={"email": "ravi@example.com", "password": "long-password-1"}).json()["token"]
    assert c.put("/api/lead-fields", json={"fields": []}, headers=h(ravi)).status_code == 403
    assert c.patch(f"/api/leads/{lid}/custom", json={"values": {"budget_lakh": 5}}, headers=h(ravi)).status_code == 200
    c.put("/api/users/ravi@example.com", json={"can_edit_contacts": False}, headers=h(owner))
    assert c.patch(f"/api/leads/{lid}/custom", json={"values": {"budget_lakh": 6}}, headers=h(ravi)).status_code == 403


def test_smart_import_maps_custom_columns_and_skips_unreadable_values(tmp_path):
    c = client(tmp_path)
    preview = c.post("/api/import/preview", json={"csv": CSV, "source": "apollo"}).json()
    mapping = preview["mapping"]
    assert mapping["Budget (₹ lakh)"] == "custom:budget_lakh" and mapping["Property type"] == "custom:property_type"
    assert mapping["Site visit"] == "custom:visit_date" and preview["fields"]["custom:budget_lakh"] == "Budget (₹ lakh) (custom)"
    done = c.post("/api/import/commit", json={"csv": CSV, "source": "apollo"}).json()
    assert done["summary"]["create"] == 2 and done["summary"]["warnings"]["custom_unreadable"] == 2
    leads = by_company(c)
    assert leads["Acme"]["custom"] == {"budget_lakh": 120, "property_type": "Villa", "visit_date": "2026-11-20"}
    assert "custom" not in leads["Beta"]
    # a second file fills gaps only
    more = "company,email,Budget (₹ lakh),Property type\nBeta,meena@beta.com,40,Plot\nAcme,ravi@acme.com,999,Flat\n"
    c.post("/api/import/commit", json={"csv": more, "source": "apollo"})
    leads = by_company(c)
    assert leads["Beta"]["custom"] == {"budget_lakh": 40, "property_type": "Plot"} and leads["Acme"]["custom"]["budget_lakh"] == 120
    # an unknown column can be mapped to a custom field by hand; a mapping to a field that doesn't exist is refused
    odd = "company,email,Ticket size\nGamma,g@gamma.com,55\n"
    r = c.post("/api/import/commit", json={"csv": odd, "source": "apollo", "mapping": {"Ticket size": "custom:budget_lakh"}})
    assert r.status_code == 200 and by_company(c)["Gamma"]["custom"] == {"budget_lakh": 55}
    assert c.post("/api/import/preview", json={"csv": odd, "mapping": {"Ticket size": "custom:nope"}}).status_code == 400


def test_quick_paste_keeps_columns_matching_a_custom_field(tmp_path):
    c = client(tmp_path)
    paste = "company,email,budget_lakh,Property Type,Unrelated\nAcme,ravi@acme.com,75,flat,x\nBeta,b@beta.com,oops,,y\n"
    assert c.post("/api/leads/import", json={"csv": paste, "source": "apollo"}).json()["created"] == 2
    leads = by_company(c)
    assert leads["Acme"]["custom"] == {"budget_lakh": 75, "property_type": "Flat"} and "custom" not in leads["Beta"]


def test_csv_export_has_one_column_per_custom_field(tmp_path):
    c = client(tmp_path)
    c.post("/api/import/commit", json={"csv": CSV, "source": "apollo"})
    text = c.get("/api/export/leads.csv").content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    assert {"budget_lakh", "property_type", "visit_date", "brochure"} <= set(rows[0])
    acme = next(r for r in rows if r["company"] == "Acme")
    assert acme["budget_lakh"] == "120" and acme["property_type"] == "Villa" and acme["brochure"] == ""


def test_placeholders_render_with_a_safe_fallback(tmp_path):
    lead = {"id": "l1", "contact_name": "Ravi Kumar", "company": "Acme", "custom": {"budget_lakh": 120, "property_type": "Villa"}}
    v = lead_variables(lead, "Team", "https://x.example")
    text, missing = render("Budget {{custom.budget_lakh}} lakh, {{custom.property_type}}; visit {{custom.visit_date}}.", v)
    assert text == "Budget 120 lakh, Villa; visit ." and missing == []
    assert render("{{custom.Bad-Key}}", v)[0] == "{{custom.Bad-Key}}"
    c = client(tmp_path)
    body = {"name": "With custom", "steps": [{"day": 0, "subject": "For {{company}}",
                                              "body": "{{greeting}} Your budget: {{custom.budget_lakh}} lakh. {{unsub_link}}"}]}
    seq = c.post("/api/sequences", json=body)
    assert seq.status_code == 200, seq.text
    preview = c.get(f"/api/sequences/{seq.json()['id']}/steps/0/preview").json()
    assert "Your budget: 25 lakh" in preview["text"] and preview["missing"] == []     # the sample shows an example value
    bad = c.post("/api/sequences", json={**body, "steps": [{**body["steps"][0], "body": "{{custom.x y}} {{unsub_link}}"}]})
    assert bad.status_code == 400
    assert c.post("/api/snippets", json={"name": "S", "channel": "whatsapp", "body": "Budget {{custom.budget_lakh}}"}).status_code == 200


def test_whatsapp_and_flow_email_fill_custom_values(tmp_path):
    c = client(tmp_path)
    c.post("/api/import/commit", json={"csv": CSV, "source": "apollo"})
    store = c.app.state.store
    lead = next(l for l in store.read("leads") if l["company"] == "Acme")
    from datetime import datetime, timezone
    from app.flow_actions import text_for
    text, missing = text_for(store, lead, "Budget {{custom.budget_lakh}}, {{custom.visit_date}}", "https://x.example",
                             datetime(2026, 11, 3, 5, tzinfo=timezone.utc))
    assert text == "Budget 120, 2026-11-20" and missing == []
    node = {"id": "e", "type": "email", "config": {"subject": "Hi", "body": "{{custom.budget_lakh}} {{my_company}} {{unsub_link}}"}}
    assert flows.config_errors(node) == []


def test_flow_conditions_use_custom_fields():
    lead = {"id": "l", "title": "", "custom": {"budget_lakh": 120, "property_type": "Villa", "visit_date": "2026-11-20"}}
    facts = act.lead_facts(lead)
    check = lambda field, op, value=None: act.evaluate({"field": field, "op": op, "value": value}, facts)[0]
    assert check("custom:budget_lakh", "gt", "100") and not check("custom:budget_lakh", "lt", "100")
    assert check("custom:property_type", "eq", "villa") and check("custom:property_type", "in", "Flat, Villa")
    assert check("custom:visit_date", "gt", "2026-11-10") and check("custom:visit_date", "lt", "2026-12-01")
    assert check("custom:budget_lakh", "exists") and not check("custom:missing", "exists")
    assert not check("custom:missing", "gt", "5")
    assert act.evaluate({"field": "custom:budget_lakh", "op": "eq", "value": "120"}, facts)[0]
    # validation: custom fields are accepted in If and Switch; bad keys and non-numbers for more/less than are not
    assert flows.config_errors({"id": "i", "type": "if", "config": {"field": "custom:budget_lakh", "op": "gt", "value": "50"}}) == []
    assert flows.config_errors({"id": "i", "type": "if", "config": {"field": "custom:visit_date", "op": "lt", "value": "2026-12-01"}}) == []
    assert flows.config_errors({"id": "i", "type": "if", "config": {"field": "custom:budget_lakh", "op": "gt", "value": "big"}})
    assert flows.config_errors({"id": "i", "type": "if", "config": {"field": "custom:Bad Key", "op": "eq", "value": "1"}})
    assert flows.config_errors({"id": "s", "type": "switch", "config": {"field": "custom:property_type", "cases": ["Flat", "Villa"]}}) == []


def test_flow_engine_branches_on_a_custom_field(tmp_path):
    from datetime import datetime, timezone
    from app.flow_engine import tick
    c = client(tmp_path)
    c.post("/api/import/commit", json={"csv": CSV, "source": "apollo"})
    store = c.app.state.store
    nodes = [{"id": "trigger", "type": "trigger.manual", "config": {}, "x": 0, "y": 0},
             {"id": "big", "type": "if", "config": {"field": "custom:budget_lakh", "op": "gt", "value": "100"}, "x": 0, "y": 100},
             {"id": "hot", "type": "add_tag", "config": {"tag": "big-budget"}, "x": 0, "y": 200},
             {"id": "small", "type": "add_tag", "config": {"tag": "small-budget"}, "x": 200, "y": 200}]
    edges = [{"id": "e1", "from": "trigger", "to": "big", "label": "next"}, {"id": "e2", "from": "big", "to": "hot", "label": "yes"},
             {"id": "e3", "from": "big", "to": "small", "label": "no"}]
    flow = c.post("/api/flows", json={"name": "Budget split", "nodes": nodes, "edges": edges}).json()
    assert c.post(f"/api/flows/{flow['id']}/activate").status_code == 200, c.get(f"/api/flows/{flow['id']}").json()
    ids = [l["id"] for l in store.read("leads")]
    r = c.post(f"/api/flows/{flow['id']}/enroll", json={"lead_ids": ids})
    assert r.status_code == 200, r.text
    tick(store, datetime(2026, 11, 3, 6, tzinfo=timezone.utc))
    tags = {l["company"]: l.get("tags") or [] for l in store.read("leads")}
    assert tags["Acme"] == ["big-budget"] and tags["Beta"] == ["small-budget"]
