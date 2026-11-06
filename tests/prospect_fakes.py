"""Generated fake prospects for tests and the UI walkthrough (no real people or companies; .example domains only)."""
import random

from app import prospect_db as P
from app import prospect_fields as F
from app.prospect_repo import repo_for

INDUSTRIES = ["BPO", "Software", "Retail", "Manufacturing", "Logistics", "Healthcare", "Education", "Fintech", "Real Estate", "Insurance"]
CITIES = [("Pune", "Maharashtra"), ("Mumbai", "Maharashtra"), ("Bengaluru", "Karnataka"), ("Noida", "Uttar Pradesh"),
          ("Gurugram", "Haryana"), ("Chennai", "Tamil Nadu"), ("Hyderabad", "Telangana"), ("Kolkata", "West Bengal"),
          ("Jaipur", "Rajasthan"), ("Ahmedabad", "Gujarat")]
TECHS = ["React", "AWS", "Salesforce", "SAP", "HubSpot", "Zoho", "Python", "Kubernetes", "Shopify", "Freshworks"]
TITLES = ["Head of HR", "HR Manager", "Talent Acquisition Lead", "Recruiter", "Founder", "CEO", "VP Sales", "Sales Manager",
          "Marketing Director", "CTO", "Engineering Manager", "Finance Head", "Operations Manager", "IT Manager", "HR Executive",
          "Senior Recruiter", "Chief People Officer", "Director of Operations", "Business Development Executive", "Co-founder"]
FIRST = ["Aarav", "Diya", "Kabir", "Isha", "Rohan", "Meera", "Arjun", "Sara", "Vikram", "Anaya", "Kunal", "Neha", "Ravi", "Pooja", "Amit"]
LAST = ["Sharma", "Verma", "Iyer", "Reddy", "Patel", "Gupta", "Nair", "Singh", "Das", "Mehta", "Rao", "Kapoor", "Joshi", "Bose"]
STATUSES = ["verified", "verified", "valid", "catch_all", "unknown", "risky", "invalid"]
SOURCES = ["apollo", "hunter", "crm", "csv", "peopledatalabs", "site_discover"]
BUCKETS = [(5, "1-10"), (30, "11-50"), (120, "51-200"), (350, "201-500"), (800, "501-1000"), (3000, "1001-5000"), (12000, "10001+")]


def company_records(n: int, seed: int = 7) -> list:
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        city, state = CITIES[i % len(CITIES)]
        emp, _ = BUCKETS[rnd.randrange(len(BUCKETS))]
        out.append({"domain": f"firm{i}.example", "name": f"Firm {i} {INDUSTRIES[i % 10]} Pvt Ltd", "industry": INDUSTRIES[i % 10],
                    "employees": emp, "revenue_range": rnd.choice(["<1Cr", "1-10Cr", "10-100Cr", "100Cr+"]), "founded": 1980 + i % 45,
                    "hq_city": city, "hq_state": state, "hq_country": "India", "technologies": rnd.sample(TECHS, 3),
                    "keywords": [INDUSTRIES[i % 10].lower(), rnd.choice(["b2b", "saas", "export", "d2c"])],
                    "signals": [{"kind": rnd.choice(F.SIGNAL_KINDS), "title": f"Signal {i}", "at": f"2026-{1 + i % 10:02d}-15", "weight": 1 + i % 3}]
                    if i % 3 == 0 else []})
    return out


def person_records(n: int, companies: int, seed: int = 11) -> list:
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        c = i % companies
        first, last = FIRST[i % len(FIRST)], LAST[(i // len(FIRST)) % len(LAST)]
        city, state = CITIES[(c + i) % len(CITIES)]
        out.append({"company_domain": f"firm{c}.example", "full_name": f"{first} {last} {i}", "title": TITLES[i % len(TITLES)],
                    "email": f"{first.lower()}.{i}@firm{c}.example" if i % 5 else "", "email_status": STATUSES[i % len(STATUSES)],
                    "phones": [f"+9198{i:08d}"[:13]] if i % 4 == 0 else [], "city": city, "state": state, "country": "India",
                    "linkedin_url": f"https://www.linkedin.com/in/fake-{i}" if i % 2 else ""})
    return out


def bulk_seed(store, companies: int, people: int, at: str = "2026-11-01T00:00:00+00:00") -> None:
    """Fast load for volume tests: the same field cleaning, provenance docs and row building as upsert, written in bulk
    (no per-row duplicate lookups, which the generated data does not need)."""
    repo = repo_for(store)
    crow, keys, sigs, by_domain = [], [], [], {}
    for i, rec in enumerate(company_records(companies)):
        src = SOURCES[i % len(SOURCES)]
        clean = P.clean_company(rec)
        doc = {"fields": {}, "lists": {}, "sources": []}
        P._merge_company(doc, clean, src, at, F.SOURCE_CONFIDENCE.get(src, 50))
        cid = f"c{i:07d}"
        row = P.materialise_company(cid, doc)
        row["suppressed"] = 0
        row["signal_score"] = float(sum(s["weight"] for s in clean["signals"]))
        row["last_signal_at"] = clean["signals"][0]["at"] if clean["signals"] else None
        row["people_count"] = people // companies
        crow.append(row)
        by_domain[row["domain"]] = row
        keys.append({"k": f"d:{row['domain']}", "ref": cid})
        for s in clean["signals"]:
            sigs.append({"company_id": cid, "kind": s["kind"], "title": s["title"], "url": "", "at": s["at"], "weight": s["weight"], "source": src,
                         "dedupe": s["title"].lower()})
    prow = []
    for i, rec in enumerate(person_records(people, companies)):
        src = SOURCES[(i * 7) % len(SOURCES)]
        clean = P.clean_person(rec)
        doc = {"fields": {}, "lists": {}, "sources": [src], "first_seen": at, "last_seen": at}
        for k in P.PERSON_SCALARS:
            F.add_entry(doc["fields"], k, clean["email"] if k == "email" else clean.get(k), src, at, 70,
                        status=clean["email_status"] if k == "email" else None)
        F.add_items(doc["lists"], "phones", clean["phones"], src, at)
        if clean["email_status"] in ("verified", "valid"):
            doc["last_verified"] = at
        pid = f"p{i:08d}"
        row = P.materialise_person(pid, doc, by_domain.get(clean["company_domain"]))
        row["suppressed"] = 0
        prow.append(row)
        if clean["email"]:
            keys.append({"k": "e:" + clean["email"], "ref": pid})
        if clean["linkedin_url"]:
            keys.append({"k": "l:" + clean["linkedin_url"].lower(), "ref": pid})
    with repo.tx():
        repo.bulk_insert("pdb_companies", crow)
        repo.bulk_insert("pdb_people", prow)
        repo.bulk_insert("pdb_keys", keys)
        repo.bulk_insert("pdb_signals", sigs)
