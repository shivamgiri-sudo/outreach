"""Owner rule: an office landline with its STD code is kept as the lead phone (phone_type "landline", calls only) by
campaign sources, Places / OpenStreetMap rows, campaign merges and the prospect database. All data is made up."""
from app import campaign_pipeline as pipe
from app import campaign_sources as src
from app.lead_model import source_phone
from app.privacy import hashes_for
from app.prospect_fields import clean_phone
from app.routes_prospects import _best_phone, _lead_row
from app.sources import osm_nominatim, osm_overpass
from tests.campaign_fakes import FakeIO, by_company, client, connect, create, run


def test_source_phone_prefers_a_mobile_and_keeps_an_std_landline():
    assert source_phone("98100 12345") == ("+919810012345", "mobile")
    assert source_phone("011-2345 6789 / 98100 12345") == ("+919810012345", "mobile")     # mobile wins
    assert source_phone("+91 80 4000 1500") == ("+918040001500", "landline")              # Bengaluru office line
    assert source_phone("(022) 2345-6789") == ("+912223456789", "landline")
    assert source_phone("2345 6789") == ("", "")                                          # no STD code: unusable
    assert source_phone("+44 20 7946 0958") == ("", "") and source_phone("") == ("", "")


def test_apollo_org_and_places_rows_keep_the_landline_as_phone():
    org = src.map_apollo_org({"name": "Vega BPO", "primary_phone": {"number": "+91 20 6712 3400"}, "id": "o1"})
    assert org["phone"] == "+912067123400" and org["phone_type"] == "landline" and org["company_phone"] == "+91 20 6712 3400"
    place = src.map_place({"displayName": {"text": "Orion"}, "nationalPhoneNumber": "0124 456 7890"}, "Gurugram")
    assert place["phone"] == "+911244567890" and place["phone_type"] == "landline"
    none = src.map_place({"displayName": {"text": "Nophone"}}, "Pune")
    assert none["phone"] == "" and none["phone_type"] == "" and none["company_phone"] == ""


def test_osm_rows_keep_the_landline_as_phone():
    el = osm_overpass.map_element({"type": "node", "id": 5, "tags": {"name": "Lyra Calls", "phone": "+91 40 2345 6789"}}, "Hyderabad")
    assert el["phone"] == "+914023456789" and el["phone_type"] == "landline"
    p = osm_nominatim.map_place({"name": "Mira", "osm_id": 9, "extratags": {"phone": "98200 11122"}}, "Mumbai")
    assert p["phone"] == "+919820011122" and p["phone_type"] == "mobile"


def test_campaign_merge_prefers_a_mobile_and_keeps_kind_with_number():
    office = {"company": "Acme", "domain": "acme.in", "phone": "+912067123400", "phone_type": "landline", "source": "google_places"}
    mobile = {"company": "Acme", "domain": "acme.in", "phone": "+919811122233", "phone_type": "mobile", "source": "apollo_hiring"}
    merged = pipe.merge_rows([office, mobile])[0]
    assert merged["phone"] == "+919811122233" and merged["phone_type"] == "mobile"
    merged = pipe.merge_rows([mobile, office])[0]
    assert merged["phone"] == "+919811122233" and merged["phone_type"] == "mobile"


def test_build_candidate_keeps_the_sources_landline_reading():
    cand = pipe.merge_rows([{"company": "Blr Co", "domain": "blr.in", "phone": "+918040001500", "phone_type": "landline",
                             "company_phone": "080 4000 1500", "source": "google_places"}])[0]
    lead = pipe.build_candidate(cand, {"id": "c1", "name": "Test"})
    assert lead["phone"] == "+918040001500" and lead["phone_type"] == "landline" and lead["company_phone"] == "080 4000 1500"


class _OneLeadStore:
    def __init__(self, lead):
        self.lead = lead

    def update_lead(self, lead_id, change):
        out = change(dict(self.lead))
        self.lead = out or self.lead
        return self.lead


def test_merge_into_existing_fills_a_landline_with_its_kind():
    store = _OneLeadStore({"id": "L1", "stage": "new", "phone": None, "phone_type": "business", "timeline": [], "dedupe_key": "domain:x.in"})
    cand = {"phone": "+911123456789", "phone_type": "landline", "provenance": {"phone": "google_places"}, "sources": ["google_places"]}
    assert pipe.merge_into_existing(store, "L1", cand, {"id": "c1", "name": "Test"})
    assert store.lead["phone"] == "+911123456789" and store.lead["phone_type"] == "landline"


def test_places_campaign_run_saves_landline_lead(tmp_path):
    places = {"places": [{"id": "pl9", "displayName": {"text": "Gamma Office"}, "nationalPhoneNumber": "020 2612 3456",
                          "websiteUri": "https://gamma-office.in", "googleMapsUri": "https://maps.google.com/?cid=9"}]}
    c = client(tmp_path, FakeIO(post={"places:searchText": places}))
    connect(c, "google_places", {"api_key": "gk"})
    camp = create(c, sources={"google_places": True})
    assert run(c, camp["id"])["new"] == 1
    gamma = by_company(c, "Gamma Office")
    assert gamma["phone"] == "+912026123456" and gamma["phone_type"] == "landline" and gamma["company_phone"] == "020 2612 3456"


def test_prospect_phone_cleaning_keeps_landlines_and_lead_prefers_mobile():
    assert clean_phone("011-2345 6789") == "+911123456789"
    assert clean_phone("98100 12345") == "+919810012345" and clean_phone("+44 20 7946 0958") == "+442079460958"
    assert clean_phone("2345 6789") == ""
    assert _best_phone(["+911123456789", "+919810012345"]) == "+919810012345"
    assert _best_phone(["+911123456789"]) == "+911123456789" and _best_phone([]) == ""
    row = _lead_row({"full_name": "Asha", "phones": ["+911123456789"], "company": {"name": "Delta", "domain": "delta.in"}})
    assert row["phone"] == "+911123456789"


def test_erasure_hashes_cover_landlines():
    assert hashes_for(None, ["022 2345 6789"]) == hashes_for(None, ["+912223456789"]) != set()
