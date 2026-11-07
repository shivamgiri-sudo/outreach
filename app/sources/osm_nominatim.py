"""OpenStreetMap Nominatim search: places by free text. Free, no key. Usage policy (strict):
https://operations.osmfoundation.org/policies/nominatim/  -> absolute maximum 1 request per second, a User-Agent that identifies
the app, no bulk or autocomplete use, cache results. API: https://nominatim.org/release-docs/latest/api/Search/
GET https://nominatim.openstreetmap.org/search?q=&format=jsonv2&countrycodes=in&limit=&extratags=1
-> [{name, display_name, osm_type, osm_id, extratags: {website, phone, email}, address: {city}}].
This is a light lookup (a few queries per run, cached 7 days), not a crawler. Data licence ODbL."""
from ..campaign_sources import own_site
from ..hygiene import normalise_email
from ..lead_model import source_phone
from .base import SourceSpec, company_row, field
from .osm_overpass import agent

URL = "https://nominatim.openstreetmap.org/search"
MAX_QUERIES = 4


def map_place(p: dict, city: str) -> dict:
    extra = p.get("extratags") or {}
    site, domain = own_site(extra.get("website") or extra.get("contact:website"))
    phone = extra.get("phone") or extra.get("contact:phone") or ""
    row = company_row("osm_nominatim", p.get("name") or "", website=site, city=(p.get("address") or {}).get("city") or city,
                      source_url=f"https://www.openstreetmap.org/{p.get('osm_type', 'node')}/{p.get('osm_id', '')}",
                      phone=source_phone(phone)[0], company_phone=phone, phone_type=source_phone(phone)[1],
                      email=normalise_email(extra.get("email") or extra.get("contact:email")) or "", address=str(p.get("display_name", ""))[:300])
    row["domain"] = domain
    return row


def discover(ctx, target, limit):
    kinds = list(target.get("industries") or []) or list(target.get("keywords") or []) or ["company"]
    cities = list(target.get("cities") or []) or [""]
    rows = []
    for kind, city in [(k, c) for k in kinds for c in cities][:MAX_QUERIES]:
        q = f"{kind} {city}".strip()
        params = {"q": q, "format": "jsonv2", "countrycodes": "in", "limit": min(20, limit), "extratags": 1, "addressdetails": 1}
        found = ctx.cached(f"q:{q}|{params['limit']}", lambda p=params: [x for x in ctx.get(URL, headers=agent(ctx), params=p).get("_list") or [] if isinstance(x, dict)])
        rows += [map_place(p, city) for p in found]
    return {"rows": [r for r in rows if r.get("company") and r.get("domain")][:limit], "requests": ctx.requests}


def test(ctx):
    ctx.get("https://nominatim.openstreetmap.org/status.php", headers=agent(ctx), params={"format": "json"})
    return {"ok": True, "message": "Nominatim is reachable (status page, no search used)."}


SPEC = SourceSpec(
    "osm_nominatim", "OpenStreetMap (Nominatim)", ("discover_companies",), "Company discovery",
    "Looks up named places such as 'software company Pune' with website and phone when the map lists them. A light lookup, not a crawler.",
    "https://operations.osmfoundation.org/policies/nominatim/",
    "Strict public policy: at most 1 request a second, identify the app, no bulk use. Only 4 searches per run. Data under ODbL.",
    ["Nothing is required. Optionally add your contact email for the User-Agent."],
    fields=[field("contact", "Your contact email (optional, sent in the User-Agent)", False, False, "you@yourcompany.in")],
    keyless=True, rate_per_min=30, free_note="Free, at most 1 request a second.", cost_note="Free; up to 4 requests per run.",
    discover=discover, test=test)
