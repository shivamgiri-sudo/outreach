"""QA: a 500-lead store with planted duplicates, closed stages and sending problems, run through the hygiene report,
manual merge, standardise, scoring, the dashboard, Insights and Today. Data is fictional (see fixtures/.../build_db.py)."""
import re
from collections import Counter
from datetime import timedelta

import pytest

from app.lead_merge import merged_stage
from app.main import _present
from app.stages import STAGES
from tests.qa.hsi_helpers import NOW, cluster_pairs, load_500, pairs, seeded, truth_pairs

CLOSED_LOST = ("unsubscribed", "disqualified")


@pytest.fixture(scope="module")
def data():
    return load_500()


@pytest.fixture
def client(tmp_path, monkeypatch, data):
    store, _ = load_500()                              # a fresh copy per test
    return seeded(tmp_path, monkeypatch, store)


def ids_of(client) -> dict:
    return {lead["id"]: lead for lead in client.app.state.store.read("leads")}


# ---------------------------------------------------------------- hygiene report vs the planted truth
def test_the_store_is_what_the_report_is_measured_against(data):
    store, truth = data
    leads = store["leads"]
    assert len(leads) == 500 and len({l["id"] for l in leads}) == 500
    kinds = Counter(g["kind"] for g in truth["groups"])
    assert kinds["same_email"] == 12 and kinds["gmail_alias"] == 8 and kinds["fuzzy_company_same_city"] == 12
    assert {l["stage"] for l in leads} >= {"new", "contacted", "unsubscribed", "disqualified", "customer", "trial"}
    assert any(l["company"].isupper() for l in leads) and any("PVT. LTD." in l["company"] for l in leads)


def test_duplicate_clusters_find_every_planted_duplicate_and_almost_nothing_else(client, data):
    _, truth = data
    rep = client.get("/api/hygiene/report").json()
    found = cluster_pairs(rep["clusters"])
    must = truth_pairs(truth, "cluster")
    company_level = truth_pairs(truth, "company_duplicate")
    recall = len(found & must) / len(must)
    true_found = found & (must | company_level)
    precision = len(true_found) / len(found)
    assert recall == 1.0, sorted(must - found)
    assert precision >= 0.95, sorted(found - must - company_level)
    # every pair grouped wrongly is the one known limitation: company rows sharing a business-centre landline
    known_fp = set().union(*(pairs(n["lead_ids"]) for n in truth["negatives"] if n.get("known_false_positive")))
    assert found - must - company_level <= known_fp
    for negative in truth["negatives"]:
        if not negative.get("known_false_positive"):
            assert not pairs(negative["lead_ids"]) & found, negative["kind"]
    assert rep["duplicate_clusters"] == len(rep["clusters"]) and rep["duplicate_leads"] == sum(
        len(c["lead_ids"]) for c in rep["clusters"])


def test_every_cluster_explains_each_match_and_suggests_the_lead_with_history(client, data):
    _, truth = data
    rep = client.get("/api/hygiene/report").json()
    by_member = {i: c for c in rep["clusters"] for i in c["lead_ids"]}
    for cluster in rep["clusters"]:
        assert cluster["reasons"] and cluster["suggested_keep"] in cluster["lead_ids"]
        assert {r["why"] for r in cluster["reasons"]} == set(cluster["why"])
    gmail = next(g for g in truth["groups"] if g["kind"] == "gmail_alias")
    assert any("Gmail" in w for w in by_member[gmail["lead_ids"][0]]["why"])
    fuzzy = next(g for g in truth["groups"] if g["note"].startswith("Acme Retail"))
    assert set(fuzzy["lead_ids"]) <= set(by_member[fuzzy["lead_ids"][0]]["lead_ids"])
    leads = ids_of(client)
    for group in truth["groups"]:
        if group["expect"] == "cluster":
            keep = by_member[group["lead_ids"][0]]["suggested_keep"]
            emailed = [i for i in group["lead_ids"] if leads[i].get("sequence")]
            if emailed:
                assert keep in emailed, group                  # never suggest keeping a lead with no sending history


def test_report_gap_and_email_counts_match_the_data(client, data):
    store, truth = data
    leads = store["leads"]
    rep = client.get("/api/hygiene/report").json()
    assert rep["total"] == 500
    assert rep["missing"]["email"] == sum(1 for l in leads if not l.get("email"))
    assert rep["missing"]["decision_maker"] == sum(1 for l in leads if not l.get("contact_name"))
    assert rep["emails"]["invalid"] == len(truth["planted"]["invalid_email_leads"]) == 3
    suppressed = set(store["suppressed"])
    assert rep["emails"]["suppressed"] == sum(1 for l in leads if l.get("email") in suppressed) > 0
    assert rep["emails"]["role"] >= sum(1 for l in leads if (l.get("email") or "").startswith("hr@"))
    assert rep["conflicts"] == 0 and rep["needs_standardising"] > 150
    # 90-day stale: only open prospects whose last event is older than 90 days
    cutoff = (NOW - timedelta(days=90)).isoformat()
    stale = [l for l in leads if l["stage"] in ("new", "verified", "enriched", "contacted", "not_now")
             and max([e["at"] for e in l["timeline"]] + [l["created_at"]]) < cutoff]
    assert rep["stale"] == len(stale) > 0


# ---------------------------------------------------------------- manual merge
def _merge(client, keep, others):
    r = client.post("/api/hygiene/merge", json={"keep_id": keep, "merge_ids": others})
    assert r.status_code == 200, r.text
    return r.json()


def test_merging_each_same_email_group_keeps_the_strictest_status_and_moves_every_row(client, data):
    _, truth = data
    for group in [g for g in truth["groups"] if g["kind"] in ("same_email", "gmail_alias")]:
        leads = ids_of(client)
        members = [leads[i] for i in group["lead_ids"]]
        expected_stage = merged_stage(members)
        withdrawn = any(m.get("consent_withdrawn_at") for m in members)
        events = sum(len(m["timeline"]) for m in members)
        keep, others = group["lead_ids"][-1], group["lead_ids"][:-1]   # keep the weaker copy on purpose
        consent = members[-1].get("consent") or next((m["consent"] for m in members[:-1] if m.get("consent")), None)
        store = client.app.state.store
        rows_before = {k: sum(1 for r in store.read(k) if r.get("lead_id") in others)
                       for k in ("outbox", "inbox", "tasks", "whatsapp_outbox", "consent_ledger")}
        body = _merge(client, keep, others)
        merged = body["lead"]
        assert merged["stage"] == expected_stage
        if "unsubscribed" in [m["stage"] for m in members]:
            assert merged["stage"] == "unsubscribed"
        if withdrawn or merged["stage"] == "unsubscribed":
            assert merged["consent"] is None and merged["consent_withdrawn_at"]
        else:
            assert merged["consent"] == consent
        assert set(others) <= set(merged["merged_ids"]) and len(merged["timeline"]) == events + 1
        for key, n in rows_before.items():
            assert body["repointed"][key] == n
            assert not any(r.get("lead_id") in others for r in store.read(key))
        assert not set(others) & set(ids_of(client))


def test_closed_and_customer_pairs_resolve_like_the_prd_says(client, data):
    _, truth = data
    leads = ids_of(client)
    seen = {}
    for group in [g for g in truth["groups"] if g["kind"] == "same_email"]:
        stages = tuple(leads[i]["stage"] for i in group["lead_ids"])
        seen[stages] = _merge(client, group["lead_ids"][0], group["lead_ids"][1:])["lead"]["stage"]
    assert seen[("engaged", "unsubscribed")] == "unsubscribed"
    assert seen[("customer", "disqualified")] == "disqualified"       # stricter status beats the advanced stage
    assert seen[("call_done", "customer")] == "customer"
    assert seen[("not_now", "new")] == "not_now"
    assert seen[("contacted", "new", "enriched")] == "contacted"


def test_merging_everything_planted_leaves_only_the_known_false_positive(client, data):
    _, truth = data
    for group in [g for g in truth["groups"] if g["expect"] == "cluster"]:
        _merge(client, group["lead_ids"][0], group["lead_ids"][1:])
    rep = client.get("/api/hygiene/report").json()
    merged_away = sum(len(g["lead_ids"]) - 1 for g in truth["groups"] if g["expect"] == "cluster")
    assert rep["total"] == 500 - merged_away
    left = [set(c["lead_ids"]) for c in rep["clusters"]]
    known = [set(n["lead_ids"]) for n in truth["negatives"] if n.get("known_false_positive")]
    company_level = [set(g["lead_ids"]) for g in truth["groups"] if g["expect"] == "company_duplicate"]
    assert all(c in known or c in company_level for c in left), left


def test_merge_refuses_bad_requests_without_changing_anything(client):
    before = client.app.state.store.read("leads")
    assert client.post("/api/hygiene/merge", json={"keep_id": "q001", "merge_ids": ["q001"]}).status_code == 400
    assert client.post("/api/hygiene/merge", json={"keep_id": "q001", "merge_ids": ["q002", "q002"]}).status_code == 400
    assert client.post("/api/hygiene/merge", json={"keep_id": "q001", "merge_ids": ["nope"]}).status_code == 404
    assert client.post("/api/hygiene/merge", json={"keep_id": "q001", "merge_ids": []}).status_code == 422
    assert client.app.state.store.read("leads") == before


# ---------------------------------------------------------------- standardise
def test_standardise_preview_changes_nothing_and_apply_matches_the_preview(client):
    store = client.app.state.store
    before = store.read("leads")
    preview = client.post("/api/hygiene/standardise", json={"dry_run": True}).json()
    assert preview["dry_run"] is True and preview["changed"] > 150 and len(preview["changes"]) == 200   # list capped
    assert store.read("leads") == before
    one = next(c for c in preview["changes"] if c["lead_id"] == "q002")              # a legacy ALL CAPS row
    assert one["fields"]["company"] == {"from": "KAVERI RETAIL PVT. LTD.", "to": "Kaveri Retail"}
    assert one["fields"]["city"]["to"] == "Mumbai" and one["fields"]["country"]["to"] == "IN"
    applied = client.post("/api/hygiene/standardise", json={"dry_run": False}).json()
    assert applied["changed"] == preview["changed"]
    after = {l["id"]: l for l in store.read("leads")}
    for old in before:
        new = after[old["id"]]
        for field in ("email", "stage", "consent", "sequence", "consent_withdrawn_at", "engagement"):
            assert new.get(field) == old.get(field), (old["id"], field)
    assert after["q002"]["company_legal"] == "Kaveri Retail Pvt. Ltd."
    assert after["q002"]["timeline"][-1]["kind"] == "standardised"
    assert client.post("/api/hygiene/standardise", json={"dry_run": True}).json()["changed"] == 0     # idempotent
    rep = client.get("/api/hygiene/report").json()
    assert rep["needs_standardising"] == 0


def test_standardise_only_the_chosen_leads(client):
    store = client.app.state.store
    r = client.post("/api/hygiene/standardise", json={"dry_run": False, "lead_ids": ["q002", "q999"]}).json()
    assert r["changed"] == 1 and [c["lead_id"] for c in r["changes"]] == ["q002"]
    untouched = next(l for l in store.read("leads") if l["id"] == "q004")
    assert untouched["company"].isupper()


def test_duplicates_are_still_found_after_standardising(client, data):
    _, truth = data
    client.post("/api/hygiene/standardise", json={"dry_run": False})
    found = cluster_pairs(client.get("/api/hygiene/report").json()["clusters"])
    must = truth_pairs(truth, "cluster")
    assert must <= found


# ---------------------------------------------------------------- scoring, dashboard, insights on the 500 leads
def test_scores_bands_and_india_fit_on_the_500(data):
    store, _ = data
    presented = [_present(l) for l in store["leads"]]
    for lead in presented:
        assert 0 <= lead["score"] <= 100
        assert lead["band"] == ("hot" if lead["score"] >= 70 else "warm" if lead["score"] >= 40 else "cold")
        assert lead["score"] == min(100, round(sum(lead["signals"].values())))
    legacy_india = [l for l in presented if l["country"] in ("India", "india", "IND") and 20 <= (l.get("company_size") or 0)]
    assert legacy_india and all(l["signals"]["fit"] == 15 for l in legacy_india)
    foreign = [l for l in presented if l["country"] in ("US", "AE", "United Kingdom")]
    assert foreign and all(l["signals"]["fit"] <= 9 for l in foreign)
    yes = [l for l in presented if l.get("consent")]
    assert yes and all(l["signals"]["intent"] == 10 for l in yes)
    # Open roles are log-scaled (full marks from 50, not 10), so fewer of the fixture's 10-15-role leads reach hot (43).
    assert Counter(l["band"] for l in presented)["hot"] > 35


def test_dashboard_counts_funnel_and_reminders(client, data):
    store, _ = data
    dash = client.get("/api/dashboard").json()
    stages = Counter(l["stage"] for l in store["leads"])
    assert dash["total_leads"] == 500 and sum(dash["by_stage"].values()) == 500
    assert all(dash["by_stage"][s] == n for s, n in stages.items())
    assert [f["stage"] for f in dash["funnel"]] == list(STAGES)
    presented = {l["id"]: _present(l) for l in store["leads"]}
    assert dash["hot"] == sum(1 for l in presented.values() if l["band"] == "hot")
    assert 0 < len(dash["reminders"]) <= 10
    for item in dash["reminders"]:
        lead = presented[item["lead_id"]]
        assert lead["band"] == "hot" and lead["stage"] not in ("customer", "advocate") + CLOSED_LOST
        assert item["text"] == lead["suggestion"]["action"]


def test_insights_rates_sources_sectors_and_top_leads(client, data):
    store, _ = data
    ins = client.get("/api/insights").json()
    leads = store["leads"]
    sent = [m for m in store["outbox"] if m["status"] not in ("failed", "provider_error")]
    emailed = {m["lead_id"] for m in sent}
    assert ins["totals"]["leads"] == 500 and ins["totals"]["emails_sent"] == len(sent) < len(store["outbox"])
    assert ins["outreach"]["emailed_leads"] == len(emailed)
    replied = {l["id"] for l in leads if l["engagement"]["replies"] > 0}
    assert ins["outreach"]["reply_rate"] == round(100 * len(replied & emailed) / len(emailed), 1)
    for rate in ("reply_rate", "interested_rate", "unsubscribe_rate"):
        assert 0 <= ins["outreach"][rate] <= 100
    by_source = {s["source"]: s for s in ins["by_source"]}
    assert "bmi_sheet" in by_source and "campaign" in by_source                     # 'bmi_sheet:leads' -> 'bmi_sheet'
    assert sum(s["leads"] for s in ins["by_source"]) == 500
    assert sum(s["leads"] for s in ins["by_sector"]) == 500 and any(s["sector"] == "Unknown" for s in ins["by_sector"])
    assert sum(ins["by_band"].values()) == 500
    stage = {l["id"]: l["stage"] for l in leads}
    top = ins["top_leads"]
    assert len(top) == 10 and not [t for t in top if stage[t["id"]] in CLOSED_LOST]
    assert [t["score"] for t in top] == sorted((t["score"] for t in top), reverse=True)


# ---------------------------------------------------------------- Today, with the clock moved forward
def test_today_lists_problems_first_and_counts_add_up(client, data):
    store, truth = data
    today = client.get("/api/today").json()
    items, counts = today["items"], today["counts"]
    assert counts["problems"] == 5                                    # 3 failed x3, 1 stuck mid-send, 1 blocked
    assert [i["kind"] for i in items[:5]] == ["problem"] * 5
    problem_ids = {i["lead_id"] for i in items[:5]}
    assert problem_ids == set(truth["planted"]["failed_send_leads"]) | {truth["planted"]["in_flight_lead"],
                                                                         truth["planted"]["blocked_lead"]}
    assert len(items) == 20 and "sending issues" in today["summary"]
    shown_tasks = [i for i in items if i["kind"] in ("task", "call") and "task_index" in i]
    overdue = [t["overdue"] for t in shown_tasks]
    assert overdue == sorted(overdue, reverse=True)                  # overdue tasks before the rest


def test_today_respects_closed_leads(client, data):
    _, truth = data
    planted = truth["planted"]
    today = client.get("/api/today").json()
    tasks_by_lead = {}
    for i in today["items"]:
        tasks_by_lead.setdefault(i.get("lead_id"), []).append(i)
    full = client.get("/api/today").json()["counts"]
    store = client.app.state.store
    # Look past the 20-item cap: rebuild the list with only tasks for the closed leads in play
    keep = {planted["erasure_task_lead"], planted["unsub_review_lead"], planted["disqualified_call_task_lead"],
            planted["disqualified_review_lead"]}
    store.write("tasks", [t for t in store.read("tasks") if t.get("lead_id") in keep])
    store.write("leads", [l for l in store.read("leads") if l["id"] in keep])
    items = client.get("/api/today").json()["items"]
    titles = {(i["lead_id"], i["title"]) for i in items}
    assert (planted["erasure_task_lead"], "Erasure request") in titles             # DPDP request is never hidden
    assert (planted["unsub_review_lead"], "Check a reply") in titles
    assert (planted["disqualified_review_lead"], "Check a reply") in titles
    assert not any(i["lead_id"] == planted["disqualified_call_task_lead"] for i in items)   # no call after a no
    assert full["tasks"] > 0


@pytest.mark.parametrize("days, overdue_more", [(0, False), (2, True)])
def test_today_after_two_days_more_tasks_are_overdue(tmp_path, monkeypatch, days, overdue_more):
    store, _ = load_500()
    base = seeded(tmp_path / "a", monkeypatch, store, NOW)
    first = base.get("/api/today").json()
    later = seeded(tmp_path / "b", monkeypatch, load_500()[0], NOW + timedelta(days=days)).get("/api/today").json()
    count = lambda t: int((re.search(r"(\d+) overdue", t["summary"]) or [0, 0])[1])
    assert (count(later) > count(first)) is overdue_more
    assert later["counts"]["problems"] == first["counts"]["problems"]


def test_today_calls_are_leads_who_said_yes_and_drop_once_called(client):
    store = client.app.state.store
    store.write("tasks", [])
    first = client.get("/api/today").json()
    calls = [i for i in first["items"] if i["kind"] == "call"]
    leads = ids_of(client)
    assert calls and all(leads[c["lead_id"]]["stage"] in ("interested", "whatsapp_active") for c in calls)
    called = calls[0]["lead_id"]
    store.update_lead(called, lambda l: {**l, "timeline": [*l["timeline"], {"at": NOW.isoformat(), "kind": "call",
                                                                            "text": "Call outcome: call back"}]})
    later = client.get("/api/today").json()
    assert called not in [i["lead_id"] for i in later["items"] if i["kind"] == "call"]
    assert later["counts"]["calls"] == first["counts"]["calls"] - 1


def test_today_nudges_trials_and_never_starts_closed_leads(client):
    store = client.app.state.store
    store.write("tasks", [])
    store.write("leads", [{**l, "sequence": None} if l["stage"] in ("trial",) + CLOSED_LOST else l
                          for l in store.read("leads") if l["stage"] in ("trial",) + CLOSED_LOST])
    today = client.get("/api/today").json()
    trials = {l["id"] for l in store.read("leads") if l["stage"] == "trial"}
    assert today["counts"]["nudges"] == len(trials) > 0
    assert today["counts"]["starts"] == 0                               # hot but closed: never 'start the sequence'
