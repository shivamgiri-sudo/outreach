"""Cost-aware provider waterfall: prospect-DB cache first, owner order, stop on first hit, spend ledger, monthly budget."""
from app import prospect_db as P
from app import prospect_waterfall as W
from app.prospect_repo import repo_for
from tests.campaign_fakes import by_company, connect, run
from tests.test_sources_pipeline import campaign, make

DOMAIN_SEARCH = "api.hunter.io/v2/domain-search"
HUNTER_HIT = {"data": {"emails": [{"value": "asha@acme-hr.in", "first_name": "Asha", "last_name": "Rao", "position": "HR Manager",
                                   "confidence": 91, "verification": {"status": "valid"}}]}}


def settings(c, **values):
    store = c.app.state.store
    store.update("settings", lambda s: {**s, "prospect_db": {**(s.get("prospect_db") or {}), **values}})


def ledger(c):
    return repo_for(c.app.state.store).q("SELECT provider, field, hit, cached, cost FROM pdb_lookups ORDER BY id")


def test_fresh_value_in_the_prospect_db_means_no_provider_call(tmp_path):
    c, http = make(tmp_path, {DOMAIN_SEARCH: HUNTER_HIT}, pages={})
    connect(c, "hunter", {"api_key": "hk"})
    P.upsert_person(c.app.state.store, {"company_domain": "acme-hr.in", "full_name": "Meera Nair", "title": "HR Head",
                                        "email": "meera@acme-hr.in", "email_status": "verified"}, "apollo")
    report = run(c, campaign(c, ["hunter"])["id"])
    assert http.urls("api.hunter.io") == []                                         # nothing spent
    lead = by_company(c, "Acme HR Services")
    assert lead["email"] == "meera@acme-hr.in" and lead["email_check"]["status"] == "valid" and report["enriched_emails"] == 1
    assert ledger(c) == [{"provider": "prospect_db", "field": "email", "hit": 1, "cached": 1, "cost": 0.0}]


def test_stale_value_is_looked_up_again_and_the_find_is_stored_with_provenance(tmp_path):
    c, http = make(tmp_path, {DOMAIN_SEARCH: HUNTER_HIT}, pages={})
    connect(c, "hunter", {"api_key": "hk"})
    store = c.app.state.store
    P.upsert_person(store, {"company_domain": "acme-hr.in", "full_name": "Meera Nair", "title": "HR Head", "email": "meera@acme-hr.in",
                            "email_status": "verified"}, "apollo", "2025-01-01T00:00:00+00:00")
    settings(c, freshness_days=30)
    run(c, campaign(c, ["hunter"])["id"])
    assert http.urls(DOMAIN_SEARCH)
    hit = P.search_people(store, {"q": "asha"})["results"][0]
    assert hit["email"] == "asha@acme-hr.in" and hit["email_status"] == "verified" and "hunter" in hit["sources"]
    view = P.get_person(store, hit["id"])["provenance"]["email"]
    assert view["entries"][0]["source"] == "hunter"
    assert ledger(c)[0] == {"provider": "hunter", "field": "email", "hit": 1, "cached": 0, "cost": 1.0}


def test_spend_counts_only_when_a_provider_returns_something_usable(tmp_path):
    c, http = make(tmp_path, {DOMAIN_SEARCH: {"data": {"emails": []}}}, pages={})
    connect(c, "hunter", {"api_key": "hk"})
    run(c, campaign(c, ["hunter"])["id"])
    assert http.urls(DOMAIN_SEARCH) and ledger(c) == [{"provider": "hunter", "field": "email", "hit": 0, "cached": 0, "cost": 0.0}]
    report = W.spend_report(c.app.state.store)
    row = report["rows"][0]
    assert row["provider"] == "hunter" and row["lookups"] == 1 and row["hit_rate"] == 0 and report["spent"] == 0


def test_owner_order_and_stop_on_first_definitive_answer(tmp_path):
    routes = {"api.hunter.io/v2/email-verifier": {"data": {"status": "valid", "result": "deliverable"}},
              "api.kickbox.com/v2/verify": {"result": "deliverable", "success": True}}
    c, http = make(tmp_path, routes)
    connect(c, "hunter", {"api_key": "hk"})
    connect(c, "kickbox", {"api_key": "kk"})
    settings(c, providers={"kickbox": {"priority": 1, "cost": 0.2}, "hunter": {"priority": 5}})
    run(c, campaign(c, ["website_deep", "hunter", "kickbox"])["id"])
    lead = by_company(c, "Acme HR Services")
    assert lead["email_check"]["provider"] == "kickbox" and http.urls("email-verifier") == []        # kickbox first, chain stopped
    verify = [r for r in ledger(c) if r["field"] == "verify"]
    assert verify == [{"provider": "kickbox", "field": "verify", "hit": 1, "cached": 0, "cost": 0.2}]


def test_switched_off_provider_is_never_called(tmp_path):
    c, http = make(tmp_path, {DOMAIN_SEARCH: HUNTER_HIT}, pages={})
    connect(c, "hunter", {"api_key": "hk"})
    settings(c, providers={"hunter": {"enabled": False}})
    run(c, campaign(c, ["hunter"])["id"])
    assert http.urls("api.hunter.io") == []


def test_monthly_budget_cap_stops_paid_lookups_with_a_clear_message(tmp_path):
    c, http = make(tmp_path, {DOMAIN_SEARCH: HUNTER_HIT})
    connect(c, "hunter", {"api_key": "hk"})
    settings(c, monthly_budget=0.5)
    report = run(c, campaign(c, ["website_deep", "hunter"])["id"])
    assert http.urls(DOMAIN_SEARCH) == []
    assert any("Monthly lookup budget of 0.5 reached" in e["message"] for e in report["errors"])
    assert by_company(c, "Acme HR Services")["email"] == "meera@acme-hr.in"          # the free website source still ran


def test_budget_counts_spend_already_made_this_month(tmp_path):
    c, http = make(tmp_path, {DOMAIN_SEARCH: HUNTER_HIT}, pages={})
    connect(c, "hunter", {"api_key": "hk"})
    store = c.app.state.store
    repo_for(store).bulk_insert("pdb_lookups", [{"at": "x", "month": W.month_of(), "provider": "hunter", "field": "email", "target": "",
                                                "hit": 1, "cached": 0, "cost": 5.0}])
    settings(c, monthly_budget=5.5)
    run(c, campaign(c, ["hunter"])["id"])
    assert http.urls(DOMAIN_SEARCH) == []


def test_estimate_and_enrich_api(tmp_path):
    c, http = make(tmp_path, {"api.hunter.io/v2/email-finder": {"data": {"email": "kiran@kk.example", "score": 92, "verification": {"status": "valid"}}}})
    connect(c, "hunter", {"api_key": "hk"})
    store = c.app.state.store
    a = P.upsert_person(store, {"company_domain": "kk.example", "full_name": "Kiran Shah", "title": "HR Head"}, "csv")["id"]
    b = P.upsert_person(store, {"company_domain": "jj.example", "full_name": "Jo Paul", "email": "jo@jj.example", "email_status": "verified"}, "csv")["id"]
    est = c.post("/api/prospects/enrich/estimate", json={"person_ids": [a, b]}).json()
    assert est["targets"] == 1 and est["to_look_up"] == 1 and "hunter" in est["providers"] and est["max_cost"] == 1.0 and http.calls == []
    out = c.post("/api/prospects/enrich", json={"person_ids": [a, b]}).json()
    assert out["found"] == 1 and out["spent"] == 1.0
    assert P.get_person(store, a)["email"] == "kiran@kk.example"
    spend = c.get("/api/prospects/spend").json()
    assert spend["spent"] == 1.0 and any(r["provider"] == "hunter" and r["hits"] == 1 for r in spend["rows"])
    saved = c.put("/api/prospects/spend", json={"monthly_budget": 50, "freshness_days": 60, "providers": {"hunter": {"cost": 2, "priority": 3}, "bogus": {}}}).json()
    assert saved["budget"] == 50 and saved["freshness_days"] == 60
    assert next(p for p in saved["providers"] if p["id"] == "hunter")["cost"] == 2.0
    assert any(r["action"] == "prospects.enrich" for r in store.read("audit"))
