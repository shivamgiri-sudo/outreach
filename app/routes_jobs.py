"""Job ingestion route: turns openings into hiring-volume and hiring-pain signals on leads."""
import csv
import io
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .jobs import job_age_days, job_domain, job_key, parse_job_postings
from .job_discover import discover_jobs
from .scraper import fetch_page, normalise_url

MAX_ROLE_TITLES = 2


# Only tests may set the clock (OUTREACH_TEST_MODE=1).
ALLOW_NOW_OVERRIDE = os.environ.get("OUTREACH_TEST_MODE") == "1"


class IngestBody(BaseModel):
    html: str = ""
    csv: str = ""
    url: str = ""
    now: Optional[str] = None


def _csv_jobs(text: str) -> list:
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        if row.get("company") and row.get("title"):
            rows.append({"title": row["title"], "company": row["company"], "company_site": row.get("website", ""),
                         "location": row.get("location", ""), "date_posted": row.get("date_posted", ""), "url": row.get("url", "")})
    return rows


def _upsert_jobs(existing: list, found: list, page_url: str, now: datetime) -> tuple:
    by_key = {j["key"]: j for j in existing}
    new = reposts = 0
    for job in found:
        domain = job_domain(job, page_url)
        key = job_key(job, domain)
        record = {**job, "domain": domain, "key": key}
        old = by_key.get(key)
        if old is None:
            by_key[key] = {**record, "first_seen": now.isoformat(), "repost_count": 0}
            new += 1
        elif job["date_posted"] and job["date_posted"] != old["date_posted"]:
            by_key[key] = {**old, "date_posted": job["date_posted"], "repost_count": old["repost_count"] + 1}
            reposts += 1
    return list(by_key.values()), new, reposts


def _signals(jobs: list, now: datetime) -> dict:
    titles = list(dict.fromkeys(j["title"] for j in jobs))[:MAX_ROLE_TITLES]
    return {
        "open_roles": len(jobs),
        "max_job_age_days": max((job_age_days(j["date_posted"], now) for j in jobs), default=0),
        "repost_count": sum(j["repost_count"] for j in jobs),
        "role": " and ".join(titles),
    }


def make_router(store, build_lead, present) -> APIRouter:
    router = APIRouter()

    @router.get("/api/jobs")
    def list_jobs():
        return store.read("jobs")

    @router.post("/api/jobs/ingest")
    def ingest(body: IngestBody):
        body.url = normalise_url(body.url)
        if not (body.html or body.csv or body.url):
            raise HTTPException(400, "Give a page URL, page HTML, or a CSV of jobs")
        now = datetime.now(timezone.utc)
        if body.now and ALLOW_NOW_OVERRIDE:          # only tests may set the clock
            now = datetime.fromisoformat(body.now)
            now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        try:
            note = ""
            if body.csv:
                found = _csv_jobs(body.csv)
            elif body.html:
                found = parse_job_postings(body.html, body.url)
            else:
                found, note = discover_jobs(body.url, fetch_page)
        except (ValueError, PermissionError, OSError) as exc:
            raise HTTPException(400, str(exc))

        jobs, new, reposts = _upsert_jobs(store.read("jobs"), found, body.url, now)
        store.write("jobs", jobs)   # jobs are only written here

        stats = {"created": 0, "updated": 0}
        touched = {job_key(f, job_domain(f, body.url)) for f in found}

        def merge_in(leads: list) -> list:
            for company, domain in dict.fromkeys((j["company"], j["domain"]) for j in jobs if j["key"] in touched):
                mine = [j for j in jobs if j["company"].lower() == company.lower() or (domain and j["domain"] == domain)]
                signals = _signals(mine, now)
                idx = next((i for i, lead in enumerate(leads)
                            if (domain and lead.get("domain") == domain) or (lead.get("company") or "").lower() == company.lower()), None)
                if idx is None:
                    lead = build_lead({"company": company, "website": f"https://{domain}" if domain else ""},
                                      f"job_page:{body.url or 'csv'}", allow_company_key=True)
                    leads.append({**lead, **signals})
                    stats["created"] += 1
                else:
                    lead = leads[idx]
                    event = {"at": now.isoformat(timespec="seconds"), "kind": "jobs",
                             "text": f"{signals['open_roles']} open role(s), oldest {signals['max_job_age_days']} days, {signals['repost_count']} repost(s)"}
                    leads[idx] = {**lead, **signals, "role": lead.get("role") or signals["role"], "timeline": [*lead["timeline"], event]}
                    stats["updated"] += 1
            return leads
        store.update("leads", merge_in)
        created, updated = stats["created"], stats["updated"]
        return {"jobs_found": len(found), "jobs_new": new, "reposts": reposts, "leads_created": created, "leads_updated": updated,
                "note": note}

    return router
