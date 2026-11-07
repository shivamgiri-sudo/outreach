"""OpenStreetMap Overpass API: businesses with a website by category and city. Free, no key.
Usage policy: https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances (about 10,000 queries and 1 GB a day
per server, be considerate, identify your app). Syntax: https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL
POST https://overpass-api.de/api/interpreter  form data=<query>  -> {elements: [{type, id, tags: {name, website, phone, email, ...}}]}.
Data licence: ODbL, (c) OpenStreetMap contributors; the OSM link is kept as the source link. The User-Agent names this app and,
if you add one, your contact address, as the policy asks."""
import re

from ..hygiene import normalise_email
from ..lead_model import source_phone
from ..campaign_sources import own_site
from .base import SourceSpec, company_row, field

URL = "https://overpass-api.de/api/interpreter"
CITY_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,40}$")
# Owner's industry word -> OSM tag filters. Anything else falls back to office=company.
TAGS = {"it": ['"office"="it"'], "software": ['"office"="it"'], "it services": ['"office"="it"'], "staffing": ['"office"="employment_agency"'],
        "recruitment": ['"office"="employment_agency"'], "bpo": ['"office"="company"'], "finance": ['"office"="financial"'],
        "bfsi": ['"office"="financial"', '"office"="insurance"'], "insurance": ['"office"="insurance"'], "logistics": ['"office"="logistics"'],
        "education": ['"amenity"="college"', '"office"="educational_institution"'], "edtech": ['"office"="educational_institution"'],
        "healthcare": ['"amenity"="hospital"', '"amenity"="clinic"'], "retail": ['"shop"="supermarket"', '"shop"="department_store"'],
        "hotel": ['"tourism"="hotel"']}


def agent(ctx) -> dict:
    contact = (ctx.values.get("contact") or "").strip()
    return {"User-Agent": f"OutreachOS/1.0 (MCN Outreach lead tool{'; contact: ' + contact if contact else ''})"}


def build_query(industry: str, city: str, limit: int) -> str:
    if not CITY_RE.match(city):
        raise ValueError("City names may only contain letters, spaces and . ' -")
    tags = TAGS.get(industry.strip().lower()) or ['"office"="company"']
    parts = "".join(f'nwr[{t}]{w}(area.a);' for t in tags for w in ('["website"]', '["contact:website"]'))
    return (f'[out:json][timeout:25];area["name"="{city}"]["boundary"="administrative"]->.a;({parts});out tags center {int(limit)};')


def map_element(el: dict, city: str) -> dict:
    tags = el.get("tags") or {}
    site, domain = own_site(tags.get("website") or tags.get("contact:website"))
    phone = tags.get("phone") or tags.get("contact:phone") or ""
    mail = normalise_email(tags.get("email") or tags.get("contact:email"))
    row = company_row("osm_overpass", tags.get("name", ""), website=site, city=tags.get("addr:city") or city,
                      source_url=f"https://www.openstreetmap.org/{el.get('type', 'node')}/{el.get('id', '')}",
                      phone=source_phone(phone)[0], company_phone=phone, phone_type=source_phone(phone)[1],
                      email=mail or "", address=", ".join(tags[k] for k in ("addr:housenumber", "addr:street", "addr:city") if tags.get(k)))
    row["domain"] = domain
    return row


def discover(ctx, target, limit):
    kinds = list(target.get("industries") or []) or ["company"]
    cities = list(target.get("cities") or [])
    if not cities:
        raise ValueError("Add at least one city: Overpass searches inside a named place.")
    rows = []
    for kind in kinds:
        for city in cities:
            if len(rows) >= limit:
                break
            query = build_query(kind, city, limit)
            found = ctx.cached(f"q:{query}", lambda q=query: [e for e in ctx.post(URL, headers=agent(ctx), form={"data": q}).get("elements") or []
                                                           if isinstance(e, dict) and (e.get("tags") or {}).get("name")])
            rows += [map_element(e, city) for e in found]
    return {"rows": [r for r in rows if r.get("company") and r.get("domain")][:limit], "requests": ctx.requests}


def test(ctx):
    reply = ctx.get("https://overpass-api.de/api/status", headers=agent(ctx))
    return {"ok": True, "message": "Overpass is reachable. " + str(reply.get("_raw", ""))[:80].replace("\n", " ")}


SPEC = SourceSpec(
    "osm_overpass", "OpenStreetMap (Overpass)", ("discover_companies",), "Company discovery",
    "Businesses with a website in a named city, from OpenStreetMap. Free; coverage in Indian cities is patchy.",
    "https://wiki.openstreetmap.org/wiki/Overpass_API",
    "Open data under ODbL (credit OpenStreetMap contributors). Public server policy: be considerate, identify your app. Do not run big loops.",
    ["Nothing is required. Optionally add your contact email so OpenStreetMap can reach you if your use causes trouble (it goes in the User-Agent).",
     "Pick a city and an industry in the campaign; results need a website tag to be kept."],
    fields=[field("contact", "Your contact email (optional, sent in the User-Agent)", False, False, "you@yourcompany.in")],
    keyless=True, rate_per_min=6, free_note="Free. Server policy asks you to stay well under 10,000 queries a day.", cost_note="Free; 1 request per industry and city.",
    discover=discover, test=test)
