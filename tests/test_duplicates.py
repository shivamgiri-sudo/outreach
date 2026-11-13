"""Duplicate detection before import (read-only), the skip-duplicates choice, and the existing-leads report."""
import copy

import pytest

from app import duplicates
from tests.ownership_helpers import rig as make_rig

HEAD = "company,contact_name,title,email,phone,website,linkedin_url\n"


def csv(*rows): return HEAD + "\n".join(rows) + "\n"


@pytest.fixture
def r(tmp_path): return make_rig(tmp_path, mode_on=False)
@pytest.fixture
def own(tmp_path): return make_rig(tmp_path)


def prev(r, who, body):
    return r.c.post("/api/leads/import-preview", json={"csv": body, "source": "t"}, headers=r.h[who])


def snapshot(r): return copy.deepcopy({k: r.store.read(k) for k in ("leads", "lists", "audit", "settings")})


def kinds(res): return {x["row"]: x["kind"] for x in res.json()["rows"]}


def test_exact_kinds_and_phone_normalisation(r):
    body = csv("Acme X,New Person,HR,ravi@acme.com,,",                       # same email
               "Beta X,Other,HR,other@beta2.in,+91 91234-56789,",            # same phone, different format
               "Cora X,Third,CTO,third@cora2.io,,,https://www.linkedin.com/in/cora/",   # same linkedin slug
               "Zed Co,Zed Z,CEO,zed@fresh.in,70000 12345,")
    res = prev(r, "owner", body)
    assert res.status_code == 200, res.text
    j = res.json()
    got = {x["row"]: x for x in j["rows"]}
    assert len(got) == 3 and all(x["kind"] == "exists" for x in got.values())
    assert j["counts"]["exists"] == 3 and j["total"] == 4
    assert {x["matched_by"] for x in got.values()} == {"same email", "same phone number", "same LinkedIn profile"}
    assert all(x["match_lead_id"] and x["match_company"] for x in got.values())


def test_domain_plus_contact_name(r):
    res = prev(r, "owner", csv("Acme India,Ravi Kumar,HR,r2@acme.com,,https://acme.com"))
    x = res.json()["rows"][0]
    assert x["kind"] == "exists" and x["matched_by"] == "same company website and contact name"


def test_duplicates_inside_the_file(r):
    res = prev(r, "owner", csv("N1,Pat One,HR,pat@new1.in,,", "N1 again,Pat One,HR,pat@new1.in,,", "N2,Q,HR,q@new2.in,70000 22222,",
                               "N2b,Q2,HR,q2@new2.in,+9170000 22222,"))
    got = res.json()["rows"]
    assert [(x["row"], x["kind"], x["same_as_row"]) for x in got] == [(3, "in_file", 2), (5, "in_file", 4)]


def test_likely_same_company_and_name_different_email(r):
    res = prev(r, "owner", csv("Acme,Ravi Kumar,HR Head,ravi.k@other-mail.com,,"))
    x = res.json()["rows"][0]
    assert x["kind"] == "likely" and x["match_company"] == "Acme" and res.json()["counts"]["likely"] == 1


def test_preview_writes_nothing(r):
    before = snapshot(r)
    prev(r, "owner", csv("Acme X,New Person,HR,ravi@acme.com,,", "Brand,New,HR,new@brand.in,,"))
    assert snapshot(r) == before


def test_ownership_hides_details_of_leads_priya_cannot_see(own):
    body = csv("Acme X,Someone,HR,ravi@acme.com,98765 43210,", "Beta X,Else,HR,sita@beta.in,,", "Beta Y,Z,HR,zz@beta.in,91234 56789,")
    res = prev(own, "priya", body)
    assert res.status_code == 200
    rows = {x["row"]: x for x in res.json()["rows"]}
    assert rows[2] == {"row": 2, "kind": "exists"}                            # Ravi's lead: nothing but the fact
    for needle in ("acme.com", "Ravi", "98765", "Acme"):
        assert needle not in res.text
    assert rows[3]["match_company"] == "Beta Ltd" and rows[4]["owner"] == "Priya"      # her own lead: details shown
    assert prev(own, "adm", body).json()["rows"][0]["match_company"] == "Acme"        # the workspace admin sees Ravi's


def test_other_workspace_lead_is_only_exists(own):
    res = prev(own, "adm", csv("Q,Q Q,HR,zed@other.com,,"))                     # lives in workspace two; adm is Main-only
    assert res.status_code == 200
    assert "Other WS" not in res.text and "Priya" not in res.text


def test_roles_viewer_cannot_post(own):
    assert prev(own, "view", csv("A,B,C,a@b.in,,")).status_code == 403
    assert own.c.post("/api/leads/import-preview", json={"csv": ""}, headers=own.h["priya"]).status_code == 400


def test_first_200_flagged_rows_only(r):
    rows = [f"C{i},P{i},HR,dup@fresh.in,," for i in range(250)]
    j = prev(r, "owner", csv(*rows)).json()
    assert j["counts"]["in_file"] == 249 and j["counts"]["exists"] == 0        # the first row is new; the rest repeat it
    assert len(j["rows"]) == 200 and j["flagged"] == 249 and j["shown"] == 200


def test_skip_duplicates_choice_on_the_import_path(r):
    body = csv("Acme X,Ravi Kumar,HR,ravi@acme.com,,", "Newco,New N,HR,n@newco.in,,", "Newco2,New N,HR,n@newco.in,,")
    n0 = len(r.store.read("leads"))
    dry = r.c.post("/api/import/preview", json={"csv": body, "source": "t", "options": {"skip_duplicates": True}}, headers=r.h["owner"]).json()
    assert dry["duplicates_skipped"] == 2 and dry["summary"]["create"] == 1 and dry["summary"]["merge"] == 0
    assert len(r.store.read("leads")) == n0
    res = r.c.post("/api/import/commit", json={"csv": body, "source": "t", "options": {"skip_duplicates": True}}, headers=r.h["owner"]).json()
    assert len(r.store.read("leads")) == n0 + 1 and res["duplicates_skipped"] == 2
    default = r.c.post("/api/import/preview", json={"csv": body, "source": "t"}, headers=r.h["owner"]).json()
    assert default["duplicates_skipped"] == 0                                   # without the choice nothing changes


def test_existing_groups_report(r):
    r.c.post("/api/leads/import", json={"csv": csv("Acme Twin,Ravi Twin,HR,ravi@acme.com,,"), "source": "t"}, headers=r.h["owner"])
    leads = r.store.read("leads")
    by = lambda c: next(l for l in leads if l["company"] == c)
    twin = {**by("Beta Ltd"), "id": "twin-1", "company": "Beta Twin", "email": "beta-twin@x.in", "dedupe_key": "email:beta-twin@x.in", "phone": "91234 56789"}
    r.store.update("leads", lambda ls: [*ls, twin])
    j = r.c.get("/api/lead-duplicates", headers=r.h["owner"]).json()
    assert j["total_groups"] == 1 and j["groups"][0]["size"] == 2 and "same phone number" in j["groups"][0]["reasons"]
    assert {x["company"] for x in j["groups"][0]["leads"]} == {"Beta Ltd", "Beta Twin"}


def test_existing_groups_roles_and_scope(own):
    assert own.c.get("/api/lead-duplicates", headers=own.h["priya"]).status_code == 403
    assert own.c.get("/api/lead-duplicates", headers=own.h["view"]).status_code == 403
    assert own.c.get("/api/lead-duplicates", headers=own.h["adm"]).status_code == 200


def test_existing_groups_max_200():
    leads = [{"id": f"a{i}", "company": f"C{i}", "email": f"e{i}@x.in"} for i in range(250)] + [{"id": f"b{i}", "company": f"D{i}", "email": f"e{i}@x.in"} for i in range(250)]
    j = duplicates.existing_groups(leads, lambda l: "mcn")
    assert j["total_groups"] == 250 and len(j["groups"]) == 200
    other = duplicates.existing_groups([leads[0], {**leads[250], "id": "z"}], lambda l: "x" if l["id"] == "z" else "y")
    assert other["total_groups"] == 0                                           # never across accounts
