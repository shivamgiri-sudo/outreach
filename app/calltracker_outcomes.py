"""Two-way call outcome sync with DialHalo, the Outreach -> DialHalo half (contract of 2026-11-18).

  * A person changes the outcome of a DialHalo call in Outreach (PUT /api/leads/<lead>/activity/<activity>/outcome).
    The row keeps ct.outcome_changed_at / outcome_origin "mcn_outreach" / outcome_by, and ONE coalesced ct_outbox job
    {"kind": "outcome", "link": the link the call came from (activity ct.conn), "call_id", "activity_id"} is queued:
        PUT /api/v1/calls/<callId>/outcome
        {outcome?, callbackAt?, note?, changedAt, by: {email, name}, origin: "mcn_outreach"}
    -> {applied, reason?: older|same|unknown_outcome|not_shared, outcome?}; a note-only change answers unknown_outcome
    (expected, not flagged a second time)
  * A call logged by hand in Outreach (no DialHalo call id) is sent as a lead history entry to EVERY active link of the
    workspace, one job {"kind": "activity", "activity_id"} per link:
        POST /api/v1/leads/activity
        {source: "mcn_outreach", refId: <activity id>, number, externalId: <lead id>, kind: "call_logged", outcome?, note?, at, by}
    DialHalo answers {stored: false, reason: "no_lead"} where it does not know the number: done, not an error. Changing
    such a call's outcome later queues the same jobs again (DialHalo updates its entry by (source, refId)).
  * Outreach outcome -> DialHalo outcome name: the link's mapping (calltracker.mapping_of) read backwards. Of the rules
    for that outcome, the one without opt_out wins; the opt_out one when the lead has opted out. No rule: no outcome is
    sent (the key is left out: null would clear DialHalo's outcome), the note says "Outcome in Outreach: X", and the name is listed
    on the link's card (unmapped_outgoing, next to the incoming unmapped names) and on the call row.
  * Jobs hold ids only (no number, no name). The payload is built when the job runs, from the activity row as it is
    then, so coalesced edits send the latest value. A job claims itself while sending (`sending`), so an edit made during
    the send queues a new job instead of being lost.
  * A change that came FROM DialHalo never queues anything: the receiver never calls these functions, and every entry
    point here returns at once while calltracker.FROM_CALLTRACKER is set.
  * Personal links (calltracker_links: dh_scope "member"): an outcome goes back to the link the call came from, except
    that an active workspace link to the same DialHalo workspace wins over a personal one. A call logged in Outreach goes
    to every active workspace link, and to a personal link only when its member is the lead's owner or the person who
    logged the call (by e-mail) and no workspace link to that DialHalo workspace exists.
Retries, back-off, "failed" and Retry are the queue's (app/calltracker.py).
"""
from datetime import datetime, timezone
from typing import Optional

from .workspaces import ws_of

KINDS = ("outcome", "activity")
PUT_PATH = "/api/v1/calls/{call_id}/outcome"
POST_PATH = "/api/v1/leads/activity"
ORIGIN = "mcn_outreach"


def _ct():
    from . import calltracker
    return calltracker


def _links():
    from . import calltracker_links
    return calltracker_links


def z_iso(dt: datetime) -> str:
    """ISO 8601 UTC with Z, as the contract asks."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _z(text) -> str:
    ct = _ct()
    return z_iso(ct.parse(text) or ct.now_utc())


def label(outcome: str) -> str:
    return str(outcome or "").replace("_", " ").capitalize()


def meta_of(row: dict) -> dict:
    """Where the outcome-change facts live: ct on a DialHalo call, outcome_change on a call logged in Outreach."""
    return (row.get("ct") if row.get("source") == "calltracker" else row.get("outcome_change")) or {}


def is_dialhalo_call(row: dict) -> bool:
    info = row.get("ct") or {}
    return row.get("source") == "calltracker" and bool(info.get("call_id")) and bool(info.get("conn"))


def reverse_name(conn: dict, outcome: str, lead: Optional[dict]) -> Optional[str]:
    """The DialHalo outcome name for an Outreach outcome on this link, or None."""
    if not outcome:
        return None
    ct = _ct()
    rules = [(name, rule) for name, rule in ct.mapping_of(conn).items() if isinstance(rule, dict) and rule.get("outcome") == outcome]
    if not rules:
        return None
    want_opt_out = bool(lead and ct.opted_out(lead))
    preferred = [name for name, rule in rules if bool(rule.get("opt_out")) == want_opt_out]
    return (preferred or [name for name, _ in rules])[0]


def _from_dialhalo() -> bool:
    return bool(getattr(_ct().FROM_CALLTRACKER, "on", False))


# ---------------------------------------------------------------- queueing
def queue_outcome(store, lead: dict, row: dict) -> Optional[dict]:
    """A DialHalo call's outcome changed in Outreach: one coalesced job for the link that call came from."""
    if _from_dialhalo() or not is_dialhalo_call(row):
        return None
    ct, links = _ct(), _links()
    link = links.get(store, row["ct"]["conn"])
    ws = ws_of(lead)
    if link and links.is_personal(link):              # a workspace link to the same DialHalo workspace wins
        active = links.active_links(store, ws)
        if links.shadowed(link, active):
            link = next(o for o in active if not links.is_personal(o) and links.dh_id(o) == links.dh_id(link))
    if not links.is_active(link) or link.get("workspace") != ws:
        return None
    call_id, lid = int(row["ct"]["call_id"]), link["id"]
    return ct._enqueue(store, {"kind": "outcome", "ws": ws, "link": lid, "lead_id": lead["id"], "activity_id": row["id"], "call_id": call_id},
                       merge_key=lambda j: j.get("kind") == "outcome" and j.get("link") == lid and j.get("call_id") == call_id and not j.get("sending"))


def _number(lead: dict, row: dict) -> str:
    ct = _ct()
    for raw in (row.get("to"), lead.get("phone"), *(lead.get("phones") or [])):
        if len(ct.norm_phone(raw)) >= 6:
            return str(raw)
    return ""


def queue_history(store, lead: dict, row: dict) -> list:
    """A call logged in Outreach (no DialHalo call id): one coalesced job per active link of the workspace."""
    if _from_dialhalo() or row.get("type") != "call" or row.get("source") == "calltracker" or not _number(lead, row):
        return []
    ct, links = _ct(), _links()
    ws = ws_of(lead)
    jobs = []
    active = links.active_links(store, ws)
    logged_by = {_by(store, row, meta_of(row))["email"].lower()} - {""}
    for link in active:
        if links.is_personal(link) and (links.shadowed(link, active) or not links.paired(store, link, lead, extra=logged_by)):
            continue
        lid, aid = link["id"], row["id"]
        jobs.append(ct._enqueue(store, {"kind": "activity", "ws": ws, "link": lid, "lead_id": lead["id"], "activity_id": aid},
                                merge_key=lambda j, lid=lid, aid=aid: j.get("kind") == "activity" and j.get("link") == lid
                                and j.get("activity_id") == aid and not j.get("sending")))
    return jobs


def on_logged(store, lead_id: str, row: dict) -> None:
    """activities.log_activity: a call was logged by hand. Never fails the logging."""
    if _from_dialhalo() or row.get("type") != "call":
        return
    try:
        lead = next((l for l in store.read("leads") if l.get("id") == lead_id), None)
        if lead:
            queue_history(store, lead, row)
    except Exception as exc:                          # the call is logged whatever happens to its copy for DialHalo
        print(f"calltracker call history not queued: {type(exc).__name__}")


def change_outcome(store, lead_id: str, activity_id: str, outcome: str, who: str, who_email: str = "", note: str = "",
                   callback_at: Optional[datetime] = None) -> dict:
    """A person changed a call's outcome in Outreach. Returns {activity, queued}. Raises LookupError for an unknown call."""
    ct = _ct()
    stamp = ct.now_utc()
    found = {}
    change = {"outcome_changed_at": ct.iso(stamp), "outcome_origin": ORIGIN, "outcome_by": who[:120], "outcome_by_email": (who_email or "")[:190],
              "outcome_note": (note or "")[:1000], "outcome_callback_at": ct.iso(callback_at) if callback_at else None}

    def apply(rows: list) -> list:
        for r in rows:
            if r.get("id") == activity_id and r.get("lead_id") == lead_id and r.get("type") == "call":
                found["old"] = r.get("outcome") or ""
                if found["old"] == outcome and not note and not callback_at:
                    found["row"] = dict(r)
                    return rows
                if r.get("source") == "calltracker":
                    r["ct"] = {**(r.get("ct") or {}), **change, "sync": None}
                else:
                    r["outcome_change"] = {**(r.get("outcome_change") or {}), **change, "sync": None}
                r["outcome"] = outcome
                found["row"] = dict(r)
                found["changed"] = True
        return rows
    store.update("activities", apply)
    if "row" not in found:
        raise LookupError("Call not found")
    row = found["row"]
    if not found.get("changed"):
        return {"activity": row, "queued": 0}
    run_rules(store, lead_id, row, found["old"], outcome, who, note, callback_at)
    row = next((a for a in store.read("activities") if a.get("id") == activity_id), row)
    text = f"Call outcome changed to {label(outcome)} by {who} in Outreach" + (f" (was {label(found['old'])})" if found["old"] else "") \
        + (f": {note[:200]}" if note else "")
    store.update_lead(lead_id, lambda l: {**l, "timeline": [*l["timeline"], {"at": ct.iso(stamp), "kind": "call_outcome", "text": text, "by": who}]})
    lead = next((l for l in store.read("leads") if l.get("id") == lead_id), None) or {}
    if is_dialhalo_call(row):
        queued = 1 if queue_outcome(store, lead, row) else 0
    else:
        queued = len(queue_history(store, lead, row))
    return {"activity": row, "queued": queued}


def run_rules(store, lead_id: str, row: dict, old: str, outcome: str, who: str, note: str = "", callback_at: Optional[datetime] = None) -> bool:
    """Outreach's own rules for an outcome changed here, once per change (the row's `applied`): the call's link's forward
    rule whose outcome this is (the same rule the reverse mapping picks: stage, consent, stop sequence, opt-out, wrong
    number) through calltracker_events.act_on_outcome, else what logging a call with this outcome does
    (rep_workflow.outcome_effects). Open call tasks are closed, as logging a call does. Never queues anything outbound
    by itself (the outcome job / history entry are queued by change_outcome). Returns True when the rules ran."""
    from . import calltracker_events as events, rep_workflow
    from .activities import close_call_tasks
    links = _links()
    lead = next((l for l in store.read("leads") if l.get("id") == lead_id), None)
    if not lead:
        return False
    conn = links.get(store, (row.get("ct") or {}).get("conn")) if is_dialhalo_call(row) else None
    name = reverse_name(conn, outcome, lead) if conn else None
    rule = _ct().mapping_of(conn).get(name) if name else None
    key = name if rule else f"outreach:{outcome}"
    meta = meta_of(row)
    current = meta.get("applied") if is_dialhalo_call(row) else meta.get("applied", f"outreach:{old}" if old else None)
    if events.same_outcome(current, key):
        return False
    close_call_tasks(store, lead_id, f"Call outcome changed to {label(outcome)} ({who})")
    if rule:
        events.act_on_outcome(store, conn, lead_id, {"outcome": name, "number": row.get("to") or lead.get("phone")}, rule, who, source="Outreach")
    else:
        rep_workflow.outcome_effects(store, lead_id, outcome, note, who, callback_at, dialled=row.get("to") or "")
    field = "ct" if row.get("source") == "calltracker" else "outcome_change"
    store.update("activities", lambda rows: [{**r, field: {**(r.get(field) or {}), "applied": key}} if r.get("id") == row["id"] else r for r in rows])
    return True


# ---------------------------------------------------------------- the queue's step
def _claim(store, job_id: str, on: bool) -> None:
    store.update("ct_outbox", lambda jobs: [{**j, "sending": on} if j.get("id") == job_id else j for j in jobs])


def _set_sync(store, activity_id: str, sync: dict) -> None:
    def apply(rows: list) -> list:
        for r in rows:
            if r.get("id") == activity_id:
                key = "ct" if r.get("source") == "calltracker" else "outcome_change"
                r[key] = {**(r.get(key) or {}), "sync": sync}
        return rows
    store.update("activities", apply)


def _by(store, row: dict, meta: dict) -> dict:
    ct = _ct()
    if meta.get("outcome_by"):                        # changed later: who changed it, never who made the call
        name = meta["outcome_by"]
        email = meta.get("outcome_by_email") or ct.email_of_person(store, name)
    else:
        name = row.get("by") or ""
        email = ((row.get("origin") or {}).get("actor") or {}).get("email") or ct.email_of_person(store, name)
    return {"email": str(email or "")[:190], "name": str(name or email or "MCN Outreach")[:120]}


def build_payload(store, conn: dict, job: dict, row: dict, lead: dict) -> tuple:
    """(method, path, body, unmapped outcome or "") for one job, from the activity as it is now."""
    ct = _ct()
    meta = meta_of(row)
    name = reverse_name(conn, row.get("outcome") or "", lead)
    unmapped = row.get("outcome") if row.get("outcome") and not name else ""
    extra = f"Outcome in Outreach: {label(unmapped)} (no matching DialHalo outcome)" if unmapped else ""
    if job["kind"] == "outcome":
        note = "\n".join(x for x in [meta.get("outcome_note") or "", extra] if x)[:1000]
        body = {"changedAt": _z(meta.get("outcome_changed_at")), "by": _by(store, row, meta), "origin": ORIGIN}
        if name:                                      # note only: the key is left out (outcome: null would CLEAR it in DialHalo)
            body["outcome"] = name
        if note:
            body["note"] = note
        if meta.get("outcome_callback_at"):
            body["callbackAt"] = _z(meta["outcome_callback_at"])
        return "PUT", PUT_PATH.format(call_id=int(job["call_id"])), body, unmapped
    note = "\n".join(x for x in [row.get("body") or "", meta.get("outcome_note") or "", extra] if x)[:2000]
    body = {"source": ct.SOURCE, "refId": row["id"], "number": _number(lead, row)[:40], "externalId": lead["id"], "kind": "call_logged",
            "at": _z(row.get("at")), "by": _by(store, row, meta)}
    if name:
        body["outcome"] = name
    if note:
        body["note"] = note
    return "POST", POST_PATH, body, unmapped


def run(store, conn: dict, job: dict) -> str:
    """Send one outcome / history job. Raises CallTrackerError on failure (the queue retries or fails it)."""
    ct = _ct()
    row = next((a for a in store.read("activities") if a.get("id") == job.get("activity_id")), None)
    lead = next((l for l in store.read("leads") if l.get("id") == job.get("lead_id")), None)
    if not row or not lead:
        raise ct.CallTrackerError("The call or its lead is no longer in Outreach.", retry=False)
    if ws_of(lead) != job["ws"]:
        raise ct.CallTrackerError("The lead moved to another workspace.", retry=False)
    _claim(store, job["id"], True)
    method, path, body, unmapped = build_payload(store, conn, job, row, lead)
    if job["kind"] == "activity" and not body["number"]:
        raise ct.CallTrackerError("The lead has no phone number, so DialHalo cannot find it.", retry=False)
    try:
        data = ct.ct_request(conn, method, path, body) or {}
    except ct.CallTrackerError as exc:
        if exc.status == 404 and job["kind"] == "outcome":
            raise ct.CallTrackerError("DialHalo does not have this call in the linked workspace, or is too old to take outcome changes (HTTP 404).",
                                      404, retry=False) from None
        if exc.status == 404:
            raise ct.CallTrackerError("This DialHalo is too old to take calls logged in Outreach. Update DialHalo, then press Retry (HTTP 404).",
                                      404, retry=False) from None
        raise
    data = data if isinstance(data, dict) else {}
    reason = str(data.get("reason") or "")
    shown = label(unmapped) if unmapped else ""
    if unmapped:
        _links().note_unmapped_out(store, conn["id"], shown, "no_rule")
    elif reason == "unknown_outcome" and body.get("outcome"):
        shown = str(body["outcome"])                  # our mapping names an outcome this DialHalo does not have
        _links().note_unmapped_out(store, conn["id"], shown, "unknown_in_dialhalo")
    sync = {"at": ct.iso(ct.now_utc()), "link": conn["id"], "unmapped": shown}
    if job["kind"] == "outcome":
        sync.update(applied=bool(data.get("applied")), reason=reason)
        _set_sync(store, row["id"], sync)
        if data.get("applied"):
            return f"outcome sent ({body.get('outcome') or 'note only'})"
        return f"outcome not applied in DialHalo ({reason or 'no reason given'})"
    sync.update(stored=bool(data.get("stored")), reason=reason)
    _set_sync(store, row["id"], sync)
    return "call history stored" if data.get("stored") else f"call history not stored ({reason or 'no reason given'})"


def settle(store, job: dict, change: dict) -> dict:
    """Release the claim; a final failure leaves a line on the lead (its push state is not touched)."""
    out = {**change, "sending": False}
    if change.get("status") == "failed" and job.get("lead_id"):
        ct = _ct()
        what = "outcome change" if job["kind"] == "outcome" else "call logged in Outreach"
        text = f"DialHalo: this {what} was not sent ({change.get('error') or 'error'}). Use Retry on Integrations → DialHalo."
        store.update_lead(job["lead_id"], lambda l: {**l, "timeline": [*l["timeline"], {"at": ct.iso(ct.now_utc()), "kind": "calltracker", "text": text}]})
    return out


# ---------------------------------------------------------------- shown on the call row
def row_view(store, row: dict) -> dict:
    """Extra fields for a call in the activity list: who changed its outcome where, and how it went to DialHalo."""
    meta = meta_of(row)
    out = {"outcome_editable": row.get("type") == "call", "dialhalo_call": is_dialhalo_call(row)}
    origin = meta.get("outcome_origin")
    if origin and meta.get("outcome_by"):
        if origin == ORIGIN:
            out["change_text"] = f"Changed to {label(row.get('outcome'))} by {meta['outcome_by']} in Outreach"
        elif meta.get("outcome_name"):
            out["change_text"] = f"Changed to {meta['outcome_name']} by {meta['outcome_by']} in DialHalo"
    sync = meta.get("sync") or {}
    if sync.get("unmapped"):
        out["sync_text"] = f"{sync['unmapped']} is not an outcome in DialHalo: sent there as a note only"
    elif sync.get("reason") in ("older", "same", "not_shared"):
        out["sync_text"] = {"older": "DialHalo already had a newer outcome", "same": "DialHalo already had this outcome",
                            "not_shared": "DialHalo does not share this call"}[sync["reason"]]
    elif sync.get("reason") == "no_lead":
        out["sync_text"] = "Not in DialHalo (it has no lead with this number)"
    return out
