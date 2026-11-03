"""The Today list: one ranked list of what a person should do next, each item linked to its lead."""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .activities import ensure_task_ids
from .sequences import IST

TASK_TITLES = {
    "review": "Check a reply", "question": "Answer a question", "wrong_person": "Find the right contact",
    "other": "Read a reply", "pilot": "Run a free pilot", "workflow": "Follow up",
    "call": "Call them", "follow_up": "Follow up", "unmatched_reply": "Match a reply to a lead",
}
CALL_STAGES = ("interested", "whatsapp_active")
CLOSED = ("unsubscribed", "disqualified", "customer", "advocate")
MAX_ITEMS = 20
KEEP_IF_UNSUBSCRIBED = ("erasure_request", "review")      # a deletion request or a "yes" to check still needs a person


class DoneBody(BaseModel):
    task_index: int


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else ("es" if word.endswith("ch") else "s"))


def _called_today(lead: dict, today: str) -> bool:
    return any(e["kind"] == "call" and datetime.fromisoformat(e["at"]).astimezone(IST).date().isoformat() == today
               for e in lead["timeline"])


def _problem(lead: dict):
    seq = lead.get("sequence") or {}
    if seq.get("in_flight"):
        return "An email may or may not have gone out (the app stopped mid-send). Check the outbox before anything else is sent."
    if seq.get("stopped") == "send_failed":
        return "Emails to this lead failed three times. Check the address or the sending setup."
    if seq.get("blocked"):
        return "The next email is waiting: a detail it needs (like the role) is missing on this lead."
    return None


def make_router(store, present) -> APIRouter:
    router = APIRouter()

    @router.get("/api/today")
    def today():
        now = datetime.now(timezone.utc)
        today_ist = now.astimezone(IST).date().isoformat()
        ensure_task_ids(store)
        leads = {lead["id"]: lead for lead in store.read("leads")}
        open_tasks = [t for t in store.read("tasks") if t.get("status", "open") == "open" and not t.get("done")]
        call_task_leads = {t.get("lead_id") for t in open_tasks if t.get("kind") == "call"}
        name = lambda lid: (leads.get(lid) or {}).get("company") or (leads.get(lid) or {}).get("domain") or "a lead"
        problems, tasks, calls, nudges, starts = [], [], [], [], []

        for lead in leads.values():
            why = _problem(lead)
            if why:
                problems.append({"kind": "problem", "lead_id": lead["id"], "company": name(lead["id"]),
                                 "title": "Sending needs a look", "detail": why, "action": "Open lead"})
            if lead["stage"] in CALL_STAGES and not _called_today(lead, today_ist) and lead["id"] not in call_task_leads:
                calls.append({"kind": "call", "lead_id": lead["id"], "company": name(lead["id"]),
                              "title": "Call them", "action": "Open call desk",
                              "detail": "Said yes. Dial through PD only, before 18:55."})
        for index, task in enumerate(store.read("tasks")):
            stage = (leads.get(task.get("lead_id")) or {}).get("stage")
            if task.get("done") or task.get("status", "open") != "open" or (
                    stage == "unsubscribed" and task.get("kind") not in KEEP_IF_UNSUBSCRIBED) or (
                    stage == "disqualified" and task.get("kind") == "call"):          # they said no after the yes
                continue
            due = task.get("due_at")
            overdue = bool(due) and datetime.fromisoformat(due) < now
            is_call = task.get("kind") == "call"
            tasks.append({"kind": "call" if is_call else "task", "task_index": index, "task_id": task.get("id"), "lead_id": task.get("lead_id"),
                          "company": name(task.get("lead_id")), "due_at": due, "overdue": overdue, "owner": task.get("owner"),
                          "priority": task.get("priority", "normal"),
                          "title": task.get("title") or TASK_TITLES.get(task.get("kind"), "Follow up"), "detail": task.get("text", ""),
                          "action": "Open call desk" if is_call else "Open lead"})
        tasks.sort(key=lambda t: (not t["overdue"], t["due_at"] or "9999", t["priority"] != "high"))
        for lead in map(present, leads.values()):
            if lead["stage"] in CLOSED:
                continue
            if lead.get("nudge"):
                nudges.append({"kind": "nudge", "lead_id": lead["id"], "company": name(lead["id"]),
                               "title": "Help them use the free role", "detail": lead["nudge"]["text"], "action": "Open lead"})
            elif lead["band"] == "hot" and lead.get("email") and not lead.get("sequence") and lead["stage"] not in CALL_STAGES:
                starts.append({"kind": "start", "lead_id": lead["id"], "company": name(lead["id"]),
                               "title": "Start the email sequence", "detail": f"Score {lead['score']}: strong hiring signals.",
                               "action": "Open lead"})

        items = (problems + tasks + calls + nudges + starts)[:MAX_ITEMS]
        replies = sum(1 for t in tasks if t["kind"] == "task" and t["title"] in ("Check a reply", "Answer a question", "Read a reply", "Find the right contact"))
        if not leads:
            summary = "Import your first list of leads to get started."
        elif not items:
            summary = "Nothing waiting on you right now. New replies and calls will show up here."
        else:
            overdue = sum(1 for t in tasks if t["overdue"])
            summary = (f"{_plural(len(problems), 'sending issue')}, {_plural(replies, 'reply')} to handle, "
                       f"{_plural(len(calls) + sum(1 for t in tasks if t['kind'] == 'call'), 'call')} to make"
                       + (f", {overdue} overdue" if overdue else "") + ".").replace("replys", "replies")
        return {"summary": summary, "items": items,
                "counts": {"problems": len(problems), "tasks": sum(1 for t in tasks if t["kind"] == "task"),
                           "calls": len(calls) + sum(1 for t in tasks if t["kind"] == "call"), "nudges": len(nudges), "starts": len(starts)}}

    @router.post("/api/tasks/done")
    def task_done(body: DoneBody):
        rows = store.read("tasks")
        if not 0 <= body.task_index < len(rows):
            raise HTTPException(404, "Task not found")
        store.update("tasks", lambda current: [{**t, "done": True} if i == body.task_index else t for i, t in enumerate(current)])
        return {"ok": True}

    return router
