"""Automatic call retry rules (settings["call_retry"], OFF by default).

When on, a call logged as no answer / busy / call disconnected books the next try as a call task, at a delay that depends
on how many unanswered tries the lead already has, inside calling hours (Mon-Sat 09:00-19:00 IST). After max_attempts the
lead leaves the call queue (lead["retry_exhausted"]). A reached outcome closes the open retry task so it does not ring again.
"""
import re
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from .activities import add_task, iso, update_task
from .sequences import IST

UNANSWERED = ("no_answer", "busy", "call_disconnected")
REACHED_RESET = ("connected", "interested", "not_interested", "meeting_booked", "call_back")    # a real conversation: a new cycle
MARKERS = ("invalid_lead", "consultant_lead")
DEFAULT_RULES = {"no_answer": [180, 1440], "busy": [60, 1440], "call_disconnected": [15, 1440]}
MIN_DELAY, MAX_DELAY, MAX_STEPS = 5, 10080, 6
MIN_ATTEMPTS, MAX_ATTEMPTS, DEFAULT_ATTEMPTS = 1, 20, 6
HOURS_FROM, HOURS_TO, RESUME_AT = 9, 19, time(10, 0)
TITLE = "Retry call (attempt"
TITLE_RE = re.compile(r"^Retry call \(attempt (\d+) of (\d+)\)")
NAMES = {"no_answer": "No answer", "busy": "Busy", "call_disconnected": "Call disconnected"}


def defaults() -> dict:
    return {"enabled": False, "max_attempts": DEFAULT_ATTEMPTS, "rules": {k: list(v) for k, v in DEFAULT_RULES.items()}}


def clean(raw: dict) -> dict:
    """Validated settings from a request body; ValueError carries a plain-words reason."""
    if not isinstance(raw, dict):
        raise ValueError("Send the retry settings as an object.")
    out = defaults()
    if "enabled" in raw:
        if not isinstance(raw["enabled"], bool):
            raise ValueError("Turn the retry rules on or off with true or false.")
        out["enabled"] = raw["enabled"]
    if "max_attempts" in raw:
        n = raw["max_attempts"]
        if isinstance(n, bool) or not isinstance(n, int) or not MIN_ATTEMPTS <= n <= MAX_ATTEMPTS:
            raise ValueError(f"The most tries per lead must be a whole number from {MIN_ATTEMPTS} to {MAX_ATTEMPTS}.")
        out["max_attempts"] = n
    rules = raw.get("rules")
    if rules is not None:
        if not isinstance(rules, dict):
            raise ValueError("Rules must list the waits for each outcome.")
        for key in rules:
            if key not in DEFAULT_RULES:
                raise ValueError(f"'{key}' is not an outcome that retries. Use: {', '.join(NAMES.values())}.")
        for key, delays in rules.items():
            label = NAMES[key]
            if not isinstance(delays, list) or not delays:
                raise ValueError(f"{label}: give at least one wait, in minutes.")
            if len(delays) > MAX_STEPS:
                raise ValueError(f"{label}: at most {MAX_STEPS} waits (the last one repeats).")
            for d in delays:
                if isinstance(d, bool) or not isinstance(d, int) or not MIN_DELAY <= d <= MAX_DELAY:
                    raise ValueError(f"{label}: each wait must be a whole number of minutes from {MIN_DELAY} to {MAX_DELAY} (one week).")
            out["rules"][key] = list(delays)
    return out


def get_settings(store) -> dict:
    saved = (store.read("settings") or {}).get("call_retry")
    if not isinstance(saved, dict):
        return defaults()
    try:
        return clean(saved)
    except ValueError:
        return defaults()


def in_calling_hours(when: datetime) -> bool:
    local = when.astimezone(IST)
    return local.weekday() < 6 and HOURS_FROM <= local.hour < HOURS_TO


def shift_into_hours(when: datetime) -> datetime:
    """`when` itself when it is Mon-Sat 09:00-19:00 IST, else the next 10:00 IST on a calling day."""
    if in_calling_hours(when):
        return when
    local = when.astimezone(IST)
    day = local.date()
    if local.hour >= HOURS_TO or local.weekday() == 6:
        day += timedelta(days=1)
    while day.weekday() == 6:
        day += timedelta(days=1)
    return datetime.combine(day, RESUME_AT, tzinfo=IST).astimezone(timezone.utc)


def retry_due(rules: list, attempt_no: int, now: datetime) -> datetime:
    """Due time after the `attempt_no`th unanswered try (1-based); the last wait repeats."""
    delay = rules[min(attempt_no, len(rules)) - 1]
    return shift_into_hours(now + timedelta(minutes=delay))


def is_retry_task(task: dict) -> bool:
    return task.get("kind") == "call" and str(task.get("title") or "").startswith(TITLE)


def retry_label(task: dict) -> str:
    m = TITLE_RE.match(str(task.get("title") or ""))
    return f"Retry (attempt {m.group(1)} of {m.group(2)})" if m else "Retry"


def _when(raw) -> datetime:
    try:
        w = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return w if w.tzinfo else w.replace(tzinfo=timezone.utc)


def unanswered_streak(store, lead_id: str) -> int:
    """Unanswered tries since the last real conversation (marker verdicts are not tries)."""
    rows = sorted((a for a in store.read("activities") if a.get("lead_id") == lead_id and a.get("type") == "call"
                   and a.get("outcome") not in MARKERS), key=lambda a: _when(a.get("at")))
    count = 0
    for a in rows:
        if a.get("outcome") in REACHED_RESET:
            count = 0
        elif a.get("outcome") in UNANSWERED:
            count += 1
    return count


def close_retry_tasks(store, lead_id: str, note: str) -> int:
    ids = [t["id"] for t in store.read("tasks") if t.get("lead_id") == lead_id and is_retry_task(t) and t.get("status", "open") == "open"
           and not t.get("done") and t.get("id")]
    for tid in ids:
        from .activities import complete_task
        complete_task(store, tid, note)
    return len(ids)


def has_open_callback(store, lead_id: str) -> bool:
    return any(t.get("lead_id") == lead_id and t.get("kind") == "call" and t.get("source") == "callback" and not is_retry_task(t)
               and t.get("status", "open") == "open" and not t.get("done") for t in store.read("tasks"))


def after_outcome(store, lead: dict, outcome: str, who: str, now: datetime, explicit_callback: bool, result: dict) -> None:
    """Called by rep_workflow.record_call_outcome after the call is logged."""
    lead_id = lead["id"]
    if outcome in MARKERS:
        close_retry_tasks(store, lead_id, f"Closed: lead marked {outcome.replace('_', ' ')}.")
        return
    if outcome not in UNANSWERED:
        closed = close_retry_tasks(store, lead_id, f"Closed: {outcome.replace('_', ' ')} call logged by {who}.")
        if (closed or lead.get("retry_exhausted")) and outcome in REACHED_RESET:
            store.update_lead(lead_id, lambda l: {k: v for k, v in l.items() if k != "retry_exhausted"})
        return
    cfg = get_settings(store)
    if not cfg["enabled"]:
        return
    close_retry_tasks(store, lead_id, "Done: tried again.")
    if explicit_callback or has_open_callback(store, lead_id):
        return
    n = unanswered_streak(store, lead_id)
    cap = cfg["max_attempts"]
    if n >= cap:
        stamp = iso(now)
        store.update_lead(lead_id, lambda l: {**l, "retry_exhausted": {"at": stamp, "attempts": n},
                                              "timeline": [*l.get("timeline", []), {"at": stamp, "kind": "retry", "by": "Auto retry",
                                                                                     "text": f"Retry limit reached after {n} attempts"}]})
        return
    due = retry_due(cfg["rules"][outcome], n, now)
    result["task"] = add_task(store, lead_id, "call", f"{NAMES[outcome]} (try {n}). Automatic retry.", due_at=due, owner=lead.get("owner") or who,
                              priority="high", title=f"{TITLE} {n + 1} of {cap})", source="callback")
    result["retry_due_at"] = iso(due)
