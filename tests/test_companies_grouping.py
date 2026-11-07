"""Companies page: every contact at one employer is counted, whatever the domain spelling (careers. / mail.
subdomains) or the company-name spelling (Pvt Ltd); job boards / LinkedIn as a website never merge strangers;
sort by name is case-insensitive and numbers sort by value."""
from app import companies
from tests.flow_helpers import make_client

CSV = ("company,contact_name,title,email,phone,website,open_roles,company_size,country\n"
       "Acme,Ravi Kumar,HR Head,ravi@acme.com,,https://acme.com,12,200,IN\n"
       "Acme,Meera Iyer,Recruiter,meera@mail.acme.com,,https://careers.acme.com/jobs,3,200,IN\n"
       "Acme Pvt. Ltd.,Old Name,Recruiter,,9876543210,,5,200,IN\n")


def test_second_and_third_contact_join_the_company(tmp_path):
    c = make_client(tmp_path, csv=CSV)
    items = c.get("/api/companies", params={"limit": 200}).json()["items"]
    assert [(r["key"], r["contacts"]) for r in items] == [("d:acme.com", 3)]
    detail = c.get("/api/companies/d:acme.com").json()
    assert {p["contact_name"] for p in detail["contacts_list"]} == {"Ravi Kumar", "Meera Iyer", "Old Name"}


def test_shared_sites_never_identify_a_company():
    leads = [{"id": "1", "company": "One", "website": "https://www.linkedin.com/company/one"},
             {"id": "2", "company": "Two", "website": "https://in.linkedin.com/company/two"},
             {"id": "3", "company": "Three", "website": "https://boards.greenhouse.io/three"},
             {"id": "4", "company": "Four", "website": "https://jobs.four.co.in/open"}]
    assert companies.group_keys(leads) == {"1": "n:one", "2": "n:two", "3": "n:three", "4": "d:four.co.in"}


def test_sort_by_name_is_case_insensitive_and_numeric(tmp_path):
    csv = "company,contact_name,email\n" + "".join(f"{n},P{i},p{i}@x{i}.in\n" for i, n in
                                                  enumerate(["store 10", "Store 2", "beta", "Alpha", "zeta"]))
    c = make_client(tmp_path, csv=csv)
    asc = c.get("/api/companies", params={"sort": "name", "order": "asc"}).json()["items"]
    assert [r["name"] for r in asc] == ["Alpha", "beta", "Store 2", "store 10", "zeta"]


def test_a_person_merged_into_another_lead_is_still_shown(tmp_path):
    from app.companies import more_people
    members = [{"id": "a", "contact_name": "Ravi Kumar", "email": "ravi@acme.com", "company": "Acme",
                "other_contacts": [{"contact_name": "Priya Shah", "email": "priya@acme.com", "title": "HR Head"},
                                   {"contact_name": "Ravi Kumar", "email": "ravi@acme.com"}]},
               {"id": "b", "contact_name": "Meera Iyer", "email": "meera@acme.com",
                "other_contacts": [{"contact_name": "Priya Shah", "email": "PRIYA@acme.com"}]}]
    extra = more_people(members)
    assert [(c["contact_name"], c["kept_on"]) for c in extra] == [("Priya Shah", "a")]
