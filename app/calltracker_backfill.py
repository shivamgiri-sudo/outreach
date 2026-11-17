"""Match past calls: Integrations -> DialHalo -> "Match past calls" (owner or the workspace's admin).

Reads the workspace's DialHalo call history (POST /api/v1/reports/history {from, to, page, pageSize}, newest first, 500
per page; rows carry `client_number`), matches each distinct number against the workspace's leads with the receiver's
phone rule (app/calltracker_events.py pick_lead), and sends the results through the coalescing "match" job
(app/calltracker_match.py). One run per workspace at a time, in a background thread, bounded to MAX_CALLS calls.
Progress is saved after every page (ct_backfill {ws: state}); a run that stopped (DialHalo down, server restart)
continues from its last page when started again with the same number of days. Repeating a run is harmless: DialHalo
keeps one match per number, so the same results only overwrite themselves.
"""
import threading
import time
from datetime import timedelta, timezone

from .workspaces import ws_of

DAYS = (7, 30, 90)
PAGE_SIZE = 500
MAX_CALLS = 20000
PAGE_TRIES = 3
BACKGROUND = True                 # tests run the backfill inline
SLEEP = time.sleep
_running = set()                  # workspaces with a live backfill thread in this process
_lock = threading.Lock()


def _ct():
    from . import calltracker
    return calltracker


def state(store, ws: str) -> dict:
    row = dict((store.read("ct_backfill") or {}).get(ws) or {})
    if row.get("status") == "running" and ws not in _running:
        row["status"] = "stopped"                 # the server restarted mid-run: it can be continued
        row["error"] = row.get("error") or "Stopped before the end (the server restarted). Start it again to continue."
    return row


def _save(store, ws: str, changes: dict) -> dict:
    out = {}

    def apply(rows: dict) -> dict:
        out.update({**(rows.get(ws) or {}), **changes})
        return {**rows, ws: dict(out)}
    store.update("ct_backfill", apply)
    return out


def start(store, ws: str, days: int, who: str) -> dict:
    """Start (or continue) a backfill. Raises ValueError with a message for the person when it cannot start."""
    ct = _ct()
    if days not in DAYS:
        raise ValueError("Choose 7, 30 or 90 days.")
    if not ct.active_connection(store, ws):
        raise ValueError("Connect DialHalo (address and API key) and switch it on first.")
    with _lock:
        if ws in _running:
            raise ValueError("Past calls are already being matched for this workspace. Wait for it to finish.")
        _running.add(ws)
    prev = state(store, ws)
    resume = prev.get("status") in ("stopped", "failed") and prev.get("days") == days and prev.get("page")
    now = ct.now_utc()
    if resume:
        _save(store, ws, {"status": "running", "error": "", "resumed_at": ct.iso(now), "by": who})
    else:
        _save(store, ws, {"status": "running", "days": days, "from": _ist_day(now - timedelta(days=days - 1)), "to": _ist_day(now), "page": 0, "calls": 0, "numbers": 0,
                                "matched": 0, "not_found": 0, "skipped": 0, "total": None, "error": "", "started_at": ct.iso(now),
                                "finished_at": None, "by": who, "seen": []})
    if BACKGROUND:
        threading.Thread(target=_run, args=(store, ws), daemon=True, name=f"calltracker-backfill-{ws}").start()
    else:
        _run(store, ws)
    return public(state(store, ws))


def _ist_day(dt) -> str:
    return dt.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")


def _index(store, ws: str) -> dict:
    """norm phone -> [leads] for the workspace, built once per run (no copy of every lead)."""
    from .calltracker_match import phones_of
    index = {}
    for lead in store.peek("leads"):
        if ws_of(lead) == ws:
            for key in phones_of(lead):
                index.setdefault(key, []).append(lead)
    return index


def _run(store, ws: str) -> None:
    ct = _ct()
    from . import calltracker_events as events, calltracker_match as match
    try:
        row = state(store, ws)
        index = _index(store, ws)
        seen = set(row.get("seen") or [])
        page = int(row.get("page") or 0)
        while row["calls"] < MAX_CALLS:
            conn = ct.active_connection(store, ws)
            if not conn:
                raise ct.CallTrackerError("The DialHalo connection was removed or switched off.", retry=False)
            data = _page(conn, row, page + 1)
            rows = [r for r in (data.get("rows") or []) if isinstance(r, dict)][:MAX_CALLS - row["calls"]]
            found, skipped = {}, 0
            for call in rows:
                number = call.get("client_number") or call.get("number")
                if not match.usable(number):
                    skipped += 1
                    continue
                key = ct.norm_phone(number)
                if key in seen:
                    continue
                seen.add(key)
                cands = index.get(key) or []
                found[str(number)] = events.pick_lead(store, [dict(l) for l in cands])
            match.queue_results(store, ws, found)
            hits = sum(1 for lead in found.values() if lead)
            page += 1
            row = _save(store, ws, {"page": page, "calls": row["calls"] + len(rows), "numbers": len(seen), "matched": row["matched"] + hits,
                                    "not_found": row["not_found"] + len(found) - hits, "skipped": row.get("skipped", 0) + skipped,
                                    "total": data.get("total"), "seen": sorted(seen)})
            if len(rows) < PAGE_SIZE or (data.get("total") is not None and page * PAGE_SIZE >= int(data["total"])):
                break
        capped = row["calls"] >= MAX_CALLS
        _save(store, ws, {"status": "done", "finished_at": ct.iso(ct.now_utc()), "capped": capped, "seen": [],
                          "error": f"Stopped at {MAX_CALLS:,} calls (the limit for one run)." if capped else ""})
    except ct.CallTrackerError as exc:
        msg = ("This DialHalo is too old to list past calls for matching. Update DialHalo (HTTP 404)." if exc.status == 404 else str(exc))
        _save(store, ws, {"status": "failed", "error": msg, "finished_at": ct.iso(ct.now_utc())})
    except Exception as exc:                          # never kill the app; the state says what happened
        _save(store, ws, {"status": "failed", "error": f"Unexpected error ({type(exc).__name__}).", "finished_at": ct.iso(ct.now_utc())})
    finally:
        with _lock:
            _running.discard(ws)


def _page(conn: dict, row: dict, page: int) -> dict:
    """One page of call history, retried a few times when DialHalo is busy (429 / 5xx / unreachable)."""
    ct = _ct()
    body = {"from": row["from"], "to": row["to"], "dateField": "call", "page": page, "pageSize": PAGE_SIZE}
    for attempt in range(PAGE_TRIES):
        try:
            data = ct.ct_request(conn, "POST", "/api/v1/reports/history", body)
            return data if isinstance(data, dict) else {}
        except ct.CallTrackerError as exc:
            if not exc.retry or attempt == PAGE_TRIES - 1:
                raise
            SLEEP(2 ** attempt * 5)
    return {}


def public(row: dict) -> dict:
    """What the card shows (no numbers)."""
    return {k: v for k, v in (row or {}).items() if k != "seen"}
