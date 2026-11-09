"""Why each outbound message went out: who sent it (a person) or which rule did (the System), and the chain behind it.

Every email, WhatsApp, LinkedIn step, call log and internal email written from 9 Nov 2026 carries an `origin` dict:

    type       "manual" (a person did it) or "automatic" (the System did it)
    trigger    one_off, send_now, sequence, flow, linkedin_agent, linkedin_by_hand, whatsapp_by_hand, whatsapp_auto,
               call_logged, alert, report, password_reset, reminder, invite
    actor      {"name", "email"} of the person; {"name": "System"} for automatic sends
    rule       which sequence step / flow node / campaign made it go
    enrolment  who put the lead on that sequence or flow, how and when (from the lead's own state)
    mode       "live" or "practice"
    settings   what allowed it at that moment: live sending, daily cap, business hours, open tracking, consent basis

Rows written before then have no origin. `describe` labels them "Sent before audit tracking (9 Nov 2026)" and adds only
facts the data holds (the row's `by`, the lead's "Enrolled in email sequence" timeline line); it never guesses.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from .contact_rules import has_consent_for

IST = timezone(timedelta(hours=5, minutes=30))
SYSTEM = "System"
LEGACY_LABEL = "Sent before audit tracking (9 Nov 2026)"
MANUAL_TRIGGERS = {"one_off", "send_now", "linkedin_by_hand", "whatsapp_by_hand", "call_logged", "activity_logged", "report"}
TRIGGER_TEXT = {
    "one_off": "a one-off email a person wrote",
    "send_now": "a person pressed \"Send email now\" on a sequence step",
    "sequence": "a scheduled sequence step (sent by the sequence runner)",
    "flow": "a step of an automatic flow",
    "linkedin_agent": "the LinkedIn agent sent it from a connected account",
    "linkedin_by_hand": "a person sent it on LinkedIn and marked it sent",
    "whatsapp_by_hand": "a person sent this WhatsApp from the lead",
    "whatsapp_auto": "an automatic WhatsApp (flow step)",
    "call_logged": "a person logged this call",
    "activity_logged": "a person logged this activity",
    "alert": "an automatic alert to the team",
    "report": "a person asked for this report by email",
    "password_reset": "someone asked for a password reset link (Forgot password)",
    "reminder": "an automatic reminder",
    "invite": "a meeting invite",
}
VIA = ("by hand", "bulk action", "campaign auto-enrol", "import", "flow trigger", "daily auto-start")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def person(name: str = "", email: str = "") -> dict:
    return {"name": str(name or "").strip() or str(email or "").strip() or "Unknown person", "email": str(email or "").strip()}


def person_of(user: Optional[dict]) -> dict:
    user = user or {}
    return person(user.get("name") or "", user.get("email") or "")


def system(**extra) -> dict:
    return {"name": SYSTEM, "email": "", **{k: v for k, v in extra.items() if v}}


def enrol_fields(by: Optional[str] = None, by_email: str = "", via: str = "by hand", **extra) -> dict:
    """What a sequence state / flow run / LinkedIn state keeps about who started it."""
    return {"enrolled_by": str(by or "").strip() or SYSTEM, "enrolled_by_email": str(by_email or "").strip(), "enrolled_via": via,
            **{f"enrolled_{k}": v for k, v in extra.items() if v}}


def enrolment_from(state: Optional[dict]) -> Optional[dict]:
    """{by, by_email, via, at} from a lead's sequence state or a flow run. None when there is no state; by/via are None
    for state saved before enrolment tracking (never filled in by guessing)."""
    if not state:
        return None
    out = {"by": state.get("enrolled_by"), "by_email": state.get("enrolled_by_email") or "", "via": state.get("enrolled_via"),
           "at": state.get("enrolled_at") or state.get("started_at")}
    for key in ("campaign_id", "detail"):
        if state.get(f"enrolled_{key}"):
            out[key] = state[f"enrolled_{key}"]
    return out


def linkedin_enrolment(lead: Optional[dict]) -> Optional[dict]:
    """Who started the lead's LinkedIn sequence: recorded fields, else the daily auto-start flag, else not recorded."""
    state = (lead or {}).get("linkedin_seq")
    if not state:
        return None
    found = enrolment_from(state)
    if not found.get("by") and state.get("auto_started"):
        found.update({"by": SYSTEM, "via": "daily auto-start"})
    return found


def stamp_linkedin_start(store, lead_id: str, by: str, by_email: str, via: str, detail: str = "") -> Optional[dict]:
    """Record who started a lead's LinkedIn sequence (kept outside linkedin_seq.start, which others edit)."""
    def apply(lead: dict):
        state = lead.get("linkedin_seq")
        if not state or state.get("enrolled_by") or state.get("auto_started"):
            return None
        return {**lead, "linkedin_seq": {**state, **enrol_fields(by, by_email, via, detail=detail)}}
    return store.update_lead(lead_id, apply)


def consent_basis(lead: Optional[dict], channel: str) -> str:
    lead = lead or {}
    if channel == "email":
        return ("recorded yes for email" if has_consent_for(lead, "email")
                else "cold email rule (business contact, unsubscribe link in every email)")
    if channel == "whatsapp":
        return "recorded yes for WhatsApp" if has_consent_for(lead, "whatsapp") else "no recorded WhatsApp yes"
    if channel == "linkedin":
        return "LinkedIn connection / message on the platform"
    if channel == "sms":
        return "recorded yes for SMS" if has_consent_for(lead, "sms") else "no recorded SMS yes"
    if channel == "internal":
        return "internal email to a team login"
    return ""


def settings_snapshot(store, lead: Optional[dict], channel: str, live: bool, cfg: Optional[dict] = None, hours: str = "") -> dict:
    settings = cfg if cfg is not None else store.read("settings")
    snap = {"live_sending": bool(live), "consent_basis": consent_basis(lead, channel)}
    if channel == "email":
        from .sequences import DEFAULT_DAILY_CAP
        snap.update({"live_switch": bool(settings.get("live_sending")), "daily_cap": settings.get("daily_cap") or DEFAULT_DAILY_CAP,
                     "track_email": settings.get("track_email") is not False})
    elif channel == "whatsapp":
        snap["live_switch"] = bool(settings.get("live_whatsapp"))
    if hours:
        snap["business_hours"] = hours
    return snap


def build(store, trigger: str, live: bool, lead: Optional[dict] = None, actor: Optional[dict] = None, rule: Optional[dict] = None,
          enrolment: Optional[dict] = None, channel: str = "email", cfg: Optional[dict] = None, hours: str = "") -> dict:
    manual = trigger in MANUAL_TRIGGERS
    return {"type": "manual" if manual else "automatic", "trigger": trigger,
            "actor": (actor or person()) if manual else (actor if actor and actor.get("name") == SYSTEM else system()),
            "rule": {k: v for k, v in (rule or {}).items() if v not in (None, "")}, "enrolment": enrolment,
            "mode": "live" if live else "practice", "settings": settings_snapshot(store, lead, channel, live, cfg, hours),
            "recorded_at": now_iso()}


HOURS_CHECKED = "Mon–Fri 9–18 IST (checked before sending)"
HOURS_PERSON = "not applied: a person chose to send it"


def for_sequence(store, lead: dict, seq: dict, step: dict, day, live: bool, cfg: dict, trigger: str = "sequence",
                 actor: Optional[dict] = None) -> dict:
    from . import sequence_library
    seq_id = seq.get("sequence_id") or sequence_library.DEFAULT_ID
    found = sequence_library.get_sequence(store, seq_id) or {}
    rule = {"sequence_id": seq_id, "sequence_name": found.get("name") or ("Default sequence" if seq_id == sequence_library.DEFAULT_ID else ""),
            "version": seq.get("version", 1), "step": step.get("name"), "day": day,
            "campaign_id": seq.get("enrolled_campaign_id") or (lead or {}).get("campaign_id")}
    return build(store, trigger, live, lead, actor, rule, enrolment_from(seq), "email", cfg,
                 HOURS_PERSON if trigger == "send_now" else HOURS_CHECKED)


def flow_rule(flow: dict, node: Optional[dict] = None, run: Optional[dict] = None) -> dict:
    from .flows import title_of
    return {"flow_id": flow.get("id"), "flow_name": flow.get("name"), "flow_version": (run or {}).get("flow_version") or flow.get("version"),
            "node_id": (node or {}).get("id"), "node": title_of(node) if node else None, "run_id": (run or {}).get("id")}


def internal(trigger: str, actor: Optional[dict] = None, live: bool = True, rule: Optional[dict] = None) -> dict:
    """Origin for an internal / transactional email (alerts, reports, resets, reminders)."""
    manual = trigger in MANUAL_TRIGGERS
    return {"type": "manual" if manual else "automatic", "trigger": trigger, "actor": (actor or person()) if manual else system(),
            "rule": rule or {}, "enrolment": None, "mode": "live" if live else "practice",
            "settings": {"consent_basis": consent_basis(None, "internal")}, "recorded_at": now_iso()}


INTERNAL_KIND_TRIGGER = {"internal": "alert", "meeting_reminder": "reminder", "reminder": "reminder", "report": "report",
                         "password_reset": "password_reset", "invite": "invite", "meeting_invite": "invite", "meeting_cancel": "invite"}


# ---------------------------------------------------------------- reading it back
def _when(value) -> str:
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


def origin_type(row: dict) -> str:
    """'automatic', 'manual' or 'unknown' (an old row whose sender the data does not say)."""
    origin = row.get("origin")
    if isinstance(origin, dict) and origin.get("type") in ("automatic", "manual"):
        return origin["type"]
    if row.get("by") and row.get("by") != "flow":
        return "manual"
    if row.get("flow_id") or row.get("by") == "flow":
        return "automatic"
    return "unknown"


def _who(actor: Optional[dict]) -> str:
    actor = actor or {}
    name = actor.get("name") or "Unknown person"
    return f"{name} ({actor['email']})" if actor.get("email") and actor.get("email") != name else name


def _rule_line(rule: dict) -> str:
    if rule.get("sequence_id"):
        name = rule.get("sequence_name") or rule["sequence_id"]
        day = f", day {rule['day']}" if rule.get("day") not in (None, "") else ""
        return f"Rule: sequence “{name}” version {rule.get('version', 1)}, step “{rule.get('step') or '?'}”{day}."
    if rule.get("flow_id"):
        node = f", step “{rule['node']}”" if rule.get("node") else ""
        return f"Rule: flow “{rule.get('flow_name') or rule['flow_id']}”{node}."
    if rule.get("campaign_id"):
        return f"Rule: campaign {rule.get('campaign_name') or rule['campaign_id']}."
    if rule.get("account"):
        return f"LinkedIn account: {rule['account']}."
    return ""


def _enrolment_line(enrolment: Optional[dict]) -> str:
    if not enrolment:
        return ""
    if not enrolment.get("by") and not enrolment.get("via"):
        when = _when(enrolment.get("at"))
        return f"The lead was started{(' on ' + when) if when else ''}; who started it was not recorded then (before 9 Nov 2026)."
    who = SYSTEM if enrolment.get("by") == SYSTEM else _who({"name": enrolment.get("by"), "email": enrolment.get("by_email")})
    when = _when(enrolment.get("at"))
    detail = f" ({enrolment['detail']})" if enrolment.get("detail") else ""
    return f"Put on it by {who}, {enrolment.get('via') or 'how not recorded'}{detail}{(' on ' + when) if when else ''}."


def _settings_line(s: dict) -> str:
    parts = []
    if "live_sending" in s:
        parts.append("live sending on" if s["live_sending"] else "practice mode (nothing left the app)")
    if s.get("daily_cap"):
        parts.append(f"daily cap {s['daily_cap']}")
    if s.get("business_hours"):
        parts.append(f"business hours: {s['business_hours']}")
    if "track_email" in s:
        parts.append("open/click tracking " + ("on" if s["track_email"] else "off"))
    if s.get("consent_basis"):
        parts.append(f"consent: {s['consent_basis']}")
    return ("Allowed because: " + "; ".join(parts) + ".") if parts else ""


def _parse(at) -> Optional[datetime]:
    try:
        t = datetime.fromisoformat(str(at))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _timeline_enrolment(lead: Optional[dict], sent_at=None) -> str:
    """The latest "Enrolled ..." timeline line at or before the send (a later re-enrolment did not cause this email)."""
    sent = _parse(sent_at)
    best = None
    for e in (lead or {}).get("timeline") or []:
        text = str(e.get("text") or "")
        if not text.startswith("Enrolled"):          # "Enrolled in email sequence ...", "Enrolled from prospect search ..."
            continue
        at = _parse(e.get("at"))
        if sent and at and at > sent:
            continue
        if best is None or (at and (_parse(best.get("at")) or at) <= at):
            best = e
    if best is None:
        return ""
    who = f" (by {best['by']})" if best.get("by") else ""
    return f"Lead timeline: “{best.get('text')}”{who} on {_when(best.get('at'))}."


def describe(row: dict, lead: Optional[dict] = None, flows: Optional[dict] = None) -> dict:
    """{type, label, legacy, actor, trigger, why: [plain-English lines]} for one outbound row."""
    origin = row.get("origin") if isinstance(row.get("origin"), dict) else None
    kind = origin_type(row)
    label = {"automatic": "Automatic", "manual": "Manual"}.get(kind, "Before tracking")
    if origin:
        actor = origin.get("actor") or {}
        lines = [f"Sent by {_who(actor)} — {TRIGGER_TEXT.get(origin.get('trigger'), origin.get('trigger') or '')}." if kind == "manual"
                 else f"Sent automatically by the System — {TRIGGER_TEXT.get(origin.get('trigger'), origin.get('trigger') or '')}."]
        lines += [x for x in (_rule_line(origin.get("rule") or {}), _enrolment_line(origin.get("enrolment")),
                              _settings_line(origin.get("settings") or {})) if x]
        if origin.get("mode") == "practice":
            lines.append("Practice run: nothing actually left the app.")
        return {"type": kind, "label": label, "legacy": False, "actor": actor.get("name") or "", "trigger": origin.get("trigger") or "",
                "why": lines}
    lines = [LEGACY_LABEL + "."]
    if row.get("by") and row.get("by") != "flow":
        lines.append(f"The row says it was sent by {row['by']}.")
    if row.get("flow_id"):
        name = (flows or {}).get(row["flow_id"]) or row["flow_id"]
        lines.append(f"The row belongs to flow “{name}”.")
    if row.get("sequence_id"):
        lines.append(f"The row belongs to saved sequence {row['sequence_id']} version {row.get('sequence_version', 1)}.")
    seen = _timeline_enrolment(lead, row.get("sent_at"))
    if seen:
        lines.append(seen)
    return {"type": kind, "label": label, "legacy": True, "actor": row.get("by") if kind == "manual" else (SYSTEM if kind == "automatic" else ""),
            "trigger": "", "why": lines}


def short_who(row: dict) -> str:
    """'by Asha', 'System (flow Welcome)' or '' (an old row that does not say), for lists of messages and calls."""
    kind = origin_type(row)
    origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
    if kind == "manual":
        return f"by {(origin.get('actor') or {}).get('name') or row.get('by')}"
    if kind == "automatic":
        rule = origin.get("rule") or {}
        what = (f"flow {rule.get('flow_name')}" if rule.get("flow_name") else f"sequence {rule.get('sequence_name')}" if rule.get("sequence_name")
                else TRIGGER_TEXT.get(origin.get("trigger"), "") if origin else "flow")
        return f"{SYSTEM} ({what})" if what else SYSTEM
    return ""


def columns(row: dict) -> dict:
    """Flat provenance columns for the CSV export."""
    origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
    actor, rule, enrol, s = origin.get("actor") or {}, origin.get("rule") or {}, origin.get("enrolment") or {}, origin.get("settings") or {}
    kind = origin_type(row)
    return {"origin_type": kind, "trigger": origin.get("trigger") or "", "actor_name": actor.get("name") or (row.get("by") if kind == "manual" else ""),
            "actor_email": actor.get("email") or "", "rule_sequence": rule.get("sequence_name") or rule.get("sequence_id") or "",
            "rule_version": rule.get("version") or "", "rule_step": rule.get("step") or rule.get("node") or "",
            "rule_day": rule.get("day") if rule.get("day") is not None else "", "rule_flow": rule.get("flow_name") or rule.get("flow_id") or row.get("flow_id") or "",
            "rule_campaign": rule.get("campaign_id") or "", "enrolled_by": enrol.get("by") or "", "enrolled_by_email": enrol.get("by_email") or "",
            "enrolled_via": enrol.get("via") or "", "enrolled_detail": enrol.get("detail") or "", "enrolled_at": enrol.get("at") or "", "mode": origin.get("mode") or "",
            "live_sending": s.get("live_sending") if "live_sending" in s else "", "daily_cap": s.get("daily_cap") or "",
            "business_hours": s.get("business_hours") or "", "track_email": s.get("track_email") if "track_email" in s else "",
            "consent_basis": s.get("consent_basis") or "", "legacy": not origin}
