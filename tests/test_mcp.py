"""MCP address: JSON-RPC handshake, read-only tools, one write (a note), and the token can reach nothing else."""
import json

from tests.test_extension_api import bearer, make, pair


def rpc(c, token, method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "method": method, **({"id": rid} if rid is not None else {}), **({"params": params} if params is not None else {})}
    return c.post("/api/mcp", json=body, headers=bearer(token))


def tool(c, token, name, **args):
    r = rpc(c, token, "tools/call", {"name": name, "arguments": args}).json()["result"]
    return r, json.loads(r["content"][0]["text"]) if not r.get("isError") else r["content"][0]["text"]


def test_handshake_and_tool_list(tmp_path):
    c, owner, acme, _ = make(tmp_path)
    t = pair(c, owner)["token"]
    init = rpc(c, t, "initialize", {"protocolVersion": "2025-03-26"}).json()["result"]
    assert init["serverInfo"]["name"] == "mcn-outreach" and "tools" in init["capabilities"]
    assert rpc(c, t, "notifications/initialized", rid=None).status_code == 202
    names = {x["name"] for x in rpc(c, t, "tools/list").json()["result"]["tools"]}
    assert names == {"search_leads", "get_lead", "reply_inbox", "list_signals", "pipeline_summary", "add_lead_note"}
    assert rpc(c, t, "nope").json()["error"]["code"] == -32601
    assert rpc(c, t, "ping").json()["result"] == {}


def test_read_tools(tmp_path):
    c, owner, acme, globex = make(tmp_path)
    t = pair(c, owner)["token"]
    _, found = tool(c, t, "search_leads", query="acme")
    assert found["count"] == 1 and found["leads"][0]["id"] == acme
    _, lead = tool(c, t, "get_lead", lead_id=acme)
    assert lead["company"] == "Acme Pvt Ltd" and isinstance(lead["timeline"], list)
    err, msg = tool(c, t, "get_lead", lead_id="zzz")
    assert err["isError"] and "no such lead" in msg
    c.post(f"/api/leads/{acme}/reply", json={"text": "Yes, please call me"}, headers=bearer(owner))
    _, inbox = tool(c, t, "reply_inbox", only_action=True)
    assert inbox["needs_action"] == 1 and inbox["replies"][0]["label"] == "interested"
    _, summary = tool(c, t, "pipeline_summary")
    assert summary["leads"] == 2 and summary["replies_needing_a_person"] == 1
    assert tool(c, t, "list_signals")[1] == {"articles": []}


def test_only_write_is_a_note_and_it_is_bounded(tmp_path):
    c, owner, acme, _ = make(tmp_path)
    t = pair(c, owner)["token"]
    assert tool(c, t, "add_lead_note", lead_id=acme, note="Asked on Claude to call Thursday")[1] == {"ok": True}
    lead = next(l for l in c.get("/api/leads", headers=bearer(owner)).json() if l["id"] == acme)
    assert lead["timeline"][-1]["text"].startswith("[via MCP]")
    assert tool(c, t, "add_lead_note", lead_id=acme, note="x" * 1001)[0]["isError"]
    assert tool(c, t, "add_lead_note", lead_id="nope", note="hi")[0]["isError"]


def test_token_cannot_reach_anything_else_and_bad_tokens_fail(tmp_path):
    c, owner, acme, _ = make(tmp_path)
    t = pair(c, owner)["token"]
    assert c.get("/api/leads", headers=bearer(t)).status_code == 403
    assert c.post("/api/icp/score", json={}, headers=bearer(t)).status_code == 403
    assert c.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
    assert c.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}, headers=bearer("mcnx_wrong")).status_code == 401


def test_malformed_requests(tmp_path):
    c, owner, _, _ = make(tmp_path)
    t = pair(c, owner)["token"]
    assert c.post("/api/mcp", content=b"{not json", headers={**bearer(t), "content-type": "application/json"}).status_code == 400
    assert c.post("/api/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}], headers=bearer(t)).status_code == 400
    assert c.post("/api/mcp", json={"id": 1, "method": "ping"}, headers=bearer(t)).json()["error"]["code"] == -32600
    assert rpc(c, t, "tools/call", {"arguments": {}}).json()["error"]["code"] == -32602
    assert tool(c, t, "no_such_tool")[0]["isError"]
    assert tool(c, t, "search_leads", limit="many")[0]["isError"]
