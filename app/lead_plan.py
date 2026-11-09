"""One lead's communication plan: every email and LinkedIn step with what happened (sent when, by whom: the system or a
person), what is next and when, why it stopped, the open follow-ups, and a list of what the system did by itself.

GET /api/leads/{id}/plan (routes_contacts). Read-only: built from the lead, its tasks, the outbox and the agent log.
"""
import re
from datetime import datetime, timedelta
from typing import Optional

from . import linkedin_agent, linkedin_seq as li, sequence_library
from .analytics_range import _t

LI_STATUS = {"waiting_to_connect": "Connection request not sent yet", "waiting_for_acceptance": "Waiting for them to accept",
             "messaging": "Connected: follow-up messages running", "finished": "All steps sent", "stopped": "Stopped"}
STOP_WORDS = {"replied": "They replied", "send_failed": "An email could not be sent", "unsubscribed": "They unsubscribed"}


def _iso(t: Optional[datetime]) -> Optional[str]:
    return t.isoformat(timespec="seconds") if t else None


def _linkedin(store, lead: dict, settings: dict, tasks: list) -> Optional[dict]:
    state = lead.get("linkedin_seq")
    if not state:
        return None
    sent_at, ai_steps = {}, {}
    for e in lead.get("timeline") or []:
        text = str(e.get("text") or "")
        if e.get("kind") == "linkedin" and text.startswith("LinkedIn step ") and text.endswith(" sent"):
            try:
                n = int(text.split()[2])
            except (ValueError, IndexError):
                continue
            sent_at[n] = _t(e.get("at"))
            if (e.get("origin") or {}).get("ai_drafted"):
                ai_steps[n] = (e.get("origin") or {}).get("ai_approved_by") or "a person"
    agent_rows = [(r, _t(r.get("at"))) for r in linkedin_agent.log_rows(store) if r.get("lead_id") == lead["id"]]

    def by(at):
        if at and any(r["kind"] in ("connect", "message") and t and abs((t - at).total_seconds()) < 180 for r, t in agent_rows):
            return "LinkedIn agent (automatic)"
        return "A person" if at else ""
    open_task = {}
    for t in tasks:
        if t.get("kind") == "linkedin" and t.get("status", "open") == "open":
            n = re.search(r"step (\d+)", t.get("title", ""))
            if n:
                open_task[int(n.group(1))] = t
    steps, done = [], set(state.get("done") or [])
    for s in li.lead_steps(lead, settings):
        row = {"n": s["n"], "kind": s["kind"], "purpose": s.get("purpose", "")}
        if s["n"] in done:
            at = sent_at.get(s["n"])
            row.update(state="done", at=_iso(at), by=by(at), **({"note": f"AI-drafted note, approved by {ai_steps[s['n']]}"} if s["n"] in ai_steps else {}))
        elif s.get("needs") and not settings.get(s["needs"]):
            row.update(state="skipped", note="Skipped until a case-study link is set")
        elif s["n"] in open_task:
            row.update(state="next", due_at=open_task[s["n"]].get("due_at"))
        elif state.get("status") in ("stopped", "finished"):
            row.update(state="not_sent")
        elif s["kind"] == "message" and not state.get("accepted_at"):
            row.update(state="waiting", note="Starts after they accept")
        else:
            row.update(state="later")
        steps.append(row)
    return {"status": state.get("status"), "status_text": LI_STATUS.get(state.get("status"), state.get("status") or ""),
            "stopped": STOP_WORDS.get(state.get("stopped"), state.get("stopped")), "account": state.get("account") or "",
            "started_at": state.get("started_at"), "auto_started": bool(state.get("auto_started")),
            "accepted_at": state.get("accepted_at"), "replied_at": state.get("replied_at"), "steps": steps}


def _email(store, lead: dict) -> Optional[dict]:
    seq = lead.get("sequence")
    if not seq:
        return None
    plan = sequence_library.steps_for(store, seq) or []
    live = bool(seq.get("sent_days"))
    days = seq.get("sent_days" if live or not seq.get("practice_days") else "practice_days") or []
    rows = {}
    for m in store.read("outbox"):
        if m.get("lead_id") == lead["id"] and str(m.get("idempotency_key", "")).count(":") >= 2:
            try:
                rows[int(m["idempotency_key"].split(":")[1])] = m
            except ValueError:
                continue
    enrolled = _t(seq.get("enrolled_at"))
    last = _t(seq.get("last_sent_at") or seq.get("practice_last_at"))
    steps, prev_day, next_found = [], None, False
    for s in plan:
        m = rows.get(s["day"]) or {}
        row = {"day": s["day"] + 1, "name": s.get("name", ""), "subject": s.get("subject", "")}
        if s["day"] in days:
            row.update(state="done", at=m.get("sent_at"), by="Email sequence (automatic)", status=m.get("status", ""),
                       opens=m.get("opens", 0), clicks=m.get("clicks", 0))
        elif seq.get("stopped"):
            row.update(state="not_sent")
        elif not next_found:
            due = enrolled + timedelta(days=s["day"]) if enrolled else None
            if last and prev_day is not None:
                due = max(due or last, last + timedelta(days=s["day"] - prev_day))
            row.update(state="next", due_at=_iso(due), note=seq.get("blocked") or "")
            next_found = True
        else:
            row.update(state="later")
        if s["day"] in days:
            prev_day = s["day"]
        steps.append(row)
    return {"sequence": seq.get("sequence_id") or "default", "enrolled_at": seq.get("enrolled_at"), "practice": not live and bool(days),
            "stopped": STOP_WORDS.get(seq.get("stopped"), seq.get("stopped")), "blocked": seq.get("blocked") or "", "steps": steps}


def _automatic(store, lead: dict) -> list:
    """What the system did by itself for this lead, newest first."""
    out = []
    for r in linkedin_agent.log_rows(store):
        if r.get("lead_id") == lead["id"] and r.get("kind") in ("connect", "message", "accepted", "replied", "error"):
            out.append({"at": r["at"], "who": "LinkedIn agent", "what": r.get("note") or r["kind"]})
    for m in store.read("outbox"):
        if m.get("lead_id") == lead["id"] and m.get("status") in ("sent", "delivered", "dry_run"):
            out.append({"at": m.get("sent_at"), "who": "Email sequence", "what": f"{'Practice email' if m['status'] == 'dry_run' else 'Email sent'}: {m.get('subject', '')}"})
    for e in lead.get("timeline") or []:
        if e.get("auto"):
            out.append({"at": e.get("at"), "who": "Auto-start", "what": e.get("text", "")})
        elif e.get("kind") in ("email_open", "email_click"):
            out.append({"at": e.get("at"), "who": "Tracking", "what": e.get("text", "")})
    return sorted((x for x in out if x.get("at")), key=lambda x: x["at"], reverse=True)[:60]


def build(store, lead: dict) -> dict:
    settings = store.read("settings")
    tasks = [t for t in store.read("tasks") if t.get("lead_id") == lead["id"]]
    follow_ups = sorted(({"title": t.get("title") or t.get("kind"), "kind": t.get("kind"), "due_at": t.get("due_at"), "owner": t.get("owner") or ""}
                         for t in tasks if t.get("status", "open") == "open"), key=lambda t: t["due_at"] or "")
    return {"linkedin": _linkedin(store, lead, settings, tasks), "email": _email(store, lead), "follow_ups": follow_ups,
            "automatic": _automatic(store, lead)}
