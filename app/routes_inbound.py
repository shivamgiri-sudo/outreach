"""Public lead-capture addresses (Meta, Google Ads, website forms, generic webhooks) and the owner's log, test and sync."""
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from . import capture_connectors as connectors
from . import inbound_auth as auth
from .growth_core import public_base_url
from .inbound_embed import script_for
from .inbound_fields import extract
from .inbound_leads import capture, capture_error, log
from .inbound_platforms import PlatformError, google_lead, graph_get, meta_events, meta_lead
from .inbound_sheet import SheetError, default_fetch, sync
from .capture_catalogue import BY_ID


def sample_payload(connector: dict) -> dict:
    """A payload shaped like the platform's own, built from the connector's field map, for the Test button."""
    values = {"name": "Test Person", "email": f"capture-test+{connector['id']}@example.com", "phone": "98100 12345",
              "company": "ZZ Test capture", "city": "Pune", "message": "This is a test lead sent from the Test button.",
              "title": "Owner", "website": "https://example.com"}
    payload = {}
    for target, path in (connector.get("field_map") or {}).items():
        value = values.get(target) or ("Test campaign" if target.startswith("attribution.") else "")
        if not value:
            continue
        node, parts = payload, str(path).split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    for target, value in values.items():
        if target not in (connector.get("field_map") or {}):
            payload.setdefault(target, value)
    if connector.get("id_path"):
        node, parts = payload, connector["id_path"].split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = "test-" + uuid.uuid4().hex[:8]
    return payload


def make_router(store, default_base: str = "", graph_transport=None, sheet_fetcher=None) -> APIRouter:
    router = APIRouter()
    graph = graph_transport or graph_get
    fetch_sheet = sheet_fetcher or default_fetch

    async def read(request: Request) -> bytes:
        if int(request.headers.get("content-length") or 0) > auth.MAX_BODY:
            raise HTTPException(413, "Too large")
        body = await request.body()
        if len(body) > auth.MAX_BODY:
            raise HTTPException(413, "Too large")
        return body

    async def authorised(request: Request, body: bytes):
        found = auth.authorize(store, request.method, request.url.path, request.headers, request.query_params, body)
        if found is None:
            raise HTTPException(401, "Bad or missing capture token.")
        connector = found[0]
        return found if connector.get("enabled", True) else _off()

    def _off():
        raise HTTPException(403, "This connector is switched off.")

    def reply(result: dict, extra_headers: dict = None) -> JSONResponse:
        outcome = result["outcome"]
        if outcome == "error":
            return JSONResponse({"ok": False, "detail": result.get("error", "Could not use this lead.")}, status_code=result.get("http", 422),
                                headers=extra_headers)
        body = {"ok": True, "status": outcome}
        if outcome in ("new", "merged", "replay") and result.get("lead_id"):
            body.update(lead_id=result["lead_id"], new=outcome == "new", merged=outcome == "merged")
        return JSONResponse(body, headers=extra_headers)

    # ---------- Meta ----------
    @router.get("/api/inbound/meta/{connector_id}")
    async def meta_verify(connector_id: str, request: Request):
        found = await authorised(request, b"")
        if found[2] != "verify":
            raise HTTPException(403, "Verification failed")
        return PlainTextResponse(request.query_params.get("hub.challenge", ""))

    @router.post("/api/inbound/meta/{connector_id}")
    async def meta_leads(connector_id: str, request: Request):
        body = await read(request)
        connector = (await authorised(request, body))[0]
        payload = auth.payload_of(request.headers.get("content-type", "application/json"), body)
        if payload is None:
            raise HTTPException(400, "The request body is not JSON.")
        tokens = connector.get("tokens") or {}
        events, failed, results = meta_events(payload), 0, []
        for event in events:
            try:
                parsed = meta_lead(event, tokens.get("page_access_token", ""), connector.get("graph_version", ""), graph)
            except PlatformError as exc:
                failed += 1
                capture_error(store, connector, str(exc), {k: str(event[k])[:60] for k in ("leadgen_id", "form_id") if event.get(k)})
                continue
            attribution = parsed["attribution"]
            detail = attribution.get("form_name") or (f"form {attribution['form_id']}" if attribution.get("form_id") else "")
            results.append(capture(store, connector, parsed, detail=detail,
                                   idem_key=request.headers.get("idempotency-key", "")))
        if failed:
            return JSONResponse({"ok": False, "detail": "Some leads could not be fetched from Meta; Meta will retry.", "leads": len(results)},
                                status_code=502)
        return {"ok": True, "leads": len(results), "results": [r["outcome"] for r in results]}

    # ---------- Google Ads ----------
    @router.post("/api/inbound/google/{connector_id}")
    async def google_lead_form(connector_id: str, request: Request):
        body = await read(request)
        connector = (await authorised(request, body))[0]
        payload = auth.payload_of(request.headers.get("content-type", "application/json"), body)
        if payload is None:
            raise HTTPException(400, "The request body is not JSON.")
        parsed = google_lead(payload)
        return reply(capture(store, connector, parsed, detail=f"form {parsed['attribution'].get('form_id', '')}".strip(),
                             is_test=parsed["is_test"], idem_key=request.headers.get("idempotency-key", "")))

    # ---------- website form script ----------
    @router.get("/api/inbound/{connector_id}/embed.js")
    async def embed(connector_id: str, request: Request):
        connector = (await authorised(request, b""))[0]
        base = public_base_url(store, default_base)
        return Response(script_for(connector, f"{base}/api/inbound/{connector['id']}"),
                        media_type="application/javascript; charset=utf-8", headers={"Access-Control-Allow-Origin": "*"})

    # ---------- generic webhook / website form ----------
    @router.post("/api/inbound/{connector_id}")
    async def capture_generic(connector_id: str, request: Request):
        body = await read(request)
        connector, _, mode = await authorised(request, body)
        payload = auth.payload_of(request.headers.get("content-type", ""), body)
        if payload is None:
            raise HTTPException(400, "Send a JSON object or a form.")
        headers = {}
        if mode == "form":
            origin = request.headers.get("origin", "")
            allowed = connector.get("allowed_origins") or []
            if allowed and origin.rstrip("/") not in allowed:
                raise HTTPException(403, "This website is not allowed to use the form.")
            if origin:
                headers = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
            if payload.get("hp") or payload.get("_hp"):
                log(store, connector, "refused", error="refused: spam trap filled")
                return JSONResponse({"ok": True, "status": "received"}, headers=headers)
            minimum = int(connector.get("min_fill_seconds") or 0)
            if minimum:
                try:
                    started = float(payload.get("_t") or 0) / 1000
                except (TypeError, ValueError):
                    started = 0
                if not started or time.time() - started < minimum:
                    log(store, connector, "refused", error="refused: form sent too fast")
                    return JSONResponse({"ok": True, "status": "received"}, headers=headers)
        parsed = extract(payload, connector.get("field_map"), connector.get("id_path", ""),
                         {"referrer": request.headers.get("referer", "")} if mode == "form" else None)
        if mode == "form":
            parsed["attribution"].setdefault("platform", "website")
        return reply(capture(store, connector, parsed, detail=parsed["attribution"].get("form_name") or connector.get("name", ""),
                             idem_key=request.headers.get("idempotency-key", "")), headers)

    # ---------- owner: log, test, sync ----------
    @router.get("/api/inbound/log")
    def capture_log(connector_id: str = "", limit: int = 100):
        rows = [r for r in store.read("capture_log") if not connector_id or r.get("connector_id") == connector_id]
        return list(reversed(rows))[:max(1, min(limit, 500))]

    @router.post("/api/inbound/{connector_id}/test")
    def send_test(connector_id: str):
        connector = connectors.get(store, connector_id)
        if connector is None:
            raise HTTPException(404, "Connector not found")
        kind = BY_ID[connector["type"]]["mode"]
        payload = sample_payload(connector)
        if kind == "google":
            payload = {"user_column_data": [{"column_id": "FULL_NAME", "string_value": "Test Person"},
                                            {"column_id": "EMAIL", "string_value": payload["email"]},
                                            {"column_id": "PHONE_NUMBER", "string_value": "98100 12345"},
                                            {"column_id": "COMPANY_NAME", "string_value": "ZZ Test capture"}],
                       "campaign_id": "test-campaign", "form_id": "test-form", "lead_id": "test-" + uuid.uuid4().hex[:8], "is_test": True}
            parsed = google_lead(payload)
        elif kind == "meta":
            payload = {"leadgen_id": "0", "form_id": "test-form", "field_data": [
                {"name": "full_name", "values": ["Test Person"]}, {"name": "email", "values": [payload["email"]]},
                {"name": "phone_number", "values": ["98100 12345"]}, {"name": "company_name", "values": ["ZZ Test capture"]}]}
            parsed = meta_lead(payload, "", "", graph)
            parsed["platform_id"] = ""
        else:
            parsed = extract(payload, connector.get("field_map"), connector.get("id_path", ""))
        result = capture(store, connector, parsed, detail="Test lead", is_test=True, whatsapp_first=kind == "whatsapp")
        lead = next((l for l in store.read("leads") if l["id"] == result.get("lead_id")), None)
        return {**result, "ok": result["outcome"] in ("new", "merged"),
                "lead": {"id": lead["id"], "company": lead.get("company"), "source_id": lead.get("source_id")} if lead else None}

    @router.post("/api/inbound/{connector_id}/sync")
    def sync_now(connector_id: str):
        connector = connectors.get(store, connector_id)
        if connector is None:
            raise HTTPException(404, "Connector not found")
        if BY_ID[connector["type"]]["mode"] != "sheet":
            raise HTTPException(400, "Only Google Sheet connectors can sync.")
        if not connector.get("enabled", True):
            raise HTTPException(409, "This connector is switched off.")
        try:
            return sync(store, connector, fetch_sheet)
        except SheetError as exc:
            capture_error(store, connector, str(exc))
            raise HTTPException(400, str(exc)) from exc

    return router


def run_sheet_syncs(store, fetch_sheet=None) -> int:
    """The background tick: sync every switched-on sheet connector. One failing sheet does not stop the others."""
    done = 0
    for connector in connectors.all_connectors(store):
        if connector.get("enabled", True) and BY_ID.get(connector["type"], {}).get("mode") == "sheet" and connector.get("sheet_url"):
            try:
                sync(store, connector, fetch_sheet or default_fetch)
                done += 1
            except SheetError as exc:
                capture_error(store, connector, str(exc))
    return done
