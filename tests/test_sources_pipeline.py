"""Extra sources inside a real campaign run: discovery, the enrichment waterfall, isolation, cache, credit cap, suppression, provenance."""
import json

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.sources.base import Cache
from tests.campaign_fakes import by_company, connect, create, run
from tests.source_fakes import NOW, FakeHttp, HttpError

BOARD = {"jobs": [{"id": 1, "title": "Customer Support Executive", "updated_at": "2026-10-20T10:00:00-04:00", "location": {"name": "Pune, India"}, "absolute_url": "u"}]}
PEOPLE_PAGE = ('<html><script type="application/ld+json">{"@context":"https://schema.org","@type":"Organization","name":"Acme HR",'
               '"sameAs":["https://www.linkedin.com/company/acme-hr","https://twitter.com/acmehr"],"employee":[{"@type":"Person","name":"Meera Nair",'
               '"jobTitle":"HR Head","email":"meera@acme-hr.in","sameAs":"https://www.linkedin.com/in/meera-example"}]}</script>'
               '<a href="mailto:careers@acme-hr.in">Careers</a></html>')


def doh(call):
    return {"Status": 0, "Answer": [{"type": 15, "data": "10 mx.example.in."}]}


def make(tmp_path, routes=None, pages=None):
    http = FakeHttp({"greenhouse.io/v1/boards/acme-hr/jobs": BOARD, "greenhouse.io/v1/boards/acme-hr": {"name": "Acme HR Services"},
                     "cloudflare-dns.com": doh, **(routes or {})})
    pages = pages if pages is not None else {"https://acme-hr.in/contact": PEOPLE_PAGE}

    def fetch(url):
        if url in pages:
            return pages[url]
        raise OSError("unreachable in tests")
    app = create_app(data_file=tmp_path / "store.json", campaign_transports={"post": lambda *a: {}, "get": lambda *a: {}, "fetch": fetch, "source_http": http})
    return TestClient(app), http


def campaign(c, enrich_extra, discover=("greenhouse",), slugs="acme-hr|acme-hr.in", **rules):
    return create(c, sources={"extra": list(discover), "extra_params": {"greenhouse": {"slugs": slugs}}},
                  enrich={"extra": list(enrich_extra)}, rules=rules or {})


def test_discovery_creates_company_leads_with_hiring_signal_and_provenance(tmp_path):
    c, http = make(tmp_path)
    camp = campaign(c, [])
    report = run(c, camp["id"])
    assert report["new"] == 1 and report["sources"]["greenhouse"]["found"] == 1 and report["errors"] == []
    lead = by_company(c, "Acme HR Services")
    assert lead["domain"] == "acme-hr.in" and lead["open_roles"] == 1 and lead["role"] == "Customer Support Executive"
    assert lead["provenance"]["open_roles"] == "greenhouse:https://boards.greenhouse.io/acme-hr" and lead["sources"] == ["greenhouse"]
    assert "Greenhouse job boards" in lead["timeline"][0]["text"]


def test_waterfall_free_sources_first_stops_at_first_verified_email(tmp_path):
    routes = {"api.kickbox.com/v2/verify": {"result": "deliverable", "reason": "accepted_email", "success": True},
              "api.hunter.io/v2/email-verifier": {"data": {"status": "invalid", "result": "undeliverable"}}}
    c, http = make(tmp_path, routes)
    connect(c, "hunter", {"api_key": "hk"})
    connect(c, "kickbox", {"api_key": "kk"})
    camp = campaign(c, ["website_deep", "builtin_verifier", "kickbox"])           # Hunter is connected but not ticked
    report = run(c, camp["id"])
    lead = by_company(c, "Acme HR Services")
    assert lead["email"] == "meera@acme-hr.in" and lead["contact_name"] == "Meera Nair" and lead["title"] == "HR Head"
    assert lead["email_check"]["status"] == "valid" and lead["email_check"]["provider"] == "kickbox"
    assert lead["email_source"].startswith("website_deep:https://acme-hr.in/contact") and lead["provenance"]["email"].startswith("website_deep:")
    assert lead["linkedin_url"] == "https://www.linkedin.com/in/meera-example" and lead["company_linkedin_url"] == "https://www.linkedin.com/company/acme-hr"
    assert http.urls("api.hunter.io") == []                       # not ticked: never called
    assert http.urls("kickbox") and len(http.urls("kickbox")) == 1 and report["enriched_emails"] == 1
    assert report["credits_used_estimate"] == 1


def test_paid_verifiers_run_cheapest_first_and_the_chain_stops_at_the_first_answer(tmp_path):
    routes = {"api.hunter.io/v2/email-verifier": {"data": {"status": "valid", "result": "deliverable"}}, "api.kickbox.com/v2/verify": {"result": "deliverable", "success": True}}
    c, http = make(tmp_path, routes)
    connect(c, "hunter", {"api_key": "hk"})
    connect(c, "kickbox", {"api_key": "kk"})
    run(c, campaign(c, ["website_deep", "builtin_verifier", "hunter", "kickbox"])["id"])
    lead = by_company(c, "Acme HR Services")
    assert lead["email_check"]["provider"] == "hunter" and http.urls("kickbox") == [] and http.urls("domain-search") == []


def test_waterfall_falls_back_to_owner_key_sources_and_uses_finder_verification(tmp_path):
    routes = {"api.hunter.io/v2/domain-search": {"data": {"emails": [{"value": "hr@acme-hr.in", "first_name": "Asha", "last_name": "Rao", "position": "HR Manager",
                                                                       "confidence": 91, "verification": {"status": "valid"}}]}}}
    c, http = make(tmp_path, routes, pages={})                     # website has nothing
    connect(c, "hunter", {"api_key": "hk"})
    camp = campaign(c, ["website_deep", "hunter", "builtin_verifier"])
    run(c, camp["id"])
    lead = by_company(c, "Acme HR Services")
    assert lead["email"] == "hr@acme-hr.in" and lead["email_check"]["status"] == "valid" and lead["email_check"]["provider"] == "hunter"
    assert lead["contact_name"] == "Asha Rao" and http.urls("domain-search")


def test_unverified_candidate_is_kept_but_marked_unverified(tmp_path):
    c, http = make(tmp_path)
    camp = campaign(c, ["website_deep", "builtin_verifier"])
    run(c, camp["id"])
    lead = by_company(c, "Acme HR Services")
    assert lead["email"] == "meera@acme-hr.in" and lead["email_check"]["status"] == "unverified"      # only the built-in check ran: plausible, not confirmed


def test_invalid_candidates_are_dropped(tmp_path):
    routes = {"api.kickbox.com/v2/verify": {"result": "undeliverable", "reason": "rejected_email", "success": True}}
    c, _ = make(tmp_path, routes)
    connect(c, "kickbox", {"api_key": "kk"})
    run(c, campaign(c, ["website_deep", "builtin_verifier", "kickbox"])["id"])
    assert not by_company(c, "Acme HR Services").get("email")


def test_one_failing_source_never_aborts_the_run(tmp_path):
    routes = {"api.hunter.io": HttpError(503), "api.kickbox.com/v2/verify": {"result": "deliverable", "success": True}}
    c, http = make(tmp_path, routes)
    connect(c, "hunter", {"api_key": "hk"})
    connect(c, "kickbox", {"api_key": "kk"})
    report = run(c, campaign(c, ["website_deep", "builtin_verifier", "kickbox"], discover=("greenhouse", "lever", "brave_search"))["id"])
    msgs = {e["source"]: e["message"] for e in report["errors"]}
    assert "Lever job postings" in msgs and "Brave Search API" in msgs        # lever has no fake route, brave has no key: both reported
    assert report["new"] == 1 and by_company(c, "Acme HR Services")["email"] == "meera@acme-hr.in"


def test_rejected_key_switches_the_source_off_for_the_rest_of_the_run(tmp_path):
    routes = {"api.hunter.io": PermissionError("HTTP 401")}
    c, http = make(tmp_path, routes, pages={})
    connect(c, "hunter", {"api_key": "bad"})
    camp = campaign(c, ["hunter"], slugs="acme-hr|acme-hr.in, beta-hr|beta-hr.in")
    http.routes["greenhouse.io/v1/boards/beta-hr/jobs"] = BOARD
    http.routes["greenhouse.io/v1/boards/beta-hr"] = {"name": "Beta HR"}
    report = run(c, camp["id"])
    assert report["new"] == 2 and len(http.urls("api.hunter.io")) == 1             # second lead did not call Hunter again
    assert any("Hunter" in e["message"] and "rejected" in e["message"] for e in report["errors"])


def test_credit_cap_stops_paid_lookups_but_not_free_ones(tmp_path):
    routes = {"api.hunter.io/v2/domain-search": {"data": {"emails": []}}}
    c, http = make(tmp_path, routes, pages={})
    connect(c, "hunter", {"api_key": "hk"})
    http.routes["greenhouse.io/v1/boards/beta-hr/jobs"] = BOARD
    http.routes["greenhouse.io/v1/boards/beta-hr"] = {"name": "Beta HR"}
    camp = campaign(c, ["website_deep", "hunter"], slugs="acme-hr|acme-hr.in, beta-hr|beta-hr.in", credit_cap=1)
    report = run(c, camp["id"])
    assert len(http.urls("domain-search")) == 1                                    # cap of 1 credit: one paid lookup only
    assert any("Credit cap" in e["message"] for e in report["errors"])


def test_cache_means_the_same_question_is_never_paid_for_twice(tmp_path):
    routes = {"api.hunter.io/v2/domain-search": {"data": {"emails": []}}}
    c, http = make(tmp_path, routes, pages={})
    connect(c, "hunter", {"api_key": "hk"})
    run(c, campaign(c, ["hunter"])["id"])
    first = len(http.calls)
    assert c.app.state.store.read("source_cache")                                  # answers are stored in the source_cache collection
    run(c, campaign(c, ["hunter"])["id"])                                          # a second campaign asks the same questions
    again = [u for u in http.urls() if "greenhouse" in u or "domain-search" in u]
    assert len(http.calls) == first and len(again) == len([u for u in http.urls() if "greenhouse" in u or "domain-search" in u])


def test_cache_expires_after_seven_days_and_stays_bounded(tmp_path):
    from app.store import Store
    from datetime import timedelta
    store = Store(tmp_path / "s.json")
    cache = Cache(store, NOW)
    cache.put("greenhouse|board:a", {"jobs": []})
    cache.flush()
    assert Cache(store, NOW + timedelta(days=6)).get("greenhouse|board:a") == {"v": {"jobs": []}}
    assert Cache(store, NOW + timedelta(days=8)).get("greenhouse|board:a") is None
    big = Cache(store, NOW)
    for i in range(1000):
        big.put(f"s|k{i}", i)
    big.flush()
    assert len(store.read("source_cache")) == 800


def test_suppressed_and_closed_companies_are_never_re_added_or_given_emails(tmp_path):
    c, http = make(tmp_path)
    store = c.app.state.store
    c.post("/api/leads/import", json={"csv": "company,contact_name,email,website\nAcme HR Services,Old,old@acme-hr.in,https://acme-hr.in\n", "source": "manual"})
    store.update("leads", lambda rows: [{**l, "stage": "unsubscribed"} for l in rows])
    report = run(c, campaign(c, [])["id"])
    assert report["new"] == 0 and report["suppressed_skipped"] == 1
    # a found address that is on the suppression list is never written to a lead
    store.update("leads", lambda rows: [])
    store.update("suppressed", lambda rows: [*rows, "meera@acme-hr.in"])
    run(c, campaign(c, ["website_deep", "builtin_verifier"])["id"])
    assert by_company(c, "Acme HR Services").get("email") != "meera@acme-hr.in"


def test_found_email_already_owned_by_another_lead_is_not_reused(tmp_path):
    c, http = make(tmp_path)
    c.post("/api/leads/import", json={"csv": "company,contact_name,email,website\nSister Co,Someone,meera@acme-hr.in,https://sister.in\n", "source": "manual"})
    run(c, campaign(c, ["website_deep", "builtin_verifier"])["id"])
    assert by_company(c, "Acme HR Services")["email"] != "meera@acme-hr.in"


def test_dry_run_runs_discovery_only_and_saves_no_leads(tmp_path):
    c, http = make(tmp_path)
    report = run(c, campaign(c, ["website_deep"])["id"], dry_run=True)
    assert report["discovered"] == 1 and report["new"] == 1 and c.get("/api/leads").json() == []


# ---------- validation and SSRF ----------
def test_unknown_or_wrong_capability_source_ids_are_refused(tmp_path):
    c, _ = make(tmp_path)
    bad = c.post("/api/campaigns", json={"name": "x", "sources": {"extra": ["nope"]}})
    assert bad.status_code == 422
    wrong = c.post("/api/campaigns", json={"name": "x", "sources": {"extra": ["kickbox"]}})        # a verifier cannot discover companies
    assert wrong.status_code == 422
    wrong = c.post("/api/campaigns", json={"name": "x", "enrich": {"extra": ["greenhouse"]}})
    assert wrong.status_code == 422
    assert c.post("/api/campaigns", json={"name": "ok", "sources": {"extra": ["lever"]}, "enrich": {"extra": ["hunter", "kickbox"]}}).status_code == 200


@pytest.mark.parametrize("slug", ["evil.com/x", "127.0.0.1#", "169.254.169.254/latest", "a b", "../x", "x@y", "acme?x=1", "http://evil.com"])
def test_typed_company_identifiers_can_never_change_the_host_or_path(slug, tmp_path):
    from app.sources.base import slugs
    assert slugs(slug) == []
    c, http = make(tmp_path)
    report = run(c, campaign(c, [], slugs=slug)["id"])
    assert report["errors"] and report["new"] == 0
    assert not [u for u in http.urls() if "evil.com" in u or "169.254" in u or "127.0.0.1" in u]


def test_slug_domain_pair_and_private_domains_are_checked():
    from app.sources.base import slugs, valid_host
    assert slugs("acme|acme.in, beta|127.0.0.1") == [("acme", "acme.in")] and slugs(["lever-co"]) == [("lever-co", "")]
    for bad in ("localhost", "10.0.0.1", "https://acme.in:8080/x", "user@acme.in", "a..b.in", "-x.in"):
        assert valid_host(bad) == "" or bad.startswith("https://acme.in")


def test_website_deep_refuses_private_hosts_without_fetching(tmp_path):
    from tests.source_fakes import ctx_for
    from app.sources import site_discover
    fetched = []
    ctx = ctx_for("website_deep", FakeHttp(), fetch=lambda u: fetched.append(u) or "")
    for bad in ("localhost", "192.168.1.5", "internal.local", "10.0.0.5"):
        assert site_discover.find_people(ctx, bad, "X", [], 5) == []
    assert fetched == []
