"""Lead harvest inputs: (keyword, location) pairs from the form or a CSV, the location kinds, and the credit estimate.

Location kinds
  place   a city, area or state in words ("Pune", "Andheri East, Mumbai", "Karnataka")
  pin     an Indian PIN code ("560001", "560 001")
  point   "lat,lng" with an optional radius in km: "12.97,77.59", "12.97,77.59,3", "12.97, 77.59 + 3km" (default 5 km)
"""
import csv
import io
import re

MAX_PAIRS = 500
MAX_CSV_CHARS = 200_000
DEFAULT_MAX_TILES, MAX_TILES_CAP = 9, 25
DEFAULT_RADIUS_KM, MAX_RADIUS_KM = 5.0, 50.0
PLACES_PAGES = 3                    # Google returns at most 3 pages of 20 per search
PLACES_CREDIT = 1                   # one credit per Places request (the registry's cost for google_places)
MAX_CREDIT_CAP = 5000
SOURCES = ("google_places", "osm")
TEMPLATE_CSV = "keyword,location\ndentist,Pune\nsoftware company,560001\nhotel,\"12.9716,77.5946,3\"\n"

_POINT = re.compile(r"^\s*(-?\d{1,2}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*(?:(?:,|\+|r\s*=)\s*(\d{1,3}(?:\.\d+)?)\s*(?:km)?)?\s*$", re.I)
_PIN = re.compile(r"^\s*([1-9]\d{2})\s?(\d{3})\s*$")
_PLACE = re.compile(r"^[A-Za-z][A-Za-z .,'()&-]{1,79}$")
_KEYWORD = re.compile(r"^[\w][\w .,&'/()+-]{1,79}$", re.UNICODE)


class PairError(ValueError):
    """A pair or file that cannot be used; the message says why in plain words."""


def parse_location(raw) -> dict:
    """{kind, text, ...}: place {name}, pin {pin}, point {lat, lng, radius_km}. Raises PairError."""
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not text:
        raise PairError("A location is missing.")
    m = _POINT.match(text)
    if m:
        lat, lng = float(m.group(1)), float(m.group(2))
        radius = float(m.group(3)) if m.group(3) else DEFAULT_RADIUS_KM
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise PairError(f"'{text}' is not a valid latitude,longitude.")
        if not 0 < radius <= MAX_RADIUS_KM:
            raise PairError(f"The radius in '{text}' must be between 0 and {MAX_RADIUS_KM:g} km.")
        return {"kind": "point", "text": text, "lat": lat, "lng": lng, "radius_km": radius}
    m = _PIN.match(text)
    if m:
        pin = m.group(1) + m.group(2)
        return {"kind": "pin", "text": pin, "pin": pin}
    if not _PLACE.match(text):
        raise PairError(f"'{text[:40]}' is not a place name, a 6-digit PIN code or 'lat,lng,radius km'.")
    return {"kind": "place", "text": text, "name": text}


def clean_keyword(raw) -> str:
    text = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not _KEYWORD.match(text):
        raise PairError(f"'{text[:40] or '(empty)'}' is not a usable keyword (2 to 80 letters, digits and simple punctuation).")
    return text


def make_pair(keyword, location) -> dict:
    return {"keyword": clean_keyword(keyword), "location": parse_location(location)}


def _dedupe(pairs: list) -> list:
    out, seen = [], set()
    for p in pairs:
        key = (p["keyword"].lower(), p["location"]["text"].lower())
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _check_count(pairs: list) -> list:
    if not pairs:
        raise PairError("Add at least one keyword and one location.")
    if len(pairs) > MAX_PAIRS:
        raise PairError(f"That is {len(pairs)} keyword and location pairs; one job takes at most {MAX_PAIRS}. Split it into smaller jobs.")
    return pairs


def _items(raw) -> list:
    values = raw if isinstance(raw, list) else re.split(r"[\n;]+", str(raw or ""))
    return [str(v).strip() for v in values if str(v).strip()]


def pairs_from_form(keywords, locations) -> list:
    """Every keyword with every location (keywords split on commas or new lines, locations on new lines or ';')."""
    kws = [k for chunk in _items(keywords) for k in chunk.split(",") if k.strip()]
    locs = _items(locations)
    if len(kws) * len(locs) > MAX_PAIRS:
        raise PairError(f"{len(kws)} keywords x {len(locs)} locations is {len(kws) * len(locs)} pairs; one job takes at most {MAX_PAIRS}.")
    return _check_count(_dedupe([make_pair(k, l) for k in kws for l in locs]))


def pairs_from_csv(text: str) -> dict:
    """{pairs, errors}: rows 'keyword,location' (a header row is optional). Bad rows are listed, not fatal."""
    if len(text or "") > MAX_CSV_CHARS:
        raise PairError(f"The file is too big ({len(text) // 1000} kB); keep it under {MAX_CSV_CHARS // 1000} kB.")
    rows = [r for r in csv.reader(io.StringIO((text or "").lstrip("﻿"))) if any(c.strip() for c in r)]
    if rows and rows[0] and rows[0][0].strip().lower() in ("keyword", "keywords", "query", "search"):
        rows = rows[1:]
    if len(rows) > MAX_PAIRS:
        raise PairError(f"The file has {len(rows)} rows; one job takes at most {MAX_PAIRS}. Split it into smaller files.")
    pairs, errors = [], []
    for n, row in enumerate(rows, start=1):
        if len(row) < 2:
            errors.append({"row": n, "error": "Needs two columns: keyword, location."})
            continue
        try:
            pairs.append(make_pair(row[0], ",".join(row[1:]) if len(row) > 2 else row[1]))
        except PairError as exc:
            errors.append({"row": n, "error": str(exc)})
    return {"pairs": _check_count(_dedupe(pairs)) if pairs else [], "errors": errors}


def clamp_tiles(value) -> int:
    try:
        return max(1, min(MAX_TILES_CAP, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_TILES


def estimate(n_pairs: int, sources, max_tiles: int = DEFAULT_MAX_TILES) -> dict:
    """Google Places requests (= credits) for n pairs: at least 1 each, usually up to 3 (60 results), and when a search is
    full, up to 3 more per tile. OpenStreetMap is free (1-2 requests per pair)."""
    google = "google_places" in (sources or ())
    tiles = clamp_tiles(max_tiles)
    low = n_pairs * PLACES_CREDIT if google else 0
    typical = n_pairs * PLACES_PAGES * PLACES_CREDIT if google else 0
    worst = n_pairs * PLACES_PAGES * (1 + tiles) * PLACES_CREDIT if google else 0
    return {"pairs": n_pairs, "max_tiles": tiles, "google_min": low, "google_typical": typical, "google_max": worst,
            "osm_requests": n_pairs * 2 if "osm" in (sources or ()) else 0,
            "default_cap": min(worst, MAX_CREDIT_CAP), "max_cap": MAX_CREDIT_CAP,
            "note": ("Each Google Places request is 1 credit and is billed by Google to your key. The job stops paid "
                     "searches when it reaches its credit cap. OpenStreetMap is free.") if google else "OpenStreetMap is free."}
