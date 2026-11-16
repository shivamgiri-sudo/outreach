"""Lead harvest inputs and source mapping: pairs from the form / CSV with limits, location kinds, the estimate,
Places (New) and OpenStreetMap field mapping, and the tile plan. No network."""
import pytest

from app import harvest_osm, harvest_pairs as hp, harvest_places as gp
from tests.harvest_fakes import osm_element, place


# ---------- pairs ----------
def test_location_kinds():
    assert hp.parse_location("Pune") == {"kind": "place", "text": "Pune", "name": "Pune"}
    assert hp.parse_location("Andheri East, Mumbai")["kind"] == "place"
    assert hp.parse_location("560 001") == {"kind": "pin", "text": "560001", "pin": "560001"}
    pt = hp.parse_location("12.9716, 77.5946 + 3km")
    assert pt["kind"] == "point" and pt["lat"] == 12.9716 and pt["radius_km"] == 3.0
    assert hp.parse_location("12.97,77.59")["radius_km"] == hp.DEFAULT_RADIUS_KM
    assert hp.parse_location("12.97,77.59,10")["radius_km"] == 10.0
    for bad in ("", "95.0,77.0", "12.9,77.5,80", "Pune; DROP", "<script>", "012345"):
        with pytest.raises(hp.PairError):
            hp.parse_location(bad)


def test_form_pairs_are_the_product_deduped():
    pairs = hp.pairs_from_form("dentist, Dentist\nhotel", "Pune\n411001")
    assert [(p["keyword"], p["location"]["text"]) for p in pairs] == [("dentist", "Pune"), ("dentist", "411001"), ("hotel", "Pune"), ("hotel", "411001")]
    with pytest.raises(hp.PairError):
        hp.pairs_from_form("", "Pune")
    with pytest.raises(hp.PairError, match="at most 500"):
        hp.pairs_from_form(",".join(f"kw{i}" for i in range(30)), "\n".join(f"City{chr(65 + i % 26)}{'x' * (i // 26)}" for i in range(20)))


def test_csv_pairs_with_header_bad_rows_and_limits():
    text = "﻿keyword,location\ndentist,Pune\nsoftware company,560001\nhotel,\"12.97,77.59,3\"\n,Pune\nonly-one-column\ngym,Pune;x\n"
    out = hp.pairs_from_csv(text)
    assert [p["location"]["kind"] for p in out["pairs"]] == ["place", "pin", "point"]
    assert [e["row"] for e in out["errors"]] == [4, 5, 6]
    unquoted = hp.pairs_from_csv("hotel,12.97,77.59,3")             # a point split over columns is joined back
    assert unquoted["pairs"][0]["location"]["radius_km"] == 3.0
    with pytest.raises(hp.PairError, match="at most 500"):
        hp.pairs_from_csv("\n".join(f"kw{i},Pune" for i in range(501)))
    assert len(hp.pairs_from_csv("\n".join(f"kw{i},Pune" for i in range(500)))["pairs"]) == 500
    with pytest.raises(hp.PairError, match="too big"):
        hp.pairs_from_csv("x" * (hp.MAX_CSV_CHARS + 1))
    assert hp.pairs_from_csv(hp.TEMPLATE_CSV)["errors"] == []


def test_estimate_bounds_and_cap():
    est = hp.estimate(10, ["google_places", "osm"], 9)
    assert (est["google_min"], est["google_typical"], est["google_max"]) == (10, 30, 300)
    assert est["default_cap"] == 300 and est["osm_requests"] == 20
    assert hp.estimate(10, ["osm"])["google_max"] == 0
    assert hp.estimate(500, ["google_places"], 25)["default_cap"] == hp.MAX_CREDIT_CAP
    assert hp.clamp_tiles(99) == hp.MAX_TILES_CAP and hp.clamp_tiles("x") == hp.DEFAULT_MAX_TILES


# ---------- Google Places mapping ----------
def test_places_new_field_mapping():
    row = gp.map_place(place(7))
    assert row["company"] == "Fake Dental Care 7"
    assert row["website"] == "https://www.fakedental7.in/" and row["domain"] == "fakedental7.in"
    assert row["city"] == "Pune" and row["state"] == "Maharashtra" and row["pin"] == "411001" and row["area"] == "Shivajinagar"
    assert row["category"] == "Dentist" and row["rating"] == 4.5 and row["reviews_count"] == 127
    assert row["maps_url"] == "https://maps.google.com/?cid=1007" and row["source_url"] == row["maps_url"]
    assert row["plus_code"] == "7JCMGV2C+XX" and row["business_status"] == "OPERATIONAL" and row["google_place_id"] == "ChIJfake0007"
    assert row["source"] == "google_places" and "+91 98220 00007" in row["phone_raw"]
    bare = gp.map_place({"id": "x1", "displayName": {"text": "Bare"}, "websiteUri": "https://facebook.com/bare", "types": ["car_repair"]}, "Mumbai")
    assert bare["website"] == "" and bare["city"] == "Mumbai" and bare["category"] == "Car repair"
    assert bare["maps_url"].endswith("place_id:x1") and bare["rating"] is None


def test_field_mask_and_bodies():
    for f in ("rating", "userRatingCount", "primaryType", "types", "plusCode", "googleMapsUri", "businessStatus", "nationalPhoneNumber",
              "internationalPhoneNumber", "websiteUri", "formattedAddress", "addressComponents", "nextPageToken"):
        assert f in gp.FIELD_MASK
    assert gp.headers("k")["X-Goog-FieldMask"] == gp.FIELD_MASK
    loc = hp.parse_location("Pune")
    assert gp.search_body("dentist", loc)["textQuery"] == "dentist in Pune"
    assert gp.search_body("dentist", loc, token="t2")["pageToken"] == "t2"
    point = gp.search_body("hotel", hp.parse_location("12.97,77.59,2"))
    rect = point["locationRestriction"]["rectangle"]
    assert point["textQuery"] == "hotel" and rect["low"]["latitude"] < 12.97 < rect["high"]["latitude"]
    assert gp.search_body("dentist", loc, {"kind": "pin", "pin": "411004"})["textQuery"] == "dentist in 411004"


# ---------- tiles ----------
def test_grid_tiles_never_pass_max():
    box = {"south": 18.4, "north": 18.6, "west": 73.7, "east": 73.9}
    assert len(gp.grid(box, 9)) == 9 and len(gp.grid(box, 8)) == 4 and gp.grid(box, 3) == []
    tiles = gp.plan_tiles(hp.parse_location("Pune"), [], box, 9)
    assert len(tiles) == 9 and all(t["kind"] == "box" for t in tiles)
    seen = [{"lat": None, "lng": None, "pin": p} for p in ["411001", "411004", "411004", "411030", "411001", "bad"]]
    pins = gp.plan_tiles(hp.parse_location("411001"), seen, None, 2)
    assert pins == [{"kind": "pin", "pin": "411004"}, {"kind": "pin", "pin": "411030"}]
    from_rows = gp.plan_tiles(hp.parse_location("Pune"), [gp.map_place(place(i)) for i in range(5)], None, 4)
    assert len(from_rows) == 4


# ---------- OpenStreetMap ----------
def test_osm_mapping_and_queries():
    row = harvest_osm.map_element(osm_element(3))
    assert row["company"] == "Fake Clinic 3" and row["category"] == "Dentist" and row["pin"] == "411004"
    assert row["source"] == "osm" and row["source_url"] == "https://www.openstreetmap.org/node/9003" and row["domain"] == "fakeclinic3.org"
    assert row["state"] == "Maharashtra" and row["city"] == "Pune"
    q = harvest_osm.build_query("dentist", hp.parse_location("Pune"))
    assert 'area["name"="Pune"]' in q and '"amenity"="dentist"' in q
    q2 = harvest_osm.build_query("Tata Motors", hp.parse_location("12.97,77.59,2"))
    assert '"name"~"Tata Motors",i' in q2 and "around:2000,12.970000,77.590000" in q2
    q3 = harvest_osm.build_query("gym", hp.parse_location("411001"), {"lat": 18.5, "lng": 73.8})
    assert "around:4000,18.500000,73.800000" in q3
    with pytest.raises(ValueError):
        harvest_osm.build_query("gym", hp.parse_location("411001"), None)
    assert '"' not in harvest_osm.tag_filters('evil" ];out;')[0].split('~"')[1].split('"')[0]
    assert "contact: me@example.com" in harvest_osm.agent("me@example.com")["User-Agent"]


def test_overpass_falls_back_to_mirrors_and_says_busy_when_all_fail():
    import pytest
    from app import harvest_osm
    calls = []

    def flaky(method, url, headers, body, form):
        calls.append(url)
        if "overpass-api.de" in url:
            raise OSError("HTTP 502")
        return {"elements": [{"type": "node", "id": 1, "tags": {"name": "Clinic"}}]}

    assert [e["id"] for e in harvest_osm.overpass(flaky, "q")] == [1]
    assert calls == list(harvest_osm.OVERPASS_MIRRORS[:2])                 # main server, then the first mirror

    def down(method, url, headers, body, form):
        raise OSError("Could not connect")

    with pytest.raises(OSError, match="busy or unreachable"):
        harvest_osm.overpass(down, "q")

    def refused(method, url, headers, body, form):
        raise PermissionError("HTTP 403")

    with pytest.raises(PermissionError):                                     # a refusal is not retried on the mirrors
        harvest_osm.overpass(refused, "q")
