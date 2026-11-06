"""Lead owner, tags, bulk actions and saved views.

- Owner: one team member (a name from the team list or a login's name). Every change is written on the lead's
  timeline with who did it. An inbound reply on an unowned lead gives it to the owner of its open task, or to the
  LinkedIn owner when the lead is in LinkedIn outreach.
- Bulk: one call changes many leads and answers per lead (ok / reason), so the screen can say what was left out.
- Saved views: each person's named Leads filters, kept server-side in settings["lead_views"] by login.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import auth
from . import workspaces as workspaces_mod
from .activities import on_became_interested, team
from .lead_model import event, now_iso
from .privacy import record_consent
from .stages import ALL_STAGES, TERMINAL, can_move
from .workflows import fire

OWNER_MAX = 80
TAG_MAX = 40
TAGS_PER_LEAD = 30
BULK_MAX = 5000
VIEWS_KEY = "lead_views"
VIEWS_PER_USER = 30
VIEW_NAME_MAX = 60
VIEW_FILTER_KEYS = ("query", "stage", "band", "sources", "inbound", "owner", "tag", "sort")
BULK_ACTIONS = ("stage", "owner", "tag_add", "tag_remove", "do_not_contact", "delete", "enroll")


class LeadOpRefused(ValueError):
    """The change is not allowed; the message says why in plain words."""


def _with_event(lead: dict, kind: str, text: str, by: str) -> list:
    return [*(lead.get("timeline") or []), {**event(kind, text), "by": by}]


# ---------- owner ----------
def owner_choices(store) -> list:
    """Names a lead can be given to: the team list plus every login (by name, or email when it has none)."""
    names = [m.get("name") for m in team(store)] + [u.get("name") or u.get("email") for u in auth.users(store)]
    seen, out = set(), []
    for n in names:
        n = " ".join(str(n or "").split())[:OWNER_MAX]
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return out


def clean_owner(store, raw, actor_name: str = "") -> str:
    """'' (unassign) or a known owner name, spelled as in the team list. Raises LeadOpRefused."""
    name = " ".join(str(raw or "").split())[:OWNER_MAX]
    if not name:
        return ""
    for known in owner_choices(store) + ([actor_name] if actor_name else []):
        if known.lower() == name.lower():
            return known
    raise LeadOpRefused(f"{name} is not in the team list. Add them under Team first.")


def _owner_change(lead: dict, owner: str, by: str, why: str = ""):
    old = lead.get("owner") or ""
    if old == owner:
        return None
    text = f"Owner: {old or 'nobody'} → {owner or 'nobody'}" + (f" ({why})" if why else "") + f" (by {by})"
    return {**lead, "owner": owner, "owner_at": now_iso(), "timeline": _with_event(lead, "owner", text, by)}


def set_owner(store, lead_id: str, owner: str, by: str, why: str = "") -> Optional[dict]:
    """Give one lead to `owner` ('' = nobody). Returns the saved lead (unchanged when already theirs) or None."""
    return store.update_lead(lead_id, lambda lead: _owner_change(lead, owner, by, why))


def auto_assign_on_reply(store, lead_id: str) -> Optional[str]:
    """An inbound reply on a lead nobody owns: give it to the owner of its open task, else the LinkedIn owner
    (when the lead is in LinkedIn outreach). Returns the new owner or None."""
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None or lead.get("owner"):
        return None
    tasks = [t for t in store.read("tasks") if t.get("lead_id") == lead_id and t.get("owner")
             and not t.get("done") and t.get("status", "open") == "open"]
    tasks.sort(key=lambda t: t.get("due_at") or "9999")
    owner, why = (tasks[0]["owner"], "owner of its open task") if tasks else ("", "")
    if not owner and (lead.get("linkedin_seq") or lead.get("linkedin_url")):
        owner, why = store.read("settings").get("linkedin_owner") or "", "LinkedIn owner"
    if not owner:
        return None
    saved = store.update_lead(lead_id, lambda l: None if l.get("owner") else _owner_change(l, owner, "auto", f"replied; {why}"))
    return owner if saved is not None and saved.get("owner") == owner else None


# ---------- tags ----------
def clean_tag(raw) -> str:
    return " ".join(str(raw or "").replace(",", " ").split())[:TAG_MAX]


def _tags_change(lead: dict, add: list = (), remove: list = (), by: str = ""):
    tags = list(lead.get("tags") or [])
    lower = {str(t).lower() for t in tags}
    added = []
    for t in add:
        if t and t.lower() not in lower:
            lower.add(t.lower())
            added.append(t)
    drop = {t.lower() for t in remove if t}
    removed = [t for t in tags if str(t).lower() in drop]
    if not added and not removed:
        return None
    new = [t for t in tags if str(t).lower() not in drop] + added
    if len(new) > TAGS_PER_LEAD:
        raise LeadOpRefused(f"A lead can have at most {TAGS_PER_LEAD} tags.")
    parts = ([f"added {', '.join(added)}"] if added else []) + ([f"removed {', '.join(removed)}"] if removed else [])
    return {**lead, "tags": new, "timeline": _with_event(lead, "tags", f"Tags: {'; '.join(parts)} (by {by})", by)}


def change_tags(store, lead_id: str, add: list, remove: list, by: str) -> Optional[dict]:
    add = [clean_tag(t) for t in add or []]
    remove = [clean_tag(t) for t in remove or []]
    if not any(add) and not any(remove):
        raise LeadOpRefused("Write a tag.")
    return store.update_lead(lead_id, lambda lead: _tags_change(lead, add, remove, by))


def all_tags(leads: list) -> list:
    counts = {}
    for lead in leads:
        for t in lead.get("tags") or []:
            counts[str(t)] = counts.get(str(t), 0) + 1
    return [{"tag": t, "count": n} for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))]


# ---------- list extras ----------
def _last_activity(lead: dict) -> str:
    stamps = [str(e.get("at") or "") for e in lead.get("timeline") or [] if isinstance(e, dict)]
    return max([lead.get("last_activity_at") or "", *stamps]) or lead.get("created_at") or ""


def with_list_extras(store, rows: list) -> list:
    """Add last_activity_at and next_task_due (earliest open task due date) to each presented lead."""
    due = {}
    for t in store.read("tasks"):
        if t.get("done") or t.get("status", "open") != "open" or not t.get("due_at"):
            continue
        lid = t.get("lead_id")
        if lid and (lid not in due or t["due_at"] < due[lid]):
            due[lid] = t["due_at"]
    return [{**r, "last_activity_at": _last_activity(r), "next_task_due": due.get(r["id"])} for r in rows]


# ---------- bulk ----------
def _stage_side_effects(store, lead_id: str, stage: str, who: str) -> None:
    if stage == "interested":
        on_became_interested(store, lead_id, f"Moved to Interested (bulk, by {who})")
    if stage == "unsubscribed":
        record_consent(store, lead_id, "withdrawn", "any", "", "moved to Unsubscribed by a person (bulk)")
    fire(store, "stage_change", stage, lead_id)


def _stage_change(lead: dict, stage: str, who: str, force: bool):
    """(new lead, None) or (None, reason). Force (owner only) skips the step order, never consent or unsubscribe rules."""
    current, consent = lead["stage"], bool(lead.get("consent"))
    if current == stage:
        return None, f"already {stage}"
    normal = can_move(current, stage, has_consent=consent)
    if not normal and not (force and current not in TERMINAL and (stage != "interested" or consent)):
        return None, ("no recorded consent for Interested" if stage == "interested" and not consent
                      else f"cannot move from {current} to {stage}")
    text = f"{current} → {stage} (bulk{'' if normal else ', forced'}, by {who})"
    return {**lead, "stage": stage, "timeline": _with_event(lead, "stage", text, who)}, None


def _dnc_change(lead: dict, who: str):
    if lead.get("do_not_contact"):
        return None, "already on the do-not-contact list"
    seq = lead.get("sequence")
    return {**lead, "do_not_contact": True, "do_not_contact_at": now_iso(),
            **({"sequence": {**seq, "stopped": seq.get("stopped") or "do_not_contact"}} if seq else {}),
            "timeline": _with_event(lead, "do_not_contact", f"Put on the do-not-contact list (by {who})", who)}, None


def _one_change(lead: dict, action: str, value, who: str, force: bool):
    """(new lead or None, reason or None) for the in-place actions."""
    if action == "stage":
        return _stage_change(lead, value, who, force)
    if action == "owner":
        changed = _owner_change(lead, value, who, "bulk")
        return (changed, None) if changed else (None, f"already owned by {value}" if value else "already unassigned")
    if action == "do_not_contact":
        return _dnc_change(lead, who)
    has = value.lower() in {str(t).lower() for t in lead.get("tags") or []}
    if action == "tag_add" and has:
        return None, "already tagged"
    if action == "tag_remove" and not has:
        return None, "did not have this tag"
    try:
        return _tags_change(lead, *(([value], []) if action == "tag_add" else ([], [value])), by=who), None
    except LeadOpRefused as exc:
        return None, str(exc)


def _log_delete(lead: dict, actor: dict) -> dict:
    return {"lead_id": lead["id"], "field": "deleted", "label": "Lead deleted", "old": lead.get("company") or lead.get("email") or "",
            "new": "", "by": actor["name"], "by_email": actor["email"], "at": now_iso(), "reason": "bulk delete",
            "company": lead.get("company") or ""}


def run_bulk(store, lead_ids: list, action: str, value, actor: dict, force: bool = False, enroll=None) -> dict:
    """Apply one action to many leads. Returns {action, asked, ok, failed, results: [{id, ok, reason}], reasons}.

    Stage, owner, tags and do-not-contact are applied in one atomic save on the latest copy of the leads. Leads
    outside the caller's workspace count as not found. `enroll(store, lead_id, note)` starts one lead's email
    sequence (routes_outreach.enroll_lead); its refusal message is the reason.
    """
    if action not in BULK_ACTIONS:
        raise LeadOpRefused(f"Unknown action. Use one of: {', '.join(BULK_ACTIONS)}.")
    ids = list(dict.fromkeys(str(i) for i in lead_ids or []))
    if not ids:
        raise LeadOpRefused("Pick at least one lead.")
    if len(ids) > BULK_MAX:
        raise LeadOpRefused(f"At most {BULK_MAX} leads at a time.")
    who, owner_role = actor["name"], actor.get("role") == "owner"
    if action == "delete" and not owner_role:
        raise PermissionError("Only an owner can delete leads.")
    if action == "stage" and value not in ALL_STAGES:
        raise LeadOpRefused("Pick a stage.")
    if action == "owner":
        value = clean_owner(store, value, who)
    if action in ("tag_add", "tag_remove"):
        value = clean_tag(value)
        if not value:
            raise LeadOpRefused("Write a tag.")
    visible = {l["id"] for l in store.read("leads") if workspaces_mod.visible(l)}
    reasons = {i: "not found" for i in ids if i not in visible}
    wanted = [i for i in ids if i in visible]
    done, gone = [], []

    if action == "enroll":
        for lead_id in wanted:
            try:
                enroll(store, lead_id, f"Enrolled in email sequence (bulk, by {who})")
            except LookupError:
                reasons[lead_id] = "not found"
            except ValueError as exc:          # EnrollRefused
                reasons[lead_id] = str(exc)
    elif wanted:
        targets = set(wanted)

        def apply(rows: list) -> list:
            done.clear(); gone.clear()
            seen, out = set(), []
            for lead in rows:
                if lead["id"] not in targets:
                    out.append(lead)
                    continue
                seen.add(lead["id"])
                if action == "delete":
                    gone.append(lead)
                    continue
                changed, why = _one_change(lead, action, value, who, force and owner_role)
                if why:
                    reasons[lead["id"]] = why
                else:
                    done.append(lead)
                out.append(changed or lead)
            for missing in targets - seen:
                reasons[missing] = "not found"
            return out
        store.update("leads", apply)
        if action == "delete" and gone:
            rows = [_log_delete(l, actor) for l in gone]
            store.update("audit", lambda audit_rows: [*audit_rows, *rows])
        if action == "do_not_contact":
            emails = {l.get("email") for l in done if l.get("email")}
            if emails:                         # the same list unsubscribes and bounces use; every sender checks it
                store.update("suppressed", lambda rows: [*rows, *sorted(e for e in emails if e not in rows)])
        if action == "stage":
            for lead in done:
                _stage_side_effects(store, lead["id"], value, who)
    results = [{"id": i, "ok": i not in reasons, **({"reason": reasons[i]} if i in reasons else {})} for i in ids]
    ok = sum(1 for r in results if r["ok"])
    return {"action": action, "asked": len(ids), "ok": ok, "failed": len(ids) - ok, "results": results,
            "reasons": _reason_counts(results)}


def _reason_counts(results: list) -> list:
    counts = {}
    for r in results:
        if not r["ok"]:
            counts[r["reason"]] = counts.get(r["reason"], 0) + 1
    return [{"reason": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]


# ---------- saved views ----------
def view_owner(user: Optional[dict]) -> str:
    user = user or {}
    return (user.get("email") or user.get("sub") or "local").lower()


def _clean_filters(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key in VIEW_FILTER_KEYS:
        value = raw.get(key)
        if key == "sources":
            out[key] = [str(s)[:60] for s in (value or []) if s][:30] if isinstance(value, list) else []
        elif key == "inbound":
            out[key] = bool(value)
        else:
            out[key] = str(value or "")[:120]
    return out


def list_views(store, user) -> list:
    return list((store.read("settings").get(VIEWS_KEY) or {}).get(view_owner(user)) or [])


def save_view(store, user, name: str, filters: dict) -> dict:
    name = " ".join(str(name or "").split())[:VIEW_NAME_MAX]
    if not name:
        raise LeadOpRefused("Give the view a name.")
    key, saved = view_owner(user), {}

    def apply(settings: dict) -> dict:
        views = dict(settings.get(VIEWS_KEY) or {})
        mine = [v for v in views.get(key) or [] if v.get("name", "").lower() != name.lower()]
        if len(mine) >= VIEWS_PER_USER:
            raise LeadOpRefused(f"You can keep {VIEWS_PER_USER} views. Delete one first.")
        view = {"id": uuid.uuid4().hex[:8], "name": name, "filters": _clean_filters(filters),
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        saved.update(view)
        views[key] = [*mine, view]
        return {**settings, VIEWS_KEY: views}
    store.update("settings", apply)
    return saved


def delete_view(store, user, view_id: str) -> bool:
    key, found = view_owner(user), {}

    def apply(settings: dict) -> dict:
        views = dict(settings.get(VIEWS_KEY) or {})
        mine = views.get(key) or []
        keep = [v for v in mine if v.get("id") != view_id]
        found["ok"] = len(keep) != len(mine)
        views[key] = keep
        return {**settings, VIEWS_KEY: views}
    store.update("settings", apply)
    return found["ok"]
