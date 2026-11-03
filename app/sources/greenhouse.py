"""Greenhouse Job Board API (public, no key): https://developers.greenhouse.io/job-board.html (now docs.greenhouse.io/job-board.html).
GET boards-api.greenhouse.io/v1/boards/{board_token}/jobs -> {"jobs": [{title, updated_at, location: {name}, absolute_url}]};
GET /v1/boards/{board_token} -> {"name": ...}. Both are meant for programmatic use by career sites."""
from .ats_common import SLUG_STEP, SLUGS_FIELD, discover_boards, test_first_board
from .base import SourceSpec

BASE = "https://boards-api.greenhouse.io/v1/boards/"


def fetch_board(ctx, slug: str) -> dict:
    jobs = ctx.get(f"{BASE}{slug}/jobs").get("jobs") or []
    name = ctx.get(f"{BASE}{slug}").get("name") or ""
    return {"name": name, "jobs": [{"title": j.get("title", ""), "location": (j.get("location") or {}).get("name", ""),
                                    "posted": j.get("updated_at", ""), "url": j.get("absolute_url", "")} for j in jobs if isinstance(j, dict)]}


def discover(ctx, target, limit):
    return discover_boards(ctx, target, limit, fetch_board, "greenhouse", lambda s: f"https://boards.greenhouse.io/{s}")


SPEC = SourceSpec(
    "greenhouse", "Greenhouse job boards", ("discover_companies", "hiring_signal"), "Company discovery",
    "Open roles at companies that publish a Greenhouse job board. A company with open roles is hiring.",
    "https://docs.greenhouse.io/job-board.html",
    "Public Job Board API published for career sites. No key. Use only boards of companies you target; follow Greenhouse's terms and keep request volume low.",
    [SLUG_STEP.format(example="boards.greenhouse.io/acme"), "Paste the identifiers here and save. No account or key is needed."],
    fields=[SLUGS_FIELD], keyless=True, rate_per_min=60, free_note="Free, no key.", cost_note="Free. 2 requests per company.",
    discover=discover, test=lambda ctx: test_first_board(ctx, fetch_board, "Greenhouse"))
