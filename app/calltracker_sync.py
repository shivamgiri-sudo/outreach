"""Lead sync sweep for DialHalo links set to "owners_members" or "all_with_phone" (app/calltracker_links.py).

A call task pushes its lead at once (app/calltracker.on_call_task). These two modes also cover leads that never got a
call task: every SWEEP_SECONDS the calltracker worker walks the workspace's leads once (read-only, no copy) and queues a
push for each lead that
  * was never pushed to DialHalo (no calltracker.lead_id) and has no push waiting,
  * has a phone, and did not opt out,
  * is routed to a sweeping link by the one-link rule (the link it already went to, else the primary, else the oldest).
At most SWEEP_MAX pushes are queued per pass, and none for a link that already has SWEEP_MAX waiting, so a large
"all_with_phone" workspace trickles in instead of flooding DialHalo or the 1 GB server. Members lists are refreshed when
older than their cache time (a network error keeps the old list).
"""
import time
import uuid
from typing import Optional

from .workspaces import ws_of

SWEEP_SECONDS = 900
SWEEP_MAX = 200
_last = {"at": 0.0}


def wanted(store) -> bool:
    from . import calltracker_links as links
    return any(links.is_active(l) and l.get("lead_sync") in links.SWEEP_MODES for l in links.all_links(store))


def sweep(store, ws: Optional[str] = None) -> int:
    """Queue pushes for leads the sweeping links should hold. Returns how many were queued."""
    from . import calltracker as ct, calltracker_links as links
    by_ws = {}
    for link in links.all_links(store):
        if links.is_active(link) and (ws is None or link["workspace"] == ws):
            by_ws.setdefault(link["workspace"], []).append(link)
    by_ws = {w: ls for w, ls in by_ws.items() if any(l.get("lead_sync") in links.SWEEP_MODES for l in ls)}
    if not by_ws:
        return 0
    for w, ls in by_ws.items():                       # owners_members needs a fresh members list
        for i, link in enumerate(ls):
            if link.get("lead_sync") == "owners_members":
                links.members_of(store, link)
                ls[i] = links.get(store, link["id"]) or link
    jobs = store.read("ct_outbox")
    waiting = {j.get("lead_id") for j in jobs if j.get("kind") == "push" and j.get("status") == "pending"}
    per_link = {}
    for j in jobs:
        if j.get("kind") == "push" and j.get("status") == "pending":
            per_link[j.get("link")] = per_link.get(j.get("link"), 0) + 1
    queue, memo = [], {}
    for lead in store.peek("leads"):                  # read-only pass
        w = ws_of(lead)
        if w not in by_ws or lead.get("id") in waiting or (lead.get("calltracker") or {}).get("lead_id") or ct.opted_out(lead):
            continue
        if not any(ct.norm_phone(p) for p in [lead.get("phone"), *(lead.get("phones") or [])]):
            continue
        link = links.route(store, lead, links=by_ws[w], memo=memo)
        if not link or link.get("lead_sync") not in links.SWEEP_MODES or per_link.get(link["id"], 0) >= SWEEP_MAX:
            continue
        per_link[link["id"]] = per_link.get(link["id"], 0) + 1
        queue.append({"kind": "push", "ws": w, "link": link["id"], "lead_id": lead["id"], "task_id": None, "sync": link["lead_sync"]})
        if len(queue) >= SWEEP_MAX:
            break
    if queue:
        stamp = ct.iso(ct.now_utc())
        rows = [{"id": uuid.uuid4().hex[:12], "attempts": 0, "status": "pending", "next_at": stamp, "created_at": stamp, "updated_at": stamp, **j}
                for j in queue]
        store.update("ct_outbox", lambda current: [*current, *[r for r in rows if r["lead_id"] not in
                                                               {j.get("lead_id") for j in current if j.get("kind") == "push" and j.get("status") == "pending"}]])
        ct.kick(store)
    return len(queue)


def tick(store, now: Optional[float] = None) -> bool:
    """Called by the calltracker worker each pass. True while some link sweeps (keeps the worker up)."""
    if not wanted(store):
        return False
    now = time.time() if now is None else now
    if now - _last["at"] >= SWEEP_SECONDS:
        _last["at"] = now
        try:
            sweep(store)
        except Exception as exc:                      # never stop the worker
            print(f"calltracker lead sync sweep failed: {type(exc).__name__}")
    return True
