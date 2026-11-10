"""Flow builder API: flows, templates, validation, activation, enrolment, simulation, runs and the engine tick.

A flow belongs to a workspace (missing = main; app/ws_templates.py): owners change any flow, a workspace admin only its
own workspace's flows, and a workspace's flow only takes that workspace's leads."""
import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from . import brand, capture_connectors, routes_outreach, ws_templates as wst
from .activation import EVENT_STEP
from .activities import caller_name, team
from .flow_engine import Context, enroll, flow_stats, present_run, stop_run, tick
from .flow_simulate import simulate
from .flow_templates import flow_from_template, template_list
from .flows import (EMAIL_PLACEHOLDERS, IF_FIELDS, IF_OPS, NODE_TYPES, REPLY_OUTPUTS, TEXT_PLACEHOLDERS,
                    TRIGGER_REPLY_LABELS, FlowError, apply_edit, build_flow, ensure_collections, graph_of,
                    normalise_graph, now_iso, validate_flow, validate_graph)
from .growth_core import public_base_url
from .senders import make_transactional_sender
from .stages import ALL_STAGES
from .whatsapp import TEMPLATES as WHATSAPP_TEMPLATES

BASE_URL = os.environ.get("OUTREACH_BASE_URL", "http://localhost:8000")
MAX_BULK = 500
EXPORT_FORMAT = "mcn-flow-1"


class FlowBody(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    nodes: Optional[list] = None
    edges: Optional[list] = None
    settings: Optional[dict] = None
    workspace: Optional[str] = None


class ImportBody(BaseModel):
    workspace: Optional[str] = None
    format: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    nodes: Optional[list] = None
    edges: Optional[list] = None
    settings: Optional[dict] = None


class GraphBody(BaseModel):
    nodes: Optional[list] = None
    edges: Optional[list] = None


class EnrollBody(BaseModel):
    lead_ids: list


class SimulateBody(BaseModel):
    lead_id: str
    assume_reply: str = "timeout"


class TickBody(BaseModel):
    now: Optional[str] = None


def _team_names(store) -> set:
    return {m.get("name") for m in team(store) if m.get("name")}


def make_router(store, email_sender, transport=None, resolver=None, ai_transport=None) -> APIRouter:
    router = APIRouter()
    ensure_collections(store)
    notify_sender = make_transactional_sender(store)

    def context() -> Context:
        return Context(sender=email_sender, base_url=public_base_url(store, BASE_URL), transport=transport, resolver=resolver,
                       notify_sender=notify_sender, ignore_hours=os.environ.get("OUTREACH_IGNORE_BUSINESS_HOURS") == "1",
                       ai_transport=ai_transport)

    def find(flow_id: str) -> dict:
        flow = next((f for f in store.read("flows") if f["id"] == flow_id), None)
        if flow is None or not wst.readable(flow):
            raise HTTPException(404, "Flow not found")
        return flow

    def editable(flow_id: str, request: Request) -> dict:
        flow = find(flow_id)
        wst.require_edit(wst.user_of(request), wst.tpl_ws(flow))
        return flow

    def new_in(request: Request, wanted: Optional[str], flow: dict) -> dict:
        """The new flow, stamped with the workspace it is made for (checked: the person may change that workspace)."""
        user = wst.user_of(request)
        ws = wst.target_ws(store, user, wanted)
        wst.require_edit(user, ws)
        flow = {**flow, **({"workspace": ws} if ws != wst.MAIN else {})}
        store.update("flows", lambda rows: [*rows, flow])
        wst.record(store, request, "flow.create", ws, flow.get("name") or flow["id"], f"id {flow['id']}")
        return flow

    def runs_of(flow_id: str) -> list:
        return [r for r in store.read("flow_runs") if r["flow_id"] == flow_id]

    def present(flow: dict, full: bool = True) -> dict:
        shown = {k: v for k, v in flow.items() if k != "versions"} if full else {
            k: flow[k] for k in ("id", "name", "description", "status", "version", "created_at", "updated_at", "activated_at")}
        ws = wst.tpl_ws(flow)
        shown.update(workspace_id=ws, workspace_name=wst.name_of(store, ws))
        trigger = next((n for n in flow["nodes"] if n["type"].startswith("trigger.")), None)
        shown.update(stats=flow_stats(runs_of(flow["id"])), trigger=trigger["type"] if trigger else None,
                     steps=len(flow["nodes"]))
        if full:
            shown["errors"] = validate_flow(flow, _team_names(store))
        return shown

    def save(flow: dict) -> dict:
        store.update("flows", lambda rows: [flow if f["id"] == flow["id"] else f for f in rows])
        return flow

    @router.get("/api/flows")
    def list_flows():
        return sorted((present(f, full=False) for f in wst.filter_readable(store.read("flows"))), key=lambda f: f["updated_at"], reverse=True)

    @router.get("/api/flows/catalog")
    def catalog():
        """Everything the builder's forms offer, from the same lists the server validates against."""
        return {"node_types": {k: {"group": g, "title": t, "outputs": list(o)} for k, (g, t, o) in NODE_TYPES.items()},
                "stages": list(ALL_STAGES), "reply_labels": list(TRIGGER_REPLY_LABELS), "reply_outputs": list(REPLY_OUTPUTS),
                "activation_events": list(EVENT_STEP), "whatsapp_templates": list(WHATSAPP_TEMPLATES),
                "if_fields": IF_FIELDS, "if_ops": list(IF_OPS), "email_placeholders": list(EMAIL_PLACEHOLDERS),
                "text_placeholders": list(TEXT_PLACEHOLDERS), "team": [m.get("name") for m in team(store)],
                "caller": caller_name(store), "templates": template_list()}

    @router.get("/api/flows/templates")
    def templates():
        return template_list()

    @router.post("/api/flows/templates/{key}")
    def from_template(key: str, request: Request, workspace: str = ""):
        try:
            flow = flow_from_template(key)
        except LookupError:
            raise HTTPException(404, "Unknown template")
        return present(new_in(request, workspace, flow))

    @router.post("/api/flows/tick")
    def run_tick(body: TickBody):
        """Advance due runs now (the background timer does this every minute). `now` is honoured in test mode only."""
        return tick(store, routes_outreach.resolve_now(body.now), context())

    @router.post("/api/flows")
    def create_flow(body: FlowBody, request: Request):
        wst.require_edit(wst.user_of(request), wst.target_ws(store, wst.user_of(request), body.workspace))
        try:
            flow = build_flow(body.name, body.description or "", body.nodes, body.edges, body.settings, brand.stage_labels(store))
        except FlowError as exc:
            raise HTTPException(400, str(exc))
        return present(new_in(request, body.workspace, flow))

    @router.post("/api/flows/import")
    def import_flow(body: ImportBody, request: Request):
        """A flow exported from this app (or written by hand). It arrives as a draft: you check it, then turn it on."""
        if body.format not in (None, EXPORT_FORMAT):
            raise HTTPException(400, "This file is not a flow exported from this app.")
        if not body.nodes:
            raise HTTPException(400, "The file has no steps.")
        try:
            flow = build_flow(f"{body.name or 'Imported flow'} (imported)"[:80], body.description or "", body.nodes, body.edges, body.settings,
                              brand.stage_labels(store))
        except FlowError as exc:
            raise HTTPException(400, str(exc))
        return present(new_in(request, body.workspace, flow))

    @router.get("/api/flows/{flow_id}/export")
    def export_flow(flow_id: str):
        flow = find(flow_id)
        return {"format": EXPORT_FORMAT, "name": flow["name"], "description": flow.get("description", ""),
                "nodes": flow["nodes"], "edges": flow["edges"], "settings": flow.get("settings") or {}}

    @router.get("/api/flows/{flow_id}")
    def get_flow(flow_id: str):
        return present(find(flow_id))

    @router.put("/api/flows/{flow_id}")
    def edit_flow(flow_id: str, body: FlowBody, request: Request):
        flow = editable(flow_id, request)
        changes = body.model_dump(exclude={"workspace"})
        try:
            updated = apply_edit(flow, changes, has_runs=any(r["flow_version"] == flow["version"] for r in runs_of(flow_id)),
                                 team_names=_team_names(store), stage_labels=brand.stage_labels(store))
        except FlowError as exc:
            raise HTTPException(400, str(exc))
        pick = lambda f: {"name": f.get("name"), "description": f.get("description"), "version": f.get("version"), "steps": len(f.get("nodes") or [])}
        wst.record_diff(store, request, "flow.change", wst.tpl_ws(flow), flow.get("name") or flow_id, pick(flow), pick(updated))
        return present(save(updated))

    @router.delete("/api/flows/{flow_id}")
    def delete_flow(flow_id: str, request: Request):
        flow = editable(flow_id, request)
        stopped = 0
        for run in runs_of(flow_id):
            if run["status"] in ("running", "waiting"):
                stop_run(store, run["id"], "the flow was deleted")
                stopped += 1
        store.update("flows", lambda rows: [f for f in rows if f["id"] != flow_id])
        wst.record(store, request, "flow.delete", wst.tpl_ws(flow), flow.get("name") or flow_id, f"id {flow_id}; {stopped} running lead(s) stopped")
        return {"deleted": flow_id, "runs_stopped": stopped}

    @router.post("/api/flows/{flow_id}/validate")
    def validate(flow_id: str, request: Request, body: Optional[GraphBody] = None):
        flow = editable(flow_id, request)
        if body is not None and (body.nodes is not None or body.edges is not None):
            try:
                nodes, edges = normalise_graph(body.nodes if body.nodes is not None else flow["nodes"],
                                               body.edges if body.edges is not None else flow["edges"])
            except FlowError as exc:
                return {"ok": False, "errors": [{"node": None, "edge": None, "message": str(exc)}]}
            errors = validate_graph(nodes, edges, _team_names(store))
        else:
            errors = validate_flow(flow, _team_names(store))
        return {"ok": not errors, "errors": errors}

    def webhook_connector(flow: dict, make: bool) -> Optional[dict]:
        """The capture address that starts this flow. One per flow, made on first use and kept."""
        found = next((c for c in capture_connectors.all_connectors(store) if c.get("enroll_flow_id") == flow["id"]), None)
        if found is None and make:
            found = capture_connectors.create(store, {"type": "generic", "name": f"Flow: {flow['name']}"[:80],
                                                      "enroll_flow_id": flow["id"]})
        return found

    @router.get("/api/flows/{flow_id}/webhook")
    def flow_webhook(flow_id: str):
        """The address (and token) other tools post leads to, to start this flow. Only for flows with a webhook trigger."""
        flow = find(flow_id)
        if not any(n["type"] == "trigger.webhook" for n in flow["nodes"]):
            raise HTTPException(400, "This flow does not start from a webhook.")
        connector = webhook_connector(flow, make=True)
        view = capture_connectors.public_view(store, connector, public_base_url(store, BASE_URL), reveal=True)
        return {"connector_id": connector["id"], "enabled": connector.get("enabled", True), "urls": view.get("urls"),
                "token": view.get("token"), "last_capture_at": connector.get("last_capture_at"),
                "counters": connector.get("counters") or {}, "active": flow["status"] == "active"}

    @router.post("/api/flows/{flow_id}/activate")
    def activate(flow_id: str, request: Request):
        flow = editable(flow_id, request)
        errors = validate_flow(flow, _team_names(store))
        if errors:
            raise HTTPException(400, "Fix these first: " + " ".join(e["message"] for e in errors[:3]))
        if any(n["type"] == "trigger.webhook" for n in flow["nodes"]):
            webhook_connector(flow, make=True)
        versions = {**flow.get("versions", {}), str(flow["version"]): graph_of(flow)}
        wst.record(store, request, "flow.activate", wst.tpl_ws(flow), flow.get("name") or flow_id, f"id {flow_id}; version {flow['version']}")
        return present(save({**flow, "status": "active", "activated_at": now_iso(), "versions": versions, "updated_at": now_iso()}))

    @router.post("/api/flows/{flow_id}/pause")
    def pause(flow_id: str, request: Request):
        flow = editable(flow_id, request)
        wst.record(store, request, "flow.pause", wst.tpl_ws(flow), flow.get("name") or flow_id, f"id {flow_id}")
        return present(save({**flow, "status": "paused", "updated_at": now_iso()}))

    @router.post("/api/flows/{flow_id}/enroll")
    def enroll_leads(flow_id: str, body: EnrollBody, request: Request):
        from . import workspaces
        flow = editable(flow_id, request)
        if len(body.lead_ids) > MAX_BULK:
            raise HTTPException(400, f"Enrol at most {MAX_BULK} leads at a time.")
        now = routes_outreach.resolve_now(None)
        from .provenance import person_of
        who = person_of(getattr(request.state, "user", None))
        enrolled, refused = [], []
        in_scope = set(workspaces.only_visible_ids(store, [str(i) for i in body.lead_ids]))
        from . import lead_access
        by_id = {l["id"]: l for l in store.read("leads")} if lead_access.any_on(store) else {}
        for lead_id in dict.fromkeys(str(i) for i in body.lead_ids):
            if lead_id not in in_scope:
                refused.append({"lead_id": lead_id, "reason": "Lead not found"})
                continue
            target = by_id.get(lead_id)
            if target is not None and (no := lead_access.refusal(store, getattr(request.state, "user", None), target)):
                refused.append({"lead_id": lead_id, "reason": no})          # lead ownership: only admins (or the owner) start a flow for it
                continue
            try:
                enrolled.append(enroll(store, flow, lead_id, now, by=who["name"], by_email=who["email"], via="by hand")["lead_id"])
            except LookupError:
                refused.append({"lead_id": lead_id, "reason": "Lead not found"})
            except FlowError as exc:
                refused.append({"lead_id": lead_id, "reason": str(exc)})
        if enrolled:
            wst.record(store, request, "flow.enroll", wst.tpl_ws(flow), flow.get("name") or flow_id, f"{len(enrolled)} lead(s) started by hand")
        return {"enrolled": enrolled, "refused": refused}

    @router.post("/api/flows/{flow_id}/simulate")
    def simulate_flow(flow_id: str, body: SimulateBody, request: Request):
        from . import workspaces
        flow = editable(flow_id, request)
        lead = next((l for l in store.read("leads") if l["id"] == body.lead_id), None)
        if lead is None or not workspaces.visible(lead):
            raise HTTPException(404, "Lead not found")
        if body.assume_reply not in REPLY_OUTPUTS:
            raise HTTPException(400, "Unknown reply to assume")
        return simulate(store, flow, lead, routes_outreach.resolve_now(None), public_base_url(store, BASE_URL), body.assume_reply)

    @router.get("/api/flows/{flow_id}/runs")
    def flow_runs(flow_id: str):
        from . import workspaces
        flow = find(flow_id)
        leads = {l["id"]: l for l in store.read("leads")}
        ids = workspaces.visible_lead_ids(store)
        runs = sorted((r for r in runs_of(flow_id) if ids is None or r["lead_id"] in ids), key=lambda r: r["started_at"], reverse=True)
        return [present_run(r, flow, leads.get(r["lead_id"])) for r in runs]

    @router.get("/api/leads/{lead_id}/flows")
    def lead_flows(lead_id: str):
        if not any(l["id"] == lead_id for l in store.read("leads")):
            raise HTTPException(404, "Lead not found")
        flows = {f["id"]: f for f in store.read("flows")}
        runs = sorted((r for r in store.read("flow_runs") if r["lead_id"] == lead_id), key=lambda r: r["started_at"], reverse=True)
        return [present_run(r, flows.get(r["flow_id"])) for r in runs]

    @router.post("/api/flow-runs/{run_id}/stop")
    def stop(run_id: str):
        try:
            run = stop_run(store, run_id)
        except LookupError:
            raise HTTPException(404, "Run not found")
        flow = next((f for f in store.read("flows") if f["id"] == run["flow_id"]), None)
        return present_run(run, flow)

    return router
