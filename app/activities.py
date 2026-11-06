"""Activity trail, tasks with due times, and the automatic follow-ups that keep leads from going cold.

- `lead_activity` builds one time-ordered list per lead from everything the app knows: emails (full text, practice
  or real), replies, WhatsApp, calls, meetings, notes, tasks and system events.
- Tasks have an id, a due time (IST shown in the UI), an owner and a priority; they can be snoozed, reassigned
  and completed. Old tasks without ids get one the first time the list is read.
- When a lead says yes, a call task for the caller (the team caller set in Settings) is created for the same business day.
"""
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from .sequences import IST

CALL_CUTOFF = time(18, 30)          # leave room to dial before 18:55
NEXT_DAY_CALL = time(11, 0)
ACTIVITY_TYPES = ("call", "meeting", "note", "email", "whatsapp", "sms", "linkedin", "other")
CALL_OUTCOMES = ("connected", "no_answer", "busy", "wrong_number", "call_back", "interested", "not_interested", "meeting_booked")
PRIORITIES = ("high", "normal", "low")
DEFAULT_CALLER = "Sales team"
TASK_TITLES = {
    "review": "Check a reply", "question": "Answer a question", "wrong_person": "Find the right contact",
    "other": "Read a reply", "pilot": "Run a free pilot", "workflow": "Follow up", "unmatched_reply": "Match a reply to a lead",
    "call": "Call them", "follow_up": "Follow up", "meeting": "Prepare for the meeting",
    "erasure_request": "Erasure request", "meeting_follow_up": "Mark the meeting done or follow up",
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def caller_name(store) -> str:
    settings = store.read("settings")
    return (settings.get("caller_name") or DEFAULT_CALLER).strip() or DEFAULT_CALLER


def team(store) -> list:
    """People who can own tasks: [{name, email, phone, role}]. The caller is always there."""
    members = list(store.read("settings").get("team") or [])
    caller = caller_name(store)
    if not any(m.get("name") == caller for m in members):
        members.insert(0, {"name": caller, "email": "", "phone": "", "role": "caller"})
    return members


def next_call_slot(now: datetime) -> datetime:
    """Same business day before the call cut-off, otherwise the next weekday at 11:00 IST."""
    local = now.astimezone(IST)
    if local.weekday() < 5 and local.time() < CALL_CUTOFF:
        return now
    day = local.date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, NEXT_DAY_CALL, tzinfo=IST).astimezone(timezone.utc)


def _normalise_task(task: dict) -> dict:
    """Give legacy task rows (no id/status) the current shape."""
    status = task.get("status") or ("done" if task.get("done") else "open")
    return {"priority": "normal", "owner": None, "due_at": None, "title": TASK_TITLES.get(task.get("kind"), "Follow up"),
            **task, "id": task.get("id") or uuid.uuid4().hex[:10], "status": status, "done": status == "done",
            "created_at": task.get("created_at") or task.get("at")}


def ensure_task_ids(store) -> None:
    if any(not t.get("id") or not t.get("status") for t in store.read("tasks")):
        store.update("tasks", lambda rows: [_normalise_task(t) for t in rows])


def add_task(store, lead_id: Optional[str], kind: str, text: str = "", due_at: Optional[datetime] = None,
             owner: Optional[str] = None, priority: str = "normal", title: Optional[str] = None, source: str = "app",
             dedupe_open: bool = False) -> dict:
    """Create a task. With dedupe_open, an open task of the same kind for the same lead is reused instead."""
    if dedupe_open:
        existing = next((t for t in store.read("tasks") if t.get("lead_id") == lead_id and t.get("kind") == kind
                         and not t.get("done") and t.get("status", "open") == "open"), None)
        if existing:
            return existing
    task = _normalise_task({"lead_id": lead_id, "kind": kind, "text": text[:4000], "title": title or TASK_TITLES.get(kind, "Follow up"),
                            "due_at": iso(due_at) if due_at else None, "owner": owner, "priority": priority if priority in PRIORITIES else "normal",
                            "source": source, "at": iso(now_utc())})
    store.update("tasks", lambda rows: [*rows, task])
    return task


def update_task(store, task_id: str, change) -> Optional[dict]:
    found = {}

    def apply(rows):
        out = []
        for t in rows:
            if t.get("id") == task_id:
                t = change(_normalise_task(t))
                found["task"] = t
            out.append(t)
        return out
    store.update("tasks", apply)
    return found.get("task")


def complete_task(store, task_id: str, note: str = "") -> Optional[dict]:
    stamp = iso(now_utc())
    return update_task(store, task_id, lambda t: {**t, "status": "done", "done": True, "done_at": stamp,
                                                  "done_note": note[:1000] if note else t.get("done_note")})


def on_became_interested(store, lead_id: str, why: str = "Said yes") -> dict:
    """The lead said yes: give the caller a call task for the next allowed slot (one open call task per lead)."""
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), {})
    company = lead.get("company") or lead.get("domain") or "this lead"
    campaign = next((c.get("name") for c in store.read("campaigns") if lead.get("campaign_id") and c.get("id") == lead["campaign_id"]), None)
    origin = f" Found by campaign {campaign}." if campaign else ""
    return add_task(store, lead_id, "call", f"{why}. Call {company} through PD only, before 18:55.{origin}",
                    due_at=next_call_slot(now_utc()), owner=caller_name(store), priority="high", source="auto", dedupe_open=True)


def close_call_tasks(store, lead_id: str, note: str = "Call logged") -> int:
    """A call was made: complete the lead's open call tasks so they leave the caller's list."""
    stamp = iso(now_utc())
    closed = {"n": 0}

    def apply(rows):
        out = []
        for t in rows:
            if t.get("lead_id") == lead_id and t.get("kind") == "call" and t.get("status", "open") == "open" and not t.get("done"):
                t = {**_normalise_task(t), "status": "done", "done": True, "done_at": stamp, "done_note": note[:500]}
                closed["n"] += 1
            out.append(t)
        return out
    store.update("tasks", apply)
    return closed["n"]


def log_activity(store, lead_id: str, kind: str, body: dict) -> dict:
    """Record a call, meeting, note, LinkedIn message… with outcome, and a short line on the lead's timeline."""
    at = body.get("at") or iso(now_utc())
    row = {"id": uuid.uuid4().hex[:12], "lead_id": lead_id, "type": kind, "direction": body.get("direction") or "out",
           "subject": (body.get("subject") or "")[:300], "body": (body.get("notes") or "")[:8000],
           "outcome": body.get("outcome") or "", "duration_minutes": body.get("duration_minutes"),
           "by": body.get("by") or "", "at": at, "created_at": iso(now_utc())}
    store.update("activities", lambda rows: [*rows, row])
    summary = f"{kind.title()}" + (f" — {row['outcome'].replace('_', ' ')}" if row["outcome"] else "") + (f": {row['body'][:200]}" if row["body"] else "")
    store.update_lead(lead_id, lambda l: {**l, "last_activity_at": at,
                                          "timeline": [*l["timeline"], {"at": at, "kind": kind, "text": summary, **({"by": row["by"]} if row["by"] else {})}]})
    if kind == "call":
        close_call_tasks(store, lead_id, summary)
    return row


# Timeline kinds that are shown from their detailed source instead (full email/WhatsApp text, logged activities).
_DETAILED_ELSEWHERE = {"email", "practice", "whatsapp", "call", "meeting", "sms", "note_logged"}


def lead_activity(store, lead: dict) -> list:
    """Everything that happened on one lead, newest first, each with date-time, type, direction and full content."""
    lid = lead["id"]
    items = []
    for m in store.read("outbox"):
        if m.get("lead_id") == lid:
            items.append({"at": m["sent_at"], "type": "email", "direction": "out", "title": m.get("subject", ""),
                          "body": m.get("body", ""), "status": {"dry_run": "practice (not sent)"}.get(m.get("status"), m.get("status")),
                          "detail": m.get("step", "") + (f" · by {m['by']}" if m.get("by") else ""), "to": m.get("to"), "delivered_at": m.get("delivered_at"), "error": m.get("error")})
    for m in store.read("whatsapp_outbox"):
        if m.get("lead_id") == lid:
            items.append({"at": m["sent_at"], "type": "whatsapp", "direction": "out", "title": m.get("template", ""),
                          "body": m.get("body", ""), "status": {"dry_run": "practice (not sent)"}.get(m.get("status"), m.get("status")), "to": m.get("to")})
    for m in store.read("inbox"):
        if m.get("lead_id") == lid:
            items.append({"at": m.get("received_at"), "type": m.get("channel", "email"), "direction": "in",
                          "title": m.get("subject") or "Reply", "body": m.get("text", ""), "status": m.get("label") or "received",
                          "detail": f"from {m.get('from', '')}"})
    for a in store.read("activities"):
        if a.get("lead_id") == lid:
            items.append({"at": a["at"], "type": a["type"], "direction": a.get("direction", "out"), "title": a.get("subject") or a["type"].title(),
                          "body": a.get("body", ""), "status": a.get("outcome", ""), "detail": a.get("by", ""), "id": a["id"]})
    for t in store.read("tasks"):
        if t.get("lead_id") == lid:
            t = _normalise_task(t)
            items.append({"at": t.get("created_at") or t.get("at"), "type": "task", "direction": "none", "title": t["title"],
                          "body": t.get("text", ""), "status": t["status"], "due_at": t.get("due_at"), "owner": t.get("owner"),
                          "task_id": t["id"], "done_at": t.get("done_at")})
    for meeting in lead.get("meetings") or []:
        items.append({"at": meeting.get("created_at") or meeting.get("start"), "type": "meeting", "direction": "out",
                      "title": meeting.get("title") or "Meeting", "body": meeting.get("join_url", ""), "status": meeting.get("status", ""),
                      "start": meeting.get("start"), "end": meeting.get("end"), "detail": meeting.get("provider", "")})
    inbox_texts = {(m.get("text") or "")[:120] for m in store.read("inbox") if m.get("lead_id") == lid}
    logged = {(a["at"], a["type"]) for a in store.read("activities") if a.get("lead_id") == lid}
    for e in lead.get("timeline", []):
        if e["kind"] in _DETAILED_ELSEWHERE or (e["at"], e["kind"]) in logged:
            continue                       # logged notes / "other" activities are already listed above
        if e["kind"] == "reply" and any(t and t in e["text"] for t in inbox_texts):
            continue                       # the captured message already shows this reply in full
        items.append({"at": e["at"], "type": e["kind"], "direction": "in" if e["kind"] == "reply" else "out" if e["kind"] == "linkedin" else "none",
                      "title": "LinkedIn" if e["kind"] == "linkedin" else e["kind"].replace("_", " ").capitalize(), "body": e["text"], "status": "",
                      "detail": e.get("by", "")})
    for r in store.read("settings").get("linkedin_agent_log") or []:
        if r.get("lead_id") == lid and r.get("kind") in ("connect", "message", "accepted", "error"):
            items.append({"at": r["at"], "type": "linkedin", "direction": "in" if r["kind"] == "accepted" else "out",
                          "title": {"connect": "LinkedIn connection request (agent)", "message": "LinkedIn message (agent)",
                                    "accepted": "LinkedIn: they accepted", "error": "LinkedIn agent could not act"}[r["kind"]],
                          "body": r.get("note", ""), "status": "problem" if r["kind"] == "error" else "done", "detail": r.get("account", "")})
    return sorted(items, key=_when, reverse=True)


def history_summary(store, lead: dict) -> dict:
    """Every attempt on one contact, counted by channel, with the call outcomes and the last touch of each kind."""
    items = lead_activity(store, lead)
    calls = [i for i in items if i["type"] == "call"]
    outcomes = {}
    for c in calls:
        key = c.get("status") or "not recorded"
        outcomes[key] = outcomes.get(key, 0) + 1
    emails_out = [i for i in items if i["type"] == "email" and i["direction"] == "out"]
    real = [e for e in emails_out if e.get("status") not in ("practice (not sent)",)]
    replies = [i for i in items if i["direction"] == "in" and i["type"] in ("email", "whatsapp", "sms", "reply")]
    li = [i for i in items if i["type"] == "linkedin"]
    last = lambda rows: max((r["at"] for r in rows if r.get("at")), default=None)
    eng = lead.get("engagement") or {}
    seq = lead.get("linkedin_seq") or {}
    return {
        "total_touches": len([i for i in items if i["direction"] == "out"]),
        "calls": {"attempts": len(calls), "outcomes": outcomes, "connected": sum(v for k, v in outcomes.items() if k in ("connected", "interested", "not_interested", "meeting_booked", "call_back")),
                  "unanswered": outcomes.get("no_answer", 0) + outcomes.get("busy", 0), "last": last(calls)},
        "emails": {"sent": len(real), "practice": len(emails_out) - len(real), "opens": eng.get("opens", 0), "clicks": eng.get("clicks", 0),
                   "replies": len([r for r in replies if r["type"] in ("email", "reply")]), "last": last(emails_out)},
        "whatsapp": {"sent": len([i for i in items if i["type"] == "whatsapp" and i["direction"] == "out"]), "last": last([i for i in items if i["type"] == "whatsapp"])},
        "linkedin": {"status": seq.get("status") or ("not started" if not seq else ""), "steps_done": seq.get("done", []), "account": seq.get("account", ""),
                     "open_profile": bool((lead.get("linkedin_profile") or {}).get("open_profile")), "last": last(li)},
        "meetings": len([i for i in items if i["type"] == "meeting"]),
        "open_tasks": len([i for i in items if i["type"] == "task" and i.get("status") == "open"]),
        "first_touch": min((i["at"] for i in items if i["direction"] == "out" and i.get("at")), default=None),
        "last_touch": last([i for i in items if i["direction"] in ("out", "in")]),
        "last_reply": last(replies),
    }


def _when(item: dict) -> datetime:
    """Sort key in real time: stamps arrive with different offsets (UTC, IST from providers, Z)."""
    try:
        when = datetime.fromisoformat((item.get("at") or "").replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def task_buckets(tasks: list, now: datetime) -> dict:
    """Open tasks split into overdue / today / upcoming / no due date (IST days)."""
    today = now.astimezone(IST).date()
    out = {"overdue": [], "today": [], "upcoming": [], "no_date": []}
    for t in tasks:
        if t.get("status") != "open":
            continue
        due = t.get("due_at")
        if not due:
            out["no_date"].append(t)
            continue
        when = datetime.fromisoformat(due)
        if when < now and when.astimezone(IST).date() <= today:
            out["overdue" if when.astimezone(IST).date() < today or when < now - timedelta(minutes=1) else "today"].append(t)
        elif when.astimezone(IST).date() == today:
            out["today"].append(t)
        else:
            out["upcoming"].append(t)
    for rows in out.values():
        rows.sort(key=lambda t: (t.get("due_at") or "9999", {"high": 0, "normal": 1, "low": 2}.get(t.get("priority"), 1)))
    return out
