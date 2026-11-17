"""Outreach match for DialHalo: for every number a member dialled, tell DialHalo whether it is one of our leads.

DialHalo's dashboard ("Outreach match" page) then shows matched calls next to numbers that are not in Outreach.

  * The webhook receiver (app/calltracker_events.py) matches each call it is told about; the result is queued here.
  * Results go out as ONE coalescing ct_outbox job of kind "match" per DialHalo link (the link the call came from, never
    another DialHalo workspace): a dict number -> result. New results
    merge into the pending job (latest result for a number wins), at most 500 numbers per job; more start a new job.
    The queue's retries / back-off apply. A failure shows on the Integrations card only: no lead notes, no alerts.
  * Sent as PUT /api/v1/external-matches/mcn_outreach {"matches": [{number, status, externalId, label}]}; label is only
    the contact name, company, stage label and a link back to the lead. Nothing else (no email, no notes).
  * Numbers DialHalo reported that are not in Outreach are kept in a small cache (ct_unmatched {ws: {norm: row with the
    ids of the links that reported it}}, at most
    5,000 per workspace, oldest dropped). The calltracker worker re-checks the cache every few minutes against the
    workspace's leads (one pass over the leads, no copy), so a lead created, imported or edited later with that phone is
    sent as "matched" and leaves the cache. This avoids a hook in every lead save (the store has a single observer and
    each save copies every lead, so a per-save check would cost a full pass on every engine write).
A job claims its numbers when it starts sending (`sending`), so results that arrive meanwhile go to a new job and are
never lost. Finished jobs keep only counts, not the numbers or names.
"""
import os
import uuid
from typing import Optional

from .workspaces import ws_of

MAX_PER_JOB = 500
CACHE_MAX = 5000
RECHECK_SECONDS = 300
PATH = "/api/v1/external-matches/{source}"
_last_recheck = {"at": 0.0}


def _ct():
    from . import calltracker
    return calltracker


def usable(number) -> bool:
    """A real number (masked numbers such as 98******10 are never matched or sent)."""
    text = str(number or "").strip()
    return bool(text) and not any(ch in text for ch in "*xX•") and len(_ct().norm_phone(text)) >= 6


def phones_of(lead: dict) -> set:
    ct = _ct()
    return {n for n in (ct.norm_phone(p) for p in [lead.get("phone"), *(lead.get("phones") or [])] if p) if n}


def lead_url(store, lead_id: str) -> str:
    """Absolute link to the lead, from the address set in the app (or OUTREACH_BASE_URL); "" when neither is set."""
    from urllib.parse import quote
    from .growth_core import public_base_url
    base = public_base_url(store, os.environ.get("OUTREACH_BASE_URL", ""))
    return f"{base}/#leads?lead={quote(lead_id, safe='')}" if base.startswith(("https://", "http://")) else ""


def result_for(store, lead: Optional[dict]) -> dict:
    if not lead:
        return {"status": "not_found"}
    from .brand import stage_label
    label = {"name": (lead.get("contact_name") or "")[:120], "company": (lead.get("company") or "")[:120],
             "stage": stage_label(store, lead.get("stage"))[:60], "url": lead_url(store, lead["id"])}
    return {"status": "matched", "externalId": lead["id"], "label": {k: v for k, v in label.items() if v}}


# ---------------------------------------------------------------- queue
def queue_results(store, target, found: dict) -> int:
    """found = {number exactly as DialHalo gave it: lead or None}. Updates the cache and merges into the match job(s) of
    the link the numbers came from (`target`: a link, a link id, or a workspace = its primary link). A number that now
    matches is also sent as matched to the other links of the workspace that had reported it as not found."""
    from . import calltracker_links as links
    ct = _ct()
    link = links.resolve(store, target)
    results = {str(n)[:40]: result_for(store, lead) for n, lead in found.items() if usable(n)}
    if not results or not links.is_active(link):
        return 0
    others = _update_cache(store, link["workspace"], link["id"], results)
    _merge(store, link, results)
    for other_id, more in others.items():
        other = links.get(store, other_id)
        if links.is_active(other) and other["workspace"] == link["workspace"]:
            _merge(store, other, more)
    ct.kick(store)
    return len(results)


def note_calls(store, target, found: dict) -> None:
    """Calls from the receiver ({number: lead or None}), once per webhook. Never fails the webhook."""
    try:
        queue_results(store, target, found)
    except Exception as exc:                          # matching for DialHalo must never break applying the call
        print(f"calltracker match not queued: {type(exc).__name__}")


def _merge(store, link: dict, results: dict) -> None:
    ct = _ct()
    stamp = ct.iso(ct.now_utc())
    ws, lid = link["workspace"], link["id"]
    mine = lambda j: j.get("kind") == "match" and j.get("ws") == ws and j.get("link") == lid

    def apply(jobs: list) -> list:
        open_jobs = [j for j in jobs if mine(j) and j.get("status") == "pending" and not j.get("sending")]
        rest = dict(results)
        for j in jobs:                                # a failed job never re-sends an older result for these numbers
            if mine(j) and j.get("status") == "failed":
                j["items"] = {k: v for k, v in (j.get("items") or {}).items() if k not in rest}
        for j in open_jobs:                           # a number already waiting: the newest result replaces it
            items = j.setdefault("items", {})
            for n in [n for n in rest if n in items]:
                items[n] = rest.pop(n)
        for j in open_jobs:
            items = j["items"]
            while rest and len(items) < MAX_PER_JOB:
                n = next(iter(rest))
                items[n] = rest.pop(n)
            j.update(updated_at=stamp)
        new = []
        while rest:
            chunk = dict(list(rest.items())[:MAX_PER_JOB])
            for n in chunk:
                rest.pop(n)
            new.append({"id": uuid.uuid4().hex[:12], "kind": "match", "ws": ws, "link": lid, "items": chunk, "attempts": 0,
                        "status": "pending", "next_at": stamp, "created_at": stamp, "updated_at": stamp})
        done = [j for j in jobs if j.get("status") == "done"]
        keep = {j["id"] for j in done[-ct.DONE_KEPT:]}
        return [*[j for j in jobs if j.get("status") != "done" or j["id"] in keep], *new]
    store.update("ct_outbox", apply)


def run(store, conn: dict, job: dict) -> str:
    """The queue's step: claim the job's numbers and PUT them to DialHalo."""
    ct = _ct()
    claimed = {}

    def claim(jobs: list) -> list:
        for j in jobs:
            if j.get("id") == job["id"]:
                j["sending"] = True
                claimed.update(j.get("items") or {})
        return jobs
    store.update("ct_outbox", claim)
    if not claimed:
        return "nothing to send"
    body = {"matches": [{"number": n, **r} for n, r in claimed.items()]}
    try:
        data = ct.ct_request(conn, "PUT", PATH.format(source=ct.SOURCE), body)
    except ct.CallTrackerError as exc:
        if exc.status == 404:
            raise ct.CallTrackerError("This DialHalo is too old to show Outreach matches. Update DialHalo, then press Retry (HTTP 404).",
                                      404, retry=False) from None
        raise
    matched = sum(1 for r in claimed.values() if r.get("status") == "matched")
    saved = (data or {}).get("saved", len(claimed)) if isinstance(data, dict) else len(claimed)
    return f"{len(claimed)} number(s) sent: {matched} matched, {len(claimed) - matched} not in Outreach ({saved} saved)"


def settle(store, job: dict, change: dict) -> dict:
    """Called by the queue with the outcome of a send. Done: keep counts only and note which leads DialHalo now has a
    match label for (so erasing them also clears DialHalo). Otherwise: release the claim so it can be retried."""
    out = {**change, "sending": False}
    if change.get("status") != "done":
        return out
    items = next((j.get("items") or {} for j in store.read("ct_outbox") if j.get("id") == job["id"]), {}) or job.get("items") or {}
    ids = {r.get("externalId") for r in items.values() if r.get("status") == "matched" and r.get("externalId")}
    if ids:
        _mark_sent(store, ids)
    return {**out, "items": {}, "sent": len(items), "matched": len(ids)}


def _mark_sent(store, ids: set) -> None:
    ct = _ct()
    if not any(l.get("id") in ids and not (l.get("calltracker") or {}).get("match_sent_at") for l in store.peek("leads")):
        return                                         # nothing new: no save of the leads
    stamp = ct.iso(ct.now_utc())
    store.update("leads", lambda leads: [{**l, "calltracker": {**(l.get("calltracker") or {}), "match_sent_at": stamp}}
                                         if l.get("id") in ids and not (l.get("calltracker") or {}).get("match_sent_at") else l for l in leads])


def pending_for_lead(store, lead_id: str) -> bool:
    return any(j.get("kind") == "match" and j.get("status") != "done" and any(r.get("externalId") == lead_id for r in (j.get("items") or {}).values())
               for j in store.read("ct_outbox"))


def scrub_lead(jobs: list, ws: str, lead_id: str) -> list:
    """Erasure: drop the person's matched entries from waiting match jobs (DialHalo removes what it already holds)."""
    for j in jobs:
        if j.get("kind") == "match" and j.get("ws") == ws and j.get("status") != "done":
            j["items"] = {n: r for n, r in (j.get("items") or {}).items() if r.get("externalId") != lead_id}
    return jobs


# ---------------------------------------------------------------- not-found cache
def _update_cache(store, ws: str, link_id: str, results: dict) -> dict:
    """Not found: cached with the links that reported it. Matched: leaves the cache. Returns {other link id: {number:
    result}} for matched numbers that other links of the workspace had reported (they get the match too)."""
    ct = _ct()
    stamp = ct.iso(ct.now_utc())
    others = {}

    def apply(rows: dict) -> dict:
        cache = dict(rows.get(ws) or {})
        for number, r in results.items():
            key = ct.norm_phone(number)
            if r.get("status") == "matched":
                old = cache.pop(key, None) or {}
                for other in old.get("links") or []:
                    if other != link_id:
                        others.setdefault(other, {})[old.get("number") or number] = r
            else:
                old = cache.get(key) or {}
                cache[key] = {"number": number, "last_seen": stamp, "links": sorted({*(old.get("links") or []), link_id})}
        if len(cache) > CACHE_MAX:
            cache = dict(sorted(cache.items(), key=lambda kv: kv[1].get("last_seen", ""))[-CACHE_MAX:])
        return {**rows, ws: cache} if cache else {k: v for k, v in rows.items() if k != ws}
    store.update("ct_unmatched", apply)
    return others


def waiting(store) -> int:
    return sum(len(c) for c in (store.peek("ct_unmatched") or {}).values() if isinstance(c, dict))


def recheck(store) -> int:
    """Numbers in the cache that a lead of the same workspace now has: sent as matched to the link(s) that reported them
    (rows cached before links existed: the workspace's primary link). Returns how many."""
    from . import calltracker_events as events, calltracker_links as links
    found_total = 0
    for ws, cache in list((store.read("ct_unmatched") or {}).items()):
        active = {l["id"]: l for l in links.active_links(store, ws)}
        if not cache or not active:
            continue
        hits = {}
        for lead in store.peek("leads"):              # read-only pass, no copy of every lead
            if ws_of(lead) != ws:
                continue
            for key in phones_of(lead) & cache.keys():
                hits.setdefault(key, []).append(lead)
        by_link = {}
        fallback = links.primary_of(list(active.values()))
        for key, cands in hits.items():
            first = next((lid for lid in cache[key].get("links") or [] if lid in active), None) or fallback["id"]
            by_link.setdefault(first, {})[cache[key]["number"]] = events.pick_lead(store, [dict(l) for l in cands])
        for lid, found in by_link.items():            # queue_results also tells the row's other links
            found_total += queue_results(store, active[lid], found)
    return found_total


def tick(store, now: Optional[float] = None) -> bool:
    """Called by the calltracker worker each pass. Re-checks the cache every RECHECK_SECONDS. True while numbers wait."""
    import time
    now = time.time() if now is None else now
    if waiting(store) and now - _last_recheck["at"] >= RECHECK_SECONDS:
        _last_recheck["at"] = now
        try:
            recheck(store)
        except Exception as exc:                      # never stop the worker
            print(f"calltracker match re-check failed: {type(exc).__name__}")
    return bool(waiting(store))


def forget_link(store, ws: str, link_id: str) -> None:
    """A link was removed but the workspace has others: numbers only that link reported leave the cache."""
    def apply(rows: dict) -> dict:
        cache = {}
        for key, row in (rows.get(ws) or {}).items():
            left = [l for l in row.get("links") or [] if l != link_id]
            if row.get("links") and not left:
                continue
            cache[key] = {**row, "links": left} if row.get("links") else row
        return {**rows, ws: cache} if cache else {k: v for k, v in rows.items() if k != ws}
    store.update("ct_unmatched", apply)


def forget_workspace(store, ws: str) -> None:
    store.update("ct_unmatched", lambda rows: {k: v for k, v in rows.items() if k != ws})
