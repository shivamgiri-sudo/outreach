"""Google Places (New) Text Search as a registry source. The adapter itself lives in app/campaign_sources.py (kept so old
campaigns keep working); this wraps it so it appears in the registry, the plan and /api/sources.
Docs: https://developers.google.com/maps/documentation/places/web-service/text-search ; billed by Google per request."""
from ..campaign_sources import PLACES_SEARCH_URL, check_places, google_places
from .base import SourceSpec


def discover(ctx, target, limit):
    key = ctx.values.get("api_key", "")
    out = google_places(target, key, lambda url, headers, body: ctx.post(url, body=body, headers=headers), limit)
    return {"rows": out["rows"], "requests": out["requests"]}


def test(ctx):
    return check_places({"mode": "api", "values": ctx.values}, lambda url, headers, body: ctx.post(url, body=body, headers=headers))


SPEC = SourceSpec(
    "google_places", "Google Places (business search)", ("discover_companies",), "Company discovery",
    "Businesses by type and city with website and office phone, using your own Google key.",
    "https://developers.google.com/maps/documentation/places/web-service/text-search",
    "Google Maps Platform terms apply; results are used for the current lead only. Billed by Google per search.",
    ["Set up the key under Integrations > Lead data > Google Places (it is the same key)."],
    fields=[], cost=1, cost_note="1 credit per search; check Google's pricing page.", rate_per_min=60,
    free_note="Google gives a monthly free allowance; see its pricing page.", integration="google_places",
    legacy=True, honest_status="existing adapter (coded from docs, fake-tested)", discover=discover, test=test)
