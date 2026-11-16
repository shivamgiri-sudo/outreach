"""Google Places (New) Text Search for the lead harvest: the request body, the field mapping and the tiles.

Docs: https://developers.google.com/maps/documentation/places/web-service/text-search (the same endpoint and key as
app/campaign_sources.py google_places). One request returns up to 20 places; `nextPageToken` gives two more pages, so
one search tops out at 60. When a search comes back full, the area is split into tiles: a grid of rectangles sent as
`locationRestriction.rectangle` (Text Search accepts only a rectangle as a restriction), or, when no area box is known,
one search per PIN code seen in the results. Each tile is one more search of up to 3 pages, and never split again.

Google Maps Platform terms: the results are kept only as the lead's own fields (as the existing integration does);
nothing else is cached, and the page that shows them carries the "Results from Google" attribution.
"""
import math
from typing import Optional

from .campaign_sources import PLACES_SEARCH_URL, own_site
from .hygiene import safe_http_url
from .places import clean_city, clean_state

FIELD_MASK = ",".join(["nextPageToken"] + [f"places.{f}" for f in (
    "id", "displayName", "formattedAddress", "addressComponents", "location", "nationalPhoneNumber", "internationalPhoneNumber",
    "websiteUri", "businessStatus", "googleMapsUri", "rating", "userRatingCount", "primaryType", "primaryTypeDisplayName",
    "types", "plusCode")])
PAGE_SIZE = 20
MAX_PAGES = 3
KM_PER_DEG = 111.32
URL = PLACES_SEARCH_URL


def headers(key: str) -> dict:
    return {"X-Goog-Api-Key": key, "X-Goog-FieldMask": FIELD_MASK}


def box_around(lat: float, lng: float, radius_km: float) -> dict:
    """{south, north, west, east} of the square around a circle."""
    dlat = radius_km / KM_PER_DEG
    dlng = radius_km / (KM_PER_DEG * max(0.01, math.cos(math.radians(lat))))
    return {"south": lat - dlat, "north": lat + dlat, "west": lng - dlng, "east": lng + dlng}


def rectangle(box: dict) -> dict:
    return {"rectangle": {"low": {"latitude": round(box["south"], 6), "longitude": round(box["west"], 6)},
                          "high": {"latitude": round(box["north"], 6), "longitude": round(box["east"], 6)}}}


def search_body(keyword: str, location: dict, tile: Optional[dict] = None, token: str = "") -> dict:
    """The Text Search body for a pair (or one of its tiles). A page token is sent with the same body as page 1."""
    body = {"pageSize": PAGE_SIZE, "regionCode": "IN", "languageCode": "en"}
    if tile and tile.get("kind") == "box":
        body.update(textQuery=keyword, locationRestriction=rectangle(tile["box"]))
    elif tile and tile.get("kind") == "pin":
        body["textQuery"] = f"{keyword} in {tile['pin']}"
    elif location["kind"] == "point":
        body.update(textQuery=keyword, locationRestriction=rectangle(box_around(location["lat"], location["lng"], location["radius_km"])))
    elif location["kind"] == "pin":
        body["textQuery"] = f"{keyword} in {location['pin']}"
    else:
        body["textQuery"] = f"{keyword} in {location['name']}"
    if token:
        body["pageToken"] = token
    return body


def _component(place: dict, kind: str, short: bool = False) -> str:
    for c in place.get("addressComponents") or []:
        if isinstance(c, dict) and kind in (c.get("types") or []):
            return str(c.get("shortText" if short else "longText") or c.get("longText") or "").strip()
    return ""


def _category(place: dict) -> str:
    shown = (place.get("primaryTypeDisplayName") or {}).get("text") if isinstance(place.get("primaryTypeDisplayName"), dict) else ""
    raw = shown or place.get("primaryType") or next(iter(place.get("types") or []), "")
    text = str(raw or "").replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else ""


def _number(value, kind=float):
    try:
        return kind(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def map_place(place: dict, fallback_city: str = "") -> dict:
    """One Places (New) result -> a harvest row (the lead fields plus lat/lng used for tiling only)."""
    website, domain = own_site(place.get("websiteUri"))
    place_id = str(place.get("id") or "")
    maps_url = safe_http_url(place.get("googleMapsUri")) or (f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else "")
    city = _component(place, "locality") or _component(place, "administrative_area_level_3") or _component(place, "administrative_area_level_2")
    loc = place.get("location") if isinstance(place.get("location"), dict) else {}
    plus = place.get("plusCode") if isinstance(place.get("plusCode"), dict) else {}
    row = {"company": str((place.get("displayName") or {}).get("text") or "").strip()[:200],
           "phone_raw": [place.get("internationalPhoneNumber") or "", place.get("nationalPhoneNumber") or ""],
           "website": website, "domain": domain, "address": str(place.get("formattedAddress") or "")[:300],
           "city": clean_city(city) or clean_city(fallback_city) or "", "state": clean_state(_component(place, "administrative_area_level_1")),
           "pin": _component(place, "postal_code")[:10], "area": _component(place, "sublocality") or _component(place, "sublocality_level_1"),
           "category": _category(place)[:120], "rating": _number(place.get("rating")), "reviews_count": _number(place.get("userRatingCount"), int),
           "maps_url": maps_url, "plus_code": str(plus.get("globalCode") or "")[:20], "business_status": str(place.get("businessStatus") or "")[:40],
           "google_place_id": place_id[:200], "country": "IN", "source": "google_places", "source_url": maps_url,
           "lat": _number(loc.get("latitude")), "lng": _number(loc.get("longitude"))}
    return row


def read_page(reply: dict, fallback_city: str = "") -> dict:
    """{rows, token, full}: the mapped places of one page, the next page token, and whether the page was full."""
    places = [p for p in (reply.get("places") or []) if isinstance(p, dict)]
    return {"rows": [map_place(p, fallback_city) for p in places], "token": str(reply.get("nextPageToken") or ""),
            "full": len(places) >= PAGE_SIZE}


# ---------- tiles ----------
def box_of_rows(rows: list) -> Optional[dict]:
    """The box around the places seen so far (a few hundred metres of margin), or None without coordinates."""
    pts = [(r["lat"], r["lng"]) for r in rows if r.get("lat") is not None and r.get("lng") is not None]
    if len(pts) < 2:
        return None
    lats, lngs = [p[0] for p in pts], [p[1] for p in pts]
    pad = 0.005
    return {"south": min(lats) - pad, "north": max(lats) + pad, "west": min(lngs) - pad, "east": max(lngs) + pad}


def grid(box: dict, max_tiles: int) -> list:
    """An n x n grid of boxes with n*n <= max_tiles (9 -> 3 x 3). Fewer than 4 allowed -> no grid."""
    n = int(math.isqrt(max(0, max_tiles)))
    if n < 2:
        return []
    dlat, dlng = (box["north"] - box["south"]) / n, (box["east"] - box["west"]) / n
    return [{"kind": "box", "box": {"south": box["south"] + i * dlat, "north": box["south"] + (i + 1) * dlat,
                                    "west": box["west"] + j * dlng, "east": box["west"] + (j + 1) * dlng}}
            for i in range(n) for j in range(n)]


def pin_tiles(pins: list, own_pin: str, max_tiles: int) -> list:
    """One search per PIN code seen in the results (not the pair's own PIN), most frequent first, up to max_tiles."""
    counts = {}
    for pin in pins:
        if pin and len(pin) == 6 and pin.isdigit() and pin != own_pin:
            counts[pin] = counts.get(pin, 0) + 1
    ordered = sorted(counts, key=lambda p: (-counts[p], p))
    return [{"kind": "pin", "pin": p} for p in ordered[:max_tiles]]


def plan_tiles(location: dict, seen_rows: list, geocoded_box: Optional[dict], max_tiles: int) -> list:
    """The tiles for a pair whose search came back full: a grid over the known area box (the circle, the geocoded
    place, or the box of the results), else PIN codes from the results. Never more than max_tiles."""
    box = None
    if location["kind"] == "point":
        box = box_around(location["lat"], location["lng"], location["radius_km"])
    box = box or geocoded_box or box_of_rows(seen_rows)
    tiles = grid(box, max_tiles) if box else []
    if not tiles:
        tiles = pin_tiles([r.get("pin") or "" for r in seen_rows], location.get("pin", ""), max_tiles)
    return tiles[:max_tiles]
