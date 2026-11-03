"""Reminders so follow-ups happen at the right time.

Runs every minute from the background timer (and on demand from the API):
- Tasks: an in-app reminder to the owner 15 minutes before the due time, and an "overdue" alert an hour after.
  When "email me reminders" is on and the owner has an email address, the reminder is emailed too.
- Meetings on a lead (`lead.meetings`, status "scheduled"): a reminder to the lead 24 hours and 1 hour before, and a
  heads-up to the owner 15 minutes before, each with the meeting's join link. Cancelled or done meetings get none.
  Emails to leads only go out when real sending is switched on and the lead hasn't unsubscribed; otherwise they are
  kept as practice rows so you can see what would have been sent.
- No-show check: 30 minutes after a meeting ends without being marked done, a task "Mark the meeting done or follow
  up" is created for its owner.
Each reminder is sent once (recorded on the task / meeting).
Reminder emails go to `transactional_outbox`, never the cold-email outbox, so they don't use the daily cold cap.
"""
import uuid
from datetime import datetime, timedelta

from .activities import add_task, caller_name, iso, team, update_task
from .growth_core import public_base_url
from .privacy import is_blocked
from .sequences import IST, identity_footer
from .unsubscribe import unsub_url

TASK_LEAD = timedelta(minutes=15)
OVERDUE_AFTER = timedelta(hours=1)
MEETING_LEAD_REMINDERS = (("24h", timedelta(hours=24)), ("1h", timedelta(hours=1)))
MEETING_OWNER_REMINDER = ("owner_15m", timedelta(minutes=15))
CLOSED_FOR_REMINDERS = ("unsubscribed",)
NO_SHOW_AFTER = timedelta(minutes=30)
NO_SHOW_WINDOW = timedelta(days=7)       # older meetings left "scheduled" are not raised all at once


def ist_text(stamp: str) -> str:
    when = datetime.fromisoformat(stamp).astimezone(IST)
    return when.strftime("%a %d %b, %I:%M %p IST").replace(" 0", " ")


def notify(store, to, title: str, text: str, lead_id=None, task_id=None, kind: str = "reminder", sender=None) -> dict:
    """An in-app notification, plus an email to the person when email reminders are on and we know their address."""
    row = {"id": uuid.uuid4().hex[:10], "at": iso(datetime.now(IST)), "to": to, "title": title, "text": text,
           "lead_id": lead_id, "task_id": task_id, "kind": kind, "read": False}
    store.update("notifications", lambda rows: [*rows[-999:], row])
    settings = store.read("settings")
    member = next((m for m in team(store) if m.get("name") == to), {})
    if sender and settings.get("notify_email") and member.get("email"):
        result = sender({"to": member["email"], "subject": f"[Outreach] {title}", "body": text, "kind": "internal"})
        _log_transactional(store, member["email"], f"[Outreach] {title}", text, "internal", lead_id, result)
    return row


def _log_transactional(store, to, subject, body, kind, lead_id, result, extra: dict = None) -> dict:
    row = {"id": uuid.uuid4().hex[:10], "to": to, "subject": subject, "body": body, "kind": kind, "lead_id": lead_id,
           "status": result.get("status", "dry_run"), "error": result.get("error"), "at": iso(datetime.now(IST)), **(extra or {})}
    store.update("transactional_outbox", lambda rows: [*rows[-4999:], row])
    return row


def _task_reminders(store, now: datetime, sender) -> int:
    sent = 0
    leads = {l["id"]: l for l in store.read("leads")}
    for task in store.read("tasks"):
        if task.get("status", "open") != "open" or task.get("done") or not task.get("due_at") or not task.get("id"):
            continue
        due = datetime.fromisoformat(task["due_at"])
        done = set(task.get("reminded") or [])
        company = (leads.get(task.get("lead_id")) or {}).get("company") or ""
        owner = task.get("owner") or caller_name(store)
        label = None
        if "due" not in done and now >= due - TASK_LEAD:
            label, title = "due", f"Due {ist_text(task['due_at'])}: {task.get('title', 'Task')}" + (f" — {company}" if company else "")
        elif "overdue" not in done and now >= due + OVERDUE_AFTER:
            label, title = "overdue", f"Overdue: {task.get('title', 'Task')}" + (f" — {company}" if company else "")
        if not label:
            continue
        notify(store, owner, title, task.get("text", ""), task.get("lead_id"), task["id"], kind=f"task_{label}", sender=sender)
        store.update("tasks", lambda rows, tid=task["id"], label=label: [
            {**t, "reminded": sorted(set(t.get("reminded") or []) | {label}, key=str)} if t.get("id") == tid else t for t in rows])
        sent += 1
    return sent


def _meeting_email(store, lead: dict, meeting: dict, which: str, base_url: str = "") -> dict:
    when = ist_text(meeting["start"])
    company = lead.get("company") or "your team"
    lines = [f"Hi {lead.get('contact_name', '').split(' ')[0] or 'there'},", "",
             f"A reminder of our call {'tomorrow' if which == '24h' else 'in about an hour'}: {meeting.get('title') or 'BookMyInterview.ai demo — ' + company}",
             f"When: {when}"]
    if meeting.get("join_url"):
        lines.append(f"Join: {meeting['join_url']}")
    lines += ["", "If the time no longer works, just reply and we'll find another slot.", "",
              f"— {store.read('settings').get('sender_name') or caller_name(store)}"]
    footer = identity_footer(store.read("settings"))
    if footer:
        lines.append(footer)
    lines.append(f"Unsubscribe: {unsub_url(store, base_url, lead['id'])}")
    return {"to": lead.get("email"), "subject": f"Reminder: {meeting.get('title') or 'our call'} — {when}", "body": "\n".join(lines), "kind": "meeting_reminder"}


def _meeting_reminders(store, now: datetime, sender, base_url: str) -> int:
    sent = 0
    live = bool(store.read("settings").get("live_sending"))
    for lead in store.read("leads"):
        for meeting in lead.get("meetings") or []:
            if meeting.get("status") != "scheduled" or not meeting.get("start"):
                continue
            start = datetime.fromisoformat(meeting["start"])
            if start < now:
                continue
            done = set(meeting.get("reminders_sent") or [])
            due = []
            for label, before in MEETING_LEAD_REMINDERS:
                # only the nearest reminder that's due (no 24h reminder when the meeting is already an hour away)
                if label not in done and now >= start - before and not (label == "24h" and start - now <= timedelta(hours=2)):
                    due.append(label)
            owner_label, owner_before = MEETING_OWNER_REMINDER
            if owner_label not in done and now >= start - owner_before:
                due.append(owner_label)
            for label in due:
                if label == owner_label:
                    notify(store, meeting.get("owner") or caller_name(store), f"Meeting at {ist_text(meeting['start'])} — {lead.get('company', '')}",
                           meeting.get("join_url", ""), lead["id"], kind="meeting_soon", sender=sender)
                elif lead.get("email") and not is_blocked(store, lead["email"]) and lead["stage"] not in CLOSED_FOR_REMINDERS:
                    message = _meeting_email(store, lead, meeting, label, base_url)
                    result = sender(message) if (live and sender) else {"status": "dry_run"}
                    _log_transactional(store, message["to"], message["subject"], message["body"], "meeting_reminder", lead["id"], result)
                sent += 1

            if due:
                def mark(l, mid=meeting.get("id"), labels=tuple(due)):
                    meetings = [{**m, "reminders_sent": sorted(set(m.get("reminders_sent") or []) | set(labels))} if m.get("id") == mid else m
                                for m in l.get("meetings") or []]
                    return {**l, "meetings": meetings}
                store.update_lead(lead["id"], mark)
    return sent


def _no_show_followups(store, now: datetime) -> int:
    """Once per meeting: 30 minutes after it ends without being marked done, ask the owner to close it or follow up."""
    from .channels.invites import meeting_end
    created = 0
    for lead in store.read("leads"):
        for meeting in lead.get("meetings") or []:
            if meeting.get("status") != "scheduled" or not meeting.get("start") or "no_show" in (meeting.get("reminders_sent") or []):
                continue
            try:
                end = meeting_end(meeting)
            except ValueError:
                continue
            if not end + NO_SHOW_AFTER <= now <= end + NO_SHOW_WINDOW:
                continue
            text = (f"{meeting.get('title') or 'The meeting'} with {lead.get('company') or 'this lead'} ended at {ist_text(end.isoformat())}. "
                    "Mark it done if it happened; if they didn't join, follow up to rebook.")
            task = add_task(store, lead["id"], "meeting_follow_up", text, due_at=now, owner=meeting.get("owner"),
                            priority="high", title="Mark the meeting done or follow up", source="auto")
            update_task(store, task["id"], lambda t, mid=meeting.get("id"): {**t, "meeting_id": mid})

            def mark(l, mid=meeting.get("id")):
                return {**l, "meetings": [{**m, "reminders_sent": sorted(set(m.get("reminders_sent") or []) | {"no_show"})}
                                          if m.get("id") == mid else m for m in l.get("meetings") or []]}
            store.update_lead(lead["id"], mark)
            created += 1
    return created


def run_reminders(store, now: datetime, sender=None, base_url: str = "") -> dict:
    return {"task_reminders": _task_reminders(store, now, sender),
            "meeting_reminders": _meeting_reminders(store, now, sender, public_base_url(store, base_url)),
            "no_show_tasks": _no_show_followups(store, now)}
