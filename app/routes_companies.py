"""Companies (accounts) API: contacts grouped by employer, with rollups and a combined timeline."""
from fastapi import APIRouter, HTTPException, Query

from . import companies


def make_router(store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/companies")
    def all_companies(q: str = Query("", max_length=120), sort: str = "last_touch", order: str = "desc",
                      limit: int = Query(50, ge=1, le=companies.MAX_LIMIT), offset: int = Query(0, ge=0)):
        return companies.list_companies(store, q, sort, order, limit, offset)

    @router.get("/api/companies/of-lead/{lead_id}")
    def of_lead(lead_id: str):
        row = companies.of_lead(store, lead_id)
        if row is None:
            raise HTTPException(404, "Lead not found")
        return row

    @router.get("/api/companies/{key:path}")
    def one(key: str):
        row = companies.detail(store, key[:300])
        if row is None:
            raise HTTPException(404, "Company not found")
        return row

    return router
