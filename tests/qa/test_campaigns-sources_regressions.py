"""Regression tests for bugs found by the campaigns & sources QA pass (inline fictional data, fake transports only)."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import campaign_pipeline as pipe
from app import campaign_sources as src
from app.jobs import job_domain, parse_job_postings
from app.store import Store

NOW = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)


def posting_page(company: str, title: str, same_as=None) -> str:
    org = {"@type": "Organization", "name": company, **({"sameAs": same_as} if same_as is not None else {})}
    node = {"@context": "https://schema.org", "@type": "JobPosting", "title": title, "datePosted": "2026-10-20",
            "hiringOrganization": org, "jobLocation": {"@type": "Place", "address": {"addressLocality": "Pune"}}}
    return f'<script type="application/ld+json">{json.dumps(node)}</script>'


# ---------- careers pages: shared hosts and messy JSON-LD ----------
def test_companies_on_a_shared_ats_host_or_linkedin_sameas_are_not_merged(tmp_path):
    """Two employers on jobs.lever.co (or both with a LinkedIn sameAs) used to share one job key and collapse into one."""
    pages = [("https://jobs.lever.co/alpha-example/1", posting_page("Alpha Care", "Telecaller")),
             ("https://jobs.lever.co/beta-example/2", posting_page("Beta Diagnostics", "Telecaller")),
             ("", posting_page("Gamma Motors", "Telecaller", "https://www.linkedin.com/company/gamma-example")),
             ("", posting_page("Delta Textiles", "Telecaller", "https://www.linkedin.com/company/delta-example"))]
    found = [(p, url) for url, html in pages for p in parse_job_postings(html, url)]
    rows = pipe.careers_rows(Store(tmp_path / "s.json"), found, NOW, dry_run=True)
    assert sorted(r["company"] for r in rows) == ["Alpha Care", "Beta Diagnostics", "Delta Textiles", "Gamma Motors"]
    assert all(r["domain"] == "" and r["open_roles"] == 1 for r in rows)
    workday = parse_job_postings(posting_page("IN02 Sigma Fintech", "Telecaller", ""), "")[0]
    assert job_domain(workday, "https://sigma.wd5.myworkdayjobs.com/External/job/1") == ""


def test_sameas_list_and_raw_line_breaks_in_json_ld():
    listed = parse_job_postings(posting_page("Lotus Hospitality", "Front Office Executive",
                                             ["https://www.facebook.com/lotus-example", "https://lotus-hospitality.example"]), "")
    assert job_domain(listed[0], "") == "lotus-hospitality.example"            # was: AttributeError on a list
    raw = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "Warehouse Supervisor",\n'
           '"description": "Line one.\nLine two\twith a tab", "hiringOrganization": {"name": "Banyan Logistics"}}</script>')
    assert [p["title"] for p in parse_job_postings(raw, "https://banyan.example/careers")] == ["Warehouse Supervisor"]


# ---------- email verifier outage ----------
def test_verifier_outage_is_not_a_risky_verdict():
    """NeverBounce returns HTTP 200 + status temp_unavail in an outage; it was stored as 'risky' and blocked the lead for good."""
    nb = {"provider": "neverbounce", "api_key": "nk", "supported": True}
    zb = {"provider": "zerobounce", "api_key": "zk", "supported": True}
    temp = {"status": "temp_unavail", "message": "A temporary service interruption occurred", "execution_time": 12}
    with pytest.raises(OSError):
        src.verify_email("meera@gamma-care.example", nb, lambda url, headers: temp)
    with pytest.raises(OSError):
        src.verify_email("meera@gamma-care.example", zb, lambda url, headers: {})
    assert src.verify_email("meera@gamma-care.example", nb, lambda url, headers: {"status": "success", "result": "catchall"}) == "risky"
