"""Activity trail, tasks and notifications API."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import workspaces
from .activities import (ACTIVITY_TYPES, CALL_OUTCOMES, PRIORITIES, add_task, complete_task, ensure_task_ids,
                         TASK_SORTS, history_summary, lead_activity, log_activity, now_utc, task_buckets, team, update_task, iso)


class ActivityBody(BaseModel):
    type: str
    at: Optional[str] = None                 # when it happened (ISO); default now
    outcome: str = ""
    notes: str = ""
    subject: str = ""
    direction: str = "out"
    duration_minutes: Optional[int] = Field(default=None, ge=0, le=600)
    by: str = ""
    follow_up_at: Optional[str] = None       # create a follow-up task due then
    follow_up_text: str = ""


class TaskBody(BaseModel):
    lead_id: Optional[str] = None
    title: str
    text: str = ""
    due_at: Optional[str] = None
    owner: Optional[str] = None
    priority: str = "normal"
    kind: str = "follow_up"


class TaskChange(BaseModel):
    title: Optional[str] = None
    text: Optional[str] = None
    due_at: Optional[str] = None
    owner: Optional[str] = None
    priority: Optional[str] = None
    snooze_minutes: Optional[int] = Field(default=None, ge=5, le=60 * 24 * 14)


class DoneBody(BaseModel):
    note: str = ""


class TeamBody(BaseModel):
    caller_name: Optional[str] = None
    team: Optional[list] = None
    notify_email: Optional[bool] = None


def _parse_when(raw: Optional[str], field: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"{field} must be a date and time like 2026-11-04T15:30:00+05:30")
    return when if when.tzinfo else when.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))   # no zone given: IST


def make_router(store, transactional_sender=None) -> APIRouter:
    router = APIRouter()
    ensure_task_ids(store)

    def lead_or_404(lead_id: str) -> dict:
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None or not workspaces.visible(lead):
            raise HTTPException(404, "Lead not found")
        return lead

    @router.get("/api/leads/{lead_id}/activity")
    def activity(lead_id: str):
        return lead_activity(store, lead_or_404(lead_id))

    @router.get("/api/leads/{lead_id}/history-summary")
    def summary(lead_id: str):
        return history_summary(store, lead_or_404(lead_id))

    @router.post("/api/leads/{lead_id}/activity")
    def add_activity(lead_id: str, body: ActivityBody, request: Request):
        lead_or_404(lead_id)
        if not body.by.strip():
            from .audit import actor_of
            user = getattr(request.state, "user", None) or {}
            body.by = actor_of(user)["name"] if user.get("email") else ""
        if body.type not in ACTIVITY_TYPES:
            raise HTTPException(400, f"type must be one of: {', '.join(ACTIVITY_TYPES)}")
        if body.type == "call" and body.outcome and body.outcome not in CALL_OUTCOMES:
            raise HTTPException(400, f"call outcome must be one of: {', '.join(CALL_OUTCOMES)}")
        at = _parse_when(body.at, "at")
        row = log_activity(store, lead_id, body.type, {**body.model_dump(), "at": iso(at) if at else None})
        follow = _parse_when(body.follow_up_at, "follow_up_at")
        task = None
        if follow:
            task = add_task(store, lead_id, "follow_up", body.follow_up_text or f"Follow up after the {body.type}",
                            due_at=follow, owner=body.by or None, source="activity")
        return {"activity": row, "follow_up_task": task}

    @router.get("/api/tasks/board")
    def board(owner: Optional[str] = None, sort: str = "due"):
        if sort not in TASK_SORTS:
            raise HTTPException(400, "sort must be due or priority")
        ensure_task_ids(store)
        from . import workspaces
        all_leads = store.read("leads")
        leads = {l["id"]: l for l in all_leads}
        rows = []
        for t in workspaces.filter_tasks(store.read("tasks"), all_leads):    # another workspace's tasks stay out
            if owner and t.get("owner") != owner:
                continue
            lead = leads.get(t.get("lead_id")) or {}
            # Automatic tasks for a lead who unsubscribed are dropped; anything a person added stays, labelled.
            if lead.get("stage") == "unsubscribed" and t.get("source") not in ("person", "activity") and t.get("kind") not in ("review", "erasure_request"):
                continue
            rows.append({**t, "company": lead.get("company") or lead.get("domain") or "", "lead_stage": lead.get("stage")})
        buckets = task_buckets(rows, now_utc(), sort)
        done = sorted((t for t in rows if t.get("status") == "done"), key=lambda t: t.get("done_at") or "", reverse=True)[:50]
        return {**buckets, "done": done, "team": team(store),
                "counts": {k: len(v) for k, v in buckets.items()}}

    @router.post("/api/tasks")
    def create_task(body: TaskBody):
        body.priority = (body.priority or "normal").strip().lower()
        if body.lead_id:
            lead_or_404(body.lead_id)
        if body.priority not in PRIORITIES:
            raise HTTPException(400, "priority must be high, normal or low")
        if not body.title.strip():
            raise HTTPException(400, "Give the task a short title")
        return add_task(store, body.lead_id, body.kind or "follow_up", body.text, due_at=_parse_when(body.due_at, "due_at"),
                        owner=body.owner, priority=body.priority, title=body.title.strip()[:200], source="person")

    @router.put("/api/tasks/{task_id}")
    def change_task(task_id: str, body: TaskChange):
        due = _parse_when(body.due_at, "due_at")

        def change(t: dict) -> dict:
            out = dict(t)
            for key in ("title", "text", "owner"):
                if getattr(body, key) is not None:
                    out[key] = getattr(body, key)
            if body.priority is not None:
                priority = body.priority.strip().lower()
                if priority not in PRIORITIES:
                    raise HTTPException(400, "priority must be high, normal or low")
                out["priority"] = priority
            if due:
                out["due_at"] = iso(due)
            if body.snooze_minutes:
                # Snooze pushes the task later: from its due time if that is still ahead, else from now.
                # (From "now" alone, "+1 hour" on a task due tonight would move it earlier.)
                current = _parse_when(t.get("due_at"), "due_at") if t.get("due_at") else None
                base = max(now_utc(), current) if current else now_utc()
                out["due_at"] = iso(base + timedelta(minutes=body.snooze_minutes))
            if due or body.snooze_minutes:
                out["reminded"] = []             # remind again at the new time
            return out
        task = update_task(store, task_id, change)
        if task is None:
            raise HTTPException(404, "Task not found")
        return task

    @router.post("/api/tasks/{task_id}/done")
    def done(task_id: str, body: DoneBody):
        task = complete_task(store, task_id, body.note)
        if task is None:
            raise HTTPException(404, "Task not found")
        if task.get("lead_id"):
            store.update_lead(task["lead_id"], lambda l: {**l, "timeline": [*l["timeline"], {
                "at": iso(now_utc()), "kind": "task_done", "text": f"Done: {task['title']}" + (f" — {body.note}" if body.note else "")}]})
        return task

    @router.get("/api/notifications")
    def notifications(request: Request, unread: bool = False, owner: Optional[str] = None):
        from . import workspaces
        user = getattr(request.state, "user", None) or {"role": "owner"}
        security_ok = user.get("role") == "owner"         # failed sign-in alerts are for owners only
        rows = [n for n in workspaces.filter_tasks(store.read("notifications"), store.read("leads"))
                if (not unread or not n.get("read")) and (not owner or n.get("to") in (owner, None))
                and (security_ok or not str(n.get("kind") or "").startswith("security:"))]
        return list(reversed(rows))[:200]

    @router.post("/api/notifications/read")
    def mark_read(request: Request, owner: Optional[str] = None):
        """Mark the caller's notifications read (those addressed to them, plus ones for everybody). `owner` picks a team
        member; a signed-in person without it gets their own. Only the no-password local owner marks everything."""
        user = getattr(request.state, "user", None) or {}
        names = {owner} if owner else ({user.get("name"), user.get("email")} - {None, ""} if user.get("sub") != "local" else set())
        mine = lambda n: not names or n.get("to") is None or n.get("to") in names
        store.update("notifications", lambda rows: [{**n, "read": True} if mine(n) else n for n in rows])
        return {"ok": True}

    @router.post("/api/reminders/run")
    def reminders_now():
        """Send any due reminders now (the background timer also does this every minute)."""
        from .reminders import run_reminders
        from .senders import make_transactional_sender
        return run_reminders(store, now_utc(), transactional_sender or make_transactional_sender(store))

    @router.get("/api/team")
    def get_team():
        settings = store.read("settings")
        return {"caller_name": settings.get("caller_name") or "Sales team", "team": team(store), "notify_email": bool(settings.get("notify_email"))}

    @router.put("/api/team")
    def put_team(body: TeamBody):
        changes = {}
        if body.caller_name is not None:
            changes["caller_name"] = body.caller_name.strip()[:60] or "Sales team"
        if body.team is not None:
            clean = []
            for m in body.team[:50]:
                if isinstance(m, dict) and str(m.get("name", "")).strip():
                    clean.append({k: str(m.get(k, "")).strip()[:120] for k in ("name", "email", "phone", "role")})
            changes["team"] = clean
        if body.notify_email is not None:
            changes["notify_email"] = body.notify_email
        store.update("settings", lambda s: {**s, **changes})
        return get_team()

    return router
