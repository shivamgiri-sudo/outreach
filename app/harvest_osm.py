"""OpenStreetMap for the lead harvest: Overpass for businesses, Nominatim to turn a PIN or place into coordinates / a box.

Overpass usage policy (https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances): be considerate,
identify the app; the harvest makes at most one Overpass query per pair and waits OVERPASS_GAP seconds between them.
Nominatim policy (https://operations.osmfoundation.org/policies/nominatim/): at most 1 request a second, identify the app,
no bulk use; the harvest asks it only for a PIN code (OSM search) or when a Google search needs tiles, one query per pair,
and keeps the answer on the job. Data licence ODbL, (c) OpenStreetMap contributors; each lead keeps its OSM link.
"""
import re
import urllib.parse
from typing import Optional

from .campaign_sources import own_site
from .hygiene import normalise_email
from .places import clean_city, clean_state
from .sources.osm_overpass import TAGS as BASE_TAGS

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_GAP, NOMINATIM_GAP = 10.0, 1.1          # seconds between calls (6 a minute; 1 a second)
MAX_ELEMENTS = 200
PIN_RADIUS_M = 4000
EXTRA_TAGS = {"dentist": ['"amenity"="dentist"'], "doctor": ['"amenity"="doctors"'], "clinic": ['"amenity"="clinic"'],
              "hospital": ['"amenity"="hospital"'], "pharmacy": ['"amenity"="pharmacy"'], "restaurant": ['"amenity"="restaurant"'],
              "cafe": ['"amenity"="cafe"'], "school": ['"amenity"="school"'], "college": ['"amenity"="college"'],
              "bank": ['"amenity"="bank"'], "gym": ['"leisure"="fitness_centre"'], "salon": ['"shop"="hairdresser"', '"shop"="beauty"'],
              "real estate": ['"office"="estate_agent"'], "lawyer": ['"office"="lawyer"'], "accountant": ['"office"="accountant"'],
              "travel agent": ['"shop"="travel_agency"'], "car dealer": ['"shop"="car"'], "furniture": ['"shop"="furniture"'],
              "electronics": ['"shop"="electronics"'], "bakery": ['"shop"="bakery"'], "hardware": ['"shop"="hardware"']}
CATEGORY_KEYS = ("shop", "amenity", "office", "craft", "tourism", "leisure", "healthcare")
_SAFE_WORD = re.compile(r"[^A-Za-z0-9 &-]")
_CITY = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,60}$")


def agent(contact: str = "") -> dict:
    """The User-Agent both OSM policies ask for: names this app and, when the owner added one, a contact address."""
    contact = re.sub(r"[^\w@.+-]", "", str(contact or ""))[:120]
    return {"User-Agent": f"OutreachOS/1.0 (MCN Outreach lead harvest{'; contact: ' + contact if contact else ''})"}


def tag_filters(keyword: str) -> list:
    """Overpass tag filters for a keyword: a known category, else a case-insensitive name match on shops / offices / amenities."""
    key = keyword.strip().lower()
    known = EXTRA_TAGS.get(key) or BASE_TAGS.get(key) or EXTRA_TAGS.get(key.rstrip("s")) or BASE_TAGS.get(key.rstrip("s"))
    if known:
        return [f"[{t}]" for t in known]
    word = re.sub(r"\s+", " ", _SAFE_WORD.sub("", keyword)).strip()[:60]     # letters, digits, space, & and -: no regex or QL syntax
    if not word:
        raise ValueError("The keyword has no letters or digits OpenStreetMap can search for.")
    return [f'["name"~"{word}",i][{k}]' for k in ('"shop"', '"office"', '"amenity"', '"craft"')]


def build_query(keyword: str, location: dict, center: Optional[dict] = None) -> str:
    """Overpass QL for a pair: inside a named area, or around a point / a geocoded PIN."""
    filters = tag_filters(keyword)
    if location["kind"] == "place":
        name = location["name"].split(",")[0].strip()
        if not _CITY.match(name):
            raise ValueError("City names may only contain letters, spaces and . ' -")
        head, scope = f'area["name"="{name}"]["boundary"="administrative"]->.a;', "(area.a)"
    else:
        point = center if location["kind"] == "pin" else location
        if not point:
            raise ValueError("Could not find where that PIN code is.")
        radius = PIN_RADIUS_M if location["kind"] == "pin" else int(location["radius_km"] * 1000)
        head, scope = "", f"(around:{int(radius)},{float(point['lat']):.6f},{float(point['lng']):.6f})"
    parts = "".join(f"nwr{f}{scope};" for f in filters)
    return f"[out:json][timeout:25];{head}({parts});out tags center {MAX_ELEMENTS};"


def map_element(el: dict, fallback_city: str = "") -> dict:
    tags = el.get("tags") or {}
    site, domain = own_site(tags.get("website") or tags.get("contact:website") or tags.get("url"))
    phones = [tags.get(k) or "" for k in ("phone", "contact:phone", "mobile", "contact:mobile")]
    category = next((f"{tags[k]}".replace("_", " ") for k in CATEGORY_KEYS if tags.get(k) and tags[k] != "yes"), "")
    osm_url = f"https://www.openstreetmap.org/{el.get('type', 'node')}/{el.get('id', '')}"
    center = el.get("center") if isinstance(el.get("center"), dict) else el
    return {"company": str(tags.get("name") or "").strip()[:200], "phone_raw": phones, "website": site, "domain": domain,
            "email": normalise_email(tags.get("email") or tags.get("contact:email")) or "",
            "address": ", ".join(tags[k] for k in ("addr:housenumber", "addr:street", "addr:suburb", "addr:city") if tags.get(k))[:300],
            "city": clean_city(tags.get("addr:city") or "") or clean_city(fallback_city) or "", "state": clean_state(tags.get("addr:state") or ""),
            "pin": str(tags.get("addr:postcode") or "").replace(" ", "")[:10], "category": category[:1].upper() + category[1:120],
            "country": "IN", "source": "osm", "source_url": osm_url, "osm_id": f"{el.get('type', 'node')}/{el.get('id', '')}",
            "lat": center.get("lat"), "lng": center.get("lon")}


def overpass(http, query: str, contact: str = "") -> list:
    reply = http("POST", OVERPASS_URL, agent(contact), None, {"data": query})
    return [e for e in reply.get("elements") or [] if isinstance(e, dict) and (e.get("tags") or {}).get("name")]


def geocode(http, location: dict, contact: str = "") -> Optional[dict]:
    """{lat, lng, box} for a PIN code or place via Nominatim (one request), or None when it is not found."""
    params = {"format": "jsonv2", "countrycodes": "in", "limit": 1}
    if location["kind"] == "pin":
        params["postalcode"] = location["pin"]
    elif location["kind"] == "place":
        params["q"] = location["name"]
    else:
        return None
    reply = http("GET", f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}", agent(contact), None, None)
    found = next((x for x in reply.get("_list") or [] if isinstance(x, dict)), None)
    if not found:
        return None
    try:
        south, north, west, east = (float(v) for v in found.get("boundingbox") or [])
        box = {"south": south, "north": north, "west": west, "east": east}
    except (TypeError, ValueError):
        box = None
    try:
        return {"lat": float(found["lat"]), "lng": float(found["lon"]), "box": box}
    except (KeyError, TypeError, ValueError):
        return None
