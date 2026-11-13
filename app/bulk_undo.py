"""Undo for bulk lead changes (owner, tags, stage, add to list).

Every bulk change writes one record in settings["bulk_undo"][workspace] (newest last, the last KEEP kept per workspace) with
each lead's old and new value. Undo restores only the leads whose CURRENT value still equals what the bulk set; any lead
changed since is skipped and reported. A record expires after TTL_HOURS. The leads are changed in one atomic
store.update on their latest copy, so a change the sending engine saved in the meantime is never overwritten.
Stage changes into Unsubscribed are never recorded, and an undo never moves a lead out of a terminal stage.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import audit, lead_access, lead_ops, lists
from . import workspaces as workspaces_mod
from .lead_model import now_iso
from .stages import TERMINAL

KEY = "bulk_undo"
KEEP = 20
TTL_HOURS = 24
FIELDS = {"owner": "owner", "tag_add": "tags", "tag_remove": "tags", "stage": "stage"}


class UndoRefused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _tagkey(tags) -> list:
    return sorted(str(t).lower() for t in tags or [])


def scope_ws(leads: list) -> str:
    """The workspace a bulk change is filed under: the one selected in the request, else the first lead's."""
    selected = workspaces_mod._SCOPE.get()[0]
    return selected or (workspaces_mod.ws_of(leads[0]) if leads else workspaces_mod.MAIN)


def _save(store, ws: str, rec: dict) -> None:
    def apply(settings: dict) -> dict:
        all_rows = dict(settings.get(KEY) or {})
        all_rows[ws] = [*(all_rows.get(ws) or []), rec][-KEEP:]
        return {**settings, KEY: all_rows}
    store.update("settings", apply)


def new_record(store, action: str, label: str, actor: dict, ws: str, changes: list, extra: Optional[dict] = None) -> Optional[str]:
    if not changes:
        return None
    now = datetime.now(timezone.utc)
    rec = {"id": uuid.uuid4().hex[:12], "action": action, "label": label, "field": FIELDS.get(action, action), "workspace": ws,
           "by": actor.get("name") or "", "by_email": (actor.get("email") or "").lower(), "created_at": now.isoformat(timespec="seconds"),
           "expires_at": (now + timedelta(hours=TTL_HOURS)).isoformat(timespec="seconds"), "changes": changes, **(extra or {})}
    _save(store, ws, rec)
    return rec["id"]


def record_bulk(store, action: str, value, actor: dict, trace: list, label: str = "") -> Optional[str]:
    """`trace` = [(lead before, lead after)] of the leads a bulk action changed."""
    field = FIELDS[action]
    changes = []
    for before, after in trace:
        if action == "stage" and after.get("stage") == "unsubscribed":
            continue                                   # never undo into un-unsubscribing
        changes.append({"id": before["id"], "old": before.get(field) or ("" if field != "tags" else []),
                        "new": after.get(field) or ("" if field != "tags" else [])})
    ws = scope_ws([b for b, _ in trace])
    return new_record(store, action, label or f"{action} {value or ''}".strip(), actor, ws, changes)


def record_list_add(store, list_row: dict, added: list, actor: dict) -> Optional[str]:
    return new_record(store, "list_add", f"added to list {list_row['name']}", actor, workspaces_mod.ws_of(list_row),
                      [{"id": i} for i in added], {"list_id": list_row["id"]})


def _find(store, undo_id: str) -> Optional[dict]:
    for rows in (store.read("settings").get(KEY) or {}).values():
        for rec in rows or []:
            if rec.get("id") == undo_id:
                return rec
    return None


def _may_undo(user: Optional[dict], rec: dict) -> bool:
    if user is None:
        return True
    if lead_access.is_admin(user, rec.get("workspace") or workspaces_mod.MAIN):
        return True
    mine = str(user.get("email") or "").lower()
    return bool(mine) and mine == rec.get("by_email")


def _restore(lead: dict, rec: dict, ch: dict, by: str):
    """(new lead, None) or (None, reason)."""
    field = rec["field"]
    note = f"(undo of bulk change, by {by})"
    if field == "owner":
        if (lead.get("owner") or "") != ch["new"]:
            return None, f"owner changed since (now {lead.get('owner') or 'nobody'})"
        changed = lead_ops._owner_change(lead, ch["old"], by, "undo of bulk change")
        return (changed, None) if changed else (None, "nothing to restore")
    if field == "tags":
        if _tagkey(lead.get("tags")) != _tagkey(ch["new"]):
            return None, "tags changed since"
        return {**lead, "tags": list(ch["old"]), "timeline": lead_ops._with_event(lead, "tags", f"Tags restored {note}", by)}, None
    if field == "stage":
        if lead.get("stage") != ch["new"]:
            return None, f"stage changed since (now {lead.get('stage')})"
        if lead.get("stage") in TERMINAL:
            return None, "unsubscribed leads are never moved back"
        return {**lead, "stage": ch["old"], "timeline": lead_ops._with_event(lead, "stage", f"{ch['new']} → {ch['old']} {note}", by)}, None
    return None, "unknown change"


def undo(store, undo_id: str, user: Optional[dict], actor: dict) -> dict:
    rec = _find(store, undo_id)
    if rec is None:
        raise UndoRefused(404, "That bulk change was not found. It may be older than the last 20.")
    if not _may_undo(user, rec):
        raise UndoRefused(403, "Only the person who made this change or an admin can undo it.")
    if rec.get("undone_at"):
        raise UndoRefused(409, "This change was already undone.")
    if datetime.now(timezone.utc) > datetime.fromisoformat(rec["expires_at"]):
        raise UndoRefused(410, f"This change is older than {TTL_HOURS} hours and can no longer be undone.")
    who = actor.get("name") or "Unknown"
    skipped, restored = {}, []
    if rec["field"] == "list_add":
        ids = {c["id"] for c in rec["changes"]}
        row = lists.get(store, rec["list_id"])
        if row is None:
            raise UndoRefused(404, "The list no longer exists.")
        have = set(row["lead_ids"])
        restored = [i for i in ids if i in have]
        skipped = {i: "no longer in the list" for i in ids if i not in have}
        lists.set_members(store, rec["list_id"], [], restored, who)
    else:
        by_id = {c["id"]: c for c in rec["changes"]}
        moves = []

        def apply(rows: list) -> list:
            restored.clear(); skipped.clear(); moves.clear()
            out = []
            for lead in rows:
                ch = by_id.get(lead["id"])
                if ch is None:
                    out.append(lead)
                elif not workspaces_mod.visible(lead):
                    skipped[lead["id"]] = "not found"
                    out.append(lead)
                else:
                    new, why = _restore(lead, rec, ch, who)
                    if why:
                        skipped[lead["id"]] = why
                    else:
                        restored.append(lead["id"])
                        moves.append((new, lead.get("owner") or "", ch["old"]))
                    out.append(new or lead)
            for missing in set(by_id) - {l["id"] for l in rows}:
                skipped[missing] = "not found"
            return out
        store.update("leads", apply)
        if rec["field"] == "owner" and moves:
            from .bulk_reassign import tell_moves
            tell_moves(store, moves, who)
    done = {"undone_at": now_iso(), "undone_by": who, "restored": len(restored), "skipped": len(skipped)}

    def mark(settings: dict) -> dict:
        all_rows = dict(settings.get(KEY) or {})
        all_rows[rec["workspace"]] = [{**r, **done} if r.get("id") == undo_id else r for r in all_rows.get(rec["workspace"]) or []]
        return {**settings, KEY: all_rows}
    store.update("settings", mark)
    audit.record(store, actor, "bulk.undo", rec["label"], f"restored {len(restored)}, skipped {len(skipped)}",
                 {"undo_id": undo_id, "restored": len(restored), "skipped": len(skipped)})
    reasons = {}
    for r in skipped.values():
        reasons[r] = reasons.get(r, 0) + 1
    return {"undo_id": undo_id, "restored": len(restored), "skipped": len(skipped),
            "reasons": [{"reason": k, "count": v} for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])]}


def list_for(store, user: Optional[dict]) -> list:
    """The still-open undo records this login may undo, newest first (for the Undo button after a reload)."""
    now = datetime.now(timezone.utc)
    out = []
    for rows in (store.read("settings").get(KEY) or {}).values():
        for rec in rows or []:
            if rec.get("undone_at") or now > datetime.fromisoformat(rec["expires_at"]) or not _may_undo(user, rec):
                continue
            if user is not None and not lead_access.in_workspace(user, {"workspace": rec.get("workspace")}):
                continue
            out.append({k: v for k, v in rec.items() if k != "changes"} | {"count": len(rec["changes"])})
    return sorted(out, key=lambda r: r["created_at"], reverse=True)
