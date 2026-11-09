"""Reply inbox: GET /api/reply-inbox (every reply from every channel, scored) and the triage a person does on it:
mark handled / unhandled, read / unread, assign to a team member, snooze until a time (creates a task), correct the
label. Triage is kept per reply key in settings.reply_state; it never changes the lead itself.
GET /api/leads/{id}/thread lists every email / message in and out for one lead, oldest first."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .activities import _when, add_task, iso, lead_activity, now_utc
from .lead_ops import owner_choices
from .reply_inbox import BASE, LABELS_SHOWN, build_inbox

ACTIONS = ("handled", "unhandled", "read", "unread", "assign", "snooze", "label")
MAX_STATE = 5000
THREAD_TYPES = ("email", "reply", "whatsapp", "sms", "linkedin")


class ReplyAction(BaseModel):
    key: str
    action: str
    assignee: Optional[str] = None
    until: Optional[str] = None          # ISO date-time (no zone = IST) for snooze
    label: Optional[str] = None


def _who(store, request: Request) -> str:
    from .rep_workflow import actor_name
    return actor_name(store, getattr(request.state, "user", None))


def _subject_for(lead_id: str, at: str, inbox: list, outbox: list) -> str:
    """The subject to reply to: the captured email's own subject, else the last email we sent this lead."""
    mine = [m for m in inbox if m.get("lead_id") == lead_id and m.get("subject")]
    exact = next((m for m in mine if (m.get("received_at") or "")[:16] == (at or "")[:16]), None)
    if exact or mine:
        return (exact or mine[-1])["subject"]
    sent = [m for m in outbox if m.get("lead_id") == lead_id and m.get("subject")]
    return sent[-1]["subject"] if sent else ""


def make_router(store) -> APIRouter:
    router = APIRouter()

    @router.get("/api/reply-inbox")
    def inbox(request: Request, label: str = "", channel: str = "", only_action: bool = False, limit: int = 200, assignee: str = ""):
        from . import workspaces
        data = build_inbox(workspaces.filter_rows(store.read("leads")), label=label if label in LABELS_SHOWN else "", channel=channel[:20],
                           only_action=only_action, limit=limit, state=store.read("settings").get("reply_state") or {},
                           assignee=assignee[:120])
        inbox_rows, outbox_rows = store.read("inbox"), store.read("outbox")
        for r in data["replies"]:
            r["subject"] = _subject_for(r["lead_id"], r["at"], inbox_rows, outbox_rows)
        data["labels"] = list(LABELS_SHOWN)
        data["team"] = owner_choices(store, getattr(request.state, "user", None))   # as for lead owners (scoped for limited logins)
        return data

    @router.post("/api/reply-inbox/action")
    def act(body: ReplyAction, request: Request):
        if body.action not in ACTIONS:
            raise HTTPException(400, f"action must be one of: {', '.join(ACTIONS)}")
        lead_id = body.key.split("|", 1)[0]
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        from . import workspaces
        if lead is None or "|" not in body.key or not workspaces.visible(lead):
            raise HTTPException(404, "Reply not found")
        who, stamp = _who(store, request), iso(now_utc())
        change, task = {}, None
        if body.action == "handled":
            change = {"handled": True, "handled_by": who, "handled_at": stamp, "read": True}
        elif body.action == "unhandled":
            change = {"handled": False, "handled_by": None, "handled_at": None}
        elif body.action in ("read", "unread"):
            change = {"read": body.action == "read"}
        elif body.action == "assign":
            name = (body.assignee or "").strip()[:120]
            if name and name not in owner_choices(store, getattr(request.state, "user", None)):
                raise HTTPException(400, "Pick someone from the team (a login, or Tasks → Team & reminders).")
            change = {"assignee": name, "assigned_by": who, "assigned_at": stamp}
        elif body.action == "label":
            if body.label not in BASE:
                raise HTTPException(400, f"label must be one of: {', '.join(BASE)}")
            change = {"label": body.label, "labelled_by": who, "labelled_at": stamp}
        elif body.action == "snooze":
            try:
                until = datetime.fromisoformat((body.until or "").replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(400, "until must be a date and time like 2026-11-07T11:00:00+05:30") from None
            until = until if until.tzinfo else until.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
            if until <= now_utc():
                raise HTTPException(400, "Pick a time in the future.")
            current = (store.read("settings").get("reply_state") or {}).get(body.key) or {}
            owner = current.get("assignee") or who
            task = add_task(store, lead_id, "follow_up", f"Snoozed reply from {lead.get('company') or lead.get('contact_name') or 'this lead'}: answer it now.",
                            due_at=until, owner=owner, title="Answer a snoozed reply", source="person")
            change = {"snoozed_until": iso(until), "snoozed_by": who, "task_id": task["id"]}

        def apply(settings: dict) -> dict:
            state = dict(settings.get("reply_state") or {})
            state[body.key] = {**(state.get(body.key) or {}), **change}
            if len(state) > MAX_STATE:                    # keep the newest triage only
                state = dict(list(state.items())[-MAX_STATE:])
            return {**settings, "reply_state": state}
        store.update("settings", apply)
        if body.action in ("handled", "assign", "label"):
            text = {"handled": f"Reply marked handled by {who}", "assign": f"Reply assigned to {change.get('assignee') or 'nobody'} by {who}",
                    "label": f"Reply label corrected to {body.label} by {who}"}[body.action]
            store.update_lead(lead_id, lambda l: {**l, "timeline": [*l["timeline"], {"at": stamp, "kind": "reply_triage", "text": text, "by": who}]})
        return {"key": body.key, "state": (store.read("settings").get("reply_state") or {}).get(body.key), "task": task}

    @router.get("/api/leads/{lead_id}/thread")
    def thread(lead_id: str):
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None:
            raise HTTPException(404, "Lead not found")
        items = [i for i in lead_activity(store, lead) if i["type"] in THREAD_TYPES and i.get("direction") in ("in", "out")]
        items = sorted(items, key=_when)                   # oldest first, like a conversation (ties keep sent-before-reply)
        return {"lead_id": lead_id, "company": lead.get("company") or "", "contact": lead.get("contact_name") or "",
                "email": lead.get("email") or "", "messages": items}

    return router
