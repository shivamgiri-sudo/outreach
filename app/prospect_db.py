"""The prospect database ("your own Apollo"): companies and people with per-field provenance, merge/dedupe, search,
suppression and erasure. Storage lives in app/prospect_repo.py (SQLite file or MySQL tables), never in the JSON store.

Contract (other engines write through these functions):
  upsert_company(store, record, source, seen_at=None) -> {id, domain, name, created} | {skipped}
  upsert_person(store, record, source, seen_at=None)  -> {id, company_id, created} | {skipped}
  search_people / search_companies (app/prospect_search.py), get_company(store, domain_or_id), get_person(store, id)
  mark_email(store, email, status, reason, at), suppress(store, email_or_domain, reason), delete_person(store, id), stats(store)

Merge/dedupe: companies by domain, then normalised name + city; people by email, then LinkedIn URL, then normalised name
+ company domain. Values from a source the owner disabled are never stored (and are ignored if stored earlier). Every
field keeps [{value, source, seen_at, confidence}]; the shown value follows the BEST VALUE RULE in prospect_fields.py.
"""
import json
import uuid

from . import prospect_fields as F
from .privacy import contact_hash
from .prospect_repo import repo_for

COMPANY_SCALARS = ("name", "website", "linkedin_url", "industry", "sub_industry", "description", "employees", "employees_range",
                   "revenue_range", "founded", "hq_city", "hq_state", "hq_country")
COMPANY_LISTS = ("aliases", "phones", "emails", "technologies", "keywords")
COMPANY_MAPS = ("social", "registry")
PERSON_SCALARS = ("full_name", "first_name", "last_name", "title", "seniority", "department", "email", "linkedin_url",
                  "city", "state", "country", "source_url", "company_domain")


class Skipped(Exception):
    pass


def settings(store) -> dict:
    raw = (store.read("settings") or {}).get("prospect_db") or {}
    return {"disabled_sources": sorted({F.source_key(s) for s in raw.get("disabled_sources") or []}),
            "freshness_days": int(raw.get("freshness_days") or 90)}


def disabled(store) -> frozenset:
    return frozenset(settings(store)["disabled_sources"])


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _doc(row) -> dict:
    return json.loads(row["doc"]) if row and row.get("doc") else {"fields": {}, "lists": {}, "sources": []}


def _conf(record: dict, source: str) -> int:
    c = F.to_int(record.get("confidence"))
    return min(100, c) if c is not None else F.SOURCE_CONFIDENCE.get(source, 50)


def _stamp(doc: dict, source: str, at: str, verified_at=None) -> None:
    doc["sources"] = sorted({*doc.get("sources", []), source})
    doc["first_seen"] = min(doc.get("first_seen") or at, at)
    doc["last_seen"] = max(doc.get("last_seen") or at, at)
    if verified_at:
        doc["last_verified"] = max(doc.get("last_verified") or verified_at, verified_at)


# ---------------------------------------------------------------- companies
def clean_company(record: dict) -> dict:
    r = record or {}
    out = {"domain": F.clean_domain(r.get("domain") or r.get("website")), "name": F.text(r.get("name") or r.get("company"), 200),
           "website": F.safe_http_url(r.get("website")), "linkedin_url": F.linkedin(r.get("linkedin_url"), "company"),
           "industry": F.text(r.get("industry"), 120), "sub_industry": F.text(r.get("sub_industry"), 120),
           "description": F.text(r.get("description"), 2000), "employees": F.to_int(r.get("employees")),
           "employees_range": F.text(r.get("employees_range"), 40), "revenue_range": F.text(r.get("revenue_range"), 60),
           "founded": F.to_int(r.get("founded")), "hq_city": F.text(r.get("hq_city"), 80), "hq_state": F.text(r.get("hq_state"), 80),
           "hq_country": F.text(r.get("hq_country"), 60)}
    if out["founded"] is not None and not 1600 <= out["founded"] <= 2100:
        out["founded"] = None
    if not out["website"] and out["domain"]:
        out["website"] = f"https://{out['domain']}"
    out["aliases"] = [F.text(a, 200) for a in r.get("aliases") or [] if F.text(a)]
    out["phones"] = [p for p in (F.clean_phone(x) for x in r.get("phones") or []) if p]
    out["emails"] = [e for e in (F.normalise_email(x) for x in r.get("emails") or []) if e]
    out["technologies"] = list(dict.fromkeys(F.text(t, 60) for t in r.get("technologies") or [] if F.text(t)))
    out["keywords"] = list(dict.fromkeys(F.text(k, 60).lower() for k in r.get("keywords") or [] if F.text(k)))
    for m in COMPANY_MAPS:
        raw = r.get(m) if isinstance(r.get(m), dict) else {}
        out[m] = {F.low(k)[:40]: (F.safe_http_url(v) if m == "social" else F.text(v, 80)) for k, v in raw.items() if F.text(v)}
        out[m] = {k: v for k, v in out[m].items() if k and v}
    out["signals"] = []
    for s in r.get("signals") or []:
        if isinstance(s, dict) and s.get("kind") in F.SIGNAL_KINDS and (s.get("title") or s.get("url")):
            out["signals"].append({"kind": s["kind"], "title": F.text(s.get("title"), 300), "url": F.safe_http_url(s.get("url")),
                                   "at": F.at_or_now(s.get("at")), "weight": float(s.get("weight") or 1)})
    return out


def _find_company(repo, rec: dict):
    if rec["domain"]:
        hit = repo.q1("SELECT c.* FROM pdb_keys k JOIN pdb_companies c ON c.id = k.ref WHERE k.k = ?", ("d:" + rec["domain"],))
        if hit:
            return hit
    nk = F.company_key(rec["name"])
    if not nk:
        return None
    hit = repo.q1("SELECT c.* FROM pdb_keys k JOIN pdb_companies c ON c.id = k.ref WHERE k.k = ?", (f"n:{nk}|{F.low(rec['hq_city'])}",))
    if hit is None and not rec["hq_city"]:
        hit = repo.q1("SELECT * FROM pdb_companies WHERE name_key = ? ORDER BY last_seen DESC LIMIT 1", (nk,))
    if hit and rec["domain"] and hit.get("domain") and hit["domain"] != rec["domain"]:
        return None                                    # same name, different company website: a different company
    return hit


def materialise_company(cid: str, doc: dict, dis=frozenset()) -> dict:
    f, ls = doc["fields"], doc["lists"]
    v = {k: (F.best(f.get(k), dis) or {}).get("v") for k in COMPANY_SCALARS}
    domain = (F.best(f.get("domain"), dis) or {}).get("v")
    techs, kws = F.list_values(ls.get("technologies"), dis), F.list_values(ls.get("keywords"), dis)
    lo, hi = F.emp_bounds(v["employees"], v["employees_range"])
    srcs = [s for s in doc.get("sources", []) if s not in dis]
    search = " ".join(x for x in [v["name"] or "", *F.list_values(ls.get("aliases"), dis), domain or "", v["industry"] or "",
                                   v["sub_industry"] or "", *kws, *techs, (v["description"] or "")[:300], v["hq_city"] or ""] if x)
    return {"id": cid, "domain": domain, "name": v["name"] or domain or "", "name_key": F.company_key(v["name"]),
            "city_key": F.low(v["hq_city"]), "industry": v["industry"], "industry_key": F.low(v["industry"]) or None,
            "employees": v["employees"], "emp_min": lo, "emp_max": hi, "revenue_range": v["revenue_range"],
            "revenue_key": F.low(v["revenue_range"]) or None, "founded": v["founded"], "hq_city": v["hq_city"], "hq_state": v["hq_state"],
            "hq_country": v["hq_country"], "city_l": F.low(v["hq_city"]) or None, "state_l": F.low(v["hq_state"]) or None,
            "country_l": F.low(v["hq_country"]) or None, "tech_text": "|" + "|".join(t.lower() for t in techs) + "|",
            "kw_text": "|" + "|".join(kws) + "|", "source_text": "|" + "|".join(srcs) + "|", "search_all": search.lower(),
            "first_seen": doc.get("first_seen"), "last_seen": doc.get("last_seen"), "last_verified": doc.get("last_verified"),
            "doc": json.dumps(doc, ensure_ascii=False)}


def _merge_company(doc: dict, rec: dict, source: str, at: str, conf: int, verified=False, domain_match_only=False) -> None:
    f, ls = doc.setdefault("fields", {}), doc.setdefault("lists", {})
    if not domain_match_only:
        F.add_entry(f, "domain", rec["domain"] or None, source, at, conf, verified=verified)
    for k in COMPANY_SCALARS:
        F.add_entry(f, k, rec.get(k), source, at, conf, verified=verified)
    for k in COMPANY_LISTS:
        F.add_items(ls, k, rec.get(k), source, at)
    for m in COMPANY_MAPS:
        for key, value in rec.get(m, {}).items():
            F.add_entry(f, f"{m}.{key}", value, source, at, conf)
    _stamp(doc, source, at, at if verified else None)


def _refresh_signals(repo, cid: str) -> None:
    rows = repo.q("SELECT weight, at FROM pdb_signals WHERE company_id = ?", (cid,))
    now = F.now_iso()
    score, last = 0.0, None
    for r in rows:
        try:
            from datetime import datetime
            age = (datetime.fromisoformat(now) - datetime.fromisoformat(r["at"])).days
        except (TypeError, ValueError):
            age = 999
        score += (r["weight"] or 1) * (1.0 if age <= 30 else 0.5 if age <= 90 else 0.2)
        last = max(last or r["at"], r["at"])
    repo.x("UPDATE pdb_companies SET signal_score = ?, last_signal_at = ? WHERE id = ?", (round(score, 3), last, cid))
    repo.x("UPDATE pdb_people SET c_signal = ? WHERE company_id = ?", (round(score, 3), cid))


def _suppressed_value(repo, *values) -> bool:
    vals = [v for v in values if v]
    if not vals:
        return False
    return bool(repo.q1(f"SELECT 1 AS x FROM pdb_suppressions WHERE value IN ({', '.join('?' for _ in vals)})", vals))


def upsert_company(store, record: dict, source: str, seen_at=None, _dis=None) -> dict:
    src = F.source_key(source)
    dis = disabled(store) if _dis is None else _dis
    if src in dis:
        return {"skipped": "source_disabled", "source": src}
    rec = clean_company(record)
    if not rec["domain"] and not F.company_key(rec["name"]):
        return {"skipped": "no_identity"}
    at, repo = F.at_or_now(seen_at), repo_for(store)
    with repo.tx():
        hit = _find_company(repo, rec)
        cid = hit["id"] if hit else _id()
        doc = _doc(hit)
        old_view = hit and (hit.get("name"), hit.get("kw_text"), hit.get("employees"))
        for k in record.get("_match_only") or ():
            rec[k] = [] if isinstance(rec.get(k), list) else ("" if k != "domain" else rec[k])
        _merge_company(doc, rec, src, at, _conf(record, src), bool(record.get("verified")), "domain" in (record.get("_match_only") or ()))
        row = materialise_company(cid, doc, dis)
        row["suppressed"] = 1 if _suppressed_value(repo, row["domain"]) else 0
        repo.save("pdb_companies", row, new=hit is None)
        for key in ([f"d:{row['domain']}"] if row["domain"] else []) + ([f"n:{row['name_key']}|{row['city_key']}"] if row["name_key"] else []):
            repo.insert_ignore("pdb_keys", {"k": key, "ref": cid})
        for s in rec["signals"]:
            dedupe = (s["url"] or s["title"]).lower()[:180]
            if not repo.q1("SELECT 1 AS x FROM pdb_signals WHERE company_id = ? AND dedupe = ? AND kind = ?", (cid, dedupe, s["kind"])):
                repo.x("INSERT INTO pdb_signals (company_id, kind, title, url, at, weight, source, dedupe) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (cid, s["kind"], s["title"], s["url"], s["at"], s["weight"], src, dedupe))
        if rec["signals"]:
            _refresh_signals(repo, cid)
        if hit and old_view != (row["name"], row["kw_text"], row["employees"]):
            _reindex_people(repo, cid, dis)
    return {"id": cid, "domain": row["domain"], "name": row["name"], "created": hit is None}


# ---------------------------------------------------------------- people
def clean_person(record: dict) -> dict:
    r = record or {}
    email = F.normalise_email(r.get("email"))
    first, last = F.text(r.get("first_name"), 80), F.text(r.get("last_name"), 80)
    full = F.text(r.get("full_name") or r.get("name"), 160) or " ".join(x for x in (first, last) if x)
    if full and not first:
        first, _, last = full.partition(" ")
    title = F.text(r.get("title"), 200)
    seniority = r.get("seniority") if r.get("seniority") in F.SENIORITIES else F.seniority_of(title)
    dept = r.get("department") if r.get("department") in F.DEPARTMENTS else F.department_of(title)
    status = r.get("email_status") if r.get("email_status") in F.EMAIL_STATUSES else None
    domain = F.clean_domain(r.get("company_domain") or r.get("company_website"))
    if not domain and email:
        domain = F.clean_domain(email)
    return {"full_name": full, "first_name": first, "last_name": last.strip(), "title": title, "seniority": seniority or None,
            "department": dept or None, "email": email, "email_status": status if email else None,
            "email_confidence": F.to_int(r.get("email_confidence")), "phones": [p for p in (F.clean_phone(x) for x in
                                                                                            (r.get("phones") or []) + ([r["phone"]] if r.get("phone") else [])) if p],
            "linkedin_url": F.linkedin(r.get("linkedin_url"), "in"), "city": F.text(r.get("city"), 80), "state": F.text(r.get("state"), 80),
            "country": F.text(r.get("country"), 60), "source_url": F.safe_http_url(r.get("source_url")), "company_domain": domain,
            "company_name": F.text(r.get("company_name") or r.get("company"), 200)}


def erased_hashes(rec: dict) -> set:
    out = {contact_hash(rec["email"])} if rec.get("email") else set()
    out |= {contact_hash(p) for p in rec.get("phones") or []}
    if rec.get("linkedin_url"):
        out.add(contact_hash(rec["linkedin_url"].lower()))
    return out


def _is_erased(store, repo, rec: dict) -> bool:
    hashes = erased_hashes(rec)
    if not hashes:
        return False
    if hashes & set(store.read("suppressed_hashes")):
        return True
    return bool(repo.q1(f"SELECT 1 AS x FROM pdb_erased WHERE h IN ({', '.join('?' for _ in hashes)})", list(hashes)))


def _person_keys(rec: dict) -> list:
    keys = []
    if rec["email"]:
        keys.append("e:" + rec["email"])
    if rec["linkedin_url"]:
        keys.append("l:" + rec["linkedin_url"].lower())
    nk = F.name_key(rec["full_name"])
    if nk and rec["company_domain"]:
        keys.append(f"n:{nk}|{rec['company_domain']}")
    return keys


def materialise_person(pid: str, doc: dict, company: dict, dis=frozenset()) -> dict:
    f, ls = doc["fields"], doc["lists"]
    v = {k: (F.best(f.get(k), dis) or {}) for k in PERSON_SCALARS}
    val = {k: e.get("v") for k, e in v.items()}
    email_entry = v["email"]
    status = email_entry.get("st") or ("unknown" if val["email"] else None)
    phones = F.list_values(ls.get("phones"), dis)
    conf = doc.get("email_confidence") if val["email"] and doc.get("email_confidence_for") == val["email"] else None
    conf = conf if conf is not None else (F.STATUS_CONFIDENCE.get(status, 0) if val["email"] else 0)
    srcs = [s for s in doc.get("sources", []) if s not in dis]
    cname = (company or {}).get("name") or ""
    ckw = (company or {}).get("kw_text") or ""
    rank = F.STATUS_POINTS.get(status, 0) + (10 if phones else 0) + (5 if val["linkedin_url"] else 0) + F.SENIORITY_POINTS.get(val["seniority"], 0)
    return {"id": pid, "company_id": (company or {}).get("id"), "company_domain": val["company_domain"],
            "full_name": val["full_name"] or "", "name_key": F.name_key(val["full_name"]) or None, "title": val["title"] or "",
            "seniority": val["seniority"], "department": val["department"], "email": val["email"], "email_status": status,
            "email_confidence": conf, "has_email": 1 if val["email"] and status != "invalid" else 0, "has_phone": 1 if phones else 0,
            "linkedin_url": val["linkedin_url"], "city": val["city"], "state": val["state"], "country": val["country"],
            "city_l": F.low(val["city"]) or None, "state_l": F.low(val["state"]) or None, "country_l": F.low(val["country"]) or None,
            "source_text": "|" + "|".join(srcs) + "|",
            "search_all": " ".join(x for x in (val["full_name"], val["title"], cname, val["company_domain"], ckw.replace("|", " ")) if x).lower(),
            "rank_score": rank, "c_signal": (company or {}).get("signal_score") or 0, "c_employees": (company or {}).get("employees"),
            "first_seen": doc.get("first_seen"), "last_seen": doc.get("last_seen"),
            "last_verified": doc.get("last_verified"), "doc": json.dumps(doc, ensure_ascii=False)}


def _reindex_people(repo, cid: str, dis) -> None:
    company = repo.q1("SELECT * FROM pdb_companies WHERE id = ?", (cid,))
    for row in repo.q("SELECT id, doc, suppressed FROM pdb_people WHERE company_id = ?", (cid,)):
        new = materialise_person(row["id"], _doc(row), company, dis)
        new["suppressed"] = row["suppressed"]
        repo.save("pdb_people", new, new=False)


def _count_people(repo, cid) -> None:
    if cid:
        repo.x("UPDATE pdb_companies SET people_count = (SELECT COUNT(*) FROM pdb_people WHERE company_id = ?) WHERE id = ?", (cid, cid))


def upsert_person(store, record: dict, source: str, seen_at=None, _dis=None) -> dict:
    src = F.source_key(source)
    dis = disabled(store) if _dis is None else _dis
    if src in dis:
        return {"skipped": "source_disabled", "source": src}
    rec = clean_person(record)
    at, repo = F.at_or_now(seen_at), repo_for(store)
    if rec["email"] and F.is_role(rec["email"]):          # hr@, info@: the company's inbox, not a person
        if rec["company_domain"]:
            upsert_company(store, {"domain": rec["company_domain"], "name": rec["company_name"], "emails": [rec["email"]]}, src, at, dis)
        rec["email"], rec["email_status"] = None, None
    keys = _person_keys(rec)
    if not keys:
        return {"skipped": "no_identity"}
    with repo.tx():
        if _is_erased(store, repo, rec):
            return {"skipped": "erased"}
        hit = None
        for key in keys:
            hit = repo.q1("SELECT p.* FROM pdb_keys k JOIN pdb_people p ON p.id = k.ref WHERE k.k = ?", (key,))
            if hit:
                break
        company = None
        if rec["company_domain"]:
            known = repo.q1("SELECT c.* FROM pdb_keys k JOIN pdb_companies c ON c.id = k.ref WHERE k.k = ?", ("d:" + rec["company_domain"],))
            if known is None or (rec["company_name"] and not known.get("name_key")):
                upsert_company(store, {"domain": rec["company_domain"], "name": rec["company_name"]}, src, at, dis)
            company = repo.q1("SELECT c.* FROM pdb_keys k JOIN pdb_companies c ON c.id = k.ref WHERE k.k = ?", ("d:" + rec["company_domain"],))
        elif hit and hit.get("company_id"):
            company = repo.q1("SELECT * FROM pdb_companies WHERE id = ?", (hit["company_id"],))
        pid = hit["id"] if hit else _id()
        doc = _doc(hit)
        conf, verified = _conf(record, src), bool(record.get("verified"))
        status = rec["email_status"]
        if rec["email"]:
            mark = repo.q1("SELECT status, at FROM pdb_email_marks WHERE email = ?", (rec["email"],))
            if mark and mark["at"] >= at:
                status = mark["status"]                  # a later bounce / verification beats what this source says
        f = doc.setdefault("fields", {})
        match_only = set(record.get("_match_only") or ())        # identity values used to find the row, credited to another source
        for k in PERSON_SCALARS:
            if k in match_only:
                continue
            if k == "email":
                F.add_entry(f, k, rec["email"], src, at, conf, status=status or "unknown")
            else:
                F.add_entry(f, k, rec.get(k), src, at, conf, verified=verified)
        F.add_items(doc.setdefault("lists", {}), "phones", rec["phones"], src, at)
        if rec["email"] and rec["email_confidence"] is not None:
            doc["email_confidence"], doc["email_confidence_for"] = min(100, rec["email_confidence"]), rec["email"]
        _stamp(doc, src, at, at if status in ("verified", "valid") or verified else None)
        row = materialise_person(pid, doc, company, dis)
        row["suppressed"] = 1 if _suppressed_value(repo, row["email"], row["company_domain"], F.clean_domain(row["email"] or "")) else 0
        repo.save("pdb_people", row, new=hit is None)
        for key in keys:
            repo.insert_ignore("pdb_keys", {"k": key, "ref": pid})
        if hit is None or (hit.get("company_id") != row["company_id"]):
            _count_people(repo, row["company_id"])
            if hit:
                _count_people(repo, hit.get("company_id"))
    return {"id": pid, "company_id": row["company_id"], "created": hit is None}


# ---------------------------------------------------------------- reads
def _public_company(row: dict, dis) -> dict:
    doc = _doc(row)
    out = {k: row.get(k) for k in ("id", "domain", "name", "industry", "employees", "revenue_range", "founded", "hq_city", "hq_state",
                                   "hq_country", "signal_score", "last_signal_at", "people_count", "first_seen", "last_seen", "last_verified")}
    f, ls = doc["fields"], doc["lists"]
    for k in ("website", "linkedin_url", "sub_industry", "description", "employees_range"):
        out[k] = (F.best(f.get(k), dis) or {}).get("v")
    for k in COMPANY_LISTS:
        out[k] = F.list_values(ls.get(k), dis)
    for m in COMPANY_MAPS:
        out[m] = {name.split(".", 1)[1]: (F.best(e, dis) or {}).get("v") for name, e in f.items() if name.startswith(m + ".")}
        out[m] = {k: v for k, v in out[m].items() if v}
    out["sources"] = [s for s in doc.get("sources", []) if s not in dis]
    out["suppressed"] = bool(row.get("suppressed"))
    out["provenance"] = {k: F.field_view(e, dis) for k, e in f.items()}
    out["list_provenance"] = {k: [{"value": i["v"], "sources": i["s"], "first_seen": i["first"], "last_seen": i["last"]} for i in items]
                              for k, items in ls.items()}
    return out


def get_company(store, key: str):
    repo, dis = repo_for(store), disabled(store)
    key = str(key or "").strip().lower()
    row = repo.q1("SELECT * FROM pdb_companies WHERE id = ?", (key,)) or \
        repo.q1("SELECT c.* FROM pdb_keys k JOIN pdb_companies c ON c.id = k.ref WHERE k.k = ?", ("d:" + (F.clean_domain(key) or key),))
    if row is None:
        return None
    out = _public_company(row, dis)
    out["signals"] = repo.q("SELECT kind, title, url, at, weight, source FROM pdb_signals WHERE company_id = ? ORDER BY at DESC LIMIT 200", (row["id"],))
    out["signals"] = [s for s in out["signals"] if s["source"] not in dis]
    from .prospect_search import person_rows
    out["people"] = person_rows(store, repo.q("SELECT p.*, c.name AS c_name, c.domain AS c_domain, c.industry AS c_industry, "
                                              "c.employees AS c_employees, c.hq_city AS c_hq_city FROM pdb_people p LEFT JOIN pdb_companies c "
                                              "ON c.id = p.company_id WHERE p.company_id = ? ORDER BY p.rank_score DESC LIMIT 200", (row["id"],)))
    return out


def get_person(store, person_id: str):
    repo, dis = repo_for(store), disabled(store)
    row = repo.q1("SELECT * FROM pdb_people WHERE id = ?", (str(person_id or ""),))
    if row is None:
        return None
    doc = _doc(row)
    company = repo.q1("SELECT * FROM pdb_companies WHERE id = ?", (row["company_id"],)) if row.get("company_id") else None
    if dis & set(doc.get("sources", [])):
        row = {**row, **materialise_person(row["id"], doc, company, dis)}       # a just-disabled source is ignored at once
    from .prospect_search import PERSON_COLS
    out = {k: row.get(k) for k in PERSON_COLS}
    out["phones"] = F.list_values(doc["lists"].get("phones"), dis)
    out["first_name"] = (F.best(doc["fields"].get("first_name"), dis) or {}).get("v")
    out["last_name"] = (F.best(doc["fields"].get("last_name"), dis) or {}).get("v")
    out["source_url"] = (F.best(doc["fields"].get("source_url"), dis) or {}).get("v")
    out["sources"] = [s for s in doc.get("sources", []) if s not in dis]
    out["suppressed"] = bool(row.get("suppressed"))
    out["provenance"] = {k: F.field_view(e, dis) for k, e in doc["fields"].items()}
    out["provenance"]["phones"] = {"items": [{"value": i["v"], "sources": i["s"], "first_seen": i["first"], "last_seen": i["last"]}
                                             for i in doc["lists"].get("phones", [])]}
    out["company"] = {k: company.get(k) for k in ("id", "domain", "name", "industry", "employees", "hq_city", "hq_country")} if company else None
    out["crm_leads"] = [r["lead_id"] for r in repo.q("SELECT lead_id FROM pdb_crm_links WHERE person_id = ?", (row["id"],))]
    return out


# ---------------------------------------------------------------- writes from bounces, opt-outs and erasure
def mark_email(store, email: str, status: str, reason: str = "", at=None) -> int:
    """Record a bounce / verifier verdict for an address; every person holding it is updated. Returns people changed."""
    email = F.normalise_email(email)
    if not email or status not in F.EMAIL_STATUSES:
        return 0
    at, repo, dis = F.at_or_now(at), repo_for(store), disabled(store)
    changed = 0
    with repo.tx():
        old = repo.q1("SELECT at FROM pdb_email_marks WHERE email = ?", (email,))
        if old is None:
            repo.x("INSERT INTO pdb_email_marks (email, status, reason, at) VALUES (?, ?, ?, ?)", (email, status, F.text(reason, 200), at))
        elif at >= old["at"]:
            repo.x("UPDATE pdb_email_marks SET status = ?, reason = ?, at = ? WHERE email = ?", (status, F.text(reason, 200), at, email))
        for ref in repo.q("SELECT ref FROM pdb_keys WHERE k = ?", ("e:" + email,)):
            row = repo.q1("SELECT * FROM pdb_people WHERE id = ?", (ref["ref"],))
            if not row:
                continue
            doc = _doc(row)
            for e in doc["fields"].get("email", []):
                if e["v"] == email and (at >= e["at"] or status == "invalid"):
                    e["st"] = status
            if status in ("verified", "valid"):
                doc["last_verified"] = max(doc.get("last_verified") or at, at)
            company = repo.q1("SELECT * FROM pdb_companies WHERE id = ?", (row["company_id"],)) if row.get("company_id") else None
            new = materialise_person(row["id"], doc, company, dis)
            new["suppressed"] = row["suppressed"]
            repo.save("pdb_people", new, new=False)
            changed += 1
    return changed


def suppress(store, value: str, reason: str = "opt-out") -> dict:
    """Opt-out by email or whole domain. Suppressed people are never exported or added to leads."""
    raw = str(value or "").strip().lower()
    email = F.normalise_email(raw)
    domain = None if email else F.clean_domain(raw)
    if not (email or domain):
        raise ValueError("Give an email address or a company domain.")
    repo = repo_for(store)
    target = email or domain
    with repo.tx():
        repo.insert_ignore("pdb_suppressions", {"value": target, "kind": "email" if email else "domain", "reason": F.text(reason, 200), "at": F.now_iso()})
        if email:
            n = repo.x("UPDATE pdb_people SET suppressed = 1 WHERE id IN (SELECT ref FROM pdb_keys WHERE k = ?)", ("e:" + email,))
        else:
            n = repo.x("UPDATE pdb_people SET suppressed = 1 WHERE company_domain = ? OR email LIKE ?", (domain, "%@" + domain))
            repo.x("UPDATE pdb_companies SET suppressed = 1 WHERE domain = ?", (domain,))
    return {"suppressed": target, "kind": "email" if email else "domain", "people": n}


def delete_person(store, person_id: str) -> bool:
    """Erasure: the row, its keys and links go; hashes of its email/phones/LinkedIn stay so no source can re-add it."""
    repo = repo_for(store)
    with repo.tx():
        row = repo.q1("SELECT * FROM pdb_people WHERE id = ?", (str(person_id or ""),))
        if row is None:
            return False
        doc = _doc(row)
        hashes = set()
        for e in doc["fields"].get("email", []):
            hashes |= erased_hashes({"email": e["v"]})
        for e in doc["fields"].get("linkedin_url", []):
            hashes |= erased_hashes({"linkedin_url": e["v"]})
        for i in doc["lists"].get("phones", []):
            hashes |= erased_hashes({"phones": [i["v"]]})
        for h in hashes:
            repo.insert_ignore("pdb_erased", {"h": h, "at": F.now_iso()})
        repo.x("DELETE FROM pdb_keys WHERE ref = ?", (row["id"],))
        repo.x("DELETE FROM pdb_crm_links WHERE person_id = ?", (row["id"],))
        repo.x("DELETE FROM pdb_people WHERE id = ?", (row["id"],))
        _count_people(repo, row.get("company_id"))
    return True


def erase_contacts(store, emails=(), phones=(), linkedin_urls=()) -> int:
    """Erase every prospect holding any of these contacts (called when a CRM lead is erased)."""
    repo = repo_for(store)
    keys = [f"e:{F.normalise_email(e)}" for e in emails if F.normalise_email(e)] + [f"l:{u.lower()}" for u in linkedin_urls if u]
    ids = {r["ref"] for k in keys for r in repo.q("SELECT ref FROM pdb_keys WHERE k = ?", (k,))}
    for p in phones:
        h = erased_hashes({"phones": [F.clean_phone(p)]}) if F.clean_phone(p) else set()
        for x in h:
            repo.insert_ignore("pdb_erased", {"h": x, "at": F.now_iso()})
    return sum(1 for pid in ids if delete_person(store, pid))


def purge_source(store, source: str, remove: bool = True) -> dict:
    """Remove every value a (now disabled) source gave; rows left with no source are deleted. remove=False only re-picks
    the shown values (after a source was switched off or back on)."""
    src, repo = F.source_key(source), repo_for(store)
    dis = disabled(store)
    out = {"people": 0, "companies": 0, "deleted": 0}
    like = f"%|{src}|%"
    with repo.tx():
        for table, kind in (("pdb_people", "people"), ("pdb_companies", "companies")):
            for row in repo.q(f"SELECT * FROM {table} WHERE source_text LIKE ?", (like,)) + \
                    repo.q(f"SELECT * FROM {table} WHERE doc LIKE ? AND source_text NOT LIKE ?", (f'%"s": "{src}"%', like)):
                doc = _doc(row)
                if not remove:
                    new = materialise_person(row["id"], doc, repo.q1("SELECT * FROM pdb_companies WHERE id = ?", (row["company_id"],))
                                             if row.get("company_id") else None, dis) if table == "pdb_people" else materialise_company(row["id"], doc, dis)
                    new["suppressed"] = row["suppressed"]
                    repo.save(table, new, new=False)
                    out[kind] += 1
                    continue
                doc["fields"] = {k: [e for e in v if e["s"] != src] for k, v in doc["fields"].items()}
                doc["fields"] = {k: v for k, v in doc["fields"].items() if v}
                for k, items in doc["lists"].items():
                    doc["lists"][k] = [{**i, "s": [s for s in i["s"] if s != src]} for i in items if [s for s in i["s"] if s != src]]
                doc["sources"] = [s for s in doc.get("sources", []) if s != src]
                if not doc["sources"]:
                    if table == "pdb_people":
                        repo.x("DELETE FROM pdb_keys WHERE ref = ?", (row["id"],))
                        repo.x("DELETE FROM pdb_people WHERE id = ?", (row["id"],))
                    else:
                        repo.x("DELETE FROM pdb_companies WHERE id = ? AND people_count = 0", (row["id"],))
                    out["deleted"] += 1
                    continue
                if table == "pdb_people":
                    company = repo.q1("SELECT * FROM pdb_companies WHERE id = ?", (row["company_id"],)) if row.get("company_id") else None
                    new = materialise_person(row["id"], doc, company, dis)
                else:
                    new = materialise_company(row["id"], doc, dis)
                new["suppressed"] = row["suppressed"]
                repo.save(table, new, new=False)
                out[kind] += 1
        if remove:
            repo.x("DELETE FROM pdb_signals WHERE source = ?", (src,))
    return out


def stats(store) -> dict:
    repo = repo_for(store)
    one = lambda sql, p=(): repo.scalar(sql, p) or 0
    by_source = {}
    for row in repo.q("SELECT source_text, COUNT(*) AS n FROM pdb_people GROUP BY source_text"):
        for s in (row["source_text"] or "").strip("|").split("|"):
            if s:
                by_source[s] = by_source.get(s, 0) + row["n"]
    return {"people": one("SELECT COUNT(*) FROM pdb_people"), "companies": one("SELECT COUNT(*) FROM pdb_companies"),
            "with_email": one("SELECT COUNT(*) FROM pdb_people WHERE has_email = 1"),
            "verified": one("SELECT COUNT(*) FROM pdb_people WHERE email_status IN ('verified', 'valid')"),
            "with_phone": one("SELECT COUNT(*) FROM pdb_people WHERE has_phone = 1"),
            "suppressed": one("SELECT COUNT(*) FROM pdb_people WHERE suppressed = 1"),
            "suppression_entries": one("SELECT COUNT(*) FROM pdb_suppressions"), "erased": one("SELECT COUNT(*) FROM pdb_erased"),
            "signals": one("SELECT COUNT(*) FROM pdb_signals"), "in_crm": one("SELECT COUNT(DISTINCT person_id) FROM pdb_crm_links"),
            "people_by_source": dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
            "storage": repo.name, "size_bytes": repo.size_bytes(),
            "last_crm_sync": repo.scalar("SELECT v FROM pdb_meta WHERE k = 'last_crm_sync'"), **settings(store)}


from .prospect_search import facets, search_companies, search_people  # noqa: E402,F401  (part of the contract)
