"""QA: importing LinkedIn Sales Navigator / scraper-tool exports (Evaboot, PhantomBuster, Waalaxy, Dux-Soup, Kaspr,
lemlist, LinkedIn Recruiter). Fixtures are built by fixtures/import-linkedin-tools/make_fixtures.py from one pool of
fictional people (pool.json), so cross-tool dedupe can be checked against ground truth. No network is used."""
import json
import re
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.standardise import company_similarity
from app.unsubscribe import unsub_path

FIX = Path(__file__).parent / "fixtures" / "import-linkedin-tools"
POOL = json.loads((FIX / "pool.json").read_text(encoding="utf-8"))
PEOPLE = {p["slug"]: p for p in POOL["people"]}
ORDER = ["evaboot_salesnav_list.csv", "phantombuster_salesnav_search.csv", "phantombuster_profile_scraper.csv",
         "waalaxy_prospects_semicolon.csv", "dux_soup_visits.csv", "kaspr_contacts.csv", "lemlist_leads.csv",
         "linkedin_recruiter_project.csv"]


def text(name):
    return (FIX / name).read_text(encoding="utf-8")


@pytest.fixture()
def app(tmp_path):
    return create_app(data_file=tmp_path / "d.json")


@pytest.fixture()
def client(app):
    return TestClient(app)


def post(client, kind, name, **extra):
    r = client.post(f"/api/import/{kind}", json={"csv": text(name), **extra})
    assert r.status_code == 200, r.text
    return r.json()


def people_of(leads):
    """(slug, person dict, lead) for every main and other contact that has a LinkedIn link."""
    for lead in leads:
        for person in [lead, *(lead.get("other_contacts") or [])]:
            url = person.get("linkedin_url") or ""
            if url:
                yield url.rsplit("/", 1)[-1], person, lead


def import_all(client):
    return {name: post(client, "commit", name) for name in ORDER}


# ---------------------------------------------------------------- detection and mapping, per tool
EXPECT = {
    "evaboot_salesnav_list.csv": ("sales_navigator", {
        "LinkedIn Profile URL": "linkedin_url", "Sales Navigator Profile URL": "ignore", "Job Title": "title",
        "Company Name": "company", "Company Domain": "website", "Company LinkedIn URL": "company_linkedin_url",
        "Location": "location", "Email": "email", "Email Status": "ignore"}),
    "phantombuster_salesnav_search.csv": ("sales_navigator", {
        "profileUrl": "linkedin_url", "linkedInProfileUrl": "linkedin_url", "defaultProfileUrl": "linkedin_url",
        "regularCompanyUrl": "company_linkedin_url", "companyName": "company", "title": "title",
        "pastExperienceCompanyName": "ignore", "profileImageUrl": "ignore", "vmid": "ignore", "query": "ignore",
        "searchAccountProfileName": "ignore"}),
    "phantombuster_profile_scraper.csv": ("sales_navigator", {
        "linkedinProfileUrl": "linkedin_url", "company": "company", "companyUrl": "company_linkedin_url",
        "jobTitle": "title", "headline": "title", "company2": "ignore", "companyUrl2": "ignore", "jobTitle2": "ignore",
        "mail": "email", "phoneNumber": "phone", "schoolUrl": "ignore"}),
    "waalaxy_prospects_semicolon.csv": ("sales_navigator", {
        "First Name": "first_name", "Occupation": "title", "Company URL": "company_linkedin_url",
        "Dropcontact Email": "email", "Phone Numbers": "phone", "LinkedIn URL": "linkedin_url",
        "Sales Navigator URL": "ignore", "Email Replied": "ignore"}),
    "dux_soup_visits.csv": ("dux_soup", {
        "Profile": "linkedin_url", "SalesProfile": "ignore", "Middle Name": "ignore", "CompanyProfile":
        "company_linkedin_url", "CompanyWebsite": "website", "VisitTime": "ignore"}),
    "kaspr_contacts.csv": ("kaspr", {
        "LinkedIn": "linkedin_url", "Work email": "email", "Direct email": "email", "Phone 1": "phone",
        "Company website": "website"}),
    "lemlist_leads.csv": ("sales_navigator", {
        "linkedinUrl": "linkedin_url", "companyDomain": "website", "jobTitle": "title", "lastState": "ignore",
        "icebreaker": "ignore"}),
    "linkedin_recruiter_project.csv": ("sales_navigator", {
        "Profile URL": "linkedin_url", "Current Title": "title", "Current Company": "company", "Headline": "title",
        "Email Address": "email"}),
}


@pytest.mark.parametrize("name", ORDER)
def test_each_tool_export_is_detected_mapped_and_saves_nothing_on_preview(client, name):
    key, mapping = EXPECT[name]
    r = post(client, "preview", name)
    assert r["dry_run"] is True and r["format"]["key"] == key
    assert {col: r["mapping"][col] for col in mapping} == mapping
    assert r["summary"]["rows"] == POOL["files"][name] - (1 if name.startswith("waalaxy") else 0)  # blank row dropped
    assert client.get("/api/leads").json() == []


def test_hinglish_all_caps_and_lower_case_names_come_out_clean(client):
    r = post(client, "preview", "waalaxy_prospects_semicolon.csv")
    names = [row["contact_name"] for row in r["rows"]]
    assert names and all(n == n.title() or "." in n for n in names if n), names
    assert all(not row["title"] or " at " not in row["title"] and "|" not in row["title"] for row in r["rows"])


def test_location_city_state_india_is_split(client):
    r = post(client, "preview", "evaboot_salesnav_list.csv")
    for row in r["rows"]:
        assert row["country"] == "IN" and row["city"]
    by_city = {row["city"]: row["state"] for row in r["rows"]}
    assert by_city.get("Hyderabad", "Telangana") == "Telangana" and by_city.get("Pune", "Maharashtra") == "Maharashtra"


# ---------------------------------------------------------------- all tools, one database
def test_eight_tools_in_a_row_give_one_record_per_person_at_their_current_employer(client):
    import_all(client)
    leads = client.get("/api/leads").json()
    seen = {}
    for slug, person, lead in people_of(leads):
        assert slug not in seen, f"{slug} appears twice"
        seen[slug] = (person, lead)
    in_files = {m for name in ORDER for m in re.findall(r"linkedin\.com/in/([a-z\-]+-qa\d{3})", text(name))}
    assert in_files <= set(seen), sorted(in_files - set(seen))[:5]
    for slug, (person, lead) in seen.items():
        truth = PEOPLE[slug]
        assert person["contact_name"] == truth["name"], (slug, person["contact_name"])
        assert not lead["company"] or company_similarity(lead["company"], truth["company"]) >= 0.92 or (
            lead["company"].startswith(truth["company"])), (slug, lead["company"], truth["company"])
        assert person["linkedin_url"] == f"https://www.linkedin.com/in/{slug}"
    # 30 employers + 2 unsafe-link probe rows; a few employers split when tools disagree on the company's own name
    # ("Nilgiri Dairy" vs "Nilgiri Dairy Farm") and on its LinkedIn link form (/company/70018 vs /company/slug).
    assert 32 <= len(leads) <= 36
    assert all(l["source"] for l in leads) and not any(l["stage"] != "new" for l in leads)


def test_no_unsafe_link_survives_any_tool(client):
    import_all(client)
    dump = json.dumps(client.get("/api/leads").json()).lower()
    assert "javascript:" not in dump and "vbscript" not in dump and "evil.invalid" not in dump
    assert "/sales/lead/" not in dump and "/sales/people/" not in dump and "/in/acwaa" not in dump


def test_emails_and_phones_from_enrichment_tools_land_on_the_sales_navigator_person(client):
    post(client, "commit", "phantombuster_salesnav_search.csv")
    before = {slug: p.get("email") for slug, p, _ in people_of(client.get("/api/leads").json())}
    assert not any(before.values())                            # Sales Navigator itself carries no emails
    post(client, "commit", "kaspr_contacts.csv")
    after = {slug: p for slug, p, _ in people_of(client.get("/api/leads").json())}
    assert set(after) == set(before)                            # Kaspr added no new people, only details
    kaspr = re.findall(r"linkedin\.com/in/([a-z\-]+-qa\d{3}),([^,]*@[^,]*)?", text("kaspr_contacts.csv"))
    with_work_email = [(slug, PEOPLE[slug]["email"]) for slug, email in kaspr if email.endswith(".example")]
    assert with_work_email
    for slug, email in with_work_email:
        person = after[slug]
        assert person.get("email") == email or email in (person.get("emails") or []), slug


def test_reimporting_everything_a_week_later_creates_nothing(client, monkeypatch):
    import_all(client)
    count = len(client.get("/api/leads").json())
    from app import lead_import
    monkeypatch.setattr(lead_import, "now_iso", lambda: "2026-11-10T04:30:00+00:00")
    again = import_all(client)
    assert all(r["summary"]["create"] == 0 for r in again.values()), {n: r["summary"] for n, r in again.items()}
    assert len(client.get("/api/leads").json()) == count


def test_a_no_stays_a_no_across_tools(app, client):
    """An unsubscribed lead stays unsubscribed and a suppressed address is never stored, whichever tool brings the
    person back (Evaboot, Kaspr, lemlist, Recruiter all carry the same work email)."""
    blocked = PEOPLE[re.findall(r"linkedin\.com/in/([a-z\-]+-qa\d{3})", text("kaspr_contacts.csv"))[0]]["email"]
    app.state.store.update("suppressed", lambda rows: [*rows, blocked])
    first = post(client, "commit", "evaboot_salesnav_list.csv")
    target = next(l for l in client.get("/api/leads").json() if l.get("email"))
    client.post(unsub_path(app.state.store, target["id"]))
    results = {name: post(client, "commit", name) for name in ORDER[1:]}
    skipped = [s for r in [first, *results.values()] for s in r["skipped"]]
    # unsubscribing also suppresses the address, so later files skip that person's rows too
    assert skipped and all(blocked in s["reason"] or target["email"] in s["reason"] for s in skipped)
    assert any(target["email"] in s["reason"] for s in skipped)
    after = {l["id"]: l for l in client.get("/api/leads").json()}
    assert after[target["id"]]["stage"] == "unsubscribed" and after[target["id"]]["consent"] is None
    assert blocked not in json.dumps(list(after.values()))


def test_importing_every_tool_makes_no_network_call(client, monkeypatch):
    """Import only: the app never looks the profiles up on LinkedIn (or anywhere else) while importing."""
    def refuse(*args, **kwargs):
        raise AssertionError("network call during import")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    results = import_all(client)
    assert sum(r["summary"]["create"] for r in results.values()) >= 30
