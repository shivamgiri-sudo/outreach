"""Reverse email lookup (app/reverse_lookup.py, app/routes_reverse_lookup.py): our data first, the company's own site,
Gravatar, confidence, the free-mail flag, the 2,000 cap, CSV, the import CSV, roles and workspace isolation.
No network: the website fetcher, Gravatar and DNS are fakes. Names and addresses are made up."""
import pytest
from fastapi.testclient import TestClient

from app import prospect_db as P, reverse_lookup as rl, routes_reverse_lookup
from app.main import create_app
from app.store import Store
from tests.test_workspace_isolation import admin_setup

TEAM = ('<html><body><ul><li class="member"><h3>Meera Shah</h3><p>Head of Talent</p>'
        '<a href="mailto:meera.shah@cardco.example">Email</a></li></ul></body></html>')
HOME = ('<html><head><script type="application/ld+json">{"@type": "Organization", "name": "CardCo Pvt Ltd", '
        '"logo": "https://cardco.example/logo.png"}</script></head><body><a href="/team">Team</a></body></html>')
PAGES = {"https://cardco.example/": HOME, "https://cardco.example/team": TEAM}
GRAVATAR = {"entry": [{"profileUrl": "https://gravatar.com/kiran", "thumbnailUrl": "https://0.gravatar.com/avatar/k",
                       "name": {"formatted": "Kiran Patel"},
                       "accounts": [{"shortname": "linkedin", "url": "https://www.linkedin.com/in/kiran-example"}]}]}


class Site:
    def __init__(self):
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if url in PAGES:
            return PAGES[url]
        raise OSError("not found")


class Grav:
    def __init__(self, who=("kiran@gmail.com",)):
        from app.gravatar import NoProfile, email_hash
        self.hashes, self.calls, self.NoProfile = {email_hash(a) for a in who}, [], NoProfile

    def __call__(self, url):
        self.calls.append(url)
        if any(h in url for h in self.hashes):
            return GRAVATAR
        raise self.NoProfile()


def dns(_domain):
    return "ok"


def known(leads=(), prospects=None):
    from app import bulk_email_check as bec
    return {"check": bec._empty_known(), "ours": rl.our_index(list(leads)), "prospects": prospects or {}}


LEAD = {"id": "L1", "company": "Acme", "domain": "acme.example", "contact_name": "Ravi Kumar", "title": "HR Head",
        "email": "ravi@acme.example", "linkedin_url": "https://www.linkedin.com/in/ravi-example",
        "other_contacts": [{"contact_name": "Asha Rao", "title": "Recruiter", "email": "asha@acme.example"},
                           {"contact_name": "Neha Jain", "title": "Founder", "email": "neha.jain@gmail.com"}]}


def run(tmp_path, emails, k=None, site=None, grav=None):
    return rl.lookup_list(Store(tmp_path / "s.json"), emails, k or known(), site or Site(), dns, grav or Grav())


def by(rows):
    return {r["email"]: r for r in rows}


def test_our_data_first_and_skips_public_lookups(tmp_path):
    site, grav = Site(), Grav()
    rows = by(run(tmp_path, ["ravi@acme.example", "asha@acme.example"], known([LEAD]), site, grav))
    ravi, asha = rows["ravi@acme.example"], rows["asha@acme.example"]
    assert (ravi["name"], ravi["title"], ravi["company"], ravi["confidence"]) == ("Ravi Kumar", "HR Head", "Acme", "high")
    assert ravi["linkedin"].endswith("ravi-example") and ravi["lead_id"] == "L1" and ravi["sources"][0]["step"] == "lead"
    assert (asha["name"], asha["company"], asha["sources"][0]["step"], asha["confidence"]) == ("Asha Rao", "Acme", "lead_contact", "high")
    assert site.urls == [] and grav.calls == []                       # our own data named both: nothing public was read


def test_prospect_database_match_is_high(tmp_path):
    rows = run(tmp_path, ["bala@beta.example"], known(prospects={"bala@beta.example": {
        "name": "Bala Iyer", "title": "Recruiter", "company": "Beta", "domain": "beta.example", "linkedin": "", "photo": "",
        "lead_id": None, "step": "prospect_db"}}))
    assert rows[0]["name"] == "Bala Iyer" and rows[0]["confidence"] == "high" and rows[0]["sources"][0]["step"] == "prospect_db"


def test_prospect_matches_reads_the_database_and_skips_suppressed(tmp_path):
    store = Store(tmp_path / "p.json")
    P.upsert_company(store, {"domain": "beta.example", "name": "Beta Pvt"}, "apollo")
    P.upsert_person(store, {"company_domain": "beta.example", "full_name": "Bala Iyer", "title": "Recruiter", "email": "bala@beta.example"}, "apollo")
    P.upsert_person(store, {"company_domain": "beta.example", "full_name": "Gita Sen", "title": "CEO", "email": "gita@beta.example"}, "apollo")
    P.suppress(store, "gita@beta.example")
    got = rl.prospect_matches(store, ["BALA@beta.example", "gita@beta.example", "nobody@beta.example"], None)
    assert list(got) == ["bala@beta.example"] and got["bala@beta.example"]["company"] == "Beta Pvt" and got["bala@beta.example"]["name"] == "Bala Iyer"


def test_website_card_gives_name_title_company_logo(tmp_path):
    rows = by(run(tmp_path, ["meera.shah@cardco.example", "someone@cardco.example"]))
    meera, other = rows["meera.shah@cardco.example"], rows["someone@cardco.example"]
    assert (meera["name"], meera["confidence"], meera["company"]) == ("Meera Shah", "high", "CardCo Pvt Ltd")
    assert meera["logo"] == "https://cardco.example/logo.png" and meera["domain"] == "cardco.example"
    assert [s["step"] for s in meera["sources"]][:2] == ["website", "site_markup"]
    assert (other["name"], other["company"], other["confidence"]) == ("", "CardCo Pvt Ltd", "low")     # company only


def test_site_read_once_per_domain_and_capped(tmp_path, monkeypatch):
    site = Site()
    run(tmp_path, ["meera.shah@cardco.example", "a@cardco.example", "b@cardco.example"], site=site)
    assert site.urls.count("https://cardco.example/") == 1
    monkeypatch.setattr(rl, "MAX_SITE_DOMAINS", 1)
    site = Site()
    run(tmp_path, ["x@one.example", "y@two.example"], site=site)
    assert not any("two.example" in u for u in site.urls)


def test_gravatar_for_free_mail_is_medium_and_never_reads_gmail_site(tmp_path):
    site = Site()
    row = run(tmp_path, ["kiran@gmail.com"], site=site)[0]
    assert (row["name"], row["confidence"], row["company"]) == ("Kiran Patel", "medium", "")
    assert row["linkedin"].endswith("kiran-example") and row["photo"] and "free" in row["flags"]
    assert site.urls == []


def test_free_mail_on_a_company_row_is_flagged(tmp_path):
    row = run(tmp_path, ["neha.jain@gmail.com"], known([LEAD]))[0]
    assert row["company"] == "Acme" and "free_mail_company" in row["flags"] and row["confidence"] == "high"


def test_nothing_found_and_lead_domain_company(tmp_path):
    rows = by(run(tmp_path, ["zed@unknown.example", "new.person@acme.example", "not-an-email"], known([LEAD])))
    assert rows["zed@unknown.example"]["confidence"] == "" and rows["zed@unknown.example"]["name"] == ""
    assert rows["new.person@acme.example"]["company"] == "Acme" and rows["new.person@acme.example"]["confidence"] == "low"
    assert rows["not-an-email"]["status"] == "invalid_syntax"


def test_cap_csv_and_import_csv(tmp_path):
    assert len(rl.read_addresses("\n".join(f"p{i}@a.example" for i in range(rl.MAX_ADDRESSES)))) == rl.MAX_ADDRESSES
    with pytest.raises(rl.TooMany):
        rl.read_addresses("\n".join(f"p{i}@a.example" for i in range(rl.MAX_ADDRESSES + 1)))
    rows = run(tmp_path, ["meera.shah@cardco.example", "kiran@gmail.com", "zed@unknown.example", "=x@evil.example"])
    text = rl.to_csv(rows)
    assert text.splitlines()[0] == ",".join(rl.CSV_COLUMNS) and "'=x@evil.example" in text and "website: name" in text
    imp = rl.import_csv(rows).splitlines()
    assert imp[0] == ",".join(rl.IMPORT_COLUMNS) and len(imp) == 3                 # meera + kiran; zed names nobody
    assert "meera.shah@cardco.example,Meera Shah,,CardCo Pvt Ltd,cardco.example," in imp[1]
    assert imp[2].startswith("kiran@gmail.com,Kiran Patel,,,,")                     # no website for webmail


@pytest.fixture
def sync(monkeypatch):
    monkeypatch.setattr(routes_reverse_lookup, "BACKGROUND", False)
    monkeypatch.setattr(routes_reverse_lookup, "DNS", dns)
    monkeypatch.setattr(routes_reverse_lookup, "GRAVATAR", Grav())


def test_api_run_export_and_import_fills_empty_fields(tmp_path, sync):
    c = TestClient(create_app(data_file=tmp_path / "d.json", fetcher=Site(), admin_password="admin-pass-123"), client=("203.0.113.9", 50000))
    ho = {"Authorization": "Bearer " + c.post("/api/login", json={"password": "admin-pass-123"}).json()["token"]}
    c.post("/api/leads/import", json={"csv": "company,email\nCardCo,meera.shah@cardco.example\n", "source": "manual"}, headers=ho)
    r = c.post("/api/email-checker/reverse", json={"text": "meera.shah@cardco.example\nkiran@gmail.com"}, headers=ho)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "done" and out["summary"]["total"] == 2 and out["importable"] == 2
    meera = out["rows"][0]
    assert meera["lead_id"] and meera["name"] == "Meera Shah" and meera["confidence"] == "high"    # name from the site
    assert "already_lead" in meera["flags"]
    assert "reverse-lookup" in c.get(f"/api/email-checker/reverse/{out['id']}/export.csv", headers=ho).headers["content-disposition"]
    imp = c.get(f"/api/email-checker/reverse/{out['id']}/import", headers=ho).json()
    done = c.post("/api/import/commit", json={"csv": imp["csv"], "source": "Reverse lookup"}, headers=ho)
    assert done.status_code == 200, done.text
    leads = {l["email"]: l for l in c.get("/api/leads", headers=ho).json()}
    assert leads["meera.shah@cardco.example"]["contact_name"] == "Meera Shah"         # empty field filled
    assert leads["meera.shah@cardco.example"]["company"] == "CardCo"                  # never overwritten
    assert leads["kiran@gmail.com"]["contact_name"] == "Kiran Patel"                  # a new lead
    big = "\n".join(f"p{i}@acme.example" for i in range(rl.MAX_ADDRESSES + 1))
    assert c.post("/api/email-checker/reverse", json={"text": big}, headers=ho).status_code == 413
    assert c.post("/api/email-checker/reverse", json={"text": " "}, headers=ho).status_code == 400
    assert c.get("/api/email-checker/reverse/nope", headers=ho).status_code == 404


def test_roles_and_workspace_isolation(tmp_path, sync):
    c, ho, ha, w, w2 = admin_setup(tmp_path)            # main has ravi@acme.com (Ravi Kumar); the admin only sees Client One
    P.upsert_person(c.app.state.store, {"company_domain": "beta.example", "full_name": "Bala Iyer", "email": "bala@beta.example"}, "apollo")
    owner = c.post("/api/email-checker/reverse", json={"text": "ravi@acme.com\nbala@beta.example"}, headers=ho).json()
    assert [r["name"] for r in owner["rows"]] == ["Ravi Kumar", "Bala Iyer"]
    admin = c.post("/api/email-checker/reverse", json={"text": "ravi@acme.com\nbala@beta.example"}, headers=ha)
    assert admin.status_code == 200
    assert [r["name"] for r in admin.json()["rows"]] == ["", ""]           # another workspace's lead and the prospect DB stay hidden
    assert all(r["lead_id"] is None for r in admin.json()["rows"])
    assert c.get(f"/api/email-checker/reverse/{owner['id']}", headers=ha).status_code == 404       # only the login that ran it
    assert c.get(f"/api/email-checker/reverse/{admin.json()['id']}/export.csv", headers=ha).status_code == 200
    hc = {"Authorization": "Bearer " + c.post("/api/login", json={"email": "raj@example.com", "password": "long-password-1"}).json()["token"]}
    assert c.post("/api/email-checker/reverse", json={"text": "a@b.example"}, headers=hc).status_code == 403
    assert c.post("/api/email-checker/reverse", json={"text": "a@b.example"}).status_code == 401
