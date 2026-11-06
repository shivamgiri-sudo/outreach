"""Feeding the prospect database from what the app already has.

  sync_crm(store)          every CRM lead becomes/updates a company + person. Each field is credited to the source in the
                           lead's own provenance (Hunter, Apollo, the website crawler...), fields a person edited by hand to
                           "manual", the rest to "crm". Incremental: a fingerprint per lead skips unchanged leads.
  ingest_rows(store, rows) raw rows from campaign / find-leads discovery (each row names its source)
  ingest_people(store, people, domain, company)  people found by finders (waterfall)
  import_csv(store, text)  a CSV straight into the prospect DB, using the smart lead import's column mapping
Every ingest is best effort: a failure is logged and never breaks the caller's run.
"""
import hashlib
import json
import logging
import threading
import time
from datetime import date

from . import prospect_db as P
from . import prospect_fields as F
from .prospect_repo import repo_for

log = logging.getLogger(__name__)
STATUS_MAP = {"valid": "valid", "verified": "verified", "deliverable": "valid", "risky": "risky", "invalid": "invalid",
              "undeliverable": "invalid", "catch_all": "catch_all", "catch-all": "catch_all", "catchall": "catch_all",
              "unverified": "unknown", "unknown": "unknown", "plausible": "unknown"}
COMPANY_FROM_LEAD = {"company": "name", "website": "website", "domain": "domain", "city": "hq_city", "state": "hq_state",
                     "country": "hq_country", "sector": "industry", "company_size": "employees", "company_linkedin_url": "linkedin_url"}
PERSON_FROM_LEAD = {"contact_name": "full_name", "title": "title", "email": "email", "phone": "phones", "linkedin_url": "linkedin_url",
                    "city": "city", "state": "state", "country": "country"}
_sync_lock = threading.Lock()
_last_auto = {}


def _status(raw) -> str:
    return STATUS_MAP.get(str(raw or "").strip().lower().replace(" ", "_"), "unknown")


def _lead_seen(lead: dict) -> str:
    stamps = [e.get("at") for e in lead.get("timeline") or [] if e.get("at")]
    return F.at_or_now(max(stamps) if stamps else lead.get("created_at"))


def lead_records(lead: dict, manual: set) -> list:
    """[(source, company_record, person_record or None)] for one lead, one tuple per source that gave its fields."""
    prov = lead.get("provenance") or {}
    by_source = {}
    for field in set(COMPANY_FROM_LEAD) | set(PERSON_FROM_LEAD):
        if lead.get(field) in (None, "", 0):
            continue
        raw = str(prov.get(field) or "")
        if raw.startswith("prospect_db:"):                   # a lead made from a prospect keeps the prospect's own source
            raw = raw.split(":", 1)[1]
        src = "manual" if field in manual else (F.source_key(raw) if raw else "crm")
        by_source.setdefault(src, set()).add(field)
    domain = F.clean_domain(lead.get("domain") or lead.get("website") or "")
    ident_c = {"domain": domain, "name": lead.get("company") or ""}
    email = F.normalise_email(lead.get("email"))
    role = bool(email and F.is_role(email))
    person_ident = {"company_domain": domain, "company_name": lead.get("company") or "", "full_name": lead.get("contact_name") or "",
                    "email": "" if role else (email or ""), "linkedin_url": lead.get("linkedin_url") or ""}
    out = []
    order = sorted(by_source, key=lambda s: (s != "crm", s == "manual", s))
    for src in order or ["crm"]:
        fields = by_source.get(src, set())
        company = dict(ident_c)
        for lf, cf in COMPANY_FROM_LEAD.items():
            if lf in fields:
                company[cf] = lead[lf]
        company["_match_only"] = [k for k, lf in (("domain", "domain"), ("name", "company")) if lf not in fields and not (k == "domain" and "website" in fields)]
        if src == (order[0] if order else "crm"):
            if lead.get("company_phone"):
                company["phones"] = [lead["company_phone"]]
            if role:
                company["emails"] = [email]
            if (lead.get("open_roles") or 0) > 0:
                company["signals"] = [{"kind": "hiring", "title": f"{lead['open_roles']} open roles" + (f" ({lead['role']})" if lead.get("role") else ""),
                                       "at": lead.get("created_at"), "weight": min(3.0, 1 + lead["open_roles"] / 10)}]
        person = None
        if any(person_ident[k] for k in ("full_name", "email", "linkedin_url")):
            person = dict(person_ident)
            for lf, pf in PERSON_FROM_LEAD.items():
                if lf in fields:
                    person[pf] = [lead[lf]] if pf == "phones" else lead[lf]
            person["_match_only"] = [pf for lf, pf in (("contact_name", "full_name"), ("email", "email"), ("linkedin_url", "linkedin_url"))
                                     if lf not in fields] + ["company_domain"] * ("domain" not in fields and "website" not in fields)
            if "email" in fields and not role and email:
                check = lead.get("email_check") or {}
                status = _status(check.get("status")) if check.get("email") in (None, email) else "unknown"
                person["email_status"] = "verified" if status == "valid" else status     # "valid" on a lead = a provider confirmed the mailbox
        out.append((src, company, person))
    for other in lead.get("other_contacts") or []:
        if other.get("email") or other.get("contact_name") or other.get("linkedin_url"):
            out.append((F.source_key(other.get("found_at") or "crm"), None,
                        {"company_domain": domain, "company_name": lead.get("company") or "", "full_name": other.get("contact_name") or "",
                         "title": other.get("title") or "", "email": other.get("email") or "", "linkedin_url": other.get("linkedin_url") or ""}))
    return out


def _fingerprint(lead: dict, manual: set) -> str:
    keys = (*COMPANY_FROM_LEAD, *PERSON_FROM_LEAD, "provenance", "other_contacts", "email_check", "open_roles", "role", "company_phone", "workspace")
    body = json.dumps([{k: lead.get(k) for k in keys}, sorted(manual)], sort_keys=True, default=str)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()


def _manual_fields(store) -> dict:
    out = {}
    for row in store.read("audit"):
        if not row.get("action") and row.get("lead_id") and row.get("field"):
            out.setdefault(row["lead_id"], set()).add(row["field"])
    return out


def sync_lead(store, lead: dict, manual: set, dis) -> tuple:
    seen = _lead_seen(lead)
    company_id = person_id = None
    for src, company, person in lead_records(lead, manual):
        if company and (company.get("domain") or company.get("name")):
            res = P.upsert_company(store, company, src, seen, dis)
            company_id = res.get("id") or company_id
        if person:
            res = P.upsert_person(store, person, src, seen, dis)
            if res.get("id") and person.get("full_name") == (lead.get("contact_name") or "") and person_id is None:
                person_id = res["id"]
    return person_id, company_id


def sync_crm(store, force: bool = False) -> dict:
    """Bring every CRM lead into the prospect DB (only changed leads unless force). Safe to call from any thread."""
    if not _sync_lock.acquire(blocking=False):
        return {"busy": True}
    try:
        started = time.perf_counter()
        repo, dis = repo_for(store), P.disabled(store)
        manual = _manual_fields(store)
        known = {r["lead_id"]: r["fp"] for r in repo.q("SELECT lead_id, fp FROM pdb_crm_links")}
        leads = store.read("leads")
        done = skipped = 0
        with repo.tx():
            for lead in leads:
                mine = manual.get(lead["id"], set())
                fp = _fingerprint(lead, mine)
                if not force and known.get(lead["id"]) == fp:
                    skipped += 1
                    continue
                person_id, company_id = sync_lead(store, lead, mine, dis)
                repo.x("DELETE FROM pdb_crm_links WHERE lead_id = ?", (lead["id"],))
                repo.x("INSERT INTO pdb_crm_links (lead_id, person_id, company_id, workspace, fp, synced_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (lead["id"], person_id, company_id, lead.get("workspace") or "main", fp, F.now_iso()))
                done += 1
            gone = set(known) - {lead["id"] for lead in leads}
            for lead_id in gone:
                repo.x("DELETE FROM pdb_crm_links WHERE lead_id = ?", (lead_id,))
            already = {r["value"] for r in repo.q("SELECT value FROM pdb_suppressions")}
            for email in store.read("suppressed"):
                if F.normalise_email(email) and email not in already:
                    P.suppress(store, email, "CRM do-not-contact list")
            stamp = F.now_iso()
            if repo.q1("SELECT 1 AS x FROM pdb_meta WHERE k = 'last_crm_sync'"):
                repo.x("UPDATE pdb_meta SET v = ? WHERE k = 'last_crm_sync'", (stamp,))
            else:
                repo.x("INSERT INTO pdb_meta (k, v) VALUES ('last_crm_sync', ?)", (stamp,))
        from .prospect_search import clear_facets
        clear_facets()
        return {"synced": done, "unchanged": skipped, "removed_links": len(gone), "took_ms": round((time.perf_counter() - started) * 1000)}
    finally:
        _sync_lock.release()


def sync_soon(store, every_seconds: float = 120.0) -> bool:
    """Start a background CRM sync if none ran for a while (never blocks the request that asked)."""
    key = id(store)
    now = time.monotonic()
    if now - _last_auto.get(key, -1e9) < every_seconds:
        return False
    _last_auto[key] = now

    def run():
        try:
            sync_crm(store)
        except Exception:                      # noqa: BLE001 - background best effort
            log.exception("prospect CRM sync failed")
    threading.Thread(target=run, name="prospect-crm-sync", daemon=True).start()
    return True


def safely(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:                          # noqa: BLE001 - ingestion must never break the caller
        log.exception("prospect DB ingest failed in %s", getattr(fn, "__name__", fn))
        return None


# ---------------------------------------------------------------- discovery rows and finder hits
def ingest_rows(store, rows: list) -> dict:
    """Campaign / find-leads discovery rows: {company, website, domain, city, contact_name, title, email, phone, linkedin_url,
    company_size, open_roles, role, source, source_url, ...}."""
    dis = P.disabled(store)
    counts = {"companies": 0, "people": 0, "skipped": 0}
    repo = repo_for(store)
    with repo.tx():
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            src = F.source_key(row.get("source") or "unknown")
            company = {"domain": row.get("domain") or row.get("website"), "name": row.get("company"), "website": row.get("website"),
                       "hq_city": row.get("city"), "hq_country": row.get("country"), "employees": row.get("company_size"),
                       "industry": row.get("industry") or row.get("sector"), "linkedin_url": row.get("company_linkedin_url"),
                       "phones": [row["company_phone"]] if row.get("company_phone") else []}
            if F.to_int(row.get("open_roles")):
                company["signals"] = [{"kind": "hiring", "title": f"{row['open_roles']} open roles" + (f" ({row['role']})" if row.get("role") else ""),
                                       "url": row.get("source_url"), "weight": min(3.0, 1 + F.to_int(row["open_roles"]) / 10)}]
            res = P.upsert_company(store, company, src, None, dis)
            counts["companies" if res.get("id") else "skipped"] += 1
            if row.get("contact_name") or row.get("email") or row.get("linkedin_url"):
                res = P.upsert_person(store, {"company_domain": company["domain"], "company_name": row.get("company"),
                                              "full_name": row.get("contact_name"), "title": row.get("title"), "email": row.get("email"),
                                              "phones": [row["phone"]] if row.get("phone") else [], "linkedin_url": row.get("linkedin_url"),
                                              "city": row.get("city"), "country": row.get("country"), "source_url": row.get("source_url"),
                                              "email_status": _status(row.get("email_status")) if row.get("email_status") else None},
                                      src, None, dis)
                counts["people" if res.get("id") else "skipped"] += 1
    return counts


def ingest_people(store, people: list, domain: str, company: str = "") -> int:
    """Person hits from finders ({email, phone, name, title, linkedin_url, confidence, verified, source, source_url, status?})."""
    n = 0
    for p in people or []:
        status = "verified" if p.get("verified") else _status(p.get("status")) if p.get("status") else None
        res = P.upsert_person(store, {"company_domain": domain, "company_name": company, "full_name": p.get("name"), "title": p.get("title"),
                                      "email": p.get("email"), "phones": [p["phone"]] if p.get("phone") else [], "email_status": status,
                                      "linkedin_url": p.get("linkedin_url"), "email_confidence": p.get("confidence"),
                                      "source_url": p.get("source_url")}, p.get("source") or "unknown")
        n += 1 if res.get("id") else 0
    return n


# ---------------------------------------------------------------- CSV
def import_csv(store, csv_text: str, source: str = "csv") -> dict:
    from .header_map import detect_format, map_columns
    from .lead_import import extract_record, read_table
    headers, table = read_table(csv_text)
    columns = map_columns(headers, [r for _, r in table[:25]], detect_format(headers))
    dis = P.disabled(store)
    out = {"rows": len(table), "companies": 0, "people": 0, "skipped": 0, "columns": {c["column"]: c["field"] for c in columns}}
    repo, today = repo_for(store), date.today()
    with repo.tx():
        for _, row in table:
            rec, _problems = extract_record(row, columns, today)
            ok = False
            if rec.get("company") or rec.get("domain"):
                res = P.upsert_company(store, {"domain": rec.get("domain"), "name": rec.get("company"), "website": rec.get("website"),
                                               "industry": rec.get("sector"), "employees": rec.get("company_size") or None,
                                               "hq_city": rec.get("city"), "hq_state": rec.get("state"), "hq_country": rec.get("country"),
                                               "linkedin_url": rec.get("company_linkedin_url")}, source, None, dis)
                out["companies"] += 1 if res.get("id") else 0
                ok = ok or bool(res.get("id"))
            if rec.get("contact_name") or rec.get("email") or rec.get("linkedin_url"):
                res = P.upsert_person(store, {"company_domain": rec.get("domain"), "company_name": rec.get("company"),
                                              "full_name": rec.get("contact_name"), "title": rec.get("title"), "email": rec.get("email"),
                                              "phones": rec.get("phones") or [], "linkedin_url": rec.get("linkedin_url"),
                                              "city": rec.get("city"), "state": rec.get("state"), "country": rec.get("country")},
                                      source, None, dis)
                out["people"] += 1 if res.get("id") else 0
                ok = ok or bool(res.get("id"))
            out["skipped"] += 0 if ok else 1
    from .prospect_search import clear_facets
    clear_facets()
    return out
