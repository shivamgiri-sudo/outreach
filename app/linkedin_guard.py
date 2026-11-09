"""Server-side guard so no LinkedIn agent (whatever version runs on the office computer) can loop on one person.

- Attempts: every claim (the agent's "I am about to open this person") is counted on the lead. After MAX_TRIES claims
  in WINDOW with no step sent / accepted in between, the person is held until the oldest try is WINDOW old: claim
  answers 409 (old and new agents skip on 409, without opening the profile) and the agent queue leaves them out.
  A step sent, an acceptance or a reply clears the count. One line in "What it did lately" when a person is held.
- Acceptance checks: the queue offers a person "waiting for acceptance" at most once per CHECK_EVERY (stamped when
  offered), so restarts of the agent do not reopen the same profiles.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

MAX_TRIES = 2
WINDOW = timedelta(hours=24)
CHECK_EVERY = timedelta(hours=6)
KEY, CHECKED = "linkedin_attempts", "linkedin_checked_at"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _recent(lead: dict, now: datetime) -> list:
    out = []
    for at in lead.get(KEY) or []:
        try:
            t = datetime.fromisoformat(at)
        except (TypeError, ValueError):
            continue
        if now - t < WINDOW:
            out.append(t)
    return sorted(out)


def held_until(lead: dict, now: Optional[datetime] = None) -> Optional[datetime]:
    now = now or _now()
    tries = _recent(lead, now)
    return tries[0] + WINDOW if len(tries) >= MAX_TRIES else None


def refusal(store, lead_id: str, account: str = "", now: Optional[datetime] = None) -> Optional[str]:
    """A refusal sentence when the person is held (logged once per hold), else None. Counts nothing."""
    from . import linkedin_agent as agent_mod
    now = now or _now()
    out = {}

    def apply(lead):
        until = held_until(lead, now)
        if not until:
            return None
        out["until"], out["name"] = until, lead.get("contact_name") or lead.get("company") or lead_id
        if lead.get("linkedin_held_logged") == until.isoformat(timespec="minutes"):
            return None
        out["log"] = True
        return {**lead, "linkedin_held_logged": until.isoformat(timespec="minutes")}
    store.update_lead(lead_id, apply)
    if not out:
        return None
    ist = out["until"].astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%d %b %I:%M %p IST")
    why = f"tried {MAX_TRIES} times in the last 24 hours without success; held until {ist} so the agent does not loop on this person"
    if out.get("log"):
        agent_mod.note(store, "error", f"{out['name']}: {why}", account, lead_id)
    return why


def count_try(store, lead_id: str, now: Optional[datetime] = None) -> None:
    """One more attempt (the claim went ahead: the agent is about to open the profile)."""
    now = now or _now()
    store.update_lead(lead_id, lambda l: {**l, KEY: [t.isoformat(timespec="seconds") for t in _recent(l, now)] + [now.isoformat(timespec="seconds")]})


def clear(store, lead_id: str) -> None:
    store.update_lead(lead_id, lambda l: {**l, KEY: [], "linkedin_held_logged": None} if l.get(KEY) else None)


def check_due(lead: dict, now: Optional[datetime] = None) -> bool:
    now = now or _now()
    try:
        return now - datetime.fromisoformat(lead.get(CHECKED) or "") >= CHECK_EVERY
    except (TypeError, ValueError):
        return True


def stamp_checked(store, lead_ids: list, now: Optional[datetime] = None) -> None:
    stamp = (now or _now()).isoformat(timespec="seconds")
    for lid in lead_ids:
        store.update_lead(lid, lambda l: {**l, CHECKED: stamp})
