"""Starter flows built from the logic the app already has, so a team can start from something that works.

1. Cold email 6-step: the built-in sequence's six emails (Day 1, 4, 8, 12, 16, 21), stopping on any reply.
2. Yes -> WhatsApp + Priya call: WhatsApp only with recorded consent, a call task, a nudge if no meeting in 2 days.
3. LinkedIn + email combo: a LinkedIn task first (a person sends it), then email; a yes goes to Priya.
`POST /api/flows/templates/{key}` saves a draft copy that can be edited freely.
"""
from .flows import build_flow
from .sequences import DEFAULT_SEQUENCE


def _column(specs: list, x: int = 300, top: int = 60, gap: int = 130) -> list:
    """Steps one under the other: [(id, type, config), ...]."""
    return [{"id": nid, "type": ntype, "config": config, "x": x, "y": top + i * gap} for i, (nid, ntype, config) in enumerate(specs)]


def _email_config(step: dict) -> dict:
    keys = ("subject", "body", "subject_no_role", "body_no_role")
    return {"title": step["name"], **{k: step[k] for k in keys if step.get(k)}}


def _edge(source: str, target: str, label: str = "next") -> dict:
    return {"id": f"e_{source}_{label}_{target}", "from": source, "to": target, "label": label}


def _chain(ids: list) -> list:
    return [_edge(a, b) for a, b in zip(ids, ids[1:])]


def _cold_email_flow() -> dict:
    s = DEFAULT_SEQUENCE
    nodes = _column([
        ("trigger", "trigger.manual", {"title": "Added by hand or in bulk"}),
        ("email1", "email", _email_config(s[0])),
        ("wait1", "wait", {"days": 3, "hours": 0, "business_hours": True}),
        ("email2", "email", _email_config(s[1])),
        ("wait2", "wait", {"days": 4, "hours": 0, "business_hours": True}),
        ("email3", "email", _email_config(s[2])),
        ("wait3", "wait", {"days": 4, "hours": 0, "business_hours": True}),
        ("email4", "email", _email_config(s[3])),
        ("wait4", "wait", {"days": 4, "hours": 0, "business_hours": True}),
        ("email5", "email", _email_config(s[4])),
        ("wait5", "wait", {"days": 5, "hours": 0, "business_hours": True}),
        ("email6", "email", _email_config(s[5])),
    ])
    return {"name": "Cold email 6-step", "settings": {"stop_on_reply": True},
            "description": "The same six emails as the built-in executive sequence (Day 1, 4, 8, 12, 16, 21). Stops as soon as they reply.",
            "nodes": nodes, "edges": _chain([n["id"] for n in nodes])}


def _yes_flow() -> dict:
    nodes = [
        {"id": "trigger", "type": "trigger.reply", "config": {"label": "interested", "title": "They reply yes"}, "x": 300, "y": 60},
        {"id": "consent", "type": "if", "config": {"title": "Consent recorded?", "field": "has_consent", "op": "eq", "value": "true"}, "x": 300, "y": 190},
        {"id": "wa", "type": "whatsapp", "config": {"template": "first_after_yes"}, "x": 120, "y": 320},
        {"id": "call", "type": "call_task", "config": {"note": "They said yes. Call through PD only, before 18:55."}, "x": 300, "y": 450},
        {"id": "wait", "type": "wait", "config": {"days": 2, "hours": 0, "business_hours": True}, "x": 300, "y": 580},
        {"id": "meeting", "type": "if", "config": {"title": "No meeting yet?", "field": "has_meeting", "op": "eq", "value": "false"}, "x": 300, "y": 710},
        {"id": "notify", "type": "notify", "config": {"to": "", "message": "No meeting booked two days after their yes. Follow up today."}, "x": 300, "y": 840},
    ]
    edges = [_edge("trigger", "consent"), _edge("consent", "wa", "yes"), _edge("consent", "call", "no"),
             _edge("wa", "call"), _edge("wa", "call", "error"), _edge("call", "wait"), _edge("wait", "meeting"),
             _edge("meeting", "notify", "yes")]
    return {"name": "Yes → WhatsApp + Priya call", "settings": {"stop_on_reply": False},
            "description": "When someone replies yes: the sample-screen WhatsApp (only with consent), a call task for "
                           "Priya, and a nudge to you if no meeting is booked within two days.",
            "nodes": nodes, "edges": edges}


def _linkedin_combo_flow() -> dict:
    s = DEFAULT_SEQUENCE
    note = ("Hi {{first_name}}, came across your profile and noticed your work building teams at {{company}}. "
            "Always good to connect with people who hire. Thought I'd reach out.")
    nodes = [
        {"id": "trigger", "type": "trigger.manual", "config": {"title": "Added by hand or in bulk"}, "x": 300, "y": 60},
        {"id": "linkedin", "type": "linkedin_task", "config": {"mode": "start_sequence", "title": "Start LinkedIn sequence"}, "x": 300, "y": 190},
        {"id": "linkedin_note", "type": "linkedin_task", "config": {"mode": "task", "title": "Find profile and connect", "message": note}, "x": 560, "y": 250},
        {"id": "wait1", "type": "wait", "config": {"days": 3, "hours": 0, "business_hours": True}, "x": 300, "y": 320},
        {"id": "email1", "type": "email", "config": _email_config(s[0]), "x": 300, "y": 450},
        {"id": "reply", "type": "wait_for_reply", "config": {"days": 4}, "x": 300, "y": 580},
        {"id": "call", "type": "call_task", "config": {"note": "Said yes after the LinkedIn + email combo. Call through PD only."}, "x": 40, "y": 710},
        {"id": "email2", "type": "email", "config": _email_config(s[1]), "x": 300, "y": 710},
        {"id": "wait2", "type": "wait", "config": {"days": 7, "hours": 0, "business_hours": True}, "x": 300, "y": 840},
        {"id": "email3", "type": "email", "config": _email_config(s[3]), "x": 300, "y": 970},
    ]
    edges = [_edge("trigger", "linkedin"), _edge("linkedin", "wait1"), _edge("linkedin", "linkedin_note", "error"),
             _edge("linkedin_note", "wait1"), _edge("wait1", "email1"), _edge("email1", "reply"),
             _edge("reply", "call", "interested"), _edge("reply", "email2", "timeout"), _edge("email2", "wait2"),
             _edge("wait2", "email3")]
    return {"name": "LinkedIn + email combo", "settings": {"stop_on_reply": True},
            "description": "A LinkedIn connection task first (sent by a person), then email 1 after 3 days. A yes goes "
                           "to Priya; silence gets the tip email and, a week later, the permission close.",
            "nodes": nodes, "edges": edges}


TEMPLATES = {"cold_email": _cold_email_flow, "yes_whatsapp_call": _yes_flow, "linkedin_email": _linkedin_combo_flow}


def template_list() -> list:
    return [{"key": key, "name": make()["name"], "description": make()["description"]} for key, make in TEMPLATES.items()]


def flow_from_template(key: str) -> dict:
    if key not in TEMPLATES:
        raise LookupError("Unknown template")
    spec = TEMPLATES[key]()
    return build_flow(spec["name"], spec["description"], spec["nodes"], spec["edges"], spec["settings"])
