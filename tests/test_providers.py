import io
import json
import urllib.error

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import integrations, providers
from app.lead_model import build_lead, event
from app.routes_providers import make_router
from app.routes_setup import make_router as make_setup_router
from app.store import Store


class FakePost:
    """Stands in for post_json(url, headers, body)."""

    def __init__(self, reply=None, error=None, by_url=None):
        self.calls, self.reply, self.error, self.by_url = [], reply or {}, error, by_url or {}

    def __call__(self, url, headers, body):
        self.calls.append({"url": url, "headers": headers, "body": body})
        if self.error:
            raise self.error
        page = body.get("url") if isinstance(body, dict) else None
        if page in self.by_url:
            return {"success": True, "data": {"html": self.by_url[page]}}
        return self.reply


class FakeGet:
    """Stands in for get_json(url, headers)."""

    def __init__(self, reply=None, error=None):
        self.calls, self.reply, self.error = [], reply or {}, error

    def __call__(self, url, headers=None):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.reply(url) if callable(self.reply) else self.reply


def page_fetcher(pages, seen=None):
    def fetch(url):
        if seen is not None:
            seen.append(url)
        if url in pages:
            return pages[url]
        raise OSError("not found")
    return fetch


def lead(**row):
    built = build_lead({k: str(v) for k, v in row.items()}, "test", allow_company_key=True)
    assert built, row
    return built


def hunter_reply(domain):
    return {"data": {"domain": domain, "linkedin": "https://www.linkedin.com/company/x", "twitter": None,
                     "emails": [
                         {"value": f"Priya@{domain}", "confidence": 90, "first_name": "Priya", "last_name": "Sharma",
                          "position": "HR Manager", "phone_number": "+91 98111 22233",
                          "sources": [{"domain": domain, "uri": f"https://{domain}/team"}]},
                         {"value": f"hr@{domain}", "confidence": 70, "sources": []},
                         {"value": "someone@other.com", "confidence": 99},
                     ]}}


def hunter_by_url(url):
    domain = url.split("domain=")[1].split("&")[0]
    return hunter_reply(domain)


def seeded_store(tmp_path, leads=None):
    store = Store(tmp_path / "d.json")
    store.write("leads", leads if leads is not None else [
        lead(company="Acme", website="https://acme.in", country="IN"),
        lead(company="Beta", website="https://beta.co.in", phone="9811122233"),
        lead(company="Gamma", website="https://gamma.com", country="US"),
    ])
    return store


def connect(store, integration_id, values, mode="api"):
    saved = store.read("integrations")
    store.write("integrations", {**saved, integration_id: {"mode": mode, "values": values}})


def client_for(store, fetcher=None, post=None, get=None):
    app = FastAPI()
    app.include_router(make_router(store, fetcher or page_fetcher({}), post or FakePost(), get or FakeGet()))
    return TestClient(app)


# ---- transports ----
def test_get_json_maps_auth_errors_to_permission_error(monkeypatch):
    def deny(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "no", {}, io.BytesIO(b""))
    monkeypatch.setattr(providers.urllib.request, "urlopen", deny)
    with pytest.raises(PermissionError):
        providers.get_json("https://api.example.com/x")

    def broken(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 500, "boom", {}, io.BytesIO(b""))
    monkeypatch.setattr(providers.urllib.request, "urlopen", broken)
    with pytest.raises(OSError):
        providers.get_json("https://api.example.com/x")


# ---- hunter ----
def test_hunter_parses_domain_search_and_keeps_only_same_domain_emails():
    get = FakeGet(hunter_reply("acme.in"))
    found = providers.find_hunter("acme.in", "Acme", {"api_key": "hk_123"}, {"get": get})
    assert "domain=acme.in" in get.calls[0] and "api_key=hk_123" in get.calls[0] and "limit=10" in get.calls[0]
    assert get.calls[0].startswith("https://api.hunter.io/v2/domain-search?")
    values = [e["value"] for e in found["emails"]]
    assert values == ["priya@acme.in", "hr@acme.in"]
    first = found["emails"][0]
    assert first == {"value": "priya@acme.in", "source": "https://acme.in/team", "confidence": 90,
                     "name": "Priya Sharma", "title": "HR Manager"}
    assert found["emails"][1]["source"] == "hunter.io"
    assert found["phones"] == [{"value": "+919811122233", "source": "https://acme.in/team"}]
    assert found["socials"] == {"linkedin": "https://www.linkedin.com/company/x"}
    assert found["credits_used"] == 1 and "error" not in found


def test_hunter_handles_empty_reply():
    found = providers.parse_hunter({}, "acme.in")
    assert found["emails"] == [] and found["phones"] == []


# ---- firecrawl ----
def test_firecrawl_fetcher_feeds_find_public_contacts_with_source_pages():
    post = FakePost(by_url={"https://acme.in/": "<p>Write to hr@acme.in or x@spam.com</p>",
                            "https://acme.in/contact": "Call +91 98111 22233"})
    config = {"base_url": "http://localhost:3002/", "api_key": "fc_key"}
    found = providers.find_firecrawl("acme.in", "Acme", config, {"post": post})
    assert found["emails"] == [{"value": "hr@acme.in", "source": "https://acme.in/"}]
    assert found["phones"] == [{"value": "+919811122233", "source": "https://acme.in/contact"}]
    assert all(c["url"] == "http://localhost:3002/v1/scrape" for c in post.calls)
    assert post.calls[0]["headers"] == {"Authorization": "Bearer fc_key"}
    assert post.calls[0]["body"] == {"url": "https://acme.in/", "formats": ["html"]}
    assert len(post.calls) == providers.FIRECRAWL_MAX_PAGES and found["credits_used"] == providers.FIRECRAWL_MAX_PAGES


def test_firecrawl_defaults_to_cloud_and_sends_no_header_without_key():
    post = FakePost(reply={"success": True, "data": {"html": ""}})
    providers.find_firecrawl("acme.in", "Acme", {}, {"post": post})
    assert post.calls[0]["url"] == "https://api.firecrawl.dev/v1/scrape" and post.calls[0]["headers"] == {}


def test_firecrawl_refusal_stops_after_first_call_and_reports_error():
    post = FakePost(error=PermissionError("HTTP 401"))
    found = providers.find_firecrawl("acme.in", "Acme", {"api_key": "bad"}, {"post": post})
    assert len(post.calls) == 1 and found["error"] == providers.FIRECRAWL_REFUSED and found["emails"] == []


def test_firecrawl_failed_scrape_is_skipped():
    post = FakePost(reply={"success": False, "error": "timeout"})
    found = providers.find_firecrawl("acme.in", "Acme", {"api_key": "k"}, {"post": post})
    assert found["emails"] == [] and "error" not in found


# ---- apollo ----
def test_apollo_needs_a_contact_name_and_makes_no_call():
    post = FakePost({"person": {"email": "a@acme.in"}})
    found = providers.find_apollo("acme.in", "Acme", {"api_key": "ak"}, {"post": post}, contact_name="")
    assert found["error"] == providers.NEEDS_NAME and post.calls == [] and found["credits_used"] == 0


def test_apollo_matches_named_person_and_ignores_locked_emails():
    post = FakePost({"person": {"email": "Ravi@Acme.in", "title": "CHRO", "name": "Ravi Kumar"}})
    found = providers.find_apollo("acme.in", "Acme", {"api_key": "ak"}, {"post": post}, contact_name="Ravi Kumar")
    assert found["emails"] == [{"value": "ravi@acme.in", "source": "api.apollo.io", "name": "Ravi Kumar", "title": "CHRO"}]
    assert post.calls[0]["body"]["first_name"] == "Ravi" and post.calls[0]["headers"]["x-api-key"] == "ak"
    locked = FakePost({"person": {"email": "email_not_unlocked@domain.com"}})
    assert providers.find_apollo("acme.in", "Acme", {"api_key": "ak"}, {"post": locked}, contact_name="Ravi")["emails"] == []


# ---- sampling ----
def test_sample_prefers_indian_leads_and_skips_unusable_ones():
    leads = [
        lead(company="US1", website="https://us1.com", country="US"),
        lead(company="US2", website="https://us2.com", country="US"),
        lead(company="IN by phone", website="https://phone.com", phone="9811122233"),
        lead(company="IN by domain", website="https://shop.in"),
        lead(company="IN by country", website="https://country.com", country="India"),
        {**lead(company="Closed", website="https://closed.in"), "stage": "unsubscribed"},
        lead(company="Local", website="http://localhost:8000"),
        lead(company="No site", email="a@b.in"),
    ]
    picked = [l["company"] for l in providers.pick_sample(leads, 3)]
    assert set(picked) == {"IN by phone", "IN by domain", "IN by country"}
    everyone = [l["company"] for l in providers.pick_sample(leads, 50)]
    assert "Closed" not in everyone and "Local" not in everyone and everyone[-2:] == ["US1", "US2"]
    assert providers.valid_domain("127.0.0.1") is None and providers.valid_domain("WWW.Acme.IN/") == "acme.in"


def test_sample_puts_leads_missing_email_first_within_a_country_group():
    leads = [lead(company="Has", website="https://has.in", email="hr@has.in"), lead(company="Needs", website="https://needs.in")]
    assert [l["company"] for l in providers.pick_sample(leads, 2)] == ["Needs", "Has"]


# ---- comparison ----
def test_compare_runs_every_provider_on_the_same_sample_and_never_writes_contacts(tmp_path):
    store = seeded_store(tmp_path)
    connect(store, "hunter", {"api_key": "hk"})
    before = store.read("leads")
    visited = []
    fetch = page_fetcher({"https://acme.in/": "hr@acme.in", "https://beta.co.in/contact": "+91 98111 22233"}, visited)
    get = FakeGet(hunter_by_url)
    run = providers.compare(store, ["website", "hunter"], 2, {"fetch": fetch, "get": get, "post": FakePost()})
    website_domains = {u.split("/")[2] for u in visited}
    hunter_domains = {u.split("domain=")[1].split("&")[0] for u in get.calls}
    assert website_domains == hunter_domains == {"acme.in", "beta.co.in"}            # Indian leads, same for both
    assert store.read("leads") == before                                              # comparison only
    saved = store.read("provider_runs")
    assert len(saved) == 1 and saved[0]["id"] == run["id"] and set(saved[0]["rows"]) == {"website", "hunter"}
    assert "rows" not in run and "lead_ids" not in run and run["sample_size"] == 2
    metrics = {m["provider"]: m for m in run["providers"]}
    assert metrics["website"]["with_email"] == 1 and metrics["website"]["with_phone"] == 1
    assert metrics["hunter"]["with_email"] == 2 and metrics["hunter"]["coverage_pct"] == 100.0
    assert metrics["hunter"]["avg_confidence"] == 80.0 and metrics["hunter"]["credits_used"] == 2
    assert metrics["hunter"]["cost_per_contact"] == 1.0 and metrics["website"]["credits_used"] == 0
    assert metrics["hunter"]["bounced"] == 0 and len(metrics["hunter"]["examples"]) == 2


def test_compare_skips_providers_that_are_not_set_up(tmp_path):
    store = seeded_store(tmp_path)
    run = providers.compare(store, ["hunter", "firecrawl", "apollo"], 5, {"fetch": page_fetcher({}), "get": FakeGet(), "post": FakePost()})
    assert all(m["skipped"] and m["reason"] for m in run["providers"])
    connect(store, "firecrawl", {})
    assert "self-hosted" in providers.provider_status(store.read("integrations"), "firecrawl")["reason"]
    connect(store, "firecrawl", {"base_url": "http://localhost:3002"})
    assert providers.provider_status(store.read("integrations"), "firecrawl")["configured"] is True


def test_compare_rejects_unknown_providers_and_empty_samples(tmp_path):
    with pytest.raises(ValueError, match="Unknown provider"):
        providers.compare(seeded_store(tmp_path), ["website", "zoominfo"], 5, {})
    with pytest.raises(ValueError, match="No open leads"):
        providers.compare(seeded_store(tmp_path / "empty", leads=[]), ["website"], 5, {})


def test_rejected_key_stops_the_provider_to_save_credits(tmp_path):
    store = seeded_store(tmp_path)
    connect(store, "hunter", {"api_key": "bad"})
    get = FakeGet(error=PermissionError("HTTP 401"))
    run = providers.compare(store, ["hunter"], 3, {"get": get})
    metrics = run["providers"][0]
    assert len(get.calls) == 1 and metrics["leads_checked"] == 1 and metrics["errors"] == 1
    assert metrics["stopped"] == providers.KEY_REJECTED


def test_apollo_in_compare_counts_missing_names_as_errors_without_calls(tmp_path):
    store = seeded_store(tmp_path, [lead(company="Acme", website="https://acme.in"),
                                    lead(company="Beta", website="https://beta.in", contact_name="Ravi Kumar")])
    connect(store, "apollo", {"api_key": "ak"})
    post = FakePost({"person": {"email": "ravi@beta.in"}})
    metrics = providers.compare(store, ["apollo"], 5, {"post": post})["providers"][0]
    assert len(post.calls) == 1 and metrics["errors"] == 1 and metrics["with_email"] == 1
    assert metrics["error_messages"] == [providers.NEEDS_NAME] and metrics["cost_per_contact"] == 1.0


def test_summary_metrics_math():
    rows = [
        {"lead_id": "1", "company": "A", "domain": "a.in", "emails": [{"value": "hr@a.in", "source": "s", "confidence": 90},
                                                                      {"value": "x@a.in", "source": "s", "confidence": 60}],
         "phones": [], "credits_used": 1},
        {"lead_id": "2", "company": "B", "domain": "b.in", "emails": [], "phones": [{"value": "+919811122233", "source": "s"}], "credits_used": 1},
        {"lead_id": "3", "company": "C", "domain": "c.in", "emails": [], "phones": [], "credits_used": 1, "error": "lookup failed: x"},
        {"lead_id": "4", "company": "D", "domain": "d.in", "emails": [{"value": "info@d.in", "source": "s"}], "phones": [], "credits_used": 0},
    ]
    m = providers.summarise("hunter", rows)
    assert (m["leads_checked"], m["with_email"], m["with_phone"], m["emails_found"]) == (4, 2, 1, 3)
    assert m["avg_confidence"] == 75.0 and m["credits_used"] == 3 and m["errors"] == 1
    assert m["coverage_pct"] == 50.0 and m["cost_per_contact"] == 1.5 and m["stopped"] == ""
    assert m["examples"][0]["email"] == "hr@a.in" and len(m["examples"]) == 4
    empty = providers.summarise("website", [])
    assert empty["coverage_pct"] == 0.0 and empty["cost_per_contact"] == 0.0 and empty["avg_confidence"] is None


# ---- apply ----
def test_apply_fills_only_empty_fields_tags_sources_and_skips_closed_leads(tmp_path):
    acme = lead(company="Acme", website="https://acme.in")
    beta = lead(company="Beta", website="https://beta.in", email="boss@beta.in")
    gone = {**lead(company="Gone", website="https://gone.in"), "stage": "unsubscribed"}
    store = seeded_store(tmp_path, [acme, beta, gone])
    connect(store, "hunter", {"api_key": "hk"})
    run = providers.compare(store, ["hunter"], 5, {"get": FakeGet(hunter_by_url)})
    assert store.read("provider_runs")[0]["lead_ids"] == [acme["id"], beta["id"]]   # closed lead never sampled
    # Pretend the closed lead was in the run (e.g. it unsubscribed after the comparison).
    store.update("provider_runs", lambda runs: [{**r, "rows": {"hunter": [*r["rows"]["hunter"], {
        "lead_id": gone["id"], "company": "Gone", "domain": "gone.in", "emails": [{"value": "hr@gone.in", "source": "x"}], "phones": []}]}}
        for r in runs])
    result = providers.apply(store, run["id"], "hunter")
    leads = {l["id"]: l for l in store.read("leads")}
    assert result == {"filled_emails": 1, "filled_phones": 2, "skipped_closed": 1, "checked": 3}
    assert leads[acme["id"]]["email"] == "hr@acme.in"                                  # hiring mailbox preferred
    assert leads[acme["id"]]["email_source"] == "hunter:hunter.io" and leads[acme["id"]]["dedupe_key"] == "email:hr@acme.in"
    assert leads[acme["id"]]["phone_source"] == "hunter:https://acme.in/team"
    assert leads[beta["id"]]["email"] == "boss@beta.in" and leads[beta["id"]]["phone"] == "+919811122233"
    assert not leads[gone["id"]].get("email") and not leads[gone["id"]].get("phone")
    assert any(e["kind"] == "enrichment" and "Hunter" in e["text"] for e in leads[acme["id"]]["timeline"])
    assert store.read("provider_runs")[0]["applied"] == ["hunter"]


def test_apply_skips_suppressed_emails_and_validates_ids(tmp_path):
    store = seeded_store(tmp_path, [lead(company="Acme", website="https://acme.in")])
    store.write("suppressed", ["hr@acme.in"])
    connect(store, "hunter", {"api_key": "hk"})
    run = providers.compare(store, ["hunter"], 5, {"get": FakeGet(hunter_by_url)})
    providers.apply(store, run["id"], "hunter")
    assert store.read("leads")[0]["email"] == "priya@acme.in"
    with pytest.raises(LookupError):
        providers.apply(store, "nope", "hunter")
    with pytest.raises(ValueError):
        providers.apply(store, run["id"], "website")


def test_bounced_metric_counts_leads_whose_email_came_from_the_provider(tmp_path):
    bounced = {**lead(company="Acme", website="https://acme.in", email="hr@acme.in"), "stage": "disqualified",
               "email_source": "hunter:hunter.io"}
    bounced = {**bounced, "timeline": [*bounced["timeline"], event("reply", "[bounce] Mail delivery failed")]}
    old_enrich = {**lead(company="Beta", website="https://beta.in", email="hr@beta.in"), "stage": "disqualified",
                  "email_source": "enrichment:https://beta.in/"}
    old_enrich = {**old_enrich, "timeline": [*old_enrich["timeline"], event("reply", "[bounce] undeliverable")]}
    not_bounced = {**lead(company="Gamma", website="https://gamma.in", email="hr@gamma.in"), "stage": "disqualified",
                   "email_source": "hunter:x"}
    counts = providers.bounced_counts([bounced, old_enrich, not_bounced])
    assert counts == {"website": 1, "hunter": 1, "firecrawl": 0, "apollo": 0}
    store = seeded_store(tmp_path, [bounced, old_enrich, not_bounced, lead(company="Open", website="https://open.in")])
    providers.compare(store, ["website"], 5, {"fetch": page_fetcher({})})
    assert providers.list_runs(store)[0]["providers"][0]["bounced"] == 1


def test_runs_are_listed_latest_first_and_capped(tmp_path, monkeypatch):
    store = seeded_store(tmp_path)
    monkeypatch.setattr(providers, "MAX_RUNS", 3)
    ids = [providers.compare(store, ["website"], 1, {"fetch": page_fetcher({})})["id"] for _ in range(4)]
    listed = providers.list_runs(store)
    assert [r["id"] for r in listed] == list(reversed(ids[1:]))
    assert all("rows" not in r for r in listed)


# ---- contact scraper import ----
SCRAPER_OUTPUT = [
    {"domain": "https://www.Acme.in/", "title": "Acme", "description": "Retail",
     "emails": [{"value": "sales@acme.in", "sources": ["https://acme.in/contact"], "is_likely_official": False},
                {"value": "hr@acme.in", "sources": ["https://acme.in/careers"], "is_likely_official": True}],
     "phones": [{"value": "+91 98111 22233", "sources": ["https://acme.in/contact"]}],
     "linkedin": [{"value": "https://www.linkedin.com/company/acme", "is_likely_official": True}],
     "instagram": ["https://instagram.com/acme"],
     "technologies": [{"name": "WordPress", "versions": ["6.4"], "categories": ["CMS"]}, "Cloudflare"],
     "error": None},
    {"domain": "beta.in", "emails": ["mailto:info@beta.in", "careers@beta.in"], "phones": ["9876543210"],
     "socials": {"facebook": "https://facebook.com/beta"}},
    {"domain": "closed.in", "emails": ["hr@closed.in"], "x": ["https://x.com/closed"]},
    {"domain": "unknown.in", "emails": ["a@unknown.in"]},
    {"domain": "broken.in", "error": "Timeout"},
]


def test_import_fills_gaps_with_sources_from_both_email_shapes(tmp_path):
    acme = lead(company="Acme", website="acme.in")
    beta = lead(company="Beta", website="http://beta.in/about", email="boss@beta.in")
    closed = {**lead(company="Closed", website="https://closed.in"), "stage": "disqualified"}
    store = seeded_store(tmp_path, [acme, beta, closed])
    result = providers.import_contact_scraper_json(store, json.dumps(SCRAPER_OUTPUT))
    assert result["matched"] == 3 and result["filled_emails"] == 1 and result["filled_phones"] == 2
    assert result["unmatched_domains"] == ["broken.in", "unknown.in"] and result["errors"] == 1
    leads = {l["company"]: l for l in store.read("leads")}
    a = leads["Acme"]
    assert a["email"] == "hr@acme.in" and a["email_source"] == "contact_scraper:https://acme.in/careers"
    assert a["phone"] == "+919811122233" and a["phone_source"] == "contact_scraper:https://acme.in/contact"
    assert a["company_socials"] == {"linkedin": "https://www.linkedin.com/company/acme", "instagram": "https://instagram.com/acme"}
    assert a["technologies"][0] == {"name": "WordPress", "versions": ["6.4"], "categories": ["CMS"]}
    assert a["technologies"][1]["name"] == "Cloudflare"
    assert any(e["kind"] == "enrichment" and "Contact scraper" in e["text"] for e in a["timeline"])
    b = leads["Beta"]
    assert b["email"] == "boss@beta.in" and b["phone"] == "+919876543210" and b["phone_source"] == "contact_scraper:beta.in"
    assert b["company_socials"] == {"facebook": "https://facebook.com/beta"}
    c = leads["Closed"]
    assert not c.get("email") and c["company_socials"] == {"x": "https://x.com/closed"}


def test_import_accepts_lists_and_wrapped_objects(tmp_path):
    store = seeded_store(tmp_path, [lead(company="Beta", website="https://beta.in")])
    assert providers.import_contact_scraper_json(store, [{"website": "beta.in", "emails": ["careers@beta.in"]}])["filled_emails"] == 1
    assert providers.parse_import_payload({"results": [{"domain": "x.in"}]}) == [{"domain": "x.in"}]
    assert providers.parse_import_payload({"domain": "x.in"}) == [{"domain": "x.in"}]
    with pytest.raises(ValueError):
        providers.parse_import_payload("{not json")
    with pytest.raises(ValueError):
        providers.parse_import_payload('"just a string"')
    with pytest.raises(ValueError):
        providers.import_contact_scraper_json(store, [{"title": "no domain"}])


def test_import_ignores_off_domain_unofficial_emails_but_keeps_official_ones(tmp_path):
    store = seeded_store(tmp_path, [lead(company="A", website="https://a.in"), lead(company="B", website="https://b.in")])
    providers.import_contact_scraper_json(store, [
        {"domain": "a.in", "emails": ["someone@gmail.com"]},
        {"domain": "b.in", "emails": [{"email": "bshop@gmail.com", "is_likely_official": True, "source": "https://b.in/"}]},
    ])
    leads = {l["company"]: l for l in store.read("leads")}
    assert not leads["A"].get("email")
    assert leads["B"]["email"] == "bshop@gmail.com" and leads["B"]["email_source"] == "contact_scraper:https://b.in/"


# ---- router ----
def test_router_endpoints(tmp_path):
    store = seeded_store(tmp_path)
    connect(store, "hunter", {"api_key": "hk"})
    c = client_for(store, get=FakeGet(hunter_by_url))
    listed = {p["id"]: p for p in c.get("/api/providers").json()}
    assert listed["website"]["configured"] and listed["hunter"]["configured"] and not listed["firecrawl"]["configured"]
    assert listed["hunter"]["cost_note"] and listed["hunter"]["setup_id"] == "hunter"
    run = c.post("/api/providers/compare", json={"providers": ["hunter", "firecrawl"], "sample_size": 2}).json()
    assert run["providers"][1]["skipped"] is True
    assert c.post("/api/providers/compare", json={"providers": ["nope"]}).status_code == 400
    assert c.post("/api/providers/compare", json={"providers": []}).status_code == 422
    assert c.post("/api/providers/compare", json={"providers": ["website"], "sample_size": 0}).status_code == 422
    runs = c.get("/api/providers/runs").json()
    assert runs[0]["id"] == run["id"] and "rows" not in runs[0]
    applied = c.post("/api/providers/apply", json={"run_id": run["id"], "provider": "hunter"}).json()
    assert applied["filled_emails"] == 2
    assert c.post("/api/providers/apply", json={"run_id": run["id"], "provider": "zoominfo"}).status_code == 400
    assert c.post("/api/providers/apply", json={"run_id": "missing", "provider": "hunter"}).status_code == 404
    assert c.post("/api/providers/apply", json={"run_id": run["id"], "provider": "firecrawl"}).status_code == 400


def test_router_import_reports_bad_json_clearly(tmp_path):
    store = seeded_store(tmp_path)
    c = client_for(store)
    bad = c.post("/api/providers/import-contact-scraper", json={"json": "{oops"})
    assert bad.status_code == 400 and "JSON" in bad.json()["detail"]
    assert c.post("/api/providers/import-contact-scraper", json={"json": ""}).status_code == 400
    assert c.post("/api/providers/import-contact-scraper", json={"json": 42}).status_code == 400
    ok = c.post("/api/providers/import-contact-scraper", json={"json": [{"domain": "acme.in", "emails": ["hr@acme.in"]}]})
    assert ok.status_code == 200 and ok.json()["filled_emails"] == 1


# ---- catalogue and Setup tests ----
def test_catalogue_has_new_connectors_with_secrets_marked():
    by_id = {i["id"]: i for i in integrations.CATALOGUE}
    assert by_id["hunter"]["group"] == "Lead data" and by_id["hunter"]["modes"] == ["api"]
    assert [f for f in by_id["hunter"]["fields"] if f["key"] == "api_key"][0]["secret"] is True
    fc = {f["key"]: f for f in by_id["firecrawl"]["fields"]}
    assert fc["api_key"]["secret"] is True and fc["api_key"]["required"] is False and fc["base_url"]["required"] is False
    assert any("AGPL" in s for s in by_id["firecrawl"]["steps"])
    assert by_id["contact_scraper"]["modes"] == ["manual"] and by_id["contact_scraper"]["fields"] == []
    assert any("proxies" in s for s in by_id["contact_scraper"]["steps"])
    shown = {i["id"]: i for i in integrations.describe({"hunter": {"mode": "api", "values": {"api_key": "hk_secret_1234"}}})}
    assert shown["hunter"]["values"]["api_key"] == "••••1234" and shown["hunter"]["status"] == "configured"


def setup_client(store, get=None, post=None):
    app = FastAPI()
    app.include_router(make_setup_router(store, get_transport=get or FakeGet(), firecrawl_transport=post or FakePost()))
    return TestClient(app)


def test_setup_tests_for_hunter(tmp_path):
    store = seeded_store(tmp_path)
    assert setup_client(store).post("/api/integrations/hunter/test", json={}).json()["ok"] is False   # not connected
    connect(store, "hunter", {"api_key": "hk"})
    get = FakeGet({"data": {"domain": "hunter.io", "emails": []}})
    assert setup_client(store, get=get).post("/api/integrations/hunter/test", json={}).json()["ok"] is True
    assert "domain=hunter.io" in get.calls[0]
    bad = setup_client(store, get=FakeGet(error=PermissionError("401"))).post("/api/integrations/hunter/test", json={}).json()
    assert bad["ok"] is False and "rejected" in bad["message"]


def test_setup_tests_for_firecrawl_and_contact_scraper(tmp_path):
    store = seeded_store(tmp_path)
    assert setup_client(store).post("/api/integrations/firecrawl/test", json={}).json()["ok"] is False
    connect(store, "firecrawl", {"base_url": "http://localhost:3002"})
    post = FakePost(by_url={"https://example.com": "<h1>Example Domain</h1>"})
    assert setup_client(store, post=post).post("/api/integrations/firecrawl/test", json={}).json()["ok"] is True
    assert post.calls[0]["url"] == "http://localhost:3002/v1/scrape" and post.calls[0]["headers"] == {}
    down = setup_client(store, post=FakePost(error=OSError("refused"))).post("/api/integrations/firecrawl/test", json={}).json()
    assert down["ok"] is False and "localhost:3002" in down["message"]
    manual = setup_client(store).post("/api/integrations/contact_scraper/test", json={}).json()
    assert manual["ok"] is True and "Data sources" in manual["message"]
