"""Runs flows for leads: enrolment, the minute tick, triggers, stopping, and per-lead run state.

flow_runs = [{id, flow_id, flow_version, lead_id, current_node, step, status: running|waiting|done|stopped|error,
              wait_until, waiting_on, history: [{node, at, result}], started_at, updated_at, ended_at, reply_mark,
              variants: {split_node: "a"|"b"}, claim, deferred, attempts, stop_reason}]

Before every step, in this order: a lead that unsubscribed, was disqualified, bounced or is on the do-not-contact
list is stopped at once; a reply stops the run unless the flow is waiting for that reply (or is set to keep going on
replies), and a cold flow (stop on reply) also stops once the lead is warm, like the email sequence. A step is
claimed on the run before it acts and recorded after, so a second tick never repeats it, and a crash between the two
leaves the run marked "check this" instead of acting twice. A lead is never enrolled twice in the same flow, and a
run has a step limit so no flow can loop forever (loops need a wait step, checked when the flow is saved).
"""
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import flow_actions as act
from .flows import FlowError, ensure_collections, graph_for_version, is_trigger, outputs_of, title_of
from .sequence_runner import WARM_STAGES

MAX_STEPS_PER_RUN = 200
MAX_STEPS_PER_TICK = 25
ACTIVE = ("running", "waiting")
COLD_STAGES = ("new", "verified", "enriched", "contacted", "engaged")
IGNORED_REPLIES = ("auto_reply",)
POLLED_TRIGGERS = ("trigger.lead_added", "trigger.meeting_booked", "trigger.no_reply_days")
EVENT_TRIGGERS = {"reply": ("trigger.reply", "label"), "stage_change": ("trigger.stage_changed", "stage"),
                  "activation": ("trigger.activation_event", "event")}
_REPLY_LABEL = re.compile(r"^\[(\w+)\]")
_TICK_LOCK = threading.Lock()


class Context:
    """What actions need from the outside world (injected so tests never touch the network)."""

    def __init__(self, sender=None, base_url: str = "", transport=None, resolver=None, notify_sender=None,
                 ignore_hours: bool = False, ai_transport=None):
        self.sender = sender or (lambda message: {"status": "dry_run"})
        self.base_url, self.transport, self.resolver = base_url, transport, resolver
        self.notify_sender, self.ignore_hours, self.ai_transport = notify_sender, ignore_hours, ai_transport
        self.emails_sent = 0


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(stamp) -> Optional[datetime]:
    try:
        value = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def reply_events(lead: dict) -> list:
    return [e for e in lead.get("timeline", []) if e.get("kind") == "reply"]


def reply_label(event: dict) -> str:
    match = _REPLY_LABEL.match(event.get("text", ""))
    return match.group(1) if match else "other"


def _lead(store, lead_id: str) -> Optional[dict]:
    return next((l for l in store.read("leads") if l["id"] == lead_id), None)


def _flow(store, flow_id: str) -> Optional[dict]:
    return next((f for f in store.read("flows") if f["id"] == flow_id), None)


def _run(store, run_id: str) -> Optional[dict]:
    return next((r for r in store.read("flow_runs") if r["id"] == run_id), None)


def trigger_of(graph: dict) -> Optional[dict]:
    return next((n for n in graph["nodes"] if is_trigger(n["type"])), None)


def next_node(graph: dict, node_id: str, label: str) -> Optional[str]:
    return next((e["to"] for e in graph["edges"] if e["from"] == node_id and e["label"] == label), None)


def _note(store, lead_id: str, flow: dict, text: str, now: datetime) -> None:
    event = {"at": iso(now), "kind": "flow", "text": f"Flow {flow['name']}: {text}"}
    store.update_lead(lead_id, lambda l: {**l, "timeline": [*l["timeline"], event]})


def _change_run(store, run_id: str, expected_step: Optional[int], change) -> Optional[dict]:
    """Apply `change(run)` on the latest saved run, only if it is still active and on the expected step."""
    result = {}

    def apply(rows: list) -> list:
        out = []
        for r in rows:
            if r["id"] == run_id and r["status"] in ACTIVE and (expected_step is None or r["step"] == expected_step):
                changed = change(dict(r))
                if changed is not None:
                    r = {**changed}
                    result["run"] = r
            out.append(r)
        return out
    store.update("flow_runs", apply)
    return result.get("run")


# ---------------------------------------------------------------- enrolment
def enroll(store, flow: dict, lead_id: str, now: datetime, why: str = "added by hand") -> dict:
    """Start one lead on an active flow. Raises LookupError or FlowError (with a reason a person can read)."""
    ensure_collections(store)
    lead = _lead(store, lead_id)
    if lead is None:
        raise LookupError("Lead not found")
    if flow["status"] != "active":
        raise FlowError("Activate the flow first; drafts and paused flows don't take new leads.")
    blocked = act.blocked_reason(store, lead)
    if blocked:
        raise FlowError(f"Not started: {blocked}.")
    graph = {"nodes": flow["nodes"], "edges": flow["edges"]}
    trigger = trigger_of(graph)
    first = next_node(graph, trigger["id"], "next") if trigger else None
    run = {"id": uuid.uuid4().hex[:12], "flow_id": flow["id"], "flow_version": flow["version"], "lead_id": lead_id,
           "current_node": first, "step": 1, "status": "running" if first else "done", "wait_until": None,
           "waiting_on": None, "history": [{"node": trigger["id"] if trigger else None, "at": iso(now), "result": f"started ({why})"}],
           "started_at": iso(now), "updated_at": iso(now), "ended_at": None if first else iso(now),
           "reply_mark": len(reply_events(lead)), "variants": {}, "claim": None, "deferred": None, "attempts": {},
           "stop_reason": None if first else "the trigger is not connected to a step"}
    added = {}

    def add(rows: list) -> list:
        if any(r["flow_id"] == flow["id"] and r["lead_id"] == lead_id for r in rows):
            return rows
        added["run"] = run
        return [*rows, run]
    store.update("flow_runs", add)
    if not added:
        raise FlowError("This lead is already in this flow. A flow never starts twice for the same lead.")
    _note(store, lead_id, flow, f"started ({why})", now)
    return run


def on_event(store, kind: str, value, lead_id: str, now: Optional[datetime] = None, exclude_flow: str = None) -> int:
    """A reply, stage change or product event happened: start every active flow whose trigger matches."""
    if kind not in EVENT_TRIGGERS:
        return 0
    now = now or datetime.now(timezone.utc)
    ensure_collections(store)
    trigger_type, key = EVENT_TRIGGERS[kind]
    started = 0
    for flow in store.read("flows"):
        if flow["status"] != "active" or flow["id"] == exclude_flow:
            continue
        trigger = trigger_of(flow)
        if not trigger or trigger["type"] != trigger_type:
            continue
        wanted = (trigger.get("config") or {}).get(key)
        matches = wanted == value or (kind == "reply" and wanted == "any" and value not in ("unsubscribe", "bounce", "auto_reply"))
        if not matches:
            continue
        try:
            enroll(store, flow, lead_id, now, why=f"{kind.replace('_', ' ')}: {value}")
            started += 1
        except (FlowError, LookupError):
            continue
    return started


def _lead_added_matches(config: dict, lead: dict) -> bool:
    for key in ("source", "campaign_id", "city", "country"):
        wanted = str(config.get(key) or "").strip().lower()
        if wanted and wanted not in str(lead.get(key) or "").lower():
            return False
    tag = str(config.get("tag") or "").strip().lower()
    return not tag or tag in {str(t).lower() for t in lead.get("tags") or []}


def _schedule_matches(config: dict, lead: dict) -> bool:
    if not _lead_added_matches(config, lead):
        return False
    for key in ("stage", "band"):
        wanted = str(config.get(key) or "").strip().lower()
        if wanted and wanted != str(lead.get(key) or "").lower():
            return False
    return True


def poll_schedules(store, now: datetime) -> int:
    """On-a-schedule flows: once a day (or weekday / week) at the chosen India-time hour, start the matching leads that
    are not already in the flow, up to the limit."""
    flows = [f for f in store.read("flows") if f["status"] == "active" and (trigger_of(f) or {}).get("type") == "trigger.schedule"]
    if not flows:
        return 0
    ist = now.astimezone(act.IST)
    today = ist.date().isoformat()
    leads, taken = store.read("leads"), {(r["flow_id"], r["lead_id"]) for r in store.read("flow_runs")}
    started = 0
    for flow in flows:
        config = trigger_of(flow).get("config") or {}
        when = config.get("when")
        if (when == "weekdays" and ist.weekday() >= 5) or (when == "weekly" and ist.weekday() != int(config.get("weekday") or 0)):
            continue
        if ist < ist.replace(hour=int(config.get("hour") or 0), minute=0, second=0, microsecond=0) or flow.get("schedule_last") == today:
            continue
        store.update("flows", lambda fs, fid=flow["id"]: [{**f, "schedule_last": today} if f["id"] == fid else f for f in fs])
        limit = int(config.get("limit") or 50)
        for lead in [l for l in leads if (flow["id"], l["id"]) not in taken and _schedule_matches(config, l)][:limit]:
            try:
                enroll(store, flow, lead["id"], now, "scheduled")
                started += 1
            except (FlowError, LookupError):
                pass
    return started


def poll_triggers(store, now: datetime) -> int:
    """Triggers that are found by looking, not told: new leads, booked meetings, no reply for N days, and schedules."""
    scheduled = poll_schedules(store, now)
    flows = [f for f in store.read("flows") if f["status"] == "active" and (trigger_of(f) or {}).get("type") in POLLED_TRIGGERS]
    if not flows:
        return scheduled
    leads = store.read("leads")
    taken = {(r["flow_id"], r["lead_id"]) for r in store.read("flow_runs")}
    last_sent = {}
    for m in store.read("outbox"):
        if m.get("status") != "failed" and _parse(m.get("sent_at")):
            stamp = _parse(m["sent_at"])
            last_sent[m["lead_id"]] = max(stamp, last_sent.get(m["lead_id"], stamp))
    started = scheduled
    for flow in flows:
        trigger = trigger_of(flow)
        config, since = trigger.get("config") or {}, _parse(flow.get("activated_at")) or now
        for lead in leads:
            if (flow["id"], lead["id"]) in taken:
                continue
            why = None
            if trigger["type"] == "trigger.lead_added":
                created = _parse(lead.get("created_at"))
                if created and created >= since and _lead_added_matches(config, lead):
                    why = "new lead"
            elif trigger["type"] == "trigger.meeting_booked":
                if any(m.get("status") == "scheduled" and (_parse(m.get("created_at")) or since - timedelta(seconds=1)) >= since
                       for m in lead.get("meetings") or []):
                    why = "meeting booked"
            elif trigger["type"] == "trigger.no_reply_days":
                sent = last_sent.get(lead["id"])
                days = int(config.get("days") or 0)
                if (sent and not lead.get("engagement", {}).get("replies") and lead["stage"] in COLD_STAGES
                        and (now - sent).days >= days):
                    why = f"no reply for {days} days"
            if why:
                try:
                    enroll(store, flow, lead["id"], now, why)
                    started += 1
                except (FlowError, LookupError):
                    pass
    return started


# ---------------------------------------------------------------- finishing and moving
def _finish(store, flow: dict, run: dict, status: str, reason: str, now: datetime, note: bool = True) -> None:
    def change(r):
        return {**r, "status": status, "stop_reason": reason, "ended_at": iso(now), "updated_at": iso(now), "claim": None,
                "wait_until": None, "history": [*r["history"], {"node": r.get("current_node"), "at": iso(now), "result": reason}]}
    if _change_run(store, run["id"], None, change) and note:
        word = {"done": "finished", "stopped": "stopped", "error": "needs a check"}[status]
        _note(store, run["lead_id"], flow, f"{word} — {reason}", now)


def _move(store, flow, graph, run, node, label, result, now, note=False, data=None) -> bool:
    """Record a finished step and go to the step on its `label` output (or finish when nothing is connected)."""
    target = next_node(graph, node["id"], label)

    def change(r):
        entry = {"node": node["id"], "at": iso(now), "result": result, **({"data": data} if data else {})}
        updated = {**r, "history": [*r["history"], entry],
                   "step": r["step"] + 1, "claim": None, "waiting_on": None, "wait_until": None, "deferred": None,
                   "updated_at": iso(now), "current_node": target}
        if target is None:
            updated.update(status="done", ended_at=iso(now), stop_reason="reached the end of the flow")
        else:
            updated["status"] = "running"
        return updated
    saved = _change_run(store, run["id"], run["step"], change)
    if saved is None:
        return False
    if note:
        _note(store, run["lead_id"], flow, f"{title_of(node)} — {result}", now)
    if target is None:
        _note(store, run["lead_id"], flow, "finished", now)
    return True


def _wait(store, run: dict, until: datetime, now: datetime, history: Optional[str], node_id: str, **extra) -> None:
    def change(r):
        updated = {**r, "status": "waiting", "wait_until": iso(until), "updated_at": iso(now), **extra}
        if history:
            updated["history"] = [*r["history"], {"node": node_id, "at": iso(now), "result": history}]
        return updated
    _change_run(store, run["id"], run["step"], change)


def stop_run(store, run_id: str, why: str = "stopped by a person", now: Optional[datetime] = None) -> Optional[dict]:
    now = now or datetime.now(timezone.utc)
    run = _run(store, run_id)
    if run is None:
        raise LookupError("Run not found")
    if run["status"] not in ACTIVE:
        return run
    flow = _flow(store, run["flow_id"]) or {"name": run["flow_id"]}
    _finish(store, flow, run, "stopped", why, now)
    return _run(store, run_id)


# ---------------------------------------------------------------- the tick
def tick(store, now: Optional[datetime] = None, ctx: Optional[Context] = None) -> dict:
    """Start leads on polled triggers, then advance every due run. Safe to call every minute; writes nothing when
    nothing is due."""
    now = now or datetime.now(timezone.utc)
    ctx = ctx or Context()
    ensure_collections(store)
    if not _TICK_LOCK.acquire(blocking=False):
        return {"skipped": "another flow tick is running"}
    try:
        counts = {"enrolled": poll_triggers(store, now), "steps": 0, "finished": 0, "stopped": 0, "waiting": 0, "errors": 0}
        flows = {f["id"]: f for f in store.read("flows")}
        for run in store.read("flow_runs"):
            flow = flows.get(run["flow_id"])
            if run["status"] in ACTIVE and flow and flow["status"] == "active":
                try:
                    advance_run(store, flow, run["id"], now, ctx, counts)
                except Exception as exc:  # one broken run must not hold up the others; its claim marks it for a check
                    print(f"flow run {run['id']} failed: {exc}")
                    counts["errors"] += 1
        counts["emails"] = ctx.emails_sent
        return counts
    finally:
        _TICK_LOCK.release()


def _due(run: dict, now: datetime) -> bool:
    until = _parse(run.get("wait_until"))
    return until is None or now >= until


def advance_run(store, flow: dict, run_id: str, now: datetime, ctx: Context, counts: dict) -> None:
    graph = graph_for_version(flow, _run(store, run_id)["flow_version"])
    nodes = {n["id"]: n for n in graph["nodes"]}
    for _ in range(MAX_STEPS_PER_TICK):
        run = _run(store, run_id)
        if run is None or run["status"] not in ACTIVE:
            return
        if run.get("claim"):
            node_title = title_of(nodes.get(run["claim"]["node"], {"type": "step", "config": {}}))
            _finish(store, flow, run, "error", f"interrupted while doing '{node_title}'; check the outbox and the lead "
                    "before doing it by hand (it is never repeated automatically)", now)
            counts["errors"] += 1
            return
        lead = _lead(store, run["lead_id"])
        if lead is None:
            _finish(store, flow, run, "stopped", "the lead no longer exists (merged or deleted)", now, note=False)
            counts["stopped"] += 1
            return
        blocked = act.blocked_reason(store, lead)
        if blocked:
            _finish(store, flow, run, "stopped", f"{blocked}; no more messages from this flow", now)
            counts["stopped"] += 1
            return
        node = nodes.get(run["current_node"])
        if node is None:
            _finish(store, flow, run, "done", "reached the end of the flow", now)
            counts["finished"] += 1
            return
        events = reply_events(lead)
        if len(events) > run.get("reply_mark", 0):
            fresh = [reply_label(e) for e in events[run.get("reply_mark", 0):] if reply_label(e) not in IGNORED_REPLIES]
            if fresh and node["type"] == "wait_for_reply" and run.get("waiting_on") == run["step"]:
                label = fresh[0]
                port = label if next_node(graph, node["id"], label) else "any"
                _change_run(store, run_id, run["step"], lambda r: {**r, "reply_mark": len(events)})
                run = _run(store, run_id)
                _move(store, flow, graph, run, node, port, f"they replied ({label})", now, note=True)
                counts["steps"] += 1
                continue
            if fresh and flow.get("settings", {}).get("stop_on_reply", True):
                _finish(store, flow, run, "stopped", f"they replied ({fresh[0]})", now)
                counts["stopped"] += 1
                return
            _change_run(store, run_id, run["step"], lambda r: {**r, "reply_mark": len(events)})
            continue
        if flow.get("settings", {}).get("stop_on_reply", True) and lead["stage"] in WARM_STAGES:
            _finish(store, flow, run, "stopped", f"the lead is warm ({lead['stage']}); a person handles them now", now)
            counts["stopped"] += 1
            return
        if len(run["history"]) >= MAX_STEPS_PER_RUN:
            _finish(store, flow, run, "error", f"more than {MAX_STEPS_PER_RUN} steps; check the flow for a loop", now)
            counts["errors"] += 1
            return
        if not _step(store, flow, graph, run, node, lead, now, ctx, counts):
            return


def _step(store, flow, graph, run, node, lead, now, ctx, counts) -> bool:
    """Do the current step. Returns True when the run moved on and the next step may run in the same tick."""
    t, c = node["type"], node.get("config") or {}
    if c.get("disabled") and t != "stop" and not is_trigger(t):
        outs = outputs_of(t)
        if not _due(run, now):
            return False
        counts["steps"] += 1
        return _move(store, flow, graph, run, node, "next" if "next" in outs else (outs[0] if outs else "next"), "skipped: this step is turned off", now)
    if t == "wait":
        if run.get("waiting_on") != run["step"]:
            until = act.wait_until(c, now)
            if until <= now:
                return _move(store, flow, graph, run, node, "next", "no wait needed", now)
            _wait(store, run, until, now, f"waiting until {act.ist_text(until)}", node["id"], waiting_on=run["step"])
            counts["waiting"] += 1
            return False
        if _due(run, now):
            counts["steps"] += 1
            return _move(store, flow, graph, run, node, "next", "wait over", now)
        return False
    if t == "wait_for_reply":
        if run.get("waiting_on") != run["step"]:
            until = now + timedelta(days=int(c.get("days") or 1))
            _wait(store, run, until, now, f"waiting up to {c.get('days')} day(s) for a reply", node["id"],
                  waiting_on=run["step"], reply_mark=len(reply_events(lead)))
            counts["waiting"] += 1
            return False
        if _due(run, now):
            counts["steps"] += 1
            return _move(store, flow, graph, run, node, "timeout", "no reply in time", now)
        return False
    if not _due(run, now):
        return False                                  # a deferred action (caps, hours) waits for its time
    counts["steps"] += 1
    if t == "if":
        passed, why = act.evaluate(c, act.lead_facts(lead))
        return _move(store, flow, graph, run, node, "yes" if passed else "no", f"{'yes' if passed else 'no'} — {why}", now)
    if t == "split":
        variant = (run.get("variants") or {}).get(node["id"]) or act.split_variant(lead["id"], node["id"], c.get("percent_a"))
        _change_run(store, run["id"], run["step"], lambda r: {**r, "variants": {**(r.get("variants") or {}), node["id"]: variant}})
        return _move(store, flow, graph, _run(store, run["id"]), node, variant, f"variant {variant.upper()}", now)
    if t == "switch":
        field = str(c.get("field") or "")
        value = str((run.get("vars") or {}).get(field[4:], "")) if field.startswith("var:") else str(act.lead_facts(lead).get(field, ""))
        cases = [str(v).strip().lower() for v in c.get("cases") or []]
        index = next((i for i, v in enumerate(cases) if v and v == value.strip().lower()), None)
        label = "other" if index is None else f"c{index + 1}"
        return _move(store, flow, graph, run, node, label, f"{value or 'empty'} → {label if index is None else cases[index]}", now)
    if t == "stop":
        _finish(store, flow, run, "done", "reached a Stop step", now)
        counts["finished"] += 1
        return False
    outcome = _act(store, flow, run, node, lead, now, ctx)
    kind = outcome["kind"]
    if kind == "ok":
        if outcome.get("vars"):
            _change_run(store, run["id"], run["step"], lambda r: {**r, "vars": {**(r.get("vars") or {}), **outcome["vars"]}})
        return _move(store, flow, graph, _run(store, run["id"]), node, "next", outcome["result"], now, note=outcome.get("note", True),
                     data=outcome.get("data"))
    if kind == "refused":
        latest = _run(store, run["id"])
        if next_node(graph, node["id"], "error"):
            return _move(store, flow, graph, latest, node, "error", outcome["result"], now, note=True)
        _finish(store, flow, latest, "stopped", outcome["result"], now)
        counts["stopped"] += 1
        return False
    if kind == "defer":
        _wait(store, _run(store, run["id"]), outcome["until"], now, None, node["id"], deferred=outcome["result"])
        counts["waiting"] += 1
        return False
    return kind == "recheck"


def _act(store, flow, run, node, lead, now, ctx) -> dict:
    step = run["step"]
    act.CURRENT_VARS.set(dict(run.get("vars") or {}))

    def claim(extra=None) -> bool:
        return _change_run(store, run["id"], step, lambda r: None if r.get("claim") else {
            **r, "claim": {"step": step, "node": node["id"], "at": iso(now)}, **(extra or {})}) is not None

    def release(extra=None) -> None:
        _change_run(store, run["id"], step, lambda r: {**r, "claim": None, **(extra or {})})

    if node["type"] == "email":
        return act.do_email(store, flow, run, node, lead, now, ctx, claim, release)
    if not claim():
        return {"kind": "idle", "result": "another tick took this step"}
    if node["type"] == "set_stage":
        return act.do_set_stage(store, flow, node, lead, now, ctx,
                                on_stage=lambda stage: on_event(store, "stage_change", stage, lead["id"], now, exclude_flow=flow["id"]))
    return act.HANDLERS[node["type"]](store, flow, node, lead, now, ctx)


# ---------------------------------------------------------------- reading
def flow_stats(runs: list) -> dict:
    stats = {s: 0 for s in ("running", "waiting", "done", "stopped", "error")}
    variants = {}
    for r in runs:
        stats[r["status"]] = stats.get(r["status"], 0) + 1
        for node_id, variant in (r.get("variants") or {}).items():
            variants.setdefault(node_id, {"a": 0, "b": 0})[variant] += 1
    return {**stats, "total": len(runs), "active_leads": stats["running"] + stats["waiting"], "split": variants}


def present_run(run: dict, flow: Optional[dict], lead: Optional[dict] = None) -> dict:
    current = None
    if flow and run.get("current_node"):
        graph = graph_for_version(flow, run["flow_version"])
        node = next((n for n in graph["nodes"] if n["id"] == run["current_node"]), None)
        current = title_of(node) if node else None
    return {**run, "flow_name": (flow or {}).get("name", "(deleted flow)"), "current_title": current,
            "company": (lead or {}).get("company") or (lead or {}).get("domain")}
