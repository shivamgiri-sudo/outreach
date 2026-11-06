"""Seed a data file with fake prospects for the Prospect search walkthrough (web/e2e/prospects.mjs).

    .venv/bin/python -m tests.qa.seed_prospects <data file> [people=3000] [companies=300]

Uses the real upsert path (merge, provenance, FTS), plus a few CRM leads synced in. Fake .example domains only.
"""
import sys
from pathlib import Path

from app import prospect_db as P
from app import prospect_ingest
from app.lead_model import build_lead
from app.store import Store
from tests.prospect_fakes import company_records, person_records


def main(path: str, people: int = 3000, companies: int = 300) -> None:
    store = Store(Path(path))
    for i, rec in enumerate(company_records(companies)):
        P.upsert_company(store, rec, ("apollo", "hunter", "csv", "opencorporates")[i % 4])
    for i, rec in enumerate(person_records(people, companies)):
        P.upsert_person(store, rec, ("apollo", "hunter", "csv", "peopledatalabs", "site_discover")[i % 5])
    leads = [build_lead({"company": f"Firm {i} Lead Co", "contact_name": f"Crm Person {i}", "email": f"crm{i}@firm{i}.example",
                         "website": f"https://firm{i}.example", "title": "HR Manager"}, "sheet") for i in range(5)]
    store.add_leads([l for l in leads if l])
    print(prospect_ingest.sync_crm(store), P.stats(store)["people"])


if __name__ == "__main__":
    main(sys.argv[1], *(int(x) for x in sys.argv[2:4]))
