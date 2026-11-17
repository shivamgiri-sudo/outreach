"""Domain trust signals, "Search the web" links and "Ask who handles hiring" for the lead drawer.

GET  /api/leads/{lead_id}/research        domain signals + the plain-English why, search links, the ask-contact draft
                                           (seeds the built-in snippet once; never sends anything)
POST /api/leads/{lead_id}/domain-signals  check this lead's domain now (public DNS + RDAP; cached 30 days): callers may
POST /api/domain-signals/run              {limit<=500}: the owner's batch in the background
GET  /api/domain-signals/run              the batch's progress (owners)
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import audit, domain_signals as DS, lead_research as LR, workspaces

BACKGROUND = True                  # tests set False: the batch then runs inside the request


class BatchBody(BaseModel):
    limit: int = Field(default=DS.BATCH_MAX, ge=1, le=DS.BATCH_MAX)


def make_router(store, resolvers: Optional[dict] = None) -> APIRouter:
    router = APIRouter()
    res = dict(resolvers or {})
    batch = DS.Batch(store, res)

    def lead_or_404(lead_id: str) -> dict:
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None or not workspaces.filter_rows([lead]):
            raise HTTPException(404, "Lead not found")
        return lead

    @router.get("/api/leads/{lead_id}/research")
    def research(lead_id: str, request: Request):
        lead = lead_or_404(lead_id)
        user = getattr(request.state, "user", None) or {}
        signals = lead.get("domain_signals") or None
        return {"domain": lead.get("domain") or "", "domain_signals": signals, "why": DS.WHY,
                "young": DS.is_young(signals), "no_mail_auth": DS.no_mail_auth(signals),
                "links": LR.search_links(lead), "ask": LR.ask_contact(store, lead, user.get("name", ""))}

    @router.post("/api/leads/{lead_id}/domain-signals")
    def check_one(lead_id: str):
        lead = lead_or_404(lead_id)
        if not lead.get("domain"):
            raise HTTPException(400, "This lead has no company website domain to check.")
        signals = DS.refresh_domain(store, lead["domain"], **res)
        return {"domain_signals": signals if DS.known(signals) else None, "known": DS.known(signals)}

    @router.post("/api/domain-signals/run")
    def run(body: BatchBody, request: Request):
        work = DS.pick(workspaces.filter_rows(store.read("leads"), owned=False), body.limit)
        if not work:
            return {"status": "done", "done": 0, "total": 0, "young": 0, "no_mail_auth": 0, "unknown": 0,
                    "note": "Every lead with a website was checked in the last 30 days."}
        try:
            state = batch.start(work, background=BACKGROUND)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        audit.record(store, getattr(request.state, "user", None), "domain_signals.run", "domain trust signals",
                     f"{len(work)} domain(s)")
        return state

    @router.get("/api/domain-signals/run")
    def progress():
        return dict(batch.state)

    return router
