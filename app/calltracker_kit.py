"""Integration Kit, Outreach side: bulk send leads to DialHalo, people matching, the user-wise calling report and the
nightly reconcile that fills calls a missed webhook never delivered.

All of it runs per workspace through that workspace's DialHalo connection (app/calltracker.py) and uses DialHalo's
public API: POST /api/v1/leads/bulk, GET /api/v1/members, GET /api/v1/stats/members, GET /api/v1/sync/calls.
"""
import csv
import io
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from . import calltracker as ct
from . import calltracker_events as ev
from .rep_workflow import CONNECTED
from .workspaces import ws_of

BULK_MAX = 500                    # DialHalo's per-request cap
SEND_MAX = 5000                   # one click never queues more than this
RECONCILE_PAGES = 20
RECONCILE_MATCH_MAX = 2000


def _phones(lead: dict) -> list:
    seen, out = set(), []
    for raw in [lead.get("phone"), *(lead.get("phones") or [])]:
        n = ct.norm_phone(raw)
        if len(n) >= 6 and n not in seen:
            seen.add(n)
            out.append(n)
    return out


# ---------------------------------------------------------------- 1. bulk send
def select_leads(store, ws: str, lead_ids=None, campaign_id: str = "", owner: str = "", stage: str = "", unsent_only: bool = False) -> list:
    """Leads of THIS workspace chosen by ids or by campaign / owner / stage filters. Opted-out leads are never included."""
    ids = set(lead_ids or [])
    out = []
    for lead in store.read("leads"):
        if ws_of(lead) != ws or ct.opted_out(lead):
            continue
        if ids and lead["id"] not in ids:
            continue
        if campaign_id and lead.get("campaign_id") != campaign_id:
            continue
        if owner and ct.email_of_person(store, lead.get("owner")) != owner.lower() and str(lead.get("owner") or "").lower() != owner.lower():
            continue
        if stage and lead.get("stage") != stage:
            continue
        if unsent_only and (lead.get("calltracker") or {}).get("lead_id"):
            continue
        out.append(lead)
    return out


def lead_body(store, lead: dict, base_url: str) -> dict:
    """One lead as DialHalo's bulk endpoint wants it: every number, the owner by e-mail, a link back, the call task's text as the note."""
    body = ct.push_payload(store, lead, base_url)
    phones = _phones(lead)
    body.pop("phone", None)
    body["phones"] = phones
    body["externalId"] = lead["id"]
    return body


def send_leads(store, ws: str, leads: list, base_url: str) -> dict:
    """Sends `leads` in chunks of 500. Re-sending updates the same DialHalo lead (by external id), never adds a twin."""
    conn = ct.active_connection(store, ws)
    if not conn:
        raise ct.CallTrackerError("DialHalo is not connected for this workspace, or it is switched off.", retry=False)
    leads = [l for l in leads if ws_of(l) == ws][:SEND_MAX]
    result = {"requested": len(leads), "sent": 0, "created": 0, "updated": 0, "skipped": [], "errors": [], "owner_unmatched": []}
    ready = []
    for lead in leads:
        if not _phones(lead):
            result["skipped"].append({"lead_id": lead["id"], "reason": "no phone number"})
        else:
            ready.append(lead)
    for start in range(0, len(ready), BULK_MAX):
        chunk = ready[start:start + BULK_MAX]
        payload = {"source": ct.SOURCE_LABEL, "externalSource": ct.SOURCE, "existing": {"details": "update_blank_only", "tags": "append"},
                   "mapExistingCalls": True, "assignment": {"strategy": "none"}, "leads": [lead_body(store, l, base_url) for l in chunk]}
        data = ct.ct_request(conn, "POST", "/api/v1/leads/bulk", payload)
        for row in data.get("saved") or []:
            lead = chunk[int(row["index"])]
            result["sent"] += 1
            result["created" if row.get("created") else "updated"] += 1
            if row.get("owner_matched") is False:
                result["owner_unmatched"].append(lead["id"])
            ct._lead_note(store, lead["id"], {"lead_id": row.get("id"), "status": "ok", "error": "", "pushed_at": ct.iso(ct.now_utc()),
                                              "owner_matched": row.get("owner_matched") is not False},
                          "Sent to DialHalo" if row.get("created") else None)
        for row in data.get("errors") or []:
            lead = chunk[int(row["index"])]
            result["errors"].append({"lead_id": lead["id"], "error": str(row.get("error") or "")[:200]})
            ct._lead_note(store, lead["id"], {"status": "failed", "error": str(row.get("error") or "")[:200]}, None)
    return result


def auto_send(store, ws: str, base_url: str, members: Optional[list] = None) -> dict:
    """The optional 'auto-send' setting: leads owned by a person who is a DialHalo member and not yet sent go over, so the
    calls they make count as lead calls and nothing shows up as 'not a lead'."""
    conn = ct.active_connection(store, ws)
    if not conn or not conn.get("auto_send"):
        return {"requested": 0, "sent": 0, "created": 0, "updated": 0, "skipped": [], "errors": [], "owner_unmatched": []}
    members = members if members is not None else _members(conn)
    emails = {str(m.get("email") or "").lower() for m in members if m.get("email") and m.get("active", 1)}
    mine = [l for l in select_leads(store, ws, unsent_only=True) if ct.email_of_person(store, l.get("owner")) in emails]
    return send_leads(store, ws, mine, base_url) if mine else {"requested": 0, "sent": 0, "created": 0, "updated": 0, "skipped": [], "errors": [], "owner_unmatched": []}


# ---------------------------------------------------------------- 2. people matching
def _members(conn: dict) -> list:
    data = ct.ct_request(conn, "GET", "/api/v1/members")
    return [m for m in (data if isinstance(data, list) else []) if isinstance(m, dict)]


def people(store, ws: str) -> dict:
    """Outreach people against DialHalo members (by e-mail). A person with leads but no member is a warning: their leads
    reach DialHalo's round robin instead of their own phone."""
    conn = ct.active_connection(store, ws)
    if not conn:
        raise ct.CallTrackerError("DialHalo is not connected for this workspace, or it is switched off.", retry=False)
    all_members = _members(conn)
    active = [m for m in all_members if m.get("active", 1)]
    preview = ct.owner_preview(store, ws, active, all_members)
    owned: dict = {}
    for lead in store.read("leads"):
        if ws_of(lead) == ws:
            email = ct.email_of_person(store, lead.get("owner"))
            if email:
                owned[email] = owned.get(email, 0) + 1
    rows = [{**p, "leads": owned.get(str(p["email"]).lower(), 0)} for p in preview]
    warnings = [{"email": r["email"], "name": r["name"], "leads": r["leads"], "reason": r.get("reason")} for r in rows if not r["matched"] and r["leads"]]
    dh_only = sorted({str(m.get("email") or "").lower() for m in active if m.get("email")} - {str(p["email"]).lower() for p in preview})
    return {"people": rows, "warnings": warnings, "members": len(active), "matched": sum(r["matched"] for r in rows),
            "only_in_dialhalo": [e for e in dh_only if e]}


# ---------------------------------------------------------------- 3. user-wise calling report
def _in_range(when, start, end) -> bool:
    return bool(when) and (not start or when >= start) and (not end or when < end)


def people_report(store, ws: str, start: Optional[datetime], end: Optional[datetime], dialhalo: Optional[dict] = None) -> dict:
    """Per person: calls Outreach saw (from DialHalo's call events, one row per call), connected, interested, meetings booked,
    talk time and leads called, next to DialHalo's own numbers for the person (all calls, talk time, lead calls) when given."""
    lead_ids = {l["id"] for l in store.read("leads") if ws_of(l) == ws}
    per: dict = {}
    for a in store.read("activities"):
        if a.get("source") != "calltracker" or a.get("lead_id") not in lead_ids:
            continue
        if not _in_range(ct.parse(a.get("at")), start, end):
            continue
        email = str((a.get("ct") or {}).get("user_email") or "").lower() or "unknown"
        r = per.setdefault(email, {"email": email, "name": a.get("by") or email, "calls": 0, "connected": 0, "interested": 0, "meetings": 0, "talk_minutes": 0.0, "leads": set()})
        r["calls"] += 1
        r["connected"] += 1 if a.get("outcome") in CONNECTED else 0
        r["interested"] += 1 if a.get("outcome") == "interested" else 0
        r["meetings"] += 1 if a.get("outcome") == "meeting_booked" else 0
        r["talk_minutes"] += float(a.get("duration_minutes") or 0)
        r["leads"].add(a["lead_id"])
    for m in (dialhalo or {}).get("members") or []:
        email = str(m.get("email") or "").lower() or "unknown"
        r = per.setdefault(email, {"email": email, "name": m.get("name") or email, "calls": 0, "connected": 0, "interested": 0, "meetings": 0, "talk_minutes": 0.0, "leads": set()})
        r.update(dialhalo_calls=int(m.get("calls") or 0), dialhalo_connected=int(m.get("connected") or 0), dialhalo_talk_minutes=round(int(m.get("talk_sec") or 0) / 60, 1),
                 dialhalo_lead_calls=int(m.get("lead_calls") or 0), dialhalo_leads_called=int(m.get("unique_leads_called") or 0))
    rows = []
    for r in per.values():
        r = {**r, "leads_called": len(r.pop("leads")), "talk_minutes": round(r["talk_minutes"], 1)}
        rows.append(r)
    rows.sort(key=lambda r: (-(r["calls"] + r.get("dialhalo_calls", 0)), r["email"]))
    return {"rows": rows, "totals": {k: round(sum(r.get(k, 0) for r in rows), 1) for k in ("calls", "connected", "interested", "meetings", "talk_minutes", "leads_called")}}


REPORT_COLUMNS = ("name", "email", "calls", "connected", "interested", "meetings", "talk_minutes", "leads_called",
                  "dialhalo_calls", "dialhalo_connected", "dialhalo_talk_minutes", "dialhalo_lead_calls", "dialhalo_leads_called")


def report_csv(report: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(REPORT_COLUMNS)
    for r in report["rows"]:
        w.writerow([("'" + str(r.get(c)) if isinstance(r.get(c), str) and str(r.get(c))[:1] in "=+-@" else r.get(c, "")) for c in REPORT_COLUMNS])
    return out.getvalue()


def dialhalo_stats(conn: dict, start: Optional[datetime], end: Optional[datetime]) -> Optional[dict]:
    """DialHalo's own per-member numbers for the same dates; None when it cannot be reached (the report still shows Outreach's side)."""
    today = datetime.now(ev.IST)
    f = (start or today - timedelta(days=29)).astimezone(ev.IST).date().isoformat()
    t = ((end - timedelta(days=1)) if end else today).astimezone(ev.IST).date().isoformat()
    try:
        return ct.ct_request(conn, "GET", f"/api/v1/stats/members?from={f}&to={t}")
    except ct.CallTrackerError:
        return None


# ---------------------------------------------------------------- 4. reconcile
def reconcile(store, ws: str) -> dict:
    """Pulls DialHalo's call change feed from the saved cursor and applies every call on one of this workspace's leads.
    Idempotent (a call already on the timeline is updated, not added), so webhooks and this job can overlap freely."""
    conn = ct.active_connection(store, ws)
    if not conn:
        raise ct.CallTrackerError("DialHalo is not connected for this workspace, or it is switched off.", retry=False)
    cursor = conn.get("sync_cursor") or ""
    seen = applied = pages = 0
    numbers = set()
    for lead in store.read("leads"):
        if ws_of(lead) == ws:
            numbers.update(_phones(lead))
    while pages < RECONCILE_PAGES and seen < RECONCILE_MATCH_MAX:
        pages += 1
        feed = ct.ct_request(conn, "GET", "/api/v1/sync/calls?limit=500" + (f"&cursor={cursor}" if cursor else ""))
        for call in feed.get("calls") or []:
            if ct.norm_phone(call.get("number")) not in numbers and not call.get("lead_id"):
                continue
            if not ev.match_lead(store, ws, {"number": call.get("number"), "lead": {"id": call.get("lead_id")}}):
                continue
            seen += 1
            try:
                detail = ct.ct_request(conn, "GET", f"/api/v1/calls/{int(call['id'])}")
            except ct.CallTrackerError as exc:
                if exc.status == 404:
                    continue
                raise
            applied += 1 if ev.apply_call(store, conn, detail) else 0
        cursor = feed.get("next_cursor") or cursor
        _conn_set(store, ws, {"sync_cursor": cursor})          # saved page by page: a crash resumes, never restarts
        if not feed.get("has_more"):
            break
    _conn_set(store, ws, {"last_reconcile": ct.iso(ct.now_utc())})
    return {"pages": pages, "matched": seen, "applied": applied, "cursor": cursor}


def _conn_set(store, ws: str, values: dict) -> None:
    store.update("ct_connections", lambda rows: {**rows, ws: {**(rows.get(ws) or {}), **values}} if rows.get(ws) else rows)


_loop = {"running": False}
_loop_lock = threading.Lock()
INTERVAL_HOURS = 20


def reconcile_due(store, now=None) -> list:
    """Reconciles every connected workspace whose last run is older than INTERVAL_HOURS. Returns the workspaces it ran."""
    now = now or ct.now_utc()
    ran = []
    for ws, conn in list(ct.connections(store).items()):
        if not ct.active_connection(store, ws):
            continue
        last = ct.parse(conn.get("last_reconcile"))
        if last and now - last < timedelta(hours=INTERVAL_HOURS):
            continue
        try:
            reconcile(store, ws)
            ct.note_status(store, ws, "last_reconcile_status", True, "")
            ran.append(ws)
        except ct.CallTrackerError as exc:
            ct.note_status(store, ws, "last_reconcile_status", False, str(exc))
    return ran


def start_loop(store, check_seconds: int = 600) -> None:
    """Background thread: checks every 10 minutes whether a nightly reconcile is due. One thread per process."""
    if not ct.KICK:
        return
    with _loop_lock:
        if _loop["running"]:
            return
        _loop["running"] = True

    def run():
        while True:
            try:
                reconcile_due(store)
            except Exception as exc:                   # never kill the app
                print(f"calltracker reconcile stopped a run: {type(exc).__name__}")
            time.sleep(check_seconds)
    threading.Thread(target=run, name="ct-reconcile", daemon=True).start()
