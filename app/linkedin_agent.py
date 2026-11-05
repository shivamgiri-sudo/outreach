"""LinkedIn agent: the rules and the record for the program that runs on YOUR computer and drives a Chrome window you logged
in to yourself (tools/linkedin_agent.mjs).

The portal never sees your LinkedIn password. It decides what the agent may do and keeps the score:
  - on/off switch, working hours (India time), weekdays only, and a fixed gap between two actions;
  - caps: connection requests per day (the LinkedIn settings number) and per week, messages per day;
  - a pause that cannot be skipped: when the agent reports a fatal problem (LinkedIn checkpoint, CAPTCHA, weekly invitation
    limit, a login wall) it is switched off and the owner is notified. A person must switch it back on.
The agent asks `control()` before every action and reports every action to `record()`, so these limits hold even if the
program is restarted. LinkedIn restricts accounts that automate: this keeps the volume low and fixed, it does not hide anything.
"""
from datetime import datetime, timedelta
from typing import Optional

from . import linkedin_seq as li
from .activities import iso, now_utc
from .reminders import notify
from .sequences import IST

SETTINGS_CFG, SETTINGS_LOG, SETTINGS_SEEN = "linkedin_agent", "linkedin_agent_log", "linkedin_agent_seen"
MAX_LOG = 400
DEFAULTS = {"enabled": False, "weekly_connections": 80, "daily_messages": 25, "start_hour": 9, "end_hour": 18,
            "weekdays_only": True, "gap_minutes": 6, "check_replies": False}
LIMITS = {"weekly_connections": (1, 100), "daily_messages": (0, 100), "start_hour": (0, 23), "end_hour": (1, 24), "gap_minutes": (3, 60)}
ACTION_KINDS = ("connect", "message")
KINDS = ("connect", "message", "accepted", "replied", "error", "paused", "started")


def config(store) -> dict:
    saved = store.read("settings").get(SETTINGS_CFG) or {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save_config(store, changes: dict) -> dict:
    clean = {}
    for key, value in changes.items():
        if key not in DEFAULTS or value is None:
            continue
        if key in ("enabled", "weekdays_only", "check_replies"):
            clean[key] = bool(value)
        else:
            low, high = LIMITS[key]
            number = int(value)
            if not low <= number <= high:
                raise ValueError(f"{key.replace('_', ' ')} must be between {low} and {high}.")
            clean[key] = number
    merged = {**config(store), **clean}
    if merged["end_hour"] <= merged["start_hour"]:
        raise ValueError("The end hour must be after the start hour.")
    store.update("settings", lambda s: {**s, SETTINGS_CFG: merged})
    return merged


def log_rows(store) -> list:
    return store.read("settings").get(SETTINGS_LOG) or []


def _count(rows: list, kind: str, since: datetime) -> int:
    return sum(1 for r in rows if r.get("kind") == kind and datetime.fromisoformat(r["at"]) >= since)


def _day_start(now: datetime) -> datetime:
    local = now.astimezone(IST)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def counts(store, now: datetime) -> dict:
    rows = log_rows(store)
    today, week = _day_start(now), _day_start(now) - timedelta(days=now.astimezone(IST).weekday())
    return {"connect_today": _count(rows, "connect", today), "message_today": _count(rows, "message", today),
            "connect_week": _count(rows, "connect", week)}


def _last_action_at(rows: list) -> Optional[datetime]:
    stamps = [datetime.fromisoformat(r["at"]) for r in rows if r.get("kind") in ACTION_KINDS]
    return max(stamps) if stamps else None


def control(store, now: Optional[datetime] = None) -> dict:
    """What the agent may do right now. `may_connect` / `may_message` are false outside hours, over a cap, or within the gap."""
    now = now or now_utc()
    cfg, rows = config(store), log_rows(store)
    local = now.astimezone(IST)
    in_hours = cfg["start_hour"] <= local.hour < cfg["end_hour"] and (not cfg["weekdays_only"] or local.weekday() < 5)
    c = counts(store, now)
    daily_connect = int(store.read("settings").get("linkedin_daily_connections") or li.DEFAULT_DAILY_CONNECTIONS)
    left_connect = max(0, min(daily_connect - c["connect_today"], cfg["weekly_connections"] - c["connect_week"]))
    left_message = max(0, cfg["daily_messages"] - c["message_today"])
    last = _last_action_at(rows)
    wait = max(0, int(cfg["gap_minutes"] * 60 - (now - last).total_seconds())) if last else 0
    why = ("the switch is off" if not cfg["enabled"] else "outside working hours" if not in_hours else "")
    active = cfg["enabled"] and in_hours
    return {"enabled": cfg["enabled"], "active_now": active, "why_not": why, "wait_seconds": wait,
            "may_connect": active and left_connect > 0 and wait == 0, "may_message": active and left_message > 0 and wait == 0,
            "connections_left_today": left_connect, "messages_left_today": left_message, "check_replies": cfg["check_replies"],
            "gap_minutes": cfg["gap_minutes"], "counts": c}


def record(store, event: dict, now: Optional[datetime] = None, notify_sender=None) -> dict:
    """Keep what the agent did. A fatal error switches the agent off and tells the owner."""
    now = now or now_utc()
    kind = str(event.get("kind") or "")
    if kind not in KINDS:
        raise ValueError("Unknown event kind.")
    row = {"at": iso(now), "kind": kind, "lead_id": str(event.get("lead_id") or "")[:40], "note": str(event.get("note") or "")[:300]}
    store.update("settings", lambda s: {**s, SETTINGS_LOG: [*(s.get(SETTINGS_LOG) or [])[-(MAX_LOG - 1):], row],
                                        SETTINGS_SEEN: {"at": iso(now), "state": str(event.get("state") or kind)[:40]}})
    if kind == "error" and event.get("fatal"):
        save_config(store, {"enabled": False})
        owner = store.read("settings").get("linkedin_owner") or "Sales team"
        notify(store, owner, "LinkedIn agent paused", f"The LinkedIn agent stopped itself: {row['note'] or 'LinkedIn showed a checkpoint'}. "
               "Open LinkedIn yourself, sort it out, then switch the agent back on.", kind="linkedin", sender=notify_sender)
    return row


def heartbeat(store, state: str = "idle", now: Optional[datetime] = None) -> None:
    now = now or now_utc()
    store.update("settings", lambda s: {**s, SETTINGS_SEEN: {"at": iso(now), "state": str(state)[:40]}})


def status(store, now: Optional[datetime] = None) -> dict:
    now = now or now_utc()
    seen = store.read("settings").get(SETTINGS_SEEN) or {}
    online = False
    if seen.get("at"):
        online = (now - datetime.fromisoformat(seen["at"])).total_seconds() < 180
    return {"config": config(store), "control": control(store, now), "online": online, "last_seen": seen.get("at"), "state": seen.get("state", ""),
            "log": list(reversed(log_rows(store)[-40:]))}
