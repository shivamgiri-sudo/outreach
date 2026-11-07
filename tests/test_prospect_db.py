"""Prospect database: merge rules, provenance, dedupe, search filters, suppression, erasure, disabled sources.

Every test runs on SQLite; set OUTREACH_TEST_MYSQL_URL=mysql://user:pass@host:port/db (a throwaway database: its pdb_
tables are dropped) to run the same tests on MySQL too.
"""
import os

import pytest

from app import prospect_db as P
from app import prospect_fields as F
from app.prospect_repo import MySqlRepo, forget, repo_for
from app.store import Store

MYSQL_URL = os.environ.get("OUTREACH_TEST_MYSQL_URL", "")
TABLES = ("pdb_meta", "pdb_companies", "pdb_people", "pdb_keys", "pdb_signals", "pdb_suppressions", "pdb_erased", "pdb_email_marks",
          "pdb_crm_links", "pdb_saved_searches", "pdb_lookups")


@pytest.fixture(params=["sqlite", "mysql"])
def store(tmp_path, request):
    s = Store(tmp_path / "d.json")
    if request.param == "mysql":
        if not MYSQL_URL:
            pytest.skip("set OUTREACH_TEST_MYSQL_URL to run the MySQL backend")
        repo = MySqlRepo(MYSQL_URL)
        cur = repo._conn().cursor()
        for t in TABLES:
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        repo._conn().commit()
        s._prospect_repo = repo
    s.backend = request.param
    yield s
    forget(s)


def test_storage_is_a_sqlite_file_next_to_the_data_file_not_a_json_collection(store, tmp_path):
    if store.backend == "mysql":
        pytest.skip("SQLite only")
    P.upsert_company(store, {"domain": "acme.example", "name": "Acme"}, "apollo")
    assert (tmp_path / "d.json.prospects.sqlite").exists()
    assert "pdb_companies" not in store.snapshot() and "prospects" not in store.snapshot()


def test_company_dedupes_by_domain_then_name_and_city(store):
    a = P.upsert_company(store, {"website": "https://www.acme.example/about", "name": "Acme Pvt Ltd", "hq_city": "Pune"}, "apollo")
    b = P.upsert_company(store, {"domain": "ACME.example", "name": "Acme Private Limited"}, "hunter")
    assert a["id"] == b["id"] and a["created"] and not b["created"]
    c = P.upsert_company(store, {"name": "Zeta Foods Pvt. Ltd.", "hq_city": "Pune"}, "csv")
    d = P.upsert_company(store, {"name": "Zeta Foods", "hq_city": "Pune", "domain": "zeta.example"}, "apollo")
    assert c["id"] == d["id"] and P.get_company(store, "zeta.example")["id"] == c["id"]
    e = P.upsert_company(store, {"name": "Zeta Foods", "hq_city": "Pune", "domain": "other.example"}, "apollo")
    assert e["id"] != c["id"]                                                       # same name, different website: different company
    assert P.upsert_company(store, {"name": ""}, "apollo") == {"skipped": "no_identity"}


def test_person_dedupes_by_email_then_linkedin_then_name_and_domain(store):
    a = P.upsert_person(store, {"email": "Ravi@Acme.example", "full_name": "Ravi Kumar", "company_domain": "acme.example"}, "apollo")
    b = P.upsert_person(store, {"email": "ravi@acme.example", "linkedin_url": "linkedin.com/in/ravik"}, "hunter")
    c = P.upsert_person(store, {"linkedin_url": "https://in.linkedin.com/in/ravik?trk=x", "title": "Head of HR"}, "crm")
    d = P.upsert_person(store, {"full_name": "Mr. Ravi  Kumar", "company_domain": "acme.example", "phones": ["98765 43210"]}, "csv")
    assert a["id"] == b["id"] == c["id"] == d["id"]
    p = P.get_person(store, a["id"])
    assert p["title"] == "Head of HR" and p["phones"] == ["+919876543210"] and p["company"]["domain"] == "acme.example"
    assert sorted(p["sources"]) == ["apollo", "crm", "csv", "hunter"]
    other = P.upsert_person(store, {"full_name": "Ravi Kumar", "company_domain": "beta.example"}, "csv")
    assert other["id"] != a["id"]


def test_every_field_keeps_provenance_and_best_value_rule(store):
    pid = P.upsert_person(store, {"email": "a@x.example", "title": "HR Manager"}, "csv", "2026-10-01T00:00:00+00:00")["id"]
    P.upsert_person(store, {"email": "a@x.example", "title": "Senior HR Manager"}, "apollo", "2026-11-01T00:00:00+00:00")
    view = P.get_person(store, pid)["provenance"]["title"]
    assert view["value"] == "Senior HR Manager"                                       # more recent wins
    assert {e["source"] for e in view["entries"]} == {"csv", "apollo"} and all(e["seen_at"] and e["confidence"] for e in view["entries"])
    P.upsert_person(store, {"email": "a@x.example", "title": "HR Lead"}, "hunter", "2026-11-01T00:00:00+00:00")
    assert P.get_person(store, pid)["title"] == "Senior HR Manager"                  # same day: higher-confidence source (apollo 80 > hunter 75)
    P.upsert_person(store, {"email": "a@x.example", "title": "Talent Partner"}, "manual", "2026-01-01T00:00:00+00:00")
    assert P.get_person(store, pid)["title"] == "Talent Partner"                     # manual edits win, even when older
    P.upsert_person(store, {"email": "a@x.example", "title": "Recruiter"}, "apollo", "2026-12-01T00:00:00+00:00")
    assert P.get_person(store, pid)["title"] == "Talent Partner"


def test_verified_beats_recent_for_email_and_marks_win(store):
    pid = P.upsert_person(store, {"linkedin_url": "linkedin.com/in/a1", "email": "old@x.example", "email_status": "verified"}, "hunter",
                          "2026-09-01T00:00:00+00:00")["id"]
    P.upsert_person(store, {"linkedin_url": "linkedin.com/in/a1", "email": "new@x.example", "email_status": "unknown"}, "csv",
                    "2026-11-01T00:00:00+00:00")
    p = P.get_person(store, pid)
    assert p["email"] == "old@x.example" and p["email_status"] == "verified" and p["last_verified"].startswith("2026-09")
    assert P.mark_email(store, "old@x.example", "invalid", "bounced", "2026-11-02T00:00:00+00:00") == 1
    p = P.get_person(store, pid)
    assert p["email"] == "new@x.example"                                             # the bounced address loses
    # a later source re-reporting the bounced address as verified does not undo a newer bounce
    P.upsert_person(store, {"email": "old@x.example", "email_status": "verified"}, "apollo", "2026-10-01T00:00:00+00:00")
    assert P.get_person(store, pid)["provenance"]["email"]["entries"][-1]["status"] in ("invalid", "verified")
    entries = {e["source"]: e for e in P.get_person(store, pid)["provenance"]["email"]["entries"] if e["value"] == "old@x.example"}
    assert entries["apollo"]["status"] == "invalid"


def test_role_addresses_go_to_the_company_not_a_person(store):
    assert P.upsert_person(store, {"email": "hr@acme.example"}, "csv") == {"skipped": "no_identity"}
    assert P.get_company(store, "acme.example")["emails"] == ["hr@acme.example"]


def test_signals_dedupe_and_score(store):
    rec = {"domain": "s.example", "name": "S", "signals": [{"kind": "hiring", "title": "5 roles", "at": "2026-11-01"},
                                                           {"kind": "funding", "title": "Series A", "url": "https://n.example/a"},
                                                           {"kind": "bogus", "title": "x"}]}
    P.upsert_company(store, rec, "apollo")
    P.upsert_company(store, rec, "apollo")
    c = P.get_company(store, "s.example")
    assert sorted(s["kind"] for s in c["signals"]) == ["funding", "hiring"] and c["signal_score"] > 0


def test_search_filters(store):
    P.upsert_company(store, {"domain": "a.example", "name": "Alpha BPO", "industry": "BPO", "employees": 300, "hq_city": "Pune",
                             "technologies": ["React"], "keywords": ["outsourcing"], "founded": 2010, "revenue_range": "10-100Cr",
                             "signals": [{"kind": "hiring", "title": "20 roles", "at": F.now_iso()}]}, "apollo")
    P.upsert_company(store, {"domain": "b.example", "name": "Beta Soft", "industry": "Software", "employees": 20, "hq_city": "Noida",
                             "technologies": ["AWS"], "founded": 1999}, "hunter")
    P.upsert_person(store, {"company_domain": "a.example", "full_name": "Asha Rao", "title": "Head of Talent Acquisition",
                            "email": "asha@a.example", "email_status": "verified", "phones": ["9876543210"]}, "apollo")
    P.upsert_person(store, {"company_domain": "a.example", "full_name": "Bala Iyer", "title": "Recruiter", "email": "bala@a.example"}, "csv")
    P.upsert_person(store, {"company_domain": "b.example", "full_name": "Chitra Das", "title": "CEO", "city": "Pune"}, "hunter")
    names = lambda f, sort="relevance": [r["full_name"] for r in P.search_people(store, f, sort)["results"]]
    assert names({"title": "talent"}) == ["Asha Rao"]
    assert names({"title": "recruit"}) == ["Bala Iyer"]                              # prefix match
    assert set(names({"title_exclude": "recruiter"})) == {"Asha Rao", "Chitra Das"}
    assert names({"seniority": "c_suite"}) == ["Chitra Das"] and names({"department": "talent"}) == ["Asha Rao", "Bala Iyer"]
    assert set(names({"industry": "bpo"})) == {"Asha Rao", "Bala Iyer"}
    assert names({"employees": ["11-50"]}) == ["Chitra Das"] and len(names({"employees_min": 100})) == 2
    assert set(names({"city": ["pune"]})) == {"Asha Rao", "Bala Iyer", "Chitra Das"}    # person city or company HQ
    assert names({"technologies": "aws"}) == ["Chitra Das"] and len(names({"keywords": "outsourcing"})) == 2
    assert len(names({"signal_kinds": "hiring", "signal_days": 7})) == 2 and names({"signal_kinds": "funding"}) == []
    assert names({"verified_only": True}) == ["Asha Rao"] and names({"has_phone": "1"}) == ["Asha Rao"]
    assert len(names({"has_email": True})) == 2 and names({"founded_max": 2000}) == ["Chitra Das"]
    assert names({"revenue": ["10-100cr"]}) == ["Asha Rao", "Bala Iyer"] and names({"source": "hunter"}) == ["Chitra Das"]
    assert names({"q": "asha alpha"}) == ["Asha Rao"] and names({"q": "nobody"}) == []
    assert names({}, "name") == ["Asha Rao", "Bala Iyer", "Chitra Das"]
    assert names({}, "signal")[0] in ("Asha Rao", "Bala Iyer") and names({}, "employees")[-1] == "Chitra Das"
    page = P.search_people(store, {}, "name", 2, 2)
    assert page["total"] == 3 and [r["full_name"] for r in page["results"]] == ["Chitra Das"]
    comp = P.search_companies(store, {"industry": "software"})
    assert [c["name"] for c in comp["results"]] == ["Beta Soft"] and comp["results"][0]["technologies"] == ["aws"]
    assert [c["name"] for c in P.search_companies(store, {"q": "alpha"})["results"]] == ["Alpha BPO"]


def test_suppression_hides_people_and_blocks_domains(store):
    P.upsert_person(store, {"email": "x@s.example", "full_name": "X"}, "apollo")
    P.upsert_person(store, {"email": "y@t.example", "full_name": "Y"}, "apollo")
    assert P.suppress(store, "X@S.example")["people"] == 1
    assert [r["full_name"] for r in P.search_people(store, {})["results"]] == ["Y"]
    assert P.search_people(store, {"include_suppressed": True})["total"] == 2
    P.suppress(store, "t.example")
    assert P.search_people(store, {})["total"] == 0
    P.upsert_person(store, {"email": "z@t.example", "full_name": "Z"}, "hunter")         # new people at a suppressed domain stay hidden
    assert P.search_people(store, {})["total"] == 0
    with pytest.raises(ValueError):
        P.suppress(store, "not a thing")


def test_erasure_deletes_and_never_comes_back(store):
    pid = P.upsert_person(store, {"email": "gone@e.example", "linkedin_url": "linkedin.com/in/gone", "phones": ["9876500000"]}, "apollo")["id"]
    assert P.delete_person(store, pid) and P.get_person(store, pid) is None
    for rec in ({"email": "gone@e.example"}, {"linkedin_url": "https://www.linkedin.com/in/gone"}, {"full_name": "G", "phones": ["+91 98765 00000"], "company_domain": "e.example"}):
        assert P.upsert_person(store, rec, "hunter") == {"skipped": "erased"}
    assert not P.delete_person(store, pid)
    store.write("suppressed_hashes", sorted(P.erased_hashes({"email": "crm-erased@e.example"})))
    assert P.upsert_person(store, {"email": "crm-erased@e.example"}, "apollo") == {"skipped": "erased"}


def test_disabled_sources_are_never_stored_and_can_be_purged(store):
    pid = P.upsert_person(store, {"email": "p@d.example", "title": "From Lusha"}, "lusha")["id"]
    P.upsert_person(store, {"email": "p@d.example", "full_name": "Pat"}, "crm")
    store.update("settings", lambda s: {**s, "prospect_db": {"disabled_sources": ["lusha"]}})
    assert P.upsert_person(store, {"email": "q@d.example"}, "lusha") == {"skipped": "source_disabled", "source": "lusha"}
    assert not P.get_person(store, pid)["title"]                                     # stored values from it are ignored at once
    P.purge_source(store, "lusha")
    p = P.get_person(store, pid)
    assert p["sources"] == ["crm"] and "lusha" not in str(p["provenance"])


def test_stats(store):
    P.upsert_person(store, {"email": "s@st.example", "email_status": "verified", "phones": ["9876543211"]}, "apollo")
    s = P.stats(store)
    assert s["people"] == 1 and s["companies"] == 1 and s["verified"] == 1 and s["with_phone"] == 1
    assert s["people_by_source"] == {"apollo": 1} and s["storage"] == store.backend


def test_schema_creation_is_idempotent_and_lazy(store, tmp_path):
    repo = repo_for(store)
    assert not (tmp_path / "d.json.prospects.sqlite").exists()                    # nothing until first use (and never with MySQL)
    repo.ensure()
    repo._ready = False
    repo.ensure()                                                                  # create-if-not-exists only: safe to repeat
    assert repo.scalar("SELECT v FROM pdb_meta WHERE k = 'schema'") == "1"


def test_mysql_missing_indexes_are_built_online_and_a_failure_never_blocks():
    """Fake MySQL connection: existing indexes are skipped, missing ones use INPLACE/LOCK=NONE, an error is only logged."""
    from app.prospect_repo import INDEXES
    sql = []

    class Cur:
        def execute(self, stmt, params=()):
            sql.append(stmt)
            if "CREATE INDEX p_state" in stmt:
                raise RuntimeError("lock wait timeout")
        def fetchall(self):
            return [("pdb_people", "p_company"), ("PDB_PEOPLE", "FT_TITLE")]
        def fetchone(self):
            return None

    class Conn:
        def cursor(self):
            return Cur()
        def commit(self):
            pass
        def ping(self, reconnect=True):
            pass

    repo = MySqlRepo("mysql://u:p@127.0.0.1:3306/x", connect=lambda **kw: Conn())
    repo.ensure()
    made = [s for s in sql if s.startswith("CREATE INDEX")]
    assert not any(" p_company " in s for s in made) and len(made) == len(INDEXES) - 1
    assert all(s.endswith("ALGORITHM=INPLACE LOCK=NONE") for s in made)
    assert not any("ft_title" in s for s in sql if s.startswith("CREATE FULLTEXT")) and repo._ready
