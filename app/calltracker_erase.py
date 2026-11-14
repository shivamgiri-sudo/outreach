"""Erasing a person in Outreach also erases them in Call Tracker (DialHalo) and blocks their number there.

When a person is erased (routes_compliance) or permanently deleted (bulk delete, workspace/account delete) and the
workspace has an active Call Tracker connection, a job {"kind": "erase"} is queued in ct_outbox. The job runs after the
lead is gone, so it carries only what it needs: the lead id, the phone numbers and a fixed reason. Never a name or email.
  1. DELETE /api/v1/leads/external/mcn_outreach/<lead id>   Call Tracker erases the person and blocks their number
  2. POST /api/v1/dnc for each phone                         belt and braces, in case Call Tracker never held the lead
Retries and back-off are the normal queue's (app/calltracker.py). Phone numbers are removed from the job record the moment
it is done or has failed for good, so the outbox does not keep personal data about someone who asked to be forgotten.
After a final failure the job stays on the card as failed (with Retry; a retry removes the lead by its id, and Call Tracker
blocks the number it holds for it) and the workspace's admins get a notification.
Jobs of the same person are never queued twice. Leads that were never sent to Call Tracker are skipped by the delete paths
(`only_known=True`), so deleting a test lead does not put its number on a do-not-call list; an explicit erasure never skips.
"""
from typing import Optional

from .workspaces import ws_of

REASON = "Erased at the source"
KINDS_TO_DROP = ("push", "dnc")        # waiting jobs about the same person: moot, and they hold phone numbers


def _calltracker():
    from . import calltracker
    return calltracker


def phones_of(lead: dict) -> list:
    ct = _calltracker()
    raw = [lead.get("phone"), *(lead.get("phones") or []), *(c.get("phone") for c in lead.get("other_contacts") or [] if isinstance(c, dict))]
    return list(dict.fromkeys(p for p in (ct.norm_phone(x) for x in raw) if p))


def known_to_calltracker(store, lead: dict) -> bool:
    return bool((lead.get("calltracker") or {}).get("lead_id")
                or any(j.get("lead_id") == lead.get("id") and j.get("kind") in ("push", "dnc") and j.get("status") != "failed"
                       for j in store.read("ct_outbox")))


def _scrub_old_jobs(store, ws: str, lead_id: str) -> None:
    """Drop the person's waiting push / do-not-call jobs and wipe the phone from their finished or failed ones."""
    def apply(jobs: list) -> list:
        out = []
        for j in jobs:
            if j.get("ws") == ws and j.get("lead_id") == lead_id and j.get("kind") != "erase":
                if j.get("status") == "pending" and j.get("kind") in KINDS_TO_DROP:
                    continue
                j = {k: v for k, v in j.items() if k != "phone"}
            out.append(j)
        return out
    store.update("ct_outbox", apply)


def on_erase(store, lead: dict, only_known: bool = False) -> Optional[dict]:
    """Queue the removal of this person from the lead's workspace Call Tracker. Call it with the lead as it was BEFORE
    it was removed. Returns the job, or None when nothing is needed (no active connection, never sent, no id)."""
    ct = _calltracker()
    ws, lead_id = ws_of(lead), lead.get("id")
    if not lead_id or not ct.active_connection(store, ws):
        return None
    if only_known and not known_to_calltracker(store, lead):
        return None
    _scrub_old_jobs(store, ws, lead_id)
    for j in store.read("ct_outbox"):
        if j.get("kind") == "erase" and j.get("ws") == ws and j.get("lead_id") == lead_id:
            return j                                  # erasing twice queues one job
    return ct._enqueue(store, {"kind": "erase", "ws": ws, "lead_id": lead_id, "phones": phones_of(lead), "reason": REASON})


def run(store, conn: dict, job: dict) -> str:
    """The queue's step for one erase job. Both calls are repeatable; a failure of one never skips the other."""
    ct = _calltracker()
    errors, retry, results = [], False, []

    def attempt(label: str, method: str, path: str, body: Optional[dict] = None) -> None:
        nonlocal retry
        try:
            ct.ct_request(conn, method, path, body)
            results.append(label)
        except ct.CallTrackerError as exc:
            errors.append(f"{label}: " + ("this Call Tracker is too old to erase people, update it (HTTP 404)." if exc.status == 404 and method == "DELETE" else str(exc)))
            retry = retry or exc.retry

    from urllib.parse import quote
    attempt("erase", "DELETE", f"/api/v1/leads/external/{ct.SOURCE}/{quote(str(job['lead_id']), safe='')}")
    for phone in job.get("phones") or []:
        attempt("do-not-call", "POST", "/api/v1/dnc", {"phone": phone, "reason": job.get("reason") or REASON, "source": ct.SOURCE})
    if errors:
        raise ct.CallTrackerError(" ".join(errors)[:300], retry=retry)
    return f"erased in Call Tracker, {len(job.get('phones') or [])} number(s) blocked"


def settle(store, job: dict, change: dict) -> dict:
    """Called by the queue with the outcome of an erase attempt: wipe the phones once it is done or failed for good, and
    tell the workspace's admins when it failed for good."""
    if change.get("status") not in ("done", "failed"):
        return change
    out = {**change, "phones": [], "phones_cleared": True}
    if change["status"] == "failed":
        notify_failed(store, job, change.get("error", ""))
    return out


def notify_failed(store, job: dict, error: str) -> None:
    """A bell entry (and an email when mail is on) for the managers: an erased person may still be in Call Tracker."""
    try:
        from . import account_alerts, accounts
        from .alerts import SENDER
        from .reminders import _log_transactional, notify
        aid = accounts.account_of_ws(store, job["ws"])
        stamp = aid if aid != accounts.MCN else ""
        title = "Erased person not removed from Call Tracker"
        text = (f"Lead {job.get('lead_id')} was erased in Outreach, but Call Tracker could not erase them ({error}) "
                "Fix the connection, then use Retry on Integrations → Call Tracker. Until then the person may still be dialled there.")
        names = [n for n in dict.fromkeys([*account_alerts.managers(store, aid), account_alerts.default_person(store, aid)]) if n] or [None]
        for name in names:
            notify(store, name, title, text, kind="alert:calltracker_erase_failed", account_id=stamp)
            email = account_alerts.address_of(store, name, aid) if name else ""
            if SENDER and email and account_alerts.email_on(store, aid):
                try:
                    result = SENDER({"to": email, "subject": f"[Outreach] {title}", "body": text, "kind": "internal"})
                except Exception as exc:
                    result = {"status": "failed", "error": str(exc)[:200]}
                _log_transactional(store, email, f"[Outreach] {title}", text, "internal", None, result)
    except Exception as exc:                          # the failed job stays visible on the card even if the bell cannot be written
        print(f"calltracker erase alert not sent: {type(exc).__name__}")


def erase_leads_now(store, leads: list) -> int:
    """For deletes that also remove the workspace's connection (workspace or account delete): queue the erase jobs and
    run them once, right now. Returns how many could not be finished (their admins are notified)."""
    ct = _calltracker()
    queued = [j for j in (on_erase(store, l, only_known=True) for l in leads) if j]
    if not queued:
        return 0
    ct.process_due(store, limit=max(25, len(queued) * 3))
    ids = {j["id"] for j in queued}
    open_jobs = [j for j in store.read("ct_outbox") if j.get("id") in ids and j.get("status") != "done"]
    for j in open_jobs:
        if j.get("status") != "failed":                # still retrying, but its connection is about to go
            notify_failed(store, j, j.get("error") or "Call Tracker did not answer.")
    return len(open_jobs)
