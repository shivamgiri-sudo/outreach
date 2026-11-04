"""QA: whole campaign runs through the API on the realistic provider fixtures (fake transports only, fictional data).

Covers dry run vs real run, provenance, dedupe against existing / suppressed / closed leads, Apollo People Match
enrichment, ZeroBounce / NeverBounce verdicts, qualification, auto-enrol through the shared enrol path, the sequence
engine's caps, the caller notification, and a multi-day autorun + 09:30 IST digest simulation.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from app.campaigns import run_due_campaigns
from app.digest import run_digest
from app.hygiene import company_domain
from app.http_util import HttpStatusError
from app.sequences import IST
from app.unsubscribe import unsub_path
from tests.campaign_fakes import FakeIO, by_company, client, connect, create, run

FIX = Path(__file__).parent / "fixtures" / "campaigns-sources"
TARGET = {"industries": ["BPO", "Hospital"], "cities": ["Pune", "Bengaluru", "Gurugram"], "roles_hiring": ["customer support"],
          "company_size_buckets": ["51-200", "201-500"], "keywords": []}
NOW = "2026-11-03T05:00:00+00:00"                     # Tue 3 Nov 2026, 10:30 IST


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def ist(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=IST).astimezone(timezone.utc)


def snapshot(tmp_path) -> dict:
    data = json.loads((tmp_path / "store.json").read_text())
    return {k: data.get(k) for k in ("leads", "jobs", "campaigns", "notifications", "tasks", "outbox", "suppressed")}


def careers_html() -> list:
    pages = [("https://lotus-hospitality.example/careers/", "careers_wordpress_yoast.html"),
             ("https://jobs.lever.co/kappa-care-example/1f0c", "careers_lever_style.html"),
             ("https://jobs.lever.co/orion-diag-example/9a2e", "careers_lever_style_other.html"),
             ("https://sigma-fin.wd5.myworkdayjobs.com/en-US/External/job/Telecaller_JR0042117", "careers_workday_style.html")]
    return [{"url": u, "html": (FIX / f).read_text(encoding="utf-8")} for u, f in pages]


def provider_io(verdicts=None, **overrides) -> FakeIO:
    """Every provider answering from the fixtures. `verdicts` maps an email to a ZeroBounce status (default valid)."""
    p1, p2 = load("apollo_people_api_search_p1.json"), load("apollo_people_api_search_p2.json")
    matches, postings, places = load("apollo_people_match.json"), load("apollo_job_postings.json"), load("places_text_search.json")
    verdicts = verdicts or {}

    def zerobounce(url):
        email = url.split("email=")[1].split("&")[0].replace("%40", "@")
        return {"address": email, "status": verdicts.get(email, "valid"), "sub_status": ""}
    post = {"mixed_people/api_search": lambda body: p1 if body["page"] == 1 else p2,
            "mixed_companies/search": load("apollo_org_search.json"),
            "people/match": lambda body: matches.get(body["id"], {"person": None}),
            "places:searchText": lambda body: places.get(body["textQuery"], {"places": []})}
    get = {"/job_postings": lambda url: postings.get(url.split("/organizations/")[1].split("/")[0], {"organization_job_postings": []}),
           "zerobounce": zerobounce}
    post.update(overrides.pop("post", {}))
    get.update(overrides.pop("get", {}))
    return FakeIO(post=post, get=get, **overrides)


def connected(tmp_path, io, verifier: str = ""):
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak-qa"})
    connect(c, "google_places", {"api_key": "gk-qa"})
    if verifier:
        connect(c, "email_verifier", {"provider": verifier, "api_key": "vk-qa"})
    return c


ALL_SOURCES = {"apollo_search": True, "apollo_hiring": True, "google_places": True, "max_results": 100}


def test_dry_run_on_all_sources_writes_nothing_and_matches_the_real_run(tmp_path):
    io = provider_io()
    c = connected(tmp_path, io)
    camp = create(c, target=TARGET, sources={**ALL_SOURCES, "careers_html": careers_html()},
                  rules={"max_new_leads_per_run": 200})
    before = snapshot(tmp_path)
    preview = run(c, camp["id"], dry_run=True, now=NOW)
    assert snapshot(tmp_path) == before                                    # no leads, jobs, runs, notifications
    found = {s: v["found"] for s, v in preview["sources"].items()}
    assert found == {"apollo_search": 99, "apollo_hiring": 30, "careers_page": 4, "google_places": 100}
    assert preview["discovered"] == 233 and preview["credits_used_estimate"] == 1 + 31 + 11
    assert len(io.urls("people/match")) == 0                               # a preview never spends enrichment credits
    assert not [e for e in preview["errors"] if e["source"] != "Careers page"]

    real = run(c, camp["id"], now=NOW)
    assert real["new"] == preview["new"] and real["new"] > 150 and real["merged"] == 0
    leads = c.get("/api/leads").json()
    assert len(leads) == real["new"] and all(l["campaign_id"] == camp["id"] for l in leads)
    keys = [l["dedupe_key"] for l in leads]
    assert len(keys) == len(set(keys))                                     # one lead per company
    for lead in leads:
        assert lead["provenance"] and lead["timeline"][0]["text"].startswith("Added by campaign")
        assert all(v.split(":")[0] in ("apollo_search", "apollo_hiring", "careers_page", "google_places")
                   for v in lead["provenance"].values())
        assert not any(h in (lead.get("domain") or "") for h in ("lever.co", "myworkdayjobs.com", "facebook.com", "linkedin.com"))
    kappa, orion = by_company(c, "Kappa Care Services"), by_company(c, "Orion Diagnostics")      # same ATS host, two leads
    assert kappa["id"] != orion["id"] and kappa["open_roles"] == orion["open_roles"] == 1
    assert by_company(c, "Lotus Hospitality Pvt. Ltd.")["domain"] == "lotus-hospitality.example"


def test_rerun_next_day_dedupes_and_never_touches_closed_or_later_stage_leads(tmp_path):
    places = load("places_text_search.json")["BPO in Pune"]["places"]
    open_places = [p for p in places if p["businessStatus"] != "CLOSED_PERMANENTLY" and p.get("websiteUri")]
    gone, warm, filler, blocked = open_places[:4]
    io = provider_io()
    c = connected(tmp_path, io)
    rows = [f"{gone['displayName']['text'].upper()},Old Contact,old@{company_domain(gone['websiteUri'])},{gone['websiteUri']},",
            f"{warm['displayName']['text']},Ravi,ravi@{company_domain(warm['websiteUri'])},{warm['websiteUri']},",
            f"{filler['displayName']['text']},Kept Name,,{filler['websiteUri']},",
            f"{blocked['displayName']['text']},,,{blocked['websiteUri']},09000 1{len(places):04d}"]
    assert c.post("/api/leads/import", json={"csv": "company,contact_name,email,website,phone\n" + "\n".join(rows) + "\n",
                                             "source": "manual"}).status_code == 200
    store = c.app.state.store
    stages = {gone["displayName"]["text"].upper(): "unsubscribed", warm["displayName"]["text"]: "contacted",
              blocked["displayName"]["text"]: "disqualified"}
    store.update("leads", lambda ls: [{**l, "stage": stages.get(l["company"], l["stage"])} for l in ls])
    before = {l["id"]: l for l in c.get("/api/leads").json()}
    camp = create(c, target={**TARGET, "industries": ["BPO"], "cities": ["Pune"]},
                  sources={"google_places": True, "max_results": 20}, rules={"max_new_leads_per_run": 200})
    first = run(c, camp["id"], now=NOW)
    assert first["suppressed_skipped"] == 2 and first["duplicates"] == 2 and first["new"] == 19 - 4
    for lead_id, old in before.items():
        now_lead = next(l for l in c.get("/api/leads").json() if l["id"] == lead_id)
        assert now_lead == old                                            # closed, later-stage and phone-less filler untouched
    second = run(c, camp["id"], now=ist(4, 11).isoformat())
    assert second["new"] == 0 and second["duplicates"] + second["suppressed_skipped"] == 19
    assert len(c.get("/api/leads").json()) == 4 + first["new"]


def test_people_match_fills_only_empty_fields_and_verifier_gates_qualification(tmp_path):
    matches = load("apollo_people_match.json")
    emails = [m["person"]["email"] for m in matches.values() if m.get("person") and m["person"].get("email")]
    verdicts = {e: s for e, s in zip(emails, ["invalid", "catch-all", "unknown", "spamtrap"])}
    io = provider_io(verdicts=verdicts)
    c = connected(tmp_path, io, verifier="zerobounce")
    camp = create(c, target=TARGET, sources={"apollo_search": True, "max_results": 100},
                  enrich={"apollo_match": True}, rules={"max_new_leads_per_run": 200, "min_score": 0})
    report = run(c, camp["id"], now=NOW)
    leads = c.get("/api/leads").json()
    assert len(io.urls("people/match")) == report["new"] and report["new"] == len(leads)
    with_email = [l for l in leads if l.get("email")]
    assert not any(e["email"].startswith("email_not_unlocked") for e in with_email)
    assert report["enriched_emails"] == len(with_email) and len(with_email) <= 54
    assert all(l["provenance"]["email"] == "apollo" or l["email_source"].startswith("apollo") for l in with_email)
    by_status = report["verification"]
    assert by_status["invalid"] >= 1 and by_status["risky"] >= 1 and sum(by_status.values()) == len(with_email)
    for lead in with_email:
        check = lead["campaign_check"]
        if lead["email_check"]["status"] in ("invalid", "risky") or company_domain(lead["email"]) == "gmail.com":
            assert check["qualified"] is False                             # free-mail and failed checks never qualify
    assert report["qualified"] == sum(l["campaign_check"]["qualified"] for l in leads)
    calls = len(io.urls("people/match")), len(io.urls("zerobounce"))
    run(c, camp["id"], now=ist(4, 11).isoformat())
    assert (len(io.urls("people/match")), len(io.urls("zerobounce"))) == calls      # nothing paid for twice


def test_verifier_outage_leaves_emails_unverified_and_a_later_run_checks_them(tmp_path):
    """NeverBounce answers an outage with HTTP 200 + status temp_unavail; that is no verdict (was cached as 'risky')."""
    outage = {"status": "temp_unavail", "message": "A temporary service interruption occurred", "execution_time": 12}
    state = {"reply": outage}
    io = provider_io(get={"neverbounce": lambda url: state["reply"]})
    c = connected(tmp_path, io, verifier="neverbounce")
    camp = create(c, target=TARGET, sources={"apollo_search": True, "max_results": 25},
                  enrich={"apollo_match": True}, rules={"min_score": 0})
    first = run(c, camp["id"], now=NOW)
    assert first["verification"]["risky"] == 0 and first["verification"]["unverified"] >= 1
    assert any("stay unverified" in e["message"] for e in first["errors"])
    state["reply"] = {"status": "success", "result": "valid", "flags": ["has_dns", "has_dns_mx"], "execution_time": 210}
    second = run(c, camp["id"], now=ist(4, 11).isoformat())
    assert second["verification"]["valid"] == first["verification"]["unverified"]


def test_places_quota_midway_keeps_paid_rows_and_says_why(tmp_path):
    places = load("places_text_search.json")
    quota = HttpStatusError(429, json.dumps(load("provider_errors.json")["places_429_quota"]))

    def reply(body):
        if body["textQuery"].startswith("Hospital"):
            raise quota
        return places[body["textQuery"]]
    io = provider_io(post={"places:searchText": reply})
    c = connected(tmp_path, io)
    camp = create(c, target=TARGET, sources={"google_places": True, "max_results": 100}, rules={"max_new_leads_per_run": 200})
    report = run(c, camp["id"], now=NOW)
    assert report["sources"]["google_places"] == {"found": 57, "requests": 6} and report["new"] == 57
    assert any(e["source"] == "Google Places" and "429" in e["message"] for e in report["errors"])


def test_job_postings_not_in_plan_keeps_the_hiring_companies(tmp_path):
    io = provider_io(get={"/job_postings": PermissionError("HTTP 403")})
    c = connected(tmp_path, io)
    camp = create(c, target=TARGET, sources={"apollo_hiring": True, "max_results": 100}, rules={"max_new_leads_per_run": 200})
    report = run(c, camp["id"], now=NOW)
    assert report["sources"]["apollo_hiring"]["found"] == 30 and report["new"] >= 25
    assert len(io.urls("/job_postings")) == 1
    assert any("could not be read for 30" in e["message"] for e in report["errors"])


def test_auto_enrol_respects_unsubscribe_and_the_engine_keeps_its_caps(tmp_path):
    io = provider_io()
    c = connected(tmp_path, io, verifier="zerobounce")
    c.put("/api/settings", json={"per_run_cap": 5})
    camp = create(c, target=TARGET, sources={"apollo_search": True, "max_results": 100},
                  enrich={"apollo_match": True}, rules={"auto_enrol": True, "max_new_leads_per_run": 200, "min_score": 0})
    report = run(c, camp["id"], now=NOW)
    enrolled = [l for l in c.get("/api/leads").json() if l.get("sequence")]
    assert report["enrolled"] == len(enrolled) == report["qualified"] and len(enrolled) >= 20
    assert c.get("/api/outbox").json() == []                               # the campaign itself sends nothing
    store = c.app.state.store
    quitter = enrolled[0]
    assert c.post(unsub_path(store, quitter["id"])).status_code in (200, 303)
    sent = c.post("/api/sequences/run", json={"now": "2026-11-03T05:30:00+00:00"}).json()
    assert sent["sent"] == 5 and sent["mode"] == "practice"
    outbox = c.get("/api/outbox").json()
    assert {m["status"] for m in outbox} == {"dry_run"} and quitter["email"] not in {m["to"] for m in outbox}
    assert len({company_domain(m["to"]) for m in outbox}) == len(outbox)   # one email per domain per day
    quit_lead = by_company(c, quitter["company"])
    assert quit_lead["stage"] == "unsubscribed"
    again = run(c, camp["id"], now=ist(4, 11).isoformat())
    assert again["enrolled"] == 0 and again["new"] == 0
    after = by_company(c, quitter["company"])
    assert after["stage"] == "unsubscribed" and after["sequence"] == quit_lead["sequence"]      # never restarted
    assert c.post(f"/api/leads/{quitter['id']}/enroll").status_code == 400                    # the button refuses too


def test_two_weeks_of_autorun_and_morning_digest(tmp_path):
    io = provider_io()
    c = connected(tmp_path, io)
    store = c.app.state.store
    camp = create(c, target=TARGET, sources={"google_places": True, "max_results": 40})
    c.post(f"/api/campaigns/{camp['id']}/resume")
    transports, runs, digests = io.transports(), [], []
    for day in range(2, 16):                                               # Mon 2 Nov .. Sun 15 Nov, every two hours
        for hour in range(7, 22, 2):
            runs += [(day, hour)] * len(run_due_campaigns(store, ist(day, hour, 5), transports))
            if run_digest(store, ist(day, hour, 31)):
                digests.append((day, hour))
    weekdays = [d for d in range(2, 16) if datetime(2026, 11, d).weekday() < 5]
    assert runs == [(d, 9) for d in weekdays]                              # first tick in business hours, once a day
    assert digests == [(d, 9) for d in weekdays]                           # 09:31 IST, once a weekday
    saved = c.get(f"/api/campaigns/{camp['id']}").json()
    assert saved["stats"]["runs"] == 10 and saved["stats"]["leads_added"] == 40      # later days only find duplicates
    notes = [n for n in store.read("notifications") if n["kind"] == "campaign"]
    assert len(notes) == 10 and notes[0]["title"].startswith(f"Campaign {camp['name']}")
