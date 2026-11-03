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


# ---------- fixes made after the merge ----------
from datetime import datetime, timezone

from app.inbound import process_delivery_event, process_inbound_message
from tests.qa.inbound_replies_support import app_with_leads, leads_by_email, seed_outbox


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def test_bounce_notice_from_the_reply_poller_counts_toward_the_bounce_pause(tmp_path):
    _, store = app_with_leads(tmp_path)
    ids = seed_outbox(store, sent_at=_now())
    emails = list(ids)[:4]
    store.update("outbox", lambda r: [*r, *({"lead_id": "x", "to": f"s{i}@f{i}.example", "status": "sent", "sent_at": _now()}
                                            for i in range(100))])
    for n, email in enumerate(emails[:3]):             # 3 DSNs found in the mailbox: 3/100 = 3% > 2%
        out = process_inbound_message(store, {"channel": "email", "from_email": "mailer-daemon@googlemail.com",
                                              "subject": "Delivery Status Notification (Failure)", "message_id": f"<dsn{n}@x>",
                                              "in_reply_to": ids[email], "raw_id": f"dsn{n}",
                                              "text": f"Delivery to {email} failed. 550 5.1.1 user unknown. Address not found."})
        assert out.get("label") == "bounce", out
    events = [e for e in store.read("delivery_events") if e["type"] == "bounce"]
    assert len(events) >= 1, "IMAP/inbound bounce wrote no delivery_events"
    assert {e["email"] for e in events} == set(emails[:3])
    assert store.read("settings").get("sending_paused"), "3 bounces from ~100+ sends should pause live sending"


def test_marking_notifications_read_for_one_owner_leaves_the_others_unread(tmp_path):
    c, store = app_with_leads(tmp_path)
    store.update("notifications", lambda r: [{"id": "a", "to": "Priya", "read": False}, {"id": "b", "to": "Rahul", "read": False},
                                              {"id": "c", "to": None, "read": False}])
    assert c.post("/api/notifications/read?owner=Priya").status_code == 200
    state = {n["id"]: n["read"] for n in store.read("notifications")}
    assert state == {"a": True, "b": False, "c": True}          # Rahul's stays unread; the broadcast one is read
    c.post("/api/notifications/read")                            # no owner on the no-password local app: everything, as before
    assert all(n["read"] for n in store.read("notifications"))


def test_enroll_lead_rechecks_the_suppression_list_on_the_latest_copy(tmp_path):
    from app.routes_outreach import EnrollRefused, enroll_lead
    _, store = app_with_leads(tmp_path)
    lead = store.read("leads")[0]
    real = store.update_lead

    def unsubscribed_meanwhile(lead_id, change):             # the address is suppressed between the first check and the save
        store.update("suppressed", lambda rows: [*rows, lead["email"]])
        return real(lead_id, change)
    store.update_lead = unsubscribed_meanwhile
    with pytest.raises(EnrollRefused, match="suppression"):
        enroll_lead(store, lead["id"])
    assert not store.read("leads")[0].get("sequence")


@pytest.mark.parametrize("host", ["172.16.0.5", "172.20.1.1", "172.31.255.1", "crm.local", "app.internal", "box.localhost",
                                  "10.1.2.3", "192.168.1.9", "169.254.169.254", "127.0.0.2", "intranet"])
def test_private_addresses_are_not_a_public_web_address(tmp_path, host):
    from app.routes_outreach import live_blockers
    from app.routes_setup import _public_url_ok
    _, store = app_with_leads(tmp_path)
    store.update("settings", lambda s: {**s, "public_base_url": f"https://{host}", "sender_company": "Acme", "sender_address": "Pune"})
    assert any("public https web address" in m for m in live_blockers(store, "https://" + host))
    assert not _public_url_ok(f"https://{host}")


@pytest.mark.parametrize("host", ["outreach.example.com", "172.15.0.1", "172.32.0.1", "93.184.216.34"])
def test_public_addresses_are_still_accepted(tmp_path, host):
    from app.routes_outreach import live_blockers
    from app.routes_setup import _public_url_ok
    _, store = app_with_leads(tmp_path)
    store.update("settings", lambda s: {**s, "public_base_url": f"https://{host}"})
    assert not any("public https web address" in m for m in live_blockers(store, "https://" + host))
    assert _public_url_ok(f"https://{host}")
