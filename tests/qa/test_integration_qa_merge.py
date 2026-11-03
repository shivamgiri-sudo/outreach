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


def test_a_whatsapp_stop_unsubscribes_every_lead_that_shares_the_phone(tmp_path):
    _, store = app_with_leads(tmp_path)
    first = store.read("leads")[0]
    twin = {**first, "id": "twin0001", "company": "Twin Co", "email": "twin@twin.example", "dedupe_key": "twin-co", "timeline": []}
    stranger = {**first, "id": "other001", "company": "Other Co", "email": "o@other.example", "dedupe_key": "other-co",
                "phone": "+919000099999", "phones": [], "timeline": []}
    first_phone = first.get("phone")
    assert first_phone
    store.update("leads", lambda rows: [*rows, twin, stranger])
    out = process_inbound_message(store, {"channel": "whatsapp", "provider": "meta", "from_phone": first_phone, "text": "STOP",
                                          "message_id": "wamid.1"})
    assert out["matched"] and out["label"] == "unsubscribe"
    stages = {l["id"]: l["stage"] for l in store.read("leads")}
    assert stages[first["id"]] == "unsubscribed" and stages["twin0001"] == "unsubscribed"
    assert stages["other001"] != "unsubscribed"


def test_same_day_meeting_gets_no_24h_reminder_and_never_says_tomorrow(tmp_path):
    from datetime import timedelta
    from app.reminders import _when_phrase, run_reminders
    _, store = app_with_leads(tmp_path)
    lead = store.read("leads")[0]
    now = datetime(2026, 11, 20, 4, 30, tzinfo=timezone.utc)                        # Fri 10:00 IST
    start = now + timedelta(hours=5)                                                 # booked 5 hours ahead: same day
    store.update_lead(lead["id"], lambda l: {**l, "meetings": [{"id": "m1", "status": "scheduled", "start": start.isoformat(),
                                                                   "title": "Demo", "attendee_email": lead["email"], "reminders_sent": []}]})
    store.update("settings", lambda s: {**s, "live_sending": True})
    sent = []
    run_reminders(store, now, lambda m: sent.append(m) or {"status": "sent"})
    assert [m for m in sent if m["kind"] == "meeting_reminder"] == []              # no "24h" mail for a call inside 24h
    run_reminders(store, start - timedelta(minutes=50), lambda m: sent.append(m) or {"status": "sent"})
    mails = [m for m in sent if m["kind"] == "meeting_reminder"]
    assert len(mails) == 1 and "tomorrow" not in mails[0]["body"] and "in about an hour" in mails[0]["body"]
    assert _when_phrase(start.isoformat(), "24h", now) == "later today"
    assert _when_phrase((now + timedelta(hours=24)).isoformat(), "24h", now) == "tomorrow"


def test_fetch_page_refuses_a_redirect_that_lands_on_linkedin(monkeypatch):
    from app.channels import netguard
    from app.scraper import fetch_page
    seen = []

    def fake(url, method, headers, body, timeout, max_bytes, resolver, allow_http):
        seen.append(url)
        if "short.example" in url:
            return netguard.Response(302, {"location": "https://www.linkedin.com/company/acme"}, b"", url)
        return netguard.Response(200, {}, b"<html>ok</html>", url)
    monkeypatch.setattr(netguard, "_one_request", fake)
    with pytest.raises(PermissionError, match="LinkedIn"):
        fetch_page("https://short.example/acme", resolver=lambda h: ["93.184.216.34"])
    assert not any("linkedin.com" in u for u in seen)            # never even requested
    assert "ok" in fetch_page("https://plain.example/", resolver=lambda h: ["93.184.216.34"])


def test_whatsapp_template_parameters_follow_what_the_template_defines():
    from app.whatsapp import template_params
    v = {"first_name": "Asha", "sender": "Priya", "company": "Acme", "demo_link": "https://x.example/demo/t"}
    full = ["Asha", "Priya", "Acme", "https://x.example/demo/t"]
    assert template_params("first_after_yes", v) == full                      # unconfigured: the four-value convention, as before
    assert template_params("something_unknown", v) == full
    assert template_params("nudge_not_published", v, {"nudge_not_published": 2}) == ["Asha", "Priya"]     # settings: a count
    assert template_params("first_after_yes", v, {"first_after_yes": 3}) == full[:3]
    assert template_params("first_after_yes", v, {"first_after_yes": ["first_name", "demo_link"]}) == ["Asha", "https://x.example/demo/t"]
    assert template_params("first_after_yes", v, {"first_after_yes": ["bogus"]}) == full                  # junk is ignored
    assert template_params("nudge_not_published", {}, {"nudge_not_published": 2}) == ["there", ""]
    import app.whatsapp as wa
    wa.TEMPLATES["numbered_test"] = "Hi {{1}}, from {{2}}. Reply STOP to opt out."        # a template that numbers its values
    try:
        assert template_params("numbered_test", v) == ["Asha", "Priya"]
    finally:
        del wa.TEMPLATES["numbered_test"]


def test_whatsapp_send_passes_only_the_declared_parameters(tmp_path):
    from app.whatsapp import send
    _, store = app_with_leads(tmp_path)
    lead = store.read("leads")[0]
    store.update_lead(lead["id"], lambda l: {**l, "consent": {"given": True, "at": "2026-11-01T00:00:00+00:00"}} if False else {**l, "consent": True})
    store.update("settings", lambda s: {**s, "whatsapp_template_params": {"nudge_not_published": 2}})
    seen = {}
    import app.whatsapp as wa
    real = wa.send_whatsapp
    wa.send_whatsapp = lambda store_, phone, name, lang, params, text=None, transport=None: seen.update(params=params, name=name) or {"status": "dry_run"}
    try:
        send(store, lead["id"], "nudge_not_published", "", datetime.now(timezone.utc), "https://app.example")
    finally:
        wa.send_whatsapp = real
    assert len(seen["params"]) == 2 and seen["name"] == "nudge_not_published"


def test_crm_closed_statuses_never_promote_a_lead_but_are_kept_as_a_note(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    csv_text = ("First Name,Last Name,Email,Company,Lead Status\n"
                "Asha,Rao,asha@won.example,Won Co,Closed Won\nBunty,Singh,b@cust.example,Cust Co,Customer\n")
    assert c.post("/api/import/commit", json={"csv": csv_text}).status_code == 200
    leads = {l["company"]: l for l in c.get("/api/leads").json()}
    assert leads["Won Co"]["stage"] == "new" and leads["Cust Co"]["stage"] == "new"          # never auto-promoted to customer
    assert any("Closed Won" in t["text"] for t in leads["Won Co"]["timeline"])               # the CRM status is not lost
    assert any("Customer" in t["text"] for t in leads["Cust Co"]["timeline"])
