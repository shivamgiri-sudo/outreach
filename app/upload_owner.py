"""Upload ownership: whoever uploads a batch of leads owns the new ones, gets a list named after the file, and rows
that belong to somebody else are skipped and reported (never merged into that person's lead).

Pure helpers plus a few small store functions; the import paths (CSV/Excel/paste, sheet, Find leads, prospects) call
them. A workspace is never crossed: lists and conflicts are stamped with the workspace of the request.
"""
from typing import Optional

from fastapi import HTTPException

from .lead_model import now_iso

MEMBER_ROLES = {"caller"}
ADMIN_ROLES = {"owner", "admin"}
DEFAULT_CAPS = {"leads_per_day": 5000, "max_lists": 50}
MAX_CONFLICTS = 5000
CLOSED = ("unsubscribed",)


def user_name(user: Optional[dict]) -> str:
    user = user or {}
    return user.get("name") or user.get("email") or ""


def is_member(user: Optional[dict]) -> bool:
    return bool(user) and str(user.get("role") or "").lower() in MEMBER_ROLES


def is_admin(user: Optional[dict]) -> bool:
    """No login at all (single-owner install) counts as admin, like require_owner."""
    return not user or str(user.get("role") or "").lower() in ADMIN_ROLES


def _read(store, key: str) -> list:
    try:
        return store.read(key)
    except KeyError:
        return []


def caps(store) -> dict:
    raw = store.read("settings").get("upload_caps") or {}
    return {k: int(raw.get(k, v)) if str(raw.get(k, v)).lstrip("-").isdigit() else v for k, v in DEFAULT_CAPS.items()}


class Ownership:
    """The uploader's view while rows are placed: who may be merged into, and how a lead is stamped."""

    def __init__(self, uploader: str, source: str = ""):
        self.uploader, self.source = uploader, source

    def conflict(self, lead: dict) -> str:
        owner = lead.get("owner") or ""
        return owner if owner and owner != self.uploader else ""

    def stamp(self, lead: dict, at: Optional[str] = None) -> dict:
        """Give an unowned (or new) lead to the uploader. A lead somebody else owns is returned unchanged."""
        if self.conflict(lead) or lead.get("owner") == self.uploader:
            return lead
        at = at or now_iso()
        return {**lead, "owner": self.uploader, "owner_at": at,
                "owner_why": {"kind": "upload", "at": at, "by": self.uploader, "source": self.source},
                "timeline": [*(lead.get("timeline") or []),
                             {"kind": "owner", "at": at, "by": self.uploader, "text": f"Owner: nobody → {self.uploader} (uploaded by {self.uploader})"}]}


def flag_unsendable(lead: dict, suppressed: set) -> dict:
    """Do-Not-Contact / unsubscribed people are kept but never sendable."""
    reason = ("unsubscribed" if lead.get("stage") in CLOSED else
              "do not contact" if lead.get("email") and lead["email"] in suppressed else "")
    if not reason or (lead.get("sendable") is False and lead.get("not_sendable_reason") == reason):
        return lead
    return {**lead, "sendable": False, "not_sendable_reason": reason}


def merge_results(parts: list) -> dict:
    """Combine the run_import results of several slices (split uploads) into one."""
    out = {}
    for part in parts:
        for key, value in part.items():
            if key == "summary":
                s = out.setdefault("summary", {})
                for k, v in value.items():
                    if isinstance(v, dict):
                        d = s.setdefault(k, {})
                        for kk, vv in v.items():
                            d[kk] = d.get(kk, 0) + vv
                    else:
                        s[k] = s.get(k, 0) + v
            elif isinstance(value, list):
                out.setdefault(key, []).extend(value)
    out["sample"] = out.get("sample", [])[:10]
    return out


# ------------------------------------------------------------------ plan
def plan(store, user: Optional[dict], body, n_rows: int) -> Optional[list]:
    """Who gets which rows. None = no ownership change (admin without assign_to/split: behaviour as before).
    Otherwise [{"owner": name, "start": i, "end": j}] over the table rows."""
    if is_member(user):
        return [{"owner": user_name(user), "start": 0, "end": n_rows}]
    if not is_admin(user):
        return None
    names = [str(n).strip() for n in (getattr(body, "split_between", None) or []) if str(n).strip()]
    assign = (getattr(body, "assign_to", "") or "").strip()
    if not names and not assign:
        return None
    known = {u.get("name") for u in (store.read("settings").get("users") or []) if isinstance(u, dict)}
    for n in names or [assign]:
        if known and n not in known:
            raise HTTPException(400, f"'{n}' is not a person in this workspace.")
    if not names:
        return [{"owner": assign, "start": 0, "end": n_rows}]
    if len(set(names)) != len(names) or len(names) > 50:
        raise HTTPException(400, "Pick each person once (at most 50).")
    mode = getattr(body, "mode", "even")
    if mode == "count":
        counts = list(getattr(body, "counts", None) or [])
        if len(counts) != len(names) or any(c < 0 for c in counts) or sum(counts) != n_rows:
            raise HTTPException(400, f"The counts must be one per person and add up to {n_rows} rows.")
    elif mode == "even":
        base, extra = divmod(n_rows, len(names))
        counts = [base + (1 if i < extra else 0) for i in range(len(names))]
    else:
        raise HTTPException(400, "mode must be 'even' or 'count'.")
    out, at = [], 0
    for n, c in zip(names, counts):
        out.append({"owner": n, "start": at, "end": at + c})
        at += c
    return out


# ------------------------------------------------------------------ caps
def check_caps(store, user: Optional[dict], n_rows: int, n_lists: int = 1) -> None:
    """Per-member daily upload cap and list-count cap (settings upload_caps)."""
    if not is_member(user):
        return
    from . import workspaces
    me, limit = user_name(user), caps(store)
    today = now_iso()[:10]
    done = sum(((r.get("summary") or {}).get("rows") or 0) for r in _read(store, "imports")
               if r.get("by") == me and (r.get("at") or "")[:10] == today and workspaces.visible(r))
    if done + n_rows > limit["leads_per_day"]:
        raise HTTPException(429, f"Daily upload limit reached: {limit['leads_per_day']} rows a day per person "
                                 f"({done} already uploaded today).")
    mine = [r for r in workspaces.filter_rows(_read(store, "lists")) if r.get("assigned_to") == me]
    if len(mine) + n_lists > limit["max_lists"]:
        raise HTTPException(429, f"You already have {len(mine)} lists (limit {limit['max_lists']}). Delete or merge some first.")


# ------------------------------------------------------------------ claim (compare-and-set)
def claim_leads(store, lead_ids: list, uploader: str, source: str = "") -> dict:
    """Make `uploader` owner of each lead that is unowned or already theirs; a lead somebody else owns (also one that
    got an owner a moment ago) is reported. Uses store.update_lead so two uploads of one person cannot both win."""
    own, mine, conflicts = Ownership(uploader, source), [], []
    for lead_id in dict.fromkeys(lead_ids):
        seen = {}

        def change(lead):
            seen["owner"] = lead.get("owner") or ""
            return None if own.conflict(lead) else own.stamp(lead)
        saved = store.update_lead(lead_id, change)
        if saved is None:
            continue
        if own.conflict(saved):
            conflicts.append({"lead_id": lead_id, "owner": saved.get("owner")})
        else:
            mine.append(lead_id)
    return {"mine": mine, "conflicts": conflicts}


def finish_upload(store, uploader: str, name: str, lead_ids: list, how: str, suppressed: Optional[set] = None, by: str = "") -> Optional[dict]:
    """Create the uploader's list for these (already claimed) leads and flag the not-sendable ones."""
    if not lead_ids:
        return None
    from . import lists
    if suppressed is not None:
        wanted = set(lead_ids)
        store.update("leads", lambda rows: [flag_unsendable(l, suppressed) if l["id"] in wanted else l for l in rows])
    return lists.create(store, name, lead_ids, by or uploader, how=how, assigned_to=uploader)


def upload_for_user(store, user: Optional[dict], lead_ids: list, source: str, how: str) -> dict:
    """After a non-import entry (sheet, Find leads, prospects) by a member: claim, list, report. {} for everyone else."""
    if not is_member(user) or not lead_ids:
        return {}
    me = user_name(user)
    claimed = claim_leads(store, lead_ids, me, source)
    row = finish_upload(store, me, source or "Upload", claimed["mine"], how, set(store.read("suppressed")))
    return {"list_id": (row or {}).get("id"), "owned": len(claimed["mine"]), "conflicts": claimed["conflicts"]}


def assign_connector_lead(store, lead_id: str) -> str:
    """Connectors with no human: let the assignment rules pick the owner (module may not exist yet)."""
    try:
        from . import assignment
    except ImportError:
        return ""
    fn = getattr(assignment, "assign_new", None)
    if fn is None:
        return ""
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    return (fn(store, lead) or "") if lead else ""
