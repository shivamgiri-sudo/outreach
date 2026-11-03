"""Shared logic for the company job-board feeds (Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Recruitee, Zoho).

Input: company identifiers the owner typed ('slug' or 'slug|domain'), in the source's saved settings or on the campaign.
Output: one company row per identifier with open roles in the target cities, the matching role titles and the age of
the oldest posting. A company identifier that does not exist or fails never stops the others.
"""
from ..campaign_sources import hiring_signals
from .base import KeyRejected, SourceError, city_match, company_row, field, pretty_name, slugs

SLUGS_FIELD = field("slugs", "Company identifiers (one per line, or comma separated)", placeholder="acme-hr\nbeta-logistics|beta.in", kind="textarea")
SLUG_STEP = ("Find a company's identifier in the address of its careers page (for example the 'acme' in {example}). "
             "Add a pipe and the company's website if you know it, like acme|acme.in, so contacts can be found for it.")


def discover_boards(ctx, target: dict, limit: int, fetch_board, source_id: str, page_url) -> dict:
    """fetch_board(ctx, slug) -> {"name": str, "jobs": [{"title","location","posted","url"}]}."""
    wanted = [r for r in target.get("roles_hiring") or []]
    cities = target.get("cities") or []
    chosen = slugs(ctx.params.get("slugs") or ctx.values.get("slugs"))
    if not chosen:
        raise SourceError("Add at least one company identifier (see the setup steps for where to find it).")
    rows, failed = [], 0
    for slug, domain in chosen[:limit]:
        try:
            board = ctx.cached(f"board:{slug}", lambda s=slug: fetch_board(ctx, s))
        except KeyRejected:
            raise
        except (OSError, ValueError, KeyError, TypeError):
            failed += 1
            continue
        jobs = [j for j in board["jobs"] if j.get("title") and city_match(j.get("location"), cities)]
        if not jobs:
            continue
        signals = hiring_signals([{"title": j["title"], "date_posted": j.get("posted") or ""} for j in jobs], wanted, ctx.now)
        rows.append(company_row(source_id, board.get("name") or pretty_name(slug), domain=domain,
                                city=next((j["location"] for j in jobs if j.get("location")), ""),
                                source_url=page_url(slug), **signals))
    return {"rows": rows, "requests": ctx.requests, "failed_lookups": failed}


def test_first_board(ctx, fetch_board, label: str) -> dict:
    chosen = slugs(ctx.values.get("slugs"))
    if not chosen:
        return {"ok": True, "message": f"{label} needs no key. Add a company identifier and test again to check it is reachable."}
    slug = chosen[0][0]
    try:
        board = fetch_board(ctx, slug)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "message": f"{label} did not return a board for '{slug}': {str(exc)[:100]}. Check the identifier."}
    return {"ok": True, "message": f"{label} answered for '{slug}': {len(board['jobs'])} open role(s). Free, no key."}
