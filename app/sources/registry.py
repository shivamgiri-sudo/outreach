"""The source registry: every source with capability flags, connected status, cost and rate limit, plus the planner.

Capabilities: discover_companies, discover_people, find_email, verify_email, hiring_signal.
`plan(goal, available)` returns the sources to try, cheapest first: free sources, then the owner's paid keys, each tier ordered
by credit cost. The waterfall (waterfall.py) walks that order and stops once a lead has a verified email.
"""
from . import (adzuna, apollo_src, ashby, brave_search, builtin_verifier, data_gov_in, dropcontact, google_cse, google_places_src,
               greenhouse, hunter_src, jooble, lever, lusha, opencorporates, osm_nominatim, osm_overpass, peopledatalabs, prospeo,
               recruitee, rocketreach, site_discover, smartrecruiters, smtp_probe_src, snov, themuse, verifiers, wikidata, workable, zoho_recruit)
from .base import CAPS, Budget, Cache, Ctx

SOURCES = [m.SPEC for m in (
    greenhouse, lever, workable, ashby, smartrecruiters, recruitee, zoho_recruit, adzuna, jooble, themuse, google_cse, brave_search,
    osm_overpass, osm_nominatim, google_places_src, opencorporates, data_gov_in, wikidata,
    site_discover, apollo_src, hunter_src, snov, prospeo, dropcontact, lusha, rocketreach, peopledatalabs, builtin_verifier, smtp_probe_src)] + verifiers.SPECS
BY_ID = {s.id: s for s in SOURCES}
assert len(BY_ID) == len(SOURCES)
LEGACY_VERIFIER_ID = "email_verifier"        # the older card that holds {provider, api_key} for ZeroBounce / NeverBounce


def get(source_id: str):
    return BY_ID.get(source_id)


def labels() -> dict:
    return {s.id: s.title for s in SOURCES}


def connection(spec, saved: dict) -> dict:
    """{connected, reason, values}. Keyless sources are always usable; their saved settings (contact email, slugs) still apply."""
    entry = saved.get(spec.integration) or {}
    values = dict(entry.get("values") or {})
    if spec.id in ("zerobounce", "neverbounce"):
        legacy = saved.get(LEGACY_VERIFIER_ID) or {}
        lv = legacy.get("values") or {}
        provider = str(lv.get("provider") or "").strip().lower().replace(" ", "")
        ok = legacy.get("mode") == "api" and provider == spec.id and bool(lv.get("api_key"))
        return {"connected": ok, "values": {"api_key": lv.get("api_key", "")},
                "reason": "" if ok else f"Choose {spec.title} and add its key in Integrations > Lead data > Email verifier."}
    if spec.keyless:
        return {"connected": True, "reason": "", "values": values}
    if spec.integration == "google_places":
        values = values or {}
    missing = [f["label"] for f in spec.fields if f.get("required") and not values.get(f["key"])]
    if spec.legacy and not spec.fields:
        missing = [] if values.get("api_key") else ["API key"]
    if entry.get("mode") != "api" or missing:
        return {"connected": False, "values": values,
                "reason": "Add " + (", ".join(missing) if missing else "the key") + f" under Integrations > Lead data > {spec.title}."}
    return {"connected": True, "reason": "", "values": values}


def plan(goal: str, available) -> list:
    """Specs able to do `goal` among `available` (ids or specs), free first, then by credit cost, then name."""
    if goal not in CAPS:
        raise ValueError(f"Unknown goal {goal}")
    specs = [BY_ID[a] if isinstance(a, str) else a for a in available if (a in BY_ID if isinstance(a, str) else True)]
    capable = [s for s in specs if goal in s.caps and _has_op(s, goal)]
    return sorted(capable, key=lambda s: (not s.free, s.cost, s.id))


def _has_op(spec, goal: str) -> bool:
    op = {"discover_companies": spec.discover, "hiring_signal": spec.discover, "discover_people": spec.find_people or spec.discover,
          "find_email": spec.find_email or spec.find_people, "verify_email": spec.verify}[goal]
    return op is not None


def describe(saved: dict) -> list:
    out = []
    for s in SOURCES:
        c = connection(s, saved)
        out.append({"id": s.id, "title": s.title, "group": s.group, "capabilities": list(s.caps), "why": s.why, "keyless": s.keyless,
                    "connected": c["connected"], "reason": c["reason"], "free": s.free, "cost": s.cost, "cost_note": s.cost_note,
                    "rate_per_min": s.rate_per_min, "free_note": s.free_note, "terms": s.terms, "docs_url": s.docs_url, "steps": s.steps,
                    "status_note": s.honest_status, "legacy": s.legacy, "discovers": s.discover is not None, "enriches": bool(s.find_people or s.find_email),
                    "verifies": s.verify is not None, "takes_identifiers": any(f["key"] == "slugs" for f in s.fields), "fields": [{k: v for k, v in f.items() if k != "secret"} | {"secret": bool(f.get("secret"))} for f in s.fields],
                    "input_note": s.input_note})
    return out


def catalogue_entries() -> list:
    """Integrations-page cards for sources that do not already have one (the existing Apollo, Hunter, Places and verifier cards stay)."""
    out = []
    for s in SOURCES:
        if s.legacy or s.id == "builtin_verifier" or s.id == "website_deep":
            continue
        out.append({"id": s.id, "title": s.title, "group": "Lead data", "why": s.why, "modes": ["api"], "fields": s.fields, "steps": s.steps,
                    "cost_note": " ".join(x for x in (s.cost_note, s.free_note) if x), "terms": s.terms, "docs_url": s.docs_url, "source": True})
    return out


def make_ctx(store, transports: dict, cap=None, now=None, spent: float = 0.0, **overrides) -> Ctx:
    budget = Budget(cap if cap is not None else 100)
    budget.spent = spent
    return Ctx(http=(transports or {}).get("source_http"), fetch=(transports or {}).get("fetch"), cache=Cache(store, now),
               budget=budget, now=now, **overrides)
