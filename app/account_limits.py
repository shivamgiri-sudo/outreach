"""Account limits, enforced in one place.

An account row carries limits (app/accounts.py LIMIT_KEYS). This module counts usage against them and refuses work that
would pass one, with a plain message: "Your account reached its <limit> of N; ask the platform admin to raise it."

  kind              counted as                              period
  imports_per_day   leads in an import (paste, file, sheet, connector capture, prospect import)   day (IST)
  daily_sends       emails + WhatsApp messages (sequences, flows, one-off)                         day (IST)
  enrich_credits    paid lookups (Apollo / Hunter / Firecrawl / waterfall / prospect enrichment)   month
  ai_credits        one per AI writer call (reply drafts, openers, flow Ask Claude, scoring)       month
  seats, workspaces counted from the logins / workspaces the account holds (not a usage counter)

Mas Callnet ("mcn") and any limit left blank are never blocked. Usage counters live in settings["account_usage"][id]
({day, month, d: {kind: n}, m: {kind: n}}) and are kept in memory per store; they are written to the
settings document only on the first use of a period, when 80% / 100% is crossed, and at most every FLUSH_SECONDS /
FLUSH_EVERY counts, so a request never costs a store write for every count. A restart loses at most FLUSH_EVERY counts.

At 80% and 100% of a limit, once per period, the account's admins and the super admins get a bell entry and an email;
both are recorded in the change log (app/audit.py, stamped with the account) and shown in the console's Alerts strip.
"""
import threading
import time
import weakref
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException

from . import accounts

IST = timezone(timedelta(hours=5, minutes=30))
KEY = "account_usage"
WARN_AT, FULL_AT = 0.8, 1.0
FLUSH_SECONDS, FLUSH_EVERY = 30, 20
# kind -> (period, words used in the message, label shown on the page)
KINDS = {
    "imports_per_day": ("day", "daily imports limit", "Leads imported today"),
    "daily_sends": ("day", "daily sending limit", "Emails and WhatsApp sent today"),
    "enrich_credits": ("month", "monthly lookup credits limit", "Lookup credits this month"),
    "ai_credits": ("month", "monthly AI credits limit", "AI credits this month"),
}
STATIC = {"seats": ("logins limit", "Logins"), "workspaces": ("workspaces limit", "Workspaces")}
ORDER = ("seats", "workspaces", "daily_sends", "imports_per_day", "enrich_credits", "ai_credits")


class LimitReached(HTTPException, ValueError):
    """A limit stops the work. Is an HTTP error (so a route answers it as is) and a ValueError (so code that already
    treats ValueError as "could not do it" falls back or reports it)."""

    def __init__(self, message: str, kind: str = "", limit: Optional[int] = None, status: int = 429):
        HTTPException.__init__(self, status, message)
        self.kind, self.limit, self.message = kind, limit, message

    def __str__(self) -> str:
        return self.message


def message(kind: str, limit: int) -> str:
    words = KINDS[kind][1] if kind in KINDS else STATIC[kind][0] if kind in STATIC else kind.replace("_", " ")
    return f"Your account reached its {words} of {limit}; ask the platform admin to raise it."


# ---------------------------------------------------------------- the counters
_LOCK = threading.RLock()
_CACHE = weakref.WeakKeyDictionary()       # store -> {"data": {...}, "dirty": n, "flushed": monotonic seconds}


def _periods(now: Optional[datetime] = None) -> tuple:
    local = (now or datetime.now(timezone.utc)).astimezone(IST)
    return local.date().isoformat(), local.strftime("%Y-%m")


def _real(store):
    return getattr(store, "_store", store)           # an AccountView wraps the real store


def _state(store) -> dict:
    store = _real(store)
    box = _CACHE.get(store)
    if box is None:
        box = {"data": dict((store.read("settings") or {}).get(KEY) or {}), "dirty": 0, "flushed": time.monotonic()}
        _CACHE[store] = box
    return box


def _row(box: dict, account_id: str, now: Optional[datetime] = None) -> dict:
    day, month = _periods(now)
    row = box["data"].setdefault(account_id, {})
    if row.get("day") != day:
        row["day"], row["d"] = day, {}
    if row.get("month") != month:
        row["month"], row["m"] = month, {}
    row.setdefault("d", {})
    row.setdefault("m", {})
    return row


def flush(store) -> None:
    """Write the counters to the settings document now."""
    store = _real(store)
    with _LOCK:
        box = _CACHE.get(store)
        if box is None or not box["dirty"]:
            return
        snapshot = {a: {**r, "d": dict(r.get("d", {})), "m": dict(r.get("m", {}))} for a, r in box["data"].items()}
        box["dirty"], box["flushed"] = 0, time.monotonic()
    store.update("settings", lambda s: {**s, KEY: snapshot})


def used(store, account_id: str, kind: str, now: Optional[datetime] = None) -> int:
    if kind in STATIC:
        return static_count(store, account_id, kind)
    period = KINDS[kind][0]
    with _LOCK:
        row = _row(_state(store), account_id, now)
        return int(row["d" if period == "day" else "m"].get(kind, 0))


def static_count(store, account_id: str, kind: str) -> int:
    if kind == "seats":
        from . import auth
        return sum(1 for u in auth.users(_real(store)) if accounts.of_user(u) == account_id)
    return len(accounts.workspaces_of(_real(store), account_id, active_only=False))


def limit_for(store, account_id: str, kind: str) -> Optional[int]:
    """None = no limit: Mas Callnet, an unknown account, or a blank limit."""
    if not account_id or account_id == accounts.MCN:
        return None
    return accounts.limit_of(_real(store), account_id, kind)


def _add(store, account_id: str, kind: str, amount: int, now: Optional[datetime]) -> int:
    period = KINDS[kind][0]
    with _LOCK:
        box = _state(store)
        row = _row(box, account_id, now)
        bucket = row["d" if period == "day" else "m"]
        first = kind not in bucket
        bucket[kind] = int(bucket.get(kind, 0)) + int(amount)
        box["dirty"] += 1
        total = bucket[kind]
        limit = limit_for(store, account_id, kind)
        crossed = limit is not None and limit > 0 and any(
            (total - amount) < limit * edge <= total for edge in (WARN_AT, FULL_AT))
        due = first or crossed or box["dirty"] >= FLUSH_EVERY or time.monotonic() - box["flushed"] >= FLUSH_SECONDS
    if due:
        flush(store)
    return total


def record(store, account_id: str, kind: str, amount: int = 1, now: Optional[datetime] = None) -> int:
    """Count usage that already happened (no refusal). Raises the 80% / 100% alerts. Mas Callnet is not counted."""
    if not account_id or account_id == accounts.MCN or amount <= 0:
        return 0
    total = _add(store, account_id, kind, amount, now)
    _thresholds(store, account_id, kind, total, now)
    return total


def check(store, account_id: Optional[str], kind: str, amount: int = 1, now: Optional[datetime] = None, record_usage: bool = True) -> int:
    """Refuse (LimitReached, HTTP 429) when `amount` more would pass the account's limit; else count it.
    amount=0 only asks "is there room left at all". Returns the usage after counting."""
    if not account_id or account_id == accounts.MCN:
        return 0
    limit = limit_for(store, account_id, kind)
    now_used = used(store, account_id, kind, now)
    if limit is not None and (now_used + amount > limit or (amount == 0 and now_used >= limit)):
        _thresholds(store, account_id, kind, max(now_used, limit), now)         # the 100% alert, once per period
        raise LimitReached(message(kind, limit), kind, limit)
    if amount <= 0 or not record_usage:
        return now_used
    return record(store, account_id, kind, amount, now)


def room(store, account_id: Optional[str], kind: str, now: Optional[datetime] = None) -> str:
    """"" when the account may still do one more of `kind`, else the plain refusal message (no exception)."""
    try:
        check(store, account_id, kind, 0, now)
    except LimitReached as exc:
        return exc.message
    return ""


def check_count(store, account_id: Optional[str], kind: str) -> None:
    """Seats and workspaces: refuse (403) when the account already holds as many as its limit allows."""
    limit = limit_for(store, account_id or "", kind)
    if limit is not None and static_count(store, account_id, kind) >= limit:
        _thresholds(store, account_id, kind, limit, None)
        raise LimitReached(message(kind, limit), kind, limit, status=403)


def current_account(store=None) -> Optional[str]:
    """The client account of this request (None for Mas Callnet logins, owners and background work)."""
    return accounts.current() if accounts.current_is_client() else None


def check_current(store, kind: str, amount: int = 1) -> int:
    """check() for the account of the request in progress."""
    return check(store, current_account(), kind, amount)


def check_workspace(store, ws_id: str, kind: str, amount: int = 1) -> int:
    """check() for the account that owns a workspace (connectors, webhooks: no login on the request)."""
    return check(store, accounts.account_of_ws(_real(store), ws_id or "main"), kind, amount)


def charge_ai(cfg: dict) -> None:
    """One AI credit for the account named in an AI config (see ai._llm_config), if any."""
    account, store = cfg.get("_account"), cfg.get("_store")
    if account and store is not None:
        check(store, account, "ai_credits", 1)


# ---------------------------------------------------------------- 80% / 100% alerts
def _thresholds(store, account_id: str, kind: str, total: int, now: Optional[datetime]) -> None:
    limit = limit_for(store, account_id, kind)
    if not limit:
        return
    day, month = _periods(now)
    period = day if (KINDS.get(kind) or ("month",))[0] == "day" else month if kind in KINDS else "now"
    for pct, edge in ((100, FULL_AT), (80, WARN_AT)):
        if total >= limit * edge:
            from . import abuse_alerts
            name = (accounts.find(_real(store), account_id) or {}).get("name", account_id)
            words = KINDS[kind][1] if kind in KINDS else STATIC[kind][0]
            title = f"{name} is at {pct}% of its {words}" if pct < 100 else f"{name} reached its {words}"
            text = (f"{name} has used {min(total, limit)} of {limit} ({words}). "
                    + ("Further work of that kind is refused until the period ends or you raise the limit."
                       if pct == 100 else "Raise the limit before it stops their work."))
            abuse_alerts.raise_alert(_real(store), f"limit{pct}|{account_id}|{kind}|{period}", f"limit_{pct}", account_id, title, text,
                                     audit_action="limit.threshold", to_account=True)
            return                                 # one alert per call: the highest edge crossed


# ---------------------------------------------------------------- what the pages show
def summary(store, account_id: str, now: Optional[datetime] = None) -> list:
    """Usage against each limit, for the account's own page and the console: [{kind, label, period, used, limit, pct, state}]."""
    out = []
    for kind in ORDER:
        limit = limit_for(store, account_id, kind)
        n = used(store, account_id, kind, now)
        pct = round(100 * n / limit) if limit else None
        label = STATIC[kind][1] if kind in STATIC else KINDS[kind][2]
        out.append({"kind": kind, "label": label, "period": "now" if kind in STATIC else KINDS[kind][0], "used": n, "limit": limit,
                    "pct": pct, "state": "unlimited" if limit is None else "full" if n >= limit else "warn" if n >= limit * WARN_AT else "ok"})
    return out
