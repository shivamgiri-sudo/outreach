"""Smart import (preview, commit, history, undo) and data hygiene (report, merge, standardise, resolve conflicts)."""
import re
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import workspaces
from . import audit, landline_restore, lead_fields
from .header_map import FIELDS, detect_format, loose_header, map_columns
from .hygiene_report import apply_standardise, report, standardise_changes
from .lead_import import fingerprint, read_table, run_import, trim_lists
from .hygiene import is_role_address
from .lead_merge import merge_leads
from .lead_model import event, now_iso
from .privacy import hashes_for
from .routes_exports import require_owner
from .standardise import email_alias_key

MAX_CSV_CHARS = 15_000_000
HISTORY_KEPT = 100
REPOINTED = ("outbox", "inbox", "tasks", "whatsapp_outbox", "activities", "transactional_outbox", "notifications",
             "consent_ledger")          # so the kept lead's DPDP export and erasure cover the merged lead's rows too
UNDO_NOTE = ("Undo removes only the leads this import created. Details it added to leads you already had were left in "
             "place, because other changes may depend on them.")


def _not_again(store, table: list) -> set:
    """Suppressed emails plus any email in this file whose person was erased, so the dry run shows the row as left out
    (the store's guard drops it on save anyway)."""
    erased = set(store.read("suppressed_hashes"))
    cells = (c for _, r in table for c in (r.values() if isinstance(r, dict) else r) if isinstance(c, str) and "@" in c)
    found = {e for c in cells for e in re.split(r"[\s,;/]+", c.strip().lower()) if erased and hashes_for(e) & erased}
    return set(store.read("suppressed")) | found


class ImportOptions(BaseModel):
    skip_role_addresses: bool = False
    skip_free_mail: bool = False


class ImportBody(BaseModel):
    csv: Optional[str] = Field(default=None, max_length=MAX_CSV_CHARS)
    rows: Optional[list] = None
    source: str = Field(default="", max_length=80)
    mapping: dict = {}
    options: ImportOptions = ImportOptions()
    remember_mapping: bool = True


class MergeBody(BaseModel):
    keep_id: str = Field(min_length=1, max_length=40)
    merge_ids: list = Field(min_length=1, max_length=50)


class StandardiseBody(BaseModel):
    dry_run: bool = True
    lead_ids: Optional[list] = None


class ResolveBody(BaseModel):
    lead_id: str
    conflict_id: str
    choice: str = Field(pattern="^(keep|use_new)$")


def _source_name(raw: str, fallback: str) -> str:
    text = re.sub(r"[^\w .:\-/()&+]", "", raw or "").strip()[:60]
    return text or fallback or "import"


def _collection(store, key: str) -> list:
    try:
        return store.read(key)
    except KeyError:
        store.write(key, [])
        return []


def make_router(store) -> APIRouter:
    router = APIRouter()

    def prepare(body: ImportBody) -> dict:
        if not (body.csv or "").strip() and not body.rows:
            raise HTTPException(400, "Paste a CSV or upload a file first.")
        if body.rows is not None and len(body.rows) > 20000:
            raise HTTPException(400, "Import at most 20000 rows at a time.")
        custom_fields = lead_fields.list_fields(store)
        custom_choices = {f"custom:{f['key']}" for f in custom_fields}
        bad = [f"{k} -> {v}" for k, v in body.mapping.items() if v != "ignore" and v not in FIELDS and v not in custom_choices]
        if bad:
            raise HTTPException(400, "Unknown field in the column choices: " + ", ".join(map(str, bad[:5])))
        try:
            headers, table = read_table(None if body.rows else body.csv, body.rows or None)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        fmt = detect_format(headers)
        saved = (store.read("settings").get("import_mappings") or {}).get(fmt["key"]) or {}
        overrides = {k: v for k, v in body.mapping.items() if k in headers}
        columns = map_columns(headers, [r for _, r in table[:25]], fmt, saved, overrides, custom_fields)
        return {"headers": headers, "table": table, "format": {**fmt, "saved_mapping": bool(saved)}, "custom_fields": custom_fields,
                "columns": columns, "source": _source_name(body.source, fmt["source"]),
                "options": body.options.model_dump()}

    def answer(ctx: dict, result: dict) -> dict:
        columns = ctx["columns"]
        return {"format": ctx["format"], "source": ctx["source"], "columns": columns,
                "mapping": {c["column"]: c["field"] or "ignore" for c in columns},
                "unmapped": [c["column"] for c in columns if not c["field"]],
                "fields": {**FIELDS, **{f"custom:{f['key']}": f"{f['label']} (custom)" for f in ctx["custom_fields"]}},
                "rows": result["sample"], **{k: v for k, v in trim_lists(result).items()
                                             if k not in ("sample", "created_ids", "merged_ids")}}

    @router.post("/api/import/excel")
    async def excel(request: Request, filename: str = Query("", max_length=200), sheet: str = Query("", max_length=120)):
        """An Excel file (raw body) as CSV text for the import screens; nothing is saved."""
        from .excel_import import MAX_BYTES, ExcelError, to_csv
        if int(request.headers.get("content-length") or 0) > MAX_BYTES:
            raise HTTPException(413, f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
        data = await request.body()
        if len(data) > MAX_BYTES:
            raise HTTPException(413, f"The file is larger than {MAX_BYTES // (1024 * 1024)} MB.")
        try:
            out = to_csv(data, filename, sheet)
        except ExcelError as exc:
            raise HTTPException(400, str(exc)) from None
        if len(out["csv"]) > MAX_CSV_CHARS:
            raise HTTPException(400, "This sheet is too large to import in one go. Split it into smaller files.")
        return out

    @router.post("/api/import/preview")
    def preview(body: ImportBody):
        """Dry run: what would happen, with nothing saved."""
        ctx = prepare(body)
        result = run_import(store.read("leads"), ctx["table"], ctx["columns"], ctx["source"],
                            _not_again(store, ctx["table"]), ctx["options"], custom_fields=ctx["custom_fields"])
        return {**answer(ctx, result), "dry_run": True}

    @router.post("/api/import/commit")
    def commit(body: ImportBody):
        ctx = prepare(body)
        import_id = uuid.uuid4().hex[:10]
        suppressed = _not_again(store, ctx["table"])
        holder = {}

        def apply(leads: list) -> list:
            holder["result"] = run_import(leads, ctx["table"], ctx["columns"], ctx["source"], suppressed, ctx["options"],
                                          import_id=import_id, custom_fields=ctx["custom_fields"])
            created = set(holder["result"]["created_ids"])
            holder["fingerprints"] = {lead["id"]: fingerprint(lead) for lead in leads if lead["id"] in created}
            return leads
        store.update("leads", apply)
        result = holder["result"]
        if result["source_opt_outs"]:          # a "no" recorded in the source CRM goes on the do-not-contact list
            store.update("suppressed", lambda rows: [*rows, *(e for e in result["source_opt_outs"] if e not in rows)])
        entry = {"id": import_id, "at": now_iso(), "source": ctx["source"], "format": ctx["format"]["name"],
                 "summary": result["summary"], "created": holder["fingerprints"], "merged_ids": result["merged_ids"],
                 "workspace": workspaces.for_new_lead()}
        _collection(store, "imports")
        store.update("imports", lambda rows: [*rows, entry][-HISTORY_KEPT:])
        if body.remember_mapping:
            mapping = {loose_header(c["column"]): c["field"] or "ignore" for c in ctx["columns"]}
            store.update("settings", lambda s: {**s, "import_mappings": {**(s.get("import_mappings") or {}),
                                                                         ctx["format"]["key"]: mapping}})
        return {**answer(ctx, result), "dry_run": False, "import_id": import_id,
                "created_ids": result["created_ids"], "merged_ids": result["merged_ids"]}

    @router.get("/api/import/history")
    def history():
        rows = workspaces.filter_rows(_collection(store, "imports"))       # each workspace sees its own imports
        return [{"id": r["id"], "at": r["at"], "source": r["source"], "format": r["format"], "summary": r["summary"],
                 "created": len(r.get("created") or {}), "merged": len(r.get("merged_ids") or []),
                 "undone_at": r.get("undone_at"), "undo": r.get("undo")} for r in reversed(rows)]

    @router.post("/api/import/{import_id}/undo")
    def undo(import_id: str):
        entry = next((r for r in _collection(store, "imports") if r["id"] == import_id), None)
        if entry is None or not workspaces.visible(entry):
            raise HTTPException(404, "Import not found")
        if entry.get("undone_at"):
            raise HTTPException(400, "This import was already undone.")
        contacted = {m.get("lead_id") for key in ("outbox", "whatsapp_outbox") for m in store.read(key)}
        kept, removed = [], []

        def apply(leads: list) -> list:
            kept.clear()
            removed.clear()
            by_id = {lead["id"]: lead for lead in leads}
            for lead_id, print_ in (entry.get("created") or {}).items():
                lead = by_id.get(lead_id)
                reason = ("no longer here (merged into another lead or deleted)" if lead is None else
                          "already contacted" if lead_id in contacted else
                          "changed since the import (a note, stage, enrichment or later merge)"
                          if fingerprint(lead) != print_ else None)
                if reason:
                    kept.append({"lead_id": lead_id, "company": (lead or {}).get("company"), "reason": reason})
                else:
                    removed.append(lead_id)
            return [lead for lead in leads if lead["id"] not in set(removed)]
        store.update("leads", apply)
        outcome = {"removed": len(removed), "kept": kept, "merged_leads_left": len(entry.get("merged_ids") or []),
                   "note": UNDO_NOTE}
        store.update("imports", lambda rows: [{**r, "undone_at": now_iso(), "undo": {"removed": len(removed),
                                                                                     "kept": len(kept)}}
                                              if r["id"] == import_id else r for r in rows])
        return outcome

    # ---------------------------------------------------------------- hygiene
    @router.get("/api/hygiene/report")
    def hygiene_report():
        return report(store.read("leads"), set(store.read("suppressed")))

    @router.post("/api/hygiene/merge")
    def merge(body: MergeBody):
        ids = [str(i) for i in body.merge_ids]
        if body.keep_id in ids or len(set(ids)) != len(ids):
            raise HTTPException(400, "Pick one lead to keep and different leads to merge into it.")
        result = {}

        def apply(leads: list) -> list:
            by_id = {lead["id"]: lead for lead in leads}
            missing = [i for i in [body.keep_id, *ids] if i not in by_id]
            if missing:
                result["missing"] = missing
                return leads
            taken = {lead.get("dedupe_key") for lead in leads if lead["id"] not in ids and lead["id"] != body.keep_id}
            merged = merge_leads(by_id[body.keep_id], [by_id[i] for i in ids], now_iso(), taken)
            result["lead"] = merged
            return [merged if lead["id"] == body.keep_id else lead for lead in leads if lead["id"] not in ids]
        store.update("leads", apply)
        if result.get("missing"):
            raise HTTPException(404, "Lead not found: " + ", ".join(result["missing"]))
        moved = {}
        for key in REPOINTED:
            count = {"n": 0}

            def repoint(rows: list, count=count) -> list:
                out = []
                for row in rows:
                    if isinstance(row, dict) and row.get("lead_id") in ids:
                        row = {**row, "lead_id": body.keep_id}
                        count["n"] += 1
                    out.append(row)
                return out
            store.update(key, repoint)
            moved[key] = count["n"]
        return {"lead": result["lead"], "merged_ids": ids, "repointed": moved}

    @router.post("/api/hygiene/standardise")
    def standardise(body: StandardiseBody):
        wanted = set(map(str, body.lead_ids)) if body.lead_ids else None
        found = []

        def collect(leads: list) -> list:
            found.clear()
            out = []
            for lead in leads:
                changes = standardise_changes(lead) if wanted is None or lead["id"] in wanted else {}
                if changes:
                    found.append({"lead_id": lead["id"], "company": lead.get("company") or lead.get("domain"),
                                  "fields": {f: {"from": lead.get(f), "to": v} for f, v in changes.items()}})
                    lead = apply_standardise(lead, changes) if not body.dry_run else lead
                out.append(lead)
            return out
        if body.dry_run:
            collect(store.read("leads"))
        else:
            store.update("leads", collect)
        return {"dry_run": body.dry_run, "changed": len(found), "changes": found[:200]}

    @router.get("/api/hygiene/restore-landlines")
    def restore_landlines_plan(request: Request):
        """Owner only. Dry run: leads with an empty phone whose own data holds a landline with STD code."""
        require_owner(request)
        return {**landline_restore.plan(store), "last_run": landline_restore.last_run(store)}

    @router.post("/api/hygiene/restore-landlines")
    def restore_landlines_apply(request: Request):
        require_owner(request)
        user = getattr(request.state, "user", None)
        result = landline_restore.apply(store, audit.actor_of(user)["name"])
        audit.record(store, user, "hygiene.restore_landlines", f"{result['changed']} lead(s)",
                     f"run {result['run_id']}; empty phones filled with a landline from the lead's own data")
        return {**result, "last_run": landline_restore.last_run(store)}

    @router.post("/api/hygiene/restore-landlines/{run_id}/undo")
    def restore_landlines_undo(run_id: str, request: Request):
        require_owner(request)
        user = getattr(request.state, "user", None)
        try:
            result = landline_restore.undo(store, run_id[:20], audit.actor_of(user)["name"])
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        audit.record(store, user, "hygiene.restore_landlines_undo", f"{result['reverted']} lead(s)",
                     f"run {run_id[:20]}; {result['kept']} left as they were (changed since)")
        return {**result, "last_run": landline_restore.last_run(store)}

    @router.post("/api/hygiene/resolve-conflict")
    def resolve(body: ResolveBody):
        suppressed = {email_alias_key(e) for e in store.read("suppressed")}
        result = {}

        def apply(leads: list) -> list:
            taken = {lead.get("dedupe_key") for lead in leads}
            out = []
            for lead in leads:
                if lead["id"] == body.lead_id:
                    result["found"] = True
                    lead = _resolve(lead, body, suppressed, taken, result)
                out.append(lead)
            return out
        store.update("leads", apply)
        if not result.get("found"):
            raise HTTPException(404, "Lead not found")
        if result.get("error"):
            raise HTTPException(400, result["error"])
        return result["lead"]

    return router


def _resolve(lead: dict, body: ResolveBody, suppressed: set, taken: set, result: dict) -> dict:
    conflict = next((c for c in lead.get("merge_conflicts") or [] if c.get("id") == body.conflict_id), None)
    if conflict is None:
        result["error"] = "That difference was already resolved."
        return lead
    field, kept, incoming = conflict["field"], conflict["kept"], conflict["incoming"]
    rest = [c for c in lead["merge_conflicts"] if c.get("id") != body.conflict_id]
    new = {**lead, "merge_conflicts": rest}
    if body.choice == "use_new":
        if field == "email" and email_alias_key(incoming) in suppressed:
            result["error"] = f"{incoming} is on the do-not-contact list, so it can't become the main email."
            return lead
        new[field] = incoming
        new["field_sources"] = {**(lead.get("field_sources") or {}), field: conflict.get("source", "")}
        if field == "email":
            new["email_is_role"] = is_role_address(incoming)
            new["emails"] = list(dict.fromkeys([incoming, *(lead.get("emails") or []), kept]))
            if lead.get("dedupe_key") == f"email:{kept}" and f"email:{incoming}" not in taken:
                new["dedupe_key"] = f"email:{incoming}"
        text = f"Chose the new {field} from {conflict.get('source')}: {incoming} (was {kept})"
    else:
        text = f"Kept the current {field}: {kept} (ignored {incoming} from {conflict.get('source')})"
    new["timeline"] = [*(lead.get("timeline") or []), event("conflict", text)]
    result["lead"] = new
    return new
