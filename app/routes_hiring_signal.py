"""Hiring now and company facts from a lead's own website (app/hiring_signal.py).

POST /api/leads/{lead_id}/hiring-signal   one lead, on the request (callers may: free public pages, like company health)
POST /api/hiring-signal/run               a bounded batch (at most 50 domains) in the background: owners only
GET  /api/hiring-signal/run               the batch's progress: owners only
GET  /api/recrawl                         the monthly re-crawl switch, days and last run (app/recrawl.py): owners only
PUT  /api/recrawl                         {enabled, days}: owners only
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, hiring_signal as hs, recrawl, workspaces

BACKGROUND = True                  # tests set False: the batch then runs inside the request


class BatchBody(BaseModel):
    limit: int = Field(default=hs.BATCH_MAX, ge=1, le=hs.BATCH_MAX)


class RecrawlBody(BaseModel):
    enabled: bool
    days: int = Field(default=recrawl.DEFAULT_DAYS, ge=recrawl.MIN_DAYS, le=recrawl.MAX_DAYS)


def make_router(store, fetch) -> APIRouter:
    router = APIRouter()
    batch = hs.Batch(store, fetch)

    @router.post("/api/leads/{lead_id}/hiring-signal")
    def refresh_one(lead_id: str):
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None or not workspaces.filter_rows([lead]):
            raise HTTPException(404, "Lead not found")
        try:
            return hs.refresh_lead(store, lead, fetch)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post("/api/hiring-signal/run")
    def run(body: BatchBody, request: Request):
        work = hs.pick(workspaces.filter_rows(store.read("leads"), owned=False), store, body.limit)
        if not work:
            return {"status": "done", "done": 0, "total": 0, "hiring": 0, "filled": 0, "errors": 0,
                    "note": "Every lead with a website was checked in the last 7 days."}
        try:
            state = batch.start(work, background=BACKGROUND)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        audit.record(store, getattr(request.state, "user", None), "hiring_signal.run", "hiring now from career pages",
                     f"{len(work)} company website(s)")
        return state

    @router.get("/api/hiring-signal/run")
    def progress():
        return dict(batch.state)

    def recrawl_view() -> dict:
        return {**recrawl.settings_of(store), "batch": recrawl.BATCH, "last_run": recrawl.status_of(store)}

    @router.get("/api/recrawl")
    def get_recrawl():
        return recrawl_view()

    @router.put("/api/recrawl")
    def put_recrawl(body: RecrawlBody, request: Request):
        store.update("settings", lambda s: {**s, recrawl.SETTINGS_KEY: {"enabled": body.enabled, "days": body.days}})
        audit.record(store, getattr(request.state, "user", None), "recrawl.settings", "monthly website re-check",
                     f"{'on' if body.enabled else 'off'}; every {body.days} days")
        return recrawl_view()

    return router
