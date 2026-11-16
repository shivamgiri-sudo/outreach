"""Lead harvest engine: lifecycle and resume, tiling limits, credit cap, cross-job dedupe that fills only empty fields,
DND flagging, failures. Fake HTTP only."""
import pytest

from app import harvest, harvest_pairs as hp
from app.store import Store
from tests.harvest_fakes import PlacesFake, env, fake_http, osm_element, page, place
from tests.source_fakes import HttpError


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "s.json")


def make(store, keywords="dentist", locations="Pune", sources=("google_places",), start=True, **kw):
    job = harvest.new_job(hp.pairs_from_form(keywords, locations), list(sources), kw.pop("ws", "main"), "mcn", "Tester", **kw)
    harvest.add(store, job)
    if start:
        harvest.transition(store, job["id"], "start")
    return job["id"]


def run(store, http, **kw):
    return harvest.run_until_idle(store, lambda job: env(http, **kw))


def leads(store):
    return store.read("leads")


def test_lifecycle_start_pause_resume_cancel(store):
    places = PlacesFake({("dentist in Pune", ""): page(1, 20, "t1"), ("dentist in Pune", "t1"): page(21, 5),
                         ("hotel in Pune", ""): page(40, 3)})
    http = fake_http(places)
    jid = make(store, "dentist, hotel", sources=("google_places",), start=False)
    assert harvest.get(store, jid)["status"] == "draft"
    assert harvest.step(store, lambda j: env(http)) is None                   # a draft never runs
    harvest.transition(store, jid, "start")
    harvest.step(store, lambda j: env(http))
    job = harvest.get(store, jid)
    assert job["status"] == "running" and job["counts"]["new"] == 20
    harvest.transition(store, jid, "pause")
    assert harvest.step(store, lambda j: env(http)) is None                   # paused: nothing runs
    with pytest.raises(ValueError):
        harvest.transition(store, jid, "start")
    harvest.transition(store, jid, "resume")
    run(store, http)
    job = harvest.get(store, jid)
    assert job["status"] == "done" and job["counts"]["new"] == 28 and job["credits_used"] == 3
    assert [p["status"] for p in job["pairs"]] == ["done", "done"]
    assert harvest.public(job)["progress"]["pct"] == 100
    with pytest.raises(ValueError):
        harvest.transition(store, jid, "cancel")                               # a finished job cannot be cancelled
    other = make(store, "gym", start=False)
    assert harvest.transition(store, other, "cancel")["status"] == "cancelled"
    with pytest.raises(KeyError):
        harvest.transition(store, "nope", "pause")


def test_resume_continues_from_the_saved_page_token(store):
    places = PlacesFake({("dentist in Pune", ""): page(1, 20, "tok-A"), ("dentist in Pune", "tok-A"): page(21, 20, "tok-B"),
                         ("dentist in Pune", "tok-B"): page(41, 4)})
    http = fake_http(places)
    jid = make(store)
    harvest.step(store, lambda j: env(http))
    harvest.transition(store, jid, "pause")
    cur = harvest.get(store, jid)["pairs"][0]["cur"]["google_places"]
    assert cur["token"] == "tok-A" and cur["page"] == 1
    harvest.transition(store, jid, "resume")
    harvest.step(store, lambda j: env(http))
    assert places.bodies[-1]["pageToken"] == "tok-A"
    run(store, http)
    assert [b.get("pageToken", "") for b in places.bodies] == ["", "tok-A", "tok-B"]
    assert harvest.get(store, jid)["counts"]["new"] == 44 and len(leads(store)) == 44


def full_search(prefix="dentist in Pune"):
    return {(prefix, ""): page(1, 20, "p2"), (prefix, "p2"): page(21, 20, "p3"), (prefix, "p3"): page(41, 20, "p4")}


def test_full_search_is_tiled_up_to_max_tiles(store):
    script = full_search()
    script[("dentist", "*")] = page(100, 2)                                   # each tile (rectangle, keyword only)
    places = PlacesFake(script)
    box = {"_list": [{"lat": "18.52", "lon": "73.85", "boundingbox": ["18.4", "18.6", "73.7", "73.95"]}]}
    http = fake_http(places, nominatim=box)
    jid = make(store, max_tiles=9)
    run(store, http)
    tiles = [b for b in places.bodies if "locationRestriction" in b]
    assert len(places.bodies) == 3 + 9 and len(tiles) == 9
    assert len(http.urls("nominatim")) == 1
    job = harvest.get(store, jid)
    assert job["credits_used"] == 12 and job["counts"]["found"] == 60 + 18
    assert job["counts"]["duplicate"] == 16                                   # the same two places in every tile


def test_tiling_stops_at_max_and_not_on_partial_searches(store):
    script = full_search()
    script[("dentist", "*")] = page(100, 20, "more")                          # tiles are never split again: page 2+ follow tokens
    places = PlacesFake(script)
    box = {"_list": [{"lat": "18.5", "lon": "73.8", "boundingbox": ["18.4", "18.6", "73.7", "73.9"]}]}
    jid = make(store, max_tiles=4)
    run(store, fake_http(places, nominatim=box))
    assert len([b for b in places.bodies if "locationRestriction" in b]) <= 4 * hp.PLACES_PAGES
    assert harvest.get(store, jid)["pairs"][0]["cur"]["google_places"]["tiles"].__len__() == 4
    partial = PlacesFake({("gym in Pune", ""): page(200, 20, "x"), ("gym in Pune", "x"): page(220, 7)})
    http = fake_http(partial, nominatim=box)
    make(store, "gym", max_tiles=9)
    run(store, http)
    assert len(partial.bodies) == 2 and http.urls("nominatim") == []          # 27 results: not full, no tiles, no geocode


def test_credit_cap_stops_paid_calls_but_osm_runs(store):
    places = PlacesFake(full_search() | full_search("dentist in Mumbai"))
    overpass = {"elements": [osm_element(1), osm_element(2)]}
    http = fake_http(places, overpass=overpass)
    jid = make(store, "dentist", "Pune\nMumbai", sources=("google_places", "osm"), credit_cap=2)
    run(store, http)
    job = harvest.get(store, jid)
    assert len(places.bodies) == 2 and job["credits_used"] == 2
    assert any("Credit cap of 2" in n for n in job["notes"]) and job["status"] == "done"
    assert len(http.urls("overpass")) == 2                                    # OpenStreetMap still ran for both pairs


def test_dedupe_by_phone_domain_and_name_city_fills_only_empty_fields(store):
    store.write("leads", [
        {"id": "L1", "company": "Old Name Clinic", "contact_name": "", "phone": "+919822000001", "website": "", "timeline": [], "stage": "new",
         "dedupe_key": "phone:+919822000001"},
        {"id": "L2", "company": "Second", "contact_name": "", "website": "https://fakedental2.in", "domain": "fakedental2.in",
         "timeline": [], "stage": "contacted", "dedupe_key": "domain:fakedental2.in"},
        {"id": "L3", "company": "Fake Dental Care 3", "city": "Pune", "contact_name": "", "rating": None, "timeline": [], "stage": "new",
         "dedupe_key": "company:fake dental care 3"},
        {"id": "L4", "company": "Elsewhere", "phone": "+919822000004", "workspace": "w9", "timeline": [], "contact_name": "",
         "dedupe_key": "phone:+919822000099"},
    ])
    p3 = place(3, nationalPhoneNumber="", internationalPhoneNumber="", websiteUri="")
    places = PlacesFake({("dentist in Pune", ""): {"places": [place(1), place(2), p3, place(4)]}})
    jid = make(store)
    run(store, fake_http(places))
    by = {l["id"]: l for l in leads(store)}
    assert by["L1"]["company"] == "Old Name Clinic"                           # never overwritten
    assert by["L1"]["website"] == "https://www.fakedental1.in/" and by["L1"]["rating"] == 4.5   # empty fields filled
    assert jid in by["L1"]["harvest_jobs"] and by["L2"]["stage"] == "contacted" and by["L2"]["phone"] == "+919822000002"
    assert by["L3"]["maps_url"].endswith("1003") and jid in by["L3"]["harvest_jobs"]
    assert "harvest_jobs" not in by["L4"]                                     # another workspace is never matched
    job = harvest.get(store, jid)
    assert job["counts"] == {"found": 4, "new": 1, "duplicate": 3, "dnd": 0, "skipped": 0, "closed": 0}
    results = {r["company"]: r for r in store.read(harvest.RESULTS)}
    assert results["Fake Dental Care 1"]["matched_by"] == "phone" and results["Fake Dental Care 2"]["matched_by"] == "domain"
    assert results["Fake Dental Care 3"]["matched_by"] == "name_city"
    again = make(store)                                                        # a second job over the same area: all duplicates
    run(store, fake_http(places))
    assert harvest.get(store, again)["counts"]["new"] == 0 and len(leads(store)) == 5


def test_new_lead_fields_and_dnd_flagged_not_dropped(store):
    store.write("suppressed", ["someone@example.com", "+919822000002"])
    places = PlacesFake({("dentist in Pune", ""): {"places": [place(1), place(2), place(5, businessStatus="CLOSED_PERMANENTLY")]}})
    jid = make(store, deep_crawl=True)
    run(store, fake_http(places))
    by = {l["company"]: l for l in leads(store)}
    lead = by["Fake Dental Care 1"]
    assert lead["phone"] == "+919822000001" and lead["phone_type"] == "mobile" and lead["phone_line_type"] == "mobile"
    for k, v in {"city": "Pune", "state": "Maharashtra", "pin": "411001", "category": "Dentist", "sector": "Dentist", "rating": 4.5,
                 "reviews_count": 121, "plus_code": "7JCMGV2C+XX", "business_status": "OPERATIONAL", "source": "google_places",
                 "domain": "fakedental1.in", "maps_url": "https://maps.google.com/?cid=1001"}.items():
        assert lead[k] == v, k
    assert lead["harvest_jobs"] == [jid] and "to-enrich" in lead["tags"] and lead["source_id"] == "google_maps"
    assert "workspace" not in lead and not lead.get("dnc")
    assert by["Fake Dental Care 2"]["dnc"] is True                             # on the DND list: kept and flagged
    assert "Fake Dental Care 5" not in by                                      # permanently closed: not saved
    assert harvest.get(store, jid)["counts"] == {"found": 3, "new": 2, "duplicate": 0, "dnd": 1, "skipped": 0, "closed": 1}


def test_landline_and_toll_free_types_and_secondary_phones(store):
    p = place(1, nationalPhoneNumber="020 2553 0001", internationalPhoneNumber="+91 98220 00001")
    q = place(2, nationalPhoneNumber="1800 11 0011", internationalPhoneNumber="")
    jid = make(store, "dentist", sources=("google_places",))
    run(store, fake_http(PlacesFake({("dentist in Pune", ""): {"places": [p, q]}})))
    by = {l["company"]: l for l in leads(store)}
    assert by["Fake Dental Care 1"]["phone"] == "+919822000001" and by["Fake Dental Care 1"]["phones"] == ["+912025530001"]
    assert by["Fake Dental Care 2"]["phone_line_type"] == "toll_free" and by["Fake Dental Care 2"]["phone_type"] == "landline"
    assert harvest.get(store, jid)["status"] == "done"


def test_osm_job_workspace_and_pin_geocode(store):
    http = fake_http(overpass={"elements": [osm_element(1), osm_element(2, phone="bad"), {"type": "node", "id": 5, "tags": {}}]},
                     nominatim={"_list": [{"lat": "18.5", "lon": "73.8", "boundingbox": ["18.4", "18.6", "73.7", "73.9"]}]})
    jid = make(store, "dentist", "411004", sources=("osm",), ws="w2")
    run(store, http)
    made = leads(store)
    assert {l["workspace"] for l in made} == {"w2"} and len(made) == 2
    assert made[0]["source"] == "osm" and made[0]["source_url"].startswith("https://www.openstreetmap.org/node/")
    assert made[0]["phone_line_type"] == "fixed_line" and not made[1].get("phone")
    assert len(http.urls("nominatim")) == 1 and "postalcode=411004" in http.urls("nominatim")[0]
    assert http.calls[-1]["headers"]["User-Agent"].startswith("OutreachOS/1.0")
    assert harvest.get(store, jid)["credits_used"] == 0


def test_rejected_key_stops_google_and_network_errors_retry_then_give_up(store):
    http = fake_http(PermissionError("403"), overpass={"elements": [osm_element(1)]})
    jid = make(store, "dentist", "Pune\nMumbai", sources=("google_places", "osm"))
    run(store, http)
    job = harvest.get(store, jid)
    assert job["google_off"] and len(http.urls("places")) == 1 and len(http.urls("overpass")) == 2 and job["status"] == "done"
    flaky = fake_http(HttpError(503))
    jid2 = make(store, "gym", sources=("google_places",))
    run(store, flaky)
    job2 = harvest.get(store, jid2)
    assert len(flaky.urls("places")) == harvest.MAX_TRIES and job2["pairs"][0]["status"] == "failed"
    assert "failed 3 times" in job2["pairs"][0]["notes"][0] and job2["status"] == "done"


def test_unexpected_error_fails_the_job_only(store, monkeypatch):
    jid = make(store, sources=("osm",))
    monkeypatch.setitem(harvest.RUNNERS, "osm", lambda *a: 1 / 0)
    run(store, fake_http())
    job = harvest.get(store, jid)
    assert job["status"] == "failed" and "ZeroDivisionError" in job["error"]


def test_erased_person_is_counted_skipped(store):
    from app.privacy import hashes_for
    store.write("suppressed_hashes", sorted(hashes_for(None, ["+919822000001"])))
    jid = make(store)
    run(store, fake_http(PlacesFake({("dentist in Pune", ""): {"places": [place(1), place(2)]}})))
    assert harvest.get(store, jid)["counts"]["skipped"] == 1 and len(leads(store)) == 1
