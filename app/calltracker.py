"""DialHalo connection (one per workspace) and the push of leads to its phone dial queue.

Outreach -> DialHalo:
  * a new "call" task (an employer replied yes, a flow step, or a person added one) queues a push of that lead to
    DialHalo's public API: PUT /api/v1/leads/external/mcn_outreach/<lead id>. Re-pushing updates the same Call
    Tracker lead, never a second one. The owner is the DialHalo member with the task owner's email, otherwise Call
    Tracker's own distribution (round robin etc.) picks one;
  * an opt-out (unsubscribe link, unsubscribe reply, moved to Unsubscribed, do-not-contact, erasure) queues a
    do-not-call entry for the lead's phone;
  * jobs run in a background worker, retry with back-off when DialHalo is down and end as "failed" (listed on the
    Integrations card with a Retry button and noted on the lead), never silently dropped.

Storage (store collections): ct_connections {workspace: connection}, ct_outbox [job], ct_seen [received webhook ids].
The API key and webhook secret are sealed at rest by app/secretbox.py and never returned or logged.
"""
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote, urlparse

from .channels import netguard
from .workspaces import MAIN, ws_of

SOURCE = "mcn_outreach"
SOURCE_LABEL = "MCN Outreach"
BACKOFF_MINUTES = (1, 5, 15, 60, 180, 360, 720)     # before attempts 2..8; then the job is "failed"
MAX_ATTEMPTS = len(BACKOFF_MINUTES) + 1
DONE_KEPT = 200
TIMEOUT = 15
KICK = True                       # tests switch the background worker off and call process_due themselves
TRANSPORT = None                  # tests: fn(method, url, headers, body_bytes) -> (status, dict)
RESOLVER = None                   # tests: fn(host) -> [ip] (no DNS in tests)
_worker = {"running": False}
_worker_lock = threading.Lock()

# What each DialHalo outcome does in Outreach (keys: DialHalo outcome names, lower case). Editable per workspace.
#   outcome        the Outreach call outcome logged on the timeline (app/activities.CALL_OUTCOMES)
#   stage          move the lead forward to this stage ("" = leave it); never backwards, never out of Unsubscribed
#   stop_sequence  stop the lead's email sequence
#   opt_out        suppress the contact (do-not-contact: email never used again)
DEFAULT_MAPPING = {
    "interested": {"outcome": "interested", "stage": "interested", "stop_sequence": False, "opt_out": False},
    "call back later": {"outcome": "call_back", "stage": "", "stop_sequence": False, "opt_out": False},
    "converted": {"outcome": "meeting_booked", "stage": "call_done", "stop_sequence": True, "opt_out": False},
    "not interested": {"outcome": "not_interested", "stage": "disqualified", "stop_sequence": True, "opt_out": False},
    "wrong number": {"outcome": "wrong_number", "stage": "", "stop_sequence": False, "opt_out": False},   # the PHONE is marked invalid; the person and their email are untouched
    "do not call": {"outcome": "not_interested", "stage": "unsubscribed", "stop_sequence": True, "opt_out": True},
}


FOLLOWUP_WINDOW_DAYS = 360          # DialHalo refuses follow-up times more than 366 days from now (external.service.ts)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse(text) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(text or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def norm_phone(raw) -> str:
    """Same rule as DialHalo (lib/logic.ts normalizeNumber): Indian numbers compare on their last 10 digits."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if digits.startswith("00"):
        digits = digits.lstrip("0")
    if 10 < len(digits) <= 12 and (digits.startswith("91") or digits.startswith("0")):
        return digits[-10:]
    return digits[-15:] if len(digits) > 15 else digits


# ---------------------------------------------------------------- connections
def connections(store) -> dict:
    return store.read("ct_connections") or {}


def connection(store, ws: str) -> Optional[dict]:
    conn = connections(store).get(ws or MAIN)
    return conn if isinstance(conn, dict) else None


def active_connection(store, ws: str) -> Optional[dict]:
    conn = connection(store, ws)
    return conn if conn and conn.get("enabled", True) and conn.get("base_url") and conn.get("api_key") else None


def by_id(store, conn_id: str) -> Optional[dict]:
    return next((c for c in connections(store).values() if isinstance(c, dict) and c.get("id") == conn_id), None)


def check_base_url(url: str) -> str:
    """https on a public host, or http(s) to a host:port the server owner allowed in OUTREACH_ALLOW_PRIVATE_HOSTS
    (e.g. 127.0.0.1:8081: DialHalo on this same server). Anything else is refused (no SSRF to other hosts)."""
    text = (url or "").strip().rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter the DialHalo address, for example https://calls.example.com")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Enter only the address (no user name, password, ? or #).")
    host = parsed.hostname.lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if netguard._private_allowed(host, port):
        return text
    if parsed.scheme != "https":
        raise ValueError("The address must start with https:// (an internal http address must be allowed by the server owner).")
    netguard.check_public_url(text, RESOLVER)
    return text


def check_public_url(url: str) -> str:
    """The DialHalo address people open in a browser (links to calls and recordings): https only."""
    text = (url or "").strip().rstrip("/")
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("The web address for links must start with https://")
    return text


def clean_mapping(raw) -> dict:
    from .activities import CALL_OUTCOMES
    from .stages import ALL_STAGES
    out = {}
    for name, rule in (raw or {}).items() if isinstance(raw, dict) else []:
        key = " ".join(str(name or "").lower().split())[:60]
        if not key or not isinstance(rule, dict):
            continue
        outcome = rule.get("outcome") if rule.get("outcome") in CALL_OUTCOMES else ""
        stage = rule.get("stage") if rule.get("stage") in ALL_STAGES else ""
        out[key] = {"outcome": outcome, "stage": stage, "stop_sequence": bool(rule.get("stop_sequence")), "opt_out": bool(rule.get("opt_out"))}
        if len(out) >= 40:
            break
    return out


def mapping_of(conn: Optional[dict]) -> dict:
    saved = (conn or {}).get("mapping")
    return saved if isinstance(saved, dict) and saved else DEFAULT_MAPPING


def save_connection(store, ws: str, changes: dict, who: str) -> dict:
    """Create or change the workspace's connection. Secrets are kept when not sent again."""
    stamp = iso(now_utc())

    def apply(rows: dict) -> dict:
        cur = dict(rows.get(ws) or {})
        if not cur:
            cur = {"id": uuid.uuid4().hex[:20], "workspace": ws, "created_at": stamp, "created_by": who, "enabled": True}
        for key in ("base_url", "public_url", "api_key", "webhook_secret", "enabled", "mapping"):
            if key in changes and changes[key] is not None:
                cur[key] = changes[key]
        cur.update(updated_at=stamp, updated_by=who)
        return {**rows, ws: cur}
    return store.update("ct_connections", apply)[ws]


def delete_connection(store, ws: str) -> bool:
    found = {}

    def apply(rows: dict) -> dict:
        found["yes"] = ws in rows
        return {k: v for k, v in rows.items() if k != ws}
    store.update("ct_connections", apply)
    store.update("ct_outbox", lambda jobs: [j for j in jobs if j.get("ws") != ws or j.get("status") == "done"])
    return bool(found.get("yes"))


def note_status(store, ws: str, key: str, ok: bool, detail: str = "") -> None:
    """last_push / last_event / last_test on the card (no secrets: messages come from our own wording)."""
    row = {"at": iso(now_utc()), "ok": bool(ok), "detail": str(detail or "")[:300]}
    store.update("ct_connections", lambda rows: {**rows, ws: {**rows[ws], key: row}} if ws in rows else rows)


def note_unmapped(store, ws: str, name: str, counted_as: str) -> None:
    """A Call Tracker outcome name with no row in the mapping was counted as `counted_as`: remember it so the card can say so."""
    key = " ".join(name.lower().split())[:80]
    stamp = iso(now_utc())

    def apply(rows: dict) -> dict:
        conn = rows.get(ws)
        if not conn:
            return rows
        seen = dict(conn.get("unmapped_outcomes") or {})
        cur = seen.get(key) or {"name": name[:80], "count": 0}
        seen[key] = {**cur, "count": cur["count"] + 1, "last_at": stamp, "counted_as": counted_as}
        if len(seen) > 30:                     # keep the 30 most recent names
            seen = dict(sorted(seen.items(), key=lambda kv: kv[1].get("last_at", ""))[-30:])
        return {**rows, ws: {**conn, "unmapped_outcomes": seen}}
    store.update("ct_connections", apply)


def public_view(store, ws: str) -> Optional[dict]:
    conn = connection(store, ws)
    if not conn:
        return None
    key = str(conn.get("api_key") or "")
    return {"id": conn["id"], "workspace": ws, "base_url": conn.get("base_url", ""), "public_url": conn.get("public_url", ""),
            "enabled": conn.get("enabled", True), "api_key_set": bool(key), "api_key_hint": ("••••" + key[-4:]) if len(key) > 8 else ("set" if key else ""),
            "webhook_secret_set": bool(conn.get("webhook_secret")), "mapping": mapping_of(conn), "mapping_custom": bool(conn.get("mapping")),
            "last_push": conn.get("last_push"), "last_event": conn.get("last_event"), "last_test": conn.get("last_test"),
            "unmapped_outcomes": sorted((conn.get("unmapped_outcomes") or {}).values(), key=lambda r: -r.get("count", 0)),
            "created_at": conn.get("created_at"), "updated_at": conn.get("updated_at")}


# ---------------------------------------------------------------- HTTP client
class CallTrackerError(OSError):
    """DialHalo could not be reached or refused; the message is safe to show (no key, no body echo)."""

    def __init__(self, message: str, status: int = 0, retry: bool = True):
        super().__init__(message)
        self.status = status
        self.retry = retry


def ct_request(conn: dict, method: str, path: str, body: Optional[dict] = None) -> dict:
    url = conn["base_url"].rstrip("/") + path
    headers = {"Authorization": f"Bearer {conn['api_key']}", "Accept": "application/json", "User-Agent": "MCN-Outreach/1.0"}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        if TRANSPORT is not None:
            status, payload = TRANSPORT(method, url, headers, data)
        else:
            check_base_url(conn["base_url"])
            resp = netguard.guarded_request(url, method, headers, data, timeout=TIMEOUT, allow_http=True)
            status = resp.status
            try:
                payload = json.loads(resp.body.decode("utf-8") or "{}")
            except ValueError:
                payload = {}
    except (netguard.FetchFailed, OSError, ValueError) as exc:
        raise CallTrackerError(f"DialHalo could not be reached ({type(exc).__name__}).") from None
    if status in (401, 403):
        msg = str((payload or {}).get("message") or "")[:200]
        raise CallTrackerError(f"DialHalo refused the API key (HTTP {status}){': ' + msg if msg else ''}.", status, retry=False)
    if status == 404:
        raise CallTrackerError("Not found in DialHalo (HTTP 404).", status, retry=False)
    if status >= 500 or status == 429:
        raise CallTrackerError(f"DialHalo is busy or down (HTTP {status}).", status)
    if status >= 300:
        msg = str((payload or {}).get("message") or "")[:200]
        raise CallTrackerError(f"DialHalo did not accept it (HTTP {status}){': ' + msg if msg else ''}.", status, retry=False)
    return (payload or {}).get("data", payload or {})


# ---------------------------------------------------------------- owners
def email_of_person(store, name_or_email) -> str:
    """Outreach task / lead owners are names or emails: the email of that person, or ""."""
    from .auth import users
    text = str(name_or_email or "").strip()
    if not text:
        return ""
    if "@" in text:
        return text.lower()
    for u in users(store):
        if (u.get("name") or "").strip().lower() == text.lower():
            return u["email"].lower()
    for m in store.read("settings").get("team") or []:
        if isinstance(m, dict) and (m.get("name") or "").strip().lower() == text.lower() and m.get("email"):
            return str(m["email"]).lower()
    return ""


def owner_preview(store, ws: str, members: list, all_members: Optional[list] = None) -> list:
    """Outreach people of this workspace and the DialHalo member each maps to (by email).
    Unmatched people carry a `reason` (no member with that email, or the member is switched off in DialHalo)."""
    from .auth import users
    by_email = {str(m.get("email") or "").lower(): m for m in members if m.get("email")}
    off = {str(m.get("email") or "").lower() for m in (all_members or []) if m.get("email") and not m.get("active", 1)}
    people = [u for u in users(store) if u.get("role") == "owner" or not u.get("workspaces") or ws in (u.get("workspaces") or [])]
    out = []
    for u in people[:200]:
        email = str(u["email"]).lower()
        m = by_email.get(email)
        row = {"name": u.get("name") or u["email"], "email": u["email"], "calltracker": (m or {}).get("name"), "matched": bool(m)}
        if not m:
            row["reason"] = ("this member is switched off in DialHalo" if email in off
                             else "no DialHalo member has this email")
        out.append(row)
    return out


# ---------------------------------------------------------------- queue
def _enqueue(store, job: dict, merge_key=None) -> dict:
    stamp = iso(now_utc())
    saved = {}

    def apply(jobs: list) -> list:
        for j in jobs:
            if merge_key and j.get("status") == "pending" and merge_key(j):
                j.update({k: v for k, v in job.items() if k not in ("id", "created_at", "attempts")}, next_at=stamp, updated_at=stamp)
                saved["job"] = j
                return jobs
        row = {"id": uuid.uuid4().hex[:12], "attempts": 0, "status": "pending", "next_at": stamp, "created_at": stamp, "updated_at": stamp, **job}
        saved["job"] = row
        done = [j for j in jobs if j.get("status") == "done"]
        keep = {j["id"] for j in done[-DONE_KEPT:]}
        return [*[j for j in jobs if j.get("status") != "done" or j["id"] in keep], row]
    store.update("ct_outbox", apply)
    kick(store)
    return saved["job"]


def opted_out(lead: dict) -> str:
    """Why this lead must never be sent to Call Tracker to be dialled ('' when it may be)."""
    if lead.get("stage") == "unsubscribed":
        return "unsubscribed"
    if lead.get("do_not_contact"):
        return "on the do-not-contact list"
    if lead.get("consent_withdrawn_at"):
        return "consent withdrawn"
    return ""


def on_call_task(store, task: dict) -> Optional[dict]:
    """A new call task: push its lead to the workspace's DialHalo (if connected). Never for a lead who opted out."""
    lead = next((l for l in store.read("leads") if l.get("id") == task.get("lead_id")), None)
    if not lead or not active_connection(store, ws_of(lead)) or opted_out(lead):
        return None
    ws = ws_of(lead)
    return _enqueue(store, {"kind": "push", "ws": ws, "lead_id": lead["id"], "task_id": task.get("id")},
                    merge_key=lambda j: j.get("kind") == "push" and j.get("lead_id") == lead["id"])


FROM_CALLTRACKER = threading.local()     # set while applying a DialHalo event, so its opt-outs are not echoed back


def on_opt_out(store, lead_id: str, reason: str) -> Optional[dict]:
    """The contact opted out in Outreach: put the phone on the workspace's DialHalo do-not-call list."""
    if getattr(FROM_CALLTRACKER, "on", False):
        return None
    lead = next((l for l in store.read("leads") if l.get("id") == lead_id), None)
    if not lead or not active_connection(store, ws_of(lead)):
        return None
    phones = [p for p in [lead.get("phone"), *(lead.get("phones") or [])] if norm_phone(p)]
    if not phones:
        return None
    job = None
    for phone in dict.fromkeys(norm_phone(p) for p in phones):
        job = _enqueue(store, {"kind": "dnc", "ws": ws_of(lead), "lead_id": lead_id, "phone": phone, "reason": str(reason or "Opted out")[:200]},
                       merge_key=lambda j, phone=phone: j.get("kind") == "dnc" and j.get("phone") == phone and j.get("ws") == ws_of(lead))
    return job


def push_payload(store, lead: dict, base_url: str) -> dict:
    """What DialHalo gets for a lead: contact, the call task's text as the note, its due time, owner, link back."""
    from .activities import _normalise_task
    tasks = [_normalise_task(t) for t in store.read("tasks") if t.get("lead_id") == lead["id"] and t.get("kind") == "call"]
    open_tasks = [t for t in tasks if t["status"] == "open"]
    task = sorted(open_tasks or tasks, key=lambda t: t.get("created_at") or "")[-1] if (open_tasks or tasks) else {}
    phone = lead.get("phone") or next((p for p in lead.get("phones") or [] if p), "")
    note = " — ".join(x for x in [task.get("title"), task.get("text")] if x)[:1000]
    due = parse(task.get("due_at"))
    owner = email_of_person(store, task.get("owner")) or email_of_person(store, lead.get("owner"))
    from .growth_core import public_base_url
    body = {"name": (lead.get("contact_name") or lead.get("company") or "")[:120], "company": (lead.get("company") or "")[:120],
            "phone": str(phone or "")[:40], "email": (lead.get("email") or "")[:190], "note": note, "sourceLabel": SOURCE_LABEL,
            "url": f"{public_base_url(store, base_url)}/#leads?lead={quote(lead['id'])}", "city": (lead.get("city") or "")[:80]}
    if due:
        # DialHalo accepts follow-up times within a year of today (past or future); anything further out would be refused with
        # a 400 and the whole push would give up. Send such a lead without a follow-up time and keep the date in the note.
        if abs((due - now_utc()).total_seconds()) <= FOLLOWUP_WINDOW_DAYS * 86400:
            body["followupAtMs"] = int(due.timestamp() * 1000)
        else:
            body["note"] = (f"{note} (follow up on {due.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime('%d %b %Y')})")[:1000]
    if owner:
        body["ownerEmail"] = owner
    return body


def _lead_note(store, lead_id: str, state: dict, event: Optional[str]) -> None:
    def change(lead: dict) -> dict:
        timeline = lead["timeline"] + ([{"at": iso(now_utc()), "kind": "calltracker", "text": event}] if event else [])
        return {**lead, "calltracker": {**(lead.get("calltracker") or {}), **state}, "timeline": timeline}
    store.update_lead(lead_id, change)


def _run_job(store, job: dict) -> str:
    """Does one job. Returns a short result text; raises CallTrackerError on failure."""
    conn = active_connection(store, job["ws"])
    if not conn:
        raise CallTrackerError("The workspace's DialHalo connection is missing or switched off.", retry=False)
    lead = next((l for l in store.read("leads") if l.get("id") == job.get("lead_id")), None)
    if job["kind"] == "dnc":
        ct_request(conn, "POST", "/api/v1/dnc", {"phone": job["phone"], "reason": job.get("reason") or "Opted out in MCN Outreach", "source": SOURCE})
        if lead:
            _lead_note(store, lead["id"], {"dnc": True, "dnc_at": iso(now_utc())}, "DialHalo: number put on the do-not-call list")
        return "do-not-call sent"
    if not lead:
        raise CallTrackerError("The lead no longer exists.", retry=False)
    if ws_of(lead) != job["ws"]:
        raise CallTrackerError("The lead moved to another workspace.", retry=False)
    if why := opted_out(lead):                 # opted out after the push was queued: never send them to be dialled
        raise CallTrackerError(f"Not sent: this lead is {why}.", retry=False)
    body = push_payload(store, lead, os.environ.get("OUTREACH_BASE_URL", "http://localhost:8000"))
    if not norm_phone(body["phone"]):
        raise CallTrackerError("The lead has no phone number, so DialHalo cannot dial it. Add a phone and retry.", retry=False)
    data = ct_request(conn, "PUT", f"/api/v1/leads/external/{SOURCE}/{quote(lead['id'], safe='')}", body)
    ct_lead = data.get("lead") or {}
    owner = data.get("owner") or {}
    first = not (lead.get("calltracker") or {}).get("lead_id")
    who = owner.get("name") or "DialHalo's distribution"
    _lead_note(store, lead["id"], {"lead_id": ct_lead.get("id"), "status": "ok", "error": "", "pushed_at": iso(now_utc()), "owner": owner.get("name") or "",
                                   "owner_matched": bool(data.get("ownerMatched")), "dnc": bool(data.get("dnc"))},
               f"Sent to DialHalo: in {who}'s dial queue" if first else None)
    return f"lead {'created' if data.get('created') else 'updated'} ({who})"


def process_due(store, now: Optional[datetime] = None, limit: int = 25) -> int:
    """Run due jobs; returns how many ran. Failures back off; after the last attempt the job is 'failed'."""
    now = now or now_utc()
    due = [j for j in store.read("ct_outbox") if j.get("status") == "pending" and (parse(j.get("next_at")) or now) <= now][:limit]
    for job in due:
        result, error, retry = "", "", False
        try:
            result = _run_job(store, job)
        except CallTrackerError as exc:
            error, retry = str(exc), exc.retry
        except Exception as exc:                  # a bug must not stop the queue; the job stays visible
            error, retry = f"Unexpected error ({type(exc).__name__}).", True
        attempts = job.get("attempts", 0) + 1
        stamp = iso(now_utc())
        if not error:
            change = {"status": "done", "attempts": attempts, "error": "", "done_at": stamp, "result": result}
        elif retry and attempts < MAX_ATTEMPTS:
            change = {"attempts": attempts, "error": error, "next_at": iso(now + timedelta(minutes=BACKOFF_MINUTES[attempts - 1]))}
        else:
            change = {"status": "failed", "attempts": attempts, "error": error, "failed_at": stamp}
        store.update("ct_outbox", lambda jobs, jid=job["id"], change=change: [{**j, **change, "updated_at": stamp} if j["id"] == jid else j for j in jobs])
        if job["ws"] in connections(store):
            note_status(store, job["ws"], "last_push", not error, result or error)
        if error and job.get("lead_id") and (attempts == 1 or change.get("status") == "failed"):
            final = change.get("status") == "failed"
            text = (f"DialHalo: could not send this lead ({error})" + (" Gave up; use Retry on Integrations → DialHalo." if final
                    else f" Retrying automatically ({MAX_ATTEMPTS - attempts} tries left)."))
            _lead_note(store, job["lead_id"], {"status": "failed" if final else "retrying", "error": error}, text)
    return len(due)


def retry_failed(store, ws: str) -> int:
    stamp = iso(now_utc())
    n = {"n": 0}

    def apply(jobs):
        out = []
        for j in jobs:
            if j.get("ws") == ws and j.get("status") == "failed":
                j = {**j, "status": "pending", "attempts": 0, "next_at": stamp, "updated_at": stamp}
                n["n"] += 1
            out.append(j)
        return out
    store.update("ct_outbox", apply)
    if n["n"]:
        kick(store)
    return n["n"]


def queue_summary(store, ws: str) -> dict:
    jobs = [j for j in store.read("ct_outbox") if j.get("ws") == ws]
    failed = [j for j in jobs if j.get("status") == "failed"]
    pending = [j for j in jobs if j.get("status") == "pending"]
    leads = {l["id"]: l for l in store.read("leads") if l.get("id") in {j.get("lead_id") for j in failed + pending}}
    row = lambda j: {"id": j["id"], "kind": j["kind"], "lead_id": j.get("lead_id"), "attempts": j.get("attempts", 0), "error": j.get("error", ""),
                     "next_at": j.get("next_at"), "company": (leads.get(j.get("lead_id")) or {}).get("company") or ""}
    return {"pending": len(pending), "failed": len(failed), "done": len([j for j in jobs if j.get("status") == "done"]),
            "failed_rows": [row(j) for j in failed[-20:]], "pending_rows": [row(j) for j in pending[:20]]}


def kick(store) -> None:
    """Start the background worker (one at a time). It keeps going while jobs are pending, sleeping until the next one
    is due (at most a minute), so retries happen without OUTREACH_AUTORUN."""
    if not KICK:
        return
    with _worker_lock:
        if _worker["running"]:
            return
        _worker["running"] = True

    def loop():
        try:
            while True:
                process_due(store)
                with _worker_lock:            # checked under the lock kick() uses, so a job queued right now is never stranded
                    pending = [parse(j.get("next_at")) for j in store.read("ct_outbox") if j.get("status") == "pending"]
                    if not pending:
                        _worker["running"] = False
                        return
                wait = min(60.0, max(1.0, (min(p for p in pending if p) - now_utc()).total_seconds())) if any(pending) else 1.0
                time.sleep(wait)
        except Exception as exc:                  # never kill the app; the next kick restarts it
            print(f"calltracker worker stopped: {type(exc).__name__}")
            with _worker_lock:
                _worker["running"] = False
    threading.Thread(target=loop, daemon=True, name="calltracker-push").start()
