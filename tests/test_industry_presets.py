"""Industry presets: every preset's content validates, apply creates a new sequence, replaces settings only at defaults
or with overwrite, is idempotent, and only the owner can apply."""
import re

import pytest
from fastapi.testclient import TestClient

from app import industry_presets as ip, linkedin_config, linkedin_seq as li, sequence_library as lib
from app.main import create_app
from app.template_vars import BRAND_PLACEHOLDERS, VAR, is_custom_placeholder

REQUIRED = {"recruitment", "real_estate", "b2b_saas", "education", "insurance", "healthcare", "agency", "manufacturing",
            "retail", "generic_b2b"}
INVENTED = re.compile(r"\b\d+\s?%|\b\d+x\b|guarantee(?!s? (results|a result))|proven|#1|best in class|leading provider", re.I)


def client(tmp_path):
    return TestClient(create_app(data_file=tmp_path / "d.json"))


def test_all_required_presets_are_listed(tmp_path):
    rows = client(tmp_path).get("/api/presets").json()["presets"]
    assert REQUIRED <= {p["id"] for p in rows}
    assert [p["id"] for p in rows if p["default"]] == ["recruitment"]
    assert all(p["description"] and p["lead_term"] for p in rows)


@pytest.mark.parametrize("preset", ip.all_presets(), ids=lambda p: p["id"])
def test_every_preset_validates(preset):
    assert ip.validate(preset) == []
    steps = preset["sequence"]["steps"]
    assert 4 <= len(steps) <= 6
    assert all(lib.UNSUB_TOKEN in s["body"] for s in steps)
    assert 3 <= len(preset["linkedin"]["personas"]) <= 6
    assert 5 <= len(preset["snippets"]) <= 8 and {"email", "whatsapp"} <= {s["channel"] for s in preset["snippets"]}
    assert 1 <= len(preset["flows"]) <= 2
    flow_types = {n["type"] for f in preset["flows"] for n in f["nodes"]}
    assert {"email", "whatsapp", "wait", "if"} <= flow_types
    assert preset["call_script"]["objections"] and preset["icp"]["target_titles"] and preset["icp"]["company_size"]
    # placeholders: all known (brand, custom or the engine's own)
    texts = [s["subject"] + s["body"] for s in steps] + [s["body"] for s in preset["snippets"]]
    for text in texts:
        for name in (m.group(1) for m in VAR.finditer(text)):
            assert name in lib.KNOWN_PLACEHOLDERS or is_custom_placeholder(name), (preset["id"], name)


@pytest.mark.parametrize("preset", ip.all_presets(), ids=lambda p: p["id"])
def test_connect_note_fits_with_a_long_name(preset):
    li_cfg = preset["linkedin"]
    for note in (li_cfg["steps"][0]["text"], li_cfg["variant_b"]):
        assert linkedin_config.stand_in_length(note) <= li.NOTE_LIMIT
        rendered = li.render({"n": 1, "kind": "connect", "text": note},
                             {"contact_name": "Venkataraghavan Subramaniam", "company": "A Very Long Company Name Private Limited"}, {})
        assert len(rendered) <= li.NOTE_LIMIT and not rendered.endswith("…")


@pytest.mark.parametrize("preset", [p for p in ip.all_presets() if p["id"] != "recruitment"], ids=lambda p: p["id"])
def test_new_presets_make_no_invented_claims_and_use_brand_placeholders(preset):
    words = " ".join(s["subject"] + " " + s["body"] for s in preset["sequence"]["steps"])
    assert not INVENTED.search(words), INVENTED.search(words).group(0)
    assert "BookMyInterview" not in str(preset)
    used = {m.group(1) for m in VAR.finditer(words)}
    assert {"my_company", "sign_off"} <= used and used & {"my_value_1", "my_value_2", "my_value_3"}
    assert set(BRAND_PLACEHOLDERS) >= {u for u in used if u.startswith("my_")}


def test_apply_on_a_fresh_install_sets_everything(tmp_path):
    c = client(tmp_path)
    r = c.post("/api/presets/real_estate/apply", json={})
    assert r.status_code == 200, r.text
    out = r.json()
    assert {"sequence", "linkedin", "call_script", "snippets", "scoring", "icp", "lead_term", "stage_labels", "flows"} <= set(out["changed"])
    seqs = c.get("/api/sequences").json()
    made = next(s for s in seqs if s["id"] == out["sequence_id"])
    assert made["name"].startswith("Real estate") and not made.get("builtin")
    assert next(s for s in seqs if s["id"] == "default")["builtin"] is True              # the built-in default is untouched
    seq = c.get("/api/linkedin/sequence").json()
    assert seq["steps"][0]["text"].startswith("Hi {first_name}, I work with people buying and investing in property")
    assert {p["key"] for p in seq["personas"]} == {"investor", "corporate", "founder", "broker"}
    assert c.get("/api/call-script").json()["script"][0]["text"].startswith("Hi {{first_name}}, this is {{rep}} from {{my_company}}")
    assert any(s["name"] == "Site visit confirmation" for s in c.get("/api/snippets").json())
    settings = c.app.state.store.read("settings")
    assert settings["lead_terms"]["open_roles"] == "Properties" and settings["stage_labels"]["trial"] == "Site visit"
    assert c.get("/api/lead-fields").json()["lead_term"] == "Properties"
    assert len(out["flow_ids"]) == 2 and all(f["status"] == "draft" for f in c.get("/api/flows").json())
    assert c.get("/api/icp").json()["text"].startswith("Home buyers")


def test_apply_is_idempotent(tmp_path):
    c = client(tmp_path)
    first = c.post("/api/presets/b2b_saas/apply").json()
    again = c.post("/api/presets/b2b_saas/apply").json()
    assert again["changed"] == [] and again["sequence_id"] == first["sequence_id"]
    assert len([s for s in c.get("/api/sequences").json() if s.get("preset") == "b2b_saas"]) == 1
    assert len(c.get("/api/flows").json()) == 2 and len(c.get("/api/snippets").json()) == 6


def test_another_preset_keeps_edited_settings_unless_overwrite(tmp_path):
    c = client(tmp_path)
    c.post("/api/presets/real_estate/apply")
    out = c.post("/api/presets/education/apply").json()
    assert "sequence" in out["changed"] and "flows" in out["changed"]          # new sequence and flows always added
    kept = {k["what"] for k in out["kept"]}
    assert {"linkedin", "call_script", "scoring", "icp", "lead_term"} <= kept
    assert c.get("/api/linkedin/sequence").json()["steps"][0]["text"].startswith("Hi {first_name}, I work with people buying")
    over = c.post("/api/presets/education/apply", json={"overwrite": True}).json()
    assert {"linkedin", "call_script", "snippets", "lead_term", "icp"} <= set(over["changed"])
    assert "sequence" not in over["changed"]                                    # one sequence per preset, never twice
    assert c.get("/api/linkedin/sequence").json()["steps"][0]["text"].startswith("Hi {first_name}, I work with schools")
    names = {s["name"] for s in c.get("/api/snippets").json()}
    assert "Demo class booked" in names and "Site visit confirmation" not in names   # the earlier preset's snippets go


def test_own_snippets_and_script_are_never_replaced_without_overwrite(tmp_path):
    c = client(tmp_path)
    c.post("/api/snippets", json={"name": "My own", "channel": "email", "body": "Hello {{first_name}}"})
    c.put("/api/call-script", json={"script": [{"title": "Mine", "text": "My words"}], "objections": []})
    out = c.post("/api/presets/agency/apply").json()
    assert {"snippets", "call_script"} <= {k["what"] for k in out["kept"]}
    assert [s["name"] for s in c.get("/api/snippets").json()] == ["My own"]
    over = c.post("/api/presets/agency/apply", json={"overwrite": True}).json()
    assert "snippets" in over["changed"] and "My own" in {s["name"] for s in c.get("/api/snippets").json()}


def test_applying_the_default_preset_matches_todays_content(tmp_path):
    c = client(tmp_path)
    out = c.post("/api/presets/recruitment/apply").json()
    assert "linkedin" in out["unchanged"] and "lead_term" in out["changed"]
    seq = next(s for s in c.get("/api/sequences").json() if s["id"] == out["sequence_id"])
    from app.sequences import DEFAULT_SEQUENCE
    assert [s["subject"] for s in seq["steps"]] == [s["subject"] for s in DEFAULT_SEQUENCE]


def test_unknown_preset_and_owner_only(tmp_path):
    c = client(tmp_path)
    assert c.post("/api/presets/nope/apply").status_code == 404
    assert c.get("/api/presets/real_estate").json()["lead_term"] == "Properties"
    a = TestClient(create_app(data_file=tmp_path / "a.json", admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    h = lambda t: {"Authorization": f"Bearer {t}"}
    owner = a.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]
    a.post("/api/users", json={"email": "ravi@example.com", "name": "Ravi", "role": "caller", "password": "long-password-1"}, headers=h(owner))
    ravi = a.post("/api/login", json={"email": "ravi@example.com", "password": "long-password-1"}).json()["token"]
    assert a.get("/api/presets", headers=h(ravi)).status_code == 200
    assert a.post("/api/presets/retail/apply", headers=h(ravi)).status_code == 403
    assert a.post("/api/presets/retail/apply", headers=h(owner)).status_code == 200


def test_preset_sequence_previews_with_brand_placeholders(tmp_path):
    c = client(tmp_path)
    c.app.state.store.update("settings", lambda s: {**s, "brand": {"company_name": "Acme Homes", "tagline": "Homes in Pune.", "sign_off": "Regards,\nAcme Homes"},
                                                    "offer": {"value_1": "Ready-to-move flats in Baner.", "value_2": "x", "value_3": "y",
                                                              "cta": "Reply with a time."}})
    out = c.post("/api/presets/real_estate/apply").json()
    p = c.get(f"/api/sequences/{out['sequence_id']}/steps/0/preview").json()
    assert "Acme Homes" in p["text"] and "Ready-to-move flats in Baner." in p["text"] and p["missing"] == []
