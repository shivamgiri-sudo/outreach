"""Managing workspaces themselves: rename, delete (with what happens to the leads), members, overview.

Rules (enforced by the routes in routes_workspaces.py):
  - owners manage every workspace; a workspace admin renames and manages members of its own workspaces only;
  - only owners delete a workspace, and only owners may delete its leads permanently (the other choice is moving
    them into another workspace); "Main" is never deleted; the workspace's name must be typed to confirm;
  - a login that loses its last workspace (workspace deleted, or removed as a member) is marked `no_workspace`: a
    non-owner with that mark sees nothing at all (workspaces.allowed_of) until an owner gives it a workspace again.
"""
from typing import Optional

from . import auth, workspaces as ws
from .lead_model import now_iso

ADMIN_GIVES = ("admin", "caller", "viewer")


def find(store, ws_id: str) -> Optional[dict]:
    return next((w for w in ws.all_workspaces(store) if w["id"] == ws_id), None)


def all_access(user: dict) -> bool:
    """Owners, and (older) non-owner logins with no workspace list and no `no_workspace` mark, see every workspace."""
    return ws.allowed_of(user) is None


def overview(store, allowed: Optional[set], include_requested: bool = False) -> list:
    """Each workspace in `allowed` (None = all) with its lead count, member count (logins limited to it), account and
    status; owners also get the workspaces client admins asked for (status "requested")."""
    from . import accounts
    leads = ws.counts(store)
    people = auth.users(store)
    names = {a["id"]: a["name"] for a in accounts.all_accounts(store)}
    out = []
    for w in ws.all_workspaces(store, include_requested=include_requested):
        if allowed is not None and w["id"] not in allowed:
            continue
        members = sum(1 for u in people if w["id"] in (u.get("workspaces") or []))
        out.append({**w, "status": w.get("status") or "active", "account_name": names.get(w["account_id"], w["account_id"]),
                    "leads": leads.get(w["id"], 0), "members": members})
    return out


def approve(store, row: dict) -> None:
    """A requested workspace becomes active; the login that asked for it (same account) gets it."""
    from . import accounts
    store.update("settings", lambda s: {**s, "workspaces": [{k: v for k, v in w.items() if k != "status"}
                                                            if isinstance(w, dict) and w.get("id") == row["id"] else w
                                                            for w in s.get("workspaces") or []]})
    asker = auth.find_user(store, row.get("requested_by") or "")
    if asker and accounts.of_user(asker) == row.get("account_id") and row["id"] not in (asker.get("workspaces") or []):
        _set_user(store, asker["email"], lambda u: {**{k: v for k, v in u.items() if k != "no_workspace"},
                                                     "workspaces": [*(u.get("workspaces") or []), row["id"]]})


def rename(store, ws_id: str, name: str) -> dict:
    name = " ".join(str(name or "").split())[:60]
    if not name:
        raise ValueError("Give the workspace a name.")
    row = find(store, ws_id)
    if row is None:
        raise LookupError("No such workspace.")
    if ws_id == ws.MAIN:
        raise ValueError("The main workspace keeps its name.")
    if any(w["name"].lower() == name.lower() and w["id"] != ws_id and w["account_id"] == row["account_id"]
           for w in ws.all_workspaces(store, include_requested=True)):
        raise ValueError("A workspace with that name already exists.")
    store.update("settings", lambda s: {**s, "workspaces": [{**w, "name": name} if isinstance(w, dict) and w.get("id") == ws_id else w
                                                            for w in s.get("workspaces") or []]})
    return {**row, "name": name}


def _set_user(store, email: str, change) -> None:
    store.update("settings", lambda s: {**s, "users": [change(u) if isinstance(u, dict) and u.get("email") == email else u
                                                       for u in s.get("users") or []]})


def _without(user: dict, ws_id: str) -> dict:
    """The login with `ws_id` taken away; losing the last one marks it `no_workspace` (sees nothing)."""
    left = [w for w in user.get("workspaces") or [] if w != ws_id]
    out = {**user, "workspaces": left}
    if not left and user.get("role") != "owner":
        out["no_workspace"] = True
    return out


def members(store, ws_id: str) -> list:
    """Logins limited to this workspace, plus those that see every workspace (marked access="all")."""
    from . import accounts
    owner_acct = accounts.account_of_ws(store, ws_id)
    out = []
    for u in auth.users(store):
        if ws_id in (u.get("workspaces") or []):
            out.append({**auth.public_user(u), "access": "member"})
        elif all_access(u) and (u.get("role") == "owner" or accounts.of_user(u) == owner_acct):
            out.append({**auth.public_user(u), "access": "all"})
    return out


def add_member(store, ws_id: str, email: str) -> dict:
    target = auth.find_user(store, email)
    if not target:
        raise LookupError("No login with that email.")
    if target.get("role") == "owner" or all_access(target):
        raise ValueError(f"{target.get('name') or target['email']} already sees every workspace.")
    if ws_id in (target.get("workspaces") or []):
        raise ValueError("Already a member of this workspace.")
    from . import accounts
    if accounts.of_user(target) != accounts.account_of_ws(store, ws_id):
        raise ValueError("That login belongs to another account.")
    updated = {**target, "workspaces": [*(target.get("workspaces") or []), ws_id]}
    updated.pop("no_workspace", None)
    _set_user(store, target["email"], lambda u: updated)
    return updated


def remove_member(store, ws_id: str, email: str) -> dict:
    target = auth.find_user(store, email)
    if not target:
        raise LookupError("No login with that email.")
    if ws_id not in (target.get("workspaces") or []):
        if all_access(target):
            raise ValueError(f"{target.get('name') or target['email']} sees every workspace; limit them in Team members.")
        raise LookupError("Not a member of this workspace.")
    updated = _without(target, ws_id)
    _set_user(store, target["email"], lambda u: updated)
    return updated


def delete(store, ws_id: str, confirm_name: str, move_to: str = "", delete_leads: bool = False, by: str = "") -> dict:
    """Delete a workspace. Its leads (and lists, import history) move to `move_to`, or with `delete_leads` the leads,
    their tasks and the workspace's lists are removed for good. Returns the counts for the audit row."""
    row = find(store, ws_id)
    if ws_id == ws.MAIN:
        raise ValueError("The main workspace cannot be deleted.")
    if row is None:
        raise LookupError("No such workspace.")
    if " ".join(str(confirm_name or "").split()).lower() != row["name"].lower():
        raise ValueError("Type the workspace name exactly to confirm.")
    if move_to and delete_leads:
        raise ValueError("Choose either moving the leads or deleting them, not both.")
    lead_ids = [l["id"] for l in store.read("leads") if isinstance(l, dict) and ws.ws_of(l) == ws_id]
    target = None
    if move_to:
        target = find(store, move_to)
        if target is None or move_to == ws_id or (target.get("account_id") or "mcn") != (row.get("account_id") or "mcn"):
            raise ValueError("Pick another workspace of the same account to move the leads into.")
    elif lead_ids and not delete_leads:
        raise ValueError(f"This workspace has {len(lead_ids)} leads: move them to another workspace or delete them.")

    stamp, moved, deleted, tasks_removed = now_iso(), 0, 0, 0
    wanted = set(lead_ids)
    if target and wanted:
        def move_all(leads):
            out = []
            for l in leads:
                if isinstance(l, dict) and l.get("id") in wanted:
                    l = {**l, "timeline": [*(l.get("timeline") or []), {"at": stamp, "kind": "edit", "by": by,
                         "text": f"Moved to workspace {target['name']} (workspace {row['name']} was deleted)"}]}
                    if move_to == ws.MAIN:
                        l.pop("workspace", None)
                    else:
                        l["workspace"] = move_to
                out.append(l)
            return out
        store.update("leads", move_all)
        moved = len(wanted)
    elif wanted:
        store.update("leads", lambda leads: [l for l in leads if not (isinstance(l, dict) and l.get("id") in wanted)])
        deleted = len(wanted)
        before = len(store.read("tasks"))
        store.update("tasks", lambda tasks: [t for t in tasks if not (isinstance(t, dict) and t.get("lead_id") in wanted)])
        tasks_removed = before - len(store.read("tasks"))

    for key in ("lists", "imports"):
        try:
            rows = store.read(key)
        except KeyError:
            continue
        if not any(isinstance(r, dict) and r.get("workspace") == ws_id for r in rows):
            continue
        if target:
            store.update(key, lambda rs: [{**r, "workspace": move_to} if isinstance(r, dict) and r.get("workspace") == ws_id else r for r in rs])
        elif key == "lists":
            store.update(key, lambda rs: [r for r in rs if not (isinstance(r, dict) and r.get("workspace") == ws_id)])

    lost, emptied = [], []
    for u in auth.users(store):
        if ws_id in (u.get("workspaces") or []):
            after = _without(u, ws_id)
            lost.append(u["email"])
            if after.get("no_workspace"):
                emptied.append(u["email"])
            _set_user(store, u["email"], lambda _u, after=after: after)
    store.update("settings", lambda s: {**s, "workspaces": [w for w in s.get("workspaces") or [] if not (isinstance(w, dict) and w.get("id") == ws_id)]})
    from . import calltracker
    calltracker.delete_connection(store, ws_id)       # its DialHalo key and webhook secret go with it
    return {"name": row["name"], "moved": moved, "moved_to": target["name"] if target else "", "deleted": deleted,
            "tasks_removed": tasks_removed, "members_removed": lost, "left_without_workspace": emptied}
