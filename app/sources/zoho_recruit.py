"""Zoho Recruit careers sites. Zoho publishes no unauthenticated JSON jobs API, so this reads the company's own public
careers page (https://{org}.zohorecruit.in/jobs/Careers, or .com) through the polite robots.txt-respecting fetcher and
parses schema.org JobPosting data, exactly like the generic careers-page source. Docs: https://help.zoho.com/portal/en/kb/recruit
If a page has no JobPosting data the company is reported as 'no listings found', never guessed."""
import re

from ..jobs import parse_job_postings
from .ats_common import SLUG_STEP, SLUGS_FIELD, discover_boards, test_first_board
from .base import SourceSpec, pretty_name

SUB_RE = re.compile(r"^[a-z0-9-]{1,60}$")
HOSTS = ("zohorecruit.in", "zohorecruit.com")


def fetch_board(ctx, slug: str) -> dict:
    sub = slug.lower()
    if not SUB_RE.match(sub):
        raise ValueError("not a Zoho Recruit account name")
    if ctx.fetch is None:
        raise ValueError("page fetcher not available")
    last = None
    for host in HOSTS:
        url = f"https://{sub}.{host}/jobs/Careers"
        try:
            html = ctx.fetch(url)
        except (OSError, ValueError) as exc:         # PermissionError (robots.txt) is an OSError subclass
            last = exc
            continue
        postings = parse_job_postings(html, url)
        if postings:
            return {"name": postings[0]["company"] or pretty_name(sub),
                    "jobs": [{"title": p["title"], "location": p["location"], "posted": p["date_posted"], "url": url} for p in postings]}
    raise OSError(str(last) if last else "no listings found")


def discover(ctx, target, limit):
    return discover_boards(ctx, target, limit, fetch_board, "zoho_recruit", lambda s: f"https://{s.lower()}.zohorecruit.in/jobs/Careers")


SPEC = SourceSpec(
    "zoho_recruit", "Zoho Recruit careers pages", ("discover_companies", "hiring_signal"), "Company discovery",
    "Open roles at companies whose careers page runs on Zoho Recruit (read from the page's JobPosting data).",
    "https://help.zoho.com/portal/en/kb/recruit",
    "No API: reads the company's public careers page with the polite fetcher (robots.txt respected, rate limited). A page that blocks crawlers is skipped.",
    [SLUG_STEP.format(example="acme.zohorecruit.in"), "Paste only the part before .zohorecruit.in. No key is needed."],
    fields=[SLUGS_FIELD], keyless=True, rate_per_min=20, free_note="Free, no key.", cost_note="Free. 1-2 page fetches per company.",
    honest_status="coded from documentation; the page format is not guaranteed and was not run against a live careers page",
    discover=discover, test=lambda ctx: test_first_board(ctx, fetch_board, "Zoho Recruit"))
