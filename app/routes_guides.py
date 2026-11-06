"""The Help centre: read-only setup guides (no secrets, same for every user)."""
from fastapi import APIRouter

from . import guides


def make_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/guides")
    def all_guides():
        return guides.payload()

    return router
