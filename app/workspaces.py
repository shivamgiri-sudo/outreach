"""Workspaces: keep each client's leads, lists and numbers apart.

A lead belongs to one workspace (`workspace`, missing = "main"). The web app sends the chosen workspace in the
X-Workspace header; a global dependency turns it (and the signed-in person's allowed workspaces) into a scope for this
request. Lead lists, lead details, lists, the company picker and Intelligence only show leads in scope, and leads made
during the request (imports, finders) go into the chosen workspace. Owners see every workspace; a caller or viewer can
be limited to some.
"""
import contextvars
import re
import uuid
from typing import Optional

from fastapi import Request

from .lead_model import now_iso

MAIN = "main"
MAX_WORKSPACES = 50
_SCOPE = contextvars.ContextVar("workspace_scope", default=(None, None))     # (selected id or None, allowed set or None)


def ws_of(row: dict) -> str:
    return (row or {}).get("workspace") or MAIN


def all_workspaces(store) -> list:
    extra = [w for w in store.read("settings").get("workspaces") or [] if isinstance(w, dict) and w.get("id") != MAIN]
    return [{"id": MAIN, "name": "Main", "created_by": "", "created_at": ""}, *extra]


def allowed_of(user: Optional[dict]) -> Optional[set]:
    user = user or {}
    if user.get("role") == "owner" or not user.get("workspaces"):
        return None
    return set(user["workspaces"])


async def scope_dependency(request: Request) -> None:
    """Global dependency (async so the scope reaches the endpoint's thread)."""
    user = getattr(request.state, "user", None)
    allowed = allowed_of(user)
    raw = (request.headers.get("x-workspace") or "").strip()[:40]
    selected = raw if raw and raw != "all" else None
    if allowed is not None and selected and selected not in allowed:
        selected = sorted(allowed)[0]
    _SCOPE.set((selected, allowed))
    request.state.workspace_scope = (selected, allowed)


def visible(row: dict) -> bool:
    selected, allowed = _SCOPE.get()
    ws = ws_of(row)
    return (allowed is None or ws in allowed) and (selected is None or ws == selected)


def filter_rows(rows) -> list:
    return [r for r in rows if visible(r)]


def for_new_lead() -> str:
    selected, allowed = _SCOPE.get()
    return selected or (sorted(allowed)[0] if allowed else MAIN)


def stamp_new(lead: dict) -> dict:
    ws = for_new_lead()
    return lead if ws == MAIN or not isinstance(lead, dict) else {**lead, "workspace": ws}


def create(store, name: str, by: str) -> dict:
    name = " ".join(str(name or "").split())[:60]
    if not name:
        raise ValueError("Give the workspace a name.")
    existing = all_workspaces(store)
    if any(w["name"].lower() == name.lower() for w in existing):
        raise ValueError("A workspace with that name already exists.")
    if len(existing) >= MAX_WORKSPACES:
        raise ValueError(f"At most {MAX_WORKSPACES} workspaces.")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "ws"
    row = {"id": f"{slug}-{uuid.uuid4().hex[:4]}", "name": name, "created_by": by, "created_at": now_iso()}
    store.update("settings", lambda s: {**s, "workspaces": [*(s.get("workspaces") or []), row]})
    return row


def delete(store, ws_id: str) -> None:
    if ws_id == MAIN:
        raise ValueError("The main workspace cannot be removed.")
    if not any(w["id"] == ws_id for w in all_workspaces(store)):
        raise LookupError("No such workspace.")
    if any(ws_of(l) == ws_id for l in store.read("leads")):
        raise ValueError("Move or delete this workspace's leads first.")
    store.update("settings", lambda s: {**s, "workspaces": [w for w in s.get("workspaces") or [] if w.get("id") != ws_id]})


def move(store, lead_ids: list, ws_id: str, by: str) -> int:
    names = {w["id"]: w["name"] for w in all_workspaces(store)}
    if ws_id not in names:
        raise LookupError("No such workspace.")
    wanted, moved = set(lead_ids), 0
    stamp = now_iso()
    for l in store.read("leads"):
        if l["id"] in wanted and ws_of(l) != ws_id and visible(l):
            store.update_lead(l["id"], lambda x: {**x, "workspace": ws_id, "timeline": [
                *x["timeline"], {"at": stamp, "kind": "edit", "text": f"Moved to workspace {names[ws_id]} (was {names.get(ws_of(x), ws_of(x))})", "by": by}]})
            moved += 1
    return moved


def counts(store) -> dict:
    out = {}
    for l in store.read("leads"):
        out[ws_of(l)] = out.get(ws_of(l), 0) + 1
    return out
