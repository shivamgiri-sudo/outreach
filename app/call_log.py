"""Cross-lead contact log: every call (and optionally email / WhatsApp / LinkedIn / SMS attempt) with who, which lead,
when (IST day filters), outcome and notes, plus a per-caller summary.

A call is stored twice by activities.log_activity: an activity row (type "call", full fields) and a lead timeline event
(kind "call", same `at`). The activity wins; a timeline call with no activity at the same (lead, at) is an older call
and is kept, so one call is never counted twice. Scoping (workspace, client account, lead ownership) is done by the
route through workspaces.filter_rows over the leads, so only calls on leads the requester can see come through."""
from datetime import datetime, timedelta
from typing import Iterable, List, Optional

from .activities import CALL_OUTCOMES
from .rep_workflow import CONNECTED, IST, parse_at

CHANNELS = ("call", "email", "whatsapp", "linkedin", "sms", "meeting", "all")
UNANSWERED = ("no_answer", "busy", "call_disconnected")
MAX_SIZE = 200


def _day_bounds(day_from: str, day_to: str):
    """(start, end) UTC datetimes for inclusive IST days; either may be empty."""
    def one(raw: str) -> Optional[datetime]:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw[:10]).replace(tzinfo=IST)
        except ValueError:
            raise ValueError(f"Dates must look like 2026-11-13, got {raw!r}") from None
    start, end = one(day_from), one(day_to)
    if start and end and start > end:
        raise ValueError("The from date is after the to date.")
    return start, (end + timedelta(days=1)) if end else None


def _timeline_call(event: dict):
    """(outcome, notes) read back from the timeline summary 'Call — no answer: notes'."""
    text = str(event.get("text") or "")
    head, _, notes = text.partition(":")
    word = head.replace("Call", "", 1).strip(" —-").strip().replace(" ", "_")
    return (word if word in CALL_OUTCOMES else ""), notes.strip()


def collect(leads: List[dict], activities: Iterable[dict], channel: str = "call") -> List[dict]:
    """All attempts on `leads` (already scoped by the caller), de-duplicated, newest first."""
    by_id = {l["id"]: l for l in leads if isinstance(l, dict) and l.get("id")}
    want = None if channel == "all" else channel
    rows, seen = [], set()
    for a in activities or []:
        kind = a.get("type")
        if a.get("lead_id") not in by_id or (want and kind != want) or kind in ("note", "other"):
            continue
        seen.add((a["lead_id"], a.get("at"), kind))
        rows.append({"id": a.get("id") or "", "lead_id": a["lead_id"], "at": a.get("at"), "by": str(a.get("by") or "").strip(),
                     "channel": kind, "outcome": a.get("outcome") or "", "notes": a.get("body") or a.get("notes") or a.get("subject") or "",
                     "to": a.get("to") or ""})
    if want in (None, "call"):
        for lead in by_id.values():
            for e in lead.get("timeline") or []:
                if e.get("kind") != "call" or (lead["id"], e.get("at"), "call") in seen:
                    continue
                outcome, notes = _timeline_call(e)
                rows.append({"id": "", "lead_id": lead["id"], "at": e.get("at"), "by": str(e.get("by") or "").strip(),
                             "channel": "call", "outcome": outcome, "notes": notes, "to": ""})
    for r in rows:
        lead = by_id[r["lead_id"]]
        r.update(company=lead.get("company") or lead.get("domain") or "", contact_name=lead.get("contact_name") or "",
                 phone=r["to"] or lead.get("phone") or "")
        r["_when"] = parse_at(r["at"])
    rows = [r for r in rows if r["_when"]]
    rows.sort(key=lambda r: r["_when"], reverse=True)
    return rows


def in_range(rows: List[dict], day_from: str, day_to: str) -> List[dict]:
    start, end = _day_bounds(day_from, day_to)
    return [r for r in rows if (not start or r["_when"] >= start) and (not end or r["_when"] < end)]


def matches(row: dict, q: str) -> bool:
    q = q.strip().lower()
    return not q or q in " ".join(str(row.get(k) or "") for k in ("company", "contact_name", "phone", "notes", "by")).lower()


def summarise(rows: List[dict]) -> List[dict]:
    """Per caller, over call rows only: {caller, calls, connected, unanswered, interested, meetings, connect_rate (percent)}."""
    out = {}
    for r in rows:
        if r["channel"] != "call":
            continue
        s = out.setdefault(r["by"] or "Unknown", {"caller": r["by"] or "Unknown", "calls": 0, "connected": 0, "unanswered": 0, "interested": 0, "meetings": 0})
        s["calls"] += 1
        s["connected"] += r["outcome"] in CONNECTED
        s["unanswered"] += r["outcome"] in UNANSWERED
        s["interested"] += r["outcome"] == "interested"
        s["meetings"] += r["outcome"] == "meeting_booked"
    for s in out.values():
        s["connect_rate"] = round(100 * s["connected"] / s["calls"], 1)
    return sorted(out.values(), key=lambda s: (-s["calls"], s["caller"]))


def public(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_") and k != "to"}
