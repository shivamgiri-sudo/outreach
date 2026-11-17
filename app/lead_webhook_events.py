"""Where lead webhook events come from (app/lead_webhook.py sends them).

lead.created / lead.updated: the store calls `on_leads_saved(store, before, after)` after every save of the leads
collection (Store.lead_observer), whatever saved it (import, form, harvest, extension, a person, the sending engine).
A lead id that was not there before is "created"; a lead whose watched fields changed is "updated" (the fields each
workspace picked; timeline and engine bookkeeping never count). With no hook switched on this returns at once.

harvest.lead_found: app/harvest.py calls `on_harvest(store, job, results)` after each saved page of listings.
"""
from . import lead_webhook as hooks
from .retry_queue import iso, now_utc

MAX_EVENTS_PER_SAVE = 2000


def install(store) -> None:
    store.lead_observer = on_leads_saved


def _snap(lead: dict, fields) -> tuple:
    return tuple(repr(lead.get(f)) for f in fields)


def on_leads_saved(store, before: list, after: list) -> None:
    table = {ws: h for ws, h in (store.peek(hooks.HOOKS) or {}).items() if hooks.active(h)}
    if not table:
        return
    wants = lambda ws, event: ws in table and event in (table[ws].get("events") or [])
    old = {l.get("id"): l for l in before or []}
    at, events = iso(now_utc()), []
    for lead in after or []:
        ws = lead.get("workspace") or "main"
        if ws not in table:
            continue
        prev = old.get(lead.get("id"))
        if prev is None:
            if wants(ws, "lead.created"):
                events.append((ws, hooks.payload("lead.created", lead, at=at)))
        elif wants(ws, "lead.updated"):
            fields = table[ws].get("fields") or hooks.DEFAULT_FIELDS
            if _snap(prev, fields) != _snap(lead, fields):
                changed = [f for f in fields if prev.get(f) != lead.get(f)]
                events.append((ws, hooks.payload("lead.updated", lead, changed=changed, at=at)))
        if len(events) >= MAX_EVENTS_PER_SAVE:
            break
    if events:
        hooks.queue_events(store, events)


def on_harvest(store, job: dict, results: list) -> int:
    hook = hooks.hook_of(store, job.get("ws") or "main")
    if not hooks.active(hook) or "harvest.lead_found" not in (hook.get("events") or []):
        return 0
    wanted = {r["lead_id"] for r in results if r.get("lead_id") and r.get("outcome") in ("new", "duplicate")}
    leads = {l["id"]: l for l in store.read("leads") if l.get("id") in wanted}
    at, events = iso(now_utc()), []
    pair_of = lambda r: (job.get("pairs") or [{}])[r.get("pair", 0)] if 0 <= r.get("pair", 0) < len(job.get("pairs") or []) else {}
    for r in results:
        lead = leads.get(r.get("lead_id"))
        if not lead:
            continue
        pair = pair_of(r)
        info = {"job_id": job["id"], "job_name": job.get("name", ""), "outcome": r["outcome"], "matched_by": r.get("matched_by", ""),
                "source": r.get("source", ""), "keyword": pair.get("keyword", ""), "location": (pair.get("location") or {}).get("text", ""),
                "dnd": bool(r.get("dnd"))}
        events.append((job.get("ws") or "main", hooks.payload("harvest.lead_found", lead, harvest=info, at=at)))
    return hooks.queue_events(store, events)
