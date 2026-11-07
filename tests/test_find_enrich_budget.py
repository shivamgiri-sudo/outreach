"""One-click fill goes through the cost-aware waterfall: prospect-DB cache first, owner's order, stop on first hit, monthly
budget, spend ledger. Fake transports only (no network, no real keys); domains are made up."""
from app import prospect_db as P
from app.prospect_repo import repo_for
from tests.campaign_fakes import FakeIO, by_company, client, connect
from tests.test_find_leads import ORG, PEOPLE, REVEALED, add_leads, go


def spend_settings(c, **values):
    c.app.state.store.update("settings", lambda s: {**s, "prospect_db": {**(s.get("prospect_db") or {}), **values}})


def ledger(c):
    return repo_for(c.app.state.store).q("SELECT provider, field, hit, cached, cost FROM pdb_lookups ORDER BY id")


def apollo_io():
    return FakeIO(get={"organizations/enrich": ORG, "hunter.io": {"data": {"emails": [{"value": "nita@gamma-works.in", "confidence": 92}]}}},
                  post={"api_search": PEOPLE, "people/match": REVEALED})


def test_fresh_prospect_db_email_means_no_paid_call(tmp_path):
    io = apollo_io()
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    P.upsert_person(c.app.state.store, {"company_domain": "gamma-works.in", "full_name": "Meera Nair", "title": "HR Head",
                                        "email": "meera@gamma-works.in", "email_status": "verified"}, "apollo")
    add_leads(c, "company,website\nGamma Works,https://gamma-works.in\n")
    run = go(c, mode="enrich_existing", titles=["HR Head"])
    lead = by_company(c, "Gamma Works")
    assert lead["email"] == "meera@gamma-works.in" and lead["contact_name"] == "Meera Nair"
    assert lead["email_source"].startswith("prospect_db:prospect_db:") and run["credits"] == 0
    assert io.urls("apollo.io") == [] and io.urls("hunter.io") == []
    assert ledger(c) == [{"provider": "prospect_db", "field": "email", "hit": 1, "cached": 1, "cost": 0.0}]


def test_stale_prospect_db_email_is_not_used(tmp_path):
    io = apollo_io()
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    P.upsert_person(c.app.state.store, {"company_domain": "gamma-works.in", "full_name": "Meera Nair", "title": "HR Head",
                                        "email": "meera@gamma-works.in", "email_status": "verified"}, "apollo", seen_at="2025-01-01T00:00:00+00:00")
    add_leads(c, "company,website\nGamma Works,https://gamma-works.in\n")
    go(c, mode="enrich_existing", titles=["HR Head", "Founder"])
    assert by_company(c, "Gamma Works")["email"] == "nita@gamma-works.in" and io.urls("people/match")


def test_monthly_budget_stops_paid_lookups_and_ledger_records_spend(tmp_path):
    io = apollo_io()
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    spend_settings(c, monthly_budget=2)
    add_leads(c, "company,website\nGamma Works,https://gamma-works.in\nDelta Co,https://delta-co.in\n")
    run = go(c, mode="enrich_existing", titles=["HR Head", "Founder"], max_credits=40)
    assert io.urls("people/match") == []                                        # third paid lookup would pass the budget
    assert "monthly lookup budget" in run["report"]["stopped_reason"]
    assert any("budget" in e["message"].lower() for e in run["errors"])
    rows = ledger(c)
    assert [(r["provider"], r["field"], r["cost"]) for r in rows] == [("apollo", "company", 1.0), ("apollo", "people", 1.0)]
    assert not by_company(c, "Delta Co").get("find_checked_at")                 # left for the next run
    assert c.get("/api/prospects/spend").json()["spent"] == 2.0
    again = go(c, mode="enrich_existing", max_credits=40)                       # budget already used up this month
    assert len(io.urls("apollo.io")) == 2 and again["credits"] == 0


def test_owner_order_runs_cheapest_first_and_stops_at_first_email(tmp_path):
    io = apollo_io()
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    connect(c, "hunter", {"api_key": "hk"})
    spend_settings(c, providers={"hunter": {"priority": 1, "cost": 0.5}, "apollo": {"priority": 5, "cost": 2}})
    add_leads(c, "company,contact_name,website\nGamma Works,Nita Rao,https://gamma-works.in\n")
    go(c, mode="enrich_existing")
    lead = by_company(c, "Gamma Works")
    assert lead["email"] == "nita@gamma-works.in" and lead["email_source"].startswith("hunter:")
    assert io.urls("apollo.io") == []                                           # hit at Hunter: Apollo never called
    assert [(r["provider"], r["hit"], r["cost"]) for r in ledger(c)] == [("hunter", 1, 0.5)]


def test_provider_switched_off_in_spend_settings_is_not_called(tmp_path):
    io = apollo_io()
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    spend_settings(c, providers={"apollo": {"enabled": False}})
    add_leads(c, "company,website\nGamma Works,https://gamma-works.in\n")
    go(c, mode="enrich_existing")
    assert io.urls("apollo.io") == [] and ledger(c) == []


def test_miss_is_recorded_but_not_charged(tmp_path):
    io = FakeIO(get={"hunter.io": {"data": {"emails": []}}})
    c = client(tmp_path, io)
    connect(c, "hunter", {"api_key": "hk"})
    spend_settings(c, monthly_budget=5)
    add_leads(c, "company,website\nEmpty Co,https://empty-co.in\n")
    go(c, mode="enrich_existing")
    assert ledger(c) == [{"provider": "hunter", "field": "email", "hit": 0, "cached": 0, "cost": 0.0}]
