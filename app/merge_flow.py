"""Merge duplicate leads into one, with a dry run, an audit row and a 24-hour undo.

merge(store, user, actor, keep_id, merge_ids, dry_run=True): owners and admins only, only leads they administer, all in ONE
workspace and account, at most MAX_MERGE leads folded into the kept one. The merge itself is app/lead_merge.merge_leads (the kept
lead fills only its EMPTY fields, never overwritten) plus these safety rules: tags and phones are unioned, the earliest
created_at and the kept lead's owner stay, the stage never moves out of an exit stage (unsubscribed > disqualified > not_now),
and a compliance flag on ANY of the leads (do-not-contact, unsubscribed, consent withdrawn, an address on the suppression list)
is kept on the result. Every record that points at a merged lead (tasks, emails, WhatsApp, replies, activities, consent
rows, automation runs, list members ...) is re-pointed to the kept lead inside one store lock. The merged leads are then removed.

The undo record (settings["merge_undo"][workspace], last KEEP, TTL_HOURS) holds full copies of the leads before the merge and the
records that were re-pointed. Undo puts the leads back and re-points the records, only while the kept lead still exists. A person
erased in the meantime is never brought back: the store's privacy guard drops that lead again.
"""
import copy
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import accounts, audit, lead_access, lead_merge, privacy
from . import workspaces as workspaces_mod
from .lead_model import event, now_iso

KEY = "merge_undo"
KEEP = 20
TTL_HOURS = 24
MAX_MERGE = 5
MAX_ROWS = 20000
EXIT_ORDER = ("unsubscribed", "disqualified", "not_now")
# (collection, field holding the lead id, plain label). Lists (lead_ids) and the LinkedIn log (settings) are handled apart.
REFERENCES = (("tasks", "lead_id", "tasks"), ("outbox", "lead_id", "emails"), ("whatsapp_outbox", "lead_id", "WhatsApp messages"),
              ("inbox", "lead_id", "replies"), ("delivery_events", "lead_id", "delivery events"), ("activities", "lead_id", "calls and activities"),
              ("notifications", "lead_id", "notifications"), ("transactional_outbox", "lead_id", "internal emails"),
              ("consent_ledger", "lead_id", "consent records"), ("flow_runs", "lead_id", "automation runs"),
              ("capture_seen", "lead_id", "form captures"), ("capture_log", "lead_id", "form capture log rows"),
              ("ct_outbox", "lead_id", "call-tool jobs"))
LOG_KEY = "linkedin_agent_log"
FILL_ONLY = ("linkedin_seq", "linkedin_account", "sales", "sequence")
SKIP_DIFF = {"timeline", "merged_ids", "engagement", "merge_conflicts", "field_sources", "sources", "dedupe_key"}


class MergeRefused(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _role(user: Optional[dict]) -> str:
    return (user or {}).get("base_role") or (user or {}).get("role") or ("owner" if user is None else "")


def _name(lead: dict) -> str:
    return lead.get("company") or lead.get("contact_name") or "a lead without a name"


def _union(*lists) -> list:
    return list(dict.fromkeys(v for items in lists for v in (items or []) if v))


# ---------------------------------------------------------------- checks
def resolve(store, user: Optional[dict], keep_id: str, merge_ids: list) -> tuple:
    """(keep lead, [leads to merge]) or MergeRefused with a plain-words reason."""
    keep_id, merge_ids = str(keep_id or "").strip(), [str(i).strip() for i in merge_ids or [] if str(i).strip()]
    if _role(user) not in ("owner", "admin"):
        raise MergeRefused(403, "Only an owner or an admin can merge duplicate leads.")
    if not keep_id or not merge_ids:
        raise MergeRefused(400, "Pick the lead to keep and at least one duplicate to merge into it.")
    if len(set([keep_id, *merge_ids])) != len(merge_ids) + 1:
        raise MergeRefused(400, "Each lead can be listed only once, and a lead cannot be merged into itself.")
    if len(merge_ids) > MAX_MERGE:
        raise MergeRefused(400, f"At most {MAX_MERGE} leads can be merged into one at a time. Merge a few, then repeat.")
    every = store.read("leads")
    by_id = {l["id"]: l for l in every}
    seen = {l["id"] for l in workspaces_mod.filter_rows(every)}
    out = {}
    for lid in [keep_id, *merge_ids]:
        lead = by_id.get(lid)
        if lead is None or lid not in seen or not lead_access.is_admin(user, lead):
            gone = next((l for l in every if lid in (l.get("merged_ids") or []) and l["id"] in seen and lead_access.is_admin(user, l)), None)
            if lead is None and gone is not None:
                raise MergeRefused(409, f"That lead was already merged into {_name(gone)}. Refresh the list and pick again.")
            raise MergeRefused(404, "One of these leads was not found.")
        out[lid] = lead
    keep = out[keep_id]
    others = [out[i] for i in merge_ids]
    if len({workspaces_mod.ws_of(l) for l in out.values()}) > 1:
        raise MergeRefused(400, "These leads are in different workspaces. Only leads in the same workspace can be merged.")
    same = accounts.same_account_as(store, keep)
    if not all(same(l) for l in others):
        raise MergeRefused(400, "These leads belong to different client accounts and cannot be merged.")
    return keep, others


def _suppressed_addresses(store) -> set:
    return {str(e).lower() for e in store.read("suppressed")}


def _addresses(lead: dict) -> set:
    return {str(a).lower() for a in [lead.get("email"), *(lead.get("emails") or [])] if a}


def _flags(lead: dict, suppressed: set) -> list:
    out = []
    if lead.get("do_not_contact"):
        out.append("on the do-not-contact list")
    if lead.get("stage") == "unsubscribed":
        out.append("unsubscribed")
    if lead.get("consent_withdrawn_at"):
        out.append("consent withdrawn")
    if _addresses(lead) & suppressed:
        out.append("an email address on the suppression list")
    return out


# ---------------------------------------------------------------- the merged lead
def build(keep: dict, others: list, suppressed: set, taken: set, at: str, erased: frozenset = frozenset()) -> tuple:
    """(merged lead, notes) without touching the store."""
    notes = {"compliance": [], "stage": None}
    all_leads = [keep, *others]
    lead = lead_merge.merge_leads(keep, others, at, taken)
    lead["timeline"] = lead["timeline"][:-1]                       # merge_leads' own summary: replaced by one without names of people
    stage = keep.get("stage") or "new"
    exits = [x.get("stage") for x in all_leads if x.get("stage") in EXIT_ORDER]
    lead["stage"] = next((s for s in EXIT_ORDER if s in exits), None) or lead_merge.merged_stage(all_leads)
    if lead["stage"] != stage:
        notes["stage"] = {"from": stage, "to": lead["stage"]}
    created = sorted(x["created_at"] for x in all_leads if x.get("created_at"))
    if created:
        lead["created_at"] = created[0]
    tags = _union(*[x.get("tags") for x in all_leads])
    if tags:
        lead["tags"] = tags
    meetings = []
    for m in [mm for x in all_leads for mm in x.get("meetings") or []]:
        if m not in meetings:
            meetings.append(m)
    if meetings:
        lead["meetings"] = meetings
    for field in FILL_ONLY:
        if lead_merge._empty(keep.get(field)):
            found = next((x[field] for x in others if not lead_merge._empty(x.get(field))), None)
            if found is not None:
                lead[field] = found
    if erased and lead.get("emails"):                              # the store's privacy guard covers email / phone / phones / contacts; this list is ours
        lead["emails"] = [e for e in lead["emails"] if e == keep.get("email") or not privacy._erased_value("email", e, erased)]
    flags = [(x, _flags(x, suppressed)) for x in all_leads]
    if any(f for _, f in flags):
        notes["compliance"] = sorted({f"{_name(x)}: {w}" for x, f in flags for w in f})
        lead["do_not_contact"] = True                           # never lose a compliance flag, whichever lead had it
    names = ", ".join(sorted({x.get("company") or "a lead without a company" for x in others}))
    extra = f"; stage kept as {lead['stage']}" if notes["stage"] else ""
    lead["timeline"] = [*lead["timeline"], event("merge", f"Merged {len(others)} duplicate lead(s) into this one: {names}{extra}")]
    lead["timeline"].sort(key=lambda e: e.get("at") or "")
    return lead, notes


def _diff(keep: dict, merged: dict) -> dict:
    filled = [k for k, v in merged.items() if k not in SKIP_DIFF and k not in ("phones", "tags", "other_contacts", "meetings", "stage", "consent",
                                                                                "consent_withdrawn_at", "do_not_contact", "created_at", "sources",
                                                                                "company_size_bucket", "email_is_role")
              and lead_merge._empty(keep.get(k)) and not lead_merge._empty(v)]
    return {"filled": sorted(filled),
            "tags_added": [t for t in merged.get("tags") or [] if t not in (keep.get("tags") or [])],
            "phones_added": [p for p in merged.get("phones") or [] if p not in (keep.get("phones") or [keep.get("phone")])],
            "conflicts": [{"field": c["field"], "kept": c.get("kept"), "incoming": c.get("incoming")}
                          for c in merged.get("merge_conflicts") or [] if c not in (keep.get("merge_conflicts") or [])]}


# ---------------------------------------------------------------- records that point at a lead
def _count_refs(data: dict, ids: set) -> dict:
    out = {}
    for coll, field, label in REFERENCES:
        n = sum(1 for r in data[coll] if isinstance(r, dict) and r.get(field) in ids)
        if n:
            out[label] = n
    n = sum(1 for r in data["log"] if isinstance(r, dict) and r.get("lead_id") in ids)
    if n:
        out["LinkedIn log rows"] = n
    n = sum(1 for r in data["lists"] if isinstance(r, dict) and ids & set(r.get("lead_ids") or []))
    if n:
        out["lists"] = n
    return out


def _snapshot(store) -> dict:
    data = {coll: store.read(coll) for coll, _, _ in REFERENCES}
    data["log"] = store.read("settings").get(LOG_KEY) or []
    data["lists"] = store.read("lists")
    return data


def _plan(store, user, keep_id, merge_ids) -> dict:
    keep, others = resolve(store, user, keep_id, merge_ids)
    ids = {x["id"] for x in others}
    every = store.read("leads")
    taken = {l.get("dedupe_key") for l in every if l["id"] not in ids and l["id"] != keep["id"]}
    merged, notes = build(keep, others, _suppressed_addresses(store), taken, now_iso(), frozenset(store.read("suppressed_hashes")))
    data = _snapshot(store)
    counts = _count_refs(data, ids)
    if sum(counts.values()) > MAX_ROWS:
        raise MergeRefused(400, "These leads have too many linked records to merge in one go. Merge fewer leads at a time.")
    return {"keep": keep, "others": others, "merged": merged, "notes": notes, "counts": counts, "data": data}


def _summary(plan: dict, done: bool) -> dict:
    keep, others, merged = plan["keep"], plan["others"], plan["merged"]
    d = _diff(keep, merged)
    total = sum(plan["counts"].values())
    return {"dry_run": not done, "keep": {"id": keep["id"], "company": _name(keep), "stage": keep.get("stage")},
            "merging": [{"id": x["id"], "company": _name(x), "stage": x.get("stage")} for x in others],
            "records": plan["counts"], "records_total": total, **d, "stage": plan["notes"]["stage"], "stage_after": merged.get("stage"),
            "compliance": plan["notes"]["compliance"], "do_not_contact_after": bool(merged.get("do_not_contact")),
            "timeline_events": len(merged.get("timeline") or []),
            "summary": f"{len(others)} lead(s) will be folded into {_name(keep)}; {total} linked record(s) move across"
                       + (f"; {len(d['filled'])} empty field(s) get filled" if d["filled"] else "")
                       + ("; a do-not-contact or opt-out on any of them stays on the merged lead" if plan["notes"]["compliance"] else "") + "."}


# ---------------------------------------------------------------- the merge
def merge(store, user: Optional[dict], actor: dict, keep_id: str, merge_ids: list, dry_run: bool = True) -> dict:
    if dry_run:
        return _summary(_plan(store, user, keep_id, merge_ids), False)
    with store._lock:                                       # one consistent update: nothing else saves between these writes
        plan = _plan(store, user, keep_id, merge_ids)
        keep, others, merged = plan["keep"], plan["others"], plan["merged"]
        ids = {x["id"] for x in others}
        ws = workspaces_mod.ws_of(keep)
        rec = {"id": uuid.uuid4().hex[:12], "workspace": ws, "keep_id": keep["id"], "merged_ids": sorted(ids),
               "label": f"Merged {len(others)} lead(s) into {_name(keep)}", "by": actor.get("name") or "", "by_email": (actor.get("email") or "").lower(),
               "leads_before": {x["id"]: x for x in [keep, *others]}, "keep_after": merged, "moved": {}, "lists": []}
        now = datetime.now(timezone.utc)
        rec["created_at"], rec["expires_at"] = now.isoformat(timespec="seconds"), (now + timedelta(hours=TTL_HOURS)).isoformat(timespec="seconds")
        store.update("leads", lambda rows: [merged if l["id"] == keep["id"] else l for l in rows if l["id"] not in ids])
        for coll, field, _ in REFERENCES:
            rec["moved"][coll] = _repoint_collection(store, coll, field, ids, keep["id"])
        rec["moved"]["settings:" + LOG_KEY] = _repoint_log(store, ids, keep["id"])
        rec["lists"] = _repoint_lists(store, ids, keep["id"])
        rec["moved"] = {k: v for k, v in rec["moved"].items() if v}

        def save(settings: dict) -> dict:
            rows = dict(settings.get(KEY) or {})
            rows[ws] = [*(rows.get(ws) or []), rec][-KEEP:]
            return {**settings, KEY: rows}
        store.update("settings", save)
        audit.record(store, actor, "lead.merge", _name(keep), f"merged {len(others)} duplicate lead(s) into lead {keep['id']}; "
                     f"{sum(plan['counts'].values())} linked record(s) moved",
                     {"lead_id": keep["id"], "merged_ids": sorted(ids), "undo_id": rec["id"], "records": plan["counts"]})
        return {**_summary(plan, True), "undo_id": rec["id"], "merged": len(others)}


def _repoint_collection(store, coll: str, field: str, ids: set, keep_id: str) -> list:
    moved = []

    def apply(rows: list) -> list:
        moved.clear()
        have = [r.get("id") for r in rows if isinstance(r, dict) and r.get("id")]
        out = []
        for r in rows:
            if isinstance(r, dict) and r.get(field) in ids:
                unique = r.get("id") and have.count(r["id"]) == 1
                moved.append({"id": r["id"], "from": r[field]} if unique else {"before": r})
                r = {**r, field: keep_id}
            out.append(r)
        return out
    store.update(coll, apply)
    return moved


def _repoint_log(store, ids: set, keep_id: str) -> list:
    moved = []

    def apply(settings: dict) -> dict:
        moved.clear()
        rows = []
        for r in settings.get(LOG_KEY) or []:
            if isinstance(r, dict) and r.get("lead_id") in ids:
                moved.append({"before": r})
                r = {**r, "lead_id": keep_id}
            rows.append(r)
        return {**settings, LOG_KEY: rows} if moved else settings
    store.update("settings", apply)
    return moved


def _repoint_lists(store, ids: set, keep_id: str) -> list:
    changed = []

    def apply(rows: list) -> list:
        changed.clear()
        out = []
        for r in rows:
            members = r.get("lead_ids") or []
            if isinstance(r, dict) and ids & set(members):
                after = _union([keep_id if m in ids else m for m in members])
                changed.append({"list_id": r["id"], "before": members, "after": after})
                r = {**r, "lead_ids": after}
            out.append(r)
        return out
    store.update("lists", apply)
    return changed


# ---------------------------------------------------------------- undo
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


def _revert_keep(current: dict, before: dict, after: dict) -> tuple:
    """Undo only what the merge changed on the kept lead; anything edited since stays. -> (lead, fields left alone)."""
    lead, skipped = dict(current), []
    for k in set(before) | set(after):
        if k == "timeline" or before.get(k) == after.get(k):
            continue
        if current.get(k) == after.get(k):
            if k in before:
                lead[k] = before[k]
            else:
                lead.pop(k, None)
        else:
            skipped.append(k)
    added = [e for e in after.get("timeline") or [] if e not in (before.get("timeline") or [])]
    line = list(current.get("timeline") or [])
    for e in added:
        if e in line:
            line.remove(e)
    lead["timeline"] = line
    return lead, skipped


def _undo_rows(store, coll: str, field: str, items: list, keep_id: str, ids: set) -> int:
    restored = [0]

    def apply(rows: list) -> list:
        restored[0] = 0
        out, pending = list(rows), [i for i in items if "before" in i]
        for item in items:
            if "before" in item:
                continue
            for n, r in enumerate(out):
                if isinstance(r, dict) and r.get("id") == item["id"] and r.get(field) == keep_id:
                    out[n] = {**r, field: item["from"]}
                    restored[0] += 1
                    break
        for item in pending:
            want = {**item["before"], field: keep_id}
            for n, r in enumerate(out):
                if r == want:
                    out[n] = item["before"]
                    restored[0] += 1
                    break
        return out
    store.update(coll, apply)
    return restored[0]


def undo(store, undo_id: str, user: Optional[dict], actor: dict) -> dict:
    rec = _find(store, undo_id)
    if rec is None:
        raise MergeRefused(404, "That merge was not found. It may be older than the last 20.")
    if not _may_undo(user, rec):
        raise MergeRefused(403, "Only the person who made this merge or an admin can undo it.")
    if rec.get("undone_at"):
        raise MergeRefused(409, "This merge was already undone.")
    if datetime.now(timezone.utc) > datetime.fromisoformat(rec["expires_at"]):
        raise MergeRefused(410, f"This merge is older than {TTL_HOURS} hours and can no longer be undone.")
    keep_id, ids = rec["keep_id"], set(rec["merged_ids"])
    with store._lock:
        leads = store.read("leads")
        if not any(l["id"] == keep_id and workspaces_mod.visible(l) for l in leads):
            raise MergeRefused(409, "The lead these were merged into has since been merged again or deleted, so this merge can no longer be undone.")
        before = rec["leads_before"]
        taken = {l["id"] for l in leads}
        kept: dict = {}

        def put_back(rows: list) -> list:
            out = []
            for l in rows:
                if l["id"] == keep_id:
                    l, kept["skipped"] = _revert_keep(l, before[keep_id], rec["keep_after"])
                out.append(l)
            return [*out, *[before[i] for i in sorted(ids) if i not in taken]]
        saved = {l["id"] for l in store.update("leads", put_back)}
        restored = [i for i in sorted(ids) if i in saved]
        for coll, field, _ in REFERENCES:
            _undo_rows(store, coll, field, (rec.get("moved") or {}).get(coll) or [], keep_id, ids)
        log_items = (rec.get("moved") or {}).get("settings:" + LOG_KEY) or []
        if log_items:
            store.update("settings", lambda s: {**s, LOG_KEY: _undo_log(s.get(LOG_KEY) or [], log_items, keep_id)})
        _undo_lists(store, rec, keep_id, ids)
        done = {"undone_at": now_iso(), "undone_by": actor.get("name") or "Unknown", "restored": len(restored)}

        def mark(settings: dict) -> dict:
            rows = dict(settings.get(KEY) or {})
            rows[rec["workspace"]] = [{**r, **done} if r.get("id") == undo_id else r for r in rows.get(rec["workspace"]) or []]
            return {**settings, KEY: rows}
        store.update("settings", mark)
    audit.record(store, actor, "lead.merge_undo", rec["label"], f"restored {len(restored)} lead(s), skipped {len(ids) - len(restored)}",
                 {"undo_id": undo_id, "lead_id": keep_id, "restored": len(restored)})
    return {"undo_id": undo_id, "restored": len(restored), "skipped": len(ids) - len(restored),
            "kept_lead_fields_left_alone": kept.get("skipped") or [],
            "notice": f"Merge undone: {len(restored)} lead(s) are back." + ("" if len(restored) == len(ids) else
                      " Some could not come back (for example a person whose data was erased).")}


def _undo_log(rows: list, items: list, keep_id: str) -> list:
    out = list(rows)
    for item in items:
        want = {**item["before"], "lead_id": keep_id}
        for n, r in enumerate(out):
            if r == want:
                out[n] = item["before"]
                break
    return out


def _undo_lists(store, rec: dict, keep_id: str, ids: set) -> None:
    by_list = {c["list_id"]: c for c in rec.get("lists") or []}

    def apply(rows: list) -> list:
        out = []
        for r in rows:
            c = by_list.get(r.get("id"))
            if c:
                members = r.get("lead_ids") or []
                if members == c["after"]:
                    members = c["before"]
                else:
                    members = [m for m in members if m != keep_id or keep_id in c["before"]] + [m for m in c["before"] if m in ids and m not in members]
                r = {**r, "lead_ids": members}
            out.append(r)
        return out
    if by_list:
        store.update("lists", apply)


def list_for(store, user: Optional[dict]) -> list:
    """Open merge undo records this login may undo, newest first (no lead copies)."""
    now = datetime.now(timezone.utc)
    out = []
    for rows in (store.read("settings").get(KEY) or {}).values():
        for rec in rows or []:
            if rec.get("undone_at") or now > datetime.fromisoformat(rec["expires_at"]) or not _may_undo(user, rec):
                continue
            if user is not None and not lead_access.in_workspace(user, {"workspace": rec.get("workspace")}):
                continue
            out.append({k: rec[k] for k in ("id", "label", "workspace", "keep_id", "by", "created_at", "expires_at")})
    return sorted(out, key=lambda r: r["created_at"], reverse=True)
