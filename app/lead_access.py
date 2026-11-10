"""Who may see / send to a lead. One owner per lead; admins of the lead's workspace see all; other workspaces never.
Everything is off (old behaviour) until the workspace switch ownership_mode is on.

`lead["owner"]` is a display name or email string. A login owns a lead when that string (case-insensitive) is the
login's name or email. The per-workspace switch lives in settings["ownership_mode"] = {workspace_id: True}.
"""
from typing import Optional

from . import workspaces

NO_OWNER = "This lead has no owner yet, so automation does not message it. An admin assigns it first."
KEY = "ownership_mode"          # settings[KEY] = {workspace_id: True}


def modes(store) -> dict:
    raw = store.read("settings").get(KEY)
    return raw if isinstance(raw, dict) else {}


def ownership_on(store, ws: str) -> bool:
    return bool(modes(store).get(ws or workspaces.MAIN))


def any_on(store) -> bool:
    return any(modes(store).values())


def _names(user: dict) -> set:
    return {str(user.get(k) or "").strip().lower() for k in ("name", "email")} - {""}


def matches(user: Optional[dict], name) -> bool:
    """True when `name` (a lead owner / list assignee string) is this login."""
    return bool(str(name or "").strip()) and str(name).strip().lower() in _names(user or {})


def owns(user: dict, lead: dict) -> bool:
    return matches(user, (lead or {}).get("owner"))


def in_workspace(user: Optional[dict], row: dict) -> bool:
    allowed = workspaces.allowed_of(user)
    return allowed is None or workspaces.ws_of(row) in allowed


def is_admin(user: Optional[dict], lead_or_ws) -> bool:
    """Owner or admin whose workspaces include the lead's. A paired-tool principal keeps the person's real role in base_role."""
    ws = lead_or_ws if isinstance(lead_or_ws, str) else workspaces.ws_of(lead_or_ws)
    allowed = workspaces.allowed_of(user)
    role = (user or {}).get("base_role") or (user or {}).get("role")
    return role in ("owner", "admin") and (allowed is None or ws in allowed)


def can_send(store, user: Optional[dict], lead: dict) -> bool:
    if not in_workspace(user, lead):
        return False
    if not ownership_on(store, workspaces.ws_of(lead)) or user is None:
        return True
    return is_admin(user, lead) or owns(user, lead)


def can_see(store, user: Optional[dict], lead: dict) -> bool:
    if not in_workspace(user, lead):
        return False
    if not ownership_on(store, workspaces.ws_of(lead)) or user is None or (user or {}).get("role") == "viewer":
        return True
    return is_admin(user, lead) or owns(user, lead)


def refusal(store, user: Optional[dict], lead: dict) -> str:
    """'' when allowed, else the message for the 409."""
    return "" if can_send(store, user, lead) else f"This lead belongs to {lead.get('owner') or 'nobody yet'}. Ask an admin to assign it to you."


def visible_leads(store, user: Optional[dict], leads) -> list:
    if not any_on(store):
        return list(leads)
    return [l for l in leads if can_see(store, user, l)]


def system_ok(store, lead: dict) -> bool:
    """A job with no person behind it (daily auto-start): with the switch on it acts only on leads that have an owner."""
    return not ownership_on(store, workspaces.ws_of(lead)) or bool(lead.get("owner"))


def check_http(store, request, lead: dict) -> None:
    """Raise 409 when the signed-in caller (or paired tool) may not send to this lead. Mode off: never raises."""
    from fastapi import HTTPException
    msg = refusal(store, getattr(request.state, "user", None), lead)
    if msg:
        raise HTTPException(409, msg)


def run_refusal(store, run: Optional[dict], lead: dict) -> str:
    """A flow run started by a named person keeps that person's rights; one started by the system (a trigger) is the
    admins' own automation but needs the lead to have an owner (replies go to the owner). '' when allowed."""
    if not ownership_on(store, workspaces.ws_of(lead)):
        return ""
    email = str((run or {}).get("enrolled_by_email") or "").strip().lower()
    if not email:
        return "" if system_ok(store, lead) else NO_OWNER
    from . import auth
    user = auth.find_user(store, email)
    if user is None:
        return "The person who started this flow is no longer a login, so it may not message this person."
    return refusal(store, user, lead)


def may_start(store, user: Optional[dict], lead: dict) -> bool:
    """For a start made by `user`, or by the system when user is None (then only leads that have an owner, once on)."""
    return can_send(store, user, lead) if user is not None else system_ok(store, lead)


def filter_queue(store, user: Optional[dict], queue: dict) -> dict:
    """Drop work-list rows (due / suggestions / waiting, each with a lead_id) of leads `user` may not send to."""
    if not any_on(store) or user is None:
        return queue
    by_id = {l["id"]: l for l in store.read("leads")}
    ok = lambda row: row.get("lead_id") not in by_id or can_send(store, user, by_id[row["lead_id"]])
    return {**queue, **{k: [r for r in queue[k] if ok(r)] for k in ("due", "suggestions", "waiting_for_acceptance") if isinstance(queue.get(k), list)}}


def system_note(store, lead: dict) -> str:
    """' (by system for <owner>)' on what a flow sent for an owned lead, once ownership is on; else ''."""
    return f" (by system for {lead['owner']})" if lead.get("owner") and ownership_on(store, workspaces.ws_of(lead)) else ""
