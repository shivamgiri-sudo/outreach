"""Rep workflow API: GET /api/my-day, targets, the call queue, call outcomes, the call script and stage options."""
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import rep_workflow as rw
from .activities import CALL_OUTCOMES, ensure_task_ids, iso, now_utc, task_buckets
from .reply_inbox import build_inbox
from .sequences import IST
from .workflows import fire

MAX_LIST = 25


class OutcomeBody(BaseModel):
    outcome: str
    notes: str = ""
    callback_at: Optional[str] = None       # call_back: when (ISO; no zone = IST)
    to: str = Field(default="", max_length=60)   # the number dialled (main or an extra one); default the main one


class TargetsBody(BaseModel):
    scope: str = "me"                        # "me", "team" (owner), or a person's email / name key (owner)
    targets: dict


class ScriptBody(BaseModel):
    script: List[dict]
    objections: List[dict] = []


def _user(request: Request) -> dict:
    return getattr(request.state, "user", None) or {}


def _when(raw: Optional[str], field: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"{field} must be a date and time like 2026-11-07T15:30:00+05:30") from None
    return when if when.tzinfo else when.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))


def make_router(store, present) -> APIRouter:
    router = APIRouter()

    def lead_or_404(lead_id: str) -> dict:
        lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
        if lead is None:
            raise HTTPException(404, "Lead not found")
        return lead

    @router.get("/api/my-day")
    def my_day(request: Request):
        """Everything the signed-in person should do today, with targets and their own numbers."""
        me = rw.me_of(store, _user(request))
        now = now_utc()
        today = now.astimezone(IST).date().isoformat()
        ensure_task_ids(store)
        from . import workspaces
        all_leads = store.read("leads")
        hidden = workspaces.hidden_ids(all_leads)            # another workspace's leads, and their tasks, stay out
        leads = {l["id"]: l for l in all_leads if l["id"] not in hidden}
        open_tasks = [t for t in store.read("tasks") if t.get("status", "open") == "open" and not t.get("done") and t.get("lead_id") not in hidden]
        task_owner = {t.get("lead_id"): t.get("owner") for t in open_tasks if t.get("lead_id") and t.get("owner")}
        lead_owner = lambda lead: lead.get("owner") or task_owner.get(lead["id"])      # no owner field yet: the task owner

        def with_lead(t: dict) -> dict:
            lead = leads.get(t.get("lead_id")) or {}
            closed = lead.get("stage") in ("unsubscribed",) and t.get("kind") not in ("review", "erasure_request")
            return {**t, "company": lead.get("company") or lead.get("domain") or "", "contact_name": lead.get("contact_name") or "",
                    "phone": lead.get("phone") or "", "email": lead.get("email") or "", "lead_stage": lead.get("stage"),
                    "next": rw.next_action(lead, task=t) if lead else {"action": "open", "label": "Open", "why": ""}, "_closed": closed}
        mine = [with_lead(t) for t in open_tasks if t.get("owner") in me["names"]]
        mine = [t for t in mine if not t.pop("_closed")]
        buckets = task_buckets(mine, now)
        due = buckets["overdue"] + buckets["today"]
        callbacks = [t for t in due if t.get("kind") == "call" and t.get("source") == "callback"]
        calls_due = [t for t in due if t.get("kind") == "call" and t.get("source") != "callback"]
        other_tasks = [t for t in due if t.get("kind") != "call"]

        inbox = build_inbox(list(leads.values()), now=now, only_action=True, limit=200,
                            state=store.read("settings").get("reply_state") or {})
        replies = []
        for r in inbox["replies"]:
            lead = leads.get(r["lead_id"]) or {}
            owner = r["assignee"] or lead_owner(lead)
            if r["assignee"] in me["names"] or (not r["assignee"] and (not owner or owner in me["names"])):
                replies.append({**r, "phone": lead.get("phone") or "", "email": lead.get("email") or "",
                                "next": rw.next_action(lead, reply=r)})

        activities = store.read("activities")
        handled_leads = {t.get("lead_id") for t in due}
        hot = []
        for lead in leads.values():
            if lead["id"] in handled_leads or lead.get("stage") in rw.CLOSED or not rw.phone_ok(lead):
                continue
            owner = lead_owner(lead)
            if owner and owner not in me["names"]:
                continue
            p = present(lead)
            if p.get("band") != "hot" or rw.called_today(activities, lead, today):
                continue
            hot.append({**rw.lead_card(lead, p), "next": rw.next_action(lead)})
        hot.sort(key=lambda l: -(l.get("score") or 0))

        meetings = []
        for lead in leads.values():
            for m in lead.get("meetings") or []:
                if m.get("status", "scheduled") == "scheduled" and rw.ist_day(m.get("start")) == today:
                    meetings.append({"lead_id": lead["id"], "company": lead.get("company") or "", "title": m.get("title") or "Meeting",
                                     "start": m.get("start"), "end": m.get("end"), "join_url": m.get("join_url") or "",
                                     "owner": m.get("owner") or lead_owner(lead)})
        meetings = sorted((m for m in meetings if not m["owner"] or m["owner"] in me["names"]), key=lambda m: m["start"] or "")

        targets = rw.targets_for(store, me)
        done_today = rw.my_counts(store, me["names"], today)
        done_week = rw.my_counts(store, me["names"], rw.week_start(now))
        week_tasks_done = sum(1 for t in store.read("tasks") if t.get("status") == "done" and t.get("owner") in me["names"]
                              and (rw.ist_day(t.get("done_at")) or "") >= rw.week_start(now))
        return {
            "me": {"name": me["name"], "email": me["email"], "role": me["role"]},
            "date": today,
            "callbacks": callbacks[:MAX_LIST], "calls_due": calls_due[:MAX_LIST], "tasks": other_tasks[:MAX_LIST],
            "replies": replies[:MAX_LIST], "hot_leads": hot[:MAX_LIST // 2], "meetings": meetings[:MAX_LIST],
            "targets": targets["targets"], "team_defaults": targets["team_defaults"], "personal_targets": targets["personal"],
            "today": done_today, "week": {**done_week, "tasks_done": week_tasks_done},
            "counts": {"callbacks": len(callbacks), "calls_due": len(calls_due), "tasks": len(other_tasks), "overdue": len(buckets["overdue"]),
                       "replies": len(replies), "hot_leads": len(hot), "meetings": len(meetings), "upcoming": len(buckets["upcoming"])},
        }

    @router.put("/api/my-day/targets")
    def put_targets(body: TargetsBody, request: Request):
        user = _user(request)
        me = rw.me_of(store, user)
        try:
            values = rw.clean_targets(body.targets)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        is_owner = me["role"] == "owner"
        if body.scope == "me":
            rw.save_targets(store, me["key"], values)
        elif not is_owner:
            raise HTTPException(403, "Only an owner can set targets for the team or for someone else.")
        elif body.scope == "team":
            rw.save_targets(store, None, values)
        else:
            rw.save_targets(store, body.scope.strip().lower()[:200], values)
        return rw.targets_for(store, me)

    @router.get("/api/call-queue")
    def queue(request: Request, mine: bool = False):
        me = rw.me_of(store, _user(request))
        rows = rw.call_queue(store, present, now_utc(), me["names"] if mine else None)
        return {"queue": rows, "count": len(rows), "me": me["name"]}

    @router.post("/api/leads/{lead_id}/call-outcome")
    def call_outcome(lead_id: str, body: OutcomeBody, request: Request):
        lead_or_404(lead_id)
        if body.outcome not in CALL_OUTCOMES:
            raise HTTPException(400, f"outcome must be one of: {', '.join(CALL_OUTCOMES)}")
        who = rw.actor_name(store, _user(request))
        before = lead_or_404(lead_id)["stage"]
        try:
            result = rw.record_call_outcome(store, lead_id, body.outcome, body.notes.strip()[:4000], who,
                                            _when(body.callback_at, "callback_at"), to=body.to.strip())
        except rw.OutcomeRefused as exc:
            raise HTTPException(400, str(exc)) from None
        if result["stage"] != before:
            fire(store, "stage_change", result["stage"], lead_id)
        return {**result, "lead": present(lead_or_404(lead_id))}

    @router.get("/api/call-script")
    def get_script():
        return rw.call_script(store)

    @router.put("/api/call-script")
    def put_script(body: ScriptBody, request: Request):
        user = _user(request)
        if user and user.get("role") != "owner":
            raise HTTPException(403, "Only an owner can change the call script.")
        try:
            script, objections = rw.clean_script(body.script, body.objections)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        who = rw.actor_name(store, user)
        from . import audit_settings
        with audit_settings.watch(store, request, "Call script"):
            store.update("settings", lambda s: {**s, "call_script": {"script": script, "objections": objections,
                                                                     "updated_by": who, "updated_at": iso(now_utc())}})
        return rw.call_script(store)

    @router.delete("/api/call-script")
    def reset_script(request: Request):
        user = _user(request)
        if user and user.get("role") != "owner":
            raise HTTPException(403, "Only an owner can change the call script.")
        from . import audit_settings
        with audit_settings.watch(store, request, "Call script (back to the built-in one)"):
            store.update("settings", lambda s: {k: v for k, v in s.items() if k != "call_script"})
        return rw.call_script(store)

    @router.get("/api/leads/{lead_id}/stage-options")
    def stage_options(lead_id: str, request: Request):
        lead = lead_or_404(lead_id)
        return {"stage": lead["stage"], "options": rw.stage_options(lead, _user(request).get("role", "owner") == "owner")}

    return router
