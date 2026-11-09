"""Ideal-customer gate: read/save the description and threshold, preview what it would hold back, score leads with the AI."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import icp
from .http_util import post_json


class IcpBody(BaseModel):
    text: Optional[str] = None
    min_score: Optional[int] = None
    gate: Optional[bool] = None


class ScoreBody(BaseModel):
    lead_ids: Optional[list] = None
    limit: int = 30


def make_router(store, ai_transport=None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/icp")
    def read():
        return {**icp.get_icp(store), "preview": icp.preview(store)}

    @router.put("/api/icp")
    def save(body: IcpBody, request: Request):
        from . import audit_settings
        try:
            with audit_settings.watch(store, request, "Ideal customer profile"):
                icp.set_icp(store, body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {**icp.get_icp(store), "preview": icp.preview(store)}

    @router.post("/api/icp/score")
    def score(body: ScoreBody):
        try:
            return icp.score_leads(store, body.lead_ids, body.limit, ai_transport or post_json)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    return router
