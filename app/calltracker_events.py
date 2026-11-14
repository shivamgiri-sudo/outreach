"""DialHalo -> Outreach: the signed webhook receiver and what each event changes.

Signature (DialHalo's existing scheme, server/src/modules/webhooks.service.ts): header
X-DialHalo-Signature (older name X-CallTracker-Signature, same value; both are accepted, the new one is preferred): t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>.<raw body>")>. Refused: a missing or wrong
signature (401), a timestamp more than 5 minutes off (401), the same signature twice (409, a replay). The same event id
delivered again with a new signature (DialHalo's retry) answers 200 and changes nothing.

The receiver URL carries the connection id, so a call is only ever matched to leads of the workspace that owns that
connection: DialHalo's external reference first (the Outreach lead id it was pushed with), then the DialHalo
lead id, then the phone number. The call itself is fetched from DialHalo's API (GET /api/v1/calls/<id>), so the
outcome name, AI summary and links are current whatever order the events arrive in. Audio and transcripts stay in Call
Tracker: Outreach keeps a link to the call page.
"""
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from . import calltracker as ct
from .workspaces import ws_of

TOLERANCE_SECONDS = 300
SIG_KEEP = timedelta(days=1)
EVENT_KEEP = timedelta(days=14)
SEEN_MAX = 5000
MAX_CALLS_PER_EVENT = 50
IST = timezone(timedelta(hours=5, minutes=30))
CALL_EVENTS = ("call.outcome", "calls.synced", "call.transcribed")


# ---------------------------------------------------------------- signature
def sign(secret: str, ts: int, raw: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), f"{ts}.".encode() + raw, hashlib.sha256).hexdigest()


def verify(secret: str, header: str, raw: bytes, now: Optional[float] = None) -> tuple:
    """(ok, reason, signature). reason is a short word for the log: missing / malformed / stale / bad."""
    if not header:
        return False, "missing", ""
    parts = dict(p.split("=", 1) for p in str(header).split(",") if "=" in p)
    try:
        ts = int(parts.get("t", ""))
    except ValueError:
        return False, "malformed", ""
    given = parts.get("v1", "")
    if len(given) != 64:
        return False, "malformed", ""
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    if abs(now - ts) > TOLERANCE_SECONDS:
        return False, "stale", given
    if not hmac.compare_digest(given.encode(), sign(secret, ts, raw).encode()):
        return False, "bad", given
    return True, "", given


def _seen(store, kind: str, value: str, ws: str) -> bool:
    return any(s.get("kind") == kind and s.get("value") == value and s.get("ws") == ws for s in store.read("ct_seen"))


def _remember(store, kind: str, value: str, ws: str) -> None:
    now = ct.now_utc()

    def apply(rows: list) -> list:
        keep = [s for s in rows if (ct.parse(s.get("at")) or now) > now - (SIG_KEEP if s.get("kind") == "sig" else EVENT_KEEP)]
        return [*keep, {"kind": kind, "value": value, "ws": ws, "at": ct.iso(now)}][-SEEN_MAX:]
    store.update("ct_seen", apply)


def handle(store, conn_id: str, header: str, raw: bytes, now: Optional[float] = None) -> tuple:
    """(HTTP status, JSON body) for one delivery. Nothing about the error is echoed beyond a short reason."""
    conn = ct.by_id(store, str(conn_id or "")[:40])
    if not conn or not conn.get("enabled", True) or not conn.get("webhook_secret"):
        return 404, {"detail": "Unknown receiver"}
    ws = conn["workspace"]
    ok, reason, given = verify(conn["webhook_secret"], header, raw, now)
    if not ok:
        ct.note_status(store, ws, "last_event", False, f"Refused a delivery: {reason} signature")
        return 401, {"detail": f"Signature {reason}"}
    if _seen(store, "sig", given, ws):
        return 409, {"detail": "Replayed delivery"}
    _remember(store, "sig", given, ws)
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        return 400, {"detail": "Body is not JSON"}
    event, event_id, data = str(payload.get("event") or ""), str(payload.get("id") or "")[:80], payload.get("data") or {}
    if event_id and _seen(store, "event", event_id, ws):
        return 200, {"ok": True, "duplicate": True}
    ct.FROM_CALLTRACKER.on = True
    try:
        applied = process(store, conn, event, data)
    except ct.CallTrackerError as exc:
        ct.note_status(store, ws, "last_event", False, f"{event}: {exc}")
        if exc.retry:
            return 503, {"detail": "DialHalo API unavailable; retry later"}
        applied = 0
    finally:
        ct.FROM_CALLTRACKER.on = False
    if event_id:
        _remember(store, "event", event_id, ws)
    ct.note_status(store, ws, "last_event", True, f"{event}: {applied} lead(s) updated")
    return 200, {"ok": True, "applied": applied}


def process(store, conn: dict, event: str, data: dict) -> int:
    ws = conn["workspace"]
    if event in ("call.outcome", "call.transcribed"):
        ids = [data.get("callId")]
    elif event == "calls.synced":
        numbers = {ct.norm_phone(p) for l in store.read("leads") if ws_of(l) == ws for p in [l.get("phone"), *(l.get("phones") or [])] if p}
        ids = [c.get("id") for c in data.get("calls") or [] if isinstance(c, dict) and ct.norm_phone(c.get("number_norm")) in numbers]
    elif event == "dnc.added":
        return apply_dnc(store, conn, data)
    elif event == "dnc.removed":
        return note_dnc_removed(store, conn, data)
    else:
        return 0
    applied = 0
    for call_id in [i for i in ids if isinstance(i, int) or str(i or "").isdigit()][:MAX_CALLS_PER_EVENT]:
        try:
            call = ct.ct_request(conn, "GET", f"/api/v1/calls/{int(call_id)}")
        except ct.CallTrackerError as exc:
            if exc.status == 404:
                continue
            raise
        applied += 1 if apply_call(store, conn, call) else 0
    return applied


# ---------------------------------------------------------------- matching
def match_lead(store, ws: str, call: dict) -> Optional[dict]:
    """The workspace's lead for this call, or None. Never looks outside `ws`."""
    from .activities import _normalise_task
    leads = [l for l in store.read("leads") if ws_of(l) == ws]
    ref = call.get("lead") or {}
    if ref.get("external_source") == ct.SOURCE and ref.get("external_id"):
        hit = next((l for l in leads if l["id"] == ref["external_id"]), None)
        if hit:
            return hit
    if ref.get("id"):
        hit = next((l for l in leads if (l.get("calltracker") or {}).get("lead_id") == ref["id"]), None)
        if hit:
            return hit
    number = ct.norm_phone(call.get("number"))
    if len(number) < 6:
        return None
    cands = [l for l in leads if number in {ct.norm_phone(p) for p in [l.get("phone"), *(l.get("phones") or [])] if p}]
    if len(cands) <= 1:
        return cands[0] if cands else None
    open_call = {t.get("lead_id") for t in map(_normalise_task, store.read("tasks")) if t.get("kind") == "call" and t["status"] == "open"}
    return sorted(cands, key=lambda l: (bool((l.get("calltracker") or {}).get("lead_id")), l["id"] in open_call,
                                        str(l.get("last_activity_at") or l.get("created_at") or "")))[-1]


def _at(text) -> str:
    try:
        return ct.iso(datetime.strptime(str(text)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST))
    except ValueError:
        return ct.iso(ct.now_utc())


def _link(conn: dict, url: Optional[str]) -> Optional[str]:
    """DialHalo builds links from its own address (internal when called over 127.0.0.1): use the web address."""
    if not url:
        return None
    base = (conn.get("public_url") or "").rstrip("/")
    if not base:
        return url if url.startswith("https://") else None
    parsed = urlparse(url)
    return f"{base}{parsed.path}" + (f"?{parsed.query}" if parsed.query else "")


def _name_for(store, email: str, fallback: str) -> str:
    from .auth import users
    email = (email or "").lower()
    if email:
        u = next((u for u in users(store) if u["email"].lower() == email), None)
        if u:
            return u.get("name") or u["email"]
        m = next((m for m in store.read("settings").get("team") or [] if isinstance(m, dict) and str(m.get("email") or "").lower() == email), None)
        if m and m.get("name"):
            return m["name"]
    return fallback


def _duration(sec: int) -> str:
    return f"{sec // 60}m {sec % 60}s" if sec >= 60 else f"{sec}s"


# ---------------------------------------------------------------- calls
def apply_call(store, conn: dict, call: dict) -> bool:
    """Put the call on the lead's timeline (once; later events update it) and act on its outcome once."""
    from .activities import add_task, close_call_tasks, now_utc
    from .provenance import build, person
    lead = match_lead(store, conn["workspace"], call)
    if not lead:
        return False
    rule = ct.mapping_of(conn).get(" ".join(str(call.get("outcome") or "").lower().split())) if call.get("outcome") else None
    connected = bool(call.get("connected"))
    direction = "in" if call.get("direction") in ("incoming", "missed", "rejected") else "out"
    outcome = (rule or {}).get("outcome") or ("connected" if connected else "no_answer")
    sec = int(call.get("duration_sec") or 0)
    who = _name_for(store, (call.get("user") or {}).get("email"), (call.get("user") or {}).get("name") or "DialHalo")
    note = (call.get("note") or "").strip()
    summary = (call.get("ai_summary") or "").strip()
    body = "\n".join(x for x in [note, f"AI summary: {summary}" if summary else ""] if x)[:8000]
    links = {k: _link(conn, v) for k, v in (call.get("links") or {}).items()}
    info = {"conn": conn["id"], "call_id": int(call["id"]), "direction": call.get("direction"), "duration_sec": sec, "connected": connected,
            "outcome_name": call.get("outcome") or "", "callback_at": call.get("callback_at"), "has_recording": bool(call.get("has_recording")),
            "links": links, "user_email": (call.get("user") or {}).get("email") or "", "ai_summary": bool(summary)}
    subject = f"DialHalo: {call.get('direction') or 'call'} call, {_duration(sec)}" + (f" — {call['outcome']}" if call.get("outcome") else "")
    at = _at(call.get("started_at"))
    found = {}

    def upsert(rows: list) -> list:
        for r in rows:
            if r.get("lead_id") == lead["id"] and r.get("source") == "calltracker" and (r.get("ct") or {}).get("call_id") == info["call_id"] \
                    and (r.get("ct") or {}).get("conn") == conn["id"]:
                found["row"] = r
                r.update(outcome=outcome, body=body or r.get("body", ""), subject=subject, duration_minutes=round(sec / 60, 1), by=who,
                         ct={**r.get("ct", {}), **info, "applied": r["ct"].get("applied"), "callback_task": r["ct"].get("callback_task")})
                return rows
        row = {"id": uuid.uuid4().hex[:12], "lead_id": lead["id"], "type": "call", "direction": direction, "subject": subject, "body": body,
               "outcome": outcome, "duration_minutes": round(sec / 60, 1), "by": who, "at": at, "created_at": ct.iso(now_utc()),
               "to": call.get("number") or lead.get("phone") or "", "source": "calltracker", "ct": info,
               "origin": build(store, "call_logged", True, lead, person(who, info["user_email"]), channel="call")}
        found["new"] = row
        return [*rows, row]
    store.update("activities", upsert)
    if "new" in found:
        line = f"Call — {outcome.replace('_', ' ')}" + (f": {body[:200]}" if body else "")
        store.update_lead(lead["id"], lambda l: {**l, "last_activity_at": max(str(l.get("last_activity_at") or ""), at),
                                                 "timeline": [*l["timeline"], {"at": at, "kind": "call", "text": line, "by": who}]})
    row = found.get("row") or found["new"]
    applied = (row.get("ct") or {}).get("applied")
    if call.get("outcome") and applied != call["outcome"]:
        act_on_outcome(store, conn, lead["id"], call, rule or {}, who)
        _set_ct(store, row["id"], {"applied": call["outcome"]})
    if connected and call.get("outcome"):
        close_call_tasks(store, lead["id"], f"DialHalo: {call['outcome']} ({who})")
    cb = ct.parse(_at(call["callback_at"])) if call.get("callback_at") else None
    if cb and cb > now_utc() and (row.get("ct") or {}).get("callback_task") != call["callback_at"]:
        add_task(store, lead["id"], "follow_up", f"Call back as agreed on the call ({who})." + (f" {note[:300]}" if note else ""),
                 due_at=cb, owner=who if who != "DialHalo" else None, priority="high", title="Call back (DialHalo)", source="calltracker")
        _set_ct(store, row["id"], {"callback_task": call["callback_at"]})
    return True


def _set_ct(store, activity_id: str, values: dict) -> None:
    store.update("activities", lambda rows: [{**r, "ct": {**r.get("ct", {}), **values}} if r.get("id") == activity_id else r for r in rows])


def act_on_outcome(store, conn: dict, lead_id: str, call: dict, rule: dict, who: str) -> None:
    """Stage, consent, sequence, suppression and alerts for one outcome (the workspace's mapping)."""
    from .activities import add_task, iso as act_iso, now_utc
    from .contact_rules import add_consent, has_consent_for
    from .privacy import record_consent
    from .stages import ALL_STAGES, EXIT_STAGES, STAGES, TERMINAL
    stamp = act_iso(now_utc())
    outcome, target = rule.get("outcome") or "", rule.get("stage") or ""
    why = f"DialHalo outcome '{call.get('outcome')}' ({who})"
    consent = {}

    def change(l: dict):
        changes, events = {}, []
        if outcome == "interested" and not has_consent_for(l, "call"):
            changes["consent"] = add_consent(l, {"text": f"Said yes on a phone call with {who} (DialHalo)", "at": stamp, "source": "phone call", "channel": "call"})
            changes["consent_withdrawn_at"] = None
            consent["text"] = changes["consent"]["text"]
        if outcome in ("wrong_number", "inactive_number") and l.get("phone") and ct.norm_phone(l["phone"]) == ct.norm_phone(call.get("number")):
            changes["phone_status"] = {"status": outcome, "number": l["phone"], "at": stamp, "by": who}
            events.append({"at": stamp, "kind": "phone_flag", "text": f"Phone {l['phone']} marked {outcome.replace('_', ' ')} ({why})", "by": who})
        cur = l["stage"]
        moved = None
        if target and target in ALL_STAGES and cur != target and cur not in TERMINAL:
            forward = target in STAGES and cur in STAGES and STAGES.index(target) > STAGES.index(cur)
            needs_consent = target == "interested" and not (l.get("consent") or changes.get("consent"))
            if (target in EXIT_STAGES or forward) and not needs_consent:
                moved = target
        if moved:
            changes["stage"] = moved
            events.append({"at": stamp, "kind": "stage", "text": f"{cur} → {moved} ({why})", "by": who})
        seq = l.get("sequence")
        if seq and not seq.get("stopped") and (rule.get("stop_sequence") or rule.get("opt_out") or moved == "unsubscribed"):
            changes["sequence"] = {**seq, "stopped": f"calltracker:{outcome or 'outcome'}"}
            events.append({"at": stamp, "kind": "sequence", "text": f"Email sequence stopped ({why})", "by": who})
        if moved == "unsubscribed":
            changes.update(consent=None, consent_withdrawn_at=stamp)
        if not changes and not events:
            return None
        consent["moved"] = moved
        return {**l, **changes, "timeline": [*l["timeline"], *events]}
    saved = store.update_lead(lead_id, change) or {}
    if consent.get("text"):
        record_consent(store, lead_id, "given", "call", consent["text"], "phone call (DialHalo)", "person", who)
    if rule.get("opt_out") or consent.get("moved") == "unsubscribed":
        suppress_contact(store, lead_id, why)
    if outcome == "interested":
        add_task(store, lead_id, "follow_up", "Said yes on the call: send the demo link and set up their first role.",
                 due_at=now_utc() + timedelta(hours=18), owner=who if who != "DialHalo" else None, priority="high",
                 title="Follow up after the yes", source="calltracker", dedupe_open=True)
    if outcome in ("interested", "meeting_booked"):
        try:
            from . import alerts
            alerts.safe(store, "call_interested", lead_id, f"{who} logged a call in DialHalo: {call.get('outcome')}.", managers=True)
        except Exception:
            pass
    if consent.get("moved"):
        try:
            from .workflows import fire
            fire(store, "stage_change", consent["moved"], lead_id)
        except Exception:
            pass
    return saved


def suppress_contact(store, lead_id: str, why: str, unsubscribe: bool = False) -> None:
    """Do-not-contact from DialHalo: email suppressed, sequence stopped, consent ledger row. Never echoed back."""
    from .inbound import suppress
    from .privacy import record_consent
    stamp = ct.iso(ct.now_utc())
    state = {}

    def change(l: dict):
        if l.get("do_not_contact") and (not unsubscribe or l["stage"] == "unsubscribed"):
            return None
        seq = l.get("sequence")
        changes = {"do_not_contact": True, "do_not_contact_at": l.get("do_not_contact_at") or stamp,
                   **({"sequence": {**seq, "stopped": seq.get("stopped") or "calltracker:do_not_contact"}} if seq else {})}
        if unsubscribe and l["stage"] != "unsubscribed":
            changes.update(stage="unsubscribed", consent=None, consent_withdrawn_at=stamp)
        state["email"] = l.get("email")
        return {**l, **changes, "timeline": [*l["timeline"], {"at": stamp, "kind": "do_not_contact", "text": f"Put on the do-not-contact list: {why}"}]}
    was_on = getattr(ct.FROM_CALLTRACKER, "on", False)
    ct.FROM_CALLTRACKER.on = True
    try:
        if store.update_lead(lead_id, change) is None or "email" not in state:
            return
        suppress(store, state["email"])
        record_consent(store, lead_id, "do_not_contact", "any", why[:500], "DialHalo", "provider")
    finally:
        ct.FROM_CALLTRACKER.on = was_on


# ---------------------------------------------------------------- do-not-call events
def _dnc_leads(store, conn: dict, data: dict) -> list:
    ws = conn["workspace"]
    leads = {l["id"]: l for l in store.read("leads") if ws_of(l) == ws}
    ids = [r.get("externalId") for r in data.get("leads") or [] if isinstance(r, dict) and r.get("externalSource") == ct.SOURCE]
    found = [leads[i] for i in ids if i in leads]
    number = ct.norm_phone(data.get("phone"))
    if len(number) >= 6:
        found += [l for l in leads.values() if l not in found and number in {ct.norm_phone(p) for p in [l.get("phone"), *(l.get("phones") or [])] if p}]
    return found


def apply_dnc(store, conn: dict, data: dict) -> int:
    """A number went on DialHalo's do-not-call list: suppress the matching contacts of this workspace."""
    if data.get("source") == ct.SOURCE:
        return 0                                  # we asked for it (an Outreach opt-out); the lead is already suppressed
    why = "on DialHalo's do-not-call list" + (f" ({data['reason']})" if data.get("reason") else "")
    leads = _dnc_leads(store, conn, data)
    for lead in leads:
        suppress_contact(store, lead["id"], why[:300], unsubscribe=True)
    return len(leads)


def note_dnc_removed(store, conn: dict, data: dict) -> int:
    """Taken off DialHalo's list: noted only. An opt-out in Outreach is never undone automatically."""
    leads = _dnc_leads(store, conn, data)
    stamp = ct.iso(ct.now_utc())
    for lead in leads:
        store.update_lead(lead["id"], lambda l: {**l, "timeline": [*l["timeline"], {"at": stamp, "kind": "calltracker",
                          "text": "DialHalo took this number off its do-not-call list. Outreach keeps the opt-out; a person decides."}]})
    return len(leads)


# ---------------------------------------------------------------- funnel numbers
def funnel(store, leads: list, start: Optional[datetime], end: Optional[datetime]) -> dict:
    """Real calls from DialHalo on these (already scoped) leads in [start, end): calls, leads called, connected,
    interested, meetings booked, and leads sent to DialHalo."""
    ids = {l["id"] for l in leads}
    rows = []
    for a in store.read("activities"):
        if a.get("source") != "calltracker" or a.get("lead_id") not in ids:
            continue
        when = ct.parse(a.get("at"))
        if when and ((start and when < start) or (end and when >= end)):
            continue
        rows.append(a)
    distinct = lambda test: len({a["lead_id"] for a in rows if test(a)})
    sent = [l for l in leads if (l.get("calltracker") or {}).get("lead_id")]
    in_range = lambda l: (lambda w: w and (not start or w >= start) and (not end or w < end))(ct.parse((l.get("calltracker") or {}).get("pushed_at")))
    return {"calls": len(rows), "called": distinct(lambda a: a.get("direction") == "out"),
            "connected": distinct(lambda a: (a.get("ct") or {}).get("connected")),
            "interested": distinct(lambda a: a.get("outcome") == "interested"),
            "meeting_booked": distinct(lambda a: a.get("outcome") == "meeting_booked"),
            "sent": len([l for l in sent if in_range(l)]), "sent_total": len(sent)}
