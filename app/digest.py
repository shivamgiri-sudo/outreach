"""The caller's morning summary: once per weekday, at or after 09:30 IST, from the background minute timer.

It lists calls due today, overdue tasks, leads who said yes since the last summary, meetings today and campaign runs
in the last 24 hours. It goes out as an in-app notification (and an email when email reminders are on). A
`last_digest_date` setting, claimed atomically before sending, makes sure it is sent once a day.
"""
from datetime import datetime, time, timedelta
from typing import Optional

from .activities import _normalise_task, caller_name, iso
from .reminders import notify
from .sequences import IST

DIGEST_TIME = time(9, 30)
MAX_LOOKBACK = timedelta(days=7)
DEFAULT_LOOKBACK = timedelta(hours=24)
MAX_LINES = 5


def is_due(now: datetime, settings: dict) -> bool:
    local = now.astimezone(IST)
    return local.weekday() < 5 and local.time() >= DIGEST_TIME and settings.get("last_digest_date") != local.date().isoformat()


def _since(settings: dict, now: datetime) -> datetime:
    last = settings.get("last_digest_at")
    try:
        since = datetime.fromisoformat(last) if last else now - DEFAULT_LOOKBACK
    except ValueError:
        since = now - DEFAULT_LOOKBACK
    return max(since, now - MAX_LOOKBACK)


def build_digest(store, now: datetime, since: Optional[datetime] = None) -> dict:
    """What the caller needs to know this morning."""
    caller = caller_name(store)
    since = since or now - DEFAULT_LOOKBACK
    today = now.astimezone(IST).date()
    from . import accounts
    amap = accounts.ws_account_map(store)            # the caller's summary is Mas Callnet's: never a client account's leads or tasks
    leads = {l["id"]: l for l in store.read("leads") if accounts.account_of_lead(amap, l) == accounts.MCN}
    other = {l["id"] for l in store.read("leads")} - set(leads)
    mine = [t for t in (_normalise_task(t) for t in store.read("tasks"))
            if t["status"] == "open" and t.get("owner") in (caller, None) and t.get("due_at") and t.get("lead_id") not in other]
    due_day = lambda t: datetime.fromisoformat(t["due_at"]).astimezone(IST).date()
    calls_today = sorted((t for t in mine if t.get("kind") == "call" and due_day(t) == today), key=lambda t: t["due_at"])
    overdue = sorted((t for t in mine if due_day(t) < today), key=lambda t: t["due_at"])
    interested = []
    for lead in leads.values():
        consent_at = (lead.get("consent") or {}).get("at")
        if lead["stage"] == "interested" and consent_at and datetime.fromisoformat(consent_at) >= since:
            interested.append(lead)
    meetings = sorted(((m, lead) for lead in leads.values() for m in lead.get("meetings") or []
                       if m.get("status") == "scheduled" and m.get("start")
                       and datetime.fromisoformat(m["start"]).astimezone(IST).date() == today), key=lambda pair: pair[0]["start"])
    day_ago = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    runs = [(c, r) for c in store.read("campaigns") for r in c.get("runs") or [] if (r.get("started_at") or "") >= day_ago]
    return {"caller": caller, "calls_today": calls_today, "overdue": overdue, "interested": interested,
            "meetings": meetings, "campaign_runs": runs, "leads": leads}


def _company(leads: dict, lead_id) -> str:
    lead = leads.get(lead_id) or {}
    return lead.get("company") or lead.get("domain") or ""


def digest_text(d: dict) -> tuple:
    leads = d["leads"]
    title = (f"Good morning {d['caller']}: {len(d['calls_today'])} call(s) today, {len(d['overdue'])} overdue, "
             f"{len(d['interested'])} new yes, {len(d['meetings'])} meeting(s)")
    lines = [f"Calls due today: {len(d['calls_today'])}"]
    lines += [f"  - {_company(leads, t.get('lead_id')) or t.get('title')}" for t in d["calls_today"][:MAX_LINES]]
    lines.append(f"Overdue tasks: {len(d['overdue'])}")
    lines += [f"  - {t.get('title')}" + (f" ({_company(leads, t.get('lead_id'))})" if t.get("lead_id") else "") for t in d["overdue"][:MAX_LINES]]
    lines.append(f"New interested leads: {len(d['interested'])}")
    lines += [f"  - {l.get('company') or l.get('domain')}" for l in d["interested"][:MAX_LINES]]
    lines.append(f"Meetings today: {len(d['meetings'])}")
    lines += [f"  - {datetime.fromisoformat(m['start']).astimezone(IST).strftime('%I:%M %p').lstrip('0')} IST "
              f"{lead.get('company', '')} {m.get('join_url', '')}".rstrip() for m, lead in d["meetings"][:MAX_LINES]]
    lines.append(f"Campaign runs in the last 24 hours: {len(d['campaign_runs'])}")
    lines += [f"  - {c['name']}: {r.get('new', 0)} new, {r.get('enrolled', 0)} enrolled" for c, r in d["campaign_runs"][:MAX_LINES]]
    return title, "\n".join(lines)


def run_digest(store, now: datetime, sender=None) -> Optional[dict]:
    """Send today's summary if it is due. Returns the notification, or None when not due or already sent."""
    if not is_due(now, store.read("settings")):
        return None
    today = now.astimezone(IST).date().isoformat()
    claimed = {}

    def claim(settings: dict) -> dict:
        if settings.get("last_digest_date") == today:
            return settings
        claimed["since"] = _since(settings, now)
        return {**settings, "last_digest_date": today, "last_digest_at": iso(now)}
    store.update("settings", claim)
    if not claimed:
        return None
    title, text = digest_text(build_digest(store, now, claimed["since"]))
    return notify(store, caller_name(store), title, text, kind="digest", sender=sender)
