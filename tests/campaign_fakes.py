"""Fakes and helpers for the campaign tests. Every name, domain and number is made up."""
import json

from fastapi.testclient import TestClient

from app.main import create_app

PERSON = {"id": "p-asha", "first_name": "Asha", "last_name": "Rao", "name": "Asha Rao", "title": "HR Head",
          "email": "asha@acme-retail.in", "linkedin_url": "https://www.linkedin.com/in/asha-example", "city": "Pune",
          "country": "India", "organization": {"id": "o-acme", "name": "Acme Retail Pvt Ltd", "website_url": "https://acme-retail.in",
                                               "primary_domain": "acme-retail.in", "estimated_num_employees": 250}}
LOCKED = {"id": "p-ravi", "first_name": "Ravi", "last_name_obfuscated": "Ku***r", "title": "Talent Acquisition Manager",
          "email": "email_not_unlocked@domain.com", "linkedin_url": "javascript:alert(1)",
          "organization": {"id": "o-beta", "name": "Beta Logistics", "website_url": "https://www.facebook.com/beta-logistics"}}


def job_html(company: str, site: str, titles: list, posted: str = "2026-09-20", city: str = "Pune") -> str:
    items = [{"@context": "https://schema.org", "@type": "JobPosting", "title": t, "datePosted": posted,
              "hiringOrganization": {"@type": "Organization", "name": company, "sameAs": site},
              "jobLocation": {"address": {"addressLocality": city}}} for t in titles]
    return f'<html><script type="application/ld+json">{json.dumps(items)}</script></html>'


ACME_JOBS = job_html("Acme Retail", "https://acme-retail.in", [f"Customer Support Executive {i}" for i in range(10)])
ZETA_JOBS = job_html("Zeta Foods", "https://zeta-foods.in", ["Sales Executive", "Telecaller"])


class FakeIO:
    """post(url, headers, body), get(url, headers) and fetch(url), answering by the first matching URL part."""

    def __init__(self, post=None, get=None, pages=None):
        self.post_routes, self.get_routes, self.pages = post or {}, get or {}, pages or {}
        self.calls = []

    @staticmethod
    def _answer(routes: dict, url: str, arg):
        for part, reply in routes.items():
            if part in url:
                if isinstance(reply, Exception):
                    raise reply
                return reply(arg) if callable(reply) else reply
        return {}

    def post(self, url, headers, body):
        self.calls.append({"kind": "post", "url": url, "headers": dict(headers or {}), "body": body})
        return self._answer(self.post_routes, url, body)

    def get(self, url, headers=None):
        self.calls.append({"kind": "get", "url": url, "headers": dict(headers or {})})
        return self._answer(self.get_routes, url, url)

    def fetch(self, url):
        self.calls.append({"kind": "fetch", "url": url})
        if url in self.pages:
            return self.pages[url]
        raise OSError("unreachable in tests")

    def transports(self) -> dict:
        return {"post": self.post, "get": self.get, "fetch": self.fetch}

    def urls(self, part: str = "") -> list:
        return [c["url"] for c in self.calls if part in c["url"]]


def client(tmp_path, io: FakeIO, **kwargs) -> TestClient:
    return TestClient(create_app(data_file=tmp_path / "store.json", campaign_transports=io.transports(), **kwargs))


def connect(c, integration: str, values: dict) -> None:
    assert c.put(f"/api/integrations/{integration}", json={"mode": "api", "values": values}).status_code == 200


def campaign_body(name: str = "Pune support hiring", **parts) -> dict:
    body = {"name": name,
            "target": {"industries": ["BPO"], "cities": ["Pune"], "roles_hiring": ["customer support"],
                       "company_size_buckets": ["51-200"], "keywords": []},
            "sources": {"careers_pages": [], "careers_html": [], "apollo_search": False, "apollo_hiring": False,
                        "google_places": False, "csv_import_ids": [], "max_results": 25},
            "enrich": {"crawler": False, "hunter": False, "firecrawl": False, "apollo_match": False},
            "rules": {"min_score": 40, "require_business_email": True, "skip_role_addresses": False,
                      "max_new_leads_per_run": 50, "auto_enrol": False, "caller": ""}}
    for part, values in parts.items():
        body[part] = {**body[part], **values}
    return body


def create(c, **parts) -> dict:
    r = c.post("/api/campaigns", json=campaign_body(**parts))
    assert r.status_code == 200, r.text
    return r.json()


def run(c, campaign_id: str, dry_run: bool = False, now: str = "2026-11-03T05:00:00+00:00") -> dict:
    r = c.post(f"/api/campaigns/{campaign_id}/run", json={"dry_run": dry_run, "now": now})
    assert r.status_code == 200, r.text
    return r.json()


def by_company(c, name: str) -> dict:
    return next(l for l in c.get("/api/leads").json() if l["company"] == name)
