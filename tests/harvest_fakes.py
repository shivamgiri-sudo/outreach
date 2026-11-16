"""Fake Google Places (New) and OpenStreetMap replies for the lead harvest tests. Every business, number and site is made up;
the shapes follow the public Places (New) Text Search and Overpass / Nominatim docs."""
from app import harvest
from tests.source_fakes import FakeHttp


def place(n: int, pin: str = "411001", lat: float = 18.52, lng: float = 73.85, **extra) -> dict:
    p = {"id": f"ChIJfake{n:04d}", "displayName": {"text": f"Fake Dental Care {n}", "languageCode": "en"},
         "formattedAddress": f"{n}, FC Road, Shivajinagar, Pune, Maharashtra {pin}, India",
         "addressComponents": [{"longText": "Shivajinagar", "shortText": "Shivajinagar", "types": ["sublocality_level_1", "sublocality", "political"]},
                               {"longText": "Pune", "shortText": "Pune", "types": ["locality", "political"]},
                               {"longText": "Maharashtra", "shortText": "MH", "types": ["administrative_area_level_1", "political"]},
                               {"longText": pin, "shortText": pin, "types": ["postal_code"]}],
         "location": {"latitude": lat + n * 0.001, "longitude": lng + n * 0.001},
         "nationalPhoneNumber": f"098220 {n:05d}", "internationalPhoneNumber": f"+91 98220 {n:05d}",
         "websiteUri": f"https://www.fakedental{n}.in/", "businessStatus": "OPERATIONAL",
         "googleMapsUri": f"https://maps.google.com/?cid={1000 + n}", "rating": 4.5, "userRatingCount": 120 + n,
         "primaryType": "dentist", "primaryTypeDisplayName": {"text": "Dentist", "languageCode": "en"},
         "types": ["dentist", "health", "point_of_interest", "establishment"],
         "plusCode": {"globalCode": "7JCMGV2C+XX", "compoundCode": "GV2C+XX Pune, Maharashtra"}}
    p.update(extra)
    return p


def page(start: int, count: int, token: str = "", **kw) -> dict:
    out = {"places": [place(start + i, **kw) for i in range(count)]}
    if token:
        out["nextPageToken"] = token
    return out


class PlacesFake:
    """Answers Places searches from a script: {(textQuery or 'box', pageToken or ''): reply}; default = empty page."""

    def __init__(self, script: dict = None):
        self.script, self.bodies = dict(script or {}), []

    def __call__(self, call):
        body = call["body"]
        self.bodies.append(body)
        key = (body["textQuery"], body.get("pageToken", ""))
        return self.script.get(key, self.script.get((body["textQuery"], "*"), {"places": []}))


def env(http, key: str = "test-key", meter=None) -> harvest.Env:
    return harvest.Env(http, key, "", meter, sleep=lambda s: None, clock=lambda: 0.0)


def fake_http(places=None, overpass=None, nominatim=None) -> FakeHttp:
    routes = {}
    if places is not None:
        routes["places.googleapis.com"] = places
    routes["overpass-api.de"] = overpass if overpass is not None else {"elements": []}
    routes["nominatim.openstreetmap.org"] = nominatim if nominatim is not None else {"_list": []}
    return FakeHttp(routes)


def osm_element(n: int, **tags) -> dict:
    base = {"name": f"Fake Clinic {n}", "amenity": "dentist", "phone": f"+91 20 2553 {n:04d}", "website": f"https://fakeclinic{n}.org",
            "addr:street": "Karve Road", "addr:city": "Pune", "addr:postcode": "411004", "addr:state": "Maharashtra"}
    base.update(tags)
    return {"type": "node", "id": 9000 + n, "lat": 18.5, "lon": 73.8, "tags": base}
