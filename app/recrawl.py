"""Scheduled re-crawl of lead websites and a timeline of real changes.

A small background loop (start_loop, started with the other timers when OUTREACH_AUTORUN=1) wakes every WAKE_SECONDS and,
at most once per RUN_EVERY_SECONDS (an hour), re-reads up to BATCH domains (20) with the hiring-signal reader
(app/hiring_signal.py: careers pages + home-page markup via app/site_markup.py, robots.txt and the polite delay kept).

Which leads: active ones only, so the load stays tiny: stage not closed (not_now / unsubscribed / disqualified), and the
lead is in a running sequence, on a list, or came from a running campaign; and its website was last read more than
`days` ago (settings.recrawl.days, default 30) or never. The owner can switch the loop off (settings.recrawl.enabled,
default on). The last run is kept in settings.recrawl_status.

Each lead keeps a compact snapshot of what its site said last time (lead.site_snapshot = {at, roles, titles (10),
phone, social}). After a read, real changes only become timeline lines:
  "Started hiring (N roles)"  (also an alert to the lead's owner: the bell, app/alerts.py)
  "Stopped hiring", "New roles: ...", "Phone on website changed", "New social link: ..."
No change means no line. The first read of a lead only stores the snapshot (the hiring reader's own line says what it found).
A site that cannot be read keeps the old snapshot and is tried again after `days`.
"""
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import hiring_signal as hs
from .lead_model import event, now_iso

SETTINGS_KEY, STATUS_KEY = "recrawl", "recrawl_status"
DEFAULT_DAYS, MIN_DAYS, MAX_DAYS = 30, 7, 180
BATCH = 20
RUN_EVERY_SECONDS = 3600
WAKE_SECONDS = 300
CLOSED_STAGES = ("not_now", "unsubscribed", "disqualified")
MAX_TITLES = 10
_lock = threading.Lock()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def settings_of(store) -> dict:
    saved = (store.read("settings") or {}).get(SETTINGS_KEY) or {}
    try:
        days = max(MIN_DAYS, min(MAX_DAYS, int(saved.get("days", DEFAULT_DAYS))))
    except (TypeError, ValueError):
        days = DEFAULT_DAYS
    return {"enabled": bool(saved.get("enabled", True)), "days": days}


def status_of(store) -> dict:
    return (store.read("settings") or {}).get(STATUS_KEY) or {}


# ---------- which leads ----------
def _active_sets(store) -> tuple:
    listed = {i for row in store.read("lists") for i in row.get("lead_ids") or []}
    running = {c["id"] for c in store.read("campaigns") if c.get("status") == "running"}
    return listed, running


def is_active(lead: dict, listed: set, running: set) -> bool:
    if lead.get("stage") in CLOSED_STAGES or not lead.get("domain"):
        return False
    seq = lead.get("sequence") or {}
    return bool((seq and not seq.get("stopped")) or lead["id"] in listed or (lead.get("campaign_id") and lead["campaign_id"] in running))


def last_read(lead: dict) -> str:
    return max(str((lead.get("hiring_signal") or {}).get("checked_at") or ""), str((lead.get("site_snapshot") or {}).get("tried_at") or ""))


def pick(store, now: Optional[datetime] = None, limit: int = BATCH) -> list:
    """[(domain, [lead ids])] for up to `limit` domains of active leads not read within `days`, oldest first."""
    now = now or now_utc()
    since = (now - timedelta(days=settings_of(store)["days"])).isoformat(timespec="seconds")
    listed, running = _active_sets(store)
    by_domain: dict = {}
    for lead in store.read("leads"):
        if is_active(lead, listed, running) and last_read(lead) < since:
            by_domain.setdefault(lead["domain"], []).append((last_read(lead), lead["id"]))
    order = sorted(by_domain.items(), key=lambda kv: min(t for t, _ in kv[1]))
    return [(d, [i for _, i in rows]) for d, rows in order[:max(1, min(int(limit), BATCH))]]


# ---------- snapshots and changes ----------
def _digits(phone) -> str:
    return re.sub(r"\D", "", str(phone or ""))[-10:]


def _social(facts: dict) -> list:
    links = list((facts.get("social") or {}).values()) + [facts.get("company_linkedin_url") or ""]
    return sorted({str(u).strip().rstrip("/") for u in links if u})


def snapshot_of(result: dict, now: datetime) -> dict:
    hiring, facts = result.get("hiring") or {}, result.get("facts") or {}
    return {"at": now.isoformat(timespec="seconds"), "roles": int(hiring.get("open_roles") or 0),
            "titles": list(hiring.get("titles") or [])[:MAX_TITLES], "phone": facts.get("phone") or "", "social": _social(facts)}


def baseline(lead: dict) -> Optional[dict]:
    """The last snapshot; for a lead read before snapshots existed, one built from its hiring signal and company facts."""
    snap = lead.get("site_snapshot") or {}
    if snap.get("at"):
        return snap
    hiring = lead.get("hiring_signal") or {}
    if not hiring.get("checked_at"):
        return None
    facts = lead.get("company_facts")
    found = (facts or {}).get("found") or {}
    return {"at": hiring["checked_at"], "roles": int(hiring.get("open_roles") or 0), "titles": list(hiring.get("titles") or []),
            "phone": found.get("phone") or "", "social": _social(found) if facts else None}      # None: links never seen


def diff(prev: dict, new: dict) -> list:
    """[(kind, text)] for real changes only."""
    out = []
    if not prev.get("roles") and new["roles"]:
        out.append(("started", f"Started hiring ({new['roles']} role{'s' if new['roles'] != 1 else ''})"))
    elif prev.get("roles") and not new["roles"]:
        out.append(("stopped", "Stopped hiring"))
    elif new["roles"]:
        before = {t.lower() for t in prev.get("titles") or []}
        added = [t for t in new["titles"] if t.lower() not in before]
        if added:
            out.append(("roles", "New roles: " + ", ".join(added[:5]) + (f" and {len(added) - 5} more" if len(added) > 5 else "")))
    if _digits(prev.get("phone")) and _digits(new["phone"]) and _digits(prev.get("phone")) != _digits(new["phone"]):
        out.append(("phone", "Phone on website changed"))
    added = [u for u in new["social"] if u not in set(prev.get("social") or [])]
    if added and prev.get("social") is not None:
        out.append(("social", "New social link: " + ", ".join(added[:3])))
    return out


def _save(store, lead_id: str, snap: dict, changes: list) -> None:
    def change(lead):
        lines = [event("jobs" if k in ("started", "stopped", "roles") else "enrichment", t) for k, t in changes]
        return {**lead, "site_snapshot": snap, "timeline": [*lead.get("timeline", []), *lines]}
    store.update_lead(lead_id, change)


def _notify(store, lead_id: str, text: str) -> None:
    from . import alerts
    try:
        alerts.send(store, "started_hiring", lead_id, f"{text} on the company's own website.")
    except Exception:                                    # noqa: BLE001 - an alert never stops the re-crawl
        pass


# ---------- one run ----------
def run_once(store, fetch: Callable, now: Optional[datetime] = None, limit: int = BATCH) -> dict:
    """Read up to `limit` due domains now. Never raises for a single site."""
    now = now or now_utc()
    work = pick(store, now, limit)
    out = {"at": now.isoformat(timespec="seconds"), "domains": 0, "changes": 0, "started": 0, "errors": 0, "note": ""}
    for domain, ids in work:
        out["domains"] += 1
        try:
            result = hs.lookup(store, domain, fetch, now, max_age=timedelta(hours=12))
        except Exception:                                # noqa: BLE001
            result = {"error": "the website could not be read"}
        by_id = {l["id"]: l for l in store.read("leads") if l["id"] in set(ids)}
        if result.get("error"):
            out["errors"] += 1
            for lead_id, lead in by_id.items():           # keep the old snapshot; try again after `days`
                snap = {**(lead.get("site_snapshot") or {}), "tried_at": now.isoformat(timespec="seconds")}
                store.update_lead(lead_id, lambda l, s=snap: {**l, "site_snapshot": s})
            continue
        new = snapshot_of(result, now)
        for lead_id, lead in by_id.items():
            prev = baseline(lead)
            changes = diff(prev, new) if prev else []
            hs.apply(store, lead_id, result, job_event=prev is None)
            _save(store, lead_id, new, changes)
            out["changes"] += len(changes)
            for kind, text in changes:
                if kind == "started":
                    out["started"] += 1
                    _notify(store, lead_id, text)
    if not work:
        out["note"] = "nothing due"
    store.update("settings", lambda s: {**s, STATUS_KEY: out})
    return out


def due(store, now: Optional[datetime] = None) -> bool:
    if not settings_of(store)["enabled"]:
        return False
    last = status_of(store).get("at")
    try:
        return (now or now_utc()) - datetime.fromisoformat(last) >= timedelta(seconds=RUN_EVERY_SECONDS)
    except (TypeError, ValueError):
        return True


def tick(store, fetch: Callable, now: Optional[datetime] = None) -> Optional[dict]:
    """One wake-up of the loop: a run when switched on and an hour has passed, else None. One run at a time."""
    if not due(store, now) or not _lock.acquire(blocking=False):
        return None
    try:
        return run_once(store, fetch, now)
    finally:
        _lock.release()


def start_loop(store, fetch: Callable, wake_seconds: int = WAKE_SECONDS) -> threading.Event:
    stop = threading.Event()

    def loop():
        while not stop.wait(wake_seconds):
            try:
                tick(store, fetch)
            except Exception as exc:                     # keep the timer alive; the next wake-up retries
                print(f"site re-crawl failed: {type(exc).__name__}")
                time.sleep(1)
    threading.Thread(target=loop, daemon=True, name="site-recrawl").start()
    return stop
