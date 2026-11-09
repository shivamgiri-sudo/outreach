"""Sales funnel through the app: the lead's sales stage, the dashboard numbers, drill-down, settings, permissions, account
scoping, and the import / sheet / Meta paths that fill the record. Pure rules are in test_sales_funnel.py."""
import time

import pytest

from app import sales_model as M
from tests.capture_helpers import add_connector, lead_by_email, make_client
from tests.limits_helpers import BOSS, LONG, bearer, make_rig
from tests.test_inbound_platforms import meta_setup, notification, signed

SQL, MQL = M.STAGES[3]["status"], M.STAGES[1]["status"]
CSV_MAIN = "company,contact_name,email\nMain Co,Mo,mo@main.example\nMain Two,Mia,mia@main2.example\n"
CSV_B = "company,contact_name,email\nBravo One,Ann,ann@bravo1.example\nBravo Two,Bob,bob@bravo2.example\nBravo Three,Cy,cy@bravo3.example\n"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    r = make_rig(tmp_path, monkeypatch)
    c = r["c"]
    assert c.post("/api/leads/import", json={"csv": CSV_MAIN}, headers=r["ho"]).status_code == 200
    r["main_ids"] = [l["id"] for l in c.get("/api/leads", headers=r["ho"]).json() if l["email"].endswith("main.example") or l["email"].endswith("main2.example")]
    assert c.post("/api/leads/import", json={"csv": CSV_B}, headers=r["hb"]).status_code == 200
    r["b_ids"] = [l["id"] for l in c.get("/api/leads", headers=r["hb"]).json()]
    return r


def put(r, lead_id, body, h=None):
    return r["c"].put(f"/api/leads/{lead_id}/sales", json=body, headers=h or r["hb"])


def funnel(r, h=None, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return r["c"].get("/api/funnel" + (f"?{q}" if q else ""), headers=h or r["hb"])


def stage_counts(data):
    return [s["count"] for s in data["funnel"]]


# ---------------------------------------------------------------- the lead's sales stage and its rules
def test_a_new_lead_reads_as_inquiry_and_the_rules_answer_in_plain_words(rig):
    lid = rig["b_ids"][0]
    d = rig["c"].get(f"/api/leads/{lid}/sales", headers=rig["hb"]).json()
    assert d["status"] == M.INQUIRY and d["history"] == [] and d["statuses"][0] == M.INQUIRY
    r = put(rig, lid, {"status": SQL})
    assert r.status_code == 400 and "Est. Monthly Value" in r.json()["detail"]
    r = put(rig, lid, {"status": "Proposal Submitted", "est_value": 5000})
    assert r.status_code == 400 and "Meeting Held Date" in r.json()["detail"]
    assert put(rig, lid, {"status": M.LOST}).json()["detail"].startswith("Pick a Lost Reason")
    assert "Closed Monthly Value" in put(rig, lid, {"status": M.WON, "est_value": 5000}).json()["detail"]
    assert put(rig, lid, {"status": "Banana"}).status_code == 400
    assert put(rig, lid, {"est_value": -4}).status_code == 400
    assert put(rig, lid, {"lost_reason": "Bored"}).status_code == 400
    saved = next(l for l in rig["store"].read("leads") if l["id"] == lid)
    assert "sales" not in saved                                                  # every refusal left the lead untouched


def test_moving_to_sql_back_fills_skipped_stages_records_history_timeline_and_audit(rig):
    lid = rig["b_ids"][0]
    r = put(rig, lid, {"status": SQL, "est_value": 150000})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == SQL and d["prob"] == 20 and d["backfilled"] == ["MQL", "SAL"] and d["dates"]["sql"] == d["dates"]["mql"]
    assert d["history"][0]["from"] == M.INQUIRY and d["history"][0]["to"] == SQL and d["history"][0]["by"] == "Bravo Admin"
    lead = next(l for l in rig["store"].read("leads") if l["id"] == lid)
    assert lead["stage"] == "new"                                               # today's outreach stage is untouched
    assert any(e["kind"] == "sales" and SQL in e["text"] for e in lead["timeline"])
    rows = [a for a in rig["store"].read("audit") if a.get("action") == "sales.stage"]
    assert rows and rows[-1]["account_id"] == rig["b"] and lid in rows[-1]["detail"]
    # the same status again changes nothing and adds no history
    assert put(rig, lid, {"status": SQL}).json()["history"].__len__() == 1


def test_won_needs_value_and_tenure_defaults_to_12_and_a_lost_deal_can_be_won_later(rig):
    lid = rig["b_ids"][0]
    put(rig, lid, {"status": "Discovery Meeting Held", "est_value": 100000})
    r = put(rig, lid, {"status": M.WON, "closed_value": 120000})
    assert r.status_code == 200 and r.json()["tenure"] == 12 and r.json()["tcv"] == 1440000 and r.json()["prob"] == 100
    other = rig["b_ids"][1]
    put(rig, other, {"status": M.LOST, "lost_reason": "Price"})
    d = rig["c"].get(f"/api/leads/{other}/sales", headers=rig["hb"]).json()
    assert d["status"] == M.LOST and d["lost_reason"] == "Price" and d["prob"] == 0
    lost_closed = d["dates"]["closed"]
    time.sleep(1.1)
    won = put(rig, other, {"status": M.WON, "closed_value": 5000, "est_value": 6000, "tenure": 6})
    assert won.status_code == 200 and won.json()["tcv"] == 30000 and won.json()["dates"]["closed"] > lost_closed
    assert [h["to"] for h in won.json()["history"]][:2] == [M.WON, M.LOST]


def test_viewer_cannot_change_a_sales_stage_but_can_read_the_funnel(rig):
    c, ho = rig["c"], rig["ho"]
    c.post("/api/users", json={"email": "view@bravo.com", "name": "Bravo Viewer", "role": "viewer", "password": LONG, "workspaces": [rig["ws"]],
                               "account_id": rig["b"]}, headers=ho)
    hv = bearer(c.post("/api/login", json={"email": "view@bravo.com", "password": LONG}).json()["token"])
    assert put(rig, rig["b_ids"][0], {"status": MQL}, hv).status_code == 403
    assert funnel(rig, hv).status_code == 200
    assert put(rig, rig["b_ids"][0], {"status": MQL}, rig["hc"]).status_code == 200      # a caller may


def test_a_login_cannot_touch_or_see_another_accounts_lead(rig):
    other = rig["main_ids"][0]
    assert put(rig, other, {"status": MQL}, rig["hb"]).status_code == 404
    assert rig["c"].get(f"/api/leads/{other}/sales", headers=rig["hb"]).status_code == 404
    assert put(rig, rig["b_ids"][0], {"status": MQL}, rig["ho"]).status_code == 200      # owners can


# ---------------------------------------------------------------- the dashboard
def test_funnel_counts_only_the_leads_the_login_may_see_and_totals_match_the_lead_list(rig):
    for lid in rig["b_ids"][:2]:
        put(rig, lid, {"status": SQL, "est_value": 1000})
    b = funnel(rig).json()
    assert stage_counts(b)[0] == 3 and stage_counts(b)[3] == 2 and b["range"]["label"].startswith("As on ")
    assert b["status"]["total"] == 3
    allv = funnel(rig, rig["ho"]).json()
    assert stage_counts(allv)[0] == 5                                           # the owner sees both accounts
    only_main = rig["c"].get("/api/funnel", headers={**rig["ho"], "X-Account": "mcn"}).json()
    assert stage_counts(only_main)[0] == 2
    assert funnel(rig, rig["hc"]).json()["funnel"][0]["count"] == 3


def test_every_number_has_a_drill_down_with_exactly_that_many_leads(rig):
    for i, lid in enumerate(rig["b_ids"]):
        put(rig, lid, {"status": [SQL, M.STAGES[4]["status"], MQL][i], "est_value": 1000 * (i + 1)})
    put(rig, rig["b_ids"][2], {"status": M.WON, "closed_value": 777, "est_value": 800})
    d = funnel(rig).json()
    keys = [(s["label"], s["drill"], s["count"]) for s in d["funnel"]]
    keys += [(t["label"], t["drill"], t["value"] if not t["money"] else None) for t in d["snapshot"]]
    keys += [(f"pipeline {p['label']}", p["drill"], p["count"]) for p in d["pipeline"]]
    keys += [(f"source {s['source']}", s["drill"], s["counts"][0]) for s in d["sources"]]
    for label, key, n in keys:
        r = rig["c"].get("/api/funnel/drill", params={"key": key}, headers=rig["hb"]).json()
        assert r["count"] == len(r["rows"]) and (n is None or r["count"] == n), label
        assert all({"id", "company", "owner", "stage", "value"} <= set(row) for row in r["rows"])
    r = rig["c"].get("/api/funnel/drill", params={"key": "funnel.0"}, headers=rig["hb"]).json()
    assert {row["id"] for row in r["rows"]} == set(rig["b_ids"])
    assert rig["c"].get("/api/funnel/drill", params={"key": "nope"}, headers=rig["hb"]).status_code == 404
    # a client never gets another account's lead through a drill key
    assert not ({row["id"] for row in r["rows"]} & set(rig["main_ids"]))


def test_closed_won_revenue_follows_the_closed_date_and_dates_are_validated(rig):
    lid = rig["b_ids"][0]
    put(rig, lid, {"status": M.WON, "closed_value": 90000, "est_value": 100000})
    tiles = funnel(rig).json()["snapshot"]
    assert tiles[5]["value"] == 90000 and tiles[4]["value"] == 1
    month = funnel(rig).json()["revenue"]["byMonth"]
    assert len(month) == 1 and month[0]["value"] == 90000
    assert funnel(rig, **{"from": "2026-05-01", "to": "2026-05-02"}).json()["snapshot"][5]["value"] == 0     # not closed then
    bad = funnel(rig, **{"from": "2026-11-05", "to": "2026-11-01"})
    assert bad.status_code == 400 and bad.json()["detail"] == "From Date cannot be after To Date"
    assert funnel(rig, **{"from": "2999-01-01"}).status_code == 400
    assert funnel(rig, **{"from": "2026-11-01"}).json()["range"]["label"].startswith("From 01-Nov-2026 to today")


def test_filters_multi_select_and_owner_targets(rig):
    c = rig["c"]
    for lid, owner in zip(rig["b_ids"], ["Bravo Caller", "Bravo Caller", "Bravo Admin"]):
        rig["store"].update_lead(lid, lambda l, o=owner: {**l, "owner": o})
    d = funnel(rig, owner="Bravo Caller").json()
    assert stage_counts(d)[0] == 2 and d["filters"]["owners"] == ["Bravo Admin", "Bravo Caller"]
    both = c.get("/api/funnel?owner=Bravo%20Caller&owner=Bravo%20Admin", headers=rig["hb"]).json()
    assert stage_counts(both)[0] == 3
    assert stage_counts(funnel(rig, source="Nothing").json())[0] == 0


def test_polling_returns_unchanged_until_something_changes(rig):
    first = funnel(rig).json()
    assert first["stamp"] and first["presets"]["fy"]["from"].endswith("-04-01") and first["refreshedAt"]
    same = rig["c"].get("/api/funnel", params={"since": first["stamp"]}, headers=rig["hb"]).json()
    assert same == {"unchanged": True, "stamp": first["stamp"]}
    put(rig, rig["b_ids"][0], {"status": MQL})
    after = rig["c"].get("/api/funnel", params={"since": first["stamp"]}, headers=rig["hb"]).json()
    assert "unchanged" not in after and after["stamp"] != first["stamp"] and stage_counts(after)[1] == 1


def test_the_page_computes_20000_leads_in_under_3_seconds(tmp_path, monkeypatch):
    r = make_rig(tmp_path, monkeypatch)
    store = r["store"]
    rows = []
    for i in range(20000):
        sales = {"status": M.STATUSES[i % 10], "est_value": 1000 * (i % 40) + 1000, "closed_value": 900, "tenure": 12, "tcv": 10800,
                 "lost_reason": "Price", "dates": {"mql": "2026-10-06T04:30:00+00:00", "sal": "2026-10-07T04:30:00+00:00", "sql": "2026-10-08T04:30:00+00:00",
                                                   "meeting": "2026-10-09T04:30:00+00:00", "proposal": "2026-10-10T04:30:00+00:00", "negotiation": "2026-10-11T04:30:00+00:00",
                                                   "closed": "2026-10-20T04:30:00+00:00", "recycled": "2026-10-21T04:30:00+00:00"}}
        rows.append({"id": f"id{i}", "company": f"Co {i}", "owner": f"O{i % 7}", "created_at": f"2026-{1 + i % 10:02d}-05T04:30:00+00:00", "stage": "new",
                     "timeline": [], "sales": sales, "source": "manual", "source_id": "manual"})
    store.update("leads", lambda old: rows)
    t = time.time()
    first = r["c"].get("/api/funnel", headers=r["ho"])
    took = time.time() - t
    assert first.status_code == 200 and first.json()["funnel"][0]["count"] == 20000 and took < 3, took
    t = time.time()
    again = r["c"].get("/api/funnel", headers=r["ho"])
    assert time.time() - t < 0.8 and again.json()["funnel"][0]["count"] == 20000        # the second request is served from the cache


# ---------------------------------------------------------------- settings, targets, recalculation
def test_settings_are_editable_by_owner_and_workspace_admin_only_and_belong_to_the_account(rig):
    c = rig["c"]
    assert c.get("/api/funnel/settings", headers=rig["hb"]).json()["sla_hours"] == 24
    assert c.put("/api/funnel/settings", json={"sla_hours": 4}, headers=rig["hc"]).status_code == 403
    r = c.put("/api/funnel/settings", json={"sla_hours": 4, "stalled_days": 3, "targets": [{"month": "2026-11", "owner": "", "target": 500000}]}, headers=rig["hb"])
    assert r.status_code == 200 and r.json()["sla_hours"] == 4 and r.json()["targets"][0]["target"] == 500000
    assert c.get("/api/funnel/settings", headers=rig["hc"]).json()["sla_hours"] == 4         # shared inside the account
    assert c.get("/api/funnel/settings", headers=rig["ho"]).json()["sla_hours"] == 24        # not Mas Callnet's
    assert c.put("/api/funnel/settings", json={"sla_hours": 0}, headers=rig["hb"]).status_code == 400
    d = funnel(rig, **{"from": "2026-11-01", "to": "2026-11-09"}).json()
    assert d["sla"]["hours"] == 4 and d["coverage"]["target"] == 500000
    assert any(a["action"] == "sales.settings" for a in rig["store"].read("audit"))
    assert c.put("/api/funnel/settings", json={"sla_hours": 12}, headers=rig["ho"]).status_code == 200
    viewer = c.post("/api/users", json={"email": "v2@bravo.com", "name": "V", "role": "viewer", "password": LONG, "workspaces": [rig["ws"]], "account_id": rig["b"]}, headers=rig["ho"])
    assert viewer.status_code == 200
    hv = bearer(c.post("/api/login", json={"email": "v2@bravo.com", "password": LONG}).json()["token"])
    assert c.put("/api/funnel/settings", json={"sla_hours": 1}, headers=hv).status_code == 403
    assert c.post("/api/funnel/recalculate", headers=rig["hc"]).status_code == 403


def test_recalculate_back_fills_dates_for_imported_statuses_only_in_scope(rig):
    store = rig["store"]
    store.update_lead(rig["b_ids"][0], lambda l: {**l, "sales": {"status": M.STAGES[4]["status"], "est_value": 10, "dates": {"meeting": "2026-10-09T04:30:00+00:00"}}})
    store.update_lead(rig["main_ids"][0], lambda l: {**l, "sales": {"status": SQL, "est_value": 10}})
    out = rig["c"].post("/api/funnel/recalculate", headers=rig["hb"]).json()
    assert out == {"checked": 1, "updated": 1, "dates_filled": 4}
    s = next(l for l in store.read("leads") if l["id"] == rig["b_ids"][0])["sales"]
    assert s["backfilled"] == ["MQL", "SAL", "SQL"] and s["dates"]["meeting"].startswith("2026-10-09")
    assert "sql" not in next(l for l in store.read("leads") if l["id"] == rig["main_ids"][0])["sales"].get("dates", {})     # other account untouched
    assert rig["c"].post("/api/funnel/recalculate", headers=rig["hb"]).json()["updated"] == 0


# ---------------------------------------------------------------- import columns, sheet sync, Meta
RAJESH_CSV = ("Company,Email,Lead Status,MQL Date,SAL Date,SQL Date,Meeting Held Date,Closed Date,Est. Monthly Value (₹),Closed Monthly Value (₹),"
              "Contract Tenure (Months),Lead Owner,Lead Source,Industry,Service Line,Lead Score (Fit),Engagement,Remarks\n"
              "Acme Pvt,a@acme.example,Closed Won,2026/10/02 10:00,2026/10/03 10:00,2026/10/04 10:00,2026/10/06 10:00,2026/11/05 11:00,\"1,00,000\",120000,6,Asha,Meta Ads,BFSI,Collections,A,Hot,good\n"
              "Beta Pvt,b@beta.example,SQL,2026/10/12 10:00,,2026/10/14 10:00,,,50000,,,Bala,Cold Call,Retail,Inbound,B,Warm,\n"
              "Gamma Pvt,g@gamma.example,Proposal Submitted,,,,,,,,,Bala,Email,Retail,Inbound,C,Cold,\n")


def test_smart_import_reads_rajeshs_columns_and_the_dashboard_matches(rig):
    c = rig["c"]
    prev = c.post("/api/import/preview", json={"csv": RAJESH_CSV, "source": "sheet"}, headers=rig["hb"])
    assert prev.status_code == 200, prev.text
    mapped = {m["column"]: m["field"] for m in prev.json()["columns"]}
    assert mapped["MQL Date"] == "sales_mql" and mapped["Lead Owner"] == "sales_owner" and mapped["Est. Monthly Value (₹)"] == "sales_est_value"
    done = c.post("/api/import/commit", json={"csv": RAJESH_CSV, "source": "sheet"}, headers=rig["hb"])
    assert done.status_code == 200, done.text
    warnings = " ".join(w["text"] for w in done.json().get("warnings", []))
    assert "Meeting Held Date" in warnings                                        # Gamma's Proposal without a meeting is refused, with the reason
    d = funnel(rig, **{"from": "2026-10-01", "to": "2026-11-09"}).json()
    leads = {l["email"]: l for l in rig["store"].read("leads") if l["email"] in ("a@acme.example", "b@beta.example", "g@gamma.example")}
    acme, beta, gamma = leads["a@acme.example"], leads["b@beta.example"], leads["g@gamma.example"]
    assert acme["owner"] == "Asha" and acme["sales"]["status"] == M.WON and gamma["sales"].get("status") in (None, M.INQUIRY)
    assert acme["sales"]["tcv"] == 720000 and beta["sales"]["est_value"] == 50000
    assert d["snapshot"][5]["value"] == 120000 and d["revenue"]["tiles"][1]["value"] == 720000
    assert {o["owner"] for o in d["owners"]} >= {"Asha", "Bala"}
    # importing the same file again updates the record but never overwrites a stage date
    changed = RAJESH_CSV.replace("2026/10/02 10:00", "2026/10/20 10:00").replace("50000", "75000")
    assert c.post("/api/import/commit", json={"csv": changed, "source": "sheet"}, headers=rig["hb"]).status_code == 200
    again = {l["email"]: l for l in rig["store"].read("leads") if l["email"] in ("a@acme.example", "b@beta.example")}
    assert again["a@acme.example"]["sales"]["dates"]["mql"].startswith("2026-10-02")
    assert again["b@beta.example"]["sales"]["est_value"] == 75000 and len(again["b@beta.example"]["sales"]["history"]) == 1
    assert len([l for l in rig["store"].read("leads") if l["email"] == "a@acme.example"]) == 1


def test_quick_paste_import_and_reimport_use_the_same_columns(rig):
    c = rig["c"]
    csv = "Company,Email,Lead Status,SQL Date,Est. Monthly Value,Lead Owner\nQuick Co,q@quick.example,SQL,2026/10/04 10:00,\"2,50,000\",Asha\n"
    assert c.post("/api/leads/import", json={"csv": csv}, headers=rig["hb"]).json()["created"] == 1
    lead = next(l for l in rig["store"].read("leads") if l["email"] == "q@quick.example")
    assert lead["sales"]["status"] == SQL and lead["sales"]["est_value"] == 250000 and lead["owner"] == "Asha"
    again = csv.replace("2,50,000", "3,00,000").replace("2026/10/04", "2026/10/30")
    assert c.post("/api/leads/import", json={"csv": again}, headers=rig["hb"]).json()["duplicates"] == 1
    lead = next(l for l in rig["store"].read("leads") if l["email"] == "q@quick.example")
    assert lead["sales"]["est_value"] == 300000 and lead["sales"]["dates"]["sql"].startswith("2026-10-04")


def test_google_sheet_connector_carries_the_sales_columns(tmp_path):
    csv = ("Name,Email,Phone,Company,Lead Status,MQL Date,Est. Monthly Value,Lead Owner,Lead Source\n"
           "Asha Rao,asha@acme.in,98100 12345,Acme,Sales Qualified Lead (SQL),2026/10/02 10:00,90000,Rajesh,Event\n")
    state = {"csv": csv}
    client, store = make_client(tmp_path, capture_sheet_fetcher=lambda url: state["csv"])
    conn = add_connector(client, "sheet_sync", "Sales sheet", sheet_url="https://docs.google.com/spreadsheets/d/e/2PACX-abc/pub?output=csv")
    assert client.post(f"/api/inbound/{conn['id']}/sync").json()["new"] == 1
    lead = lead_by_email(store, "asha@acme.in")
    assert lead["owner"] == "Rajesh" and lead["sales"]["status"] == SQL and lead["sales"]["est_value"] == 90000 and lead["sales"]["source"] == "Event"
    state["csv"] = csv.replace("90000", "95000")                                  # the row changed in the sheet
    assert client.post(f"/api/inbound/{conn['id']}/sync").json()["merged"] == 1
    lead = lead_by_email(store, "asha@acme.in")
    assert lead["sales"]["est_value"] == 95000 and lead["sales"]["dates"]["mql"].startswith("2026-10-02")


def test_meta_lead_enters_at_inquiry_with_source_meta_ads_and_campaign_details_kept(tmp_path):
    client, store, conn, _graph = meta_setup(tmp_path)
    assert signed(client, conn, notification()).json()["results"] == ["new"]
    lead = lead_by_email(store, "meera@shah.in")
    assert lead["sales"]["source"] == "Meta Ads" and lead["sales"]["dates"]["inquiry"] and "status" not in lead["sales"]
    assert lead["attribution"]["campaign_name"] == "Diwali Hiring"
    d = client.get("/api/funnel").json()
    assert d["funnel"][0]["count"] == 1 and d["sources"][0]["source"] == "Meta Ads"
    assert client.get(f"/api/leads/{lead['id']}/sales").json()["source"] == "Meta Ads"


def test_the_funnel_routes_are_reviewed_for_limited_logins_and_roles():
    from app import routes_auth, workspace_guard
    for route in (("GET", "/api/funnel"), ("GET", "/api/funnel/drill"), ("GET", "/api/funnel/settings"), ("PUT", "/api/funnel/settings"), ("POST", "/api/funnel/recalculate")):
        assert route in workspace_guard.LIMITED_OK, route
    assert routes_auth.needed_role("GET", "/api/funnel") == "viewer"
    assert routes_auth.needed_role("PUT", "/api/leads/{lead_id}/sales") == "caller"
    assert routes_auth.needed_role("PUT", "/api/funnel/settings") == "owner" and ("PUT", "/api/funnel/settings") in routes_auth.ADMIN_WRITES
    assert workspace_guard.allowed_route("PUT", "/api/leads/{lead_id}/sales")
