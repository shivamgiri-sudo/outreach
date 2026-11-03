"""Workflow rules and AI reply drafts."""
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .ai import draft_reply
from .growth_core import public_base_url
from .http_util import post_json
from .workflows import STARTER_RULES, new_rule, run_time_rules

BASE_URL = os.environ.get("OUTREACH_BASE_URL", "http://localhost:8000")


class Step(BaseModel):
    type: str
    value: object


class RuleBody(BaseModel):
    name: str = ""
    trigger: Step
    action: Step


class RuleToggle(BaseModel):
    enabled: bool


class DraftBody(BaseModel):
    incoming: str


def make_router(store, ai_transport=post_json) -> APIRouter:
    router = APIRouter()

    @router.get("/api/workflows")
    def list_rules():
        return store.read("workflows")

    @router.post("/api/workflows")
    def add_rule(body: RuleBody):
        try:
            rule = new_rule(body.name, body.trigger.model_dump(), body.action.model_dump())
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, str(exc))
        store.write("workflows", [*store.read("workflows"), rule])
        return rule

    @router.post("/api/workflows/starter")
    def add_starter_rules():
        existing = {r["name"] for r in store.read("workflows")}
        fresh = [new_rule(r["name"], r["trigger"], r["action"]) for r in STARTER_RULES if r["name"] not in existing]
        store.write("workflows", [*store.read("workflows"), *fresh])
        return store.read("workflows")

    @router.put("/api/workflows/{rule_id}")
    def toggle_rule(rule_id: str, body: RuleToggle):
        rules = store.read("workflows")
        if rule_id not in {r["id"] for r in rules}:
            raise HTTPException(404, "Rule not found")
        store.write("workflows", [{**r, "enabled": body.enabled} if r["id"] == rule_id else r for r in rules])
        return next(r for r in store.read("workflows") if r["id"] == rule_id)

    @router.delete("/api/workflows/{rule_id}")
    def delete_rule(rule_id: str):
        rules = store.read("workflows")
        if rule_id not in {r["id"] for r in rules}:
            raise HTTPException(404, "Rule not found")
        store.write("workflows", [r for r in rules if r["id"] != rule_id])
        return {"deleted": rule_id}

    @router.post("/api/workflows/run-time-rules")
    def run_timed():
        return {"ran": run_time_rules(store, datetime.now(timezone.utc))}

    @router.post("/api/leads/{lead_id}/draft-reply")
    def draft(lead_id: str, body: DraftBody):
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None:
            raise HTTPException(404, "Lead not found")
        return draft_reply(store, lead, body.incoming, public_base_url(store, BASE_URL), ai_transport)

    return router
