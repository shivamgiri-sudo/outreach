"""Ashby public job posting API (no key): https://developers.ashbyhq.com/docs/public-job-posting-api
GET https://api.ashbyhq.com/posting-api/job-board/{JOB_BOARD_NAME} -> {"jobs": [{title, location, isListed, publishedAt, jobUrl}]}."""
from .ats_common import SLUG_STEP, SLUGS_FIELD, discover_boards, test_first_board
from .base import SourceSpec, pretty_name


def fetch_board(ctx, slug: str) -> dict:
    reply = ctx.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "false"})
    jobs = [{"title": j.get("title", ""), "location": j.get("location", "") or ", ".join(
                s.get("location", "") for s in j.get("secondaryLocations") or [] if isinstance(s, dict)),
             "posted": j.get("publishedAt", ""), "url": j.get("jobUrl", "")}
            for j in reply.get("jobs") or [] if isinstance(j, dict) and j.get("isListed", True)]
    return {"name": pretty_name(slug), "jobs": jobs}


def discover(ctx, target, limit):
    return discover_boards(ctx, target, limit, fetch_board, "ashby", lambda s: f"https://jobs.ashbyhq.com/{s}")


SPEC = SourceSpec(
    "ashby", "Ashby job boards", ("discover_companies", "hiring_signal"), "Company discovery",
    "Open roles at companies that publish an Ashby jobs page.", "https://developers.ashbyhq.com/docs/public-job-posting-api",
    "Public Job Posting API for careers pages. No key. Compensation data is not requested.",
    [SLUG_STEP.format(example="jobs.ashbyhq.com/acme"), "Paste and save. No key is needed."],
    fields=[SLUGS_FIELD], keyless=True, rate_per_min=60, free_note="Free, no key.", cost_note="Free. 1 request per company.",
    discover=discover, test=lambda ctx: test_first_board(ctx, fetch_board, "Ashby"))
