from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.jobs import job_age_days, parse_job_postings
from app.main import create_app

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)

PAGE = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting","title":"Telecaller",
 "datePosted":"2026-10-01","hiringOrganization":{"@type":"Organization","name":"Acme Retail","sameAs":"https://acme.com"},
 "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Pune","addressCountry":"IN"}}}
</script>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"JobPosting","title":"Store Manager","datePosted":"2026-10-28T09:00:00+05:30",
  "hiringOrganization":{"name":"Acme Retail"},"jobLocation":[{"address":{"addressLocality":"Mumbai"}}]},
 {"@type":"Organization","name":"Acme Retail"}]}
</script>
<script type="application/ld+json">not json at all</script>
</head><body></body></html>
"""


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(data_file=tmp_path / "d.json"))


def test_parse_finds_postings_in_plain_and_graph_jsonld():
    jobs = parse_job_postings(PAGE, "https://acme.com/careers")
    assert {j["title"] for j in jobs} == {"Telecaller", "Store Manager"}
    tele = next(j for j in jobs if j["title"] == "Telecaller")
    assert tele["company"] == "Acme Retail" and tele["location"] == "Pune"
    assert tele["date_posted"] == "2026-10-01"
    assert tele["url"] == "https://acme.com/careers"


def test_parse_ignores_pages_without_jobs():
    assert parse_job_postings("<html>nothing</html>", "https://x.com") == []


def test_job_age_days():
    assert job_age_days("2026-10-01", NOW) == 33
    assert job_age_days("2026-10-28T09:00:00+05:30", NOW) == 5
    assert job_age_days("garbage", NOW) == 0
    assert job_age_days("", NOW) == 0


def test_ingest_creates_lead_with_hiring_signals(client):
    r = client.post("/api/jobs/ingest", json={"html": PAGE, "url": "https://acme.com/careers", "now": NOW.isoformat()}).json()
    assert r["jobs_found"] == 2 and r["leads_created"] == 1
    lead = client.get("/api/leads").json()[0]
    assert lead["company"] == "Acme Retail" and lead["domain"] == "acme.com"
    assert lead["open_roles"] == 2 and lead["max_job_age_days"] == 33 and lead["repost_count"] == 0
    assert lead["source"].startswith("job_page")
    assert lead["signals"]["hiring_volume"] > 0 and lead["signals"]["hiring_pain"] > 0


def test_second_ingest_does_not_duplicate_and_counts_repost(client):
    body = {"html": PAGE, "url": "https://acme.com/careers", "now": NOW.isoformat()}
    client.post("/api/jobs/ingest", json=body)
    again = client.post("/api/jobs/ingest", json=body).json()
    assert again["leads_created"] == 0 and again["jobs_new"] == 0
    reposted = PAGE.replace("2026-10-01", "2026-11-01")
    r = client.post("/api/jobs/ingest", json={**body, "html": reposted}).json()
    assert r["reposts"] == 1
    lead = client.get("/api/leads").json()[0]
    assert lead["repost_count"] == 1 and lead["open_roles"] == 2
    assert len(client.get("/api/jobs").json()) == 2


def test_ingest_attaches_to_existing_lead_by_domain(client):
    client.post("/api/leads/import", json={"csv": "company,email,website\nAcme Retail,ravi@acme.com,https://acme.com\n", "source": "apollo"})
    r = client.post("/api/jobs/ingest", json={"html": PAGE, "url": "https://acme.com/careers", "now": NOW.isoformat()}).json()
    assert r["leads_created"] == 0 and r["leads_updated"] == 1
    leads = client.get("/api/leads").json()
    assert len(leads) == 1 and leads[0]["open_roles"] == 2 and leads[0]["email"] == "ravi@acme.com"


def test_ingest_csv_of_jobs(client):
    csv_text = "company,title,location,date_posted,website\nBeta Logistics,Driver,Delhi,2026-09-01,https://beta.in\nBeta Logistics,Driver,Delhi,2026-10-20,https://beta.in\n"
    r = client.post("/api/jobs/ingest", json={"csv": csv_text, "now": NOW.isoformat()}).json()
    assert r["jobs_found"] == 2 and r["reposts"] == 1
    lead = client.get("/api/leads").json()[0]
    assert lead["company"] == "Beta Logistics" and lead["open_roles"] == 1 and lead["repost_count"] == 1


def test_ingest_requires_some_input(client):
    assert client.post("/api/jobs/ingest", json={}).status_code == 400
