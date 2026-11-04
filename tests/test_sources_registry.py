"""The source registry, the planner, /api/sources, the Integrations cards and the setup test hook."""
import pytest
from fastapi.testclient import TestClient

from app import integrations
from app.channels import registry as hub
from app.main import create_app
from app.sources import registry
from tests.source_fakes import FakeHttp

EXPECTED = {"greenhouse", "lever", "workable", "ashby", "smartrecruiters", "recruitee", "zoho_recruit", "adzuna", "jooble", "themuse", "google_cse",
            "brave_search", "osm_overpass", "osm_nominatim", "google_places", "opencorporates", "data_gov_in", "wikidata", "website_deep", "apollo",
            "hunter", "snov", "prospeo", "dropcontact", "lusha", "rocketreach", "peopledatalabs", "builtin_verifier", "smtp_probe", "zerobounce", "neverbounce",
            "kickbox", "millionverifier", "bouncer", "emailable", "abstract", "reoon"}


def app_client(tmp_path, http=None):
    return TestClient(create_app(data_file=tmp_path / "store.json", campaign_transports={"source_http": http or FakeHttp(), "fetch": lambda u: ""}))


def test_every_documented_source_is_registered_with_docs_steps_terms_and_a_test():
    assert set(registry.BY_ID) == EXPECTED
    for spec in registry.SOURCES:
        assert spec.docs_url.startswith("https://") and spec.steps and spec.terms and spec.why and spec.cost_note is not None
        assert spec.test is not None and spec.rate_per_min > 0
        assert spec.honest_status
        for cap in spec.caps:
            assert registry._has_op(spec, cap), (spec.id, cap)


def test_plan_is_cheapest_first_free_before_owner_keys():
    ids = list(registry.BY_ID)
    verifiers = [s.id for s in registry.plan("verify_email", ids)]
    assert verifiers[0] == "builtin_verifier" and verifiers[1] == "smtp_probe" and set(verifiers[2:]) == {"hunter", "zerobounce", "neverbounce", "kickbox", "millionverifier", "bouncer", "snov", "emailable", "abstract", "reoon"}
    finders = registry.plan("find_email", ids)
    assert finders[0].free and finders[0].id == "website_deep"
    costs = [(not s.free, s.cost) for s in finders]
    assert costs == sorted(costs)
    assert [s.id for s in registry.plan("find_email", ["hunter", "website_deep"])] == ["website_deep", "hunter"]
    assert registry.plan("find_email", ["kickbox", "greenhouse", "nonsense"]) == []
    disc = [s.id for s in registry.plan("hiring_signal", ids)]
    assert "greenhouse" in disc and "adzuna" in disc and "snov" not in disc
    with pytest.raises(ValueError):
        registry.plan("scrape_everything", ids)


def test_connection_status_for_keyless_keyed_and_legacy_cards():
    saved = {}
    assert registry.connection(registry.get("greenhouse"), saved)["connected"]                       # public feed: nothing to connect
    assert not registry.connection(registry.get("snov"), saved)["connected"]
    assert "Snov.io" in registry.connection(registry.get("snov"), saved)["reason"]
    saved = {"snov": {"mode": "api", "values": {"client_id": "a", "client_secret": "b"}}, "hunter": {"mode": "api", "values": {"api_key": "k"}},
             "email_verifier": {"mode": "api", "values": {"provider": "ZeroBounce", "api_key": "zk"}}}
    assert registry.connection(registry.get("snov"), saved)["connected"] and registry.connection(registry.get("hunter"), saved)["connected"]
    assert registry.connection(registry.get("zerobounce"), saved)["connected"] and not registry.connection(registry.get("neverbounce"), saved)["connected"]
    assert registry.connection(registry.get("zerobounce"), saved)["values"]["api_key"] == "zk"
    assert not registry.connection(registry.get("apollo"), {"apollo": {"mode": "manual", "values": {"api_key": "k"}}})["connected"]


def test_api_sources_lists_everything_with_status_steps_and_terms(tmp_path):
    c = app_client(tmp_path)
    body = c.get("/api/sources").json()
    assert {s["id"] for s in body["sources"]} == EXPECTED and "verify_email" in body["capabilities"]
    lever = next(s for s in body["sources"] if s["id"] == "lever")
    assert lever["connected"] and lever["keyless"] and lever["terms"] and lever["steps"] and lever["docs_url"].startswith("https://github.com/lever")
    assert lever["capabilities"] == ["discover_companies", "hiring_signal"] and lever["rate_per_min"] > 0 and "Free" in lever["cost_note"]
    snov = next(s for s in body["sources"] if s["id"] == "snov")
    assert not snov["connected"] and snov["reason"] and [f["key"] for f in snov["fields"]] == ["client_id", "client_secret"] and snov["status_note"]


def test_source_test_endpoint_is_free_and_isolated(tmp_path):
    http = FakeHttp({"boards-api.greenhouse.io/v1/boards/acme/jobs": {"jobs": [{"title": "x", "location": {"name": "Pune"}}]},
                     "boards-api.greenhouse.io/v1/boards/acme": {"name": "Acme"}, "overpass-api.de/api/status": {"_raw": "Connected as: 1\nRate limit: 2"}})
    c = app_client(tmp_path, http)
    assert c.post("/api/sources/nope/test").status_code == 404
    r = c.post("/api/sources/osm_overpass/test").json()
    assert r["ok"] and "reachable" in r["message"] and http.calls[0]["url"].endswith("/api/status")      # status page, no query
    nothing = c.post("/api/sources/greenhouse/test").json()
    assert nothing["ok"] and "Add a company identifier" in nothing["message"] and len(http.calls) == 1   # nothing typed yet: no request
    c.put("/api/integrations/greenhouse", json={"mode": "api", "values": {"slugs": "acme"}})
    assert "1 open role" in c.post("/api/sources/greenhouse/test").json()["message"]
    not_connected = c.post("/api/sources/kickbox/test").json()
    assert not_connected["ok"] is False and "Kickbox" in not_connected["message"]
    c.put("/api/integrations/brave_search", json={"mode": "api", "values": {"api_key": "bad"}})
    http.routes["res/v1/web/search"] = PermissionError("HTTP 401")
    bad = c.post("/api/sources/brave_search/test").json()
    assert bad["ok"] is False and "rejected" in bad["message"]
    http.routes["res/v1/web/search"] = OSError("boom")
    assert c.post("/api/sources/brave_search/test").json()["ok"] is False


def test_setup_test_button_routes_to_the_same_source_test(tmp_path):
    http = FakeHttp({"jobs/in/categories": {"results": []}})
    c = app_client(tmp_path, http)
    c.put("/api/integrations/adzuna", json={"mode": "api", "values": {"app_id": "a", "app_key": "k"}})
    r = c.post("/api/integrations/adzuna/test", json={}).json()
    assert r["ok"] and "categories" in r["message"]
    assert c.post("/api/integrations/not_a_thing/test", json={}).status_code == 404


def test_integrations_cards_exist_for_every_new_source_and_mask_secrets(tmp_path):
    new = [s for s in registry.SOURCES if not s.legacy and s.id not in ("builtin_verifier", "website_deep")]
    ids = {i["id"] for i in integrations.SOURCE_CATALOGUE}
    assert not ids & {i["id"] for i in integrations.CATALOGUE}                      # the Setup page keeps only the original 11
    assert {s.id for s in new} <= ids
    lead_data = [p["id"] for p in hub.providers_for("lead_data")]
    assert {s.id for s in new} <= set(lead_data) and lead_data[:7] == ["apollo", "hunter", "firecrawl", "google_places", "contact_scraper", "sales_navigator", "email_verifier"]
    assert len(lead_data) == len(set(lead_data))                                           # no card twice
    c = app_client(tmp_path)
    c.put("/api/integrations/snov", json={"mode": "api", "values": {"client_id": "id-123456", "client_secret": "sec-987654"}})
    assert len(c.get("/api/integrations").json()) == len(integrations.CATALOGUE)       # Setup > Connections is unchanged
    shown = c.put("/api/integrations/snov", json={"mode": "api", "values": {"client_id": "id-123456", "client_secret": "sec-987654"}}).json()
    assert shown["values"]["client_secret"].startswith("••••") and shown["values"]["client_id"].startswith("••••") and shown["status"] == "configured"
    channels = c.get("/api/channels").json()["channels"]
    cards = next(ch for ch in channels if ch["id"] == "lead_data")["providers"]
    snov = next(p for p in cards if p["id"] == "snov")
    assert snov["steps"] and snov["docs_url"] == "https://snov.io/api" and snov["terms"] and snov["cost_note"] and snov["status"] == "configured"
    assert "sec-987654" not in c.get("/api/channels").text


def test_old_campaign_keys_still_work_and_default_extras_are_empty(tmp_path):
    c = app_client(tmp_path)
    body = {"name": "legacy", "sources": {"apollo_search": True, "google_places": True}, "enrich": {"hunter": True, "apollo_match": True}}
    made = c.post("/api/campaigns", json=body).json()
    assert made["sources"]["extra"] == [] and made["enrich"]["extra"] == [] and made["sources"]["apollo_search"] and made["rules"]["credit_cap"] == 100
    assert c.get("/api/campaign-sources").json()["apollo"]["connected"] in (True, False)
    changed = c.put(f"/api/campaigns/{made['id']}", json={"sources": {"extra": ["lever"]}}).json()
    assert changed["sources"]["extra"] == ["lever"] and changed["sources"]["apollo_search"] is True
    assert c.put(f"/api/campaigns/{made['id']}", json={"sources": {"extra_params": {"lever": {"slugs": "acme"}}}}).json()["sources"]["extra_params"] == {"lever": {"slugs": "acme"}}
    assert c.put(f"/api/campaigns/{made['id']}", json={"sources": {"extra_params": {"nope": {}}}}).status_code == 422
    assert c.put(f"/api/campaigns/{made['id']}", json={"rules": {"credit_cap": 99999}}).status_code == 422
