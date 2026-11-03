"""Every source adapter against fake responses shaped like the provider's documentation. Nothing touches the network."""
import json

import pytest

from app.campaign_sources import KeyRejected
from app.sources import builtin_verifier, registry
from app.sources.base import CreditCapReached, RateLimited
from tests.source_fakes import FakeHttp, HttpError, ctx_for

TARGET = {"industries": ["BPO"], "cities": ["Pune"], "roles_hiring": ["customer support"], "keywords": []}


def run_discover(sid, http, values=None, params=None, target=TARGET, limit=10, fetch=None):
    return registry.get(sid).discover(ctx_for(sid, http, values, params, fetch=fetch), target, limit)


# ---------- job-board feeds ----------
def test_greenhouse_board_to_company_row():
    http = FakeHttp({"/boards/acme-hr/jobs": {"jobs": [
        {"id": 1, "title": "Customer Support Executive", "updated_at": "2026-10-20T10:00:00-04:00", "location": {"name": "Pune, India"}, "absolute_url": "https://boards.greenhouse.io/acme-hr/jobs/1"},
        {"id": 2, "title": "Backend Engineer", "updated_at": "2026-10-30T10:00:00-04:00", "location": {"name": "Berlin"}, "absolute_url": "x"}], "meta": {"total": 2}},
        "/boards/acme-hr": {"name": "Acme HR Services"}})
    out = run_discover("greenhouse", http, params={"slugs": "acme-hr|acme-hr.in"})
    row = out["rows"][0]
    assert row["company"] == "Acme HR Services" and row["domain"] == "acme-hr.in" and row["open_roles"] == 1       # Berlin job filtered out
    assert row["role"] == "Customer Support Executive" and row["source"] == "greenhouse" and row["source_url"] == "https://boards.greenhouse.io/acme-hr"
    assert http.urls()[0] == "https://boards-api.greenhouse.io/v1/boards/acme-hr/jobs"


def test_lever_postings_with_millisecond_dates_and_eu_host():
    http = FakeHttp({"/v0/postings/beta": [{"id": "a", "text": "Telecaller", "categories": {"location": "Pune", "team": "Sales"}, "hostedUrl": "https://jobs.lever.co/beta/a", "createdAt": 1761000000000}]})
    http.routes["/v0/postings/beta"] = {"_list": [{"id": "a", "text": "Telecaller", "categories": {"location": "Pune"}, "hostedUrl": "https://jobs.lever.co/beta/a", "createdAt": 1761000000000}]}
    out = run_discover("lever", http, params={"slugs": "beta, eu-gamma"})
    assert out["rows"][0]["company"] == "Beta" and out["rows"][0]["open_roles"] == 1 and out["rows"][0]["max_job_age_days"] > 0
    assert out["failed_lookups"] == 1                                   # eu-gamma has no fake route: isolated failure
    assert any("api.eu.lever.co/v0/postings/gamma" in u for u in http.urls())
    assert "mode=json" in http.urls()[0]


def test_workable_widget_feed():
    http = FakeHttp({"apply.workable.com/api/v1/widget/accounts/zeta": {"name": "Zeta Foods", "jobs": [
        {"title": "Sales Executive", "city": "Pune", "state": "Maharashtra", "country": "India", "published_on": "2026-10-25", "url": "https://apply.workable.com/zeta/j/1"}]}})
    out = run_discover("workable", http, params={"slugs": "zeta"})
    assert out["rows"][0]["company"] == "Zeta Foods" and out["rows"][0]["city"] == "Pune, Maharashtra, India"


def test_ashby_skips_unlisted_jobs():
    http = FakeHttp({"posting-api/job-board/gamma": {"apiVersion": "1", "jobs": [
        {"title": "Support Lead", "location": "Pune", "isListed": True, "publishedAt": "2026-10-01T00:00:00.000+00:00", "jobUrl": "u1"},
        {"title": "Hidden", "location": "Pune", "isListed": False, "publishedAt": "2026-10-01", "jobUrl": "u2"}]}})
    out = run_discover("ashby", http, params={"slugs": "gamma"})
    assert out["rows"][0]["open_roles"] == 1 and "includeCompensation=false" in http.urls()[0]


def test_smartrecruiters_public_postings():
    http = FakeHttp({"/v1/companies/Delta/postings": {"totalFound": 1, "content": [
        {"id": "743", "name": "Customer Care Associate", "releasedDate": "2026-10-28T00:00:00.000Z", "company": {"name": "Delta Telecom"}, "location": {"city": "Pune", "country": "in"}}]}})
    out = run_discover("smartrecruiters", http, params={"slugs": "Delta"})
    assert out["rows"][0]["company"] == "Delta Telecom" and "country=in" in http.urls()[0]


def test_recruitee_offers_feed_and_unsafe_account_names():
    http = FakeHttp({"acme.recruitee.com/api/offers": {"offers": [{"title": "Support Agent", "city": "Pune", "country": "India", "created_at": "2026-10-10", "careers_url": "u", "status": "published"},
                                                                  {"title": "Draft", "city": "Pune", "status": "draft"}]}})
    out = run_discover("recruitee", http, params={"slugs": "acme"})
    assert out["rows"][0]["open_roles"] == 1
    http2 = FakeHttp()
    with pytest.raises(Exception):                                       # nothing valid to read -> readable error, no request
        run_discover("recruitee", http2, params={"slugs": "evil.com/x, 127.0.0.1#, a b, ../x"})
    assert http2.calls == []


def test_zoho_recruit_reads_jobposting_json_ld_through_the_polite_fetcher():
    from tests.campaign_fakes import job_html
    pages = {"https://acme.zohorecruit.in/jobs/Careers": job_html("Acme Pvt", "https://acme.in", ["Telecaller"], city="Pune")}
    def fetch(url):
        if url in pages:
            return pages[url]
        raise OSError("nope")
    out = run_discover("zoho_recruit", FakeHttp(), params={"slugs": "acme"}, fetch=fetch)
    assert out["rows"][0]["company"] == "Acme Pvt" and out["rows"][0]["open_roles"] == 1


def test_robots_refusal_on_a_careers_page_is_a_failed_lookup_not_a_crash():
    def fetch(url):
        raise PermissionError("robots.txt does not allow fetching this page")
    out = run_discover("zoho_recruit", FakeHttp(), params={"slugs": "acme"}, fetch=fetch)
    assert out["rows"] == [] and out["failed_lookups"] == 1


# ---------- job APIs ----------
ADZUNA = {"count": 2, "results": [
    {"id": "1", "title": "Customer Support Executive", "company": {"display_name": "Acme Retail"}, "location": {"display_name": "Pune, Maharashtra"}, "created": "2026-10-28T07:00:00Z", "redirect_url": "https://www.adzuna.in/land/1"},
    {"id": "2", "title": "Customer Support Lead", "company": {"display_name": "Acme Retail"}, "location": {"display_name": "Pune"}, "created": "2026-10-01T07:00:00Z", "redirect_url": "https://www.adzuna.in/land/2"}]}


def test_adzuna_groups_postings_by_employer():
    http = FakeHttp({"jobs/in/search/1": ADZUNA})
    out = run_discover("adzuna", http, values={"app_id": "id1", "app_key": "key1"})
    assert len(out["rows"]) == 1 and out["rows"][0]["open_roles"] == 2 and out["rows"][0]["source"] == "adzuna"
    url = http.urls()[0]
    assert "app_id=id1" in url and "what=customer+support" in url and "where=Pune" in url


def test_adzuna_test_uses_categories_not_a_search():
    http = FakeHttp({"jobs/in/categories": {"results": []}})
    assert registry.get("adzuna").test(ctx_for("adzuna", http, {"app_id": "a", "app_key": "b"}))["ok"]
    assert "search" not in http.urls()[0]


def test_jooble_posts_keywords_with_key_in_path():
    http = FakeHttp({"jooble.org/api/": {"totalCount": 1, "jobs": [{"title": "Telecaller", "location": "Pune", "company": "Zeta Foods", "updated": "2026-10-30T00:00:00", "link": "https://jooble.org/jdp/1"}]}})
    out = run_discover("jooble", http, values={"api_key": "jk-1"})
    call = http.calls[0]
    assert call["method"] == "POST" and call["url"].endswith("/api/jk-1") and call["body"]["keywords"] == "customer support" and call["body"]["location"] == "Pune"
    assert out["rows"][0]["company"] == "Zeta Foods"


def test_themuse_is_free_and_may_find_nothing_in_india():
    http = FakeHttp({"themuse.com/api/public/jobs": {"page_count": 1, "results": [{"name": "Support Rep", "publication_date": "2026-10-01T00:00:00Z",
                     "company": {"name": "Remote Co"}, "locations": [{"name": "Flexible / Remote"}], "refs": {"landing_page": "u"}}]}})
    out = run_discover("themuse", http)
    assert out["rows"] == []                                              # honest: only remote/US listings, none in Pune


# ---------- search APIs ----------
def test_google_cse_and_brave_turn_hits_into_company_sites_and_drop_linkedin():
    g = FakeHttp({"customsearch/v1": {"items": [
        {"title": "Acme Retail - Careers", "link": "https://acme-retail.in/careers", "snippet": "Join"},
        {"title": "Acme on LinkedIn", "link": "https://in.linkedin.com/company/acme", "snippet": ""},
        {"title": "Support jobs | Naukri", "link": "https://www.naukri.com/x", "snippet": ""}]}})
    out = run_discover("google_cse", g, values={"api_key": "k", "cx": "cx1"})
    assert [r["domain"] for r in out["rows"]] == ["acme-retail.in"] and "cx=cx1" in g.urls()[0]
    b = FakeHttp({"res/v1/web/search": {"web": {"results": [{"title": "Beta Logistics | Jobs", "url": "https://beta-logistics.in/jobs", "description": "d"}]}}})
    out = run_discover("brave_search", b, values={"api_key": "bk"})
    assert out["rows"][0]["company"] == "Beta Logistics" and b.calls[0]["headers"]["X-Subscription-Token"] == "bk"


def test_brave_pages_for_domain_only_same_host_careers_and_contact():
    b = FakeHttp({"res/v1/web/search": {"web": {"results": [
        {"title": "t", "url": "https://acme-retail.in/careers", "description": ""}, {"title": "t", "url": "https://other.com/contact", "description": ""},
        {"title": "t", "url": "https://acme-retail.in/blog/x", "description": ""}]}}})
    spec = registry.get("brave_search")
    assert spec.pages(ctx_for("brave_search", b, {"api_key": "k"}), "acme-retail.in") == ["https://acme-retail.in/careers"]
    assert spec.pages(ctx_for("brave_search", b, {"api_key": "k"}), "127.0.0.1") == []


# ---------- open data ----------
def test_overpass_query_and_mapping():
    q = registry.get("osm_overpass")
    from app.sources import osm_overpass
    query = osm_overpass.build_query("IT services", "Pune", 20)
    assert '"office"="it"' in query and 'area["name"="Pune"]' in query and "out tags center 20" in query
    with pytest.raises(ValueError):
        osm_overpass.build_query("IT", 'Pune"];out;//', 5)               # injection into the query is refused
    http = FakeHttp({"overpass-api.de/api/interpreter": {"elements": [
        {"type": "node", "id": 11, "tags": {"name": "Zed Soft", "office": "it", "website": "https://zedsoft.in", "phone": "+91 98765 43210", "addr:city": "Pune"}},
        {"type": "way", "id": 12, "tags": {"name": "No Site", "office": "it"}}]}})
    out = run_discover("osm_overpass", http, params={}, target={"industries": ["IT services"], "cities": ["Pune"]})
    assert [r["company"] for r in out["rows"]] == ["Zed Soft"] and out["rows"][0]["phone"] == "+919876543210"
    assert out["rows"][0]["source_url"] == "https://www.openstreetmap.org/node/11"
    assert "OutreachOS" in http.calls[0]["headers"]["User-Agent"] and http.calls[0]["form"]["data"].startswith("[out:json]")


def test_overpass_user_agent_carries_the_owners_contact():
    http = FakeHttp({"overpass-api.de": {"elements": []}})
    run_discover("osm_overpass", http, values={"contact": "ops@example.in"}, target={"industries": ["IT"], "cities": ["Pune"]})
    assert "ops@example.in" in http.calls[0]["headers"]["User-Agent"]


def test_nominatim_search_params_and_status_test():
    http = FakeHttp({"nominatim.openstreetmap.org/search": {"_list": [{"name": "Kappa Tech", "osm_type": "node", "osm_id": 5, "display_name": "Kappa Tech, Pune",
                                                                        "extratags": {"website": "https://kappa.in", "phone": "+91 99887 76655"}, "address": {"city": "Pune"}}]},
                     "status.php": {"_raw": "OK"}})
    out = run_discover("osm_nominatim", http, target={"industries": ["software company"], "cities": ["Pune"]})
    assert out["rows"][0]["domain"] == "kappa.in" and "countrycodes=in" in http.urls()[0] and "extratags=1" in http.urls()[0]
    assert registry.get("osm_nominatim").test(ctx_for("osm_nominatim", http))["ok"]


def test_opencorporates_needs_token_and_maps_registry_rows():
    http = FakeHttp({"companies/search": {"results": {"companies": [{"company": {"name": "Lambda Services Pvt Ltd", "company_number": "U72200MH2015PTC000001",
                     "registered_address_in_full": "Pune 411001", "opencorporates_url": "https://opencorporates.com/companies/in/U72200MH2015PTC000001"}}]}},
                     "account_status": {"results": {"account_status": {"status": "ok"}}}})
    out = run_discover("opencorporates", http, values={"api_key": "oc"}, target={"industries": ["services"]})
    assert out["rows"][0]["company"] == "Lambda Services Pvt Ltd" and "jurisdiction_code=in" in http.urls()[0] and "api_token=oc" in http.urls()[0]
    assert registry.get("opencorporates").test(ctx_for("opencorporates", http, {"api_key": "oc"}))["ok"]


def test_data_gov_in_resource_filters_by_city_and_status():
    rid = "0b3e2a6c-0000-4000-8000-000000000000"
    http = FakeHttp({"api.data.gov.in/resource/": {"total": 3, "count": 3, "records": [
        {"CORPORATE_IDENTIFICATION_NUMBER": "U1", "COMPANY_NAME": "MU Services Pvt Ltd", "COMPANY_STATUS": "Active", "REGISTERED_OFFICE_ADDRESS": "Baner, Pune 411045"},
        {"CORPORATE_IDENTIFICATION_NUMBER": "U2", "COMPANY_NAME": "Nu Struck Off", "COMPANY_STATUS": "Strike Off", "REGISTERED_OFFICE_ADDRESS": "Pune"},
        {"CORPORATE_IDENTIFICATION_NUMBER": "U3", "COMPANY_NAME": "Xi Mumbai Ltd", "COMPANY_STATUS": "Active", "REGISTERED_OFFICE_ADDRESS": "Mumbai"}]}})
    out = run_discover("data_gov_in", http, values={"api_key": "gk", "resource_id": rid}, target={"cities": ["Pune"]})
    assert [r["company"] for r in out["rows"]] == ["MU Services Pvt Ltd"] and f"/resource/{rid}" in http.urls()[0]
    with pytest.raises(ValueError):
        run_discover("data_gov_in", FakeHttp(), values={"api_key": "gk", "resource_id": "../../etc"}, target={})


def test_wikidata_sparql_rows_and_injection_safe_query():
    from app.sources import wikidata
    assert 'x") || (1' not in wikidata.build_query(['x") || (1'], 5)
    http = FakeHttp({"query.wikidata.org/sparql": {"results": {"bindings": [
        {"c": {"value": "http://www.wikidata.org/entity/Q1"}, "cLabel": {"value": "Omega Systems"}, "site": {"value": "https://omega.example.in"}, "emp": {"value": "3200"}, "hqLabel": {"value": "Bengaluru"}},
        {"c": {"value": "http://www.wikidata.org/entity/Q2"}, "cLabel": {"value": "Tiny Co"}, "site": {"value": "https://tiny.example.in"}, "emp": {"value": "12"}}]}}})
    out = run_discover("wikidata", http, target={"industries": ["IT"], "company_size_buckets": ["1001-5000"]})
    assert [r["company"] for r in out["rows"]] == ["Omega Systems"] and out["rows"][0]["company_size"] == 3200
    assert http.calls[0]["headers"]["Accept"] == "application/sparql-results+json" and "OutreachOS" in http.calls[0]["headers"]["User-Agent"]


def test_google_places_registry_source_wraps_the_existing_adapter():
    http = FakeHttp({"places.googleapis.com": {"places": [{"id": "p1", "displayName": {"text": "Sigma BPO"}, "websiteUri": "https://sigma-bpo.in", "nationalPhoneNumber": "098765 43210"}]}})
    out = run_discover("google_places", http, values={"api_key": "gk"})
    assert out["rows"][0]["company"] == "Sigma BPO" and out["rows"][0]["domain"] == "sigma-bpo.in"
    assert http.calls[0]["headers"]["X-Goog-Api-Key"] == "gk"


# ---------- people / email finders ----------
def test_hunter_domain_search_finder_verifier_and_free_test():
    http = FakeHttp({"domain-search": {"data": {"emails": [
        {"value": "asha@acme-retail.in", "confidence": 94, "first_name": "Asha", "last_name": "Rao", "position": "HR Head", "linkedin": "https://www.linkedin.com/in/asha-example", "verification": {"status": "valid"}},
        {"value": "dev@acme-retail.in", "confidence": 80, "first_name": "Dev", "last_name": "K", "position": "Software Engineer"}]}},
        "email-finder": {"data": {"email": "ravi@acme-retail.in", "score": 88, "verification": {"status": "accept_all"}}},
        "email-verifier": {"data": {"status": "valid", "result": "deliverable", "score": 96}}, "/account": {"data": {"requests": {"searches": {"used": 3, "available": 25}}}}})
    spec = registry.get("hunter")
    ctx = ctx_for("hunter", http, {"api_key": "hk"})
    people = spec.find_people(ctx, "acme-retail.in", "Acme", ["HR Head"], 5)
    assert [p["email"] for p in people] == ["asha@acme-retail.in"] and people[0]["verified"] is True and people[0]["confidence"] == 94
    found = spec.find_email(ctx, "Ravi", "Kumar", "acme-retail.in", "Acme")[0]
    assert found["email"] == "ravi@acme-retail.in" and found["verified"] is None and found["source"] == "hunter"
    assert spec.verify(ctx, "ravi@acme-retail.in")["status"] == "valid"
    assert "25" in spec.test(ctx)["message"]


def test_apollo_registry_source_people_by_domain_and_match():
    from tests.campaign_fakes import PERSON
    http = FakeHttp({"mixed_people/api_search": {"people": [PERSON]}, "people/match": {"person": {"name": "Ravi K", "email": "ravi@acme-retail.in", "title": "Founder"}}})
    spec = registry.get("apollo")
    ctx = ctx_for("apollo", http, {"api_key": "ak"})
    assert spec.find_people(ctx, "acme-retail.in", "Acme", ["HR Head"], 5)[0]["name"] == "Asha Rao"
    assert http.calls[0]["body"]["q_organization_domains_list"] == ["acme-retail.in"]
    assert spec.find_email(ctx, "Ravi", "K", "acme-retail.in", "Acme")[0]["email"] == "ravi@acme-retail.in"


def test_snov_polls_async_result_then_gives_up_quietly():
    http = FakeHttp({"oauth/access_token": {"access_token": "tok", "token_type": "Bearer", "expires_in": 3600},
                     "emails-by-domain-by-name/start": {"data": {"task_hash": "h1"}, "meta": {"rows": 1}},
                     "emails-by-domain-by-name/result": [{"status": "in_progress"}, {"status": "completed", "data": [{"people": "Ravi Kumar", "result": [
                         {"email": "ravi@acme-retail.in", "smtp_status": "valid", "is_valid_format": True, "is_disposable": False}]}]}]})
    ctx = ctx_for("snov", http, {"client_id": "i", "client_secret": "s"})
    got = registry.get("snov").find_email(ctx, "Ravi", "Kumar", "acme-retail.in", "Acme")
    assert got[0]["email"] == "ravi@acme-retail.in" and got[0]["verified"] is True and ctx.sleeps.count(2) == 1
    assert http.calls[1]["headers"]["Authorization"] == "Bearer tok"
    slow = FakeHttp({"oauth/access_token": {"access_token": "t"}, "start": {"data": {"task_hash": "h"}}, "result": {"status": "in_progress"}})
    assert registry.get("snov").find_email(ctx_for("snov", slow, {"client_id": "i", "client_secret": "s"}), "A", "B", "x.in", "X") == []


def test_snov_verifier_maps_smtp_status():
    http = FakeHttp({"oauth/access_token": {"access_token": "t"}, "email-verification/start": {"data": {"task_hash": "h"}},
                     "email-verification/result": {"status": "completed", "data": [{"email": "a@x.in", "result": {"smtp_status": "not_valid"}}]}})
    assert registry.get("snov").verify(ctx_for("snov", http, {"client_id": "i", "client_secret": "s"}), "a@x.in")["status"] == "invalid"


def test_prospeo_enrich_person_and_account_check():
    http = FakeHttp({"enrich-person": {"error": False, "person": {"full_name": "Ravi Kumar", "current_job_title": "Founder", "linkedin_url": "https://www.linkedin.com/in/ravi-example",
                                                                   "email": {"status": "VERIFIED", "email": "ravi@acme-retail.in"}}},
                     "account-information": {"error": False, "response": {"remaining_credits": 40}}})
    ctx = ctx_for("prospeo", http, {"api_key": "pk"})
    got = registry.get("prospeo").find_email(ctx, "Ravi", "Kumar", "acme-retail.in", "Acme")[0]
    assert got["verified"] is True and got["title"] == "Founder" and http.calls[0]["headers"]["X-KEY"] == "pk"
    assert http.calls[0]["body"]["data"]["company_website"] == "acme-retail.in"
    assert "40" in registry.get("prospeo").test(ctx)["message"]


def test_dropcontact_async_request_then_result():
    http = FakeHttp({"/v1/enrich/all/req-12345678": [{"success": False, "reason": "Request not ready yet"}, {"success": True, "data": [{"email": [{"email": "ravi@acme-retail.in", "qualification": "nominative@pro"}], "job": "Founder"}]}],
                     "/v1/enrich/all": {"error": False, "success": True, "request_id": "req-12345678", "credits_left": 99}})
    ctx = ctx_for("dropcontact", http, {"api_key": "dk"})
    got = registry.get("dropcontact").find_email(ctx, "Ravi", "Kumar", "acme-retail.in", "Acme")
    assert got[0]["email"] == "ravi@acme-retail.in" and got[0]["verified"] is True and ctx.sleeps.count(8) == 2
    assert http.calls[0]["headers"]["X-Access-Token"] == "dk"
    assert registry.get("dropcontact").test(ctx)["ok"] is False                 # honest: no free check exists


def test_lusha_keeps_only_work_emails_and_work_numbers():
    http = FakeHttp({"v2/person": {"contact": {"data": {"emailAddresses": [{"email": "ravi.k@gmail.com", "emailType": "personal"}, {"email": "ravi@acme-retail.in", "emailType": "work"}],
                     "phoneNumbers": [{"number": "+91 98765 43210", "phoneType": "mobile"}], "jobTitle": {"title": "Founder"}}}},
                     "account/usage": {"usage": {"used": 1, "remaining": 49, "total": 50}}})
    ctx = ctx_for("lusha", http, {"api_key": "lk"})
    got = registry.get("lusha").find_email(ctx, "Ravi", "Kumar", "acme-retail.in", "Acme")[0]
    assert got["email"] == "ravi@acme-retail.in" and got["phone"] == "" and got["title"] == "Founder" and http.calls[0]["headers"]["api_key"] == "lk"
    assert "49" in registry.get("lusha").test(ctx)["message"]


def test_rocketreach_polls_until_complete_and_keeps_professional_emails():
    http = FakeHttp({"person/lookup": {"id": 7, "status": "searching"},
                     "person/checkStatus": {"_list": [{"id": 7, "status": "complete", "current_title": "Founder", "linkedin_url": "https://www.linkedin.com/in/ravi-example",
                                                       "emails": [{"email": "ravi@acme-retail.in", "smtp_valid": "valid", "type": "professional", "grade": "A"},
                                                                  {"email": "r@gmail.com", "type": "personal"}]}]}})
    ctx = ctx_for("rocketreach", http, {"api_key": "rk"})
    got = registry.get("rocketreach").find_email(ctx, "Ravi", "Kumar", "acme-retail.in", "Acme")
    assert [g["email"] for g in got] == ["ravi@acme-retail.in"] and got[0]["verified"] is True and got[0]["confidence"] == 95


def test_people_data_labs_no_match_is_free_and_not_an_error():
    http = FakeHttp({"person/enrich": HttpError(404)})
    ctx = ctx_for("peopledatalabs", http, {"api_key": "pk"})
    assert registry.get("peopledatalabs").find_email(ctx, "A", "B", "x.in", "X") == [] and ctx.budget.spent == 1   # the estimate is conservative: charged before the call, PDL bills only matches
    ok = FakeHttp({"person/enrich": {"status": 200, "likelihood": 8, "data": {"work_email": "ravi@acme-retail.in", "job_title": "Founder", "mobile_phone": "+919876543210", "personal_emails": ["r@gmail.com"]}}})
    got = registry.get("peopledatalabs").find_email(ctx_for("peopledatalabs", ok, {"api_key": "pk"}), "Ravi", "Kumar", "acme-retail.in", "Acme")[0]
    assert got["email"] == "ravi@acme-retail.in" and got["phone"] == "" and got["confidence"] == 80


# ---------- verifiers ----------
@pytest.mark.parametrize("sid,route,reply,expected", [
    ("kickbox", "api.kickbox.com/v2/verify", {"result": "deliverable", "reason": "accepted_email", "role": False, "success": True}, "valid"),
    ("kickbox", "api.kickbox.com/v2/verify", {"result": "undeliverable", "reason": "rejected_email", "success": True}, "invalid"),
    ("kickbox", "api.kickbox.com/v2/verify", {"result": "risky", "reason": "low_quality", "success": True}, "risky"),
    ("millionverifier", "api.millionverifier.com/api/v3/", {"email": "a@x.in", "result": "ok", "resultcode": 1, "credits": 99, "error": ""}, "valid"),
    ("millionverifier", "api.millionverifier.com/api/v3/", {"result": "catch_all", "resultcode": 3, "error": ""}, "risky"),
    ("millionverifier", "api.millionverifier.com/api/v3/", {"result": "disposable", "resultcode": 5, "error": ""}, "invalid"),
    ("bouncer", "api.usebouncer.com/v1.1/email/verify", {"email": "a@x.in", "status": "deliverable", "reason": "accepted_email"}, "valid"),
    ("bouncer", "api.usebouncer.com/v1.1/email/verify", {"status": "undeliverable", "reason": "rejected_email"}, "invalid"),
    ("bouncer", "api.usebouncer.com/v1.1/email/verify", {"status": "unknown", "reason": "timeout"}, "unknown"),
    ("zerobounce", "api.zerobounce.net/v2/validate", {"address": "a@x.in", "status": "valid"}, "valid"),
    ("zerobounce", "api.zerobounce.net/v2/validate", {"status": "catch-all"}, "risky"),
    ("neverbounce", "api.neverbounce.com/v4/single/check", {"status": "success", "result": "invalid"}, "invalid"),
])
def test_paid_verifiers_map_to_one_vocabulary(sid, route, reply, expected):
    http = FakeHttp({route: reply})
    out = registry.get(sid).verify(ctx_for(sid, http, {"api_key": "k"}), "a@x.in")
    assert out["status"] == expected and out["provider"] == sid


def test_verifier_free_key_checks_and_kickbox_honesty():
    http = FakeHttp({"millionverifier.com/api/v3/credits": {"credits": 12}, "usebouncer.com/v1.1/credits": {"credits": 7},
                     "zerobounce.net/v2/getcredits": {"Credits": "40"}, "neverbounce.com/v4/account/info": {"status": "success"}})
    for sid in ("millionverifier", "bouncer", "zerobounce", "neverbounce"):
        assert registry.get(sid).test(ctx_for(sid, http, {"api_key": "k"}))["ok"]
    assert all("verify" not in u and "validate" not in u and "check" not in u for u in http.urls())     # no verification credit spent
    assert registry.get("kickbox").test(ctx_for("kickbox", http, {"api_key": "live_x"}))["ok"] is False
    assert registry.get("kickbox").test(ctx_for("kickbox", http, {"api_key": "test_x"}))["ok"] is True


def test_builtin_verifier_syntax_disposable_role_and_mx_via_doh():
    mx = {"cloudflare-dns.com/dns-query": lambda call: {"Status": 0, "Answer": [{"type": 15, "data": "10 mx.acme-retail.in."}]} if "acme-retail.in" in call["url"]
          else {"Status": 3} if "nowhere.invalid" in call["url"] else {"Status": 0, "Answer": [{"type": 15, "data": "0 ."}]}}
    http = FakeHttp(mx)
    v = lambda e: registry.get("builtin_verifier").verify(ctx_for("builtin_verifier", http), e)
    assert v("asha@acme-retail.in")["status"] == "plausible"
    assert v("info@acme-retail.in")["status"] == "risky"
    assert v("someone@mailinator.com")["status"] == "invalid" and v("not-an-email")["status"] == "invalid"
    assert v("x@nowhere.invalid")["status"] == "invalid" and v("x@nullmx.in")["status"] == "invalid"
    assert http.calls[0]["headers"]["Accept"] == "application/dns-json" and "type=MX" in http.calls[0]["url"]
    assert builtin_verifier.is_disposable("sub.yopmail.com")


# ---------- failure modes shared by every source ----------
def test_401_is_key_rejected_and_429_is_rate_limited():
    spec = registry.get("brave_search")
    with pytest.raises(KeyRejected):
        spec.discover(ctx_for("brave_search", FakeHttp({"": PermissionError("HTTP 401")}), {"api_key": "bad"}), TARGET, 5)
    with pytest.raises(RateLimited):
        spec.discover(ctx_for("brave_search", FakeHttp({"": HttpError(429)}), {"api_key": "k"}), TARGET, 5)


def test_rate_limit_spacing_uses_the_injected_sleep():
    http = FakeHttp({"nominatim.openstreetmap.org": {"_list": []}})
    ctx = ctx_for("osm_nominatim", http)
    ctx.get("https://nominatim.openstreetmap.org/search")
    ctx.get("https://nominatim.openstreetmap.org/search")
    assert ctx.sleeps and ctx.sleeps[0] == 2.0                           # 30 per minute -> 2 s apart, clock frozen in tests


def test_credit_cap_blocks_the_next_paid_call_but_not_cached_ones():
    http = FakeHttp({"email-verifier": {"data": {"status": "valid"}}})
    ctx = ctx_for("hunter", http, {"api_key": "k"}, cap=1)
    spec = registry.get("hunter")
    assert spec.verify(ctx, "a@x.in")["status"] == "valid"
    assert spec.verify(ctx, "a@x.in")["status"] == "valid" and len(http.calls) == 1        # cache hit: no second charge
    with pytest.raises(CreditCapReached):
        spec.verify(ctx, "b@x.in")
    assert len(http.calls) == 1
