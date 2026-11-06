"""100k generated prospects: every search shape answers in under 300 ms on SQLite; upserts keep a usable pace."""
import time

import pytest

from app import prospect_db as P
from app.prospect_repo import forget
from app.store import Store
from tests.prospect_fakes import bulk_seed

LIMIT_MS = 300
SHAPES = [
    ({}, "relevance"), ({}, "updated"), ({}, "signal"), ({}, "employees"), ({}, "name"),
    ({"title": "hr,talent"}, "relevance"), ({"title": "recruit", "title_exclude": "senior"}, "relevance"),
    ({"q": "aarav firm"}, "relevance"), ({"seniority": ["head", "director"], "industry": ["BPO"]}, "relevance"),
    ({"city": ["Pune", "Mumbai"], "has_email": True, "verified_only": True}, "updated"),
    ({"technologies": "react", "employees": ["51-200", "201-500"]}, "signal"),
    ({"signal_kinds": "hiring", "signal_days": 365}, "employees"), ({"department": "hr", "has_phone": True}, "name"),
    ({"source": "apollo", "not_in_crm": True}, "relevance"), ({"keywords": "saas", "founded_min": 2000}, "relevance"),
    ({"country": ["India"], "revenue": ["1-10Cr"], "state": ["Karnataka"]}, "signal"),
]


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("perf") / "d.json")
    bulk_seed(store, 10000, 100000)
    P.search_people(store, {})                                      # first query warms the page cache
    yield store
    forget(store)


@pytest.mark.parametrize("filters,sort", SHAPES)
def test_people_search_under_300ms_at_100k(big, filters, sort):
    started = time.perf_counter()
    out = P.search_people(big, filters, sort, 3, 25)
    took = (time.perf_counter() - started) * 1000
    assert took < LIMIT_MS, (filters, sort, took)
    assert out["total"] > 0 and len(out["results"]) == 25


@pytest.mark.parametrize("filters", [{}, {"q": "bpo"}, {"industry": ["Software"], "employees": ["1001-5000"]}, {"technologies": "aws", "signal_kinds": "hiring"}])
def test_company_search_under_300ms(big, filters):
    started = time.perf_counter()
    out = P.search_companies(big, filters, "signal", 1, 25)
    assert (time.perf_counter() - started) * 1000 < LIMIT_MS and out["total"] > 0


def test_counts_and_upserts_on_a_big_table(big):
    assert P.stats(big)["people"] == 100000
    started = time.perf_counter()
    for i in range(500):
        P.upsert_person(big, {"company_domain": f"firm{i}.example", "full_name": f"New Person {i}", "email": f"new{i}@firm{i}.example",
                              "title": "HR Manager"}, "hunter")
    per = (time.perf_counter() - started) * 1000 / 500
    assert per < 20, per                                            # each upsert (lookups + write + FTS) stays a few ms
    assert P.search_people(big, {"q": "new person 42"})["total"] >= 1
