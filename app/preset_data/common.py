"""Builders shared by the industry presets (app/industry_presets.py). Content rules for every preset:
honest and plain, no invented numbers, customers or results; anything that is a claim comes from the owner's own brand
settings ({{my_value_1}}..{{my_value_3}}, {{my_product}}, {{my_cta}}), so the owner decides what is said.
"""

SIGN = "\n\n{{sign_off}}\n\nNot relevant? Unsubscribe: {{unsub_link}}"


def email(day: int, name: str, subject: str, body: str) -> dict:
    """One sequence step; the standard sign-off with the unsubscribe link is added."""
    return {"day": day, "name": name, "subject": subject, "body": "{{greeting}}\n\n" + body.strip() + SIGN}


def sequence(name: str, steps: list) -> dict:
    return {"name": name, "steps": steps}


def linkedin(connect: str, messages: list, personas: list, default_hook: str, variant_b: str) -> dict:
    """messages: [(days, purpose, text)]. The first message waits for the connection to be accepted."""
    steps = [{"n": 1, "kind": "connect", "after": "start", "days": 0, "purpose": "Connection request (no pitch)", "text": connect}]
    for i, (days, purpose, text) in enumerate(messages):
        steps.append({"n": i + 2, "kind": "message", "after": "accepted" if i == 0 else "previous", "days": days,
                      "purpose": purpose, "text": text})
    return {"steps": steps, "personas": [{"key": k, "pattern": p, "hook": h} for k, p, h in personas],
            "default_hook": default_hook, "variant_b": variant_b}


def script(lines: list, objections: list) -> dict:
    return {"script": [{"title": t, "text": x} for t, x in lines], "objections": [{"q": q, "a": a} for q, a in objections]}


def snippet(name: str, channel: str, body: str, subject: str = "") -> dict:
    return {"name": name, "channel": channel, "subject": subject, "body": body}


def _col(specs: list, x: int = 300, top: int = 60, gap: int = 130) -> list:
    return [{"id": nid, "type": t, "config": c, "x": x, "y": top + i * gap} for i, (nid, t, c) in enumerate(specs)]


def _edge(a: str, b: str, label: str = "next") -> dict:
    return {"id": f"e_{a}_{label}_{b}", "from": a, "to": b, "label": label}


def flows(industry: str, first: dict, second: dict, call_note: str) -> list:
    """Two starter flows for a preset: email -> wait -> if replied / if consent -> WhatsApp -> email, and a yes -> call."""
    nodes = [
        {"id": "trigger", "type": "trigger.manual", "config": {"title": "Added by hand or in bulk"}, "x": 300, "y": 60},
        {"id": "email1", "type": "email", "config": {"title": first["name"], "subject": first["subject"], "body": first["body"]}, "x": 300, "y": 190},
        {"id": "wait1", "type": "wait", "config": {"days": 3, "hours": 0, "business_hours": True}, "x": 300, "y": 320},
        {"id": "replied", "type": "if", "config": {"title": "Replied already?", "field": "has_replied", "op": "eq", "value": "true"}, "x": 300, "y": 450},
        {"id": "notify", "type": "notify", "config": {"to": "", "message": "They replied to the first email. Answer them personally today."}, "x": 80, "y": 580},
        {"id": "consent", "type": "if", "config": {"title": "WhatsApp consent recorded?", "field": "has_consent", "op": "eq", "value": "true"}, "x": 420, "y": 580},
        {"id": "wa", "type": "whatsapp", "config": {"template": "generic_follow_up"}, "x": 300, "y": 710},
        {"id": "wait2", "type": "wait", "config": {"days": 2, "hours": 0, "business_hours": True}, "x": 300, "y": 840},
        {"id": "email2", "type": "email", "config": {"title": second["name"], "subject": second["subject"], "body": second["body"]}, "x": 520, "y": 970},
    ]
    edges = [_edge("trigger", "email1"), _edge("email1", "wait1"), _edge("wait1", "replied"), _edge("replied", "notify", "yes"),
             _edge("replied", "consent", "no"), _edge("consent", "wa", "yes"), _edge("consent", "email2", "no"),
             _edge("wa", "wait2"), _edge("wa", "email2", "error"), _edge("wait2", "email2")]
    yes_nodes = _col([
        ("trigger", "trigger.reply", {"label": "interested", "title": "They reply yes"}),
        ("consent", "if", {"title": "WhatsApp consent recorded?", "field": "has_consent", "op": "eq", "value": "true"}),
        ("wa", "whatsapp", {"template": "generic_after_yes"}),
        ("call", "call_task", {"note": call_note}),
    ])
    yes_edges = [_edge("trigger", "consent"), _edge("consent", "wa", "yes"), _edge("consent", "call", "no"),
                 _edge("wa", "call"), _edge("wa", "call", "error")]
    return [
        {"key": "email_whatsapp", "name": f"{industry}: email + WhatsApp follow-up", "settings": {"stop_on_reply": True},
         "description": "First email, wait 3 days; if they replied you are told, otherwise a WhatsApp follow-up (only with "
                        "recorded consent) and the second email.", "nodes": nodes, "edges": edges},
        {"key": "yes_call", "name": f"{industry}: yes → WhatsApp + call", "settings": {"stop_on_reply": False},
         "description": "When someone replies yes: a WhatsApp thank-you (only with consent) and a call task for the team.",
         "nodes": yes_nodes, "edges": yes_edges},
    ]
