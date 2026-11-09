"""A small MCP server (JSON-RPC 2.0 over one POST address) so Claude or another MCP client can read this app.

Tools are read-only except `add_lead_note`. Nothing here can send a message, change a stage, start a sequence or
change settings, so a leaked token cannot do harm beyond reading and leaving notes. Authenticated by the same pairing
token as the Chrome assistant (a "caller" at most, never an owner). Cloud LinkedIn slot tokens are refused (routes_mcp),
and every tool sees only the leads in the request's workspace scope (workspaces.visible), like the web pages.
"""
import json
from typing import Optional

from . import signals
from .lead_model import event
from .reply_inbox import build_inbox
from .scoring import score_lead

PROTOCOL = "2025-03-26"
SERVER = {"name": "mcn-outreach", "version": "1.0"}
MAX_LIMIT = 50
NOTE_MAX = 1000

TOOLS = [
    {"name": "search_leads", "description": "Find leads by words in company, contact, title or city, optionally by stage. Returns a short list.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "stage": {"type": "string"},
                                                       "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT}}}},
    {"name": "get_lead", "description": "Everything known about one lead, including its latest timeline.",
     "inputSchema": {"type": "object", "properties": {"lead_id": {"type": "string"}}, "required": ["lead_id"]}},
    {"name": "reply_inbox", "description": "Replies from all channels, scored 0-100, best first. only_action keeps the ones that need a person.",
     "inputSchema": {"type": "object", "properties": {"only_action": {"type": "boolean"}, "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT}}}},
    {"name": "list_signals", "description": "Fresh public-news articles about the watched keywords, competitors and companies.",
     "inputSchema": {"type": "object", "properties": {"status": {"type": "string", "enum": ["new", "added", "dismissed"]}}}},
    {"name": "pipeline_summary", "description": "Counts of leads by stage, how many replies need a person, and how many signals are new.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "add_lead_note", "description": "Leave a short note on a lead's timeline. This is the only thing this server can write.",
     "inputSchema": {"type": "object", "properties": {"lead_id": {"type": "string"}, "note": {"type": "string", "maxLength": NOTE_MAX}},
                     "required": ["lead_id", "note"]}},
]


class ToolError(Exception):
    pass


def _brief(lead: dict) -> dict:
    scored = score_lead({**lead, "consent_yes": bool(lead.get("consent"))})
    return {"id": lead["id"], "company": lead.get("company"), "contact": lead.get("contact_name"), "title": lead.get("title"),
            "stage": lead.get("stage"), "city": lead.get("city"), "score": scored["score"], "band": scored["band"],
            "has_email": bool(lead.get("email")), "source": lead.get("source")}


def _limit(args: dict, default: int = 20) -> int:
    try:
        return max(1, min(int(args.get("limit") or default), MAX_LIMIT))
    except (TypeError, ValueError):
        raise ToolError("limit must be a whole number")


def call_tool(store, name: str, args: dict):
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    from . import workspaces
    leads = workspaces.filter_rows(store.read("leads"))          # a limited login's token sees only its workspaces
    if name == "search_leads":
        words = str(args.get("query") or "").lower().split()
        stage = str(args.get("stage") or "")
        hits = []
        for lead in leads:
            hay = " ".join(str(lead.get(k) or "") for k in ("company", "contact_name", "title", "city", "email")).lower()
            if all(w in hay for w in words) and (not stage or lead.get("stage") == stage):
                hits.append(_brief(lead))
        hits.sort(key=lambda r: -r["score"])
        return {"count": len(hits), "leads": hits[:_limit(args)]}
    if name == "get_lead":
        lead = next((l for l in leads if l["id"] == str(args.get("lead_id") or "")), None)
        if lead is None:
            raise ToolError("no such lead")
        keep = ("id", "company", "contact_name", "title", "email", "phone", "website", "city", "country", "stage", "source", "open_roles",
                "company_size", "tags", "icp", "email_check", "created_at")
        return {**{k: lead.get(k) for k in keep if lead.get(k) not in (None, "", [])}, "score": _brief(lead)["score"],
                "timeline": (lead.get("timeline") or [])[-15:]}
    if name == "reply_inbox":
        data = build_inbox(leads, only_action=bool(args.get("only_action")), limit=_limit(args))
        return {"needs_action": data["needs_action"], "total": data["total"], "replies": data["replies"]}
    if name == "list_signals":
        status = str(args.get("status") or "")
        return {"articles": signals.items(store, status if status in ("new", "added", "dismissed") else "")[:MAX_LIMIT]}
    if name == "pipeline_summary":
        by_stage = {}
        for lead in leads:
            by_stage[lead.get("stage")] = by_stage.get(lead.get("stage"), 0) + 1
        return {"leads": len(leads), "by_stage": by_stage, "replies_needing_a_person": build_inbox(leads, limit=1)["needs_action"],
                "new_signals": len(signals.items(store, "new"))}
    if name == "add_lead_note":
        note = " ".join(str(args.get("note") or "").split())
        if not note:
            raise ToolError("the note is empty")
        if len(note) > NOTE_MAX:
            raise ToolError(f"keep the note under {NOTE_MAX} characters")
        if not any(l["id"] == str(args.get("lead_id") or "") for l in leads):
            raise ToolError("no such lead")
        saved = store.update_lead(str(args.get("lead_id") or ""), lambda l: {**l, "timeline": [*l["timeline"], event("note", f"[via MCP] {note}")]})
        if saved is None:
            raise ToolError("no such lead")
        return {"ok": True}
    raise ToolError(f"unknown tool {name}")


def _error(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(store, req) -> Optional[dict]:
    """One JSON-RPC request -> its response, or None for a notification (no answer is sent)."""
    if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str):
        return _error(None, -32600, "Invalid request")
    rid, method, params = req.get("id"), req["method"], req.get("params") or {}
    if "id" not in req:
        return None
    if method == "initialize":
        asked = params.get("protocolVersion") if isinstance(params, dict) else None
        return {"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": asked or PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": SERVER,
                                                        "instructions": "Read-only view of the MCN Outreach app. Replies are scored 0-100."}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _error(rid, -32602, "tools/call needs a tool name")
        try:
            result = call_tool(store, params["name"], params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}}
        except ToolError as exc:
            return {"jsonrpc": "2.0", "id": rid, "result": {"isError": True, "content": [{"type": "text", "text": str(exc)}]}}
    return _error(rid, -32601, f"Unknown method {method}")
