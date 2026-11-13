"""Lead ageing: which open leads have gone cold, and a re-engage list built from them.

untouched  never contacted, and created this many days ago or more
stale      last real attempt (call, email, WhatsApp, SMS, LinkedIn) this long ago, and the person never replied
stuck      in the same stage this long (days since the latest stage change on the timeline, else since it was created)

Attempts come from app/contact_status (never marks or imports). Closed stages, leads a caller marked invalid or consultant, and
people on the do-not-contact list are never aged. Reading is free: nothing here changes a stage, stops a sequence or sends anything.
"""
from datetime import datetime, timezone
from typing import Optional

from . import workspaces
from .lead_model import now_iso
from .rep_workflow import CLOSED

KEY = "lead_ageing"
DEFAULTS = {"untouched_days": 7, "stale_days": 14, "stuck_days": 21}
LABELS = {"untouched": "Untouched", "stale": "Gone quiet", "stuck": "Stuck in stage"}
KINDS = tuple(LABELS)
MAX_DAYS = 3650
REENGAGE_MAX = 1000


class AgeingRefused(ValueError):
    """The request is not allowed; the message says why in plain words."""


def config_of(settings: dict) -> dict:
    """The rules from a settings dict; anything missing or out of range falls back to its default."""
    saved = settings.get(KEY) or {}
    out = {}
    for k, default in DEFAULTS.items():
        try:
            v = int(saved.get(k, default))
        except (TypeError, ValueError):
            v = default
        out[k] = v if 1 <= v <= MAX_DAYS else default
    return out


def config(store) -> dict:
    return config_of(store.read("settings"))


def clean_config(raw) -> dict:
    """Validate a PUT body: whole numbers of days, 1 to 3650. Missing keys keep their current value (the caller merges)."""
    if not isinstance(raw, dict):
        raise AgeingRefused("Send the number of days for each rule.")
    out = {}
    for k in DEFAULTS:
        if raw.get(k) is None:
            continue
        v, name = raw[k], LABELS[k.split("_")[0]]
        try:
            if isinstance(v, bool) or (isinstance(v, float) and v != int(v)):
                raise ValueError
            v = int(v)
        except (TypeError, ValueError, OverflowError):
            raise AgeingRefused(f"{name}: write a whole number of days.") from None
        if not 1 <= v <= MAX_DAYS:
            raise AgeingRefused(f"{name}: choose between 1 and {MAX_DAYS} days.")
        out[k] = v
    return out


def save_config(store, raw) -> dict:
    new = clean_config(raw)
    store.update("settings", lambda s: {**s, KEY: {**config_of(s), **new}})
    return config(store)


def _parse(value) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def stage_since(lead: dict) -> str:
    """When the lead entered its current stage: the latest 'stage' timeline event, else when it was created."""
    stamps = [str(e.get("at") or "") for e in lead.get("timeline") or [] if isinstance(e, dict) and e.get("kind") == "stage"]
    return max(stamps, default="") or lead.get("created_at") or ""


def _days(since, now: datetime) -> Optional[int]:
    d = _parse(since)
    return None if d is None else max(0, (now - d).days)


def of(lead: dict, contact: dict, cfg: dict, now: Optional[datetime] = None) -> str:
    """'' | 'untouched' | 'stale' | 'stuck' for one lead. `contact` is contact_status.of(lead, idx)."""
    if (lead.get("stage") in CLOSED or lead.get("do_not_contact") or (lead.get("lead_quality") or {}).get("kind") in ("invalid", "consultant")
            or contact.get("status") in ("invalid", "consultant")):
        return ""
    now = now or datetime.now(timezone.utc)
    last = contact.get("last_at")
    if contact.get("status") == "never":
        if (_days(lead.get("created_at"), now) or 0) >= cfg["untouched_days"]:
            return "untouched"
    elif contact.get("status") != "replied" and last and (_days(last, now) or 0) >= cfg["stale_days"]:
        return "stale"
    if (_days(stage_since(lead), now) or 0) >= cfg["stuck_days"]:
        return "stuck"
    return ""


def counts(rows: list) -> dict:
    out = {k: 0 for k in KINDS}
    for r in rows:
        if r.get("ageing") in out:
            out[r["ageing"]] += 1
    return out


def re_engage_list(store, user, actor: dict, lead_ids: list, kind: str = "") -> dict:
    """Make a list from the aged leads among `lead_ids`. Same scoping as the bulk 'add to list' action: only leads the login
    may see and work, in the workspace the list is made in. Ageing is worked out again here, so a stale screen cannot add a lead
    that is not aged. Changes no stage, stops nothing, sends nothing."""
    from . import contact_status, lead_access, lists
    if kind and kind not in KINDS:
        raise AgeingRefused("Pick one of: untouched, gone quiet, stuck in stage.")
    ids = list(dict.fromkeys(str(i) for i in lead_ids or []))
    if not ids:
        raise AgeingRefused("There are no leads in this view to put in a list.")
    if len(ids) > 20000:
        raise AgeingRefused("That is too many leads at once. Narrow the filters first.")
    wanted, ws, cfg, idx = set(ids), workspaces.for_new_lead(), config(store), contact_status.index(store)
    now = datetime.now(timezone.utc)
    keep, outside = [], 0
    for lead in lead_access.visible_leads(store, user, workspaces.filter_rows(store.read("leads"))):
        if lead["id"] not in wanted or not lead_access.can_send(store, user, lead):
            continue
        a = of(lead, contact_status.of(lead, idx), cfg, now)
        if not a or (kind and a != kind):
            continue
        if workspaces.ws_of(lead) != ws:
            outside += 1
            continue
        keep.append((lead, a))
    if not keep:
        raise AgeingRefused("None of those leads are aged any more, so no list was made.")
    keep.sort(key=lambda p: p[0].get("created_at") or "")      # the longest-waiting first
    capped = len(keep) > REENGAGE_MAX
    chosen = [l["id"] for l, _ in keep[:REENGAGE_MAX]]
    today = datetime.now().strftime("%-d %b")
    mine = "" if user is None or lead_access.is_admin(user, ws) else actor["name"]       # a member's own list stays theirs (as POST /api/lists)
    row = lists.create(store, f"Re-engage {today}", chosen, actor["name"], "re-engage list from aged leads", assigned_to=mine, workspace=ws)
    from .audit import record
    record(store, {"name": actor.get("name"), "email": actor.get("email"), "role": actor.get("role", "")}, "lead_ageing.re_engage",
           row["name"], f"{len(chosen)} lead(s)", {"list_id": row["id"], "lead_ids": chosen[:500]})
    note = f"Created {row['name']}: {len(chosen)} lead{'s' if len(chosen) != 1 else ''}"
    if capped:
        note += f" (the list holds at most {REENGAGE_MAX}; the longest-waiting leads came first)"
    if outside:
        note += f". {outside} in another workspace left out"
    return {"list_id": row["id"], "name": row["name"], "count": len(chosen), "capped": capped, "left_out_other_workspace": outside,
            "notice": note + "."}
