"""Campaign discovery adapters with fake transports: Apollo, Google Places, careers pages, email verifiers."""
from datetime import datetime, timezone

import pytest

from app import campaign_sources as src
from app.http_util import HttpStatusError
from tests.campaign_fakes import ACME_JOBS, LOCKED, PERSON, ZETA_JOBS, FakeIO

NOW = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)
TARGET = {"industries": ["BPO"], "cities": ["Pune"], "roles_hiring": ["customer support"], "company_size_buckets": ["51-200"],
          "keywords": ["call centre"]}


def people(n, prefix="p"):
    return [{"id": f"{prefix}{i}", "first_name": f"Person{i}", "title": "HR Manager",
             "organization": {"name": f"Company {prefix}{i}", "primary_domain": f"company-{prefix}{i}.in"}} for i in range(n)]


# ---------- Apollo people search ----------
def test_apollo_people_request_and_mapping():
    io = FakeIO(post={"mixed_people/api_search": {"people": [PERSON, LOCKED]}})
    out = src.apollo_people(TARGET, "ak-test", io.post, 25)
    call = io.calls[0]
    assert call["url"] == "https://api.apollo.io/api/v1/mixed_people/api_search"
    assert call["headers"]["x-api-key"] == "ak-test" and "ak-test" not in call["url"]
    body = call["body"]
    assert "HR Head" in body["person_titles"] and "Founder" in body["person_titles"]
    assert body["organization_locations"] == ["Pune"] and body["organization_num_employees_ranges"] == ["51,200"]
    assert body["q_organization_keyword_tags"] == ["BPO", "call centre"] and body["per_page"] == 25 and body["page"] == 1
    asha, ravi = out["rows"]
    assert asha == {**asha, "company": "Acme Retail Pvt Ltd", "contact_name": "Asha Rao", "title": "HR Head",
                    "email": "asha@acme-retail.in", "domain": "acme-retail.in", "website": "https://acme-retail.in",
                    "linkedin_url": "https://www.linkedin.com/in/asha-example", "country": "IN", "company_size": 250,
                    "apollo_person_id": "p-asha", "source": "apollo_search"}
    # locked email, obfuscated last name, a javascript: link and a Facebook "website" are all dropped
    assert ravi["email"] == "" and ravi["contact_name"] == "Ravi" and ravi["linkedin_url"] == ""
    assert ravi["domain"] == "" and ravi["website"] == ""
    assert out["requests"] == 1


def test_apollo_people_credit_cap_and_paging():
    io = FakeIO(post={"api_search": lambda body: {"people": people(body["per_page"], f"pg{body['page']}-")}})
    out = src.apollo_people(TARGET, "k", io.post, 3)
    assert len(out["rows"]) == 3 and out["requests"] == 1 and io.calls[0]["body"]["per_page"] == 3
    io = FakeIO(post={"api_search": lambda body: {"people": people(100 if body["page"] == 1 else 40, f"pg{body['page']}-")}})
    out = src.apollo_people(TARGET, "k", io.post, 150)
    assert len(out["rows"]) == 140 and out["requests"] == 2          # a short page means there are no more
    io = FakeIO(post={"api_search": lambda body: {"people": people(100, f"pg{body['page']}-")}})
    out = src.apollo_people(TARGET, "k", io.post, 100)
    assert len(out["rows"]) == 100 and out["requests"] == 1           # never asks for more than max_results


def test_apollo_people_401_stops_with_plain_message():
    io = FakeIO(post={"api_search": PermissionError("HTTP 401")})
    with pytest.raises(src.KeyRejected, match="rejected the API key"):
        src.apollo_people(TARGET, "bad", io.post, 10)


# ---------- Apollo hiring companies ----------
def test_apollo_hiring_filters_by_job_titles_and_reads_job_postings():
    orgs = {"organizations": [{"id": "o-gamma", "name": "Gamma Care", "website_url": "https://gamma-care.in",
                               "primary_phone": {"number": "+91 98111 22233"}, "estimated_num_employees": 120, "country": "India"}]}
    postings = {"organization_job_postings": [
        {"title": "Customer Support Associate", "posted_at": "2026-10-01T00:00:00Z"},
        {"title": "Customer Support Team Lead", "posted_at": "2026-10-20T00:00:00Z"},
        {"title": "Accountant", "posted_at": "2026-10-30T00:00:00Z"}]}
    io = FakeIO(post={"mixed_companies/search": orgs}, get={"/organizations/o-gamma/job_postings": postings})
    out = src.apollo_hiring(TARGET, "k", io.post, io.get, 10, NOW)
    assert io.calls[0]["body"]["q_organization_job_titles"] == ["customer support"]
    assert io.calls[1]["url"] == "https://api.apollo.io/api/v1/organizations/o-gamma/job_postings"
    assert io.calls[1]["headers"]["x-api-key"] == "k"
    row = out["rows"][0]
    assert row["company"] == "Gamma Care" and row["domain"] == "gamma-care.in" and row["open_roles"] == 3
    assert row["role"] == "Customer Support Associate and Customer Support Team Lead" and row["max_job_age_days"] == 33
    assert row["phone"] == "+919811122233" and row["phone_type"] == "business" and out["requests"] == 2


def test_apollo_hiring_needs_a_role():
    with pytest.raises(src.SourceError, match="role they hire for"):
        src.apollo_hiring({**TARGET, "roles_hiring": []}, "k", FakeIO().post, FakeIO().get, 10, NOW)


# ---------- Google Places ----------
PLACES = {"places": [
    {"id": "pl1", "displayName": {"text": "Delta Call Services"}, "formattedAddress": "Baner, Pune, Maharashtra",
     "internationalPhoneNumber": "+91 98222 33344", "websiteUri": "https://delta-calls.in/", "businessStatus": "OPERATIONAL",
     "googleMapsUri": "https://maps.google.com/?cid=1"},
    {"id": "pl2", "displayName": {"text": "Epsilon BPO"}, "nationalPhoneNumber": "020 2612 3456",
     "websiteUri": "https://www.facebook.com/epsilonbpo"},
    {"id": "pl3", "displayName": {"text": "Closed Co"}, "businessStatus": "CLOSED_PERMANENTLY"}]}


def test_places_request_and_mapping():
    io = FakeIO(post={"places:searchText": PLACES})
    out = src.google_places(TARGET, "gk-test", io.post, 25)
    call = io.calls[0]
    assert call["url"] == "https://places.googleapis.com/v1/places:searchText"
    assert call["headers"]["X-Goog-Api-Key"] == "gk-test" and "places.websiteUri" in call["headers"]["X-Goog-FieldMask"]
    assert call["body"]["textQuery"] == "BPO in Pune" and call["body"]["regionCode"] == "IN" and call["body"]["pageSize"] == 20
    delta, epsilon = out["rows"]
    assert delta["company"] == "Delta Call Services" and delta["domain"] == "delta-calls.in" and delta["phone"] == "+919822233344"
    assert delta["phone_type"] == "business" and delta["source"] == "google_places" and delta["source_url"] == "https://maps.google.com/?cid=1"
    assert delta["city"] == "Pune" and "Baner" in delta["address"]
    assert epsilon["domain"] == "" and epsilon["phone"] == "" and epsilon["company_phone"] == "020 2612 3456"   # landline kept raw
    assert len(out["rows"]) == 2 and out["requests"] == 1


def test_places_bad_key_is_reported_as_rejected():
    io = FakeIO(post={"places": HttpStatusError(400, '{"error": {"message": "API key not valid. Please pass a valid API key."}}')})
    with pytest.raises(src.KeyRejected):
        src.google_places(TARGET, "bad", io.post, 5)
    with pytest.raises(src.KeyRejected):
        src.google_places(TARGET, "bad", FakeIO(post={"places": PermissionError("HTTP 403")}).post, 5)


def test_places_check_for_setup():
    io = FakeIO(post={"places": {"places": []}})
    assert src.check_places({"mode": "api", "values": {"api_key": "gk"}}, io.post)["ok"] is True
    assert io.calls[0]["headers"]["X-Goog-FieldMask"] == "places.id"
    assert src.check_places({}, io.post)["ok"] is False


# ---------- careers pages ----------
def test_careers_pages_use_the_jobposting_parser_and_never_read_job_boards():
    io = FakeIO(pages={"https://acme-retail.in/careers": ACME_JOBS})
    out = src.careers_postings(["https://acme-retail.in/careers", "https://www.naukri.com/acme-jobs", "https://gone.example/jobs"],
                               [{"url": "https://zeta-foods.in/careers", "html": ZETA_JOBS},
                                {"url": "https://in.indeed.com/x", "html": ZETA_JOBS}], io.fetch)
    assert io.urls() == ["https://acme-retail.in/careers", "https://gone.example/jobs"]   # the job board was never fetched
    assert len(out["postings"]) == 12 and out["pages"] == 2
    assert any("job boards are not read" in e for e in out["errors"]) and any("could not read it" in e for e in out["errors"])
    assert any("job board pages are not used" in e for e in out["errors"])


# ---------- email verifiers ----------
def test_verifiers_map_results_and_stop_on_key_errors():
    zb = {"provider": "zerobounce", "api_key": "zk", "supported": True}
    nb = {"provider": "neverbounce", "api_key": "nk", "supported": True}
    assert src.verify_email("a@acme.in", zb, FakeIO(get={"zerobounce": {"status": "valid"}}).get) == "valid"
    assert src.verify_email("a@acme.in", zb, FakeIO(get={"zerobounce": {"status": "catch-all"}}).get) == "risky"
    assert src.verify_email("a@acme.in", nb, FakeIO(get={"neverbounce": {"status": "success", "result": "invalid"}}).get) == "invalid"
    with pytest.raises(src.KeyRejected):
        src.verify_email("a@acme.in", zb, FakeIO(get={"zerobounce": {"error": "Invalid API key"}}).get)
    with pytest.raises(src.KeyRejected):
        src.verify_email("a@acme.in", nb, FakeIO(get={"neverbounce": {"status": "auth_failure"}}).get)
    assert src.verifier_config({"email_verifier": {"mode": "api", "values": {"provider": "ZeroBounce", "api_key": "k"}}})["supported"]
    assert src.verifier_config({"email_verifier": {"mode": "api", "values": {"provider": "acme-check", "api_key": "k"}}})["supported"] is False
    assert src.verifier_config({}) is None


# ---------- Setup and Integrations hub ----------
def test_google_places_is_a_lead_data_connector_and_setup_tests_spend_no_credits(tmp_path):
    from fastapi.testclient import TestClient

    from app import integrations
    from app.channels import registry
    from app.main import create_app

    assert "google_places" in integrations.known_ids()
    assert registry.get_provider("lead_data", "google_places")["docs_url"].startswith("https://developers.google.com/")
    io = FakeIO(post={"usage_stats": {"api/v1/mixed_people/api_search": {"day": {"limit": 600}}}, "places": {"places": []}})
    c = TestClient(create_app(data_file=tmp_path / "s.json", apollo_transport=io.post))
    c.put("/api/integrations/apollo", json={"mode": "api", "values": {"api_key": "ak"}})
    assert c.post("/api/integrations/apollo/test", json={}).json()["ok"] is True
    assert io.calls[-1]["url"] == "https://api.apollo.io/api/v1/usage_stats/api_usage_stats"     # free usage check, not a match
    assert c.post("/api/integrations/google_places/test", json={}).json()["ok"] is False          # not connected yet
    c.put("/api/integrations/google_places", json={"mode": "api", "values": {"api_key": "gk-secret-1234"}})
    shown = next(i for i in c.get("/api/integrations").json() if i["id"] == "google_places")
    assert shown["values"]["api_key"] == "••••1234" and shown["status"] == "configured"
    assert c.post("/api/integrations/google_places/test", json={}).json()["ok"] is True
    assert io.calls[-1]["headers"]["X-Goog-FieldMask"] == "places.id"
