"""Why has nothing been sent to this lead? An ordered list of reasons (first blocking reason first). Read-only: it mirrors the
checks the agent's path makes (extension_api.describe, the guard, agent.control) and adds what the agent reported."""
from datetime import datetime
from typing import Optional

from . import extension_api as ext, linkedin_agent as agent, linkedin_guard as guard, linkedin_seq as li, workspaces
from .activities import now_utc

STALE_INVITE_DAYS = 30


def reasons(store, lead: dict, now: Optional[datetime] = None) -> list:
    """[{code, severity: block|wait|info, text}] in the order the agent would hit them."""
    now = now or now_utc()
    out, seq = [], lead.get("linkedin_seq") or {}
    add = lambda code, sev, text: out.append({"code": code, "severity": sev, "text": text})
    if lead.get("stage") in li.CLOSED:
        add("closed", "block", f"Lead is {lead['stage']}; never contacted.")
    if not li.has_profile_link(lead):
        add("no_link", "block", "No LinkedIn person profile link (linkedin.com/in/...). A profile lookup may be pending.")
    if lead.get("linkedin_wrong_send"):
        add("wrong_send", "block", lead["linkedin_wrong_send"].get("why", "A wrong send was flagged."))
    if li.target_changed(lead):
        add("target_changed", "block", "The name or profile link changed after the sequence started. Stop it and start it again.")
    if not seq:
        add("not_started", "block", "Not on the LinkedIn sequence yet.")
    else:
        acct = seq.get("account", "")
        info = ext.describe(store, lead, acct)
        if info["reason"]:
            add("sequence", "block" if seq.get("status") in ("stopped", "finished") else "wait", info["reason"])
        if seq.get("status") == "waiting_for_acceptance" and seq.get("last_sent_at"):
            days = (now - datetime.fromisoformat(seq["last_sent_at"])).days
            if days >= STALE_INVITE_DAYS:
                add("stale_invite", "block", f"Invitation pending {days} days: withdraw it on LinkedIn and close this lead.")
            else:
                add("waiting_accept", "wait", f"Invitation pending {days} day(s); only acceptance checks run (each lead every {int(guard.CHECK_EVERY.total_seconds() // 3600)} h).")
        until = guard.held_until(lead, now)
        if until:
            add("held", "block", f"Held after {guard.MAX_TRIES} failed tries in 24 h, until {until.isoformat(timespec='minutes')}.")
        ctl = agent.control(store, now, account=acct, owner=agent.owner_of_slug(store, acct))
        if ctl["why_not"]:
            add("agent", "wait", f"Agent: {ctl['why_not']}.")
        if ctl["wait_seconds"]:
            add("gap", "wait", f"Next action allowed in {ctl['wait_seconds']} s (gap between actions).")
        kind = (info.get("message") or {}).get("kind")
        if kind == "connect" and not ctl["connections_left_today"]:
            add("cap", "wait", "Connection cap reached for today or this week.")
        if kind == "message" and not ctl["messages_left_today"]:
            add("cap", "wait", "Message cap reached for today.")
    if not workspaces.visible(lead):
        add("scope", "info", "Lead is outside the selected workspace.")
    mine = [r for r in agent.log_rows(store) if r.get("lead_id") == lead["id"]]
    for r in mine[-3:]:
        add("agent_event", "info", f"{r['at'][:16].replace('T', ' ')} UTC  {r['kind']}: {r.get('note', '')}")
    if not out:
        add("ready", "info", "Nothing blocks it: it waits for the agent's next turn.")
    return out


def stuck(store, leads: list, now: Optional[datetime] = None) -> list:
    """Leads on the sequence that will not move without a person: held, invitation pending 30+ days, wrong send flagged."""
    rows = []
    for l in leads:
        if not l.get("linkedin_seq") and not l.get("linkedin_wrong_send"):
            continue
        hit = [r for r in reasons(store, l, now) if r["code"] in ("held", "stale_invite", "wrong_send", "target_changed")]
        if hit:
            rows.append({"lead_id": l["id"], "name": l.get("contact_name"), "company": l.get("company"), "reasons": hit})
    return rows
