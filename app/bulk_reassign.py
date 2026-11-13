"""Hand over a person's leads ("reassign everything owned by <person>"), with a dry run and an undo record.

Only owners and admins (an admin only within their own workspaces, only leads they may see). The new owner must be a name
owner_choices() allows for the caller and, when they have a login, an active, non-view-only login of the lead's workspace.
Leads change owner in ONE atomic store.update on their latest copy (a lead changed since the scan is skipped), through
lead_ops._owner_change, exactly like a manual reassignment: the first-touch clock is left as it is (a running clock keeps
running; the new owner's first touch stops it), running sequences carry on under the new owner. Bells go through
lead_ops.tell_reassigned for a few leads and as one summary per person for many.
"""
from typing import Optional

from . import assignment, audit, auth, bulk_undo, first_touch, lead_access, lead_ops
from . import workspaces as workspaces_mod
from .stages import ALL_STAGES

SAMPLE = 10
PER_LEAD_BELLS = 5


def _low(v) -> str:
    return " ".join(str(v or "").split()).lower()


def _aliases(store, name: str) -> set:
    """The name as typed plus the name and email of the login it belongs to."""
    out = {_low(name)}
    login = assignment._login_for(auth.users(store), name)
    if login:
        out |= {_low(login.get("name")), _low(login.get("email"))}
    return out - {""}


def _role(user: Optional[dict]) -> str:
    return (user or {}).get("base_role") or (user or {}).get("role") or ("owner" if user is None else "")


def untouched(lead: dict) -> bool:
    """The owner has not yet made a first touch (email / WhatsApp / LinkedIn send, call, note, meeting)."""
    if lead.get("first_touch_at"):
        return False
    owner = lead.get("owner")
    return not any(first_touch._is_touch(e, owner) for e in lead.get("timeline") or [] if isinstance(e, dict))


def recipient_problem(store, to: str, ws: str) -> str:
    login = assignment._login_for(auth.users(store), to)
    if login is None:
        return ""                                    # a team-list name with no login of its own
    if _role(login) == "viewer":
        return f"{to} has a view-only login and cannot work leads"
    if assignment._unavailable(login, ws):
        return f"{to} is not available in this workspace (no access, on leave or disabled)"
    return ""


def tell_moves(store, moves: list, by: str) -> None:
    """moves = [(lead after, old owner, new owner)]. A few leads: one bell each (lead_ops.tell_reassigned); many: one summary per pair."""
    moves = [m for m in moves if _low(m[1]) != _low(m[2])]
    if len(moves) <= PER_LEAD_BELLS:
        for lead, old, new in moves:
            lead_ops.tell_reassigned(store, lead, old, new, by)
        return
    from .reminders import notify
    pairs = {}
    for lead, old, new in moves:
        if old and new:
            pairs.setdefault((old, new), []).append(lead)
    for (old, new), rows in pairs.items():
        text = f"{len(rows)} leads moved from {old} to {new} (by {by}). Running email and LinkedIn steps carry on under {new}."
        sent = set()
        manager = assignment.manager_of(store, workspaces_mod.ws_of(rows[0]), old)
        for to, title in ((old, "Leads were taken from you"), (new, "Leads were given to you"), (manager, "Leads changed owner")):
            if to and _low(to) not in sent:
                sent.add(_low(to))
                notify(store, to, title, text, kind="reassigned")


def reassign_all(store, user: Optional[dict], actor: dict, from_owner: str, to_owner: str, stage: str = "", only_untouched: bool = False,
                 dry_run: bool = True) -> dict:
    if _role(user) not in ("owner", "admin"):
        raise PermissionError("Only an owner or an admin can hand over a person's leads.")
    who = actor["name"]
    from_owner = " ".join(str(from_owner or "").split())
    if not from_owner:
        raise lead_ops.LeadOpRefused("Say whose leads to hand over.")
    if stage and stage not in ALL_STAGES:
        raise lead_ops.LeadOpRefused("Pick a stage from the list, or leave it empty for every stage.")
    to = lead_ops.clean_owner(store, to_owner, "", user)
    if not to:
        raise lead_ops.LeadOpRefused("Say who takes the leads over.")
    old_names = _aliases(store, from_owner)
    if _low(to) in old_names:
        raise lead_ops.LeadOpRefused("The leads already belong to that person.")

    def mine(lead: dict) -> bool:
        return _low(lead.get("owner")) in old_names and (not stage or lead.get("stage") == stage) and (not only_untouched or untouched(lead))

    candidates = [l for l in store.read("leads") if workspaces_mod.visible(l) and (user is None or lead_access.is_admin(user, l)) and mine(l)]
    if len(candidates) > lead_ops.BULK_MAX:
        raise lead_ops.LeadOpRefused(f"That is {len(candidates)} leads and at most {lead_ops.BULK_MAX} can be handed over at once. "
                                     "Pick one stage, or only untouched leads, to make it smaller, then repeat.")
    skipped, movable = {}, []
    problems = {}
    for lead in candidates:
        ws = workspaces_mod.ws_of(lead)
        if ws not in problems:
            problems[ws] = recipient_problem(store, to, ws)
        if problems[ws]:
            skipped[lead["id"]] = problems[ws]
        else:
            movable.append(lead)
    by_stage = {}
    for lead in movable:
        by_stage[lead.get("stage")] = by_stage.get(lead.get("stage"), 0) + 1
    summary = {"from_owner": from_owner, "to_owner": to, "stage": stage, "only_untouched": only_untouched, "matched": len(candidates),
               "will_move": len(movable), "by_stage": by_stage,
               "sample": [{"id": l["id"], "company": l.get("company") or l.get("contact_name") or l["id"], "stage": l.get("stage")} for l in movable[:SAMPLE]]}
    if dry_run:
        return {**summary, "dry_run": True, "skipped": len(skipped), "reasons": _count(skipped)}

    chosen = {l["id"] for l in movable}
    trace = []

    def apply(rows: list) -> list:
        trace.clear()
        out = []
        for lead in rows:
            if lead["id"] not in chosen:
                out.append(lead)
            elif not (_low(lead.get("owner")) in old_names and workspaces_mod.visible(lead) and mine(lead)):
                skipped[lead["id"]] = "changed while this ran"
                out.append(lead)
            else:
                changed = lead_ops._owner_change(lead, to, who, f"hand-over from {from_owner}")
                trace.append((lead, changed))
                out.append(changed or lead)
        return out
    store.update("leads", apply)
    undo_id = bulk_undo.new_record(
        store, "owner", f"Handed over {len(trace)} lead(s) from {from_owner} to {to}", actor,
        bulk_undo.scope_ws([b for b, _ in trace]) if trace else workspaces_mod.MAIN,
        [{"id": b["id"], "old": b.get("owner") or "", "new": a["owner"]} for b, a in trace]) if trace else None
    tell_moves(store, [(a, b.get("owner") or "", to) for b, a in trace], who)
    audit.record(store, actor, "bulk.reassign", f"{from_owner} → {to}",
                 f"handed over {len(trace)} lead(s)" + (f", skipped {len(skipped)}" if skipped else "")
                 + (f", stage {stage}" if stage else "") + (", only untouched" if only_untouched else ""),
                 {"moved": len(trace), "skipped": len(skipped), "undo_id": undo_id, "lead_ids": [b["id"] for b, _ in trace][:500]})
    return {**summary, "dry_run": False, "moved": len(trace), "skipped": len(skipped), "reasons": _count(skipped), "undo_id": undo_id}


def _count(skipped: dict) -> list:
    counts = {}
    for r in skipped.values():
        counts[r] = counts.get(r, 0) + 1
    return [{"reason": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]
