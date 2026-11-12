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


def circle(store, user: Optional[dict], ws: str) -> set:
    """Lower-case names and emails of every member of the teams `user` belongs to in workspace `ws` (the viewer's team).
    Empty when the person is in no team: a viewer then sees nothing."""
    from . import assignment, auth
    mine = _names(user or {})
    users = auth.users(store)
    out = set()
    for t in assignment.config(store, ws)["teams"]:
        members = {str(m).strip().lower() for m in t["members"]}
        for m in list(members):
            login = assignment._login_for(users, m)
            if login:
                members |= _names(login)
        if mine & members:
            out |= members
    return out


def can_see(store, user: Optional[dict], lead: dict) -> bool:
    """Viewers are read-only: with ownership on they see their own leads plus those of the people in their team (none if no team)."""
    if not in_workspace(user, lead):
        return False
    ws = workspaces.ws_of(lead)
    if not ownership_on(store, ws) or user is None:
        return True
    if is_admin(user, lead) or owns(user, lead):
        return True
    if (user or {}).get("role") == "viewer":
        return str(lead.get("owner") or "").strip().lower() in circle(store, user, ws)
    return False


class Hider:
    """One request's ownership filter: `hider(lead)` is True when the lead is hidden from `user`, `hider.list(row)` the same for a list."""

    def __init__(self, store, user, on):
        self.store, self.user, self.on, self.circles, self.admin = store, user, on, {}, {}
        self.me = _names(user or {})
        self.viewer = (user or {}).get("role") == "viewer"

    def _admin(self, ws):
        if ws not in self.admin:
            self.admin[ws] = is_admin(self.user, ws)
        return self.admin[ws]

    def _circle(self, ws):
        if ws not in self.circles:
            self.circles[ws] = circle(self.store, self.user, ws)
        return self.circles[ws]

    def __call__(self, row) -> bool:
        ws = workspaces.ws_of(row)
        if ws not in self.on or self._admin(ws) or str(row.get("owner") or "").strip().lower() in self.me:
            return False
        if self.viewer:
            return str(row.get("owner") or "").strip().lower() not in self._circle(ws)
        return True

    def list(self, row) -> bool:
        ws = workspaces.ws_of(row)
        if ws not in self.on or self._admin(ws):
            return False
        mine = matches(self.user, row.get("assigned_to")) or any(matches(self.user, n) for n in row.get("shared_with") or [])
        if mine:
            return False
        if (self.user or {}).get("role") == "viewer":
            return str(row.get("assigned_to") or "").strip().lower() not in self._circle(ws)
        return True


def hidden_test(store, user: Optional[dict]):
    """The request's ownership filter (a Hider), or None when nothing is hidden (switch off everywhere, or no person)."""
    on = {w for w, v in modes(store).items() if v}
    return Hider(store, user, on) if on and user is not None else None


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
