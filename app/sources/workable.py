"""Workable public widget feed (no key). Workable's widget endpoint for career pages:
GET https://apply.workable.com/api/v1/widget/accounts/{subdomain} -> {name, jobs: [{title, city, state, country,
published_on, url}]}. Workable's documented API (https://workable.readme.io/) needs the company's own token, so this
uses only the widget feed that Workable publishes for embedding careers pages. Coded from documentation and public
examples; confirm with one live call."""
import re

from .ats_common import SLUG_STEP, SLUGS_FIELD, discover_boards, test_first_board
from .base import SourceSpec, pretty_name

SUB_RE = re.compile(r"^[A-Za-z0-9-]{1,60}$")


def fetch_board(ctx, slug: str) -> dict:
    if not SUB_RE.match(slug):
        raise ValueError("not a Workable account name")
    reply = ctx.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    jobs = [{"title": j.get("title", ""), "location": ", ".join(p for p in (j.get("city"), j.get("state"), j.get("country")) if p),
             "posted": j.get("published_on") or j.get("created_at") or "", "url": j.get("url") or j.get("shortlink") or ""}
            for j in reply.get("jobs") or [] if isinstance(j, dict)]
    return {"name": reply.get("name") or pretty_name(slug), "jobs": jobs}


def discover(ctx, target, limit):
    return discover_boards(ctx, target, limit, fetch_board, "workable", lambda s: f"https://apply.workable.com/{s}/")


SPEC = SourceSpec(
    "workable", "Workable job boards", ("discover_companies", "hiring_signal"), "Company discovery",
    "Open roles at companies that publish a Workable careers page.", "https://workable.readme.io/",
    "Uses the public widget feed Workable provides for careers pages; no key. Workable's own API needs the employer's token and is not used.",
    [SLUG_STEP.format(example="apply.workable.com/acme"), "Paste and save. No key is needed."],
    fields=[SLUGS_FIELD], keyless=True, rate_per_min=30, free_note="Free, no key.", cost_note="Free. 1 request per company.",
    discover=discover, test=lambda ctx: test_first_board(ctx, fetch_board, "Workable"))
