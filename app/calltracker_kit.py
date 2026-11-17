"""Integration Kit, Outreach side: "send now" in bulk, people matching, the user-wise calling report and the nightly
reconcile that fills calls a missed webhook never delivered. Everything runs per DialHalo LINK (app/calltracker_links.py):
one Outreach workspace can be linked to several DialHalo workspaces, and each has its own members, numbers and feed.

Uses DialHalo's public API: POST /api/v1/leads/bulk, GET /api/v1/members, GET /api/v1/stats/members,
GET /api/v1/sync/calls, GET /api/v1/calls/{id}.

One pusher, one routing rule. Automatic sending belongs to the links' lead sync (call tasks, owners who are members, every
lead with a phone: app/calltracker_sync.py and the push queue). The kit adds no automatic pusher: its bulk send is a
manual "send now" to ONE chosen link, on top of the same rule that a lead lives in at most one DialHalo workspace. A lead
already pushed to (or waiting to be pushed to) another link is skipped and reported, never copied. The bulk endpoint is
used instead of one PUT per lead because a person clicking "send 2,000 leads" wants an answer now, with every number of
each lead; the result is written to the lead exactly as the queue writes it (calltracker.lead_id + link), so the sweep
and call tasks treat a lead sent here as already sent. (The old kit "auto-send" switch is the links' "Leads whose owner
is a member there" lead sync.)

The reconcile also feeds the Outreach match of the same link: a call on a lead goes through the receiver's apply_call
(queued as "matched"), a number that is not one of our leads is queued as "not found", so DialHalo's Outreach match page
is complete even for calls whose webhook never arrived.
"""
import csv
import io
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from . import calltracker as ct
from . import calltracker_events as ev
from . import calltracker_links as links
from . import calltracker_match as ct_match
from .rep_workflow import CONNECTED
from .workspaces import ws_of

BULK_MAX = 500                    # DialHalo's per-request cap
SEND_MAX = 5000                   # one click never sends more than this
STATS_MAX_DAYS = 90
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


def _request(store, link: dict, method: str, path: str, body: Optional[dict] = None) -> dict:
    """ct_request that also keeps the link's "Not authorised — reconnect" state (app/calltracker_links.note_auth)."""
    try:
        return ct.ct_request(link, method, path, body)
    except ct.CallTrackerError as exc:
        if exc.status in (401, 403):
            links.note_auth(store, link["id"], True, str(exc))
        raise


def link_for(store, ws: str, link_id: str = "") -> dict:
    """The active link a kit action means: `link_id` (must be this workspace's), else the workspace's primary active link."""
    link = links.get(store, link_id) if link_id else links.primary_active(store, ws)
    if link_id and (not link or link.get("workspace") != ws):
        raise ct.CallTrackerError("This DialHalo link was not found in this workspace.", 404, retry=False)
    if not links.is_active(link):
        raise ct.CallTrackerError("DialHalo is not connected for this workspace, or this link is switched off.", retry=False)
    return link


def _name(link: Optional[dict]) -> str:
    return ((link or {}).get("dh_workspace") or {}).get("name") or (link or {}).get("public_url") or (link or {}).get("base_url") or "another DialHalo workspace"


# ---------------------------------------------------------------- 1. bulk "send now"
def select_leads(store, ws: str, lead_ids=None, campaign_id: str = "", owner: str = "", stage: str = "", unsent_only: bool = False) -> list:
    """Leads of THIS workspace chosen by ids or by campaign / owner / stage filters. Opted-out leads are never included.
    unsent_only: never sent to any DialHalo workspace."""
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
    body.pop("phone", None)
    body["phones"] = _phones(lead)
    body["externalId"] = lead["id"]
    return body


def send_leads(store, ws: str, leads: list, base_url: str, link: dict) -> dict:
    """Sends `leads` to `link` in chunks of 500. Re-sending updates the same DialHalo lead (by external id), never adds a
    twin. A lead that belongs to another link of the workspace (already pushed, or a push waiting there) is skipped."""
    if (link.get("lead_sync") or "call_tasks") == "off":
        raise ct.CallTrackerError(f"Lead sending is off for {_name(link)}. Choose how leads are sent on its card first.", retry=False)
    mine = links.links_of(store, ws)
    waiting = {j.get("lead_id"): j.get("link") for j in store.read("ct_outbox")
               if j.get("kind") == "push" and j.get("status") == "pending" and j.get("ws") == ws}
    leads = [l for l in leads if ws_of(l) == ws][:SEND_MAX]
    result = {"link": link["id"], "requested": len(leads), "sent": 0, "created": 0, "updated": 0, "skipped": [], "errors": [], "owner_unmatched": []}
    ready = []
    for lead in leads:
        owner_link = links.pushed_link(store, lead, mine) or waiting.get(lead["id"])
        if owner_link and owner_link != link["id"]:
            result["skipped"].append({"lead_id": lead["id"], "reason": f"already in {_name(links.get(store, owner_link))}"})
        elif not _phones(lead):
            result["skipped"].append({"lead_id": lead["id"], "reason": "no phone number"})
        else:
            ready.append(lead)
    where = ((link.get("dh_workspace") or {}).get("name") or "")[:80]
    for start in range(0, len(ready), BULK_MAX):
        chunk = ready[start:start + BULK_MAX]
        payload = {"source": ct.SOURCE_LABEL, "externalSource": ct.SOURCE, "existing": {"details": "update_blank_only", "tags": "append"},
                   "mapExistingCalls": True, "assignment": {"strategy": "none"}, "leads": [lead_body(store, l, base_url) for l in chunk]}
        try:
            data = _request(store, link, "POST", "/api/v1/leads/bulk", payload)
        except ct.CallTrackerError as exc:
            ct.note_status(store, link["id"], "last_push", False, f"Send now: {exc}")
            raise
        for row in data.get("saved") or []:
            lead = chunk[int(row["index"])]
            result["sent"] += 1
            result["created" if row.get("created") else "updated"] += 1
            if row.get("owner_matched") is False:
                result["owner_unmatched"].append(lead["id"])
            ct._lead_note(store, lead["id"], {"lead_id": row.get("id"), "link": link["id"], "status": "ok", "error": "", "pushed_at": ct.iso(ct.now_utc()),
                                              "owner_matched": row.get("owner_matched") is not False},
                          f"Sent to DialHalo{f' ({where})' if where else ''}" if row.get("created") else None)
        for row in data.get("errors") or []:
            lead = chunk[int(row["index"])]
            result["errors"].append({"lead_id": lead["id"], "error": str(row.get("error") or "")[:200]})
            ct._lead_note(store, lead["id"], {"status": "failed", "error": str(row.get("error") or "")[:200]}, None)
    if ready:
        ct.note_status(store, link["id"], "last_push", not result["errors"], f"Send now: {result['sent']} sent, {len(result['errors'])} refused")
        links.note_auth(store, link["id"], False)
    return result


# ---------------------------------------------------------------- 2. people matching
def people(store, ws: str, link: dict) -> dict:
    """Outreach people against this link's DialHalo members (by e-mail; refreshes the members cache the link's pairing and
    "owners who are members" lead sync use). A person with leads but no member is a warning: their leads reach DialHalo's
    distribution instead of their own phone (and are never sent by "owners who are members")."""
    all_members = links.members_of(store, link, refresh=True)
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
    return {"link": link["id"], "people": rows, "warnings": warnings, "members": len(active), "matched": sum(r["matched"] for r in rows),
            "only_in_dialhalo": [e for e in dh_only if e]}


# ---------------------------------------------------------------- 3. user-wise calling report
def _in_range(when, start, end) -> bool:
    return bool(when) and (not start or when >= start) and (not end or when < end)


def people_report(store, ws: str, start: Optional[datetime], end: Optional[datetime], dialhalo: Optional[dict] = None,
                  link_id: str = "") -> dict:
    """Per person: calls Outreach saw (DialHalo call events, one row per call; only this link's when `link_id`), connected,
    interested, meetings booked, talk time and leads called, next to DialHalo's own numbers for the person when given."""
    lead_ids = {l["id"] for l in store.read("leads") if ws_of(l) == ws}
    per: dict = {}
    for a in store.read("activities"):
        if a.get("source") != "calltracker" or a.get("lead_id") not in lead_ids:
            continue
        if link_id and (a.get("ct") or {}).get("conn") != link_id:
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
        leads = r.pop("leads")
        rows.append({**r, "leads_called": len(leads), "talk_minutes": round(r["talk_minutes"], 1)})
    rows.sort(key=lambda r: (-(r["calls"] + r.get("dialhalo_calls", 0)), r["email"]))
    return {"link": link_id or None, "dialhalo": {"ok": dialhalo is not None, "clamped_from": (dialhalo or {}).get("clamped_from")},
            "rows": rows, "totals": {k: round(sum(r.get(k, 0) for r in rows), 1) for k in ("calls", "connected", "interested", "meetings", "talk_minutes", "leads_called")}}


REPORT_COLUMNS = ("name", "email", "calls", "connected", "interested", "meetings", "talk_minutes", "leads_called",
                  "dialhalo_calls", "dialhalo_connected", "dialhalo_talk_minutes", "dialhalo_lead_calls", "dialhalo_leads_called")


def report_csv(report: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(REPORT_COLUMNS)
    for r in report["rows"]:
        w.writerow([("'" + str(r.get(c)) if isinstance(r.get(c), str) and str(r.get(c))[:1] in "=+-@" else r.get(c, "")) for c in REPORT_COLUMNS])
    return out.getvalue()


def dialhalo_stats(store, link: dict, start: Optional[datetime], end: Optional[datetime]) -> Optional[dict]:
    """DialHalo's own per-member numbers (this link's workspace) for the same dates; None when it cannot be reached."""
    today = datetime.now(ev.IST)
    last = min(((end - timedelta(days=1)) if end else today).astimezone(ev.IST), today)     # never ask about days still to come
    first = (start or last - timedelta(days=29)).astimezone(ev.IST)
    clamped = (last - first).days + 1 > STATS_MAX_DAYS
    if clamped:                                    # DialHalo answers at most 93 days at a time: show the latest part and say so
        first = last - timedelta(days=STATS_MAX_DAYS - 1)
    try:
        data = _request(store, link, "GET", f"/api/v1/stats/members?from={first.date().isoformat()}&to={last.date().isoformat()}")
    except ct.CallTrackerError:
        return None
    return {**data, "clamped_from": first.date().isoformat() if clamped else None}


# ---------------------------------------------------------------- 4. reconcile (per link)
def reconcile(store, link: dict) -> dict:
    """Pulls this link's DialHalo call change feed from its saved cursor, applies every call on one of the workspace's leads
    (idempotent with webhooks: a call already on the timeline is updated, not added) and sends the Outreach match results
    for the numbers it saw to the same link."""
    ws, lid = link["workspace"], link["id"]
    cursor = link.get("sync_cursor") or ""
    seen = applied = pages = 0
    numbers = set()
    for lead in store.read("leads"):
        if ws_of(lead) == ws:
            numbers.update(_phones(lead))
    while pages < RECONCILE_PAGES and seen < RECONCILE_MATCH_MAX:
        pages += 1
        feed = _request(store, link, "GET", "/api/v1/sync/calls?limit=500" + (f"&cursor={cursor}" if cursor else ""))
        found: dict = {}
        try:
            for call in feed.get("calls") or []:
                if ct.norm_phone(call.get("number")) not in numbers and not call.get("lead_id"):
                    if call.get("number") and len(found) < ev.MAX_UNMATCHED_PER_EVENT:
                        found.setdefault(call["number"], None)          # "not found" (masked numbers are skipped by the match)
                    continue
                if not ev.match_lead(store, ws, {"number": call.get("number"), "lead": {"id": call.get("lead_id")}}):
                    continue
                seen += 1
                try:
                    detail = _request(store, link, "GET", f"/api/v1/calls/{int(call['id'])}")
                except ct.CallTrackerError as exc:
                    if exc.status == 404:
                        continue
                    raise
                applied += 1 if ev.apply_call(store, link, detail, found) else 0
        finally:
            if found:
                ct_match.note_calls(store, link, found)
        cursor = feed.get("next_cursor") or cursor
        _link_set(store, lid, {"sync_cursor": cursor})          # saved page by page: a crash resumes, never restarts
        if not feed.get("has_more"):
            break
    _link_set(store, lid, {"last_reconcile": ct.iso(ct.now_utc())})
    return {"link": lid, "pages": pages, "matched": seen, "applied": applied, "cursor": cursor}


def reconcile_workspace(store, ws: str, link_id: str = "") -> dict:
    """One link (`link_id`) or every active link of the workspace. Totals plus one row per link."""
    targets = [link_for(store, ws, link_id)] if link_id else links.active_links(store, ws)
    if not targets:
        raise ct.CallTrackerError("DialHalo is not connected for this workspace, or it is switched off.", retry=False)
    rows = [reconcile(store, l) for l in targets]
    return {"links": rows, **{k: sum(r[k] for r in rows) for k in ("pages", "matched", "applied")}}


def _link_set(store, link_id: str, values: dict) -> None:
    store.update("ct_connections", lambda rows: {**rows, link_id: {**rows[link_id], **values}} if link_id in rows else rows)


_loop = {"running": False}
_loop_lock = threading.Lock()
INTERVAL_HOURS = 20


def reconcile_due(store, now=None) -> list:
    """Reconciles every active link whose last run is older than INTERVAL_HOURS. Returns the link ids it ran."""
    now = now or ct.now_utc()
    ran = []
    for link in links.all_links(store):
        if not links.is_active(link):
            continue
        last = ct.parse(link.get("last_reconcile"))
        if last and now - last < timedelta(hours=INTERVAL_HOURS):
            continue
        try:
            reconcile(store, link)
            ct.note_status(store, link["id"], "last_reconcile_status", True, "")
            ran.append(link["id"])
        except ct.CallTrackerError as exc:
            ct.note_status(store, link["id"], "last_reconcile_status", False, str(exc))
    return ran


def start_loop(store, check_seconds: int = 600) -> None:
    """Background thread: checks every 10 minutes whether a nightly reconcile is due. One thread per process. (Automatic
    lead sending is the links' lead sync in the calltracker worker, not this loop.)"""
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
