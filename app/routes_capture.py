"""Owner-only management of lead-capture connectors, plus the source registry every screen reads."""
from fastapi import APIRouter, HTTPException, Request

from . import capture_connectors as connectors
from . import inbound_meta_pull, source_names
from .capture_catalogue import BY_ID
from .inbound_embed import snippet_for
from .inbound_leads import remove_test_leads


def make_router(store, default_base: str = "", graph_transport=None) -> APIRouter:
    router = APIRouter()

    def user_of(request: Request):
        return getattr(request.state, "user", None)

    def choices(request: Request) -> dict:
        """The workspaces this person may send a connector's leads into, and the Outreach users who can own them."""
        from . import auth, workspaces
        allowed = workspaces.allowed_of(user_of(request)) if user_of(request) is not None else None
        spaces = [{"id": w["id"], "name": w["name"]} for w in workspaces.all_workspaces(store) if allowed is None or w["id"] in allowed]
        owners, seen = [], set()
        for u in auth.users(store):
            value = (u.get("name") or "").strip() or u["email"]
            if value.lower() in seen:
                value = u["email"]
            seen.add(value.lower())
            owners.append({"value": value, "label": f"{u.get('name') or u['email']} ({u['email']})", "email": u["email"]})
        return {"workspaces": spaces, "owners": owners}

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
    def catalogue(request: Request):
        return {**connectors.catalogue(store, default_base), **choices(request)}

    @router.get("/api/capture/connectors")
    def list_connectors():
        return [view(c) for c in connectors.all_connectors(store)]

    @router.post("/api/capture/connectors")
    def add_connector(body: dict, request: Request):
        try:
            return view(connectors.create(store, body or {}, user_of(request)), reveal=True)
        except connectors.ConnectorError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/api/capture/connectors/{connector_id}")
    def one_connector(connector_id: str):
        return view(found(connector_id))

    @router.put("/api/capture/connectors/{connector_id}")
    def change_connector(connector_id: str, body: dict, request: Request):
        found(connector_id)
        try:
            return view(connectors.change(store, connector_id, body or {}, user_of(request)))
        except connectors.ConnectorError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.delete("/api/capture/connectors/{connector_id}")
    def delete_connector(connector_id: str, request: Request):
        row = found(connector_id)
        removed = remove_test_leads(store, connector_id)
        connectors.remove(store, connector_id)
        from . import audit
        audit.record(store, getattr(request.state, "user", None), "capture.delete", row.get("name") or connector_id,
                     f"id {connector_id}; {removed} test lead(s) removed")
        return {"ok": True, "test_leads_removed": removed}

    @router.post("/api/capture/connectors/{connector_id}/rotate-token")
    def rotate_token(connector_id: str, body: dict = None):
        found(connector_id)
        which = str((body or {}).get("which") or "capture")
        try:
            return view(connectors.rotate(store, connector_id, which), reveal=True)
        except connectors.ConnectorError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/api/capture/connectors/{connector_id}/pull")
    def pull_now(connector_id: str):
        """Pull now: read new leads of a Meta connector from the Graph API (ignores the back-off; never shows the token)."""
        connector = found(connector_id)
        if BY_ID.get(connector["type"], {}).get("mode") != "meta":
            raise HTTPException(400, "Only Facebook and Instagram Lead Ads connectors can pull leads.")
        if not connector.get("enabled", True):
            raise HTTPException(409, "This connector is switched off.")
        if not connectors.pull_configured(connector):
            raise HTTPException(400, "Paste the page access token and add the form ids (or the page id) first.")
        try:
            result = inbound_meta_pull.pull(store, connector, graph_transport, manual=True)
        except inbound_meta_pull.PullBusy as exc:
            raise HTTPException(409, str(exc)) from exc
        return {**result, "connector": view(connectors.get(store, connector_id))}

    @router.delete("/api/capture/connectors/{connector_id}/test-leads")
    def delete_test_leads(connector_id: str, request: Request):
        found(connector_id)
        removed = remove_test_leads(store, connector_id)
        from . import audit
        audit.record(store, getattr(request.state, "user", None), "capture.test_leads_delete", connector_id, f"{removed} test lead(s) removed")
        return {"removed": removed}

    return router
