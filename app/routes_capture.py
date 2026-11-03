"""Owner-only management of lead-capture connectors, plus the source registry every screen reads."""
from fastapi import APIRouter, HTTPException

from . import capture_connectors as connectors
from . import source_names
from .inbound_embed import snippet_for
from .inbound_leads import remove_test_leads


def make_router(store, default_base: str = "") -> APIRouter:
    router = APIRouter()

    def view(connector: dict, reveal: bool = False) -> dict:
        out = connectors.public_view(store, connector, default_base, reveal)
        if out.get("mode") == "form":
            out["snippet"] = snippet_for(out["urls"]["embed"])
        return out

    def found(connector_id: str) -> dict:
        connector = connectors.get(store, connector_id)
        if connector is None:
            raise HTTPException(404, "Connector not found")
        return connector

    @router.get("/api/lead-sources")
    def lead_sources():
        return source_names.registry()

    @router.get("/api/capture/catalogue")
    def catalogue():
        return connectors.catalogue(store, default_base)

    @router.get("/api/capture/connectors")
    def list_connectors():
        return [view(c) for c in connectors.all_connectors(store)]

    @router.post("/api/capture/connectors")
    def add_connector(body: dict):
        try:
            return view(connectors.create(store, body or {}), reveal=True)
        except connectors.ConnectorError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/api/capture/connectors/{connector_id}")
    def one_connector(connector_id: str):
        return view(found(connector_id))

    @router.put("/api/capture/connectors/{connector_id}")
    def change_connector(connector_id: str, body: dict):
        found(connector_id)
        try:
            return view(connectors.change(store, connector_id, body or {}))
        except connectors.ConnectorError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.delete("/api/capture/connectors/{connector_id}")
    def delete_connector(connector_id: str):
        found(connector_id)
        removed = remove_test_leads(store, connector_id)
        connectors.remove(store, connector_id)
        return {"ok": True, "test_leads_removed": removed}

    @router.post("/api/capture/connectors/{connector_id}/rotate-token")
    def rotate_token(connector_id: str, body: dict = None):
        found(connector_id)
        which = str((body or {}).get("which") or "capture")
        try:
            return view(connectors.rotate(store, connector_id, which), reveal=True)
        except connectors.ConnectorError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.delete("/api/capture/connectors/{connector_id}/test-leads")
    def delete_test_leads(connector_id: str):
        found(connector_id)
        return {"removed": remove_test_leads(store, connector_id)}

    return router
