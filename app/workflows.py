"""Your own communication flow: "when X happens, do Y". Each rule runs at most once per lead."""
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from .activation import EVENT_STEP
from .growth_core import public_base_url
from .replies import _RULES as REPLY_RULES
from .activities import add_task
from .sequence_runner import NO_EMAIL_STAGES
from .stages import ALL_STAGES, CONSENT_REQUIRED, can_move
from .whatsapp import TEMPLATES, WhatsAppError, send

BASE_URL = os.environ.get("OUTREACH_BASE_URL", "http://localhost:8000")
REPLY_LABELS = tuple(label for label, _ in REPLY_RULES) + ("other",)
TRIGGER_TYPES = ("reply", "stage_change", "activation", "no_reply_days")
ACTION_TYPES = ("whatsapp", "task", "set_stage", "note")
STARTER_RULES = [
    {"name": "Yes by email → WhatsApp the sample screen", "trigger": {"type": "reply", "value": "interested"},
     "action": {"type": "whatsapp", "value": "first_after_yes"}},
    {"name": "No reply after 14 days → pause (not now)", "trigger": {"type": "no_reply_days", "value": 14},
     "action": {"type": "set_stage", "value": "not_now"}},
]


def validate(rule: dict) -> None:
    trigger, action = rule["trigger"], rule["action"]
    if trigger["type"] not in TRIGGER_TYPES:
        raise ValueError(f"Unknown trigger: {trigger['type']}")
    allowed = {"reply": REPLY_LABELS, "stage_change": ALL_STAGES, "activation": tuple(EVENT_STEP)}
    if trigger["type"] in allowed and trigger["value"] not in allowed[trigger["type"]]:
        raise ValueError(f"Bad trigger value: {trigger['value']}")
    if trigger["type"] == "no_reply_days" and not (isinstance(trigger["value"], int) and trigger["value"] >= 1):
        raise ValueError("Days must be a whole number of 1 or more")
    if action["type"] not in ACTION_TYPES:
        raise ValueError(f"Unknown action: {action['type']}")
    if action["type"] == "whatsapp" and action["value"] not in TEMPLATES:
        raise ValueError("Unknown WhatsApp template")
    if action["type"] == "set_stage" and action["value"] not in ALL_STAGES:
        raise ValueError("Unknown stage")
    if action["type"] in ("task", "note") and not str(action["value"]).strip():
        raise ValueError("Write the text for this action")


def new_rule(name: str, trigger: dict, action: dict) -> dict:
    rule = {"id": uuid.uuid4().hex[:8], "name": name or "Untitled rule", "enabled": True,
            "trigger": trigger, "action": action, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    validate(rule)
    return rule


def _task(store, lead_id: str, text: str, now: datetime) -> None:
    add_task(store, lead_id, "workflow", text, source="workflow")


def _update(store, lead_id: str, change) -> None:
    store.update_lead(lead_id, change)


def _mark_run(store, lead_id: str, rule: dict, now: datetime, text: str) -> None:
    event = {"at": now.isoformat(timespec="seconds"), "kind": "workflow", "text": text}
    _update(store, lead_id, lambda lead: {**lead, "workflow_runs": [*lead.get("workflow_runs", []), rule["id"]],
                                          "timeline": [*lead["timeline"], event]})


def _apply(store, rule: dict, lead: dict, now: datetime) -> str:
    action = rule["action"]
    if action["type"] == "whatsapp":
        try:
            send(store, lead["id"], action["value"], "", now, public_base_url(store, BASE_URL))
            return f"sent WhatsApp {action['value']}"
        except WhatsAppError as exc:
            _task(store, lead["id"], f"Workflow '{rule['name']}' could not send WhatsApp: {exc}", now)
            return f"WhatsApp not sent ({exc})"
    if action["type"] == "task":
        _task(store, lead["id"], action["value"], now)
        return "task created"
    if action["type"] == "set_stage":
        target = action["value"]
        allowed = lead["stage"] not in NO_EMAIL_STAGES or target == lead["stage"]
        movable = can_move(lead["stage"], target, has_consent=bool(lead.get("consent"))) and not (target in CONSENT_REQUIRED and not lead.get("consent"))
        if allowed and movable:
            _update(store, lead["id"], lambda l: {**l, "stage": target})
            return f"stage set to {target}"
        _task(store, lead["id"], f"Workflow '{rule['name']}' could not move the lead to {target}", now)
        return "stage not changed"
    return "note added"


def _run_rule(store, rule: dict, lead: dict, now: datetime) -> bool:
    if rule["id"] in lead.get("workflow_runs", []):
        return False
    result = _apply(store, rule, lead, now)
    text = rule["action"]["value"] if rule["action"]["type"] == "note" else f"{rule['name']}: {result}"
    _mark_run(store, lead["id"], rule, now, text)
    return True


def fire(store, trigger_type: str, value, lead_id: str, now: Optional[datetime] = None) -> int:
    """Run every enabled rule matching this event for this lead. Returns how many ran."""
    now = now or datetime.now(timezone.utc)
    ran = 0
    for rule in store.read("workflows"):
        if not rule["enabled"] or rule["trigger"]["type"] != trigger_type or rule["trigger"]["value"] != value:
            continue
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead and _run_rule(store, rule, lead, now):
            ran += 1
    return ran


COLD_STAGES = ("new", "verified", "enriched", "contacted", "engaged")


def run_time_rules(store, now: Optional[datetime] = None) -> int:
    """Rules that depend on time passing: no reply for N days after our last email."""
    now = now or datetime.now(timezone.utc)
    rules = [r for r in store.read("workflows") if r["enabled"] and r["trigger"]["type"] == "no_reply_days"]
    if not rules:
        return 0
    last_sent = {}
    for message in store.read("outbox"):
        if message.get("status") != "failed":
            stamp = datetime.fromisoformat(message["sent_at"])
            last_sent[message["lead_id"]] = max(stamp, last_sent.get(message["lead_id"], stamp))
    ran = 0
    for rule in rules:
        for lead in store.read("leads"):
            sent_at = last_sent.get(lead["id"])
            # Only leads still in the cold sequence: a warm lead (interested, on WhatsApp, called, on a trial) is
            # being handled by a person and must never be demoted just because they didn't reply by email.
            if not sent_at or lead["engagement"]["replies"] or lead["stage"] not in COLD_STAGES:
                continue
            if (now - sent_at).days >= rule["trigger"]["value"] and _run_rule(store, rule, lead, now):
                ran += 1
    return ran
