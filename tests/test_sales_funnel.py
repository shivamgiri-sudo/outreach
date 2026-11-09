"""Sales funnel core: a port of every case of the Apps Script version (mcn-lead-funnel/test/core.test.js) plus read-time
defaults, automatic dates and the import columns. Pure functions only (no server); the API is in test_sales_funnel_api.py."""
import time

import pytest

from app import sales_calc as C, sales_import, sales_model as M, sales_range as R, sales_service, sales_view

NOW = M.parse_dt("2026/11/09 12:00")
SQL, MQL, SAL = M.STAGES[3]["status"], M.STAGES[1]["status"], M.STAGES[2]["status"]
MEETING, PROPOSAL, NEG = M.STAGES[4]["status"], "Proposal Submitted", "Negotiation / Commercials"
INQ = M.INQUIRY
DATE_KEYS = {"MQL": "mql", "SAL": "sal", "SQL": "sql", "MEETING": "meeting", "PROPOSAL": "proposal", "NEGOTIATION": "negotiation",
             "CLOSED": "closed", "RECYCLED": "recycled"}


def stored(**o) -> dict:
    """A stored lead like the JS test's sheet row: ID, CREATED, COMPANY, OWNER, SOURCE, STATUS, stage dates, values."""
    sales = {"status": o.get("STATUS", INQ), "dates": {DATE_KEYS[k]: M.iso(M.parse_dt(o[k])) for k in DATE_KEYS if o.get(k)}}
    for src, dst in (("EST_VALUE", "est_value"), ("CLOSED_VALUE", "closed_value"), ("TENURE", "tenure"), ("TCV", "tcv"),
                     ("LOST_STAGE", "lost_stage"), ("LOST_REASON", "lost_reason"), ("FIT", "fit"), ("ENGAGEMENT", "engagement")):
        if o.get(src) not in (None, ""):
            sales[dst] = o[src]
    if o.get("LAST_ACTIVITY"):
        sales["last_activity"] = M.iso(M.parse_dt(o["LAST_ACTIVITY"]))
    if o.get("SOURCE"):
        sales["source"] = o["SOURCE"]
    return {"id": o.get("ID", "L"), "created_at": M.iso(M.parse_dt(o["CREATED"])), "company": o.get("COMPANY", ""), "owner": o.get("OWNER", ""),
            "sales": sales}


def lead(**o) -> C.FLead:
    return sales_view.funnel_lead(stored(**o))


def sales(**o) -> dict:
    return stored(**o)["sales"]


def fmt(iso_text):
    return M.fmt_ist(M.parse_dt(iso_text))


# ---------------------------------------------------------------- status rules (Model.applyStatus)
def test_status_sql_stamps_sql_date_and_back_fills_skipped_mql_sal_with_the_same_time_marked_back_filled():
    out = M.apply_status(sales(CREATED="2026/11/01 10:00", STATUS=INQ, EST_VALUE=100000), SQL, INQ, NOW)
    assert out["ok"]
    d = out["set"]["dates"]
    assert fmt(d["sql"]) == fmt(d["mql"]) == fmt(d["sal"]) == "2026/11/09 12:00"
    assert fmt(out["set"]["first_response"]) == "2026/11/09 12:00"
    assert out["set"]["prob"] == 20
    assert out["set"]["backfilled"] == ["MQL", "SAL"]


def test_an_existing_stage_date_is_never_overwritten():
    out = M.apply_status(sales(CREATED="2026/11/01 10:00", MQL="2026/11/02 09:00", STATUS=MQL), SAL, MQL, NOW)
    assert out["ok"] and "mql" not in out["set"]["dates"] and fmt(out["set"]["dates"]["sal"]) == "2026/11/09 12:00"


def test_proposal_without_meeting_lost_without_reason_won_without_values_sql_without_estimate_are_refused():
    base = sales(CREATED="2026/11/01 10:00", EST_VALUE=50000)
    assert "Meeting Held" in M.apply_status(base, PROPOSAL, SQL, NOW)["error"]
    assert "Lost Reason" in M.apply_status(base, M.LOST, SQL, NOW)["error"]
    assert "Closed Monthly Value" in M.apply_status(base, M.WON, NEG, NOW)["error"]
    assert "Est. Monthly Value" in M.apply_status(sales(CREATED="2026/11/01 10:00"), SQL, "", NOW)["error"]
    assert "Pick a Lead Status" in M.apply_status(base, "Banana", SQL, NOW)["error"]


def test_closed_won_fills_tenure_12_and_total_contract_value_lost_records_the_stage_it_was_lost_at():
    won = M.apply_status(sales(CREATED="2026/11/01", EST_VALUE=1, CLOSED_VALUE=500000, MEETING="2026/11/03 10:00"), M.WON, NEG, NOW)
    assert won["ok"] and won["set"]["tenure"] == 12 and won["set"]["tcv"] == 6000000 and won["set"]["prob"] == 100
    assert fmt(won["set"]["dates"]["closed"]) == "2026/11/09 12:00"
    lost = M.apply_status(sales(CREATED="2026/11/01", MQL="2026/11/02", SAL="2026/11/03", LOST_REASON="Price"), M.LOST, SAL, NOW)
    assert lost["ok"] and lost["set"]["lost_stage"] == SAL and lost["set"]["prob"] == 0 and "sql" not in lost["set"]["dates"]
    assert "Lost Reason" in M.apply_status(sales(CREATED="2026/11/01", LOST_REASON="Moon"), M.LOST, SAL, NOW)["error"]
    assert "Tenure" in M.apply_status(sales(CREATED="2026/11/01", EST_VALUE=5, CLOSED_VALUE=10, TENURE=-3), M.WON, NEG, NOW)["error"]


def test_a_lost_deal_won_later_gets_the_won_date_as_its_closed_date():
    s = sales(CREATED="2026/10/01", STATUS=M.LOST, CLOSED="2026/10/20", CLOSED_VALUE=1000, EST_VALUE=1000, LOST_REASON="Timing")
    out = M.apply_status(s, M.WON, M.LOST, NOW)
    assert out["ok"] and fmt(out["set"]["dates"]["closed"]) == "2026/11/09 12:00"


def test_lost_from_recycled_uses_the_furthest_stage_reached_and_recycled_gets_its_date():
    s = sales(CREATED="2026/10/01", MQL="2026/10/02", SAL="2026/10/03", SQL="2026/10/04", EST_VALUE=5, LOST_REASON="Other")
    assert M.apply_status(s, M.LOST, M.RECYCLED, NOW)["set"]["lost_stage"] == SQL
    rec = M.apply_status(s, M.RECYCLED, SQL, NOW)
    assert rec["ok"] and "recycled" in rec["set"]["dates"] and rec["set"]["prob"] == 0


# ---------------------------------------------------------------- dates (Range)
def test_date_filter_labels_validation_and_previous_period():
    e = M.parse_dt("2026/01/05 10:00")
    assert R.resolve_range("", "", e, NOW)["label"] == "As on 09-Nov-2026"
    assert R.resolve_range("2026-11-01", "", e, NOW)["label"] == "From 01-Nov-2026 to today"
    assert R.resolve_range("", "2026-10-31", e, NOW)["label"] == "As on 31-Oct-2026"
    assert R.resolve_range("2026-10-01", "2026-10-31", e, NOW)["label"] == "01-Oct-2026 to 31-Oct-2026"
    assert R.resolve_range("2026-11-05", "2026-11-01", e, NOW)["error"] == "From Date cannot be after To Date"
    assert "Future" in R.resolve_range("2026-12-01", "", e, NOW)["error"]
    assert "date pickers" in R.resolve_range("2026-13-45", "", e, NOW)["error"]
    p = R.previous_range(R.resolve_range("2026-11-01", "2026-11-30", e, M.parse_dt("2026/11/30 12:00")))
    assert (p["from"], p["to"]) == ("2026-10-02", "2026-10-31")
    assert R.preset("fy", NOW) == {"from": "2026-04-01", "to": "2026-11-09"}
    assert R.preset("lastMonth", NOW) == {"from": "2026-10-01", "to": "2026-10-31"}
    assert R.preset("today", NOW) == {"from": "2026-11-09", "to": "2026-11-09"}
    assert R.preset("week", NOW) == {"from": "2026-11-09", "to": "2026-11-09"}          # 9 Nov 2026 is a Monday
    assert R.preset("month", NOW) == {"from": "2026-11-01", "to": "2026-11-09"}
    assert R.preset("quarter", NOW) == {"from": "2026-10-01", "to": "2026-11-09"}
    assert R.preset("fy", M.parse_dt("2027/02/10 10:00"))["from"] == "2026-04-01"
    assert R.preset("lastMonth", M.parse_dt("2027/01/10 10:00")) == {"from": "2026-12-01", "to": "2026-12-31"}


def test_range_covers_from_0000_to_2359_ist():
    r = R.resolve_range("2026-10-01", "2026-10-31", None, NOW)
    assert M.fmt_ist(r["start"]) == "2026/10/01 00:00" and M.fmt_ist(r["end"]) == "2026/10/31 23:59"


# ---------------------------------------------------------------- counting (Calc)
SAMPLE_ROWS = [
    # created 15 Oct, won 5 Nov (spec example): October counts stages 1-7, not Won
    dict(ID="L1", CREATED="2026/10/15 10:00", COMPANY="Oct Co", OWNER="Asha", SOURCE="Email", STATUS=M.WON, MQL="2026/10/16", SAL="2026/10/17",
         SQL="2026/10/18", MEETING="2026/10/20", PROPOSAL="2026/10/22", NEGOTIATION="2026/10/25", CLOSED="2026/11/05 11:00", EST_VALUE=400000,
         CLOSED_VALUE=450000, TENURE=12, TCV=5400000),
    # created 10 Sep, won 5 Nov at 5,00,000/month: in Deal Value Closed for November, not September
    dict(ID="L2", CREATED="2026/09/10 10:00", COMPANY="Sep Co", OWNER="Bala", SOURCE="Referral", STATUS=M.WON, MQL="2026/09/11", SAL="2026/09/12",
         SQL="2026/09/13", MEETING="2026/09/14", PROPOSAL="2026/09/15", NEGOTIATION="2026/09/20", CLOSED="2026/11/05 12:00", EST_VALUE=500000,
         CLOSED_VALUE=500000, TENURE=12, TCV=6000000),
    dict(ID="L3", CREATED="2026/10/20 10:00", COMPANY="Lost Co", OWNER="Asha", SOURCE="Email", STATUS=M.LOST, MQL="2026/10/21", CLOSED="2026/10/25",
         LOST_STAGE=MQL, LOST_REASON="Price"),
    # odd hand entry: SQL date filled but MQL blank -> still counted in MQL (monotonic)
    dict(ID="L4", CREATED="2026/10/05 10:00", COMPANY="Odd Co", OWNER="Bala", SOURCE="LinkedIn", STATUS=SQL, SQL="2026/10/06", EST_VALUE=100000,
         LAST_ACTIVITY="2026/10/06 10:00", FIT="A", ENGAGEMENT="Hot"),
    dict(ID="L5", CREATED="2026/11/02 10:00", COMPANY="New Co", OWNER="Asha", SOURCE="Email", STATUS=INQ, LAST_ACTIVITY="2026/11/08 10:00", FIT="B",
         ENGAGEMENT="Warm"),
]
SAMPLE = [lead(**r) for r in SAMPLE_ROWS]


def compute(**kw):
    return C.compute(SAMPLE, NOW, kw.pop("from_", ""), kw.pop("to", ""), **kw)


def test_cohort_rule_a_lead_created_15_oct_and_won_5_nov_counts_in_stages_1_to_7_for_october_not_in_closed_won():
    oct_ = compute(from_="2026-10-01", to="2026-10-31")
    assert [s["count"] for s in oct_["funnel"]] == [3, 3, 2, 2, 1, 1, 1, 0]
    assert "L1" in oct_["funnel"][6]["ids"] and "L1" not in oct_["funnel"][7]["ids"]


def test_no_stage_is_ever_larger_than_the_one_before_it():
    for kw in ({}, {"from_": "2026-10-01", "to": "2026-10-31"}, {"from_": "2026-09-01"}):
        counts = [s["count"] for s in compute(**kw)["funnel"]]
        assert all(counts[i] <= counts[i - 1] for i in range(1, len(counts))), counts


def test_no_dates_chosen_as_on_today_and_the_totals_equal_every_row():
    d = compute()
    assert d["range"]["label"] == "As on 09-Nov-2026"
    assert d["funnel"][0]["count"] == len(SAMPLE)
    assert d["snapshot"][5]["value"] == 950000                      # deal value closed to date


def test_deal_value_closed_uses_the_closed_date_sep_created_deal_won_5_nov_is_in_november_not_september():
    nov, sep = compute(from_="2026-11-01", to="2026-11-09"), compute(from_="2026-09-01", to="2026-09-30")
    assert "L2" in nov["revenue"]["tiles"][0]["ids"] and "L2" not in sep["revenue"]["tiles"][0]["ids"]
    assert nov["revenue"]["tiles"][0]["value"] == 950000
    assert nov["revenue"]["tiles"][1]["value"] == 11400000
    assert sep["revenue"]["tiles"][0]["value"] == 0
    assert nov["revenue"]["byMonth"] == [{"month": "2026-11", "value": 950000}]


def test_drill_down_rows_equal_the_number_on_the_stage_filters_stalled_score_grid_leaderboard():
    d = compute()
    for s in d["funnel"]:
        assert len(C.drill(SAMPLE, s["ids"])) == s["count"]
    for t in d["snapshot"]:
        assert len(C.drill(SAMPLE, t["ids"])) == (t["value"] if not t["money"] else len(t["ids"]))
    asha = compute(filters={"owners": ["Asha"]})
    assert asha["funnel"][0]["count"] == 3
    assert d["stalled"]["ids"] == ["L4"] and d["stalled"]["total"] == 1          # idle more than 7 days, open
    assert d["scoreGrid"]["A"]["Hot"]["count"] == 1 and d["scoreGrid"]["B"]["Warm"]["count"] == 1
    assert d["owners"][0]["owner"] == "Bala"                                      # most won value first
    assert d["leakage"][MQL]["Price"] == 1


def test_conversion_is_count_n_over_count_n_minus_1_with_one_decimal_and_a_dash_when_previous_is_zero():
    oct_ = compute(from_="2026-10-01", to="2026-10-31")
    assert oct_["funnel"][2]["conv"] == 66.7 and oct_["funnel"][1]["conv"] == 100
    assert oct_["funnel"][7]["conv"] == 0
    empty = compute(from_="2026-01-01", to="2026-01-31")
    assert all(s["conv"] is None for s in empty["funnel"][1:]) and empty["conversions"][-1]["rate"] is None
    assert oct_["conversions"][-1] == {"from": INQ, "to": "Closed Won (overall)", "rate": 0.0, "fromCount": 3, "toCount": 0}


def test_kpi_tiles_show_the_change_against_the_previous_period_of_equal_length():
    d = compute(from_="2026-11-01", to="2026-11-09")
    inquiries = d["snapshot"][0]
    assert inquiries["value"] == 1 and inquiries["previous"] == 0 and inquiries["change"] is None      # no base: a dash
    full = compute(from_="2026-10-02", to="2026-10-31")
    assert full["snapshot"][0]["previous"] == 1 and full["snapshot"][0]["value"] == 3 and full["snapshot"][0]["change"] == 200


def test_filters_by_source_industry_service_and_owner_multi_select():
    assert compute(filters={"sources": ["Email", "Referral"]})["funnel"][0]["count"] == 4
    assert compute(filters={"sources": ["Email"], "owners": ["Bala"]})["funnel"][0]["count"] == 0
    assert compute()["filters"]["owners"] == ["Asha", "Bala"]


def test_pipeline_value_by_stage_weighted_coverage_and_velocity():
    d = compute(from_="2026-11-01", to="2026-11-09", targets=[{"month": "2026-11", "owner": "Asha", "target": 1000000},
                                                              {"month": "2026-10", "owner": "Asha", "target": 5}])
    sql = next(p for p in d["pipeline"] if p["key"] == "sql")
    assert sql["value"] == 100000 and sql["weighted"] == 20000 and sql["count"] == 1
    assert d["coverage"] == {"pipeline": 100000, "target": 1000000, "ratio": 0.1}
    assert d["velocity"]["winRate"] == 100 and d["velocity"]["avgWonValue"] == 475000
    assert compute()["velocity"]["winRate"] == 66.7                              # all time: 2 won, 1 lost
    assert compute()["coverage"]["ratio"] is None                                 # no target set


def test_average_days_per_stage_leaves_back_filled_stages_out():
    row = dict(SAMPLE_ROWS[0], ID="B1")
    plain = lead(**row)
    marked = sales_view.funnel_lead({**stored(**row), "sales": {**stored(**row)["sales"], "backfilled": ["SAL"]}})
    a = C.compute([plain], NOW)["cycle"]
    b = C.compute([marked], NOW)["cycle"]
    assert a[1]["measured"] == 1 and a[2]["measured"] == 1
    assert b[1]["measured"] == 0 and b[2]["measured"] == 0 and b[0]["measured"] == 1     # MQL->SAL and SAL->SQL left out


def test_speed_to_lead_and_sla_default_24_hours():
    fast = stored(ID="S1", CREATED="2026/11/01 10:00", OWNER="Asha")
    fast["sales"]["first_response"] = M.iso(M.parse_dt("2026/11/01 12:00"))
    slow = stored(ID="S2", CREATED="2026/11/01 10:00", OWNER="Bala")
    slow["sales"]["first_response"] = M.iso(M.parse_dt("2026/11/04 10:00"))
    leads = [sales_view.funnel_lead(fast), sales_view.funnel_lead(slow)]
    d = C.compute(leads, NOW)
    assert d["speed"]["avgHours"] == 37 and d["speed"]["medianHours"] == 37 and d["speed"]["measured"] == 2
    assert d["sla"] == {"hours": 24, "pct": 50, "within": 1, "measured": 2}
    assert C.compute(leads, NOW, sla_hours=100)["sla"]["pct"] == 100
    assert [o["owner"] for o in d["speed"]["byOwner"]] == ["Asha", "Bala"]


def test_recycled_pool_and_revenue_breakdown_with_variance():
    rec = lead(ID="R1", CREATED="2026/10/01", STATUS=M.RECYCLED, RECYCLED="2026/10/20", LOST_REASON="Timing")
    d = C.compute([rec, *SAMPLE], NOW)
    assert d["recycled"]["count"] == 1 and d["recycled"]["byReason"] == {"Timing": 1} and d["recycled"]["byAge"]["0-30 days"] == 1
    by_owner = {r["name"]: r for r in d["revenue"]["byOwner"]}
    assert by_owner["Asha"]["variancePct"] == 12.5 and by_owner["Bala"]["variancePct"] == 0.0
    assert d["revenue"]["tiles"][3]["value"] == 475000 and d["revenue"]["tiles"][2]["value"] == 2


def test_recalculate_migration_back_fills_from_the_created_date_and_marks_it():
    s = sales(CREATED="2026/10/01 09:00", STATUS=MEETING)
    out = M.recalc(s, M.parse_dt("2026/10/01 09:00"), NOW)
    assert fmt(out["dates"]["meeting"]) == "2026/10/01 09:00"
    assert out["backfilled"] == ["MQL", "SAL", "SQL", "Meeting"] and out["prob"] == 35
    again = M.merge_set(M.merge_set(s, out), M.recalc(M.merge_set(s, out), M.parse_dt("2026/10/01 09:00"), NOW))
    assert again == M.merge_set(s, out)                                              # running it twice changes nothing


def test_20000_leads_compute_well_under_1_5_seconds():
    many = []
    for i in range(20000):
        many.append(lead(ID=f"X{i}", CREATED=f"2026/{1 + i % 10:02d}/05 10:00", OWNER=f"O{i % 7}", SOURCE=["Cold Call", "Email", "Meta Ads", "SEO / Website",
                                                                                              "LinkedIn", "Referral", "Event"][i % 7],
                         STATUS=M.STATUSES[i % 10], MQL="2026/10/06" if i % 2 else "", EST_VALUE=1000 * (i % 50), LAST_ACTIVITY="2026/10/06 10:00"))
    t = time.time()
    C.compute(many, NOW)
    assert time.time() - t < 1.5, f"took {time.time() - t:.2f}s"


# ---------------------------------------------------------------- read-time defaults and automatic dates
def test_a_lead_without_a_sales_record_reads_as_inquiry_created_when_the_lead_was():
    f = sales_view.funnel_lead({"id": "a", "created_at": "2026-11-01T04:30:00+00:00", "company": "Acme", "source": "apollo_search"})
    assert f.status == INQ and M.fmt_ist(f.created) == "2026/11/01 10:00" and f.source == "Apollo" and f.est == 0


def test_meta_inbound_lead_reads_as_inquiry_with_source_meta_ads_and_mql_from_the_form():
    lead_ = {"id": "m", "created_at": "2026-11-01T04:30:00+00:00", "source": "meta_ads", "source_id": "meta_ads", "first_inbound_at": "2026-11-01T04:30:00+00:00"}
    f = sales_view.funnel_lead(lead_)
    assert f.source == "Meta Ads" and f.dates["mql"] == f.created
    assert f.status == M.STAGES[1]["status"]            # no sales record: the shown stage follows the automatic dates


def test_first_human_outbound_contact_sets_sal_and_first_response_but_automatic_emails_do_not():
    base = {"id": "h", "created_at": "2026-11-01T04:30:00+00:00", "timeline": [
        {"at": "2026-11-01T05:00:00+00:00", "kind": "email", "text": "Step 1 sent"},                    # sequence mail: no person
        {"at": "2026-11-01T08:30:00+00:00", "kind": "call", "text": "Call", "by": "Asha"}]}
    f = sales_view.funnel_lead(base)
    assert M.fmt_ist(f.first_response) == "2026/11/01 14:00" and f.dates["sal"] == f.first_response
    act = sales_view.auto_index([{"lead_id": "h", "type": "whatsapp", "direction": "out", "at": "2026-11-01T06:00:00+00:00"},
                                 {"lead_id": "h", "type": "call", "outcome": "connected", "at": "2026-11-01T07:00:00+00:00"}])
    g = sales_view.funnel_lead(base, act)
    assert M.fmt_ist(g.first_response) == "2026/11/01 11:30" and M.fmt_ist(g.dates["mql"]) == "2026/11/01 12:30"
    assert sales_view.funnel_lead({"id": "z", "created_at": "2026-11-01T04:30:00+00:00", "timeline": [
        {"at": "2026-11-01T05:00:00+00:00", "kind": "email", "text": "x"}]}).first_response is None


def test_an_explicit_sales_date_always_beats_the_automatic_one():
    lead_ = {"id": "e", "created_at": "2026-11-01T04:30:00+00:00", "timeline": [{"at": "2026-11-01T08:30:00+00:00", "kind": "reply", "text": "yes"}],
             "sales": {"status": SQL, "est_value": 5, "dates": {"mql": "2026-11-03T04:30:00+00:00"}}}
    assert M.fmt_ist(sales_view.funnel_lead(lead_).dates["mql"]) == "2026/11/03 10:00"


# ---------------------------------------------------------------- import columns (Rajesh's sheet)
HEADERS = ["Lead Status", "MQL Date", "SAL Date", "SQL Date", "Meeting Held Date", "Proposal Date", "Negotiation Date", "Closed Date", "Recycled Date",
           "Lost Stage", "Lost Reason", "Est. Monthly Value (₹)", "Closed Monthly Value (₹)", "Contract Tenure (Months)", "Lead Owner", "Lead Source",
           "Industry", "Service Line", "Lead Score (Fit)", "Engagement", "Remarks"]


def test_every_column_of_the_sheet_is_recognised_and_read():
    row = dict(zip(HEADERS, ["Closed Won", "2026/10/02 10:00", "2026/10/03 10:00", "2026/10/04 10:00", "2026/10/05 10:00", "2026/10/06 10:00",
                             "2026/10/07 10:00", "2026/10/09 10:00", "", "", "", "1,50,000", "₹ 2L", "6", "Asha", "Meta Ads", "BFSI", "Collections", "a", "hot", "ok"]))
    parsed, notes = sales_import.from_row(row)
    s = parsed["sales"]
    assert parsed["owner"] == "Asha" and not notes
    assert s["status"] == M.WON and s["est_value"] == 150000 and s["closed_value"] == 200000 and s["tenure"] == 6
    assert s["fit"] == "A" and s["engagement"] == "Hot" and s["service"] == "Collections" and s["source"] == "Meta Ads" and s["industry"] == "BFSI"
    assert fmt(s["dates"]["sql"]) == "2026/10/04 10:00" and fmt(s["dates"]["closed"]) == "2026/10/09 10:00"
    from app.header_map import ALIAS, loose_header
    for h in HEADERS[1:-1]:
        assert loose_header(h) in ALIAS, h


def test_bad_values_in_the_sheet_are_reported_not_guessed():
    parsed, notes = sales_import.from_row({"MQL Date": "next week", "Est. Monthly Value": "lots", "Lead Status": "Banana"})
    assert parsed["sales"] == {} and len(notes) == 3


def test_reimport_updates_values_but_never_overwrites_stage_dates_and_refuses_a_status_that_breaks_a_rule():
    first, _ = sales_import.from_row({"Lead Status": "SQL", "SQL Date": "2026/10/04 10:00", "Est. Monthly Value": "100000"})
    s, warnings, changed = sales_import.apply_incoming(None, first["sales"], M.parse_dt("2026/10/01 09:00"), NOW)
    assert changed and not warnings and s["status"] == SQL and s["backfilled"] == ["MQL", "SAL"]
    assert fmt(s["dates"]["mql"]) == "2026/10/01 09:00"                             # back-filled with the created date, marked
    second, _ = sales_import.from_row({"Lead Status": "SQL", "SQL Date": "2026/10/30 10:00", "MQL Date": "2026/10/29", "Est. Monthly Value": "250000"})
    s2, w2, c2 = sales_import.apply_incoming(s, second["sales"], M.parse_dt("2026/10/01 09:00"), NOW)
    assert c2 and s2["est_value"] == 250000 and fmt(s2["dates"]["sql"]) == "2026/10/04 10:00" and fmt(s2["dates"]["mql"]) == "2026/10/01 09:00"
    third, _ = sales_import.from_row({"Lead Status": "Proposal Submitted"})
    s3, w3, _c3 = sales_import.apply_incoming(s2, third["sales"], M.parse_dt("2026/10/01 09:00"), NOW)
    assert s3["status"] == SQL and "Meeting Held Date" in w3[0]                       # refused, the old status stays
    assert len(s2["history"]) == 1


def test_status_names_people_type_are_understood():
    assert M.clean_status("sql") == SQL and M.clean_status("Won") == M.WON and M.clean_status("lost") == M.LOST
    assert M.clean_status("Marketing Qualified Lead") == MQL and M.clean_status("recycled / nurture") == M.RECYCLED
    assert M.clean_status("whatever") == "" and M.clean_status("") == ""


def test_money_in_indian_forms():
    assert M.money("1,50,000") == 150000 and M.money("₹ 2.5L") == 250000 and M.money("1.2cr") == 12000000
    assert M.money("abc") == 0 and M.money(-5) == 0 and M.money(True) == 0


def test_settings_validation():
    assert sales_service.clean_settings({"sla_hours": 48, "stalled_days": "10"}) == {"sla_hours": 48, "stalled_days": 10}
    for bad in ({"sla_hours": 0}, {"stalled_days": 999}, {"sla_hours": "x"}, {"targets": [{"month": "Nov", "target": 5}]},
                {"targets": [{"month": "2026-11", "target": "x"}]}, {"targets": [{"month": "2026-11", "target": 1}, {"month": "2026-11", "target": 2}]}):
        with pytest.raises(sales_service.SalesRefused):
            sales_service.clean_settings(bad)
