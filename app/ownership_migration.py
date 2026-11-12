"""Moving a workspace to lead ownership without surprises. Nothing here is silent or automatic for leads:

1. `migrate_lists` gives every existing list (one without an `assigned_to` field) to its creator when that name is an
   active login who may work in the list's workspace, else to the admin pool (""). Only list fields change, never a lead.
2. `report` says what that left: lists assigned / in the pool, leads whose owner differs from their list's assignee
   (mismatches), and leads with no owner at all.
3. `fix_owners` (an admin's explicit click) makes the list assignee the owner of the listed mismatching leads.
4. `turn_on` refuses while unowned leads exist unless the admin acknowledges it, and migrates lists first so no member
   loses a list when the switch goes on.
"""
from . import auth, lead_access, lead_ops, list_assign, lists, workspaces


class SwitchRefused(ValueError):
    """The switch cannot be turned on yet; the message says why."""


def _ws_lists(store, ws: str) -> list:
    return [r for r in store.read("lists") if workspaces.ws_of(r) == ws]


def _legacy(row: dict) -> bool:
    return "assigned_to" not in row


def proposed_assignee(store, row: dict) -> str:
    person = list_assign.login_named(store, row.get("created_by"), workspaces.ws_of(row))
    return list_assign.display(person) if person else ""


def migrate_lists(store, ws: str) -> dict:
    """Set assigned_to on this workspace's legacy lists. Safe to repeat: lists that already have the field are left alone."""
    todo = {r["id"]: proposed_assignee(store, r) for r in _ws_lists(store, ws) if _legacy(r)}

    def apply(rows):
        out = []
        for r in rows:
            if r["id"] in todo and _legacy(r):
                to = todo[r["id"]]
                r = {**r, "assigned_to": to, "shared_with": r.get("shared_with") or [], "assigned_at": r.get("created_at", "") if to else "",
                     "assigned_by": "migration" if to else "", "log": [*r.get("log", []), lists._log(
                         f"Migrated to lead ownership: {'assigned to ' + to if to else 'moved to the admin pool'}", "migration")][-lists.MAX_LOG:]}
            out.append(r)
        return out
    if todo:
        store.update("lists", apply)
    return {"migrated": len(todo), "assigned": sum(1 for v in todo.values() if v), "pool": sum(1 for v in todo.values() if not v)}


def _same_person(store, assignee: str, owner: str, ws: str) -> bool:
    person = list_assign.login_named(store, assignee, ws)
    return lead_access.matches(person or {"name": assignee}, owner)


def report(store, ws: str) -> dict:
    """Read-only. A legacy list counts with the assignee migration would give it."""
    leads = {l["id"]: l for l in store.read("leads") if workspaces.ws_of(l) == ws}
    rows = _ws_lists(store, ws)
    effective = {r["id"]: (proposed_assignee(store, r) if _legacy(r) else (r.get("assigned_to") or "")) for r in rows}
    mismatches, seen = [], set()
    for r in rows:
        who = effective[r["id"]]
        if not who:
            continue
        for lid in r["lead_ids"]:
            lead = leads.get(lid)
            if lead is None or (lid, r["id"]) in seen:
                continue
            owner = str(lead.get("owner") or "").strip()
            if owner and not _same_person(store, who, owner, ws):
                seen.add((lid, r["id"]))
                mismatches.append({"lead_id": lid, "owner": owner, "list_assignee": who, "list_id": r["id"], "list": r.get("name", "")})
    return {"workspace": ws, "lists_assigned": sum(1 for v in effective.values() if v), "lists_pool": sum(1 for v in effective.values() if not v),
            "legacy_lists": sum(1 for r in rows if _legacy(r)), "mismatches": mismatches,
            "unowned": sum(1 for l in leads.values() if not str(l.get("owner") or "").strip()), "leads": len(leads),
            "ownership_on": lead_access.ownership_on(store, ws)}


def preview(store, ws: str) -> dict:
    """Read-only 'what each person would see' once the switch is on for this workspace: per login that works here, how many
    leads and lists they would see (lists as migration would assign them). Nothing is written, the switch is not touched."""
    leads = [l for l in store.read("leads") if workspaces.ws_of(l) == ws]
    rows = [{**r, "assigned_to": proposed_assignee(store, r) if _legacy(r) else (r.get("assigned_to") or "")} for r in _ws_lists(store, ws)]
    hider = lead_access.Hider
    members, seen = [], set()
    for u in auth.users(store):
        person = list_assign.login_named(store, u.get("email"), ws)
        if person is None or person.get("email") in seen:
            continue
        seen.add(person.get("email"))
        role = person.get("role") or ""
        if lead_access.is_admin(person, ws):
            n_leads, n_lists, why = len(leads), len(rows), "everything in this workspace"
        else:
            h = hider(store, person, {ws})
            n_leads, n_lists = sum(1 for l in leads if not h(l)), sum(1 for r in rows if not h.list(r))
            why = "their team's leads and lists (read-only)" if role == "viewer" else "leads they own and lists assigned or shared with them"
        members.append({"name": list_assign.display(person), "email": person.get("email") or "", "role": role, "leads": n_leads,
                        "lists": n_lists, "sees": why})
    unowned = sum(1 for l in leads if not str(l.get("owner") or "").strip())
    return {"workspace": ws, "ownership_on": lead_access.ownership_on(store, ws), "leads": len(leads), "lists": len(rows), "unowned": unowned,
            "unowned_note": f"{unowned} lead(s) have no owner: only admins would see them until they are assigned." if unowned else "",
            "members": sorted(members, key=lambda m: (m["role"] not in ("owner", "admin"), m["name"].lower()))}


def fix_owners(store, ws: str, by: str, lead_ids=None) -> dict:
    """Make the list assignee the owner of mismatching leads (all of them, or only `lead_ids`). A lead that two lists with
    different assignees both claim is left alone and reported as ambiguous."""
    wanted = None if lead_ids is None else {str(i) for i in lead_ids}
    claims = {}
    for m in report(store, ws)["mismatches"]:
        if wanted is None or m["lead_id"] in wanted:
            claims.setdefault(m["lead_id"], []).append(m)
    fixed, ambiguous = 0, []
    for lid, ms in claims.items():
        assignees = {m["list_assignee"].lower() for m in ms}
        if len(assignees) > 1:
            ambiguous.append({"lead_id": lid, "assignees": sorted({m["list_assignee"] for m in ms})})
            continue
        m = ms[0]
        if lead_ops.set_owner(store, lid, m["list_assignee"], by, f"fix owners: list {m['list']}") is not None:
            fixed += 1
    return {"fixed": fixed, "ambiguous": ambiguous, "report": report(store, ws)}


def turn_on(store, ws: str, acknowledge: bool = False) -> dict:
    info = report(store, ws)
    if info["unowned"] and not acknowledge:
        raise SwitchRefused(f"{info['unowned']} leads have no owner. With ownership on, only admins can work them until they are assigned. "
                            "Assign them first, or confirm that you accept this.")
    migrated = migrate_lists(store, ws)
    set_mode(store, ws, True)
    return {"on": True, "migrated": migrated, "unowned": info["unowned"]}


def set_mode(store, ws: str, on: bool) -> None:
    def apply(settings):
        mode = dict(settings.get(lead_access.KEY) or {})
        if on:
            mode[ws] = True
        else:
            mode.pop(ws, None)
        return {**settings, lead_access.KEY: mode}
    store.update("settings", apply)
