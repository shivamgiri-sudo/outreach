"""Data sources: compare lead-data providers on the same leads, keep the best results, import scraper output."""
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import providers as data_sources
from .http_util import post_json

MAX_SAMPLE = 100
MAX_PROVIDERS = 10


class CompareBody(BaseModel):
    providers: list[str] = Field(min_length=1, max_length=MAX_PROVIDERS)
    sample_size: int = Field(default=data_sources.DEFAULT_SAMPLE, ge=1, le=MAX_SAMPLE)


class ApplyBody(BaseModel):
    run_id: str = Field(min_length=1, max_length=40)
    provider: str = Field(min_length=1, max_length=40)


class ImportBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    payload: Any = Field(alias="json")


def make_router(store, fetcher, transport=post_json, get_transport=data_sources.get_json) -> APIRouter:
    router = APIRouter()
    transports = {"fetch": fetcher, "post": transport, "get": get_transport}

    @router.get("/api/providers")
    def list_providers():
        return data_sources.describe_providers(store)

    @router.post("/api/providers/compare")
    def compare(body: CompareBody):
        try:
            return data_sources.compare(store, body.providers, body.sample_size, transports)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/api/providers/runs")
    def runs():
        return data_sources.list_runs(store)

    @router.post("/api/providers/apply")
    def apply(body: ApplyBody):
        if body.provider not in data_sources.PROVIDERS:
            raise HTTPException(400, f"Unknown provider: {body.provider}")
        try:
            return data_sources.apply(store, body.run_id, body.provider)
        except data_sources.RunNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/api/providers/import-contact-scraper")
    def import_contact_scraper(body: ImportBody):
        if not isinstance(body.payload, (str, list, dict)) or body.payload in ("", [], {}):
            raise HTTPException(400, "Paste or upload the scraper's JSON output.")
        try:
            return data_sources.import_contact_scraper_json(store, body.payload)
        except ValueError as exc:
            raise HTTPException(400, f"Could not read that JSON: {exc}") from exc

    return router
