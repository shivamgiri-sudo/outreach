"""Integration checks added while merging the QA branches: CRM opt-out detection must not over-trigger, and the five
import fixture families (Apollo, data vendors, India sources, LinkedIn tools, messy/unified) go into ONE store in turn."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.header_map import detect_format, map_columns
from app.lead_import import read_table, run_import
from app.main import create_app

FIX = Path(__file__).parent / "fixtures"
TODAY = __import__("datetime").date(2026, 11, 3)


def _run(csv_text):
    headers, table = read_table(csv_text)
    fmt = detect_format(headers)
    columns = map_columns(headers, [r for _, r in table[:25]], fmt)
    leads = []
    return leads, run_import(leads, table, columns, fmt["source"], set(), {}, today=TODAY)


def test_only_the_opt_out_column_with_a_yes_value_suppresses():
    csv_text = ("First Name,Last Name,Email,Company,Email Opt Out,Notes\n"
                "Asha,Rao,asha@optout.example,Optout Co,true,\n"                      # real opt-out: skipped
                "Bunty,Singh,bunty@fine.example,Fine Co,false,\n"                     # explicit no: imported
                "Chitra,Nair,chitra@notes.example,Notes Co,,customer opted out of the brochure but wants a call\n"   # free text elsewhere
                "Dev,Patel,dev@blank.example,Blank Co,,\n")
    leads, out = _run(csv_text)
    emails = {e for lead in leads for e in ([lead.get("email")] + [c.get("email") for c in lead.get("other_contacts") or []]) if e}
    assert out["source_opt_outs"] == ["asha@optout.example"]
    assert "asha@optout.example" not in emails
    assert {"bunty@fine.example", "chitra@notes.example", "dev@blank.example"} <= emails


def test_a_column_that_only_looks_like_an_opt_out_does_not_suppress():
    csv_text = ("First Name,Last Name,Email,Company,Opted In,Unsubscribe Reason\n"
                "Esha,Gupta,esha@keep.example,Keep Co,true,\n"
                "Farid,Khan,farid@keep2.example,Keep Two Co,yes,none\n")
    leads, out = _run(csv_text)
    assert not out.get("source_opt_outs")
    assert len(leads) == 2


FAMILIES = [
    "import-apollo/apollo_accounts_export.csv", "import-apollo/apollo_people_export.csv",
    "import-data-vendors/lusha_prospecting.csv", "import-data-vendors/zoominfo_contacts.csv",
    "import-data-vendors/hubspot_contacts.csv", "import-data-vendors/salesforce_leads_report.csv",
    "import-india-sources/indiamart_leads.csv", "import-india-sources/justdial_apify.csv",
    "import-india-sources/naukri_apify.csv", "import-india-sources/apna.csv",
    "import-linkedin-tools/phantombuster_profile_scraper.csv", "import-linkedin-tools/evaboot_salesnav_list.csv",
    "import-linkedin-tools/lemlist_leads.csv", "import-messy-unified/1_apollo.csv", "import-messy-unified/3_lusha.csv",
    "import-messy-unified/5_indiamart.csv", "import-messy-unified/6_team_leads.csv",
]


def test_five_fixture_families_imported_in_turn_into_one_store(tmp_path):
    client = TestClient(create_app(data_file=tmp_path / "d.json"))
    rows_total = 0
    for name in FAMILIES:
        text = (FIX / name).read_bytes().decode("utf-8-sig")
        rows_total += len(read_table(text)[1])
        r = client.post("/api/import/commit", json={"csv": text})
        assert r.status_code == 200, (name, r.text[:300])
        assert r.json()["summary"] is not None
    leads = client.get("/api/leads").json()
    assert 100 < len(leads) <= rows_total
    keys = [l.get("dedupe_key") for l in leads]
    assert len(keys) == len(set(keys))                          # no exact duplicate keys after six-plus overlapping sources
    assert all(l.get("id") for l in leads)
    # every lead kept at least one way to reach it or a name; none were created empty
    assert all(l.get("company") or l.get("contact_name") or l.get("email") or l.get("phone") for l in leads)
