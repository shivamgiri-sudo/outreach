"""POST /api/mcp: the MCP address for Claude and other MCP clients (see mcp_server)."""
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import mcp_server

MAX_BODY = 64_000


def make_router(store) -> APIRouter:
    router = APIRouter()

    @router.post("/api/mcp")
    async def mcp(request: Request):
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return JSONResponse(mcp_server._error(None, -32600, "Request too large"), status_code=413)
        try:
            body = json.loads(raw or b"null")
        except ValueError:
            return JSONResponse(mcp_server._error(None, -32700, "Parse error"), status_code=400)
        if isinstance(body, list):                      # batches are not supported: one request per call keeps it simple
            return JSONResponse(mcp_server._error(None, -32600, "Send one request at a time"), status_code=400)
        reply = mcp_server.handle(store, body)
        return Response(status_code=202) if reply is None else JSONResponse(reply)

    return router
