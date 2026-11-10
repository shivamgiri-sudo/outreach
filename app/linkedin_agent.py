"""LinkedIn agent: the rules and the record for the program that runs on YOUR computer and drives a Chrome window you logged
in to yourself (tools/linkedin_agent.mjs).

The portal never sees your LinkedIn password. It decides what the agent may do and keeps the score:
  - on/off switch, working hours (India time), weekdays only, and a fixed gap between two actions;
  - caps: connection requests per day (the LinkedIn settings number) and per week, messages per day;
  - a pause that cannot be skipped: when the agent reports a fatal problem (LinkedIn checkpoint, CAPTCHA, weekly invitation
    limit, a login wall) it is switched off and the owner is notified. A person must switch it back on.
The agent asks `control()` before every action and reports every action to `record()`, so these limits hold even if the
program is restarted. LinkedIn restricts accounts that automate: this keeps the volume low and fixed, it does not hide anything.

Several LinkedIn accounts: each agent tells the portal which account it is logged in to (its profile slug, header
X-LinkedIn-Account). Caps, the gap, the heartbeat and the safety pause are then kept PER ACCOUNT (LinkedIn limits each
account separately), and a lead belongs to the account that first contacted it, so two accounts never invite the same
person and follow-up messages always come from the account they are connected to. An agent that sends no account works
as before (one shared account).

Client accounts (app/linkedin_scope.py): every LinkedIn account (slug) works for one client account ("mcn" unless an
agent of another account reported it first). Each client account has its own switch, hours, gap and caps (clamped to the
ceilings an owner set for it), its own log and last-seen state; the "mcn" account keeps the old global keys. The owner's
platform-wide kill switch stops every account's agent.
"""
import re
from datetime import datetime, timedelta
from typing import Optional

from . import linkedin_scope as scope, linkedin_seq as li
from .accounts import MCN
from .activities import iso, now_utc
from .reminders import notify
from .sequences import IST

SETTINGS_CFG, SETTINGS_LOG, SETTINGS_SEEN = "linkedin_agent", "linkedin_agent_log", "linkedin_agent_seen"
SETTINGS_ACCOUNTS, SETTINGS_PAUSED = "linkedin_agent_accounts", "linkedin_agent_paused"
_ACCOUNT = re.compile(r"[^a-z0-9_-]")


def norm_account(raw) -> str:
    """A LinkedIn profile slug (the part after /in/), lower-case; '' when the agent sent none."""
    return _ACCOUNT.sub("", str(raw or "").strip().lower())[:100]


def _mine(rows: list, account: str, owner: str = MCN) -> list:
    return [r for r in rows if (r.get("account") or "") == account and (r.get("account_id") or MCN) == (owner or MCN)]


SETTINGS_SEEN_BY = "linkedin_agent_seen_by_account"
MAX_LOG = 400
DEFAULTS = {"enabled": False, "weekly_connections": 80, "daily_messages": 25, "start_hour": 9, "end_hour": 18,
            "weekdays_only": True, "gap_minutes": 6, "check_replies": True,
            "connect_without_note": False}     # free accounts run out of connection notes each month
CLIENT_DEFAULTS = {"daily_connections": li.DEFAULT_DAILY_CONNECTIONS, "auto_start": False}   # mcn: the LinkedIn settings
BOOLS = ("enabled", "weekdays_only", "check_replies", "connect_without_note", "auto_start")
LIMITS = {"daily_connections": (1, 40), "weekly_connections": (1, 100), "daily_messages": (0, 100), "start_hour": (0, 23), "end_hour": (1, 24), "gap_minutes": (3, 60)}
ACTION_KINDS = ("connect", "message")
KINDS = ("connect", "message", "accepted", "replied", "error", "paused", "started", "auto_start")


def config(store, owner: str = MCN, clamped: bool = True) -> dict:
    """The agent settings of one client account (default "mcn": the old global settings), caps clamped to its ceilings."""
    owner = owner or MCN
    settings = store.read("settings")
    if owner == MCN:
        saved = settings.get(SETTINGS_CFG) or {}
        cfg = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS},
               "daily_connections": int(settings.get("linkedin_daily_connections") or li.DEFAULT_DAILY_CONNECTIONS),
               "auto_start": bool(settings.get("linkedin_auto_start"))}
    else:
        saved = scope.own_config(settings, owner)
        cfg = {**DEFAULTS, **CLIENT_DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS or k in CLIENT_DEFAULTS}}
    return scope.clamp(cfg, scope.ceilings(store, owner)) if clamped else cfg


def save_config(store, changes: dict, owner: str = MCN) -> dict:
    """Save one account's settings. Caps above the account's ceiling are lowered to it (never refused)."""
    owner = owner or MCN
    keys = set(DEFAULTS) | (set(CLIENT_DEFAULTS) if owner != MCN else set())
    clean = {}
    for key, value in changes.items():
        if key not in keys or value is None:
            continue
        if key in BOOLS:
            clean[key] = bool(value)
        else:
            low, high = LIMITS[key]
            number = int(value)
            if not low <= number <= high:
                raise ValueError(f"{key.replace('_', ' ')} must be between {low} and {high}.")
            clean[key] = number
    merged = scope.clamp({**config(store, owner, clamped=False), **clean}, scope.ceilings(store, owner))
    if merged["end_hour"] <= merged["start_hour"]:
        raise ValueError("The end hour must be after the start hour.")
    if owner == MCN:
        store.update("settings", lambda s: {**s, SETTINGS_CFG: {k: v for k, v in merged.items() if k in DEFAULTS}})
    else:
        scope.save_own_config(store, owner, {k: v for k, v in merged.items() if k in keys})
    return config(store, owner)


def log_rows(store, owner: Optional[str] = None) -> list:
    """The agent log; with `owner` only that client account's rows."""
    rows = store.read("settings").get(SETTINGS_LOG) or []
    return rows if owner is None else [r for r in rows if (r.get("account_id") or MCN) == owner]


def _count(rows: list, kind: str, since: datetime) -> int:
    return sum(1 for r in rows if r.get("kind") == kind and datetime.fromisoformat(r["at"]) >= since)


def _day_start(now: datetime) -> datetime:
    local = now.astimezone(IST)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def counts(store, now: datetime, account: str = "", owner: str = MCN) -> dict:
    rows = _mine(log_rows(store), account, owner)
    today, week = _day_start(now), _day_start(now) - timedelta(days=now.astimezone(IST).weekday())
    return {"connect_today": _count(rows, "connect", today), "message_today": _count(rows, "message", today),
            "connect_week": _count(rows, "connect", week)}


def _last_action_at(rows: list) -> Optional[datetime]:
    stamps = [datetime.fromisoformat(r["at"]) for r in rows if r.get("kind") in ACTION_KINDS]
    return max(stamps) if stamps else None


def paused(store) -> dict:
    return store.read("settings").get(SETTINGS_PAUSED) or {}


def owner_of_slug(store, account: str, fallback: str = MCN) -> str:
    """The client account a LinkedIn account works for (an unknown one: the asking agent's account)."""
    return (scope.slug_account(store.read("settings"), account) if account else None) or fallback or MCN


def control(store, now: Optional[datetime] = None, account: str = "", owner: Optional[str] = None) -> dict:
    """What the agent may do right now. `may_connect` / `may_message` are false outside hours, over a cap, or within the gap.
    `owner`: the client account of the asking agent (its token); a known LinkedIn account uses its own account's rules."""
    now = now or now_utc()
    account = norm_account(account)
    owner = owner_of_slug(store, account, owner or MCN)
    cfg, rows = config(store, owner), _mine(log_rows(store), account, owner)
    local = now.astimezone(IST)
    in_hours = cfg["start_hour"] <= local.hour < cfg["end_hour"] and (not cfg["weekdays_only"] or local.weekday() < 5)
    c = counts(store, now, account, owner)
    left_connect = max(0, min(cfg["daily_connections"] - c["connect_today"], cfg["weekly_connections"] - c["connect_week"]))
    left_message = max(0, cfg["daily_messages"] - c["message_today"])
    last = _last_action_at(rows)
    wait = max(0, int(cfg["gap_minutes"] * 60 - (now - last).total_seconds())) if last else 0
    hold = paused(store).get(account) if account else None
    killed = scope.kill_state(store.read("settings"))
    why = (scope.KILL_WHY if killed else "the switch is off" if not cfg["enabled"] else f"this LinkedIn account is paused: {hold['note']}" if hold
           else "outside working hours" if not in_hours else "")
    active = cfg["enabled"] and in_hours and not hold and not killed
    return {"enabled": cfg["enabled"], "active_now": active, "why_not": why, "wait_seconds": wait,
            "may_connect": active and left_connect > 0 and wait == 0, "may_message": active and left_message > 0 and wait == 0,
            "connections_left_today": left_connect, "messages_left_today": left_message, "check_replies": cfg["check_replies"], "allow_no_note": cfg["connect_without_note"],
            "gap_minutes": cfg["gap_minutes"], "counts": c, "account": account, "killed": bool(killed)}


def _stamp(owner: str) -> dict:
    return {"account_id": owner} if owner and owner != MCN else {}


def note(store, kind: str, text: str, account: str = "", lead_id: str = "", now: Optional[datetime] = None, owner: str = MCN) -> None:
    """A line in "What it did lately" written by the portal itself (e.g. the daily auto-start), without touching the
    agent's last-seen state."""
    owner = owner_of_slug(store, norm_account(account), owner)
    row = {"at": iso(now or now_utc()), "kind": kind, "lead_id": lead_id[:40], "note": text[:300], **({"account": norm_account(account)} if account else {}),
           **_stamp(owner)}
    store.update("settings", lambda s: {**s, SETTINGS_LOG: [*(s.get(SETTINGS_LOG) or [])[-(MAX_LOG - 1):], row]})


def _seen_update(s: dict, owner: str, seen: dict) -> dict:
    if owner == MCN:
        return {SETTINGS_SEEN: seen}
    return {SETTINGS_SEEN_BY: {**(s.get(SETTINGS_SEEN_BY) or {}), owner: seen}}


def _slug_update(s: dict, account: str, owner: str, change: dict) -> dict:
    accounts_seen = dict(s.get(SETTINGS_ACCOUNTS) or {})
    old = accounts_seen.get(account) or {}
    accounts_seen[account] = {**old, **change, **({"account_id": owner} if not old.get("account_id") and owner != MCN else {})}
    return {SETTINGS_ACCOUNTS: accounts_seen}


def record(store, event: dict, now: Optional[datetime] = None, notify_sender=None, owner: str = MCN) -> dict:
    """Keep what the agent did. A fatal error switches the agent off (or pauses its LinkedIn account) and tells the owner,
    the account's admins and the super admins."""
    now = now or now_utc()
    kind = str(event.get("kind") or "")
    if kind not in KINDS:
        raise ValueError("Unknown event kind.")
    account = norm_account(event.get("account"))
    owner = owner_of_slug(store, account, owner)
    row = {"at": iso(now), "kind": kind, "lead_id": str(event.get("lead_id") or "")[:40], "note": str(event.get("note") or "")[:300],
           **({"account": account} if account else {}), **_stamp(owner),
           **({"verified": bool(event["verified"])} if event.get("verified") is not None and kind in ("connect", "message") else {})}
    seen = {"at": iso(now), "state": str(event.get("state") or kind)[:40]}
    store.update("settings", lambda s: {**s, SETTINGS_LOG: [*(s.get(SETTINGS_LOG) or [])[-(MAX_LOG - 1):], row], **_seen_update(s, owner, seen),
                                        **(_slug_update(s, account, owner, seen) if account else {})})
    if kind == "error" and event.get("fatal") and account:
        store.update("settings", lambda s: {**s, SETTINGS_PAUSED: {**(s.get(SETTINGS_PAUSED) or {}), account: {"at": iso(now), "note": row["note"] or "checkpoint"}}})
        if owner == MCN:
            who = store.read("settings").get("linkedin_owner") or "Sales team"
            notify(store, who, f"LinkedIn account {account} paused", f"The LinkedIn agent for {account} stopped itself: {row['note'] or 'LinkedIn showed a checkpoint'}. "
                   "Other LinkedIn accounts keep going. Open LinkedIn as that account, sort it out, then press Resume for it on the LinkedIn page.",
                   kind="linkedin", sender=notify_sender)
        scope.alert_paused(store, owner, account, row["note"])
        return row
    if kind == "error" and event.get("fatal"):
        save_config(store, {"enabled": False}, owner)
        if owner == MCN:
            who = store.read("settings").get("linkedin_owner") or "Sales team"
            notify(store, who, "LinkedIn agent paused", f"The LinkedIn agent stopped itself: {row['note'] or 'LinkedIn showed a checkpoint'}. "
                   "Open LinkedIn yourself, sort it out, then switch the agent back on.", kind="linkedin", sender=notify_sender)
        scope.alert_paused(store, owner, "", row["note"])
    return row


def heartbeat(store, state: str = "idle", now: Optional[datetime] = None, account: str = "", name: str = "", owner: str = MCN) -> None:
    """The agent is alive. A LinkedIn account seen for the first time is stamped with the asking agent's client account
    (callers check linkedin_scope.check_slug first, so another account's LinkedIn account is never taken over)."""
    now = now or now_utc()
    account = norm_account(account)
    owner = owner_of_slug(store, account, owner)
    seen = {"at": iso(now), "state": str(state)[:40]}

    def apply(s):
        out = {**s, **_seen_update(s, owner, seen)}
        if account:
            out.update(_slug_update(s, account, owner, {**seen, **({"name": name[:80]} if name else {})}))
        return out
    store.update("settings", apply)


def resume(store, account: str) -> None:
    account = norm_account(account)
    store.update("settings", lambda s: {**s, SETTINGS_PAUSED: {k: v for k, v in (s.get(SETTINGS_PAUSED) or {}).items() if k != account}})


def merge(store, old: str, new: str, owner: Optional[str] = None) -> int:
    """The same LinkedIn account under a new profile link (renamed): its people, counts and pause move to the new name.
    With `owner`, both LinkedIn accounts must work for that client account."""
    old, new = norm_account(old), norm_account(new)
    if not old or not new or old == new:
        raise ValueError("Pick two different accounts.")
    known = store.read("settings").get(SETTINGS_ACCOUNTS) or {}
    if old not in known:
        raise ValueError(f"No LinkedIn account {old}.")
    old_owner = scope.account_of_row(known.get(old))
    if owner is not None and old_owner != owner:
        raise ValueError(f"No LinkedIn account {old}.")
    if new in known and scope.account_of_row(known.get(new)) != old_owner:
        raise ValueError("Those two LinkedIn accounts work for different client accounts.")

    def fix_settings(s):
        accts = dict(s.get(SETTINGS_ACCOUNTS) or {})
        merged = {**accts.pop(old, {}), **accts.get(new, {})}
        accts[new] = merged
        held = dict(s.get(SETTINGS_PAUSED) or {})
        if old in held:
            held.setdefault(new, held.pop(old))
        rows = [{**r, "account": new} if r.get("account") == old else r for r in s.get(SETTINGS_LOG) or []]
        return {**s, SETTINGS_ACCOUNTS: accts, SETTINGS_PAUSED: held, SETTINGS_LOG: rows}
    store.update("settings", fix_settings)
    moved = 0
    for l in store.read("leads"):
        if (l.get("linkedin_seq") or {}).get("account") == old:
            store.update_lead(l["id"], lambda x: {**x, "linkedin_seq": {**x["linkedin_seq"], "account": new}})
            moved += 1
    return moved


def accounts(store, now: datetime, owner: Optional[str] = None) -> list:
    """The LinkedIn accounts seen (with `owner`: only those working for that client account), each with its account."""
    held = paused(store)
    out = []
    for acct, seen in sorted((store.read("settings").get(SETTINGS_ACCOUNTS) or {}).items()):
        acct_owner = scope.account_of_row(seen)
        if owner is not None and acct_owner != owner:
            continue
        online = bool(seen.get("at")) and (now - datetime.fromisoformat(seen["at"])).total_seconds() < 180
        out.append({"account": acct, "name": seen.get("name", ""), "last_seen": seen.get("at"), "online": online, "state": seen.get("state", ""),
                    "paused": held.get(acct), "counts": counts(store, now, acct, acct_owner), "account_id": acct_owner,
                    "leads": sum(1 for l in store.read("leads") if (l.get("linkedin_seq") or {}).get("account") == acct)})
    return out


def resume_allowed(store, account: str, owner: Optional[str]) -> bool:
    return owner is None or owner_of_slug(store, norm_account(account), MCN) == owner


def status(store, now: Optional[datetime] = None, owner: Optional[str] = MCN) -> dict:
    """The agent box for one client account (owner None = the owners' overview: mcn's settings, every account's rows)."""
    now = now or now_utc()
    settings = store.read("settings")
    acct = owner or MCN
    seen = (settings.get(SETTINGS_SEEN) or {}) if acct == MCN else ((settings.get(SETTINGS_SEEN_BY) or {}).get(acct) or {})
    online = False
    if seen.get("at"):
        online = (now - datetime.fromisoformat(seen["at"])).total_seconds() < 180
    return {"config": config(store, acct), "control": control(store, now, owner=acct), "online": online, "last_seen": seen.get("at"),
            "state": seen.get("state", ""), "log": list(reversed(log_rows(store, owner)[-40:])), "accounts": accounts(store, now, owner),
            "account_id": owner, "ceilings": scope.ceilings(store, acct), "kill": scope.kill_state(settings) or None,
            "today": _today(store, now, owner)}


def _today(store, now: datetime, owner: Optional[str]) -> dict:
    from . import linkedin_watch
    return linkedin_watch.summary(store, now, owner) if owner is not None else {}
