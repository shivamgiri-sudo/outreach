"""LinkedIn stage board: where every lead with a LinkedIn profile stands, counted per stage and per account.

Stages: has a profile link but not contacted; request queued; request sent, waiting to accept; accepted, message 1
not sent; message N sent, message N+1 due (one row per N, with the due date from the open LinkedIn task); replied;
finished; stopped (with the reason). Only leads in the request's workspace scope are counted.
"""
import re

from . import linkedin_accounts as accts, linkedin_seq as li, workspaces

FIXED = {
    "not_contacted": "Has LinkedIn link, not contacted",
    "queued": "Request queued",
    "waiting_accept": "Request sent, waiting to accept",
    "accepted": "Accepted, message 1 not sent",
    "replied": "Replied",
    "finished": "Finished",
    "stopped": "Stopped",
}
ORDER_HEAD = ("not_contacted", "queued", "waiting_accept", "accepted")
ORDER_TAIL = ("replied", "finished", "stopped")
FILTER_KEYS = (*ORDER_HEAD, "messaging", *ORDER_TAIL)          # the Leads filter: "messaging" = any message N row
CSV_COLUMNS = ["company", "person", "title", "account", "owner", "stage", "last_step", "last_step_at", "next_due", "stopped_reason", "lead_id"]
_MSG = re.compile(r"^message_(\d+)$")


def classify(lead: dict) -> str:
    """The stage key of one lead ('' when it has no LinkedIn link and was never on the sequence)."""
    seq = lead.get("linkedin_seq") or {}
    if not seq:
        return "not_contacted" if li.has_profile_link(lead) and lead.get("stage") not in li.CLOSED else ""
    status = seq.get("status")
    if seq.get("stopped") == "replied" or (status == "stopped" and seq.get("replied_at")):
        return "replied"
    if status == "stopped":
        return "stopped"
    if status == "finished":
        return "finished"
    if status == "waiting_to_connect":
        return "queued"
    if status == "waiting_for_acceptance":
        return "waiting_accept"
    messages = len([n for n in seq.get("done") or [] if n != 1])
    return f"message_{messages}" if messages else "accepted"


def label(key: str) -> str:
    m = _MSG.match(key or "")
    if m:
        n = int(m.group(1))
        return f"Message {n} sent, message {n + 1} due"
    return FIXED.get(key, "")


def filter_key(key: str) -> str:
    return "messaging" if _MSG.match(key or "") else key


def _sort_key(key: str) -> tuple:
    m = _MSG.match(key)
    if m:
        return (len(ORDER_HEAD), int(m.group(1)))
    if key in ORDER_HEAD:
        return (ORDER_HEAD.index(key), 0)
    return (len(ORDER_HEAD) + 1 + ORDER_TAIL.index(key), 0)


def _next_due(store) -> dict:
    due = {}
    for t in store.read("tasks"):
        if t.get("kind") != "linkedin" or t.get("status", "open") != "open" or t.get("done") or not t.get("due_at"):
            continue
        lid = t.get("lead_id")
        if lid and (lid not in due or t["due_at"] < due[lid]):
            due[lid] = t["due_at"]
    return due


def _row(lead: dict, key: str, settings: dict, due: dict) -> dict:
    seq = lead.get("linkedin_seq") or {}
    done = seq.get("done") or []
    return {"lead_id": lead["id"], "company": lead.get("company") or lead.get("domain") or "", "person": lead.get("contact_name") or "",
            "title": lead.get("title") or "", "account": accts.assigned(lead, settings), "assigned_by": accts.how_assigned(lead, settings),
            "owner": lead.get("owner") or "", "stage": key, "stage_label": label(key), "last_step": max(done) if done else None,
            "last_step_at": seq.get("last_sent_at") or seq.get("accepted_at") or seq.get("started_at") or "",
            "next_due": due.get(lead["id"]) if key not in ORDER_TAIL else None,
            "stopped_reason": seq.get("stopped") or "" if key in ("stopped", "replied") else ""}


def _matches(row: dict, account: str, owner: str) -> bool:
    if account and (row["account"] != account if account != "__none" else row["account"]):
        return False
    if owner and (row["owner"] != owner if owner != "__none" else row["owner"]):
        return False
    return True


def rows(store, account: str = "", owner: str = "", stage: str = "") -> list:
    """Every lead on the board (in scope), optionally only one stage / account ('__none' = unassigned) / owner."""
    settings, due = store.read("settings"), _next_due(store)
    out = []
    for lead in workspaces.filter_rows(store.read("leads")):
        key = classify(lead)
        if not key or (stage and key != stage):
            continue
        row = _row(lead, key, settings, due)
        if _matches(row, account, owner):
            out.append(row)
    return sorted(out, key=lambda r: (r["next_due"] or "9999", r["company"].lower()))


def board(store, account: str = "", owner: str = "") -> dict:
    found = rows(store, account, owner)
    stages = {}
    for r in found:
        s = stages.setdefault(r["stage"], {"key": r["stage"], "label": r["stage_label"], "total": 0, "by_account": {}, "next_due": None})
        s["total"] += 1
        s["by_account"][r["account"]] = s["by_account"].get(r["account"], 0) + 1
        if r["next_due"] and (not s["next_due"] or r["next_due"] < s["next_due"]):
            s["next_due"] = r["next_due"]
    for key in (*ORDER_HEAD, *ORDER_TAIL):
        stages.setdefault(key, {"key": key, "label": label(key), "total": 0, "by_account": {}, "next_due": None})
    ordered = sorted(stages.values(), key=lambda s: _sort_key(s["key"]))
    names = sorted({r["account"] for r in found if r["account"]} | {k["account"] for k in accts.known(store)})
    return {"stages": ordered, "accounts": names, "total": len(found),
            "owners": sorted({r["owner"] for r in found if r["owner"]})}


def list_extras(store, presented: list) -> list:
    """For the Leads list: each lead's LinkedIn stage, account and next due LinkedIn step."""
    settings, due = store.read("settings"), _next_due(store)
    out = []
    for r in presented:
        key = classify(r)
        out.append({**r, "li_stage": key, "li_stage_filter": filter_key(key), "li_stage_label": label(key),
                    "li_account": accts.assigned(r, settings), "li_next_due": due.get(r["id"]) if key and key not in ORDER_TAIL else None})
    return out
