"""QA: campaign discovery adapters against realistic provider replies (fake transports only).

Shapes follow the public docs (see docs/qa/campaigns-sources.md for the URLs): Apollo People API Search (no emails,
obfuscated last names, organisation name + has_* flags only), Organization Search (pagination block, primary_phone
object, accounts[]), organization job postings, Google Places Text Search (New), ZeroBounce and NeverBounce, and
careers-page JSON-LD copied in structure from public Lever, Workday and WordPress/Yoast pages. All data is fictional.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app import campaign_pipeline as pipe
from app import campaign_sources as src
from app.http_util import HttpStatusError
from app.jobs import job_domain, parse_job_postings
from tests.campaign_fakes import FakeIO

FIX = Path(__file__).parent / "fixtures" / "campaigns-sources"
NOW = datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)          # Tue 3 Nov 2026, 10:30 IST
TARGET = {"industries": ["BPO", "Hospital"], "cities": ["Pune", "Bengaluru", "Gurugram"], "roles_hiring": ["customer support"],
          "company_size_buckets": ["51-200", "201-500"], "keywords": [], "person_titles": []}
SOCIAL_OR_ATS = ("facebook.com", "linkedin.com", "x.com", "instagram.com", "lever.co", "myworkdayjobs.com", "business.site")


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def page(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def apollo_pages(body: dict) -> dict:
    return load("apollo_people_api_search_p1.json") if body["page"] == 1 else load("apollo_people_api_search_p2.json")


class Sequenced:
    """A transport that answers from `replies` in order; an Exception in the list is raised instead."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, url, headers, body=None):
        self.calls.append({"url": url, "headers": headers, "body": body})
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply(body) if callable(reply) else reply


# ---------- Apollo People API Search ----------
def test_people_api_search_documented_shape_gives_company_rows_without_guessing_names():
    io = FakeIO(post={"mixed_people/api_search": apollo_pages})
    out = src.apollo_people(TARGET, "ak-live-1234", io.post, 100)
    call = io.calls[0]
    assert len(io.calls) == 1 and out["requests"] == 1                       # 100 asked, 100 returned: no 2nd page
    assert call["headers"]["x-api-key"] == "ak-live-1234" and "ak-live-1234" not in json.dumps(call["body"]) + call["url"]
    assert call["body"]["per_page"] == 100 and call["body"]["organization_locations"] == ["Pune", "Bengaluru", "Gurugram"]
    assert call["body"]["organization_num_employees_ranges"] == ["51,200", "201,500"]
    assert "Founder" in call["body"]["person_titles"]                       # default decision-maker titles
    rows = out["rows"]
    assert len(rows) == 99                                                   # one person has no organisation name: dropped
    assert all(r["email"] == "" and r["domain"] == "" and r["website"] == "" for r in rows)   # api_search returns no contacts
    assert not any("*" in r["contact_name"] for r in rows)                   # obfuscated surnames never become names
    assert {r["contact_name"] for r in rows} <= set(json.dumps(load("apollo_people_api_search_p1.json")).split('"'))
    assert all(r["apollo_person_id"] and r["source"] == "apollo_search" for r in rows)
    assert next(r for r in rows if r["apollo_person_id"] == load("apollo_people_api_search_p1.json")["people"][58]["id"])["title"] == ""


def test_people_api_search_paging_stops_on_a_short_page_and_merges_colleagues():
    io = FakeIO(post={"mixed_people/api_search": apollo_pages})
    out = src.apollo_people(TARGET, "k", io.post, 150)
    assert out["requests"] == 2 and [c["body"]["page"] for c in io.calls] == [1, 2]
    assert len(out["rows"]) == 140                                           # 141 people, one without a company
    candidates = pipe.merge_rows(out["rows"])
    keys = {pipe.company_match_key(r["company"]) for r in out["rows"]}
    assert len(candidates) == len(keys)                                      # one candidate per company
    repeated = load("apollo_people_api_search_p1.json")["people"][10]
    twice = next(c for c in candidates if c["apollo_person_id"] == repeated["id"])
    assert twice["other_contacts"] == []                                     # the person Apollo repeated is not a colleague
    team = [c for c in candidates if len(c["other_contacts"]) >= 2]
    assert len(team) == 10 and all(len(c["sources"]) == 1 for c in team)     # companies 100-109 have 4 HR people each


@pytest.mark.parametrize("error, expected", [
    (PermissionError("HTTP 401"), src.KeyRejected),
    (PermissionError("HTTP 403"), src.KeyRejected),
    (HttpStatusError(429, "The maximum number of api calls allowed ... is 600 times per hour."), HttpStatusError),
    (HttpStatusError(422, '{"error": "Value too long for person_titles"}'), HttpStatusError),
])
def test_people_api_search_errors(error, expected):
    with pytest.raises(expected):
        src.apollo_people(TARGET, "k", FakeIO(post={"api_search": error}).post, 25)


# ---------- Apollo Organization Search + job postings ----------
def org_io(**overrides) -> FakeIO:
    postings = load("apollo_job_postings.json")

    def jobs(url):
        org_id = url.split("/organizations/")[1].split("/")[0]
        if org_id in overrides:
            raise overrides[org_id]
        return postings.get(org_id, {"organization_job_postings": []})
    return FakeIO(post={"mixed_companies/search": load("apollo_org_search.json")}, get={"/job_postings": jobs})


def test_hiring_companies_from_realistic_org_search_and_postings():
    io = org_io()
    out = src.apollo_hiring(TARGET, "k", io.post, io.get, 100, NOW)
    orgs = load("apollo_org_search.json")["organizations"]
    assert io.calls[0]["body"]["q_organization_job_titles"] == ["customer support"] and io.calls[0]["body"]["per_page"] == 100
    assert len(out["rows"]) == 30 and out["requests"] == 31 and out["failed_lookups"] == 0     # the nameless org is skipped
    rows = {r["apollo_org_id"]: r for r in out["rows"]}
    mobile, landline, sanitized, facebook = rows[orgs[0]["id"]], rows[orgs[1]["id"]], rows[orgs[2]["id"]], rows[orgs[5]["id"]]
    assert mobile["phone"] == "+919000020000" and mobile["phone_type"] == "business"
    assert landline["phone"] == "" and landline["company_phone"].startswith("+91 20 0712") and landline["phone_type"] == "business"
    assert sanitized["phone"].startswith("+919000")
    assert facebook["website"] == "" and facebook["domain"] == "" and facebook["company"]
    assert rows[orgs[8]["id"]]["domain"] == orgs[8]["primary_domain"]       # website_url null: primary_domain used
    assert rows[orgs[0]["id"]].get("open_roles") is None                    # no postings: no hiring signal invented
    busy = rows[orgs[3]["id"]]                                               # 15 postings, some without a date
    assert busy["open_roles"] == 15 and "Customer Support" in busy["role"] and busy["max_job_age_days"] > 60
    assert not any(any(s in r["domain"] for s in SOCIAL_OR_ATS) for r in out["rows"])


def test_hiring_companies_keep_rows_when_some_postings_are_rate_limited():
    orgs = load("apollo_org_search.json")["organizations"]
    io = org_io(**{orgs[3]["id"]: HttpStatusError(429, "rate limited"), orgs[9]["id"]: OSError("timed out")})
    out = src.apollo_hiring(TARGET, "k", io.post, io.get, 100, NOW)
    assert len(out["rows"]) == 30 and out["failed_lookups"] == 2
    assert {r["apollo_org_id"]: r for r in out["rows"]}[orgs[3]["id"]].get("open_roles") is None


def test_hiring_companies_are_kept_when_the_plan_does_not_include_job_postings():
    """Organization Search worked (1 credit spent); a 403 on job postings must not throw the companies away."""
    orgs = load("apollo_org_search.json")["organizations"]
    io = org_io(**{o["id"]: PermissionError("HTTP 403") for o in orgs})
    out = src.apollo_hiring(TARGET, "k", io.post, io.get, 100, NOW)
    assert len(out["rows"]) == 30 and out["failed_lookups"] == 30
    assert len(io.urls("/job_postings")) == 1                                # stops asking after the first refusal


# ---------- Google Places Text Search (New) ----------
def places_reply(body: dict) -> dict:
    return load("places_text_search.json").get(body["textQuery"], {"places": []})


def test_places_six_searches_map_to_business_rows():
    io = FakeIO(post={"places:searchText": places_reply})
    out = src.google_places(TARGET, "gk", io.post, 100)
    queries = [c["body"]["textQuery"] for c in io.calls]
    assert queries[:4] == ["BPO in Pune", "BPO in Pune", "BPO in Bengaluru", "BPO in Bengaluru"]   # 2nd page followed once
    assert io.calls[1]["body"]["pageToken"] and "pageToken" not in io.calls[0]["body"]
    assert out["requests"] == len(io.calls) and len(out["rows"]) == 100                  # stops at max_results
    assert io.calls[-1]["body"]["pageSize"] <= 20                             # last search asks only for what is missing
    rows = out["rows"]
    assert not any(any(s in r["domain"] for s in SOCIAL_OR_ATS) for r in rows)
    assert all(r["phone"] == "" or r["phone"].startswith("+919") for r in rows)
    assert all(r["phone_type"] == ("business" if r["company_phone"] else "") for r in rows)
    assert all(r["country"] == "IN" and r["city"] in ("Pune", "Bengaluru", "Gurugram") for r in rows)
    assert all(r["source_url"].startswith("https://maps.google.com/?cid=") for r in rows)
    closed = [p["displayName"]["text"] for q in load("places_text_search.json").values() for p in q["places"]
              if p["businessStatus"] == "CLOSED_PERMANENTLY"]
    assert closed and not set(closed) & {r["company"] for r in rows}


def test_places_quota_hit_midway_keeps_paid_results_and_counts_requests():
    replies = [places_reply, places_reply, places_reply,
               HttpStatusError(429, json.dumps(load("provider_errors.json")["places_429_quota"]))]
    post = Sequenced(*replies)
    out = src.google_places(TARGET, "gk", post, 100)
    assert len(post.calls) == 4 and out["requests"] == 3 and len(out["rows"]) > 30
    assert "429" in out["error"] or "quota" in out["error"].lower()


@pytest.mark.parametrize("error", [
    HttpStatusError(400, json.dumps(load("provider_errors.json")["places_400_key"])),
    PermissionError("HTTP 403"),
])
def test_places_key_problems_are_rejections(error):
    with pytest.raises(src.KeyRejected):
        src.google_places(TARGET, "bad", FakeIO(post={"places": error}).post, 20)


# ---------- careers pages (JSON-LD) ----------
def rows_for(pages: list) -> list:
    """careers_postings + careers_rows the way a campaign runs them (dry run: the job store is not written)."""
    from app.store import Store
    import tempfile
    out = src.careers_postings([], [{"url": u, "html": h} for u, h in pages], FakeIO().fetch)
    with tempfile.TemporaryDirectory() as tmp:
        return pipe.careers_rows(Store(Path(tmp) / "s.json"), out["postings"], NOW, dry_run=True), out["errors"]


def test_lever_hosted_pages_of_two_companies_stay_two_companies():
    rows, errors = rows_for([("https://jobs.lever.co/kappa-care-example/1f0c", page("careers_lever_style.html")),
                             ("https://jobs.lever.co/orion-diag-example/9a2e", page("careers_lever_style_other.html"))])
    assert errors == [] and sorted(r["company"] for r in rows) == ["Kappa Care Services", "Orion Diagnostics"]
    assert all(r["open_roles"] == 1 and r["domain"] != "jobs.lever.co" and "lever.co" not in r["website"] for r in rows)


def test_workday_page_keeps_the_entity_name_but_not_the_workday_host():
    rows, _ = rows_for([("https://sigma-fin.wd5.myworkdayjobs.com/en-US/External/job/India-Bengaluru/Telecaller_JR0042117",
                         page("careers_workday_style.html"))])
    assert rows[0]["company"] == "IN02 Sigma Fintech Services Bengaluru" and rows[0]["city"] == "India, Bengaluru"
    assert "myworkdayjobs.com" not in rows[0]["domain"] and rows[0]["max_job_age_days"] == 10


def test_wordpress_page_with_sameas_lists_is_read_without_crashing():
    postings = parse_job_postings(page("careers_wordpress_yoast.html"), "https://lotus-hospitality.example/careers/")
    assert [p["title"] for p in postings] == ["Front Office Executive", "Guest Relations Executive"]
    assert job_domain(postings[0], "https://lotus-hospitality.example/careers/") == "lotus-hospitality.example"
    rows, _ = rows_for([("https://lotus-hospitality.example/careers/", page("careers_wordpress_yoast.html"))])
    assert rows[0]["domain"] == "lotus-hospitality.example" and rows[0]["open_roles"] == 2 and rows[0]["city"] == "Pune"


def test_hand_written_json_ld_with_raw_line_breaks_is_still_read():
    postings = parse_job_postings(page("careers_handwritten_template.html"), "https://banyan-logistics.example/careers")
    assert [(p["title"], p["company"], p["location"]) for p in postings] == [("Warehouse Supervisor", "Banyan Logistics", "Pune")]


def test_linkedin_sameas_is_not_the_company_domain():
    rows, _ = rows_for([("", page("careers_linkedin_sameas.html"))])
    assert sorted((r["company"], r["open_roles"]) for r in rows) == [("Ganga Textiles", 1), ("Peacock Motors", 1)]
    assert all(r["domain"] == "" for r in rows)
    own, _ = rows_for([("https://peacock-motors.example/careers", page("careers_linkedin_sameas.html").split("</script>")[0] + "</script>")])
    assert own[0]["domain"] == "peacock-motors.example"                    # the company's own page host is used instead


def test_careers_page_without_jobposting_and_job_boards():
    io = FakeIO(pages={"https://deccan-pharma.example/careers/": page("careers_no_jobposting.html")})
    out = src.careers_postings(["https://deccan-pharma.example/careers/", "https://www.foundit.in/srp/results?query=deccan",
                                "https://in.linkedin.com/jobs/view/123"], [], io.fetch)
    assert io.urls()[0] == "https://deccan-pharma.example/careers/"
    assert not io.urls("foundit") and not io.urls("linkedin")               # job boards never fetched
    assert out["postings"] == [] and len(out["errors"]) == 3
    assert any("no job listings found" in e for e in out["errors"])


# ---------- email verifiers ----------
ZB = {"provider": "zerobounce", "api_key": "zk", "supported": True}
NB = {"provider": "neverbounce", "api_key": "nk", "supported": True}


def zb_reply(status: str, sub_status: str = "") -> dict:
    return {"address": "meera.iyer@gamma-care.example", "status": status, "sub_status": sub_status, "free_email": False,
            "did_you_mean": None, "account": "meera.iyer", "domain": "gamma-care.example", "domain_age_days": "4012",
            "smtp_provider": "google", "mx_found": "true", "mx_record": "aspmx.l.google.example", "firstname": "Meera",
            "lastname": "Iyer", "gender": "female", "country": None, "region": None, "city": None, "zipcode": None,
            "processed_at": "2026-11-03 05:01:22.817"}


@pytest.mark.parametrize("status, expected", [("valid", "valid"), ("invalid", "invalid"), ("catch-all", "risky"),
                                              ("unknown", "risky"), ("spamtrap", "invalid"), ("abuse", "invalid"),
                                              ("do_not_mail", "invalid")])
def test_zerobounce_statuses(status, expected):
    get = FakeIO(get={"zerobounce": zb_reply(status)}).get
    assert src.verify_email("meera.iyer@gamma-care.example", ZB, get) == expected


@pytest.mark.parametrize("result, expected", [("valid", "valid"), ("invalid", "invalid"), ("disposable", "invalid"),
                                              ("catchall", "risky"), ("unknown", "risky")])
def test_neverbounce_results(result, expected):
    reply = {"status": "success", "result": result, "flags": ["has_dns", "has_dns_mx"], "suggested_correction": "", "execution_time": 499}
    assert src.verify_email("a@gamma-care.example", NB, FakeIO(get={"neverbounce": reply}).get) == expected


def test_verifier_key_errors_stop_and_outages_stay_unverified():
    errors = load("provider_errors.json")
    with pytest.raises(src.KeyRejected):
        src.verify_email("a@x.example", ZB, FakeIO(get={"zerobounce": errors["zerobounce_key"]}).get)
    for key in ("neverbounce_auth", "neverbounce_throttle"):
        with pytest.raises(src.KeyRejected):
            src.verify_email("a@x.example", NB, FakeIO(get={"neverbounce": errors[key]}).get)
    # NeverBounce answers a partial outage with HTTP 200 + status temp_unavail and no result: not a verdict on the address
    with pytest.raises(OSError):
        src.verify_email("a@x.example", NB, FakeIO(get={"neverbounce": errors["neverbounce_temp"]}).get)
    with pytest.raises(OSError):
        src.verify_email("a@x.example", ZB, FakeIO(get={"zerobounce": {}}).get)
