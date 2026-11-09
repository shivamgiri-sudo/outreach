"""Lead lists (audiences): pick companies or leads, save them as a named list, then start email or LinkedIn outreach
for that list only. Each list keeps its own journey: who made it, who started what, and how its members progressed.

The list holds lead ids, never copies, so edits and replies on a lead show everywhere. Starting outreach uses the same
functions as the single-lead buttons, so every consent, suppression and cap check still applies.
"""
import uuid
from typing import Optional

from . import linkedin_seq
from . import workspaces
from .lead_model import event, now_iso
from .routes_outreach import EnrollRefused, enroll_lead

MAX_LIST_LEADS = 5000
MAX_LOG = 200


def is_enriched(lead: dict) -> bool:
    return any(e.get("kind") == "enrichment" for e in lead.get("timeline", [])) or bool(lead.get("email_check"))


def ready_for_email(lead: dict) -> bool:
    return bool(lead.get("email")) and not lead.get("sequence") and lead.get("stage") not in ("unsubscribed", "disqualified", "customer", "advocate", "not_now")


def resolve(store, lead_ids=None, companies=None, enriched=False) -> list:
    """Lead ids from explicit ids, company names (case-insensitive, whole name), and/or every enriched lead."""
    leads = workspaces.filter_rows(store.read("leads"))
    wanted = set(lead_ids or [])
    names = {c.strip().lower() for c in (companies or []) if c and c.strip()}
    out = []
    for l in leads:
        if l["id"] in wanted or (names and (l.get("company") or "").strip().lower() in names) or (enriched and is_enriched(l)):
            out.append(l["id"])
    return out[:MAX_LIST_LEADS]


def _log(entry: str, by: str) -> dict:
    return {"at": now_iso(), "by": by, "text": entry}


def create(store, name: str, lead_ids: list, by: str, how: str = "") -> dict:
    row = {"id": uuid.uuid4().hex[:10], "name": name.strip()[:80] or "Untitled list", "lead_ids": list(dict.fromkeys(lead_ids)),
           "created_by": by, "created_at": now_iso(), "workspace": workspaces.for_new_lead(), "log": [_log(f"Created with {len(set(lead_ids))} leads{(' (' + how + ')') if how else ''}", by)]}
    store.update("lists", lambda rows: [*rows, row])
    return row


def get(store, list_id: str) -> Optional[dict]:
    return next((r for r in store.read("lists") if r["id"] == list_id), None)


def _change(store, list_id: str, fn) -> Optional[dict]:
    hit = {}

    def apply(rows):
        out = []
        for r in rows:
            if r["id"] == list_id:
                r = fn(r)
                hit["r"] = r
            out.append(r)
        return out
    store.update("lists", apply)
    return hit.get("r")


def set_members(store, list_id: str, add: list, remove: list, by: str) -> Optional[dict]:
    def fn(r):
        ids = [i for i in r["lead_ids"] if i not in set(remove)]
        ids += [i for i in add if i not in ids]
        note = []
        if add:
            note.append(f"added {len(add)}")
        if remove:
            note.append(f"removed {len(remove)}")
        return {**r, "lead_ids": ids[:MAX_LIST_LEADS], "log": [*r["log"], _log("Members " + " and ".join(note), by)][-MAX_LOG:]}
    return _change(store, list_id, fn)


def delete(store, list_id: str) -> bool:
    before = len(store.read("lists"))
    return len(store.update("lists", lambda rows: [r for r in rows if r["id"] != list_id])) < before


def journey(lead: dict) -> dict:
    """Where one lead is in outreach, from facts already on the lead."""
    seq, li = lead.get("sequence") or {}, lead.get("linkedin_seq") or {}
    eng = lead.get("engagement") or {}
    return {"emailed": bool(seq.get("sent_days")), "email_started": bool(seq), "opened": eng.get("opens", 0) > 0, "clicked": eng.get("clicks", 0) > 0,
            "replied": eng.get("replies", 0) > 0, "linkedin_started": bool(li), "linkedin_sent": bool(li.get("done")),
            "linkedin_accepted": bool(li.get("accepted_at")) or li.get("status") in ("accepted", "messaging", "replied"),
            "interested": lead.get("stage") in ("interested", "meeting", "customer", "advocate"), "stage": lead.get("stage")}


def stats(store, row: dict) -> dict:
    by_id = {l["id"]: l for l in store.read("leads")}
    members = [by_id[i] for i in row["lead_ids"] if i in by_id]
    j = [journey(l) for l in members]
    count = lambda key: sum(1 for x in j if x[key])
    return {"members": len(members), "with_email": sum(1 for l in members if l.get("email")),
            "with_linkedin": sum(1 for l in members if l.get("linkedin_url")),
            "companies": len({(l.get("company") or l["id"]).lower() for l in members}),
            "email_ready": sum(1 for l in members if ready_for_email(l)),
            "email_started": count("email_started"), "emailed": count("emailed"), "opened": count("opened"), "replied": count("replied"),
            "linkedin_started": count("linkedin_started"), "linkedin_sent": count("linkedin_sent"),
            "linkedin_accepted": count("linkedin_accepted"), "interested": count("interested"),
            "open_rate": _rate(count("opened"), count("emailed")), "reply_rate": _rate(count("replied"), count("emailed")),
            "accept_rate": _rate(count("linkedin_accepted"), count("linkedin_sent"))}


def _rate(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0


def start_email(store, list_id: str, by: str, only: Optional[list] = None) -> dict:
    row = get(store, list_id)
    if row is None:
        raise LookupError("List not found")
    ids = [i for i in row["lead_ids"] if not only or i in set(only)]
    done, refused = 0, {}
    for lid in ids:
        try:
            enroll_lead(store, lid, note=f"Enrolled in email sequence by {by} (list {row['name']})", by=by, via="bulk action",
                        detail=f"list {row['name']}")
            done += 1
        except (EnrollRefused, LookupError) as exc:
            refused[str(exc)] = refused.get(str(exc), 0) + 1
    _change(store, list_id, lambda r: {**r, "log": [*r["log"], _log(f"Started the email sequence for {done} of {len(ids)}", by)][-MAX_LOG:]})
    return {"started": done, "asked": len(ids), "refused": [{"reason": k, "count": v} for k, v in refused.items()]}


def start_linkedin(store, list_id: str, by: str, only: Optional[list] = None) -> dict:
    row = get(store, list_id)
    if row is None:
        raise LookupError("List not found")
    ids = [i for i in row["lead_ids"] if not only or i in set(only)]
    done, refused = 0, {}
    for lid in ids:
        try:
            linkedin_seq.start(store, lid)
            store.update_lead(lid, lambda l: {**l, "timeline": [*l["timeline"], {**event("linkedin", f"LinkedIn outreach started by {by} (list {row['name']})"), "by": by}]})
            done += 1
        except (ValueError, LookupError) as exc:
            refused[str(exc)] = refused.get(str(exc), 0) + 1
    _change(store, list_id, lambda r: {**r, "log": [*r["log"], _log(f"Started LinkedIn outreach for {done} of {len(ids)}", by)][-MAX_LOG:]})
    return {"started": done, "asked": len(ids), "refused": [{"reason": k, "count": v} for k, v in refused.items()]}
