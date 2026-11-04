"""Hiring-lead engine: run it, and see how far each lead's email can be trusted."""
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel, Field

from . import hiring_engine as he


class CheckItem(BaseModel):
    email: str
    status: str


class ResultItem(BaseModel):
    lead_id: str
    checks: list[CheckItem] = []


class ApplyBody(BaseModel):
    results: list[ResultItem] = Field(default=[], max_length=500)


class RunBody(BaseModel):
    max_leads: int = Field(default=50, ge=1, le=300)
    max_credits: int = Field(default=30, ge=0, le=500)
    dry_run: bool = False
    recheck_guesses: bool = False          # also re-verify guesses that are still unconfirmed (uses paid checks)


def make_router(store, transports: dict) -> APIRouter:
    router = APIRouter()

    @router.post("/api/hiring-engine/run")
    def run(body: RunBody):
        return he.run_engine(store, transports, datetime.now(timezone.utc), body.max_leads, body.max_credits, body.dry_run, body.recheck_guesses)

    @router.get("/api/hiring-engine/candidates")
    def candidates(max_leads: int = 50):
        """Guesses waiting for a mailbox check, for a checker running on another machine (tools/probe_live.py)."""
        return he.candidates(store, max(1, min(max_leads, 500)))

    @router.post("/api/hiring-engine/apply")
    def apply(body: ApplyBody):
        return he.apply_checks(store, [r.model_dump() for r in body.results])

    @router.get("/api/hiring-engine/summary")
    def summary():
        rows = he.sheet_rows(store)
        trust = Counter(r["email_trust"] for r in rows)
        posters = Counter(r["posted_by"] for r in rows)
        return {"total": len(rows), "trust": {k: trust.get(k, 0) for k in he.TRUST_LABELS}, "labels": he.TRUST_LABELS,
                "posted_by": dict(posters), "top": rows[:25]}

    return router
