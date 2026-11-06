"""Flow builder data model: a visual sequence (trigger -> logic -> waits -> actions) kept as nodes and edges.

flows = [{id, name, description, status: draft|active|paused, version, nodes: [{id, type, config, x, y}],
          edges: [{id, from, to, label}], settings: {stop_on_reply}, versions: {"<n>": {nodes, edges}},
          activated_at, created_at, updated_at}]

This module holds the node catalogue, validation (every problem in plain words, tied to the node or edge it is
about) and versioning; the starter templates are in app/flow_templates.py. The engine that runs a flow for each lead is app/flow_engine.py.
Saving refuses only what would make the data unusable (unknown step types, edges to nowhere, loops without a
wait); a draft may still be incomplete. Activating needs a flow with no problems at all.
"""
import copy
import re
import uuid
from datetime import datetime, timezone

from .activation import EVENT_STEP
from .channels.netguard import check_public_url
from .stages import ALL_STAGES
from .whatsapp import TEMPLATES as WHATSAPP_TEMPLATES

COLLECTIONS = ("flows", "flow_runs")
STATUSES = ("draft", "active", "paused")
MAX_NODES, MAX_EDGES = 100, 200
MAX_NAME, MAX_DESCRIPTION, MAX_TITLE = 80, 500, 80
MAX_SUBJECT, MAX_BODY, MAX_SMS, MAX_NOTE = 200, 5000, 480, 1000

TRIGGER_REPLY_LABELS = ("interested", "not_interested", "question", "not_now", "wrong_person", "any")
REPLY_OUTPUTS = ("interested", "not_interested", "question", "not_now", "wrong_person", "other", "any", "timeout")
EMAIL_PLACEHOLDERS = ("greeting", "first_name", "company", "role", "sender", "unsub_link", "demo_link", "offer_line")
TEXT_PLACEHOLDERS = ("greeting", "first_name", "company", "role", "sender", "demo_link")
IF_FIELDS = {
    "score": "number", "open_roles": "number", "band": "text", "stage": "text", "city": "text", "country": "text",
    "title": "text", "persona": "text", "source": "text", "campaign_id": "text", "tag": "tags",
    "has_email": "bool", "has_phone": "bool", "has_consent": "bool", "has_meeting": "bool", "has_replied": "bool",
    "linkedin_accepted": "bool", "linkedin_status": "text",
}
IF_OPS = ("eq", "ne", "gt", "lt", "contains", "in", "exists")
LINKEDIN_MODES = ("start_sequence", "task")
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# type -> (group, default title, outputs). Outputs ending in "?" are optional extras (the error path).
NODE_TYPES = {
    "trigger.lead_added": ("trigger", "New lead added", ("next",)),
    "trigger.manual": ("trigger", "Added by hand", ("next",)),
    "trigger.reply": ("trigger", "They reply", ("next",)),
    "trigger.stage_changed": ("trigger", "Stage changes", ("next",)),
    "trigger.meeting_booked": ("trigger", "Meeting booked", ("next",)),
    "trigger.activation_event": ("trigger", "Product activity", ("next",)),
    "trigger.no_reply_days": ("trigger", "No reply for some days", ("next",)),
    "if": ("logic", "If / else", ("yes", "no")),
    "split": ("logic", "A/B split", ("a", "b")),
    "wait": ("logic", "Wait", ("next",)),
    "wait_for_reply": ("logic", "Wait for a reply", REPLY_OUTPUTS),
    "email": ("action", "Send email", ("next", "error")),
    "whatsapp": ("action", "Send WhatsApp", ("next", "error")),
    "sms": ("action", "Send SMS", ("next", "error")),
    "linkedin_task": ("action", "LinkedIn task", ("next", "error")),
    "call_task": ("action", "Call task", ("next",)),
    "meeting_link": ("action", "Meeting link", ("next", "error")),
    "set_stage": ("action", "Set stage", ("next", "error")),
    "add_tag": ("action", "Add tag", ("next",)),
    "notify": ("action", "Notify a person", ("next",)),
    "webhook": ("action", "Webhook", ("next", "error")),
    "stop": ("action", "Stop", ()),
    # n8n-style additions: a schedule trigger, an AI step, variables, a multi-way switch, a generic HTTP call and sticky notes
    "trigger.schedule": ("trigger", "On a schedule", ("next",)),
    "trigger.webhook": ("trigger", "Webhook received", ("next",)),
    "ai": ("action", "Ask Claude", ("next", "error")),
    "set_vars": ("action", "Set fields", ("next",)),
    "switch": ("logic", "Switch", ("c1", "c2", "c3", "c4", "other")),
    "http": ("action", "HTTP request", ("next", "error")),
    "note": ("note", "Note", ()),
    "loop": ("logic", "Repeat", ("again", "done")),
}
WAIT_TYPES = ("wait", "wait_for_reply")
VAR_NAME = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
RESERVED_VARS = frozenset({"greeting", "first_name", "company", "role", "sender", "unsub_link", "demo_link", "offer_line"})
AI_MODES = ("draft", "classify", "extract")
SCHEDULE_WHEN = ("daily", "weekdays", "weekly")
HTTP_METHODS = ("GET", "POST")
MAX_PROMPT, MAX_VAR_VALUE = 2000, 500
MAX_VARS_PER_STEP, MAX_SWITCH_CASES = 8, 4


class FlowError(ValueError):
    """A flow that cannot be saved or run, with a reason a person can read."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:8]


def ensure_collections(store) -> None:
    """Create the flows / flow_runs collections in older data files (atomically, under the store's lock)."""
    with store._lock:
        for key in COLLECTIONS:
            try:
                store.read(key)
            except KeyError:
                store.write(key, [])


def is_trigger(node_type: str) -> bool:
    return str(node_type).startswith("trigger.")


def title_of(node: dict) -> str:
    title = str((node.get("config") or {}).get("title") or "").strip()
    return title or NODE_TYPES.get(node.get("type"), ("", node.get("type", "Step"), ()))[1]


def outputs_of(node_type: str) -> tuple:
    return NODE_TYPES.get(node_type, ("", "", ()))[2]


# ---------------------------------------------------------------- normalising what the browser sends
def _number(value, default=0.0) -> float:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return default


def normalise_graph(nodes, edges) -> tuple:
    """Clean node and edge lists from the browser. Raises FlowError when the shape itself is wrong."""
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise FlowError("Nodes and edges must be lists.")
    if len(nodes) > MAX_NODES:
        raise FlowError(f"A flow can have at most {MAX_NODES} steps.")
    if len(edges) > MAX_EDGES:
        raise FlowError(f"A flow can have at most {MAX_EDGES} connections.")
    clean_nodes, clean_edges = [], []
    for raw in nodes:
        if not isinstance(raw, dict) or not raw.get("type"):
            raise FlowError("Every step needs a type.")
        config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
        clean_nodes.append({"id": str(raw.get("id") or new_id("n"))[:40], "type": str(raw["type"])[:40],
                            "config": copy.deepcopy(config), "x": _number(raw.get("x")), "y": _number(raw.get("y"))})
    for raw in edges:
        if not isinstance(raw, dict):
            raise FlowError("Every connection must be an object.")
        clean_edges.append({"id": str(raw.get("id") or new_id("e"))[:40], "from": str(raw.get("from") or ""),
                            "to": str(raw.get("to") or ""), "label": str(raw.get("label") or "next")[:40]})
    return clean_nodes, clean_edges


def normalise_settings(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {"stop_on_reply": bool(raw.get("stop_on_reply", True))}


# ---------------------------------------------------------------- validation
def _problem(message: str, node: str = None, edge: str = None) -> dict:
    return {"node": node, "edge": edge, "message": message}


def _placeholders(text) -> set:
    return set(_PLACEHOLDER.findall(str(text or "")))


def _check_text_placeholders(text, allowed, what: str) -> list:
    unknown = sorted(_placeholders(text) - set(allowed))
    if unknown:
        return [f"{what} uses unknown placeholder(s): " + ", ".join("{{%s}}" % u for u in unknown)
                + ". Use: " + ", ".join("{{%s}}" % a for a in allowed) + "."]
    return []


def _int_in(value, low: int, high: int):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if low <= number <= high and float(value) == number else None


def _check_webhook_url(url: str) -> list:
    """Static check at save time (https, no credentials, no private address typed in). Names are resolved when it runs."""
    try:
        check_public_url(url, resolver=lambda host: ["93.184.216.34"])
    except ValueError as exc:
        return [f"Webhook address: {exc}"]
    return []


def flow_var_names(nodes: list) -> list:
    """Field names this flow creates (Set fields, Ask Claude and HTTP request steps): usable as {{name}} in later text."""
    names = []
    for n in nodes:
        c = n.get("config") or {}
        if n["type"] == "set_vars":
            names += [str(v.get("name") or "") for v in c.get("values") or [] if isinstance(v, dict)]
        elif n["type"] in ("ai", "http"):
            names.append(str(c.get("save_as") or ""))
    return [x for x in dict.fromkeys(names) if VAR_NAME.match(x) and x not in RESERVED_VARS]


def _var_errors(name, what: str) -> list:
    name = str(name or "")
    if not VAR_NAME.match(name):
        return [f"{what}: use a short lowercase name with letters, numbers or underscores (for example intent or summary)."]
    if name in RESERVED_VARS:
        return [f"{what}: '{name}' is already used by the app. Pick another name."]
    return []


def _new_step_errors(t: str, c: dict, extra_vars=()) -> list:
    out = []
    if t == "trigger.webhook":
        return out                      # nothing to set: the address is made when the flow is turned on
    if t == "trigger.schedule":
        if c.get("when") not in SCHEDULE_WHEN:
            out.append("Pick how often it runs: every day, every weekday or once a week.")
        if _int_in(c.get("hour"), 0, 23) is None:
            out.append("Pick the hour it runs (0–23, India time).")
        if c.get("when") == "weekly" and _int_in(c.get("weekday"), 0, 6) is None:
            out.append("Pick the day of the week it runs.")
        if _int_in(c.get("limit", 50), 1, 500) is None:
            out.append("Start between 1 and 500 leads each time.")
        for key in ("source", "campaign_id", "tag", "city", "country", "stage", "band"):
            if len(str(c.get(key) or "")) > 80:
                out.append(f"The {key.replace('_', ' ')} filter is too long.")
    elif t == "ai":
        if c.get("mode") not in AI_MODES:
            out.append("Pick what Claude should do: write a draft, choose one of your options, or pull out a detail.")
        prompt = str(c.get("prompt") or "").strip()
        if not prompt:
            out.append("Tell Claude what to do. You can use the lead's details, for example {{company}} and {{role}}.")
        elif len(prompt) > MAX_PROMPT:
            out.append(f"Keep the instruction under {MAX_PROMPT} characters.")
        if c.get("mode") == "classify":
            options = [str(o).strip() for o in c.get("options") or [] if str(o).strip()]
            if not 2 <= len(options) <= 6 or len(set(o.lower() for o in options)) != len(options):
                out.append("Give Claude 2 to 6 different options to choose from.")
        out += _var_errors(c.get("save_as"), "Save the answer as")
    elif t == "set_vars":
        values = c.get("values")
        if not isinstance(values, list) or not 1 <= len(values) <= MAX_VARS_PER_STEP:
            out.append(f"Add 1 to {MAX_VARS_PER_STEP} fields to set.")
        else:
            for row in values:
                row = row if isinstance(row, dict) else {}
                out += _var_errors(row.get("name"), "Field name")
                if len(str(row.get("value") or "")) > MAX_VAR_VALUE:
                    out.append(f"Keep each field value under {MAX_VAR_VALUE} characters.")
                out += _check_text_placeholders(row.get("value"), [*TEXT_PLACEHOLDERS, *extra_vars],
                                                f"The value of {row.get('name') or 'a field'}")
    elif t == "switch":
        field = str(c.get("field") or "")
        if not (field in IF_FIELDS or (field.startswith("var:") and VAR_NAME.match(field[4:]))):
            out.append("Pick what to switch on: a lead detail (stage, band, city…) or a field made earlier in the flow.")
        cases = [str(v).strip() for v in c.get("cases") or [] if str(v).strip()]
        if not 1 <= len(cases) <= MAX_SWITCH_CASES:
            out.append(f"Give 1 to {MAX_SWITCH_CASES} values to match; everything else goes to 'other'.")
    elif t == "http":
        if str(c.get("method") or "GET").upper() not in HTTP_METHODS:
            out.append("The method must be GET or POST.")
        out += _check_webhook_url(re.sub(r"\{\{[^}]*\}\}", "x", str(c.get("url") or "")))
        if len(str(c.get("body") or "")) > MAX_BODY:
            out.append("The request body is too long.")
        if str(c.get("save_as") or ""):
            out += _var_errors(c.get("save_as"), "Save the response as")
    elif t == "note":
        if len(str(c.get("text") or "")) > MAX_NOTE:
            out.append(f"Keep the note under {MAX_NOTE} characters.")
    return out


def config_errors(node: dict, team_names=None, extra_vars=()) -> list:
    """Plain-English problems with one step's settings."""
    t, c = node["type"], node.get("config") or {}
    out = []
    if t in ("trigger.schedule", "trigger.webhook", "ai", "set_vars", "switch", "http", "note"):
        if len(str(c.get("title") or "")) > MAX_TITLE:
            out.append(f"Keep the step name under {MAX_TITLE} characters.")
        return out + _new_step_errors(t, c, extra_vars) + (
            _check_text_placeholders(c.get("prompt"), [*TEXT_PLACEHOLDERS, *extra_vars], "The instruction") if t == "ai" else [])
    if len(str(c.get("title") or "")) > MAX_TITLE:
        out.append(f"Keep the step name under {MAX_TITLE} characters.")
    if t == "trigger.reply" and c.get("label") not in TRIGGER_REPLY_LABELS:
        out.append("Pick which kind of reply starts the flow (interested, question, not now, wrong person, not interested or any).")
    elif t == "trigger.stage_changed" and c.get("stage") not in ALL_STAGES:
        out.append("Pick the stage that starts the flow.")
    elif t == "trigger.activation_event" and c.get("event") not in EVENT_STEP:
        out.append("Pick the product activity that starts the flow (for example role published).")
    elif t == "trigger.no_reply_days" and _int_in(c.get("days"), 1, 90) is None:
        out.append("Days without a reply must be a whole number from 1 to 90.")
    elif t == "trigger.lead_added":
        for key in ("source", "campaign_id", "tag", "city", "country"):
            if len(str(c.get(key) or "")) > 80:
                out.append(f"The {key.replace('_', ' ')} filter is too long.")
    elif t == "if":
        out += _if_errors(c)
    elif t == "loop" and _int_in(c.get("times"), 1, 10) is None:
        out.append("Repeat 1 to 10 times.")
    elif t == "split" and _int_in(c.get("percent_a"), 1, 99) is None:
        out.append("Variant A must get between 1 and 99 percent of leads.")
    elif t == "wait":
        days, hours = _int_in(c.get("days", 0), 0, 60), _int_in(c.get("hours", 0), 0, 23)
        if days is None or hours is None:
            out.append("Wait days must be 0–60 and hours 0–23 (whole numbers).")
        elif days == 0 and hours == 0 and not c.get("business_hours"):
            out.append("Set how long to wait (days or hours), or tick 'until business hours'.")
    elif t == "wait_for_reply" and _int_in(c.get("days"), 1, 60) is None:
        out.append("Wait for a reply for 1 to 60 days.")
    elif t == "email":
        out += _email_errors(c, extra_vars)
    elif t == "whatsapp" and c.get("template") not in WHATSAPP_TEMPLATES:
        out.append("Pick an approved WhatsApp template (free text is only allowed inside the 24-hour window, so flows use templates).")
    elif t == "sms":
        text = str(c.get("text") or "").strip()
        if not text:
            out.append("Write the SMS text (it must match your DLT-approved template in India).")
        elif len(text) > MAX_SMS:
            out.append(f"Keep the SMS under {MAX_SMS} characters.")
        out += _check_text_placeholders(text, [*TEXT_PLACEHOLDERS, *extra_vars], "The SMS")
    elif t == "linkedin_task":
        if c.get("mode", "task") not in LINKEDIN_MODES:
            out.append("Choose 'start the LinkedIn sequence' or 'one task with my message'.")
        elif c.get("mode", "task") == "task":
            message = str(c.get("message") or "").strip()
            if not message:
                out.append("Write the LinkedIn message the person will send by hand.")
            elif len(message) > MAX_NOTE:
                out.append(f"Keep the LinkedIn message under {MAX_NOTE} characters.")
            out += _check_text_placeholders(message, [*TEXT_PLACEHOLDERS, *extra_vars], "The LinkedIn message")
    elif t in ("call_task", "meeting_link"):
        if len(str(c.get("note") or "")) > MAX_NOTE:
            out.append(f"Keep the note under {MAX_NOTE} characters.")
        out += _check_text_placeholders(c.get("note"), [*TEXT_PLACEHOLDERS, *extra_vars], "The note")
        out += _owner_errors(c, team_names)
    elif t == "set_stage" and c.get("stage") not in ALL_STAGES:
        out.append("Pick the stage to move the lead to.")
    elif t == "add_tag":
        tag = str(c.get("tag") or "").strip()
        if not tag or len(tag) > 40:
            out.append("Write a tag of 1 to 40 characters (for example 'diwali-hiring').")
    elif t == "notify":
        if not str(c.get("message") or "").strip():
            out.append("Write the message for the person to be notified.")
        elif len(str(c.get("message"))) > MAX_NOTE:
            out.append(f"Keep the message under {MAX_NOTE} characters.")
        out += _check_text_placeholders(c.get("message"), [*TEXT_PLACEHOLDERS, *extra_vars], "The message")
        out += _owner_errors(c, team_names, key="to")
    elif t == "webhook":
        out += _check_webhook_url(str(c.get("url") or ""))
    return out


def _owner_errors(c: dict, team_names, key: str = "owner") -> list:
    owner = str(c.get(key) or "").strip()
    if owner and team_names is not None and owner not in team_names:
        return [f"{owner} is not in your team list (Tasks → Team). Pick someone from the list."]
    return []


def _if_errors(c: dict) -> list:
    field, op = c.get("field"), c.get("op")
    if field not in IF_FIELDS:
        return ["Pick what to check (score, stage, city, has consent…)."]
    if op not in IF_OPS:
        return ["Pick how to compare (is, is not, more than, less than, contains, is one of, is filled in)."]
    kind, value = IF_FIELDS[field], c.get("value")
    if op in ("gt", "lt") and kind != "number":
        return ["'More than' and 'less than' only work with numbers (score, open roles)."]
    if op in ("gt", "lt"):
        try:
            float(value)
        except (TypeError, ValueError):
            return ["Type a number to compare with (for example 60)."]
    if kind == "bool" and op in ("eq", "ne") and str(value).lower() not in ("true", "false", "yes", "no"):
        return ["Choose yes or no."]
    if op not in ("exists",) and kind != "bool" and (value is None or str(value).strip() == "" or value == []):
        return ["Type the value to compare with (for example Pune, or hot)."]
    return []


def _email_errors(c: dict, extra_vars=()) -> list:
    out = []
    subject, body = str(c.get("subject") or "").strip(), str(c.get("body") or "").strip()
    if not subject:
        out.append("Write the email subject.")
    elif len(subject) > MAX_SUBJECT:
        out.append(f"Keep the subject under {MAX_SUBJECT} characters.")
    if not body:
        out.append("Write the email text.")
    elif len(body) > MAX_BODY:
        out.append(f"Keep the email under {MAX_BODY} characters.")
    elif "unsub_link" not in _placeholders(body):
        out.append("Every email needs an unsubscribe: add {{unsub_link}} to the text.")
    for key in ("subject_no_role", "body_no_role"):
        if c.get(key) and len(str(c[key])) > MAX_BODY:
            out.append("The no-role version is too long.")
    if c.get("body_no_role") and "unsub_link" not in _placeholders(c["body_no_role"]):
        out.append("The no-role version also needs {{unsub_link}}.")
    text = " ".join(str(c.get(k) or "") for k in ("subject", "body", "subject_no_role", "body_no_role"))
    return out + _check_text_placeholders(text, [*EMAIL_PLACEHOLDERS, *extra_vars], "The email")


def _cycle_errors(nodes: list, edges: list) -> list:
    """Every loop must pass through a wait step that really waits; otherwise a lead could spin forever."""
    def waits(node):
        c = node.get("config") or {}
        if c.get("disabled"):
            return False                        # a step that is turned off is skipped, so it never makes anyone wait
        if node["type"] == "wait_for_reply":
            return True
        return node["type"] == "wait" and (_int_in(c.get("days", 0), 0, 60) or _int_in(c.get("hours", 0), 0, 23))
    keep = {n["id"] for n in nodes if not waits(n)}
    graph = {nid: [] for nid in keep}
    for e in edges:
        if e["from"] in keep and e["to"] in keep:
            graph[e["from"]].append(e["to"])
    state, found = {}, []

    def visit(nid, stack):
        state[nid] = 1
        stack.append(nid)
        for nxt in graph[nid]:
            if state.get(nxt) == 1 and not found:
                found.append(stack[stack.index(nxt):])
            elif nxt not in state:
                visit(nxt, stack)
        stack.pop()
        state[nid] = 2
    for nid in graph:
        if nid not in state and not found:
            visit(nid, [])
    if not found:
        return []
    return [_problem("This step is in a loop with no Wait step. Add a Wait (days or hours) inside the loop so a lead "
                     "can never go round it without pause.", node=found[0][0])]


def structure_errors(nodes: list, edges: list) -> list:
    """Problems that stop a flow from being saved at all."""
    out = []
    ids = [n["id"] for n in nodes]
    by_id = {n["id"]: n for n in nodes}
    for nid in {i for i in ids if ids.count(i) > 1}:
        out.append(_problem(f"Two steps share the id {nid}.", node=nid))
    for n in nodes:
        if n["type"] not in NODE_TYPES:
            out.append(_problem(f"Unknown step type: {n['type']}.", node=n["id"]))
    seen_ports, edge_ids = set(), set()
    for e in edges:
        if e["id"] in edge_ids:
            out.append(_problem(f"Two connections share the id {e['id']}.", edge=e["id"]))
        edge_ids.add(e["id"])
        if e["from"] not in by_id or e["to"] not in by_id:
            out.append(_problem("This connection points to a step that does not exist (dangling connection).", edge=e["id"]))
            continue
        if e["from"] == e["to"]:
            out.append(_problem("A step cannot connect to itself.", node=e["from"], edge=e["id"]))
            continue
        source, target = by_id[e["from"]], by_id[e["to"]]
        if e["label"] not in outputs_of(source["type"]):
            allowed = ", ".join(outputs_of(source["type"])) or "none"
            out.append(_problem(f"'{title_of(source)}' has no '{e['label']}' output (it has: {allowed}).", node=source["id"], edge=e["id"]))
        if target["type"] == "note":
            out.append(_problem("A note is only a comment: nothing can connect to it.", node=target["id"], edge=e["id"]))
        if is_trigger(target["type"]):
            out.append(_problem("Nothing can lead into a trigger; the trigger is where the flow starts.", node=target["id"], edge=e["id"]))
        port = (e["from"], e["label"])
        if port in seen_ports:
            out.append(_problem(f"The '{e['label']}' output of '{title_of(source)}' is connected twice. Keep one.", node=source["id"], edge=e["id"]))
        seen_ports.add(port)
    if not out:
        out += _cycle_errors(nodes, edges)
    return out


def validate_graph(nodes: list, edges: list, team_names=None) -> list:
    """Every problem with a flow, each tied to a step or a connection. Empty when it can be activated."""
    out = structure_errors(nodes, edges)
    if any(p["message"].startswith(("Two steps", "Unknown step")) for p in out):
        return out
    triggers = [n for n in nodes if is_trigger(n["type"])]
    if not triggers:
        out.append(_problem("Add a trigger: every flow starts with one (for example 'Added by hand' or 'They reply')."))
    elif len(triggers) > 1:
        for extra in triggers[1:]:
            out.append(_problem("Use one trigger per flow. Make a second flow for a different start.", node=extra["id"]))
    names = flow_var_names(nodes)
    for n in nodes:
        out += [_problem(m, node=n["id"]) for m in config_errors(n, team_names, names)]
    if triggers:
        reach, todo = {triggers[0]["id"]}, [triggers[0]["id"]]
        while todo:
            current = todo.pop()
            for e in edges:
                if e["from"] == current and e["to"] not in reach:
                    reach.add(e["to"])
                    todo.append(e["to"])
        for n in nodes:
            if n["id"] not in reach and not is_trigger(n["type"]) and n["type"] != "note":
                out.append(_problem("This step is not connected to the trigger, so no lead can reach it.", node=n["id"]))
        if not any(e["from"] == triggers[0]["id"] for e in edges):
            out.append(_problem("Connect the trigger to the first step.", node=triggers[0]["id"]))
    return out


def validate_flow(flow: dict, team_names=None) -> list:
    return validate_graph(flow.get("nodes") or [], flow.get("edges") or [], team_names)


# ---------------------------------------------------------------- building and saving
def build_flow(name: str, description: str = "", nodes=None, edges=None, settings=None) -> dict:
    """A new draft. Raises FlowError when the graph cannot be saved."""
    nodes, edges = normalise_graph(nodes if nodes is not None else blank_nodes(), edges or [])
    problems = structure_errors(nodes, edges)
    if problems:
        raise FlowError(problems[0]["message"])
    stamp = now_iso()
    return {"id": new_id("f"), "name": clean_name(name), "description": str(description or "")[:MAX_DESCRIPTION],
            "status": "draft", "version": 1, "nodes": nodes, "edges": edges, "settings": normalise_settings(settings),
            "versions": {}, "activated_at": None, "created_at": stamp, "updated_at": stamp}


def clean_name(name) -> str:
    text = re.sub(r"\s+", " ", str(name or "")).strip()[:MAX_NAME]
    return text or "Untitled flow"


def blank_nodes() -> list:
    return [{"id": "trigger", "type": "trigger.manual", "config": {}, "x": 300, "y": 60}]


def graph_of(flow: dict) -> dict:
    return {"nodes": flow["nodes"], "edges": flow["edges"]}


def graph_for_version(flow: dict, version: int) -> dict:
    """The nodes and edges a run started on. Old versions are kept so running leads finish on their version."""
    if int(version) == int(flow["version"]):
        return graph_of(flow)
    return flow.get("versions", {}).get(str(version)) or graph_of(flow)


def apply_edit(flow: dict, changes: dict, has_runs: bool, team_names=None) -> dict:
    """Return the flow with `changes` applied. Editing the steps of an active flow (or one with leads on it) makes a
    new version; leads already running keep the version they started on. An active flow must stay valid."""
    updated = dict(flow)
    if "name" in changes and changes["name"] is not None:
        updated["name"] = clean_name(changes["name"])
    if "description" in changes and changes["description"] is not None:
        updated["description"] = str(changes["description"])[:MAX_DESCRIPTION]
    if changes.get("settings") is not None:
        updated["settings"] = normalise_settings({**flow.get("settings", {}), **changes["settings"]})
    if changes.get("nodes") is not None or changes.get("edges") is not None:
        nodes, edges = normalise_graph(changes.get("nodes", flow["nodes"]) if changes.get("nodes") is not None else flow["nodes"],
                                       changes["edges"] if changes.get("edges") is not None else flow["edges"])
        problems = structure_errors(nodes, edges)
        if problems:
            raise FlowError(problems[0]["message"])
        if flow["status"] == "active":
            problems = validate_graph(nodes, edges, team_names)
            if problems:
                raise FlowError("An active flow must stay complete: " + problems[0]["message"] + " Pause it to save a draft.")
        if _steps_changed(flow, nodes, edges) and (flow["status"] == "active" or has_runs):
            versions = dict(flow.get("versions") or {})
            versions[str(flow["version"])] = copy.deepcopy(graph_of(flow))
            updated["versions"] = versions
            updated["version"] = int(flow["version"]) + 1
        updated["nodes"], updated["edges"] = nodes, edges
    updated["updated_at"] = now_iso()
    return updated


def _steps_changed(flow: dict, nodes: list, edges: list) -> bool:
    """Moving a card on the canvas is not a new version; changing what a step does or how steps connect is."""
    def key(ns, es):
        return (sorted((n["id"], n["type"], repr(sorted((n.get("config") or {}).items()))) for n in ns),
                sorted((e["from"], e["to"], e["label"]) for e in es))
    return key(flow["nodes"], flow["edges"]) != key(nodes, edges)
