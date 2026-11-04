"""Buying-signal watchlist and article list. Owner-only for changes; reading is open to the team."""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import signals


class WatchBody(BaseModel):
    kind: str
    text: str


class StatusBody(BaseModel):
    status: str


class AddBody(BaseModel):
    company: str


def make_router(store, fetch=None, ai_transport=None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/signals")
    def read(status: str = ""):
        s = signals.state(store)
        return {"watch": s["watch"], "last_run": s["last_run"], "items": signals.items(store, status if status in ("new", "dismissed", "added") else "")}

    @router.post("/api/signals/watch")
    def add_watch(body: WatchBody):
        try:
            return signals.add_watch(store, body.kind, body.text)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.delete("/api/signals/watch/{watch_id}")
    def drop_watch(watch_id: str):
        if not signals.remove_watch(store, watch_id):
            raise HTTPException(404, "Not on the list")
        return {"removed": watch_id}

    @router.post("/api/signals/run")
    def run():
        return signals.run_watch(store, fetch or signals.default_fetch, ai_transport)

    @router.post("/api/signals/items/{item_id}/status")
    def status(item_id: str, body: StatusBody):
        try:
            ok = signals.set_status(store, item_id, body.status)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if not ok:
            raise HTTPException(404, "Article not found")
        return {"ok": True}

    @router.post("/api/signals/items/{item_id}/lead")
    def to_lead(item_id: str, body: AddBody):
        try:
            return signals.add_as_lead(store, item_id, body.company)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except LookupError as exc:
            raise HTTPException(404, str(exc))

    return router
