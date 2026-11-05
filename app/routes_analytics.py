"""GET /api/intelligence: channel funnels, reply timing, personas, accounts, data quality, team activity."""
from fastapi import APIRouter

from . import analytics


def make_router(store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/intelligence")
    def intelligence():
        return analytics.build(store)

    return router
