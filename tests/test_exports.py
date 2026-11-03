"""Downloads: columns, filters, formula-injection guard, BOM, IST columns, no secrets, owner-only data."""
import json
import re

import pytest

from app import exports
from app.auth import make_token
from app.main import create_app
from fastapi.testclient import TestClient

from .export_fakes import BOM, SECRETS, header, make_client, read_csv, seed

REQUIRED = {"activity": {"lead_id": "l1"}, "campaign_leads": {"campaign_id": "c1"}, "campaign_runs": {"campaign_id": "c1"}}


@pytest.fixture()
def seeded(tmp_path):
    client, store = make_client(tmp_path)
    info = seed(store)
    return client, store, info


def get(client, dataset, ext="csv", **params):
    return client.get(f"/api/export/{dataset}.{ext}", params={**REQUIRED.get(dataset, {}), **params})


def by(rows, key):
    return {r[key]: r for r in rows}


def test_every_dataset_downloads_with_bom_header_and_dated_name(seeded):
    client, _, _ = seeded
    listed = {d["dataset"]: d for d in client.get("/api/export").json()}
    assert set(listed) == set(exports.DATASETS)
    assert listed["suppressed"]["owner_only"] and not listed["leads"]["owner_only"]
    for dataset in exports.DATASETS:
        r = get(client, dataset)
        assert r.status_code == 200, (dataset, r.text)
        assert r.content.startswith(BOM.encode("utf-8")), dataset
        assert r.headers["content-type"].startswith("text/csv")
        assert header(r) == listed[dataset]["columns"], dataset
        name = re.search(r'filename="([^"]+)"', r.headers["content-disposition"]).group(1)
        assert re.fullmatch(rf"outreach-{dataset.replace('_', '-')}(-[A-Za-z0-9_-]+)?-\d{{4}}-\d{{2}}-\d{{2}}\.csv", name), name
    assert 'filename="outreach-activity-l1-' in get(client, "activity").headers["content-disposition"]


def test_leads_columns_hold_score_consent_sequence_owner_and_linkedin(seeded):
    client, _, _ = seeded
    rows = by(read_csv(get(client, "leads")), "id")
    ravi = rows["l1"]
    assert ravi["band"] == "hot" and int(ravi["score"]) >= 70
    assert (ravi["consent"], ravi["consent_channel"], ravi["consent_proof"]) == ("yes", "whatsapp", "Yes on a call with Priya")
    assert ravi["consent_at"] == "2026-11-02T06:30:00+00:00" and ravi["consent_at_ist"] == "02 Nov 2026, 12:00 PM"
    assert ravi["sequence_status"] == "active" and ravi["emails_sent"] == "2"
    assert ravi["owner"] == "Priya"                          # from the lead's next open task
    assert ravi["linkedin_status"] == "waiting for acceptance"
    assert ravi["tags"] == "vip" and ravi["campaign_id"] == "c1" and ravi["source"] == "apollo"
    assert ravi["created_at_ist"] == "01 Nov 2026, 09:30 AM"
    assert rows["l2"]["consent"] == "no" and rows["l2"]["sequence_status"] == "not started" and rows["l2"]["band"] == "cold"
    assert list(rows)[0] == "l1"                              # highest score first, like the Leads page


@pytest.mark.parametrize("params,expected", [
    ({"stage": "contacted"}, {"l1"}), ({"band": "warm"}, {"l3"}), ({"source": "web_scraper"}, {"l2"}),
    ({"source": "apollo"}, {"l1"}), ({"campaign_id": "c1"}, {"l1", "l3"}), ({"tag": "VIP"}, {"l1"}),
    ({"q": "gamma"}, {"l3", "l4"}), ({"q": "sam@ganesa"}, {"l2"}), ({"ids": "l2,l4"}, {"l2", "l4"}),
    ({"stage": "new", "campaign_id": "c1"}, {"l3"}),
])
def test_lead_filters(seeded, params, expected):
    client, _, _ = seeded
    assert {r["id"] for r in read_csv(get(client, "leads", **params))} == expected


def test_selected_leads_by_post_body(seeded):
    client, _, _ = seeded
    r = client.post("/api/export/leads.csv", json={"ids": ["l1", "l3"]})
    assert r.status_code == 200 and {row["id"] for row in read_csv(r)} == {"l1", "l3"}


def test_every_cell_goes_through_csv_safe(seeded):
    client, _, _ = seeded
    rows = by(read_csv(get(client, "leads")), "id")
    assert rows["l1"]["company"] == "'=HYPERLINK(\"http://evil.example\",\"x\")"
    assert rows["l1"]["phone"] == "'+919876543210"
    assert rows["l2"]["contact_name"] == "'@admin"
    assert rows["l3"]["contact_name"] == "'-Neha"
    outbox = by(read_csv(get(client, "outbox")), "subject")
    assert "'+1 idea" in outbox
    raw = get(client, "leads").content.decode("utf-8")
    assert "\n=" not in raw and ",=" not in raw and ",@" not in raw and ",+" not in raw and ",-" not in raw


def test_indian_names_and_rupee_survive_with_bom(seeded):
    client, _, _ = seeded
    r = get(client, "leads")
    assert r.content[:3] == b"\xef\xbb\xbf"
    assert "Śrī Gaṇeśa Traders ₹" in r.content.decode("utf-8")


def test_outbox_status_filters_body_and_ist(seeded):
    client, _, _ = seeded
    rows = read_csv(get(client, "outbox"))
    assert [r["subject"] for r in rows] == ["'+1 idea", "Follow-up", "Hiring at Acme"]          # newest first
    sent = by(rows, "subject")["Hiring at Acme"]
    assert sent["sent_at_ist"] == "03 Nov 2026, 09:30 AM" and sent["sent_at"] == "2026-11-03T04:00:00+00:00"
    assert (sent["status"], sent["delivered"], sent["delivered_at_ist"]) == ("sent", "yes", "03 Nov 2026, 09:31 AM")
    assert sent["company"].startswith("'=HYPERLINK") and sent["variant"] == "default" and sent["step"] == "Email 1"
    assert "body" not in rows[0]
    assert {r["status"] for r in read_csv(get(client, "outbox", status="practice"))} == {"practice"}
    assert [r["error"] for r in read_csv(get(client, "outbox", status="failed"))] == ["mailbox full"]
    assert [r["subject"] for r in read_csv(get(client, "outbox", status="delivered"))] == ["Hiring at Acme"]
    with_body = read_csv(get(client, "outbox", include_body="1"))
    assert by(with_body, "subject")["Hiring at Acme"]["body"] == "Hi Ravi, full email text"
    assert get(client, "outbox", status="bogus").status_code == 400


def test_inbox_whatsapp_and_transactional(seeded):
    client, _, _ = seeded
    inbox = read_csv(get(client, "inbox"))
    assert [(r["from"], r["matched"], r["label"]) for r in inbox] == [("stranger@x.in", "no", ""), ("ravi@acme.in", "yes", "interested")]
    assert [r["from"] for r in read_csv(get(client, "inbox", matched="yes"))] == ["ravi@acme.in"]
    assert read_csv(get(client, "inbox"))[1]["received_at_ist"] == "03 Nov 2026, 12:30 PM"
    wa = read_csv(get(client, "whatsapp"))
    assert (wa[0]["to"], wa[0]["template"], wa[0]["status"]) == ("'+919876543210", "first_after_yes", "practice")
    assert read_csv(get(client, "whatsapp", status="sent")) == []
    tr = read_csv(get(client, "transactional"))
    assert [r["kind"] for r in tr] == ["invite", "internal"]
    assert [r["subject"] for r in read_csv(get(client, "transactional", kind="internal"))] == ["[Outreach] Call Acme"]


def test_tasks_owner_status_and_bucket(seeded):
    client, _, info = seeded
    rows = by(read_csv(get(client, "tasks")), "id")
    assert set(rows) == {"k1", "k2", "k3"}
    assert (rows["k1"]["bucket"], rows["k2"]["bucket"], rows["k3"]["bucket"]) == ("overdue", "upcoming", "done")
    assert rows["k1"]["due_at"] == info["overdue"] and rows["k1"]["due_at_ist"]
    assert rows["k1"]["company"].startswith("'=HYPERLINK") and rows["k1"]["lead_stage"] == "contacted"
    assert {r["id"] for r in read_csv(get(client, "tasks", owner="Priya"))} == {"k1", "k3"}
    assert {r["id"] for r in read_csv(get(client, "tasks", owner="Priya", status="open"))} == {"k1"}
    assert {r["id"] for r in read_csv(get(client, "tasks", bucket="upcoming"))} == {"k2"}
    assert {r["id"] for r in read_csv(get(client, "tasks", owner="Amit", bucket="overdue"))} == set()
    assert get(client, "tasks", bucket="someday").status_code == 400


def test_activity_needs_a_lead_and_holds_the_full_trail(seeded):
    client, _, _ = seeded
    missing = client.get("/api/export/activity.csv")
    assert missing.status_code == 400 and "lead_id" in missing.json()["detail"]
    assert client.get("/api/export/activity.csv?lead_id=nope").status_code == 404
    rows = read_csv(get(client, "activity"))
    kinds = {r["type"] for r in rows}
    assert {"email", "whatsapp", "call", "task", "meeting"} <= kinds
    email = next(r for r in rows if r["type"] == "email" and r["title"] == "Hiring at Acme")
    assert email["body"] == "Hi Ravi, full email text" and email["direction"] == "out" and email["at_ist"] == "03 Nov 2026, 09:30 AM"
    assert any(r["direction"] == "in" and r["body"] == "Yes, send details" for r in rows)
    assert {r["lead_id"] for r in rows} == {"l1"}


def test_campaign_leads_and_runs(seeded):
    client, _, _ = seeded
    assert client.get("/api/export/campaign_leads.csv").status_code == 400
    assert client.get("/api/export/campaign_runs.csv?campaign_id=zzz").status_code == 404
    leads = by(read_csv(get(client, "campaign_leads")), "id")
    assert set(leads) == {"l1", "l3"}
    assert leads["l1"]["ready_for_email"] == "yes"
    assert leads["l3"]["not_ready_because"] == "no email found yet; score below 40"
    run = read_csv(get(client, "campaign_runs"))[0]
    assert (run["campaign"], run["new"], run["verified_valid"], run["verified_invalid"], run["lead_count"]) == ("Pune BPO", "2", "1", "1", "2")
    assert run["problems"] == "Careers page: site unreachable" and run["started_at_ist"] == "02 Nov 2026, 09:30 AM"


def test_meetings_with_from_to(seeded):
    client, _, _ = seeded
    rows = read_csv(get(client, "meetings"))
    assert [(r["title"], r["start_ist"], r["company"][:2]) for r in rows] == [("Demo with Acme", "05 Nov 2026, 10:00 AM", "'=")]
    assert read_csv(get(client, "meetings", **{"from": "2026-11-06T00:00:00+05:30"})) == []
    assert len(read_csv(get(client, "meetings", **{"from": "2026-11-05T00:00:00+05:30", "to": "2026-11-05T23:00:00+05:30"}))) == 1
    assert get(client, "meetings", **{"from": "tomorrow"}).status_code == 400


def test_duplicates_insights_linkedin(seeded):
    client, _, _ = seeded
    dupes = read_csv(get(client, "duplicates"))
    assert {r["lead_id"] for r in dupes} == {"l3", "l4"} and {r["group"] for r in dupes} == {"1"}
    assert "same email" in dupes[0]["why"] and sorted(r["suggested_keep"] for r in dupes) == ["no", "yes"]
    insights = read_csv(get(client, "insights"))
    lookup = {(r["section"], r["item"], r["metric"]): r["value"] for r in insights}
    assert lookup[("totals", "", "leads")] == "4" and lookup[("totals", "", "emails_sent")] == "3"
    assert lookup[("by_source", "manual", "leads")] == "2" and lookup[("by_band", "hot", "leads")] == "1"
    linkedin = read_csv(get(client, "linkedin_queue"))
    assert [(r["section"], r["lead_id"]) for r in linkedin] == [("waiting for acceptance", "l1")]


def test_json_has_the_same_rows_typed(seeded):
    client, _, _ = seeded
    data = get(client, "leads", "json", stage="contacted").json()
    assert data["dataset"] == "leads" and data["count"] == 1 and data["filters"] == {"stage": "contacted"}
    row = data["rows"][0]
    assert row["consent"] is True and row["tags"] == ["vip"] and row["company"].startswith("=HYPERLINK")
    assert row["consent_at_ist"] == "02 Nov 2026, 12:00 PM" and list(row) == data["columns"]
    r = get(client, "outbox", "json")
    assert 'filename="outreach-outbox-' in r.headers["content-disposition"] and r.json()["count"] == 3
    assert client.get("/api/export/activity.json").status_code == 400


def test_no_secret_ever_appears_in_a_download(seeded):
    client, _, _ = seeded
    for dataset in exports.DATASETS:
        for ext in ("csv", "json"):
            text = get(client, dataset, ext, include_body="1").content.decode("utf-8")
            leaked = [s for s in SECRETS if s in text]
            assert not leaked, (dataset, ext, leaked)


def test_unknown_dataset(seeded):
    client, _, _ = seeded
    assert client.get("/api/export/passwords.csv").status_code == 404


def test_suppressed_is_plain_values_and_owner_only(tmp_path):
    client, store = make_client(tmp_path)
    seed(store)
    rows = read_csv(client.get("/api/export/suppressed.csv"))
    assert [(r["value"], r["type"]) for r in rows] == [("blocked@spam.in", "email"), ("a" * 64, "sha256 hash")]
    app = create_app(data_file=tmp_path / "store.json")

    @app.middleware("http")
    async def as_member(request, call_next):           # what the roles branch will set for a non-owner
        request.state.role = "member"
        return await call_next(request)
    member = TestClient(app)
    assert member.get("/api/export/suppressed.csv").status_code == 403
    assert member.get("/api/export/suppressed.json").status_code == 403
    assert member.get("/api/backup").status_code == 403
    assert member.post("/api/backup/restore", content=b"{}").status_code == 403
    assert member.get("/api/export/leads.csv").status_code == 200


def test_downloads_need_the_login_token(tmp_path):
    client = TestClient(create_app(data_file=tmp_path / "store.json", admin_password="pw"))
    assert client.get("/api/export/leads.csv").status_code == 401
    assert client.get("/api/backup").status_code == 401
    ok = client.get("/api/export/leads.csv", headers={"Authorization": f"Bearer {make_token('pw')}"})
    assert ok.status_code == 200 and ok.content.startswith(BOM.encode("utf-8"))


def test_csv_streams_in_chunks(seeded):
    _, store, _ = seeded
    columns, rows = exports.build(store, "leads", {}, lambda lead: {**lead, "score": 1, "band": "cold"})
    chunks = list(exports.csv_chunks(columns, rows * 400))
    assert len(chunks) > 2 and chunks[0].startswith(BOM) and json.dumps(chunks[0]).count("\\r\\n") == 1
