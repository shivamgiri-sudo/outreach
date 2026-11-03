"""Recruitee Careers Site API (public, no key): GET https://{company}.recruitee.com/api/offers/ ->
{"offers": [{title, city, country, created_at, careers_url, status}]}. This is the feed Recruitee's own careers sites use
(Recruitee's authenticated Company API, https://docs.recruitee.com/reference/offers-get, needs a token and is not used).
Coded from public documentation of the careers-site API; confirm with one live call."""
import re

from .ats_common import SLUG_STEP, SLUGS_FIELD, discover_boards, test_first_board
from .base import SourceSpec, pretty_name

SUB_RE = re.compile(r"^[a-z0-9-]{1,60}$")


def fetch_board(ctx, slug: str) -> dict:
    sub = slug.lower()
    if not SUB_RE.match(sub):
        raise ValueError("not a Recruitee account name")
    reply = ctx.get(f"https://{sub}.recruitee.com/api/offers/")
    jobs = [{"title": o.get("title", ""), "location": ", ".join(p for p in (o.get("city"), o.get("country")) if p),
             "posted": o.get("created_at") or o.get("published_at") or "", "url": o.get("careers_url", "")}
            for o in reply.get("offers") or [] if isinstance(o, dict) and o.get("status", "published") == "published"]
    return {"name": pretty_name(sub), "jobs": jobs}


def discover(ctx, target, limit):
    return discover_boards(ctx, target, limit, fetch_board, "recruitee", lambda s: f"https://{s.lower()}.recruitee.com/")


SPEC = SourceSpec(
    "recruitee", "Recruitee careers sites", ("discover_companies", "hiring_signal"), "Company discovery",
    "Open roles at companies whose careers site runs on Recruitee.", "https://docs.recruitee.com/reference/offers-get",
    "Reads the same public offers feed a Recruitee careers site loads. No key. Only letters, digits and dashes are accepted as the account name.",
    [SLUG_STEP.format(example="acme.recruitee.com"), "Paste only the part before .recruitee.com. No key is needed."],
    fields=[SLUGS_FIELD], keyless=True, rate_per_min=60, free_note="Free, no key.", cost_note="Free. 1 request per company.",
    discover=discover, test=lambda ctx: test_first_board(ctx, fetch_board, "Recruitee"))
