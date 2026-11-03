"""Canonical lead sources: every legacy value maps to one id, backfill is lazy and idempotent, and the numbers add up."""
import pytest

from app import source_names as sn
from app.lead_merge import merge_leads
from app.lead_model import build_lead, merge_contact

from .capture_helpers import add_connector, leads, make_client, post_lead

LEGACY = {
    "apollo": "apollo", "apollo_search": "apollo", "apollo_hiring": "apollo", "google_maps": "google_maps",
    "google_places": "google_maps", "lusha": "lusha", "zoominfo": "zoominfo", "hunter": "hunter", "snov": "snov",
    "directory": "directory", "bmi_sheet": "bmi_sheet", "bmi_sheet:demo": "bmi_sheet", "bmi_sheet:leads": "bmi_sheet",
    "bmi_demo_sheet": "bmi_sheet", "sheet": "sheet", "web_scraper": "web_scraper", "web_scraper:https://acme.in/contact": "web_scraper",
    "referral": "referral", "referral:ravi": "referral", "job_board": "job_board", "sales_navigator": "sales_navigator",
    "manual": "manual", "careers_page": "careers_page", "meta_ads": "meta_ads", "google_ads": "google_ads",
    "website_form": "website_form", "whatsapp_click": "whatsapp_click", "linkedin_leadgen": "linkedin_leadgen",
    "zapier_webhook": "zapier_webhook", "indiamart": "indiamart", "justdial": "justdial", "tradeindia": "tradeindia",
    "tiktok_ads": "tiktok_ads", "sheet_sync": "sheet_sync", "other_inbound": "other_inbound", "Apollo": "apollo",
    "enrichment": "enrichment", "contact_scraper": "web_scraper", "": "unknown", None: "unknown",
}


@pytest.mark.parametrize("raw,expected", sorted(LEGACY.items(), key=lambda kv: str(kv[0])))
def test_every_legacy_source_maps_to_a_registry_id(raw, expected):
    sid, _ = sn.canonical(raw)
    assert sid == expected
    assert sid in sn.BY_ID


def test_detail_suffix_is_split_off_and_unknown_text_is_kept():
    assert sn.canonical("web_scraper:https://acme.in/contact") == ("web_scraper", "https://acme.in/contact")
    assert sn.canonical("bmi_sheet:demo") == ("bmi_sheet", "demo")
    assert sn.canonical("some odd list") == ("unknown", "some odd list")
    assert sn.label_of("apollo_search") == "Apollo" and sn.label_of("google_maps") == "Google Maps"
    assert sn.label_of("some odd list") == "Some odd list"
    assert sn.kind_of("meta_ads") == "inbound" and sn.kind_of("apollo") == "outbound" and sn.kind_of("manual") == "manual"


def test_registry_endpoint_lists_every_inbound_source(tmp_path):
    client, _ = make_client(tmp_path)
    body = client.get("/api/lead-sources").json()
    ids = {s["id"] for s in body["sources"]}
    assert {"meta_ads", "google_ads", "website_form", "whatsapp_click", "linkedin_leadgen", "zapier_webhook", "indiamart",
            "justdial", "tradeindia", "tiktok_ads", "sheet_sync", "other_inbound", "apollo", "manual"} <= ids
    assert set(body["inbound"]) >= {"meta_ads", "google_ads"} and "apollo_search" not in ids
    assert all(s["kind"] in body["kinds"] and s["label"] for s in body["sources"])


def test_backfill_adds_source_id_without_touching_source_and_is_idempotent(tmp_path):
    client, store = make_client(tmp_path)
    old = [{**build_lead({"company": f"Co{i}", "email": f"a{i}@co{i}.in"}, raw), "source": raw}
           for i, raw in enumerate(["apollo_search", "bmi_sheet:demo", "web_scraper:https://x.in", "mystery"])]
    for lead in old:
        lead.pop("source_id")
    store.write("leads", old)
    assert sn.backfill(store) == 4
    saved = store.read("leads")
    assert [l["source"] for l in saved] == ["apollo_search", "bmi_sheet:demo", "web_scraper:https://x.in", "mystery"]
    assert [l["source_id"] for l in saved] == ["apollo", "bmi_sheet", "web_scraper", "unknown"]
    before = store.path.read_bytes()
    assert sn.backfill(store) == 0 and store.path.read_bytes() == before      # second run: nothing changes, nothing is saved


def test_api_presents_source_id_even_for_an_unfilled_lead(tmp_path):
    client, store = make_client(tmp_path)
    lead = build_lead({"company": "Old Co", "email": "a@old.in"}, "apollo_search")
    lead.pop("source_id")
    store.write("leads", [lead])
    assert client.get("/api/leads").json()[0]["source_id"] == "apollo"
    assert "source_id" not in store.read("leads")[0]


def test_source_history_grows_when_a_lead_is_seen_again_from_another_source():
    first = build_lead({"company": "Acme", "email": "a@acme.in"}, "apollo_search")
    other = build_lead({"company": "Acme", "email": "a@acme.in", "phone": "9810012345"}, "google_places")
    merged = merge_contact(first, other)
    assert merged["source"] == "apollo_search"
    assert [t["id"] for t in merged["sources"]] == ["apollo", "google_maps"]
    assert merge_contact(merged, other)["sources"] == merged["sources"]         # the same source is not added twice


def test_merging_two_leads_keeps_every_source():
    a = build_lead({"company": "Acme", "email": "a@acme.in"}, "apollo")
    b = {**build_lead({"company": "Acme Ltd", "phone": "9810012345"}, "manual"), "sources": [{"id": "manual", "at": "x", "detail": ""}]}
    merged = merge_leads(a, [b], "2026-11-03T00:00:00+00:00")
    assert {t["id"] for t in merged["sources"]} == {"apollo", "manual"}


def test_import_merge_appends_a_source_touch_and_keeps_the_primary(tmp_path):
    client, store = make_client(tmp_path)
    client.post("/api/leads/import", json={"csv": "company,email\nAcme,a@acme.in\n", "source": "apollo"})
    client.post("/api/leads/import", json={"csv": "company,email,phone\nAcme,a@acme.in,9810012345\n", "source": "manual"})
    # the quick paste skips an exact duplicate; the smart importer merges and records the touch
    csv = "Company,Email,Phone\nAcme,a@acme.in,98100 12345\n"
    preview = client.post("/api/import/preview", json={"csv": csv, "source": "directory"}).json()
    assert preview
    r = client.post("/api/import/commit", json={"csv": csv, "source": "directory"})
    assert r.status_code == 200, r.text
    lead = leads(store)[0]
    assert lead["source"] == "apollo" and lead["source_id"] == "apollo"
    assert "directory" in [t["id"] for t in lead["sources"]]


def test_dashboard_by_source_counts_and_ignores_test_leads(tmp_path):
    client, store = make_client(tmp_path)
    mk = lambda i, src, stage="new", **extra: {**build_lead({"company": f"C{i}", "email": f"p{i}@c{i}.in"}, src), "stage": stage, **extra}
    store.write("leads", [mk(1, "apollo_search"), mk(2, "apollo", "customer"), mk(3, "meta_ads", "interested", inbound=True),
                          mk(4, "meta_ads", inbound=True), mk(5, "manual"), mk(6, "meta_ads", is_test=True, inbound=True)])
    d = client.get("/api/dashboard").json()
    assert d["total_leads"] == 5
    rows = {r["source"]: r for r in d["by_source"]}
    assert rows["apollo"]["leads"] == 2 and rows["apollo"]["customers"] == 1
    assert rows["meta_ads"]["leads"] == 2 and rows["meta_ads"]["inbound"] == 2 and rows["meta_ads"]["interested"] == 1
    assert rows["meta_ads"]["new_7d"] == 2 and rows["meta_ads"]["label"] == "Facebook / Instagram ads"
    assert rows["manual"]["kind"] == "manual" and "inbound_7d" in d and "inbound_today" in d
    assert [r["leads"] for r in d["by_source"]] == sorted((r["leads"] for r in d["by_source"]), reverse=True)


def test_leads_export_filters_by_canonical_source_and_inbound_only(tmp_path):
    client, store = make_client(tmp_path)
    mk = lambda i, src, **extra: {**build_lead({"company": f"C{i}", "email": f"p{i}@c{i}.in"}, src), **extra}
    store.write("leads", [mk(1, "apollo_search"), mk(2, "apollo"), mk(3, "meta_ads", inbound=True), mk(4, "manual")])
    names = lambda **q: sorted(l["company"] for l in __import__("csv").DictReader(
        __import__("io").StringIO(client.get("/api/export/leads.csv", params=q).text.lstrip("﻿"))))
    assert names(source="apollo") == ["C1", "C2"]
    assert names(source="meta_ads,manual") == ["C3", "C4"]
    assert names(inbound="1") == ["C3"]


def test_insights_use_canonical_sources_stage_counts_and_spend(tmp_path):
    client, store = make_client(tmp_path)
    mk = lambda i, src, stage="new", **extra: {**build_lead({"company": f"C{i}", "email": f"p{i}@c{i}.in"}, src), "stage": stage, **extra}
    store.write("leads", [mk(1, "meta_ads", "customer"), mk(2, "meta_ads"), mk(3, "meta_ads", "interested"), mk(4, "apollo_search"),
                          mk(5, "meta_ads", is_test=True)])
    assert client.put("/api/insights/source-spend", json={"spend": {"meta_ads": 3000}}).json()["source_spend"] == {"meta_ads": 3000}
    ins = client.get("/api/insights").json()
    meta = next(r for r in ins["by_source"] if r["source"] == "meta_ads")
    assert meta["leads"] == 3 and meta["label"] == "Facebook / Instagram ads" and meta["stages"]["customer"] == 1
    assert meta["cost_per_lead"] == 1000.0 and meta["cost_per_customer"] == 3000.0
    assert next(r for r in ins["by_source"] if r["source"] == "apollo")["cost_per_lead"] is None
    assert ins["totals"]["leads"] == 4
    assert client.put("/api/insights/source-spend", json={"spend": {"meta_ads": "abc"}}).status_code == 400
