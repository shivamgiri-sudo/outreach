"""Alerts: tell the right person the moment something good (or urgent) happens on a lead.

- A real reply (not an auto-reply or bounce): the lead's owner, else the team caller (a client account: its first admin).
- A lead said yes (interested), a meeting was booked, a call was logged as interested / meeting booked: the owner
  (else the caller) and everyone in the managers list (settings.alert_managers; a client account has its own list in
  its account settings). Alerts stay inside the lead's account: see app/account_alerts.py.
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
          "sla": "Response time missed", "open": "Email opened", "click": "Link clicked", "li_accepted": "Accepted on LinkedIn"}


def _address(store, name: str, account_id: str = "mcn") -> str:
    from . import account_alerts
    return account_alerts.address_of(store, name, account_id or "mcn")


def recipients(store, lead: dict, managers: bool) -> list:
    """Who hears about a lead: its owner (else the account's default person), and the account's own managers list.
    A client account's alerts never reach Mas Callnet's staff, and Mas Callnet's never reach a client."""
    from . import account_alerts, accounts
    aid = accounts.account_of_lead_in(store, lead)
    names = [lead.get("owner") or account_alerts.default_person(store, aid)]
    if managers:
        names += account_alerts.managers(store, aid)
    if aid != accounts.MCN:
        names = [n for n in names if account_alerts.is_member(store, n, aid)]
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
    from . import account_alerts, accounts
    from .reminders import _log_transactional, notify
    lead = next((l for l in store.read("leads") if l.get("id") == lead_id), None)
    if lead is None:
        return []
    aid = accounts.account_of_lead_in(store, lead)
    stamp = aid if aid != accounts.MCN else ""
    company = lead.get("company") or lead.get("contact_name") or "a lead"
    title = f"{TITLES.get(kind, 'Alert')}: {company}"
    done = []
    for name in recipients(store, lead, managers):
        if _recent(store, kind, lead_id, name):
            continue
        notify(store, name, title, text, lead_id=lead_id, kind=f"alert:{kind}", account_id=stamp)
        email = _address(store, name, aid)
        if SENDER and email and account_alerts.email_on(store, aid):
            link = account_alerts.base_url(store, aid, base_url)
            body = f"{text}\n\n{('Open the lead: ' + link + '/#leads') if link else ''}".strip()
            try:
                result = SENDER({"to": email, "subject": f"[Outreach] {title}", "body": body, "kind": "internal"})
            except Exception as exc:                # an alert must never break the action that raised it
                result = {"status": "failed", "error": str(exc)[:200]}
            _log_transactional(store, email, f"[Outreach] {title}", body, "internal", lead_id, result)
        done.append(name)
    return done


DIGEST_LINES = 15


def digest(store, kind: str, items: list, managers: bool = False, base_url: str = "") -> int:
    """One alert per person for many leads at once (items: [(lead_id, text)]), so a batch of breaches is one bell
    entry and one email per person, not one per lead. Each account's people only get that account's leads.
    Returns the number of people alerted."""
    from . import account_alerts, accounts
    from .reminders import _log_transactional, notify
    by_id = {l.get("id"): l for l in store.read("leads")}
    amap = accounts.ws_account_map(store)
    per_person = {}
    for lead_id, text in items:
        lead = by_id.get(lead_id)
        if lead is None:
            continue
        aid = accounts.account_of_lead(amap, lead)
        for name in recipients(store, lead, managers):
            per_person.setdefault((aid, name), []).append((lead, text))
    for (aid, name), rows in per_person.items():
        stamp = aid if aid != accounts.MCN else ""
        link = account_alerts.base_url(store, aid, base_url)
        company = lambda l: l.get("company") or l.get("contact_name") or "a lead"
        title = f"{TITLES.get(kind, 'Alert')}: {company(rows[0][0])}" if len(rows) == 1 else f"{TITLES.get(kind, 'Alert')}: {len(rows)} leads"
        lines = [f"- {company(l)}: {t}" for l, t in rows[:DIGEST_LINES]]
        if len(rows) > DIGEST_LINES:
            lines.append(f"... and {len(rows) - DIGEST_LINES} more.")
        text = rows[0][1] if len(rows) == 1 else "\n".join(lines)
        notify(store, name, title, text, lead_id=rows[0][0].get("id") if len(rows) == 1 else None, kind=f"alert:{kind}", account_id=stamp)
        email = _address(store, name, aid)
        if SENDER and email and account_alerts.email_on(store, aid):
            body = f"{text}\n\n{('See them all: ' + link + '/#reports') if link else ''}".strip()
            try:
                result = SENDER({"to": email, "subject": f"[Outreach] {title}", "body": body, "kind": "internal"})
            except Exception as exc:
                result = {"status": "failed", "error": str(exc)[:200]}
            _log_transactional(store, email, f"[Outreach] {title}", body, "internal", None, result)
    return len(per_person)


def safe(store, kind: str, lead_id: Optional[str], text: str, managers: bool = False) -> None:
    if not lead_id:
        return
    try:
        send(store, kind, lead_id, text, managers)
    except Exception:                               # never let an alert break a reply, call or booking
        pass
