"""GET /api/sources (catalogue + status + steps + terms note) and POST /api/sources/{id}/test."""
from fastapi import APIRouter, HTTPException

from . import registry, runner


def make_router(store, transports: dict) -> APIRouter:
    router = APIRouter()

    @router.get("/api/sources")
    def catalogue():
        return {"sources": registry.describe(store.read("integrations")), "capabilities": list(registry.CAPS)}

    @router.post("/api/sources/{source_id}/test")
    def test(source_id: str):
        if registry.get(source_id) is None:
            raise HTTPException(404, "Unknown source")
        return runner.test_source(store, source_id, transports)

    return router
