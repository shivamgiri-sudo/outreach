"""Prospect database API (Apollo-style search). Signed-in users read; callers add people to leads; the owner exports,
imports, suppresses, erases, edits and chooses which sources may be stored (see routes_auth.OWNER_ROUTES).

GET    /api/prospects/stats | /facets | /people | /companies | /people/{id} | /companies/{key}
POST   /api/prospects/add-to-leads        {person_ids | filters, list_id?, list_name?, sequence?: bool}
GET/POST/DELETE /api/prospects/saved-searches[/{id}]    per signed-in user
POST   /api/prospects/export              {person_ids | filters} -> CSV (owner, audited, suppressed never included)
POST   /api/prospects/import              {csv, source}
POST   /api/prospects/suppress            {value, reason}
GET    /api/prospects/suppressions        the prospect opt-out list (owner)
POST   /api/prospects/unsuppress          {value} (owner, audited; refused while on a global do-not-contact/unsubscribe/erased list)
DELETE /api/prospects/people/{id}         erasure
PATCH  /api/prospects/people/{id}         manual edit (wins over every source)
POST   /api/prospects/sync-crm            {force}
GET/PUT /api/prospects/settings           {disabled_sources, freshness_days, purge}
"""
import csv
import io
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import audit, lists, prospect_db as P, prospect_ingest as ingest, prospect_search as S, prospect_waterfall as W
from . import prospect_fields as F
from . import prospect_unsuppress as U
from .hygiene import csv_safe
from .lead_model import build_lead, now_iso
from .privacy import is_blocked
from .prospect_repo import repo_for
from .workspaces import filter_rows

MAX_ADD = 2000
MAX_EXPORT = 20000
MAX_SAVED = 50
EXPORT_COLUMNS = ("full_name", "title", "seniority", "department", "email", "email_status", "email_confidence", "phone", "linkedin_url",
                  "city", "state", "country", "company", "company_domain", "industry", "employees", "sources", "last_seen", "last_verified")
EDITABLE = ("full_name", "title", "email", "linkedin_url", "city", "state", "country", "seniority", "department")


class AddBody(BaseModel):
    person_ids: List[str] = []
    filters: Optional[dict] = None
    list_id: str = ""
    list_name: str = Field("", max_length=80)
    sequence: bool = False


class SavedBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str = "people"
    filters: dict = {}


class ExportBody(BaseModel):
    person_ids: List[str] = []
    filters: Optional[dict] = None


class ImportBody(BaseModel):
    csv: str = Field(min_length=1, max_length=20_000_000)
    source: str = "csv"


class SuppressBody(BaseModel):
    value: str = Field(min_length=3, max_length=200)
    reason: str = Field("opt-out", max_length=200)


class UnsuppressBody(BaseModel):
    value: str = Field(min_length=3, max_length=200)


class EditBody(BaseModel):
    changes: dict


class SyncBody(BaseModel):
    force: bool = False


class EnrichBody(BaseModel):
    person_ids: List[str] = []


class SpendBody(BaseModel):
    monthly_budget: Optional[float] = Field(None, ge=0, le=10_000_000)
    freshness_days: int = Field(90, ge=1, le=3650)
    providers: dict = {}


class SettingsBody(BaseModel):
    disabled_sources: List[str] = []
    freshness_days: Optional[int] = Field(None, ge=1, le=3650)
    purge: bool = False


def _user(request: Request) -> dict:
    return getattr(request.state, "user", None) or {"sub": "local", "role": "owner", "name": "This computer", "email": ""}


def _owner_key(request: Request) -> str:
    u = _user(request)
    return str(u.get("email") or u.get("sub") or "local")[:120]


def _filters_from_query(request: Request) -> tuple:
    qp = request.query_params
    raw = {k: (qp.getlist(k) if k in S.LIST_KEYS else qp.get(k)) for k in qp.keys()}
    return raw, qp.get("sort") or "relevance", qp.get("page") or 1, qp.get("size") or 25


def blocked_reason(store, person: dict) -> str:
    if person.get("suppressed"):
        return "suppressed (opt-out)"
    email = person.get("email")
    if email and email in set(store.read("suppressed")):
        return "on the do-not-contact list"
    if is_blocked(store, email, (person.get("phones") or [None])[0]):
        return "erased on request"
    return ""


def _best_phone(phones: list) -> str:
    """A mobile first; otherwise the first number (an office landline is kept as the lead phone, for calls only)."""
    from .hygiene import normalise_phone
    return next((p for p in phones if normalise_phone(p)), phones[0] if phones else "")


def _lead_row(person: dict) -> dict:
    c = person.get("company") or {}
    return {"company": c.get("name") or "", "contact_name": person.get("full_name") or "", "title": person.get("title") or "",
            "email": person.get("email") if person.get("email_status") != "invalid" else "", "phone": _best_phone(person.get("phones") or []),
            "website": f"https://{c['domain']}" if c.get("domain") else "", "country": person.get("country") or c.get("hq_country") or "",
            "linkedin_url": person.get("linkedin_url") or "", "city": person.get("city") or c.get("hq_city") or "",
            "state": person.get("state") or "", "sector": c.get("industry") or "", "company_size": str(c.get("employees") or "")}


def add_to_leads(store, person_ids: list, by: str) -> dict:
    """New CRM leads for these people (deduped against the CRM, in the request's workspace), skipping suppressed/erased."""
    repo = repo_for(store)
    result = {"added": [], "existing": [], "skipped": []}
    new, wanted = [], {}
    by_key = {l.get("dedupe_key"): l["id"] for l in store.read("leads") if l.get("dedupe_key")}
    for pid in list(dict.fromkeys(person_ids))[:MAX_ADD]:
        person = P.get_person(store, pid)
        if person is None:
            result["skipped"].append({"id": pid, "reason": "not found"})
            continue
        why = blocked_reason(store, person)
        if why:
            result["skipped"].append({"id": pid, "name": person.get("full_name"), "reason": why})
            continue
        best_src = next((s for s in person.get("sources") or [] if s != "crm"), "crm")
        lead = build_lead(_lead_row(person), f"prospect_db:{best_src}")
        if lead is None:
            result["skipped"].append({"id": pid, "name": person.get("full_name"), "reason": "no email, phone or website"})
            continue
        if lead["dedupe_key"] in by_key:
            lead_id = by_key[lead["dedupe_key"]]
            result["existing"].append({"id": pid, "lead_id": lead_id})
            if not repo.q1("SELECT 1 AS x FROM pdb_crm_links WHERE lead_id = ?", (lead_id,)):
                ws = next((l.get("workspace") or "main" for l in store.read("leads") if l["id"] == lead_id), "main")
                repo.x("INSERT INTO pdb_crm_links (lead_id, person_id, company_id, workspace, fp, synced_at) VALUES (?, ?, ?, ?, ?, ?)",
                       (lead_id, pid, (person.get("company") or {}).get("id"), ws, "", now_iso()))
            continue
        lead["provenance"] = {f: f"prospect_db:{person['provenance'].get(pf, {}).get('entries', [{}])[0].get('source', best_src)}"
                              for f, pf in (("email", "email"), ("contact_name", "full_name"), ("title", "title")) if lead.get(f)}
        by_key[lead["dedupe_key"]] = lead["id"]
        wanted[lead["id"]] = (pid, person)
        new.append(lead)
    saved = store.add_leads(new)
    for lead in saved:
        pid, person = wanted[lead["id"]]
        result["added"].append({"id": pid, "lead_id": lead["id"]})
        repo.x("DELETE FROM pdb_crm_links WHERE lead_id = ?", (lead["id"],))
        repo.x("INSERT INTO pdb_crm_links (lead_id, person_id, company_id, workspace, fp, synced_at) VALUES (?, ?, ?, ?, ?, ?)",
               (lead["id"], pid, (person.get("company") or {}).get("id"), lead.get("workspace") or "main", "", now_iso()))
    for lead in new:
        if lead["id"] not in {l["id"] for l in saved}:
            result["skipped"].append({"id": wanted[lead["id"]][0], "reason": "erased or taken by another import"})
    return result


def make_router(store, transports=None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/prospects/stats")
    def stats():
        ingest.sync_soon(store)
        return P.stats(store)

    @router.get("/api/prospects/facets")
    def facets():
        return S.facets(store)

    @router.get("/api/prospects/people")
    def people(request: Request):
        raw, sort, page, size = _filters_from_query(request)
        return S.search_people(store, raw, sort, page, size)

    @router.post("/api/prospects/people/search")
    def people_post(body: dict):
        return S.search_people(store, body.get("filters") or {}, body.get("sort") or "relevance", body.get("page") or 1, body.get("size") or 25)

    @router.get("/api/prospects/companies")
    def companies(request: Request):
        raw, sort, page, size = _filters_from_query(request)
        return S.search_companies(store, raw, sort, page, size)

    @router.get("/api/prospects/people/{person_id}")
    def person(person_id: str):
        found = P.get_person(store, person_id)
        if found is None:
            raise HTTPException(404, "Prospect not found")
        return found

    @router.get("/api/prospects/companies/{key}")
    def company(key: str):
        found = P.get_company(store, key)
        if found is None:
            raise HTTPException(404, "Company not found")
        return found

    @router.post("/api/prospects/add-to-leads")
    def add(body: AddBody, request: Request):
        ids = body.person_ids or (S.matching_person_ids(store, body.filters, MAX_ADD) if body.filters is not None else [])
        if not ids:
            raise HTTPException(400, "Pick people first.")
        by = audit.actor_of(getattr(request.state, "user", None))["name"]
        out = add_to_leads(store, ids, by)
        lead_ids = [r["lead_id"] for r in out["added"] + out["existing"]]
        if lead_ids and (body.list_id or body.list_name.strip()):
            if body.list_id:
                row = lists.get(store, body.list_id)
                if row is None or not filter_rows([row]):
                    raise HTTPException(404, "List not found")
                lists.set_members(store, body.list_id, lead_ids, [], by)
                out["list_id"] = body.list_id
            else:
                out["list_id"] = lists.create(store, body.list_name.strip(), lead_ids, by, "prospect search")["id"]
        if body.sequence and lead_ids:
            from .routes_outreach import EnrollRefused, enroll_lead
            started, refused = 0, {}
            for lid in lead_ids:
                try:
                    enroll_lead(store, lid, note=f"Enrolled from prospect search by {by}", by=by, via="bulk action",
                                detail="prospect search")
                    started += 1
                except (EnrollRefused, LookupError) as exc:
                    refused[str(exc)] = refused.get(str(exc), 0) + 1
            out["sequence"] = {"started": started, "refused": [{"reason": k, "count": v} for k, v in refused.items()]}
        return out

    # ---- saved searches (per user)
    @router.get("/api/prospects/saved-searches")
    def saved(request: Request):
        rows = repo_for(store).q("SELECT id, name, kind, filters, created_at FROM pdb_saved_searches WHERE owner = ? ORDER BY created_at DESC",
                                 (_owner_key(request),))
        import json
        return [{**r, "filters": json.loads(r["filters"] or "{}")} for r in rows]

    @router.post("/api/prospects/saved-searches")
    def save_search(body: SavedBody, request: Request):
        import json
        repo, owner = repo_for(store), _owner_key(request)
        if (repo.scalar("SELECT COUNT(*) FROM pdb_saved_searches WHERE owner = ?", (owner,)) or 0) >= MAX_SAVED:
            raise HTTPException(400, f"At most {MAX_SAVED} saved searches. Delete one first.")
        clean = {k: v for k, v in S.normalise(body.filters).items() if v not in (None, "", [], False)}
        row = {"id": uuid.uuid4().hex[:10], "owner": owner, "name": F.text(body.name, 80), "kind": "companies" if body.kind == "companies" else "people",
               "filters": json.dumps(clean), "created_at": now_iso()}
        repo.save("pdb_saved_searches", row, new=True)
        return {**row, "filters": clean}

    @router.delete("/api/prospects/saved-searches/{search_id}")
    def delete_search(search_id: str, request: Request):
        n = repo_for(store).x("DELETE FROM pdb_saved_searches WHERE id = ? AND owner = ?", (search_id, _owner_key(request)))
        if not n:
            raise HTTPException(404, "Saved search not found")
        audit.record(store, getattr(request.state, "user", None), "prospects.search_delete", search_id)
        return {"ok": True}

    # ---- owner
    @router.post("/api/prospects/export")
    def export(body: ExportBody, request: Request):
        ids = body.person_ids or (S.matching_person_ids(store, body.filters, MAX_EXPORT) if body.filters is not None else [])
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(EXPORT_COLUMNS)
        written = left_out = 0
        for pid in ids[:MAX_EXPORT]:
            p = P.get_person(store, pid)
            if p is None:
                continue
            if blocked_reason(store, p):
                left_out += 1
                continue
            c = p.get("company") or {}
            values = {**p, "phone": (p.get("phones") or [""])[0], "company": c.get("name"), "industry": c.get("industry"),
                      "employees": c.get("employees"), "sources": " ".join(p.get("sources") or [])}
            writer.writerow([csv_safe(values.get(k)) for k in EXPORT_COLUMNS])
            written += 1
        audit.record(store, getattr(request.state, "user", None), "prospects.export", f"{written} people",
                     f"left out {left_out} suppressed/erased; " + ("picked ids" if body.person_ids else f"filters {S.normalise(body.filters or {})['q'] or 'search'}"))
        return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="prospects.csv"', "Cache-Control": "no-store",
                                 "X-Exported": str(written), "X-Left-Out": str(left_out)})

    @router.post("/api/prospects/import")
    def import_csv(body: ImportBody, request: Request):
        try:
            out = ingest.import_csv(store, body.csv, F.source_key(body.source or "csv"))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        audit.record(store, getattr(request.state, "user", None), "prospects.import", f"{out['rows']} rows", f"source {body.source}")
        return out

    @router.post("/api/prospects/suppress")
    def suppress(body: SuppressBody, request: Request):
        try:
            out = P.suppress(store, body.value, body.reason)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        audit.record(store, getattr(request.state, "user", None), "prospects.suppress", out["kind"], f"{out['people']} people")
        return out

    @router.get("/api/prospects/suppressions")
    def suppressions():
        return {"rows": U.list_suppressions(store)}

    @router.post("/api/prospects/unsuppress")
    def unsuppress(body: UnsuppressBody, request: Request):
        user = getattr(request.state, "user", None)
        try:
            out = U.unsuppress(store, body.value)
        except U.Refused as exc:
            audit.record(store, user, "prospects.unsuppress_refused", body.value.strip().lower()[:200], str(exc))
            raise HTTPException(409, str(exc)) from None
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        audit.record(store, user, "prospects.unsuppress", out["unsuppressed"],
                     f"{out['kind']}; {out['people']} people back, {out['kept']} kept suppressed (global lists)")
        return out

    @router.delete("/api/prospects/people/{person_id}")
    def erase(person_id: str, request: Request):
        if not P.delete_person(store, person_id):
            raise HTTPException(404, "Prospect not found")
        audit.record(store, getattr(request.state, "user", None), "prospects.erase", "prospect erased", "hashes kept so no source re-adds them")
        return {"ok": True, "erased": person_id}

    @router.patch("/api/prospects/people/{person_id}")
    def edit(person_id: str, body: EditBody, request: Request):
        current = P.get_person(store, person_id)
        if current is None:
            raise HTTPException(404, "Prospect not found")
        changes = {k: v for k, v in body.changes.items() if k in EDITABLE}
        if not changes:
            raise HTTPException(400, "Nothing to change. Editable: " + ", ".join(EDITABLE))
        ident = {"email": current.get("email"), "linkedin_url": current.get("linkedin_url"), "company_domain": current.get("company_domain"),
                 "full_name": current.get("full_name")}
        record = {k: v for k, v in ident.items() if v}
        record.update(changes)
        if "email" in changes:
            record["email_status"] = "unknown"
        res = P.upsert_person(store, record, "manual")
        if not res.get("id"):
            raise HTTPException(400, f"Not saved: {res.get('skipped')}")
        audit.record(store, getattr(request.state, "user", None), "prospects.edit", res["id"], ", ".join(sorted(changes)))
        return P.get_person(store, res["id"])

    @router.post("/api/prospects/sync-crm")
    def sync(body: SyncBody):
        return ingest.sync_crm(store, body.force)

    @router.get("/api/prospects/settings")
    def get_settings():
        from .sources import registry
        known = sorted({"crm", "csv", "manual", "website", "careers_page", "apollo", "google_places", *registry.BY_ID})
        return {**P.settings(store), "known_sources": known}

    @router.put("/api/prospects/settings")
    def put_settings(body: SettingsBody, request: Request):
        chosen = sorted({F.source_key(s) for s in body.disabled_sources} - {"manual"})
        before = set(P.settings(store)["disabled_sources"])
        store.update("settings", lambda s: {**s, "prospect_db": {**(s.get("prospect_db") or {}), "disabled_sources": chosen,
                                                                   **({"freshness_days": body.freshness_days} if body.freshness_days else {})}})
        purged = {src: P.purge_source(store, src, remove=body.purge) for src in sorted(set(chosen) ^ before)}
        purged = purged if body.purge else {}
        audit.record(store, getattr(request.state, "user", None), "prospects.settings", "sources",
                     f"disabled: {', '.join(chosen) or 'none'}" + (f"; purged {', '.join(purged)}" if purged else ""))
        S.clear_facets()
        return {**P.settings(store), "purged": purged}

    # ---- cost-aware email lookups (app/prospect_waterfall.py)
    @router.post("/api/prospects/enrich/estimate")
    def enrich_estimate(body: EnrichBody):
        return W.enrich_prospects(store, body.person_ids, transports or {}, dry_run=True)

    @router.post("/api/prospects/enrich")
    def enrich(body: EnrichBody, request: Request):
        if not body.person_ids:
            raise HTTPException(400, "Pick people first.")
        out = W.enrich_prospects(store, body.person_ids, transports or {}, dry_run=False)
        audit.record(store, getattr(request.state, "user", None), "prospects.enrich", f"{out.get('targets', 0)} people",
                     f"found {out.get('found', 0)}, spent {out.get('spent', 0)}")
        return out

    @router.get("/api/prospects/spend")
    def spend():
        return W.spend_report(store)

    @router.put("/api/prospects/spend")
    def put_spend(body: SpendBody, request: Request):
        known = {p["id"] for p in W.provider_config(store)}
        providers = {}
        for pid, cfg in (body.providers or {}).items():
            if pid in known and isinstance(cfg, dict):
                entry = {}
                if cfg.get("cost") not in (None, ""):
                    entry["cost"] = max(0.0, min(100000.0, float(cfg["cost"])))
                if cfg.get("priority") not in (None, ""):
                    entry["priority"] = max(0, min(99, int(cfg["priority"])))
                if "enabled" in cfg:
                    entry["enabled"] = bool(cfg["enabled"])
                providers[pid] = entry
        store.update("settings", lambda s: {**s, "prospect_db": {**(s.get("prospect_db") or {}), "monthly_budget": body.monthly_budget,
                                                                   "freshness_days": body.freshness_days, "providers": providers}})
        audit.record(store, getattr(request.state, "user", None), "prospects.spend", "lookup budget",
                     f"budget {body.monthly_budget if body.monthly_budget is not None else 'none'}, freshness {body.freshness_days} days")
        return W.spend_report(store)

    return router
