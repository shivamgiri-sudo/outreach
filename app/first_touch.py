"""Speed-to-lead: the first-touch clock on an owned lead (design 2026-11-10, flow 5).

The clock starts when a lead gets its owner and runs in working hours (default Mon-Sat 10:00-19:00 IST; a workspace
can override it with `first_touch.hours` in its assignment config). The owner's first email / WhatsApp / LinkedIn
send, logged call, note or booked meeting stops it (a stage change alone does not). `tick` then:
  - alert_minutes (default 30) of working time with no touch: one bell to the owner;
  - reassign_minutes (default 120): the lead moves to the next member of the owner's team (owners already tried are
    skipped), the old owner and the manager get a bell, the clock restarts; after 2 moves it is flagged
    `first_touch_stuck` and the manager is told once.
Never for Do-Not-Contact, closed stages, touched leads, unowned leads or workspaces where assignment is off.
Moving the owner never touches running sequences (linkedin_seq / sequence, LinkedIn ID stay as they are).
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_HOURS = {"days": [0, 1, 2, 3, 4, 5], "start": "10:00", "end": "19:00"}      # Monday=0
MAX_MOVES = 2
CLOSED_STAGES = ("unsubscribed", "disqualified", "not_now")
SEND_KINDS = ("email", "whatsapp", "linkedin")                      # automated sends carry no "by": they go out as the owner
TOUCH_KINDS = SEND_KINDS + ("call", "note", "meeting")
DEFAULTS = {"alert_minutes": 30, "reassign_minutes": 120, "alert": True, "reassign": True}


def _assignment():
    """The only place that knows the assignment module (built separately); tests swap in a fake."""
    from . import assignment
    return assignment


def _ws(lead: dict) -> str:
    return (lead or {}).get("workspace") or "main"


def _cfg(store, ws: str) -> dict:
    """{'on': bool, **first_touch settings}; assignment off or missing means the clock does nothing."""
    try:
        raw = _assignment().config(store, ws) or {}
    except Exception:
        return {"on": False, **DEFAULTS}
    return {"on": bool(raw.get("on")), **DEFAULTS, **(raw.get("first_touch") or {})}


def _parse(value) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _hm(text, fallback: str) -> int:
    try:
        h, m = str(text).split(":")
        return int(h) * 60 + int(m)
    except (TypeError, ValueError):
        h, m = fallback.split(":")
        return int(h) * 60 + int(m)


def working_minutes(start: datetime, end: datetime, hours: Optional[dict] = None) -> int:
    """Whole working minutes between two moments (IST days and opening hours; nights and days off count as 0)."""
    hours = {**DEFAULT_HOURS, **(hours or {})}
    days = set(hours.get("days") or [])
    open_m, close_m = _hm(hours.get("start"), "10:00"), _hm(hours.get("end"), "19:00")
    a, b = start.astimezone(IST), end.astimezone(IST)
    if b <= a or close_m <= open_m:
        return 0
    total, day = 0, a.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= b:
        if day.weekday() in days:
            lo = max(a, day + timedelta(minutes=open_m))
            hi = min(b, day + timedelta(minutes=close_m))
            if hi > lo:
                total += int((hi - lo).total_seconds() // 60)
        day += timedelta(days=1)
    return total


def add_working_minutes(start: datetime, minutes: int, hours: Optional[dict] = None) -> datetime:
    """The moment `minutes` of working time after `start`."""
    hours = {**DEFAULT_HOURS, **(hours or {})}
    days = set(hours.get("days") or [])
    open_m, close_m = _hm(hours.get("start"), "10:00"), _hm(hours.get("end"), "19:00")
    if not days or close_m <= open_m:
        return start + timedelta(minutes=minutes)
    cur, left = start.astimezone(IST), minutes
    for _ in range(800):
        day = cur.replace(hour=0, minute=0, second=0, microsecond=0)
        if day.weekday() in days:
            lo, hi = max(cur, day + timedelta(minutes=open_m)), day + timedelta(minutes=close_m)
            room = int((hi - lo).total_seconds() // 60)
            if room > 0:
                if left <= room:
                    return lo + timedelta(minutes=left)
                left -= room
        cur = day + timedelta(days=1)
    return cur


def _eligible(lead: dict) -> bool:
    return bool(lead.get("owner")) and not lead.get("do_not_contact") and lead.get("stage") not in CLOSED_STAGES


def _is_touch(event: dict, owner: str) -> bool:
    kind, by = event.get("kind"), (event.get("by") or "").strip()
    if kind not in TOUCH_KINDS:
        return False
    if not by:
        return kind in SEND_KINDS
    return by.lower() == (owner or "").lower()


def stamp(old: dict, new: dict) -> dict:
    """Pure helper for Store.update_lead: when `new` carries a fresh owner touch on a running clock, record it."""
    if not new.get("first_touch_started") or new.get("first_touch_at"):
        return new
    fresh = (new.get("timeline") or [])[len((old or {}).get("timeline") or []):]
    for e in fresh:
        if _is_touch(e, new.get("owner")):
            return {**new, "first_touch_at": e.get("at") or datetime.now(timezone.utc).isoformat()}
    return new


def start(store, lead_id: str, now: Optional[datetime] = None) -> Optional[dict]:
    """Start the clock (called when a lead gets its owner). A clock already running keeps running."""
    now = now or datetime.now(timezone.utc)
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None or not _eligible(lead) or lead.get("first_touch_started"):
        return None
    cfg = _cfg(store, _ws(lead))
    if not cfg["on"]:
        return None
    due = add_working_minutes(now, int(cfg["alert_minutes"]), cfg.get("hours"))
    return store.update_lead(lead_id, lambda l: None if l.get("first_touch_started") else {
        **l, "first_touch_started": now.isoformat(timespec="seconds"), "first_touch_due_at": due.isoformat(timespec="seconds"),
        "first_touch_at": None, "reassigns": 0, "first_touch_alerted": False, "first_touch_tried": [l.get("owner")],
        "first_touch_stuck": False})


def touched(store, lead_id: str, kind: str, by: str, now: Optional[datetime] = None) -> bool:
    """The owner touched the lead (email/WhatsApp/LinkedIn sent, call, note, meeting). True when it stopped the clock."""
    if kind not in TOUCH_KINDS:
        return False
    when = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    result = {}

    def change(l):
        if not l.get("first_touch_started") or l.get("first_touch_at") or not l.get("owner"):
            return None
        if (by or "").strip().lower() != l["owner"].strip().lower():
            return None
        result["ok"] = True
        return {**l, "first_touch_at": when}
    store.update_lead(lead_id, change)
    return bool(result.get("ok"))


def _bell(store, to: str, title: str, text: str, lead_id: str) -> None:
    if not to:
        return
    from .reminders import notify
    notify(store, to, title, text, lead_id=lead_id, kind="first_touch")


def _name(lead: dict) -> str:
    return lead.get("company") or lead.get("name") or lead["id"]


def _reassign(store, lead: dict, cfg: dict, now: datetime) -> None:
    from . import lead_ops
    api, ws, old = _assignment(), _ws(lead), lead["owner"]
    tried = list(lead.get("first_touch_tried") or [old])
    team_id = api.team_of_owner(store, ws, old)
    new = api.next_member(store, ws, team_id, exclude=tuple(tried)) if team_id else ""
    manager = api.manager_of(store, ws, old)
    if not new:
        store.update_lead(lead["id"], lambda l: {**l, "first_touch_stuck": True})
        _bell(store, manager, "Lead stuck without a first touch", f"{_name(lead)} (owner {old}) has had no first touch and nobody is left to give it to.", lead["id"])
        return
    lead_ops.set_owner(store, lead["id"], new, "system", "no first touch in time")
    due = add_working_minutes(now, int(cfg["alert_minutes"]), cfg.get("hours"))
    store.update_lead(lead["id"], lambda l: {**l, "reassigns": int(l.get("reassigns") or 0) + 1, "first_touch_tried": [*tried, new],
                                             "first_touch_started": now.isoformat(timespec="seconds"), "first_touch_due_at": due.isoformat(timespec="seconds"),
                                             "first_touch_alerted": False})
    text = f"{_name(lead)} had no first touch from {old}, so it moved to {new}."
    _bell(store, old, "Lead moved to someone else", text, lead["id"])
    _bell(store, manager, "Lead moved for no first touch", text, lead["id"])
    _bell(store, new, "New lead for you", f"{_name(lead)} was passed to you: {old} did not touch it in time. Please make first contact now.", lead["id"])


def _check(store, lead: dict, now: datetime) -> str:
    cfg = _cfg(store, _ws(lead))
    if not cfg["on"] or not _eligible(lead) or lead.get("first_touch_at") or lead.get("first_touch_stuck"):
        return ""
    started = _parse(lead.get("first_touch_started"))
    if started is None:
        return ""
    waited = working_minutes(started, now, cfg.get("hours"))
    if waited >= int(cfg["reassign_minutes"]) and cfg["reassign"]:
        if int(lead.get("reassigns") or 0) >= MAX_MOVES:
            store.update_lead(lead["id"], lambda l: {**l, "first_touch_stuck": True})
            manager = _assignment().manager_of(store, _ws(lead), lead["owner"])
            _bell(store, manager, "Lead stuck without a first touch", f"{_name(lead)} was moved {MAX_MOVES} times and still has no first touch (now with {lead['owner']}).", lead["id"])
            return "stuck"
        _reassign(store, lead, cfg, now)
        return "reassigned"
    if waited >= int(cfg["alert_minutes"]) and cfg["alert"] and not lead.get("first_touch_alerted"):
        store.update_lead(lead["id"], lambda l: {**l, "first_touch_alerted": True})
        _bell(store, lead["owner"], "First touch is due", f"{_name(lead)} was given to you {waited} working minutes ago and has had no first touch yet.", lead["id"])
        return "alerted"
    return ""


def tick(store, now: Optional[datetime] = None) -> dict:
    """Alert / reassign every running clock that is due. Safe to call every minute."""
    now = now or datetime.now(timezone.utc)
    out = {"alerted": 0, "reassigned": 0, "stuck": 0}
    for lead in store.read("leads"):
        if not lead.get("first_touch_started") or lead.get("first_touch_at"):
            continue
        # catch touches written by code that does not go through update_lead
        if any(_is_touch(e, lead.get("owner")) and (_parse(e.get("at")) or now) >= (_parse(lead["first_touch_started"]) or now)
               for e in lead.get("timeline") or []):
            store.update_lead(lead["id"], lambda l: {**l, "first_touch_at": l.get("first_touch_at") or now.isoformat(timespec="seconds")})
            continue
        res = _check(store, lead, now)
        if res:
            out[res] += 1
    return out
