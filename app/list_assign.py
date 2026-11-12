"""List assignment: who works a list. `assigned_to` is a login's name ('' = the admin pool), `shared_with` are view-only.

Assigning gives the list's leads to that person (`lead.owner`), skipping leads another member already owns unless the
admin says "take over". It only changes the owner: a running sequence (email or LinkedIn) is never touched, restarted
or re-sent; email steps simply use the new owner's sender from then on, LinkedIn keeps its LinkedIn ID.
"""
from typing import Optional

from . import auth, lead_access, lead_ops, lists, workspaces
from .lead_model import now_iso


class AssignRefused(ValueError):
    """The change is not allowed; the message says why in plain words."""


def login_named(store, name: str, ws: str) -> Optional[dict]:
    """The active login called `name` (or with that email) who may work in workspace `ws`, else None."""
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None
    for u in auth.users(store):
        if wanted in {str(u.get("name") or "").strip().lower(), str(u.get("email") or "").strip().lower()} - {""}:
            principal = auth.subject_principal(store, "", "user:" + u["email"])
            if principal is None or principal.get("account_pending"):
                return None
            allowed = workspaces.allowed_of(principal)
            return principal if allowed is None or ws in allowed else None
    return None


def display(principal: dict) -> str:
    return principal.get("name") or principal.get("email") or ""


def visible(store, user: Optional[dict], row: dict) -> bool:
    """May this login see this list? Workspace strictness always; with the switch on a member sees only lists assigned to
    them or shared with them."""
    if not lead_access.in_workspace(user, row):
        return False
    ws = workspaces.ws_of(row)
    if not lead_access.ownership_on(store, ws) or user is None:
        return True
    if user.get("role") == "viewer":          # read-only: lists of their own team's people
        return lead_access.matches(user, row.get("assigned_to")) or str(row.get("assigned_to") or "").strip().lower() in lead_access.circle(store, user, ws)
    return (lead_access.is_admin(user, row) or lead_access.matches(user, row.get("assigned_to"))
            or any(lead_access.matches(user, n) for n in row.get("shared_with") or []))


def may_work(store, user: Optional[dict], row: dict) -> bool:
    """Start outreach / change members: admins, and the assignee (a view-only share cannot)."""
    if not visible(store, user, row):
        return False
    if not lead_access.ownership_on(store, workspaces.ws_of(row)) or user is None:
        return True
    return lead_access.is_admin(user, row) or lead_access.matches(user, row.get("assigned_to"))


def pool(store, ws: str) -> list:
    """The admin pool: lists of a workspace nobody is assigned to."""
    return [r for r in store.read("lists") if workspaces.ws_of(r) == ws and not r.get("assigned_to")]


def _update(store, list_id: str, fn) -> Optional[dict]:
    return lists._change(store, list_id, fn)


def _stamp(row: dict, to: str, by: str, text: str) -> dict:
    return {**row, "assigned_to": to, "assigned_at": now_iso(), "assigned_by": by, "log": [*row["log"], lists._log(text, by)][-lists.MAX_LOG:]}


def _give(store, lead_ids: list, person: dict, by: str, why: str, take_over: bool) -> dict:
    """Make `person` the owner of these leads; leads another person owns are skipped (reported) unless take_over."""
    to = display(person)
    by_id = {l["id"]: l for l in store.read("leads")}
    moved, skipped = 0, []
    for lid in lead_ids:
        lead = by_id.get(lid)
        if lead is None:
            continue
        current = str(lead.get("owner") or "").strip()
        if current and not lead_access.matches(person, current) and not take_over:
            skipped.append({"lead_id": lid, "owner": current})
            continue
        if lead_ops.set_owner(store, lid, to, by, why) is not None and current.lower() != to.lower():
            moved += 1
            lead_ops.tell_reassigned(store, lead, current, to, by)
    return {"moved": moved, "skipped": skipped}


def assign(store, list_id: str, to: str, by: str, take_over: bool = False) -> dict:
    """Give a list to a login (`to`), or back to the admin pool (`to` = ''; lead owners are left as they are)."""
    row = lists.get(store, list_id)
    if row is None:
        raise LookupError("List not found")
    ws = workspaces.ws_of(row)
    if not to:
        _update(store, list_id, lambda r: _stamp(r, "", by, "Moved to the admin pool"))
        return {"moved": 0, "skipped": [], "list": lists.get(store, list_id)}
    person = login_named(store, to, ws)
    if person is None:
        raise AssignRefused(f"{to} is not an active login of this workspace.")
    name = display(person)
    result = _give(store, row["lead_ids"], person, by, f"list {row['name']}", take_over)
    _update(store, list_id, lambda r: _stamp(r, name, by, f"Assigned to {name}: {result['moved']} leads moved"
                                                          + (f", {len(result['skipped'])} owned by someone else left alone" if result["skipped"] else "")))
    return {**result, "list": lists.get(store, list_id)}


def share(store, list_id: str, users: list, by: str) -> dict:
    row = lists.get(store, list_id)
    if row is None:
        raise LookupError("List not found")
    names = []
    for u in users or []:
        person = login_named(store, u, workspaces.ws_of(row))
        if person is None:
            raise AssignRefused(f"{u} is not an active login of this workspace.")
        if display(person) not in names:
            names.append(display(person))
    return _update(store, list_id, lambda r: {**r, "shared_with": names, "log": [*r["log"], lists._log(
        "Shared (view only) with " + (", ".join(names) or "nobody"), by)][-lists.MAX_LOG:]})


def split(store, list_id: str, members: list, by: str, mode: str = "even", counts: Optional[list] = None,
          take_over: bool = False) -> dict:
    """Cut a list between members into new lists, one each (the source list stays). Leads another member owns are left out
    and reported. mode 'even' shares the rest as evenly as possible; 'count' takes counts[i] leads for members[i]."""
    row = lists.get(store, list_id)
    if row is None:
        raise LookupError("List not found")
    if not members:
        raise AssignRefused("Pick at least one person.")
    ws = workspaces.ws_of(row)
    people, principals = [], []
    for m in members:
        person = login_named(store, m, ws)
        if person is None:
            raise AssignRefused(f"{m} is not an active login of this workspace.")
        people.append(display(person))
        principals.append(person)
    if len(set(p.lower() for p in people)) != len(people):
        raise AssignRefused("Each person can be picked once.")
    if mode == "count":
        if not counts or len(counts) != len(people) or any((not isinstance(c, int)) or c < 0 for c in counts):
            raise AssignRefused("Give one whole number per person.")
    elif mode != "even":
        raise AssignRefused("mode must be 'even' or 'count'.")
    by_id = {l["id"]: l for l in store.read("leads")}
    skipped, free = [], []
    for lid in row["lead_ids"]:
        lead = by_id.get(lid)
        if lead is None:
            continue
        owner = str(lead.get("owner") or "").strip()
        if owner and not take_over and owner.lower() not in {p.lower() for p in people}:
            skipped.append({"lead_id": lid, "owner": owner})
        else:
            free.append(lid)
    if mode == "count":
        if sum(counts) > len(free):
            raise AssignRefused(f"Only {len(free)} leads can be handed out; you asked for {sum(counts)}.")
        sizes = list(counts)
    else:
        base, extra = divmod(len(free), len(people))
        sizes = [base + (1 if i < extra else 0) for i in range(len(people))]
    made, at = [], 0
    for person, principal, size in zip(people, principals, sizes):
        chunk = free[at:at + size]
        at += size
        if not chunk:
            continue
        new = lists.create(store, f"{row['name']} ({person})", chunk, by, f"split from {row['name']}", assigned_to=person, workspace=ws)
        _give(store, chunk, principal, by, f"list {new['name']}", True)
        made.append({"list_id": new["id"], "assigned_to": person, "leads": len(chunk)})
    _update(store, list_id, lambda r: {**r, "log": [*r["log"], lists._log(f"Split into {len(made)} lists", by)][-lists.MAX_LOG:]})
    return {"lists": made, "skipped": skipped, "left_over": len(free) - at}
