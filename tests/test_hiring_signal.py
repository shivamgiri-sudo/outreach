"""Hiring now + company facts from a lead's own website (app/hiring_signal.py, app/site_markup.py). No network: pages
come from a dict; robots.txt is checked through the real scraper.fetch_page with its low-level getter faked."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import hiring_signal as hs, routes_hiring_signal, scraper, site_markup
from app.channels.netguard import FetchFailed
from app.main import create_app

NOW = datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc)
ORG = {"@type": "Organization", "name": "Acme Services", "logo": {"@type": "ImageObject", "url": "/img/logo.png"},
       "telephone": "+91 98765 43210",
       "address": {"@type": "PostalAddress", "streetAddress": "12 MG Road", "addressLocality": "Pune", "postalCode": "411001", "addressCountry": "IN"},
       "sameAs": ["https://www.linkedin.com/company/acme-services/", "https://twitter.com/acme", "https://www.facebook.com/acme",
                  "https://instagram.com/acme", "https://www.youtube.com/@acme"]}


def ld(data) -> str:
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


def posting(title, place="Pune", posted="2026-11-01"):
    return {"@type": "JobPosting", "title": title, "datePosted": posted, "hiringOrganization": {"@type": "Organization", "name": "Acme"},
            "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": place}}}


HOME = ("<html><head><meta property=\"og:site_name\" content=\"Acme\">" + ld({"@context": "https://schema.org", "@graph": [ORG]})
        + '</head><body><a href="/careers">Careers</a></body></html>')
CAREERS = (ld([posting("Customer Support Executive"), posting("Team Leader", "Noida", "2026-11-10")])
           + "<script type=\"application/ld+json\">{not json</script>"
           + ld({"@graph": [posting("Customer Support Executive"), posting("Quality Analyst", "Pune", "2026-10-20T09:00:00+05:30")]}))


class Site:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, url, **kw):
        self.calls.append(url)
        if url in self.pages:
            return self.pages[url]
        raise FetchFailed("That page was not found (HTTP 404). Check the address.")


def acme():
    return Site({"https://acme.com/": HOME, "https://acme.com/careers": CAREERS})


def test_jobs_from_graph_lists_and_malformed_blocks():
    out = hs.check_domain("acme.com", acme(), NOW)
    h = out["hiring"]
    assert h["open_roles"] == 3                                     # the repeated posting counts once
    assert h["titles"] == ["Customer Support Executive", "Team Leader", "Quality Analyst"]
    assert h["locations"] == ["Pune", "Noida"] and h["latest_posted"] == "2026-11-10"
    assert h["sources"] == ["https://acme.com/careers"] and h["checked_at"] == NOW.isoformat(timespec="seconds")
    assert out["error"] == ""


def test_company_facts_from_markup():
    f = site_markup.facts(HOME, "https://acme.com/")
    assert f["name"] == "Acme Services" and f["logo_url"] == "https://acme.com/img/logo.png" and f["city"] == "Pune"
    assert f["address"] == "12 MG Road, Pune, 411001, IN" and f["phone"] == "+91 98765 43210"
    assert f["company_linkedin_url"] == "https://www.linkedin.com/company/acme-services"
    assert set(f["social"]) == {"x", "facebook", "instagram", "youtube"}
    meta_only = site_markup.facts('<meta property="og:site_name" content="Beta &amp; Co"><meta name="twitter:site" content="@beta">', "https://b.in/")
    assert meta_only == {"name": "Beta & Co", "social": {"x": "https://x.com/beta"}}
    assert site_markup.facts("<script type=\"application/ld+json\">{oops</script>", "https://c.in/") == {}


def test_only_empty_fields_are_filled():
    found = site_markup.facts(HOME, "https://acme.com/")
    lead = {"company": "ACME (own name)", "city": "", "phone": "", "social": {"x": "https://x.com/mine"}, "provenance": {"email": "apollo"}}
    changes, filled = site_markup.changes_for(lead, found)
    assert "company" not in changes and changes["city"] == "Pune" and changes["phone"] == "+919876543210"
    assert changes["social"]["x"] == "https://x.com/mine" and changes["social"]["facebook"] == "https://www.facebook.com/acme"
    assert "social.x" not in filled and changes["provenance"]["city"] == "site-markup" and changes["provenance"]["email"] == "apollo"


def test_job_boards_and_login_pages_are_never_followed():
    html = ('<a href="https://www.indeed.com/cmp/acme">x</a><a href="https://www.linkedin.com/company/acme/jobs">y</a>'
            '<a href="https://acme.wd3.myworkdayjobs.com/login">z</a><a href="https://jobs.lever.co/acme">Lever</a>')
    assert hs.ats_links(html, "https://acme.com/") == ["https://jobs.lever.co/acme"]
    pages = hs.Pages(Site({}))
    with pytest.raises(PermissionError):
        pages("https://www.glassdoor.com/acme")


def test_hosted_job_board_link_is_followed_when_the_site_has_no_jobs():
    site = Site({"https://acme.com/": '<a href="https://apply.workable.com/acme/">Open roles</a>',
                 "https://apply.workable.com/acme/": ld(posting("Sales Executive"))})
    out = hs.check_domain("acme.com", site, NOW)
    assert out["hiring"]["open_roles"] == 1 and "https://apply.workable.com/acme/" in site.calls


def test_page_budget_per_domain():
    site = Site({})
    pages = hs.Pages(site, limit=2)
    for u in ("https://a.com/1", "https://a.com/2"):
        with pytest.raises(FetchFailed):
            pages(u)
    with pytest.raises(hs.PageBudget):
        pages("https://a.com/3")


def test_robots_disallow_is_respected(monkeypatch):
    got = []

    def fake_get(url, resolver=None, max_bytes=0):
        got.append(url)
        if url.endswith("/robots.txt"):
            return "User-agent: *\nDisallow: /careers\n"
        return {"https://acme.com/": HOME, "https://acme.com/careers": CAREERS}.get(url) or (_ for _ in ()).throw(FetchFailed("404"))
    monkeypatch.setattr(scraper, "_get", fake_get)
    out = hs.check_domain("acme.com", scraper.fetch_page, NOW)
    assert "https://acme.com/careers" not in got and out["hiring"]["open_roles"] == 0
    assert out["facts"]["name"] == "Acme Services"                 # the home page was allowed and read
    monkeypatch.setattr(scraper, "_get", lambda url, resolver=None, max_bytes=0: "User-agent: *\nDisallow: /\n")
    refused = hs.check_domain("acme.com", scraper.fetch_page, NOW)
    assert "robots" in refused["error"] and refused["hiring"]["open_roles"] == 0


def test_cache_window(tmp_path):
    from app.store import Store
    store = Store(tmp_path / "s.json")
    site = acme()
    first = hs.lookup(store, "acme.com", site, NOW)
    assert not first["from_cache"] and len(site.calls) >= 2
    n = len(site.calls)
    assert hs.lookup(store, "acme.com", site, NOW + timedelta(days=6))["from_cache"] and len(site.calls) == n
    assert not hs.lookup(store, "acme.com", site, NOW + timedelta(days=8))["from_cache"] and len(site.calls) > n


def test_pick_skips_recently_checked_and_closed_leads():
    since = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
    leads = [{"id": "1", "domain": "a.com"}, {"id": "2", "domain": "a.com"}, {"id": "3", "domain": "b.com", "hiring_signal": {"checked_at": since}},
             {"id": "4", "domain": "c.com", "stage": "unsubscribed"}, {"id": "5", "domain": ""}, {"id": "6", "domain": "d.com"}]
    assert hs.pick(leads, None, 50) == [("a.com", ["1", "2"]), ("d.com", ["6"])]
    assert len(hs.pick([{"id": str(i), "domain": f"x{i}.com"} for i in range(80)], None, 500)) == hs.BATCH_MAX


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setattr(routes_hiring_signal, "BACKGROUND", False)
    c = TestClient(create_app(data_file=tmp_path / "d.json", admin_password="admin-pass-123", fetcher=acme()), client=("203.0.113.9", 50000))
    ho = {"Authorization": "Bearer " + c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]}
    c.post("/api/leads/import", json={"csv": "company,email,website,city\nAcme,ravi@acme.com,https://acme.com,Mumbai\n", "source": "manual"}, headers=ho)
    return c, ho


def test_api_refresh_one_lead_and_batch(app_client):
    c, ho = app_client
    lead = c.get("/api/leads", headers=ho).json()[0]
    r = c.post(f"/api/leads/{lead['id']}/hiring-signal", headers=ho)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hiring_signal"]["open_roles"] == 3 and "logo_url" in body["filled"] and "city" not in body["filled"]
    saved = c.get(f"/api/leads/{lead['id']}", headers=ho).json()
    assert saved["open_roles"] == 3 and saved["city"] == "Mumbai" and saved["company"] == "Acme"
    assert saved["provenance"]["logo_url"] == "site-markup" and saved["company_facts"]["source"] == "site-markup"
    assert c.post("/api/leads/nope/hiring-signal", headers=ho).status_code == 404
    batch = c.post("/api/hiring-signal/run", json={"limit": 50}, headers=ho).json()
    assert batch["total"] == 0                                       # checked a moment ago: nothing to do
    assert c.post("/api/hiring-signal/run", json={"limit": 51}, headers=ho).status_code == 422


def test_api_batch_runs_bounded_and_records_unreadable_sites(app_client):
    c, ho = app_client
    c.post("/api/leads/import", json={"csv": "company,email,website\nBeta,sita@beta.in,https://beta.in\n", "source": "manual"}, headers=ho)
    state = c.post("/api/hiring-signal/run", json={"limit": 50}, headers=ho).json()
    assert state["status"] == "done" and state["total"] == 2 and state["hiring"] == 1 and state["errors"] == 1
    assert c.get("/api/hiring-signal/run", headers=ho).json()["done"] == 2
    beta = next(l for l in c.get("/api/leads", headers=ho).json() if l["company"] == "Beta")
    assert beta["hiring_signal"]["open_roles"] == 0 and beta["hiring_signal"]["checked_at"]
