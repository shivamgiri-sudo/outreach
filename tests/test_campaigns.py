"""The campaign pipeline end to end through the API, with fake transports only."""
import json

from app import campaigns, routes_outreach
from app.campaign_pipeline import qualify_reasons
from tests.campaign_fakes import ACME_JOBS, PERSON, ZETA_JOBS, FakeIO, by_company, client, connect, create, run

ACME_PAGE = {"url": "https://acme-retail.in/careers", "html": ACME_JOBS}
ZETA_PAGE = {"url": "https://zeta-foods.in/careers", "html": ZETA_JOBS}
CONTACT_PAGE = '<p>Write to <a href="mailto:careers@zeta-foods.in">careers@zeta-foods.in</a> or call 98765 11223</p>'


def apollo_io(**extra):
    return FakeIO(post={"mixed_people/api_search": {"people": [PERSON]}}, **extra)


def snapshot(tmp_path) -> dict:
    data = json.loads((tmp_path / "store.json").read_text())
    return {k: data.get(k) for k in ("leads", "jobs", "campaigns", "notifications", "tasks", "outbox", "suppressed")}


def test_dry_run_discovers_and_scores_but_writes_nothing(tmp_path):
    io = apollo_io()
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak-test"})
    camp = create(c, sources={"apollo_search": True, "careers_html": [ACME_PAGE, ZETA_PAGE]})
    before = snapshot(tmp_path)
    report = run(c, camp["id"], dry_run=True)
    assert snapshot(tmp_path) == before                                  # no leads, jobs, runs or notifications saved
    assert report["dry_run"] and report["discovered"] == 3 and report["new"] == 2
    rows = {r["company"]: r for r in report["preview"]}
    acme = rows["Acme Retail Pvt Ltd"]                                   # Apollo person + careers page merged into one
    assert acme["would"] == "add" and acme["email"] == "asha@acme-retail.in" and acme["score"] == 70 and acme["qualified"]
    assert acme["sources"] == ["Apollo people search", "Careers page"] and not acme["would_enrol"]   # auto_enrol is off
    zeta = rows["Zeta Foods"]
    assert zeta["qualified"] is False and "no email yet (the real run looks for one)" in zeta["reasons"]
    assert report["credits_used_estimate"] == 1 and report["sources"]["apollo_search"] == {"found": 1, "requests": 1}


def test_real_run_adds_leads_with_campaign_tag_timeline_and_provenance(tmp_path):
    io = apollo_io()
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak-test"})
    camp = create(c, sources={"apollo_search": True, "careers_html": [ACME_PAGE]})
    report = run(c, camp["id"])
    assert report["new"] == 1 and report["qualified"] == 1 and report["enrolled"] == 0 and report["finished_at"]
    acme = by_company(c, "Acme Retail Pvt Ltd")
    assert acme["campaign_id"] == camp["id"] and acme["open_roles"] == 10 and acme["country"] == "IN"
    assert acme["timeline"][0]["text"] == "Added by campaign Pune support hiring from Apollo people search, Careers page"
    assert acme["provenance"]["email"] == "apollo_search:https://app.apollo.io" and acme["email_source"].startswith("apollo_search")
    assert acme["provenance"]["open_roles"] == "careers_page:https://acme-retail.in/careers"
    assert acme["campaign_check"]["qualified"] is True and acme["email_check"]["status"] == "unverified"
    assert acme["apollo_person_id"] == "p-asha" and acme.get("sequence") is None
    saved = c.get(f"/api/campaigns/{camp['id']}").json()
    assert saved["stats"]["runs"] == 1 and saved["stats"]["leads_added"] == 1 and len(saved["runs"]) == 1
    assert [l["company"] for l in c.get(f"/api/campaigns/{camp['id']}/leads").json()] == ["Acme Retail Pvt Ltd"]
    assert c.get(f"/api/campaigns/{camp['id']}/runs").json()[0]["id"] == report["id"]
    assert any(j["company"] == "Acme Retail" for j in c.get("/api/jobs").json())   # same job store as Read job pages
    # running again the same day adds nothing new
    again = run(c, camp["id"])
    assert again["new"] == 0 and again["duplicates"] == 1 and len(c.get("/api/leads").json()) == 1


def test_dedupe_existing_suppressed_and_later_stage_leads(tmp_path):
    io = FakeIO(post={"api_search": {"people": [
        PERSON,                                                                                      # same email as an existing lead
        {"id": "x1", "name": "Meera Iyer", "email": "meera@gone-co.in", "organization": {"name": "Gone Co", "primary_domain": "gone-co.in"}},
        {"id": "x2", "name": "Kiran Das", "email": "kiran@warm-co.in", "organization": {"name": "Warm Co", "primary_domain": "warm-co.in"}},
        {"id": "x3", "name": "Nisha Pal", "email": "nisha@blocked-co.in", "organization": {"name": "Blocked Co", "primary_domain": "blocked-co.in"}},
        {"id": "x4", "name": "Omar Shah", "email": "omar@fill-co.in", "organization": {"name": "Fill Co", "primary_domain": "fill-co.in"}},
        {"id": "x5", "name": "Lata Sen", "email": "lata@fresh-co.in", "organization": {"name": "Fresh Co", "primary_domain": "fresh-co.in"}}]}})
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    csv = ("company,contact_name,email,website\nAcme Retail,Asha Rao,asha@acme-retail.in,https://acme-retail.in\n"
           "Gone Co,Old Contact,old@gone-co.in,https://gone-co.in\nWarm Co,Ravi,ravi@warm-co.in,https://warm-co.in\n"
           "Fill Co,,,https://fill-co.in\n")
    c.post("/api/leads/import", json={"csv": csv, "source": "manual"})
    store = c.app.state.store
    store.update("leads", lambda rows: [{**l, "stage": {"Gone Co": "unsubscribed", "Warm Co": "interested"}.get(l["company"], l["stage"])} for l in rows])
    store.update("suppressed", lambda rows: [*rows, "nisha@blocked-co.in"])
    warm_before = by_company(c, "Warm Co")
    camp = create(c, sources={"apollo_search": True})
    report = run(c, camp["id"])
    assert report["new"] == 1 and report["merged"] == 1 and report["duplicates"] == 2 and report["suppressed_skipped"] == 2
    names = [l["company"] for l in c.get("/api/leads").json()]
    assert names.count("Gone Co") == 1 and names.count("Warm Co") == 1 and "Blocked Co" not in names and "Fresh Co" in names
    warm = by_company(c, "Warm Co")
    assert {k: warm[k] for k in ("stage", "email", "timeline", "contact_name")} == \
           {k: warm_before[k] for k in ("stage", "email", "timeline", "contact_name")}       # later stage: untouched
    fill = by_company(c, "Fill Co")                                                         # company-only early lead completed
    assert fill["email"] == "omar@fill-co.in" and fill["contact_name"] == "Omar Shah" and fill["campaign_id"] == camp["id"]
    assert "Details added by campaign" in fill["timeline"][-1]["text"]
    assert by_company(c, "Gone Co")["stage"] == "unsubscribed" and by_company(c, "Gone Co").get("campaign_id") is None


def test_enrichment_fills_only_empty_fields_and_is_not_repeated(tmp_path):
    io = FakeIO(pages={"https://zeta-foods.in/contact": CONTACT_PAGE},
                get={"hunter.io": {"data": {"emails": [{"value": "hr@acme-retail.in", "confidence": 80}]}}})
    c = client(tmp_path, io)
    connect(c, "hunter", {"api_key": "hk"})
    c.post("/api/leads/import", json={"csv": "company,phone,website\nAcme Retail,9811100011,https://acme-retail.in\n", "source": "manual"})
    acme_id = by_company(c, "Acme Retail")["id"]
    camp = create(c, sources={"careers_html": [ZETA_PAGE], "csv_import_ids": [acme_id]}, enrich={"crawler": True, "hunter": True})
    report = run(c, camp["id"])
    zeta, acme = by_company(c, "Zeta Foods"), by_company(c, "Acme Retail")
    assert zeta["email"] == "careers@zeta-foods.in" and zeta["phone"] == "+919876511223"
    assert zeta["email_source"] == "website:https://zeta-foods.in/contact" and zeta["provenance"]["email"].startswith("website:")
    assert acme["campaign_id"] == camp["id"] and acme["email"] == "hr@acme-retail.in" and acme["phone"] == "+919811100011"
    assert acme["email_source"].startswith("hunter") and report["enriched_emails"] == 2 and report["attached"] == 1
    hunter_calls = len(io.urls("hunter.io"))
    assert hunter_calls == 1                                    # Zeta already had an email from its website, so no paid lookup
    run(c, camp["id"])
    assert len(io.urls("hunter.io")) == hunter_calls             # already checked: not asked again


def test_qualification_thresholds():
    rules = {"min_score": 40, "require_business_email": True, "skip_role_addresses": False}
    ok = {"email": "asha@acme-retail.in"}
    assert qualify_reasons(ok, rules, 55) == []
    assert qualify_reasons(ok, rules, 39) == ["score 39 is below 40"]
    assert qualify_reasons({"email": "asha@gmail.com"}, rules, 80) == ["free-mail address, not a business email"]
    assert qualify_reasons({"email": "asha@gmail.com"}, {**rules, "require_business_email": False}, 80) == []
    assert qualify_reasons({"email": "hr@acme-retail.in"}, rules, 80) == []                    # shared mailboxes allowed by default
    assert qualify_reasons({"email": "hr@acme-retail.in"}, {**rules, "skip_role_addresses": True}, 80) == ["shared mailbox (hr@, info@…)"]
    bad = {**ok, "email_check": {"email": ok["email"], "status": "invalid"}}
    risky = {**ok, "email_check": {"email": ok["email"], "status": "risky"}}
    assert "does not work" in qualify_reasons(bad, rules, 80)[0] and "could not confirm" in qualify_reasons(risky, rules, 80)[0]
    assert qualify_reasons({}, rules, 80) == ["no email found yet"]


def test_verifier_result_blocks_qualification(tmp_path):
    io = apollo_io(get={"zerobounce": {"status": "invalid"}})
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    connect(c, "email_verifier", {"provider": "zerobounce", "api_key": "zk"})
    camp = create(c, sources={"apollo_search": True, "careers_html": [ACME_PAGE]}, rules={"auto_enrol": True})
    report = run(c, camp["id"])
    acme = by_company(c, "Acme Retail Pvt Ltd")
    assert report["verification"]["invalid"] == 1 and report["qualified"] == 0 and report["enrolled"] == 0
    assert acme.get("sequence") is None and acme["email_check"]["provider"] == "zerobounce"


def test_auto_enrol_goes_through_the_manual_enrol_checks(tmp_path):
    assert campaigns.enroll_lead is routes_outreach.enroll_lead          # one code path for the button and campaigns
    io = apollo_io()
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    c.post("/api/leads/import", json={"csv": "company,email,website,open_roles\nOld Enrolled,ops@old-enrolled.in,https://old-enrolled.in,12\n",
                                      "source": "manual"})
    old = by_company(c, "Old Enrolled")
    c.post(f"/api/leads/{old['id']}/enroll")
    enrolled_at = by_company(c, "Old Enrolled")["sequence"]["enrolled_at"]
    camp = create(c, sources={"apollo_search": True, "careers_html": [ACME_PAGE], "csv_import_ids": [old["id"]]},
                  rules={"auto_enrol": True})
    report = run(c, camp["id"])
    acme = by_company(c, "Acme Retail Pvt Ltd")
    assert report["enrolled"] == 1 and acme["sequence"]["sent_days"] == [] and acme["sequence"]["stopped"] is None
    assert acme["timeline"][-1]["text"] == "Enrolled in email sequence by campaign Pune support hiring"
    assert by_company(c, "Old Enrolled")["sequence"]["enrolled_at"] == enrolled_at        # never restarted
    assert any("left out" in e["message"] for e in report["errors"])
    # the shared function refuses what the button refuses
    store = c.app.state.store
    store.update("suppressed", lambda rows: [*rows, "asha@acme-retail.in"])
    store.update_lead(acme["id"], lambda l: {**l, "sequence": None})
    r = c.post(f"/api/leads/{acme['id']}/enroll")
    assert r.status_code == 400 and "suppression" in r.json()["detail"]
    c.post("/api/leads/import", json={"csv": "company,phone\nNo Mail Co,9811100022\n", "source": "manual"})
    assert c.post(f"/api/leads/{by_company(c, 'No Mail Co')['id']}/enroll").status_code == 400


def test_run_never_sends_and_the_engine_keeps_its_caps(tmp_path):
    pages = [{"url": f"https://co{i}.in/careers", "html": ACME_JOBS.replace("Acme Retail", f"Co {i}").replace("acme-retail.in", f"co{i}.in")}
             for i in range(3)]
    people = [{"id": f"p{i}", "name": f"Person {i}", "email": f"person{i}@co{i}.in", "organization": {"name": f"Co {i}", "primary_domain": f"co{i}.in"}}
              for i in range(3)]
    io = FakeIO(post={"api_search": {"people": people}})
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    c.put("/api/settings", json={"per_run_cap": 2})
    camp = create(c, sources={"apollo_search": True, "careers_html": pages}, rules={"auto_enrol": True})
    assert run(c, camp["id"])["enrolled"] == 3
    assert c.get("/api/outbox").json() == []                             # the campaign itself sends nothing
    sent = c.post("/api/sequences/run", json={"now": "2026-11-03T05:30:00+00:00"}).json()
    assert sent["sent"] == 2 and sent["mode"] == "practice"              # engine: per-run cap and practice mode
    assert {m["status"] for m in c.get("/api/outbox").json()} == {"dry_run"}
    night = c.post("/api/sequences/run", json={"now": "2026-11-03T16:00:00+00:00"}).json()
    assert night["sent"] == 0 and "business hours" in night["skipped"]


def test_caller_is_notified_and_yes_reply_task_names_the_campaign(tmp_path):
    io = apollo_io()
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    camp = create(c, sources={"apollo_search": True, "careers_html": [ACME_PAGE]}, rules={"auto_enrol": True})
    run(c, camp["id"])
    note = c.get("/api/notifications").json()[0]
    assert note["to"] == "Priya" and note["title"] == "Campaign Pune support hiring: 1 new leads, 1 enrolled"
    assert "practice or live mode" in note["text"] and note["kind"] == "campaign"
    acme = by_company(c, "Acme Retail Pvt Ltd")
    c.post(f"/api/leads/{acme['id']}/reply", json={"text": "Yes, interested. Please send it."})
    task = next(t for t in c.get("/api/tasks/board").json()["today"] + c.get("/api/tasks/board").json()["upcoming"]
                if t.get("kind") == "call" and t["lead_id"] == acme["id"])
    assert "Found by campaign Pune support hiring" in task["text"]
    other = create(c, name="Second", rules={"caller": "Asha"})
    run(c, other["id"])
    assert c.get("/api/notifications").json()[0]["to"] == "Asha"


def test_per_source_errors_are_human_readable_and_others_still_run(tmp_path):
    io = FakeIO(post={"api_search": PermissionError("HTTP 401")})
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "bad"})
    camp = create(c, sources={"apollo_search": True, "apollo_hiring": True, "google_places": True,
                              "careers_pages": ["https://careers.unreachable.example/jobs"], "careers_html": [ZETA_PAGE]})
    report = run(c, camp["id"])
    by_source = {e["source"]: e["message"] for e in report["errors"]}
    assert "rejected the API key" in by_source["Apollo people search"] and "Apollo hiring companies" not in by_source
    assert "not connected" in by_source["Google Places"] and "could not read it" in by_source["Careers page"]
    assert report["new"] == 1 and len(io.urls("mixed_companies")) == 0       # stopped after the rejected key


def test_campaign_api_validation_status_and_delete(tmp_path):
    c = client(tmp_path, FakeIO())
    bad = c.post("/api/campaigns", json={"name": "x", "target": {"company_size_buckets": ["huge"]}})
    assert bad.status_code == 422
    assert c.post("/api/campaigns", json={"name": "x", "sources": {"careers_pages": ["javascript:alert(1)"]}}).status_code == 422
    assert c.post("/api/campaigns", json={"name": "  "}).status_code == 422
    assert c.get("/api/campaigns/nope").status_code == 404 and c.post("/api/campaigns/nope/run", json={}).status_code == 404
    camp = create(c, sources={"careers_html": [ZETA_PAGE]})
    assert camp["status"] == "draft" and camp["rules"]["auto_enrol"] is False
    assert c.post(f"/api/campaigns/{camp['id']}/resume").json()["status"] == "running"
    assert c.post(f"/api/campaigns/{camp['id']}/pause").json()["status"] == "paused"
    edited = c.put(f"/api/campaigns/{camp['id']}", json={"name": "Renamed", "rules": {"min_score": 55}}).json()
    assert edited["name"] == "Renamed" and edited["rules"]["min_score"] == 55 and edited["status"] == "paused"
    run(c, camp["id"])
    listed = c.get("/api/campaigns").json()[0]
    assert listed["last_run"]["new"] == 1 and "runs" not in listed
    lock = campaigns._lock(camp["id"])
    lock.acquire()
    try:
        assert c.post(f"/api/campaigns/{camp['id']}/run", json={}).status_code == 409
    finally:
        lock.release()
    assert c.delete(f"/api/campaigns/{camp['id']}").json()["deleted"] is True
    assert c.get("/api/campaigns").json() == [] and by_company(c, "Zeta Foods")["campaign_id"] == camp["id"]   # leads kept
    sources = c.get("/api/campaign-sources").json()
    assert sources["crawler"]["connected"] and not sources["apollo"]["connected"] and not sources["google_places"]["connected"]


def test_partial_update_keeps_other_parts(tmp_path):
    c = client(tmp_path, FakeIO())
    camp = create(c, sources={"careers_pages": ["https://acme-retail.in/careers"]})
    done = c.put(f"/api/campaigns/{camp['id']}", json={"status": "done"}).json()
    assert done["status"] == "done" and done["sources"]["careers_pages"] == ["https://acme-retail.in/careers"]
    assert c.put(f"/api/campaigns/{camp['id']}", json={"status": "finished"}).status_code == 400
    assert c.put(f"/api/campaigns/{camp['id']}", json={"name": None}).json()["name"] == "Pune support hiring"


def test_apollo_match_reveals_the_business_email_by_person_id(tmp_path):
    searched = [{"id": "p-meera", "first_name": "Meera", "last_name_obfuscated": "Iy***r", "title": "HR Manager", "has_email": True,
                 "organization": {"name": "Gamma Care", "primary_domain": "gamma-care.in"}},
                {"id": "p-other", "first_name": "Dev", "title": "Founder", "organization": {"name": "Theta Labs", "primary_domain": "theta-labs.in"}}]
    matches = {"p-meera": {"person": {"name": "Meera Iyer", "email": "meera@gamma-care.in", "title": "HR Manager"}},
               "p-other": {"person": {"email": "dev@elsewhere.in"}}}                 # a different company: not kept
    io = FakeIO(post={"api_search": {"people": searched}, "people/match": lambda body: matches[body["id"]]})
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    camp = create(c, sources={"apollo_search": True}, enrich={"apollo_match": True})
    report = run(c, camp["id"])
    gamma, theta = by_company(c, "Gamma Care"), by_company(c, "Theta Labs")
    assert gamma["email"] == "meera@gamma-care.in" and gamma["email_source"] == "apollo:api.apollo.io"
    assert gamma["contact_name"] == "Meera"                         # the name already known is kept (only empty fields fill)
    assert theta.get("email") is None and theta["apollo_checked_at"]
    match_bodies = [call["body"] for call in io.calls if "people/match" in call["url"]]
    assert {b["id"] for b in match_bodies} == {"p-meera", "p-other"} and all(b["reveal_personal_emails"] is False for b in match_bodies)
    assert report["credits_used_estimate"] == 3 and report["enriched_emails"] == 1


def test_places_leads_keep_the_office_phone_as_business(tmp_path):
    places = {"places": [{"id": "pl1", "displayName": {"text": "Delta Call Services"}, "internationalPhoneNumber": "+91 98222 33344",
                          "websiteUri": "https://delta-calls.in", "googleMapsUri": "https://maps.google.com/?cid=1"}]}
    io = FakeIO(post={"places:searchText": places})
    c = client(tmp_path, io)
    connect(c, "google_places", {"api_key": "gk"})
    camp = create(c, sources={"google_places": True})
    assert run(c, camp["id"])["new"] == 1
    delta = by_company(c, "Delta Call Services")
    assert delta["phone"] == "+919822233344" and delta["phone_type"] == "business" and delta["domain"] == "delta-calls.in"
    assert delta["phone_source"] == "google_places:https://maps.google.com/?cid=1" and delta.get("consent") is None


def test_pasted_pages_keep_only_their_job_data(tmp_path):
    c = client(tmp_path, FakeIO())
    big = "<html><body>" + "x" * 50_000 + ZETA_JOBS + "</body></html>"
    camp = create(c, sources={"careers_html": [{"url": "https://zeta-foods.in/careers", "html": big},
                                               {"url": "https://plain.example/careers", "html": "<p>We are hiring!</p>"}]})
    stored = camp["sources"]["careers_html"]
    assert len(stored[0]["html"]) < 2_000 and "Zeta Foods" in stored[0]["html"] and stored[1]["html"] == ""
    report = run(c, camp["id"])
    assert report["new"] == 1 and any("plain.example" in e["message"] and "no job listings" in e["message"] for e in report["errors"])
