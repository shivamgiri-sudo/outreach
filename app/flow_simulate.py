"""Simulate a flow for one lead: the path it would take and what each step would do. Nothing is saved or sent.

The simulation reads the lead, the settings and the do-not-contact list, and uses the same checks as the engine
(conditions, sticky A/B variants, consent, closed stages, unfilled placeholders, stage rules). Stage changes and tags
are applied to a private copy of the lead so later If steps see them. A wait-for-reply step follows the reply you
choose to assume (default: no reply, so the timeout path).
"""
import copy
from datetime import datetime, timedelta

from . import flow_actions as act
from .activities import caller_name, next_call_slot
from .channels.netguard import check_public_url
from .flows import graph_of, is_trigger, title_of
from .senders import email_ready
from .sequence_runner import NO_EMAIL_STAGES, get_settings
from .stages import CONSENT_REQUIRED, can_move
from .whatsapp import TEMPLATES as WHATSAPP_TEMPLATES

MAX_SIMULATED_STEPS = 60
LINKEDIN_URL = ("linkedin.com/",)


def _entry(node: dict, result: str, label=None, ok: bool = True) -> dict:
    return {"node": node["id"], "type": node["type"], "title": title_of(node), "result": result, "label": label, "ok": ok}


def _message_block(store, lead: dict):
    reason = act.blocked_reason(store, lead)
    return f"would not send: {reason}" if reason else None


def _simulate_action(store, flow, node, lead, now, base_url) -> tuple:
    """(label, result, ok) for one action, without doing it."""
    t, c = node["type"], node.get("config") or {}
    if t == "email":
        if lead["stage"] in NO_EMAIL_STAGES:
            return "error", f"would not email: lead is {lead['stage']}", False
        if not lead.get("email"):
            return "error", "would not email: no email address", False
        composed = act.compose_email(store, flow, node, lead, now, base_url)
        if composed is None:
            return "error", "would not email: a {{placeholder}} cannot be filled for this lead", False
        live = bool(get_settings(store)["live_sending"]) and bool(email_ready(store))
        mode = "for real" if live else "practice only (Send for real is off)"
        return "next", f"would email “{composed[0]['subject']}” to {lead['email']} — {mode}; caps, warm-up and business hours apply", True
    if t == "whatsapp":
        if not lead.get("consent"):
            return "error", "would not send WhatsApp: no recorded consent", False
        if not lead.get("phone"):
            return "error", "would not send WhatsApp: no phone number", False
        _, missing = act.text_for(store, lead, WHATSAPP_TEMPLATES.get(c.get("template"), ""), base_url, now)
        if missing:
            return "error", f"would not send WhatsApp: details missing ({', '.join(missing)})", False
        return "next", f"would send WhatsApp template {c.get('template')} (practice until WhatsApp is connected and on)", True
    if t == "sms":
        if not lead.get("consent") or not lead.get("phone"):
            return "error", "would not send SMS: needs recorded consent and a phone number", False
        return "next", "would send the SMS (practice until SMS is connected and switched on)", True
    if t == "linkedin_task":
        if c.get("mode", "task") == "start_sequence":
            if not any(part in (lead.get("linkedin_url") or "").lower() for part in LINKEDIN_URL):
                return "error", "would not start the LinkedIn sequence: no LinkedIn profile link", False
            if lead.get("linkedin_seq"):
                return "error", "would not start the LinkedIn sequence: already on it", False
            return "next", "would start the LinkedIn sequence (a person sends each step)", True
        return "next", "would create a LinkedIn task with your message (sent by hand)", True
    if t == "call_task":
        owner = str(c.get("owner") or "").strip() or caller_name(store)
        return "next", f"would create a call task for {owner}, due {act.ist_text(next_call_slot(now))}", True
    if t == "meeting_link":
        return "next", "would prepare a booking link (or a 'book a meeting' task if no booking-link calendar is connected)", True
    if t == "set_stage":
        target, current = c.get("stage"), lead["stage"]
        closed = current in NO_EMAIL_STAGES and not (current == "not_now" and target == "new")
        if current != target and (closed or not can_move(current, target, bool(lead.get("consent")))
                                  or (target in CONSENT_REQUIRED and not lead.get("consent"))):
            return "error", f"would not change the stage: {current} → {target} is not allowed", False
        lead["stage"] = target
        return "next", f"would set the stage to {target}", True
    if t == "add_tag":
        lead["tags"] = [*(lead.get("tags") or []), c.get("tag")]
        return "next", f"would add the tag “{c.get('tag')}”", True
    if t == "notify":
        return "next", f"would notify {str(c.get('to') or '').strip() or caller_name(store)}", True
    if t == "webhook":
        try:
            url = check_public_url(str(c.get("url") or ""), resolver=lambda host: ["93.184.216.34"])
        except ValueError as exc:
            return "error", f"would not call the webhook: {exc}", False
        return "next", f"would POST the lead's basic details to {url.split('/')[2]}", True
    return "next", "", True


def simulate(store, flow: dict, lead: dict, now: datetime, base_url: str, assume_reply: str = "timeout") -> dict:
    graph = graph_of(flow)
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = graph["edges"]
    lead = copy.deepcopy(lead)
    trigger = next((n for n in graph["nodes"] if is_trigger(n["type"])), None)
    if trigger is None:
        return {"path": [], "ended": "This flow has no trigger yet.", "writes": "none"}
    path = [_entry(trigger, "starts here", "next")]
    blocked = act.blocked_reason(store, lead)
    if blocked:
        return {"path": path, "ended": f"Would not start: {blocked}.", "writes": "none"}

    def after(node_id, label):
        return next((e["to"] for e in edges if e["from"] == node_id and e["label"] == label), None)

    current, clock, seen, ended = after(trigger["id"], "next"), now, {}, "Reaches the end of the flow."
    for _ in range(MAX_SIMULATED_STEPS):
        if current is None:
            break
        node = nodes[current]
        seen[current] = seen.get(current, 0) + 1
        if seen[current] > 1:
            ended = f"Loops back to “{title_of(node)}”; the simulation stops here."
            break
        if node["type"] not in ("wait", "wait_for_reply", "if", "split", "stop") and (block := _message_block(store, lead)):
            path.append(_entry(node, block, None, False))
            ended = "Stops: the lead may not be contacted."
            break
        t, c = node["type"], node.get("config") or {}
        if t == "if":
            passed, why = act.evaluate(c, act.lead_facts(lead))
            label = "yes" if passed else "no"
            path.append(_entry(node, f"{label} — {why}", label))
        elif t == "split":
            label = act.split_variant(lead["id"], node["id"], c.get("percent_a"))
            path.append(_entry(node, f"variant {label.upper()} (the same lead always gets the same variant)", label))
        elif t == "wait":
            clock = act.wait_until(c, clock)
            label = "next"
            path.append(_entry(node, f"waits until {act.ist_text(clock)}", label))
        elif t == "wait_for_reply":
            label = "timeout" if assume_reply == "timeout" else assume_reply if after(node["id"], assume_reply) else "any"
            clock = clock + timedelta(days=int(c.get("days") or 1)) if label == "timeout" else clock
            shown = "no reply, so the timeout path" if label == "timeout" else f"assumed reply: {assume_reply}"
            path.append(_entry(node, f"waits up to {c.get('days')} day(s) for a reply — {shown}", label))
        elif t == "stop":
            path.append(_entry(node, "stops here", None))
            ended = "Stops at a Stop step."
            current = None
            break
        else:
            label, result, ok = _simulate_action(store, flow, node, lead, clock, base_url)
            if not ok and not after(node["id"], "error"):
                path.append(_entry(node, result + " — the flow would stop here", None, False))
                ended = "Stops: a step was refused and has no error path."
                current = None
                break
            path.append(_entry(node, result, label, ok))
        current = after(node["id"], label)
    else:
        ended = f"Stopped after {MAX_SIMULATED_STEPS} steps."
    return {"path": path, "ended": ended, "writes": "none"}
