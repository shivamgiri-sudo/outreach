"""Apollo-style search over the prospect database: filter normalisation, SQL building, paging and facets.

Filters (all optional; lists match any value):
  q                      free text over name, title, company, domain and company keywords (every word must match)
  title / title_exclude  title keywords (phrase or word prefix, any of them) / titles to leave out
  seniority, department  enums from prospect_fields
  industry, revenue      company industry / revenue range (exact, case-insensitive)
  employees              buckets like "51-200" or "10001+" (overlap); employees_min / employees_max
  city, state, country   person location OR company HQ (case-insensitive)
  technologies, keywords company technologies / keywords (any)
  signal_kinds, signal_days   company has a signal of these kinds within N days
  email_status, has_email, has_phone, verified_only
  founded_min, founded_max, updated_days, source, not_in_crm, include_suppressed, company_id
Sort: relevance (default), updated, signal, employees, name.
"""
import time
from datetime import datetime, timedelta, timezone

from . import prospect_fields as F
from .prospect_repo import repo_for

LIST_KEYS = ("title", "title_exclude", "seniority", "department", "industry", "revenue", "employees", "city", "state", "country",
             "technologies", "keywords", "signal_kinds", "email_status", "source")
SPLIT_KEYS = ("title", "title_exclude", "technologies", "keywords", "seniority", "department", "email_status", "source", "signal_kinds")
INT_KEYS = ("employees_min", "employees_max", "founded_min", "founded_max", "signal_days", "updated_days")
BOOL_KEYS = ("has_email", "has_phone", "verified_only", "not_in_crm", "include_suppressed")
PERSON_SORTS = {"relevance": "p.rank_score DESC, p.last_seen DESC", "updated": "p.last_seen DESC", "name": "p.full_name ASC",
                "signal": "p.c_signal DESC, p.rank_score DESC", "employees": "p.c_employees DESC, p.rank_score DESC"}
COMPANY_SORTS = {"relevance": "c.signal_score DESC, c.people_count DESC, c.last_seen DESC", "updated": "c.last_seen DESC",
                 "name": "c.name ASC", "signal": "c.signal_score DESC", "employees": "c.employees DESC"}
PERSON_COLS = ("id", "full_name", "title", "seniority", "department", "email", "email_status", "email_confidence", "linkedin_url",
               "city", "state", "country", "company_id", "company_domain", "first_seen", "last_seen", "last_verified")
MAX_SIZE = 100
MAX_VALUES = 30
_FACETS = {}


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def normalise(raw: dict) -> dict:
    """Clean a filter dict (from JSON or query params: lists may be repeated params or comma-separated text)."""
    raw = raw or {}
    out = {"q": F.text(raw.get("q"), 200)}
    for key in LIST_KEYS:
        value = raw.get(key)
        values = value if isinstance(value, list) else ([value] if value not in (None, "") else [])
        if key in SPLIT_KEYS:
            values = [part for v in values for part in str(v).split(",")]
        out[key] = list(dict.fromkeys(F.text(v, 120) for v in values if F.text(v)))[:MAX_VALUES]
    for key in INT_KEYS:
        out[key] = F.to_int(raw.get(key)) if raw.get(key) not in (None, "") else None
    for key in BOOL_KEYS:
        out[key] = _truthy(raw.get(key)) if raw.get(key) not in (None, "") else False
    out["company_id"] = F.text(raw.get("company_id"), 40)
    out["seniority"] = [s for s in out["seniority"] if s in F.SENIORITIES]
    out["department"] = [d for d in out["department"] if d in F.DEPARTMENTS]
    out["email_status"] = [s for s in out["email_status"] if s in F.EMAIL_STATUSES]
    out["signal_kinds"] = [s for s in out["signal_kinds"] if s in F.SIGNAL_KINDS]
    out["source"] = [F.source_key(s) for s in out["source"]]
    return out


def _in(col: str, values: list, lower=True) -> tuple:
    vals = [v.lower() for v in values] if lower else list(values)
    return f"{col} IN ({', '.join('?' for _ in vals)})", vals


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat(timespec="seconds")


def _workspace_clause() -> tuple:
    from .workspaces import _SCOPE
    selected, allowed = _SCOPE.get()
    if selected:
        return " AND l.workspace = ?", [selected]
    if allowed is not None:
        vals = sorted(allowed)
        return f" AND l.workspace IN ({', '.join('?' for _ in vals)})", vals
    return "", []


def company_where(repo, f: dict, where: list, params: list) -> None:
    def add(sql, vals=()):
        where.append(sql)
        params.extend(vals)
    if f["industry"]:
        add(*_in("c.industry_key", f["industry"]))
    if f["revenue"]:
        add(*_in("c.revenue_key", f["revenue"]))
    buckets = []
    for b in f["employees"]:
        lo, hi = F.emp_bounds(None, b)
        if lo is not None:
            buckets.append((lo, hi))
    if buckets:
        add("(" + " OR ".join("(c.emp_max >= ? AND c.emp_min <= ?)" for _ in buckets) + ")", [x for b in buckets for x in b])
    if f["employees_min"] is not None:
        add("c.emp_max >= ?", [f["employees_min"]])
    if f["employees_max"] is not None:
        add("c.emp_min <= ?", [f["employees_max"]])
    if f["technologies"]:
        add("(" + " OR ".join("c.tech_text LIKE ?" for _ in f["technologies"]) + ")", [f"%|{t.lower()}|%" for t in f["technologies"]])
    if f["keywords"]:
        sql, vals = repo.text_match("companies", "c", "search_all", f["keywords"])
        if sql:
            add(sql, vals)
    if f["founded_min"] is not None:
        add("c.founded >= ?", [f["founded_min"]])
    if f["founded_max"] is not None:
        add("c.founded <= ?", [f["founded_max"]])
    if f["signal_kinds"] or f["signal_days"]:
        sql, vals = "c.id IN (SELECT s.company_id FROM pdb_signals s WHERE 1 = 1", []
        if f["signal_kinds"]:
            part, v = _in("s.kind", f["signal_kinds"], lower=False)
            sql, vals = sql + " AND " + part, vals + v
        if f["signal_days"]:
            sql, vals = sql + " AND s.at >= ?", vals + [_days_ago(f["signal_days"])]
        add(sql + ")", vals)


def _location(f: dict, where: list, params: list, person: bool) -> None:
    for key, pcol, ccol in (("city", "p.city_l", "c.city_l"), ("state", "p.state_l", "c.state_l"), ("country", "p.country_l", "c.country_l")):
        if f[key]:
            vals = [v.lower() for v in f[key]]
            marks = ", ".join("?" for _ in vals)
            if person:
                where.append(f"({pcol} IN ({marks}) OR p.company_id IN (SELECT c.id FROM pdb_companies c WHERE {ccol} IN ({marks})))")
                params.extend(vals + vals)
            else:
                where.append(f"{ccol} IN ({marks})")
                params.extend(vals)


def people_query(repo, f: dict) -> tuple:
    where, params = [], []
    if not f["include_suppressed"]:
        where.append("p.suppressed = 0")
    if f["q"]:
        sql, vals = repo.text_match("people", "p", "search_all", [f["q"]], all_words=True)
        if sql:
            where.append(sql)
            params += vals
    if f["title"]:
        sql, vals = repo.text_match("people", "p", "title", f["title"])
        if sql:
            where.append(sql)
            params += vals
    if f["title_exclude"]:
        sql, vals = repo.text_match("people", "p", "title", f["title_exclude"])
        if sql:
            where.append(f"NOT ({sql})")
            params += vals
    if f["seniority"]:
        where.append(_in("p.seniority", f["seniority"])[0])
        params += f["seniority"]
    if f["department"]:
        where.append(_in("p.department", f["department"])[0])
        params += f["department"]
    statuses = list(f["email_status"])
    if f["verified_only"]:
        statuses = [s for s in (statuses or ["verified", "valid"]) if s in ("verified", "valid")] or ["verified"]
    if statuses:
        where.append(_in("p.email_status", statuses)[0])
        params += statuses
    if f["has_email"]:
        where.append("p.has_email = 1")
    if f["has_phone"]:
        where.append("p.has_phone = 1")
    if f["updated_days"]:
        where.append("p.last_seen >= ?")
        params.append(_days_ago(f["updated_days"]))
    if f["source"]:
        where.append("(" + " OR ".join("p.source_text LIKE ?" for _ in f["source"]) + ")")
        params += [f"%|{s}|%" for s in f["source"]]
    if f["company_id"]:
        where.append("p.company_id = ?")
        params.append(f["company_id"])
    if f["not_in_crm"]:
        ws_sql, ws_vals = _workspace_clause()
        where.append(f"NOT EXISTS (SELECT 1 FROM pdb_crm_links l WHERE l.person_id = p.id{ws_sql})")
        params += ws_vals
    _location(f, where, params, person=True)
    cwhere, cparams = [], []
    company_where(repo, f, cwhere, cparams)
    if cwhere:                                   # company filters run once over companies, then people by company_id
        where.append(f"p.company_id IN (SELECT c.id FROM pdb_companies c WHERE {' AND '.join(cwhere)})")
        params += cparams
    return (" WHERE " + " AND ".join(where)) if where else "", params, False


def person_rows(store, rows: list) -> list:
    repo = repo_for(store)
    ids = [r["id"] for r in rows]
    linked = set()
    if ids:
        ws_sql, ws_vals = _workspace_clause()
        linked = {r["person_id"] for r in repo.q(f"SELECT person_id FROM pdb_crm_links l WHERE person_id IN ({', '.join('?' for _ in ids)}){ws_sql}",
                                                 ids + ws_vals)}
    status = _crm_status(store, ids, linked)
    dis = None
    out = []
    for r in rows:
        item = {k: r.get(k) for k in PERSON_COLS}
        item["crm_status"] = status.get(r["id"], "")
        item["has_phone"] = bool(r.get("has_phone"))
        item["suppressed"] = bool(r.get("suppressed"))
        item["in_crm"] = r["id"] in linked
        dis = dis if dis is not None else _disabled(store)
        item["sources"] = [s for s in (r.get("source_text") or "").strip("|").split("|") if s and s not in dis]
        item["company"] = {"id": r.get("company_id"), "name": r.get("c_name"), "domain": r.get("c_domain"), "industry": r.get("c_industry"),
                           "employees": r.get("c_employees"), "hq_city": r.get("c_hq_city"), "signal_score": r.get("c_signal")} if r.get("company_id") else None
        out.append(item)
    return out


_ORDER = ("replied", "connected", "tried", "never", "consultant", "invalid")


def _crm_status(store, ids: list, linked: set) -> dict:
    """For people who are already leads: the furthest any of their leads got (replied > connected > tried > never)."""
    mine = [i for i in ids if i in linked]
    if not mine:
        return {}
    from . import contact_status
    repo = repo_for(store)
    by_person: dict = {}
    for r in repo.q(f"SELECT person_id, lead_id FROM pdb_crm_links WHERE person_id IN ({', '.join('?' for _ in mine)})", mine):
        by_person.setdefault(r["person_id"], []).append(r["lead_id"])
    leads = {l["id"]: l for l in store.read("leads")}
    idx = contact_status.index(store)
    out = {}
    for pid, lead_ids in by_person.items():
        got = [contact_status.of(leads[l], idx)["status"] for l in lead_ids if l in leads]
        if got:
            out[pid] = min(got, key=_ORDER.index)
    return out


def _disabled(store):
    from .prospect_db import disabled
    return disabled(store)


def _page(page, size) -> tuple:
    size = max(1, min(MAX_SIZE, F.to_int(size) or 25))
    page = max(1, F.to_int(page) or 1)
    return page, size


def search_people(store, filters=None, sort="relevance", page=1, size=25) -> dict:
    started = time.perf_counter()
    repo, f = repo_for(store), normalise(filters)
    page, size = _page(page, size)
    where, params, uses_company = people_query(repo, f)
    order = PERSON_SORTS.get(sort or "relevance", PERSON_SORTS["relevance"])
    join = " LEFT JOIN pdb_companies c ON c.id = p.company_id"
    total = repo.scalar(f"SELECT COUNT(*) FROM pdb_people p{join if uses_company else ''}{where}", params) or 0
    rows = repo.q(f"SELECT p.id, p.full_name, p.title, p.seniority, p.department, p.email, p.email_status, p.email_confidence, "
                  f"p.linkedin_url, p.city, p.state, p.country, p.company_id, p.company_domain, p.first_seen, p.last_seen, p.last_verified, "
                  f"p.has_phone, p.suppressed, p.source_text, c.name AS c_name, c.domain AS c_domain, c.industry AS c_industry, "
                  f"c.employees AS c_employees, c.hq_city AS c_hq_city, c.signal_score AS c_signal "
                  f"FROM pdb_people p{join}{where} ORDER BY {order} LIMIT ? OFFSET ?", [*params, size, (page - 1) * size])
    return {"total": total, "page": page, "size": size, "sort": sort if sort in PERSON_SORTS else "relevance",
            "results": person_rows(store, rows), "took_ms": round((time.perf_counter() - started) * 1000, 1), "filters": f}


def search_companies(store, filters=None, sort="relevance", page=1, size=25) -> dict:
    started = time.perf_counter()
    repo, f = repo_for(store), normalise(filters)
    page, size = _page(page, size)
    where, params = [], []
    if not f["include_suppressed"]:
        where.append("c.suppressed = 0")
    if f["q"]:
        sql, vals = repo.text_match("companies", "c", "search_all", [f["q"]], all_words=True)
        if sql:
            where.append(sql)
            params += vals
    if f["updated_days"]:
        where.append("c.last_seen >= ?")
        params.append(_days_ago(f["updated_days"]))
    if f["source"]:
        where.append("(" + " OR ".join("c.source_text LIKE ?" for _ in f["source"]) + ")")
        params += [f"%|{s}|%" for s in f["source"]]
    if f["not_in_crm"]:
        ws_sql, ws_vals = _workspace_clause()
        where.append(f"NOT EXISTS (SELECT 1 FROM pdb_crm_links l WHERE l.company_id = c.id{ws_sql})")
        params += ws_vals
    _location(f, where, params, person=False)
    company_where(repo, f, where, params)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    order = COMPANY_SORTS.get(sort or "relevance", COMPANY_SORTS["relevance"])
    total = repo.scalar(f"SELECT COUNT(*) FROM pdb_companies c{clause}", params) or 0
    rows = repo.q(f"SELECT c.id, c.domain, c.name, c.industry, c.employees, c.revenue_range, c.founded, c.hq_city, c.hq_state, c.hq_country, "
                  f"c.signal_score, c.last_signal_at, c.people_count, c.source_text, c.suppressed, c.last_seen, c.last_verified, c.tech_text "
                  f"FROM pdb_companies c{clause} ORDER BY {order} LIMIT ? OFFSET ?", [*params, size, (page - 1) * size])
    dis = _disabled(store)
    for r in rows:
        r["sources"] = [s for s in (r.pop("source_text") or "").strip("|").split("|") if s and s not in dis]
        r["technologies"] = [t for t in (r.pop("tech_text") or "").strip("|").split("|") if t][:8]
        r["suppressed"] = bool(r["suppressed"])
    return {"total": total, "page": page, "size": size, "sort": sort if sort in COMPANY_SORTS else "relevance", "results": rows,
            "took_ms": round((time.perf_counter() - started) * 1000, 1), "filters": f}


def matching_person_ids(store, filters, limit: int = 5000) -> list:
    """Ids of every person matching the filters (for bulk add / export of a whole search), best first."""
    repo, f = repo_for(store), normalise(filters)
    where, params, _ = people_query(repo, f)
    return [r["id"] for r in repo.q(f"SELECT p.id FROM pdb_people p LEFT JOIN pdb_companies c ON c.id = p.company_id{where} "
                                    f"ORDER BY p.rank_score DESC LIMIT ?", [*params, limit])]


def facets(store, max_age: float = 60.0) -> dict:
    """Values to pick from in the filter panel (top industries, places, technologies, sources), cached for a minute."""
    repo = repo_for(store)
    key = id(repo)
    hit = _FACETS.get(key)
    if hit and time.monotonic() - hit[0] < max_age:
        return hit[1]

    def top(sql, n=40):
        return [{"value": r["v"], "count": r["n"]} for r in repo.q(sql + f" ORDER BY n DESC LIMIT {n}") if r["v"]]
    techs, sources = {}, {}
    for r in repo.q("SELECT tech_text, source_text FROM pdb_companies ORDER BY last_seen DESC LIMIT 20000"):
        for t in (r["tech_text"] or "").strip("|").split("|"):
            if t:
                techs[t] = techs.get(t, 0) + 1
        for s in (r["source_text"] or "").strip("|").split("|"):
            if s:
                sources[s] = sources.get(s, 0) + 1
    out = {"industries": top("SELECT industry AS v, COUNT(*) AS n FROM pdb_companies WHERE industry IS NOT NULL GROUP BY industry"),
           "countries": top("SELECT country AS v, COUNT(*) AS n FROM pdb_people WHERE country IS NOT NULL AND country <> '' GROUP BY country"),
           "cities": top("SELECT city AS v, COUNT(*) AS n FROM pdb_people WHERE city IS NOT NULL AND city <> '' GROUP BY city", 60),
           "states": top("SELECT state AS v, COUNT(*) AS n FROM pdb_people WHERE state IS NOT NULL AND state <> '' GROUP BY state"),
           "revenues": top("SELECT revenue_range AS v, COUNT(*) AS n FROM pdb_companies WHERE revenue_range IS NOT NULL GROUP BY revenue_range"),
           "technologies": [{"value": k, "count": v} for k, v in sorted(techs.items(), key=lambda kv: -kv[1])[:60]],
           "sources": [{"value": k, "count": v} for k, v in sorted(sources.items(), key=lambda kv: -kv[1])],
           "seniorities": list(F.SENIORITIES), "departments": list(F.DEPARTMENTS), "email_statuses": list(F.EMAIL_STATUSES),
           "signal_kinds": list(F.SIGNAL_KINDS),
           "employee_buckets": ["1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000", "5001-10000", "10001+"]}
    _FACETS[key] = (time.monotonic(), out)
    return out


def clear_facets() -> None:
    _FACETS.clear()
