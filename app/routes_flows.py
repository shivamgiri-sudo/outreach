"""Flow builder API: flows, templates, validation, activation, enrolment, simulation, runs and the engine tick."""
import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import capture_connectors, routes_outreach
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


class FlowBody(BaseModel):
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
        if flow is None:
            raise HTTPException(404, "Flow not found")
        return flow

    def runs_of(flow_id: str) -> list:
        return [r for r in store.read("flow_runs") if r["flow_id"] == flow_id]

    def present(flow: dict, full: bool = True) -> dict:
        shown = {k: v for k, v in flow.items() if k != "versions"} if full else {
            k: flow[k] for k in ("id", "name", "description", "status", "version", "created_at", "updated_at", "activated_at")}
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
        return sorted((present(f, full=False) for f in store.read("flows")), key=lambda f: f["updated_at"], reverse=True)

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
    def from_template(key: str):
        try:
            flow = flow_from_template(key)
        except LookupError:
            raise HTTPException(404, "Unknown template")
        store.update("flows", lambda rows: [*rows, flow])
        return present(flow)

    @router.post("/api/flows/tick")
    def run_tick(body: TickBody):
        """Advance due runs now (the background timer does this every minute). `now` is honoured in test mode only."""
        return tick(store, routes_outreach.resolve_now(body.now), context())

    @router.post("/api/flows")
    def create_flow(body: FlowBody):
        try:
            flow = build_flow(body.name, body.description or "", body.nodes, body.edges, body.settings)
        except FlowError as exc:
            raise HTTPException(400, str(exc))
        store.update("flows", lambda rows: [*rows, flow])
        return present(flow)

    @router.get("/api/flows/{flow_id}")
    def get_flow(flow_id: str):
        return present(find(flow_id))

    @router.put("/api/flows/{flow_id}")
    def edit_flow(flow_id: str, body: FlowBody):
        flow = find(flow_id)
        changes = body.model_dump()
        try:
            updated = apply_edit(flow, changes, has_runs=any(r["flow_version"] == flow["version"] for r in runs_of(flow_id)),
                                 team_names=_team_names(store))
        except FlowError as exc:
            raise HTTPException(400, str(exc))
        return present(save(updated))

    @router.delete("/api/flows/{flow_id}")
    def delete_flow(flow_id: str):
        find(flow_id)
        stopped = 0
        for run in runs_of(flow_id):
            if run["status"] in ("running", "waiting"):
                stop_run(store, run["id"], "the flow was deleted")
                stopped += 1
        store.update("flows", lambda rows: [f for f in rows if f["id"] != flow_id])
        return {"deleted": flow_id, "runs_stopped": stopped}

    @router.post("/api/flows/{flow_id}/validate")
    def validate(flow_id: str, body: Optional[GraphBody] = None):
        flow = find(flow_id)
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
    def activate(flow_id: str):
        flow = find(flow_id)
        errors = validate_flow(flow, _team_names(store))
        if errors:
            raise HTTPException(400, "Fix these first: " + " ".join(e["message"] for e in errors[:3]))
        if any(n["type"] == "trigger.webhook" for n in flow["nodes"]):
            webhook_connector(flow, make=True)
        versions = {**flow.get("versions", {}), str(flow["version"]): graph_of(flow)}
        return present(save({**flow, "status": "active", "activated_at": now_iso(), "versions": versions, "updated_at": now_iso()}))

    @router.post("/api/flows/{flow_id}/pause")
    def pause(flow_id: str):
        flow = find(flow_id)
        return present(save({**flow, "status": "paused", "updated_at": now_iso()}))

    @router.post("/api/flows/{flow_id}/enroll")
    def enroll_leads(flow_id: str, body: EnrollBody):
        flow = find(flow_id)
        if len(body.lead_ids) > MAX_BULK:
            raise HTTPException(400, f"Enrol at most {MAX_BULK} leads at a time.")
        now = routes_outreach.resolve_now(None)
        enrolled, refused = [], []
        for lead_id in dict.fromkeys(str(i) for i in body.lead_ids):
            try:
                enrolled.append(enroll(store, flow, lead_id, now)["lead_id"])
            except LookupError:
                refused.append({"lead_id": lead_id, "reason": "Lead not found"})
            except FlowError as exc:
                refused.append({"lead_id": lead_id, "reason": str(exc)})
        return {"enrolled": enrolled, "refused": refused}

    @router.post("/api/flows/{flow_id}/simulate")
    def simulate_flow(flow_id: str, body: SimulateBody):
        flow = find(flow_id)
        lead = next((l for l in store.read("leads") if l["id"] == body.lead_id), None)
        if lead is None:
            raise HTTPException(404, "Lead not found")
        if body.assume_reply not in REPLY_OUTPUTS:
            raise HTTPException(400, "Unknown reply to assume")
        return simulate(store, flow, lead, routes_outreach.resolve_now(None), public_base_url(store, BASE_URL), body.assume_reply)

    @router.get("/api/flows/{flow_id}/runs")
    def flow_runs(flow_id: str):
        flow = find(flow_id)
        leads = {l["id"]: l for l in store.read("leads")}
        runs = sorted(runs_of(flow_id), key=lambda r: r["started_at"], reverse=True)
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
