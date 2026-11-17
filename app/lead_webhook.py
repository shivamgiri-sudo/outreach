"""Lead webhook: push new and changed leads to another system the moment they happen (Zapier, Make, a CRM, a Google Sheet).

One setting per workspace (owner only), kept in `lead_hooks` {workspace: hook}:
  url      where to POST (https, public internet only: private / internal addresses are refused, here and at send time)
  secret   optional; every POST then carries X-Outreach-Signature: sha256=<HMAC-SHA256 of the raw body, hex>
  mode     "webhook" (JSON event) or "sheets" (a Google Apps Script web app that appends one row per event)
  events   lead.created, lead.updated (only when one of `fields` changes), harvest.lead_found
  enabled  on / off (no url = off)
Deliveries go through a retry queue (app/retry_queue.py, `lead_hook_outbox`) with back-off; the last 50 per workspace are
in `lead_hook_log`. URL and secret are sealed at rest (app/secretbox.py) and left out of backups without secrets.
"""
import hashlib
import hmac
import json
import re
import uuid
from urllib.parse import urlparse

from .channels import http as channel_http
from .channels.netguard import check_public_url
from .http_util import HttpStatusError
from .retry_queue import JobError, RetryQueue, iso, now_utc

HOOKS, OUTBOX, LOG = "lead_hooks", "lead_hook_outbox", "lead_hook_log"
EVENTS = ("lead.created", "lead.updated", "harvest.lead_found")
EVENT_TEXT = {"lead.created": "A new lead is added (any source: import, form, harvest, by hand)",
              "lead.updated": "A lead changes (only the fields you pick)",
              "harvest.lead_found": "The lead harvest finds a business (new or already in Leads)"}
WATCHABLE = ("stage", "owner", "email", "phone", "contact_name", "title", "company", "website", "tags", "dnc", "do_not_contact",
             "city", "rating")
DEFAULT_FIELDS = ("stage", "owner", "email", "phone", "dnc")
LEAD_FIELDS = ("id", "company", "contact_name", "title", "email", "phone", "phone_type", "phone_line_type", "website", "domain",
               "linkedin_url", "address", "city", "state", "pin", "country", "sector", "category", "rating", "reviews_count",
               "maps_url", "source", "stage", "owner", "tags", "dnc", "created_at")
SHEET_COLUMNS = ("event", "occurred_at", "lead_id", "company", "contact_name", "title", "email", "phone", "phone_line_type", "website",
                 "city", "state", "pin", "category", "rating", "reviews_count", "source", "stage", "owner", "changed")
SHEETS_HOSTS = ("script.google.com", "script.googleusercontent.com")
LOG_KEPT = 50
TIMEOUT = 10
USER_AGENT = "OutreachOS-LeadWebhook/1"
RESOLVER = None                     # tests put a fake DNS here
TRANSPORT = None                    # tests put a fake (url, headers, raw) -> response here; None = channels/http.request


class HookError(ValueError):
    """A setting that can't be saved; the message says why in plain words."""


# ---------------------------------------------------------------- settings
def hooks(store) -> dict:
    return store.read(HOOKS) or {}


def hook_of(store, ws: str):
    return hooks(store).get(ws)


def active(hook) -> bool:
    return bool(hook and hook.get("enabled") and hook.get("url"))


def check_url(url: str, mode: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    if len(text) > 2000:
        raise HookError("The address is too long.")
    parsed = urlparse(text)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise HookError("The address must start with https:// (no user name or password in it).")
    if mode == "sheets" and (parsed.hostname not in SHEETS_HOSTS or not parsed.path.endswith("/exec")):
        raise HookError("For Google Sheets, paste the web app address from Apps Script: https://script.google.com/macros/s/…/exec")
    try:
        return check_public_url(text, RESOLVER)
    except ValueError as exc:
        raise HookError(str(exc)) from None


def save(store, ws: str, changes: dict, who: str) -> dict:
    """Merge changes into the workspace's hook. An empty secret string clears it; None leaves it as it is."""
    current = dict(hook_of(store, ws) or {"id": uuid.uuid4().hex[:12], "mode": "webhook", "events": list(EVENTS),
                                           "fields": list(DEFAULT_FIELDS), "enabled": False, "secret": ""})
    mode = changes.get("mode") or current.get("mode") or "webhook"
    if mode not in ("webhook", "sheets"):
        raise HookError("Choose webhook or Google Sheets.")
    current["mode"] = mode
    if changes.get("url") is not None or "mode" in changes:
        current["url"] = check_url(changes["url"] if changes.get("url") is not None else current.get("url", ""), mode)
    if changes.get("secret") is not None:
        secret = str(changes["secret"]).strip()
        if secret and len(secret) < 12:
            raise HookError("Use a secret of at least 12 characters (or leave it empty).")
        current["secret"] = secret[:200]
    if changes.get("events") is not None:
        current["events"] = [e for e in EVENTS if e in (changes["events"] or [])]
    if changes.get("fields") is not None:
        current["fields"] = [f for f in WATCHABLE if f in (changes["fields"] or [])]
    if changes.get("enabled") is not None:
        current["enabled"] = bool(changes["enabled"])
    if current["enabled"] and not current.get("url"):
        raise HookError("Add the address first, then switch it on.")
    current.update(updated_at=iso(now_utc()), updated_by=who)
    store.update(HOOKS, lambda rows: {**(rows or {}), ws: current})
    return current


def remove(store, ws: str) -> bool:
    found = ws in hooks(store)
    store.update(HOOKS, lambda rows: {k: v for k, v in (rows or {}).items() if k != ws})
    store.update(OUTBOX, lambda rows: [r for r in rows if r.get("ws") != ws or r.get("status") != "pending"])
    return found


def public_view(store, ws: str) -> dict:
    hook = hook_of(store, ws) or {}
    host = urlparse(hook.get("url") or "").hostname or ""
    return {"configured": bool(hook.get("url")), "enabled": bool(hook.get("enabled")), "mode": hook.get("mode", "webhook"),
            "url": hook.get("url", ""), "host": host, "secret_set": bool(hook.get("secret")),
            "events": hook.get("events", list(EVENTS)), "fields": hook.get("fields", list(DEFAULT_FIELDS)),
            "updated_at": hook.get("updated_at"), "updated_by": hook.get("updated_by", "")}


# ---------------------------------------------------------------- payloads
def lead_view(lead: dict) -> dict:
    return {k: lead.get(k) for k in LEAD_FIELDS if lead.get(k) not in (None, "", [])} | {"workspace": lead.get("workspace") or "main"}


def payload(event: str, lead: dict, changed=None, harvest=None, delivery: str = "", at: str = "") -> dict:
    body = {"event": event, "delivery_id": delivery or uuid.uuid4().hex[:16], "occurred_at": at or iso(now_utc()),
            "workspace": lead.get("workspace") or "main", "lead": lead_view(lead)}
    if changed is not None:
        body["changed"] = changed
    if harvest is not None:
        body["harvest"] = harvest
    return body


def sheet_body(body: dict) -> dict:
    """What the Apps Script receives in Sheets mode: the header and one row, in the same column order every time."""
    lead = body.get("lead") or {}
    values = {"event": body["event"], "occurred_at": body["occurred_at"], "lead_id": lead.get("id"),
              "changed": ", ".join(body.get("changed") or []), **lead}
    row = []
    for c in SHEET_COLUMNS:
        v = values.get(c)
        v = "" if v is None else "; ".join(map(str, v)) if isinstance(v, list) else v
        row.append("'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v)     # never a formula in the sheet
    return {"columns": list(SHEET_COLUMNS), "row": row, "event": body["event"]}


def sign(secret: str, raw: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


def request_for(hook: dict, body: dict) -> tuple:
    """(raw bytes, headers) for one delivery."""
    data = sheet_body(body) if hook.get("mode") == "sheets" else body
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, "X-Outreach-Event": body["event"],
               "X-Outreach-Delivery": body["delivery_id"]}
    if hook.get("secret"):
        headers["X-Outreach-Signature"] = sign(hook["secret"], raw)
    return raw, headers


# ---------------------------------------------------------------- delivery
def send(hook: dict, body: dict) -> str:
    """POST one event. Returns a short result; raises JobError (retry for network / 5xx / 429, not for other refusals)."""
    raw, headers = request_for(hook, body)
    try:
        if TRANSPORT is not None:
            reply = TRANSPORT(hook["url"], headers, raw)
        else:
            reply = channel_http.request(hook["url"], headers, raw=raw, timeout=TIMEOUT, resolver=RESOLVER)
        return f"HTTP {reply.get('_status', 200)}"
    except HttpStatusError as exc:
        code = getattr(exc, "code", 0)
        if hook.get("mode") == "sheets" and code in (302, 303):
            return f"HTTP {code} (Apps Script ran)"                # Apps Script answers a POST with a redirect to its reply
        raise JobError(f"HTTP {code}", retry=code >= 500 or code in (408, 429)) from None
    except PermissionError as exc:
        raise JobError(f"Refused by the receiver ({exc})", retry=False) from None
    except OSError as exc:
        text = str(exc) or "Could not connect"
        raise JobError(text, retry="private" not in text.lower() and "internal" not in text.lower()) from None


def _run(store, job: dict) -> str:
    hook = hook_of(store, job["ws"])
    if not active(hook):
        raise JobError("The lead webhook is off or removed.", retry=False)
    return send(hook, job["body"])


def _note(store, job: dict) -> None:
    status = job.get("status", "pending")
    entry = {"at": iso(now_utc()), "event": job["body"]["event"], "lead_id": (job["body"].get("lead") or {}).get("id"),
             "company": (job["body"].get("lead") or {}).get("company", ""), "status": "retrying" if status == "pending" else status,
             "attempts": job.get("attempts", 0), "result": job.get("result") or job.get("error") or "", "ws": job["ws"]}
    log(store, entry)


def log(store, entry: dict) -> None:
    def apply(rows: list) -> list:
        rows = [*rows, entry]
        mine = [i for i, r in enumerate(rows) if r.get("ws") == entry["ws"]]
        drop = set(mine[:-LOG_KEPT])
        return [r for i, r in enumerate(rows) if i not in drop]
    store.update(LOG, apply)


QUEUE = RetryQueue(OUTBOX, _run, "lead-webhook", after=_note)


def queue_events(store, events: list) -> int:
    """events: [(ws, body)]; only for workspaces whose hook is on and asks for that event."""
    table = hooks(store)
    jobs = [{"ws": ws, "body": body} for ws, body in events if active(table.get(ws)) and body["event"] in table[ws].get("events", [])]
    return QUEUE.enqueue(store, jobs)


def delivery_log(store, ws: str) -> list:
    return [r for r in reversed(store.read(LOG)) if r.get("ws") == ws][:LOG_KEPT]


def queue_summary(store, ws: str) -> dict:
    jobs = [j for j in store.read(OUTBOX) if j.get("ws") == ws]
    return {k: sum(1 for j in jobs if j.get("status") == k) for k in ("pending", "done", "failed")}


def sample_lead(ws: str) -> dict:
    return {"id": "test-lead", "company": "Example Traders", "contact_name": "Asha Rao", "email": "asha@example.com",
            "phone": "+919800000000", "phone_line_type": "mobile", "city": "Pune", "source": "test", "stage": "new",
            "workspace": ws, "created_at": iso(now_utc())}


def clean_ws(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", raw or "")[:40]
