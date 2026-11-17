"""The Indian company register (MCA Company Master Data, GODL): import, status, matching leads.

GET  /api/company-register                         counts per state, size, last imports, licence, job progress (owners)
POST /api/company-register/upload?filename=        one CSV file as the raw body, streamed to disk, imported in the background
GET  /api/company-register/import                  import progress
POST /api/company-register/pull                    page through the data.gov.in API with the owner's key (background)
GET  /api/company-register/pull                    pull progress
POST /api/company-register/batch                   match up to 500 leads in the background; GET: progress
POST /api/leads/{lead_id}/company-register         match one lead (callers too: a local lookup, no spend)
POST /api/leads/{lead_id}/company-register/confirm {cin}: attach this company
POST /api/leads/{lead_id}/company-register/reject  {cin}: not this company
Every /api/company-register route is owner-only (routes_auth.OWNER_PREFIXES, checked again here).
"""
import re
import shutil
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import audit, company_register as R, company_register_jobs as J, company_register_leads as L, workspaces
from .upload_owner import user_name

BACKGROUND = True                  # tests set False: imports, pulls and batches then run inside the request
NAME_RE = re.compile(r"[^A-Za-z0-9 ._()-]+")


class CinBody(BaseModel):
    cin: str = Field(min_length=6, max_length=25)


class BatchBody(BaseModel):
    limit: int = Field(default=L.BATCH_MAX, ge=1, le=L.BATCH_MAX)


class PullBody(BaseModel):
    max_pages: int = Field(default=50, ge=1, le=J.PULL_MAX_PAGES)


def _owner(request: Request) -> None:
    user = getattr(request.state, "user", None)
    if user is not None and user.get("role") != "owner":
        raise HTTPException(403, "Only an owner can manage the company register.")


def _by(request: Request) -> str:
    return user_name(getattr(request.state, "user", None)) or "Owner"


def make_router(store, transports=None) -> APIRouter:
    router = APIRouter()
    jobs = J.jobs_for(store, transports)
    batch = L.Batch(store)

    def visible_lead(lead_id: str) -> dict:
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None or not workspaces.filter_rows([lead]):
            raise HTTPException(404, "Lead not found")
        return lead

    @router.get("/api/company-register")
    def summary(request: Request):
        _owner(request)
        return {**R.summary(store), "import": jobs.view(), "pull": dict(jobs.pull_state), "batch": dict(batch.state)}

    @router.post("/api/company-register/upload")
    async def upload(request: Request, filename: str = ""):
        _owner(request)
        declared = request.headers.get("content-length") or ""
        if declared.isdigit() and int(declared) > J.MAX_UPLOAD_BYTES:
            raise HTTPException(413, "That file is larger than 1.5 GB. Upload the RoC-wise files one by one.")
        folder = J.upload_dir(store)
        folder.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(folder).free < J.MIN_FREE_BYTES + int(declared or 0):
            raise HTTPException(507, "Not enough free disk space on the server for this file.")
        name = NAME_RE.sub("", filename or "")[:120] or "register.csv"
        path = folder / f"{uuid.uuid4().hex[:12]}.csv"
        size = 0
        try:
            with open(path, "wb") as out:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > J.MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "That file is larger than 1.5 GB. Upload the RoC-wise files one by one.")
                    out.write(chunk)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        if size == 0:
            path.unlink(missing_ok=True)
            raise HTTPException(400, "The file is empty.")
        audit.record(store, getattr(request.state, "user", None), "company_register.upload", name, f"{size} bytes")
        return await run_in_threadpool(jobs.enqueue, path, name, _by(request), BACKGROUND)

    @router.get("/api/company-register/import")
    def import_progress(request: Request):
        _owner(request)
        return jobs.view()

    @router.post("/api/company-register/pull")
    def pull(body: PullBody, request: Request):
        _owner(request)
        from .sources import data_gov_in, registry
        values = registry.connection(data_gov_in.SPEC, store.read("integrations"))["values"]
        try:
            state = jobs.start_pull(values, _by(request), body.max_pages, background=BACKGROUND)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        audit.record(store, getattr(request.state, "user", None), "company_register.pull", "data.gov.in", f"up to {body.max_pages} page(s)")
        return state

    @router.get("/api/company-register/pull")
    def pull_progress(request: Request):
        _owner(request)
        return dict(jobs.pull_state)

    @router.post("/api/company-register/batch")
    def run_batch(body: BatchBody, request: Request):
        _owner(request)
        ids = L.pick(workspaces.filter_rows(store.read("leads"), owned=False), body.limit)
        if not ids:
            return {"status": "done", "done": 0, "total": 0, "attached": 0, "suggested": 0, "low": 0, "errors": 0,
                    "note": "Every lead with a company name was matched or checked in the last 30 days."}
        try:
            state = batch.start(ids, _by(request), background=BACKGROUND)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        audit.record(store, getattr(request.state, "user", None), "company_register.batch", "company register match", f"{len(ids)} lead(s)")
        return state

    @router.get("/api/company-register/batch")
    def batch_progress(request: Request):
        _owner(request)
        return dict(batch.state)

    @router.post("/api/leads/{lead_id}/company-register")
    def match_one(lead_id: str, request: Request):
        lead = visible_lead(lead_id)
        try:
            return L.check(store, lead, _by(request))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/leads/{lead_id}/company-register/confirm")
    def confirm(lead_id: str, body: CinBody, request: Request):
        visible_lead(lead_id)
        try:
            out = L.confirm(store, lead_id, body.cin, _by(request))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"registry": out["registry"]}

    @router.post("/api/leads/{lead_id}/company-register/reject")
    def reject(lead_id: str, body: CinBody, request: Request):
        visible_lead(lead_id)
        saved = L.reject(store, lead_id, body.cin, _by(request))["lead"] or {}
        return {"registry": saved.get("registry"), "registry_match": saved.get("registry_match"),
                "registry_rejected": saved.get("registry_rejected", [])}

    return router
