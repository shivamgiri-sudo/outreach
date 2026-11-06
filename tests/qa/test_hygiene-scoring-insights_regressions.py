"""Regression tests for the bugs found in the hygiene / scoring / Today / Insights QA run (one small fixture each).
All names, numbers and domains are fictional."""
from fastapi.testclient import TestClient

from app.hygiene_report import duplicate_clusters
from app.main import _present, create_app
from app.scoring import score_lead
from app.standardise import clean_person_name, names_compatible, person_key
from tests.qa.hsi_helpers import NOW, seeded

AT = "2026-10-20T05:00:00+00:00"


def lead(lid, **extra) -> dict:
    return {"id": lid, "company": "", "contact_name": "", "email": None, "phone": None, "stage": "new", "source": "manual",
            "consent": None, "engagement": {"opens": 0, "clicks": 0, "replies": 0}, "created_at": AT,
            "timeline": [{"at": AT, "kind": "created", "text": "Imported"}], **extra}


def grouped(leads) -> set:
    return {frozenset(c["lead_ids"]) for c in duplicate_clusters(leads)}


# ---------------------------------------------------------------- hygiene
def test_all_caps_last_comma_first_keeps_the_first_name():
    assert clean_person_name("NAIDU, FARAH") == "Naidu, Farah"
    assert person_key("NAIDU, FARAH") == person_key("Farah Naidu")
    assert names_compatible("CHATTERJEE, NITIN", "Nitin Chatterjee")
    assert clean_person_name("RAVI KUMAR, MBA") == "Ravi Kumar"                 # real credentials still go
    assert clean_person_name("Anita Rao, SHRM-CP") == "Anita Rao"
    assert grouped([lead("a", company="Indus Tech Labs", contact_name="Farah Naidu", email="farah.naidu@indus.example"),
                    lead("b", contact_name="NAIDU, FARAH", email="fnaidu@indus.example", domain="indus.example")]) == {
        frozenset({"a", "b"})}


def test_a_family_number_groups_the_second_persons_two_leads_whatever_the_order():
    rows = [lead("dev", company="Rao Textile Mills", contact_name="Dev Rao", phone="+919000020011", city="Surat"),
            lead("anita", company="Shree Rao Fabrics", contact_name="Anita Rao", phone="+919000020011", city="Surat"),
            lead("a_rao", company="RAO FABRICS", contact_name="A. RAO", phones=["+91-90000-20011"])]
    assert grouped(rows) == {frozenset({"anita", "a_rao"})}
    assert grouped(rows[::-1]) == {frozenset({"anita", "a_rao"})}


def test_a_cityless_row_does_not_chain_same_named_firms_in_different_cities():
    rows = [lead(f"s{i}", company="Sai Enterprises", city=city) for i, city in enumerate(("Pune", "Lucknow", "Surat"))]
    blank = lead("blank", company="SAI ENTERPRISES", country="India")
    assert grouped(rows + [blank]) == set()
    # one city only: the city-less row is still the same firm
    assert grouped([rows[0], blank]) == {frozenset({"s0", "blank"})}
    assert grouped([rows[0], blank, lead("s0b", company="Sai Enterprises Pvt Ltd", city="Poona")]) == {
        frozenset({"s0", "blank", "s0b"})}


def test_merge_moves_activities_and_consent_rows_so_export_and_erasure_cover_them(tmp_path, monkeypatch):
    c = seeded(tmp_path, monkeypatch, {
        "leads": [lead("keep", company="Kaveri Retail", contact_name="Priyanka Das", email="priyanka@kaveri.example",
                       stage="engaged"),
                  lead("dup", company="KAVERI RETAIL PVT. LTD.", contact_name="MR. PRIYANKA DAS",
                       email="priyanka@kaveri.example", stage="interested",
                       consent={"text": "Haan, bhejiye", "at": AT, "source": "email reply", "channel": "any"})],
        "activities": [{"id": "a1", "lead_id": "dup", "type": "call", "text": "Spoke to Priyanka, wants Hindi screens",
                        "at": AT}],
        "consent_ledger": [{"id": "cl1", "lead_id": "dup", "event": "given", "channel": "any", "text": "Haan, bhejiye",
                            "source": "email reply", "at": AT}],
        "transactional_outbox": [{"lead_id": "dup", "to": "priyanka@kaveri.example", "kind": "meeting_invite"}],
    })
    body = c.post("/api/hygiene/merge", json={"keep_id": "keep", "merge_ids": ["dup"]}).json()
    assert body["repointed"]["activities"] == 1 and body["repointed"]["consent_ledger"] == 1
    assert body["repointed"]["transactional_outbox"] == 1 and body["lead"]["stage"] == "interested"
    export = c.get("/api/leads/keep/export").json()
    assert [a["id"] for a in export["activities"]] == ["a1"]
    assert [r["id"] for r in export["consent_ledger"]] == ["cl1"]
    assert c.post("/api/leads/keep/erase", json={"confirm": True}).status_code == 200
    store = c.app.state.store
    assert store.read("activities") == [] and store.read("transactional_outbox") == []
    assert all(r["text"] == "" and r["lead_id"].startswith("erased:") for r in store.read("consent_ledger"))


# ---------------------------------------------------------------- scoring
REALISTIC = [
    # (lead, expected band, expected fit)  -- fit = 9 for size 20+ (no upper limit), +6 for India.
    # Open roles count on a log scale with full marks from 50 roles (was: full marks from 10). These examples used to
    # carry 10-25 roles; they now carry 60 so the India / not-India cases still sit either side of the hot line (70).
    ({"company": "Shivam Auto Components", "country": "India", "company_size": 320, "open_roles": 60,
      "max_job_age_days": 45}, "hot", 15),
    ({"company": "Ganesh Retail", "country": "IND", "company_size": 150, "open_roles": 60, "max_job_age_days": 30}, "hot", 15),
    ({"company": "Kaveri Agro Foods", "country": "", "city": "Bangalore", "company_size": 150, "open_roles": 60,
      "max_job_age_days": 30}, "hot", 15),
    ({"company": "Narmada Logistics", "country": "", "phone": "+91 90000 10077", "company_size": 150, "open_roles": 60,
      "max_job_age_days": 30}, "hot", 15),
    ({"company": "Gulf Staffing FZE", "country": "AE", "city": "Dubai", "company_size": 150, "open_roles": 60,
      "max_job_age_days": 30}, "warm", 9),
    ({"company": "Ganesh Retail", "country": "india", "company_size": 150, "open_roles": 60, "max_job_age_days": 28},
     "warm", 15),                                                                       # 30 + 23.3 + 15 = 68
    ({"company": "Ganesh Retail", "country": "IND", "company_size": 150, "open_roles": 10, "max_job_age_days": 30},
     "warm", 15),                                                                       # 18.3 + 25 + 15 = 58: 10 roles is no longer full marks
    ({"company": "Tiny Tiffins", "country": "IN", "company_size": 8, "open_roles": 1}, "cold", 6),
    ({"company": "Pearl Edutech", "country": "IN", "company_size": 60, "open_roles": 20}, "cold", 15),  # 23.2 + 15 = 38
    ({"company": "Pearl Edutech", "country": "IN", "company_size": 60, "open_roles": 20,
      "engagement": {"opens": 1, "clicks": 0, "replies": 0}}, "warm", 15),             # + 3.3 = 42
    ({"company": "Big BPO Services", "country": "IN", "company_size": 5000, "open_roles": 80,
      "max_job_age_days": 30}, "hot", 15),                                              # 5,000 staff used to score 0 for size
]


def test_realistic_leads_land_in_the_right_band_with_india_detected():
    for raw, expected_band, expected_fit in REALISTIC:
        result = score_lead(raw)
        assert (result["band"], result["signals"]["fit"]) == (expected_band, expected_fit), raw


def test_engagement_and_consent_signals():
    keen = score_lead({"engagement": {"opens": 2, "clicks": 1, "replies": 1}, "consent_yes": True})
    assert keen["signals"]["engagement"] == 20 and keen["signals"]["intent"] == 10
    presented = _present(lead("x", consent={"text": "Yes please", "at": AT}, country="IN", company_size=50))
    assert presented["signals"]["intent"] == 10 and presented["signals"]["fit"] == 15


# ---------------------------------------------------------------- insights and dashboard
def _closed_but_hot(lid, stage, **extra):
    return lead(lid, company=f"Firm {lid}", stage=stage, country="IN", company_size=300, open_roles=60,
                max_job_age_days=60, email=f"hr@{lid}.example", **extra)


def test_insights_top_leads_skip_closed_and_failed_sends_are_not_sent(tmp_path, monkeypatch):
    c = seeded(tmp_path, monkeypatch, {
        "leads": [_closed_but_hot("u1", "unsubscribed"), _closed_but_hot("d1", "disqualified"),
                  lead("w1", company="Warm Co", country="IN", company_size=100, open_roles=5),
                  lead("i1", company="Sheet Yes", stage="interested", source="bmi_sheet:demo"),
                  lead("i2", company="Sheet Yes Two", stage="interested", source="bmi_sheet:demo"),
                  lead("e1", company="Emailed", stage="contacted", email="ravi@emailed.example")],
        "outbox": [{"lead_id": "e1", "status": "dry_run", "to": "ravi@emailed.example"},
                   {"lead_id": "w1", "status": "failed", "error": "550 mailbox unavailable"},
                   {"lead_id": "w1", "status": "provider_error", "error": "provider down"}],
    })
    ins = c.get("/api/insights").json()
    assert {t["id"] for t in ins["top_leads"]} == {"w1", "e1", "i1", "i2"}     # hot but closed leads are left out
    # practice emails (dry_run) are not "sent" anywhere (one definition across Dashboard, Analytics, Insights)
    assert ins["totals"]["emails_sent"] == 0 and ins["outreach"]["emailed_leads"] == 0
    assert ins["outreach"]["interested"] == 2 and ins["outreach"]["interested_rate"] == 0.0   # not 200 %
    assert ins["outreach"]["unsubscribe_rate"] == 0.0


def test_dashboard_reminders_skip_disqualified_hot_leads(tmp_path, monkeypatch):
    c = seeded(tmp_path, monkeypatch, {"leads": [_closed_but_hot("d1", "disqualified"), _closed_but_hot("n1", "new")]})
    dash = c.get("/api/dashboard").json()
    assert dash["hot"] == 2 and [r["lead_id"] for r in dash["reminders"]] == ["n1"]


# ---------------------------------------------------------------- Today and the task board
CSV = ("company,contact_name,email,phone,website,open_roles,company_size,country\n"
       "Kaveri Retail,Priyanka Das,priyanka@kaveri.example,9000010001,https://kaveri.example,12,200,IN\n")


def test_an_erasure_request_shows_on_today_and_the_board_after_the_lead_unsubscribes(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "d.json"))
    c.post("/api/leads/import", json={"csv": CSV, "source": "apollo"})
    lid = c.get("/api/leads").json()[0]["id"]
    r = c.post(f"/api/leads/{lid}/reply", json={"text": "Please delete all my data. Mera data delete karo."}).json()
    assert r["lead"]["stage"] == "unsubscribed"
    items = c.get("/api/today").json()["items"]
    assert [(i["lead_id"], i["title"], i["priority"]) for i in items] == [(lid, "Erasure request", "high")]
    board = c.get("/api/tasks/board").json()
    assert any(t["kind"] == "erasure_request" for bucket in ("overdue", "today", "upcoming", "no_date")
               for t in board.get(bucket, []))


def test_a_call_task_for_a_lead_who_later_said_no_is_not_on_today(tmp_path, monkeypatch):
    c = seeded(tmp_path, monkeypatch, {
        "leads": [lead("d1", company="Changed Mind Co", stage="disqualified"),
                  lead("y1", company="Still Keen Co", stage="interested")],
        "tasks": [{"id": "t1", "lead_id": "d1", "kind": "call", "title": "Call them", "status": "open",
                   "due_at": NOW.isoformat(), "text": "Said yes by email."},
                  {"id": "t2", "lead_id": "d1", "kind": "review", "title": "Check a reply", "status": "open",
                   "due_at": NOW.isoformat(), "text": "Actually, yes, send it"},
                  {"id": "t3", "lead_id": "y1", "kind": "call", "title": "Call them", "status": "open",
                   "due_at": NOW.isoformat()}]})
    today = c.get("/api/today").json()
    assert {(i["lead_id"], i["title"]) for i in today["items"]} == {("d1", "Check a reply"), ("y1", "Call them")}
    assert today["counts"]["calls"] == 1
