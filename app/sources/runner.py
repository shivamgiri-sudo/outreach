"""Run the chosen discovery sources of a campaign, and test a single source.

Failure isolation: every source runs inside its own guard. A rejected key, a rate limit, a network error or a bad answer is
reported once for that source and the others still run. Free sources run before paid ones; a paid source stops when the
campaign's per-run credit cap is used up.
"""
from datetime import datetime

from ..campaign_sources import KeyRejected, SourceError
from . import registry
from .base import CreditCapReached, DEFAULT_CREDIT_CAP, RateLimited


def credit_cap(campaign: dict) -> int:
    return int((campaign.get("rules") or {}).get("credit_cap", DEFAULT_CREDIT_CAP))


def describe_error(spec, exc: Exception) -> str:
    if isinstance(exc, (KeyRejected, RateLimited, CreditCapReached, SourceError)):
        return str(exc)
    return f"{spec.title} failed: {str(exc)[:160]}"


def discover_rows(store, campaign: dict, transports: dict, report: dict, now: datetime, add_error) -> list:
    """Raw company/people rows from every ticked extra source, cheapest first."""
    chosen = [s for s in (campaign["sources"].get("extra") or []) if s in registry.BY_ID]
    if not chosen:
        return []
    saved = store.read("integrations")
    params = campaign["sources"].get("extra_params") or {}
    limit = campaign["sources"].get("max_results", 25)
    ctx = registry.make_ctx(store, transports, credit_cap(campaign), now, spent=report.get("source_credits", 0.0))
    rows = []
    # Sources that discover companies or people; the cheapest tier runs first.
    ordered = registry.plan("discover_companies", chosen)
    ordered += [s for s in registry.plan("discover_people", chosen) if s not in ordered and s.discover]
    for spec in ordered:
        if spec.legacy:
            continue                                # Apollo / Places keep their own checkboxes
        conn = registry.connection(spec, saved)
        if not conn["connected"]:
            add_error(report, spec.id, f"Not connected: {conn['reason']}")
            continue
        bound = ctx.bind(spec, conn["values"], params.get(spec.id) or {})
        before = ctx.budget.spent
        try:
            out = spec.discover(bound, campaign["target"], limit)
        except Exception as exc:                      # noqa: BLE001 - one source must never abort the run
            add_error(report, spec.id, describe_error(spec, exc))
            continue
        found = [dict(r) for r in out["rows"] if isinstance(r, dict) and r.get("company")]
        for r in found:
            r.setdefault("source", spec.id)
        report["sources"][spec.id] = {"found": len(found), "requests": out.get("requests", 0)}
        report["credits_used_estimate"] += ctx.budget.spent - before
        if out.get("failed_lookups"):
            add_error(report, spec.id, f"{out['failed_lookups']} company identifier(s) could not be read (not found or unavailable).")
        rows += found
    report["source_credits"] = ctx.budget.spent
    ctx.cache.flush()
    return rows


def test_source(store, source_id: str, transports: dict) -> dict:
    """The free 'Test connection' for one source (never raises)."""
    spec = registry.get(source_id)
    if spec is None:
        raise KeyError(source_id)
    if spec.test is None:
        return {"ok": False, "message": "This source has no connection test."}
    conn = registry.connection(spec, store.read("integrations"))
    if not conn["connected"]:
        return {"ok": False, "message": conn["reason"]}
    ctx = registry.make_ctx(store, transports, 0).bind(spec, conn["values"])
    try:
        return spec.test(ctx)
    except KeyRejected as exc:
        return {"ok": False, "message": str(exc)}
    except Exception as exc:                          # noqa: BLE001
        return {"ok": False, "message": f"Could not reach {spec.title}: {str(exc)[:160]}"}
