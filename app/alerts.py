"""Alerts: tell the right person the moment something good (or urgent) happens on a lead.

- A real reply (not an auto-reply or bounce): the lead's owner, else the team caller.
- A lead said yes (interested), a meeting was booked, a call was logged as interested / meeting booked: the owner
  (else the caller) and everyone in settings.alert_managers.
- A response-time (SLA) breach: see sla.py.
Each alert is an in-app notification (the bell) plus an email when settings.alerts_email is not switched off and the
person's address is known (a login or a team member). The same alert for the same lead is not repeated within
DEDUPE_MINUTES. SENDER is set by the app at start-up (the transactional email sender).
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

SENDER = None
DEDUPE_MINUTES = 10
TITLES = {"reply": "New reply", "interested": "Lead said yes", "meeting": "Meeting booked", "call_interested": "Interested on a call",
          "sla": "Response time missed", "open": "Email opened", "click": "Link clicked"}


def _address(store, name: str) -> str:
    from . import auth
    from .activities import team
    for u in auth.users(store):
        if name in ((u.get("name") or ""), u.get("email") or ""):
            return u.get("email") or ""
    member = next((m for m in team(store) if m.get("name") == name), {})
    return member.get("email") or ""


def recipients(store, lead: dict, managers: bool) -> list:
    from .activities import caller_name
    names = [lead.get("owner") or caller_name(store)]
    if managers:
        names += [n for n in store.read("settings").get("alert_managers") or [] if isinstance(n, str)]
    return list(dict.fromkeys(n for n in names if n))


def _recent(store, kind: str, lead_id: str, to: str) -> bool:
    cut = (datetime.now(timezone.utc) - timedelta(minutes=DEDUPE_MINUTES)).isoformat()
    for n in reversed(store.read("notifications")[-300:]):
        if n.get("kind") == f"alert:{kind}" and n.get("lead_id") == lead_id and n.get("to") == to:
            try:
                return datetime.fromisoformat(n["at"]).astimezone(timezone.utc).isoformat() >= cut
            except (KeyError, ValueError):
                return False
    return False


def send(store, kind: str, lead_id: str, text: str, managers: bool = False, base_url: str = "") -> list:
    """Raise one alert. Returns the names alerted (empty when nothing was sent)."""
    from .reminders import _log_transactional, notify
    lead = next((l for l in store.read("leads") if l.get("id") == lead_id), None)
    if lead is None:
        return []
    company = lead.get("company") or lead.get("contact_name") or "a lead"
    title = f"{TITLES.get(kind, 'Alert')}: {company}"
    settings = store.read("settings")
    done = []
    for name in recipients(store, lead, managers):
        if _recent(store, kind, lead_id, name):
            continue
        notify(store, name, title, text, lead_id=lead_id, kind=f"alert:{kind}")
        email = _address(store, name)
        if SENDER and email and settings.get("alerts_email", True) is not False:
            link = (settings.get("public_base_url") or base_url or "").rstrip("/")
            body = f"{text}\n\n{('Open the lead: ' + link + '/#leads') if link else ''}".strip()
            try:
                result = SENDER({"to": email, "subject": f"[Outreach] {title}", "body": body, "kind": "internal"})
            except Exception as exc:                # an alert must never break the action that raised it
                result = {"status": "failed", "error": str(exc)[:200]}
            _log_transactional(store, email, f"[Outreach] {title}", body, "internal", lead_id, result)
        done.append(name)
    return done


def safe(store, kind: str, lead_id: Optional[str], text: str, managers: bool = False) -> None:
    if not lead_id:
        return
    try:
        send(store, kind, lead_id, text, managers)
    except Exception:                               # never let an alert break a reply, call or booking
        pass
