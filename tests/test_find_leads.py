"""One-click find & enrich: finding new decision makers, filling in leads you have, the background run, the credit limit.

Fake transports only: nothing here reaches a real provider or website. Names, domains and numbers are made up."""
import json
import threading
import time

from app import find_enrich, find_leads, routes_auth
from tests.campaign_fakes import PERSON, FakeIO, by_company, client, connect

NAME = find_leads.CAMPAIGN_NAME
CONTACT_PAGE = '<p>Write to <a href="mailto:careers@gamma-works.in">careers@gamma-works.in</a> or call 98765 11223</p>'
ORG = {"organization": {"id": "o-gamma", "name": "Gamma Works", "primary_domain": "gamma-works.in", "website_url": "https://gamma-works.in",
                        "estimated_num_employees": 80, "city": "Noida"}}
PEOPLE = {"people": [
    {"id": "p-nita", "first_name": "Nita", "last_name_obfuscated": "Ra**", "title": "HR Head", "email": "email_not_unlocked@domain.com",
     "linkedin_url": "https://www.linkedin.com/in/nita-example", "organization": {"id": "o-gamma", "name": "Gamma Works", "primary_domain": "gamma-works.in"}},
    {"id": "p-omi", "first_name": "Omi", "last_name": "Sen", "name": "Omi Sen", "title": "Founder", "email": "",
     "organization": {"id": "o-gamma", "name": "Gamma Works", "primary_domain": "gamma-works.in"}}]}
REVEALED = {"person": {"id": "p-nita", "name": "Nita Rao", "title": "HR Head", "email": "nita@gamma-works.in"}}


def people_at(body):
    """Apollo's people search answered for whichever domain was asked: one HR head there."""
    domain = body["q_organization_domains_list"][0]
    return {"people": [{"id": "p-" + domain, "first_name": "Nita", "last_name_obfuscated": "Ra**", "title": "HR Head",
                        "email": "email_not_unlocked@domain.com", "organization": {"name": domain, "primary_domain": domain}}]}


def wait(c, run_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        run = c.get(f"/api/find-leads/{run_id}").json()
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.02)
    raise AssertionError("the run did not finish")


def go(c, **body):
    r = c.post("/api/find-leads", json=body)
    assert r.status_code == 200, r.text
    return wait(c, r.json()["id"])


def add_leads(c, csv):
    assert c.post("/api/leads/import", json={"csv": csv, "source": "manual"}).status_code == 200


def set_stage(c, company, stage):
    c.app.state.store.update("leads", lambda rows: [{**l, "stage": stage} if l["company"] == company else l for l in rows])


def snapshot(c):
    s = c.app.state.store
    return json.dumps({k: s.read(k) for k in ("leads", "campaigns", "suppressed", "jobs", "tasks", "outbox")}, sort_keys=True)


# ---------- find new ----------
def test_find_new_dedupes_respects_suppression_and_closed_stages(tmp_path):
    io = FakeIO(post={"api_search": {"people": [
        PERSON,                                                                                  # already a lead (same email)
        {"id": "g1", "name": "Meera Iyer", "email": "meera@gone-co.in", "organization": {"name": "Gone Co", "primary_domain": "gone-co.in"}},
        {"id": "g2", "name": "Nisha Pal", "email": "nisha@blocked-co.in", "organization": {"name": "Blocked Co", "primary_domain": "blocked-co.in"}},
        {"id": "g3", "name": "Lata Sen", "email": "lata@fresh-co.in", "organization": {"name": "Fresh Co", "primary_domain": "fresh-co.in"}},
        {"id": "g4", "name": "Omar Shah", "email": "omar@fresh-two.in", "organization": {"name": "Fresh Two", "primary_domain": "fresh-two.in"}}]}})
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    add_leads(c, "company,contact_name,email,website\nAcme Retail,Asha Rao,asha@acme-retail.in,https://acme-retail.in\n"
                 "Gone Co,Old,old@gone-co.in,https://gone-co.in\n")
    set_stage(c, "Gone Co", "unsubscribed")
    c.app.state.store.update("suppressed", lambda rows: [*rows, "nisha@blocked-co.in"])
    gone = by_company(c, "Gone Co")
    run = go(c, mode="find_new", cities=["Noida"], titles=["HR Head"], max_new=10)
    assert run["status"] == "done" and run["found"] == 2 and run["report"]["new_leads"] == 2
    companies = [l["company"] for l in c.get("/api/leads").json()]
    assert "Fresh Co" in companies and "Blocked Co" not in companies and companies.count("Gone Co") == 1
    assert "Fresh Two" in companies and companies.count("Acme Retail") == 1 and "Acme Retail Pvt Ltd" not in companies   # same email: a duplicate
    assert by_company(c, "Gone Co") == gone                                      # closed lead untouched
    body = io.calls[0]["body"]
    assert body["person_titles"] == ["HR Head"] and body["organization_locations"] == ["Noida"]
    assert len(run["report"]["lead_ids"]) == 2 and run["report"]["campaign_id"]


def test_find_new_adds_at_most_max_new_leads(tmp_path):
    people = [{"id": f"m{i}", "name": f"Person {i}", "email": f"p{i}@firm{i}.in", "organization": {"name": f"Firm {i}", "primary_domain": f"firm{i}.in"}}
              for i in range(6)]
    io = FakeIO(post={"api_search": {"people": people}})
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    run = go(c, mode="find_new", max_new=3)
    assert run["found"] == 3 and len(c.get("/api/leads").json()) == 3


def test_find_new_reuses_one_saved_campaign_and_never_enrols(tmp_path):
    io = FakeIO(post={"api_search": {"people": [PERSON]}})
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    first = go(c, mode="find_new")
    second = go(c, mode="find_new", cities=["Delhi"])
    saved = [x for x in c.get("/api/campaigns").json() if x["name"] == NAME]
    assert len(saved) == 1 and saved[0]["target"]["cities"] == ["Delhi"] and saved[0]["status"] == "draft"
    assert first["report"]["campaign_id"] == second["report"]["campaign_id"] == saved[0]["id"]
    assert saved[0]["rules"]["auto_enrol"] is False and by_company(c, "Acme Retail Pvt Ltd").get("sequence") is None
    acme = by_company(c, "Acme Retail Pvt Ltd")
    assert (acme["id"] in first["report"]["qualified_ids"]) == acme["campaign_check"]["qualified"]


def test_find_new_without_any_finder_says_what_to_connect(tmp_path):
    c = client(tmp_path, FakeIO())
    run = go(c, mode="find_new")
    assert run["status"] == "done" and run["report"]["new_leads"] == 0
    assert any("Connect Apollo or Google Places" in e["message"] for e in run["errors"])
    assert c.get("/api/campaigns").json() == []                                  # no campaign made for nothing


# ---------- fill in leads you have ----------
def test_enrich_picks_early_leads_without_email_websites_first(tmp_path):
    c = client(tmp_path, FakeIO())
    add_leads(c, "company,email,website,phone\nHas Email,a@has-email.in,https://has-email.in,\nSite Co,,https://site-co.in,\n"
                 "Closed Co,,https://closed-co.in,\nWarm Co,,https://warm-co.in,\nPhone Only Co,,,9811100022\n")
    set_stage(c, "Closed Co", "disqualified")
    set_stage(c, "Warm Co", "interested")
    c.app.state.store.update("leads", lambda rows: [{**l, "company": "Name Only"} if l["company"] == "Phone Only Co" else l for l in rows])
    picks = find_enrich.pick_leads(c.app.state.store, 10)
    assert [l["company"] for l in picks["leads"]] == ["Site Co", "Name Only"]
    assert picks["with_website"] == 1 and picks["name_only"] == 1
    assert [l["company"] for l in find_enrich.pick_leads(c.app.state.store, 1)["leads"]] == ["Site Co"]


def test_crawler_fills_only_empty_fields_with_provenance(tmp_path):
    io = FakeIO(pages={"https://gamma-works.in/contact": CONTACT_PAGE})
    c = client(tmp_path, io)
    add_leads(c, "company,contact_name,phone,website,open_roles,company_size,max_job_age_days,country\nGamma Works,Ravi Kumar,9811100011,https://gamma-works.in,25,200,30,IN\n")
    run = go(c, mode="enrich_existing")
    lead = by_company(c, "Gamma Works")
    assert lead["email"] == "careers@gamma-works.in" and lead["contact_name"] == "Ravi Kumar" and lead["phone"] == "+919811100011"
    assert lead["email_source"] == "website:https://gamma-works.in/contact" and lead["provenance"]["email"] == lead["email_source"]
    assert "phone" not in lead["provenance"]                                     # the phone was already there: not touched
    assert lead["dedupe_key"] == "email:careers@gamma-works.in" and lead["find_checked_at"]
    assert "find & enrich" in lead["timeline"][-1]["text"] and "careers@gamma-works.in" in lead["timeline"][-1]["text"]
    assert run["enriched"] == 1 and run["report"]["emails_found"] == 1 and run["report"]["lead_ids"] == [lead["id"]]
    assert run["report"]["credits_used"] == 0 and run["report"]["qualified_ids"] == [lead["id"]]
    assert go(c, mode="enrich_existing")["report"]["checked"] == 0                # checked lately: not looked up again


def test_unreachable_site_is_reported_in_plain_english_and_not_retried_at_once(tmp_path):
    c = client(tmp_path, FakeIO())
    add_leads(c, "company,website\nGhost Works,https://ghost-works.in\n")
    run = go(c, mode="enrich_existing")
    assert run["report"]["checked"] == 1 and run["enriched"] == 0
    assert any("could not be read" in e["message"] for e in run["errors"])
    assert by_company(c, "Ghost Works")["find_checked_at"]


def test_name_only_lead_without_apollo_is_reported_and_stamped(tmp_path):
    c = client(tmp_path, FakeIO())
    add_leads(c, "company,phone\nName Only Co,9811100033\n")
    run = go(c, mode="enrich_existing")
    assert run["report"]["checked"] == 1 and run["enriched"] == 0 and by_company(c, "Name Only Co")["find_checked_at"]
    assert any("no website" in e["message"] for e in run["errors"])


def test_crawled_emails_that_are_suppressed_or_belong_to_another_lead_are_not_used(tmp_path):
    page = '<a href="mailto:hr@shared-co.in">x</a> <a href="mailto:jobs@shared-co.in">y</a> <a href="mailto:talent@shared-co.in">z</a>'
    io = FakeIO(pages={"https://shared-co.in/contact": page})
    c = client(tmp_path, io)
    add_leads(c, "company,email,website\nOther Lead,hr@shared-co.in,https://other.in\nShared Co,,https://shared-co.in\n")
    c.app.state.store.update("suppressed", lambda rows: [*rows, "jobs@shared-co.in"])
    go(c, mode="enrich_existing")
    assert by_company(c, "Shared Co")["email"] == "talent@shared-co.in"           # hr@ is another lead's, jobs@ is suppressed


def test_erased_person_hash_is_never_filled_back(tmp_path):
    from app.privacy import contact_hash
    io = FakeIO(pages={"https://erased-co.in/contact": '<a href="mailto:hr@erased-co.in">x</a>'})
    c = client(tmp_path, io)
    add_leads(c, "company,website\nErased Co,https://erased-co.in\n")
    c.app.state.store.update("suppressed_hashes", lambda rows: [*rows, contact_hash("hr@erased-co.in")])
    go(c, mode="enrich_existing")
    assert not by_company(c, "Erased Co").get("email")


def test_apollo_domain_search_attaches_decision_maker_and_reveals_email(tmp_path):
    io = FakeIO(get={"organizations/enrich": ORG}, post={"api_search": PEOPLE, "people/match": REVEALED})
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    add_leads(c, "company,website\nGamma Works,https://gamma-works.in\n")
    run = go(c, mode="enrich_existing", titles=["HR Head", "Founder"])
    lead = by_company(c, "Gamma Works")
    assert lead["contact_name"] == "Nita Rao" and lead["title"] == "HR Head" and lead["email"] == "nita@gamma-works.in"
    assert lead["linkedin_url"] == "https://www.linkedin.com/in/nita-example" and lead["apollo_person_id"] == "p-nita"
    assert [(p["contact_name"], p["title"]) for p in lead["other_contacts"]] == [("Omi Sen", "Founder")]
    assert lead["provenance"]["contact_name"] == "apollo:people search" and lead["email_source"] == "apollo:api.apollo.io"
    assert lead["apollo_org_id"] == "o-gamma" and lead["company_size"] == 80
    search = next(c_ for c_ in io.calls if "api_search" in c_["url"])
    assert search["body"]["q_organization_domains_list"] == ["gamma-works.in"] and search["body"]["person_titles"] == ["HR Head", "Founder"]
    assert search["headers"]["x-api-key"] == "ak" and "ak" not in search["url"]
    assert run["credits"] == 3 and run["report"]["credits_used"] == 3 and run["report"]["decision_makers"] == 1


def test_apollo_name_only_lead_gets_its_website_from_the_organisation_search(tmp_path):
    io = FakeIO(post={"mixed_companies/search": {"organizations": [
        {"id": "o-x", "name": "Different Name", "primary_domain": "different.in"},
        {"id": "o-gamma", "name": "Gamma Works Pvt Ltd", "primary_domain": "gamma-works.in"}]}, "api_search": {"people": []}},
        get={"organizations/enrich": ORG}, pages={"https://gamma-works.in/contact": CONTACT_PAGE})
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    add_leads(c, "company,phone\nGamma Works,9811100011\n")
    go(c, mode="enrich_existing")
    lead = by_company(c, "Gamma Works")
    assert lead["domain"] == "gamma-works.in" and lead["provenance"]["domain"] == "apollo:organization search"
    assert lead["email"] == "careers@gamma-works.in"


def test_hunter_runs_only_when_the_site_gave_no_email(tmp_path):
    io = FakeIO(get={"hunter.io": {"data": {"emails": [{"value": "hr@hunted.in", "confidence": 90}]}}},
                pages={"https://hunted.in/": "<p>No email here</p>", "https://has-page.in/contact": '<a href="mailto:hr@has-page.in">x</a>'})
    c = client(tmp_path, io)
    connect(c, "hunter", {"api_key": "hk"})
    add_leads(c, "company,website\nHunted,https://hunted.in\nHas Page,https://has-page.in\n")
    run = go(c, mode="enrich_existing")
    assert by_company(c, "Hunted")["email"] == "hr@hunted.in" and by_company(c, "Hunted")["email_source"].startswith("hunter:")
    assert by_company(c, "Has Page")["email"] == "hr@has-page.in" and len(io.urls("hunter.io")) == 1 and run["credits"] == 1


def test_a_rejected_apollo_key_is_reported_and_apollo_is_skipped_for_the_rest(tmp_path):
    io = FakeIO(get={"organizations/enrich": PermissionError("HTTP 401")})
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "bad"})
    add_leads(c, "company,website\nOne Co,https://one-co.in\nTwo Co,https://two-co.in\n")
    run = go(c, mode="enrich_existing")
    assert len(io.urls("organizations/enrich")) == 1
    assert any("rejected the API key" in e["message"] for e in run["errors"])


def test_credit_limit_stops_the_run_and_leaves_the_rest_unchecked(tmp_path):
    io = FakeIO(get={"organizations/enrich": ORG}, post={"api_search": people_at, "people/match": REVEALED})
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    add_leads(c, "company,website\nAlpha Co,https://alpha-co.in\nBeta Co,https://beta-co.in\nCharlie Co,https://charlie-co.in\n")
    run = go(c, mode="enrich_existing", max_credits=4)
    assert run["status"] == "done" and run["credits"] <= 4 and "credit limit" in run["report"]["stopped_reason"]
    assert run["credits"] == 4 and len(io.urls("people/match")) == 1          # lead 1: 3 credits; lead 2: org lookup only, then stop
    checked = [l for l in c.get("/api/leads").json() if l.get("find_checked_at")]
    unchecked = [l for l in c.get("/api/leads").json() if not l.get("find_checked_at")]
    assert len(checked) >= 1 and len(unchecked) >= 1 and run["report"]["checked"] == len(checked)
    assert len(io.urls("people/match")) <= 1
    free = go(c, mode="enrich_existing", max_credits=0)                          # a limit of 0 never calls a paid API
    assert free["credits"] == 0


def test_estimate_shows_connections_leads_and_the_most_credits(tmp_path):
    c = client(tmp_path, FakeIO())
    add_leads(c, "company,website\nAlpha Co,https://alpha-co.in\nBeta Co,https://beta-co.in\n")
    body = {"mode": "both", "max_credits": 40}
    cold = c.post("/api/find-leads/estimate", json=body).json()
    assert cold["connections"]["apollo"]["connected"] is False and cold["connections"]["crawler"]["connected"]
    assert cold["fill"]["will_check"] == 2 and cold["credits_estimate_max"] == 0
    assert cold["find"]["runnable"] is False and "Connect Apollo" in cold["find"]["problems"][0]
    connect(c, "apollo", {"api_key": "ak"})
    warm = c.post("/api/find-leads/estimate", json=body).json()
    assert warm["connections"]["apollo"]["connected"] and warm["fill"]["will_check"] == 2
    assert warm["credits_fill_max"] == 2 * 3 and warm["find"]["new_cap"] == 19 and warm["credits_find_max"] == 1 + 19
    assert c.post("/api/find-leads/estimate", json={**body, "max_new": 999}).json()["find"]["new_cap"] == 19
    assert c.post("/api/find-leads/estimate", json={"mode": "nope"}).status_code == 400


def test_limits_are_capped_and_defaults_applied(tmp_path):
    p = find_leads.normalise({"mode": "both", "max_new": 5000, "max_enrich": 5000, "max_credits": 99999, "titles": []})
    assert (p["max_new"], p["max_enrich"], p["max_credits"]) == (100, 100, 500)
    d = find_leads.normalise({})
    assert (d["mode"], d["max_new"], d["max_enrich"], d["max_credits"]) == ("both", 25, 30, 40) and "HR Head" in d["titles"]


# ---------- dry run ----------
def test_dry_run_writes_no_leads_or_campaign_and_makes_no_lookups_for_existing_leads(tmp_path):
    io = FakeIO(post={"api_search": {"people": [PERSON]}}, pages={"https://gamma-works.in/contact": CONTACT_PAGE},
                get={"organizations/enrich": ORG})
    c = client(tmp_path, io)
    connect(c, "apollo", {"api_key": "ak"})
    add_leads(c, "company,website\nGamma Works,https://gamma-works.in\n")
    before = snapshot(c)
    run = go(c, mode="both", dry_run=True)
    assert snapshot(c) == before
    assert run["status"] == "done" and run["report"]["dry_run"] and run["report"]["new_leads"] == 1
    kinds = {r["kind"]: r for r in run["preview"]}
    assert kinds["find"]["company"] == "Acme Retail Pvt Ltd" and kinds["find"]["would"] == "add"
    assert kinds["fill"]["company"] == "Gamma Works" and any("contact pages" in s for s in kinds["fill"]["steps"])
    assert not io.urls("gamma-works.in") and not io.urls("organizations/enrich")


# ---------- background run ----------
def test_progress_is_visible_while_running_and_a_second_run_gets_409(tmp_path):
    gate, started = threading.Event(), threading.Event()
    io = FakeIO()
    real = io.fetch

    def slow(url):
        started.set()
        gate.wait(5)
        return real(url)
    io.fetch = slow
    c = client(tmp_path, io)
    add_leads(c, "company,website\nAlpha Co,https://alpha-co.in\nBeta Co,https://beta-co.in\n")
    start = c.post("/api/find-leads", json={"mode": "enrich_existing"})
    assert start.status_code == 200
    run_id = start.json()["id"]
    assert started.wait(5)
    live = c.get(f"/api/find-leads/{run_id}").json()
    assert live["status"] == "running" and live["total"] == 2 and live["done"] == 0
    assert c.get("/api/find-leads").json()["active"]["id"] == run_id
    busy = c.post("/api/find-leads", json={"mode": "enrich_existing"})
    assert busy.status_code == 409 and "already working" in busy.json()["detail"]
    gate.set()
    done = wait(c, run_id)
    assert done["status"] == "done" and done["done"] == 2 and done["report"]["checked"] == 2
    assert c.get("/api/find-leads").json()["active"] is None
    assert c.post("/api/find-leads", json={"mode": "enrich_existing"}).status_code == 200      # the slot is free again
    assert c.get("/api/find-leads/missing").status_code == 404


def test_a_crash_inside_the_run_ends_as_failed_not_stuck(tmp_path, monkeypatch):
    c = client(tmp_path, FakeIO())
    add_leads(c, "company,website\nAlpha Co,https://alpha-co.in\n")
    monkeypatch.setattr(find_enrich, "enrich_lead", lambda lead, ctx: 1 / 0)
    run = go(c, mode="enrich_existing")
    assert run["status"] == "failed" and any("unexpected problem" in e["message"] for e in run["errors"])
    monkeypatch.undo()
    assert c.post("/api/find-leads", json={"mode": "enrich_existing"}).status_code == 200


def test_restart_marks_a_run_that_was_working_as_interrupted_and_a_rerun_continues(tmp_path):
    io = FakeIO(pages={"https://alpha-co.in/contact": '<a href="mailto:hr@alpha-co.in">x</a>'})
    c = client(tmp_path, io)
    add_leads(c, "company,website\nAlpha Co,https://alpha-co.in\nBeta Co,https://beta-co.in\n")
    store = c.app.state.store
    store.write("find_runs", [{"id": "oldrun", "status": "running", "phase": "Working", "mode": "both", "done": 1, "total": 2,
                               "errors": [], "found": 0, "enriched": 0, "credits": 0, "started_at": "2026-11-03T05:00:00+00:00"}])
    store.update("leads", lambda rows: [{**l, "find_checked_at": "2099-01-01T00:00:00+00:00"} if l["company"] == "Alpha Co" else l for l in rows])
    c2 = client(tmp_path, io)                                                    # a new process over the same data file
    old = c2.get("/api/find-leads/oldrun").json()
    assert old["status"] == "interrupted" and "restarted" in old["errors"][-1]["message"] and old["finished_at"]
    assert c2.get("/api/find-leads").json()["active"] is None
    again = go(c2, mode="enrich_existing")
    assert again["report"]["checked"] == 1 and by_company(c2, "Beta Co")["find_checked_at"]       # only the unchecked lead


def test_starting_a_real_run_needs_the_owner_role():
    assert routes_auth.needed_role("POST", "/api/find-leads") == "owner"
    assert routes_auth.needed_role("GET", "/api/find-leads/{run_id}") == "viewer"
