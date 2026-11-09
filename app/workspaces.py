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


def all_workspaces(store, include_requested: bool = False) -> list:
    """Every workspace (each with its account_id; "main" belongs to Mas Callnet). A workspace a client admin asked for
    stays out (status "requested") until an owner approves it, unless include_requested."""
    extra = [{**w, "account_id": w.get("account_id") or "mcn"} for w in store.read("settings").get("workspaces") or []
             if isinstance(w, dict) and w.get("id") != MAIN and (include_requested or (w.get("status") or "active") == "active")]
    return [{"id": MAIN, "name": "Main", "created_by": "", "created_at": "", "account_id": "mcn"}, *extra]


def allowed_of(user: Optional[dict]) -> Optional[set]:
    """The workspaces a login may see (None = every one). Cut to the login's account: a client login (account_id other
    than "mcn") only ever gets workspaces of its own account and never None; `account_ws` is put on the signed-in
    principal by auth.subject_principal (accounts.login_account_ws)."""
    user = user or {}
    boundary = user.get("account_ws")
    client = (user.get("account_id") or "mcn") != "mcn"
    if client:
        mine = set(user.get("workspaces") or []) if not user.get("no_workspace") else set()
        return mine & set(boundary) if boundary is not None else mine
    if user.get("role") != "owner" and user.get("no_workspace"):
        return set()                                # lost its last workspace (deleted, or removed): sees nothing until given one
    if user.get("role") == "admin" and not user.get("workspaces"):
        return set()                                # a workspace admin always has workspaces; without one it sees nothing
    if user.get("role") == "owner" or not user.get("workspaces"):
        return None if boundary is None or user.get("role") == "owner" else set(boundary)
    return set(user["workspaces"]) & set(boundary) if boundary is not None else set(user["workspaces"])


async def scope_dependency(request: Request) -> None:
    """Global dependency (async so the scope reaches the endpoint's thread). Owners may pick an account (X-Account):
    their lists then show only that account's workspaces."""
    from . import accounts
    user = getattr(request.state, "user", None)
    allowed = allowed_of(user)
    account, client = None, False
    if user is not None and accounts.is_client(user):
        account, client = accounts.of_user(user), True
    elif user is not None and allowed is None:
        picked = (request.headers.get("x-account") or "").strip()[:40]
        store = getattr(request.app.state, "store", None)
        if picked and picked != "all" and store is not None and accounts.find(store, picked) is not None:
            account, allowed = picked, accounts.workspaces_of(store, picked)
    elif user is not None:
        account = "mcn"
    raw = (request.headers.get("x-workspace") or "").strip()[:40]
    selected = raw if raw and raw != "all" else None
    if allowed is not None and selected and selected not in allowed:
        selected = sorted(allowed)[0] if allowed else None
    if account is None and selected:
        store = getattr(request.app.state, "store", None)
        account = accounts.account_of_ws(store, selected) if store is not None else None
    accounts.set_current(account, client)
    _SCOPE.set((selected, allowed))
    request.state.workspace_scope = (selected, allowed)


def visible(row: dict) -> bool:
    selected, allowed = _SCOPE.get()
    ws = ws_of(row)
    return (allowed is None or ws in allowed) and (selected is None or ws == selected)


def filter_rows(rows) -> list:
    return [r for r in rows if visible(r)]


def hidden_ids(leads) -> set:
    """Ids of leads outside this request's scope, so tasks, call queues and summaries tied to them can be left out
    (a task for a deleted lead, or one with no lead, is not hidden)."""
    selected, allowed = _SCOPE.get()
    if selected is None and allowed is None:
        return set()
    return {lead["id"] for lead in leads if isinstance(lead, dict) and lead.get("id") and not visible(lead)}


def filter_tasks(tasks, leads) -> list:
    """Rows tied to a lead (tasks, notifications) this request may see. A client login also never sees a row with no
    lead (a team-wide task or bell entry of Mas Callnet) unless the row is stamped with its own account."""
    from . import accounts
    if accounts.current_is_client():
        ids = {l["id"] for l in leads if isinstance(l, dict) and l.get("id") and visible(l)}
        return [t for t in tasks if t.get("lead_id") in ids or (not t.get("lead_id") and accounts.visible_row(t))]
    hidden = hidden_ids(leads)
    return [t for t in tasks if t.get("lead_id") not in hidden] if hidden else list(tasks)


def for_new_lead() -> str:
    selected, allowed = _SCOPE.get()
    return selected or (sorted(allowed)[0] if allowed else MAIN)


def stamp_new(lead: dict) -> dict:
    ws = for_new_lead()
    return lead if ws == MAIN or not isinstance(lead, dict) else {**lead, "workspace": ws}


def create(store, name: str, by: str, account_id: str = "mcn", status: str = "active", requested_by: str = "") -> dict:
    name = " ".join(str(name or "").split())[:60]
    if not name:
        raise ValueError("Give the workspace a name.")
    existing = all_workspaces(store, include_requested=True)
    if any(w["name"].lower() == name.lower() and w["account_id"] == (account_id or "mcn") for w in existing):    # per account
        raise ValueError("A workspace with that name already exists.")
    if len(existing) >= MAX_WORKSPACES:
        raise ValueError(f"At most {MAX_WORKSPACES} workspaces.")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "ws"
    row = {"id": f"{slug}-{uuid.uuid4().hex[:4]}", "name": name, "created_by": by, "created_at": now_iso(), "account_id": account_id or "mcn"}
    if status != "active":
        row.update(status=status, requested_by=requested_by)
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
    from . import accounts
    names = {w["id"]: w["name"] for w in all_workspaces(store)}
    if ws_id not in names:
        raise LookupError("No such workspace.")
    wanted, moved = set(lead_ids), 0
    stamp = now_iso()
    amap = accounts.ws_account_map(store)
    target = amap.get(ws_id, "mcn")
    keys = {l.get("dedupe_key") for l in store.read("leads") if accounts.account_of_lead(amap, l) == target}
    for l in store.read("leads"):
        if accounts.account_of_lead(amap, l) != target and l.get("dedupe_key") in keys:
            continue                                   # another account's lead would become a duplicate there
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


def scoped() -> bool:
    """This request sees only some workspaces (a limited login, or a workspace picked in the menu)."""
    selected, allowed = _SCOPE.get()
    return selected is not None or allowed is not None


def visible_lead_ids(store) -> Optional[set]:
    """Ids of leads this request may see, or None when it may see everything."""
    if not scoped():
        return None
    return {l["id"] for l in store.read("leads") if isinstance(l, dict) and l.get("id") and visible(l)}


def filter_by_lead(store, rows, key: str = "lead_id") -> list:
    """Rows tied to a lead (outbox, inbox, meetings, activities) that this request may see. In a scoped request a row
    with no lead (an unmatched email) is hidden too, since nothing says whose it is."""
    ids = visible_lead_ids(store)
    rows = list(rows or [])
    return rows if ids is None else [r for r in rows if isinstance(r, dict) and r.get(key) in ids]


def lead_visible(store, lead_id) -> bool:
    ids = visible_lead_ids(store)
    return ids is None or lead_id in ids


def only_visible_ids(store, lead_ids) -> list:
    """The ids from a request body that this request may act on (others are dropped silently, as if missing)."""
    ids = visible_lead_ids(store)
    return [i for i in (lead_ids or []) if ids is None or i in ids]
