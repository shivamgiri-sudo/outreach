"""Rep workflow: what one sales representative should do today, the power-dial call queue, call outcomes, the call
script, reply triage state and stage moves with a reason.

- "Who am I": the signed-in person's name and email, plus the team member with the same email (tasks are owned by
  team names). A login without an email (this computer / the admin password) acts as the team caller from Settings.
- Leads have no owner field yet everywhere; when `lead.owner` is missing, the owner of the lead's open task counts.
- State that belongs to the whole team (targets, call script, reply triage) lives in settings so it needs no new
  collection: settings.rep_targets, settings.call_script, settings.reply_state.
"""
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from .activities import (ACTIVITY_TYPES, add_task, caller_name, ensure_task_ids, history_summary, iso, log_activity, now_utc,
                         task_buckets, team)
from .sequences import IST
from .stages import ALL_STAGES, EXIT_STAGES, STAGES, TERMINAL, can_move

CLOSED = ("unsubscribed", "disqualified", "not_now", "customer", "advocate")
CALL_STAGES = ("interested", "whatsapp_active")
CONNECTED = ("connected", "interested", "not_interested", "meeting_booked", "call_back")
TARGET_KEYS = ("calls", "connects", "emails", "linkedin", "whatsapp")
DEFAULT_TARGETS = {"calls": 40, "connects": 10, "emails": 20, "linkedin": 15, "whatsapp": 10}
MAX_TARGET = 1000
NEXT_DAY_CALL = time(11, 0)

# The default call script. {{first_name}}, {{rep}}, {{role}} and {{company}} are filled in per lead. Owners edit it in the
# call desk (settings.call_script); nothing here claims results the business does not have.
DEFAULT_SCRIPT = [
    {"title": "Opener", "text": "Hi {{first_name}}, this is {{rep}} from BookMyInterview. You replied about screening for {{role}} at {{company}} — is this a good minute?"},
    {"title": "Opener (Hinglish)", "text": "Hi {{first_name}}, main {{rep}}, BookMyInterview se. Aapne {{role}} ki screening ke baare mein reply kiya tha — kya abhi ek minute baat ho sakti hai?"},
    {"title": "Ask", "text": "How is hiring for {{role}} going right now — what takes most of your team's time?"},
    {"title": "Why us", "text": "We screen every applicant once with a few role-specific questions, so you only interview the right few. Your first role is free, so you can judge it on your own candidates."},
    {"title": "Next step", "text": "Shall I set up your first role with you now? It's free and takes a few minutes."},
]
DEFAULT_OBJECTIONS = [
    {"q": "We use Naukri / another portal", "a": "Keep it. This starts after applications arrive: everyone is screened once and you only meet the best few."},
    {"q": "No time", "a": "Understood. When is a better time? I'll call back then."},
    {"q": "Send details", "a": "Of course. I'll send the sample screen for your role. WhatsApp or email?"},
    {"q": "What does it cost after the free period?", "a": "I don't want to guess on the call. I'll check the current plans and send them in writing. Nothing is charged unless you choose to continue."},
    {"q": "Is candidate data safe?", "a": "It's used only for your hiring and deleted on request, in line with the DPDP Act."},
    {"q": "We have our own HR team / ATS", "a": "Good, keep them. This only does the first screen and the booking, so your team spends time on the right people."},
    {"q": "AI can't judge people", "a": "Agreed. The AI ranks, you decide, and every score comes with a reason you can check."},
    {"q": "How did you get my number?", "a": "From your public job post or company website. If you'd rather not hear from us, I'll delete it right now."},
    {"q": "We're not hiring now", "a": "No problem. Shall I check back in about 2 months?"},
]
PD_NOTE = "Calls are placed by a person, never by a bot or an auto-dialler. Redial missed leads the same day, before 18:55."


# ---------------------------------------------------------------- who am I
def me_of(store, user: Optional[dict]) -> dict:
    """{name, email, names, key, role}: `names` are every string a task owner / `by` field may hold for this person."""
    user = user or {}
    email = (user.get("email") or "").strip().lower()
    names = {n for n in (user.get("name"), user.get("email")) if n}
    for member in team(store):
        if email and (member.get("email") or "").strip().lower() == email:
            names.add(member["name"])
    name = user.get("name") or user.get("email") or ""
    if not email:                                  # this computer / admin password: acts as the team caller
        name = caller_name(store)
        names.add(name)
    return {"name": name, "email": email, "names": names, "key": email or name.lower(), "role": user.get("role") or "owner"}


def actor_name(store, user: Optional[dict]) -> str:
    return me_of(store, user)["name"]


# ---------------------------------------------------------------- time helpers
def parse_at(raw) -> Optional[datetime]:
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def ist_day(raw) -> Optional[str]:
    when = parse_at(raw)
    return when.astimezone(IST).date().isoformat() if when else None


def week_start(now: datetime) -> str:
    local = now.astimezone(IST).date()
    return (local - timedelta(days=local.weekday())).isoformat()


def next_business_morning(now: datetime) -> datetime:
    day = now.astimezone(IST).date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, NEXT_DAY_CALL, tzinfo=IST).astimezone(timezone.utc)


# ---------------------------------------------------------------- targets
def targets_for(store, me: dict) -> dict:
    cfg = store.read("settings").get("rep_targets") or {}
    team_defaults = {**DEFAULT_TARGETS, **(cfg.get("defaults") or {})}
    own = (cfg.get("users") or {}).get(me["key"]) or {}
    return {"targets": {**team_defaults, **own}, "team_defaults": team_defaults, "personal": bool(own)}


def clean_targets(raw: dict) -> dict:
    out = {}
    for key in TARGET_KEYS:
        value = (raw or {}).get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_TARGET:
            raise ValueError(f"{key} must be a whole number between 0 and {MAX_TARGET}.")
        out[key] = value
    return out


def save_targets(store, scope_key: Optional[str], values: dict) -> None:
    """scope_key None = team defaults; otherwise the person's key (email, or name in lower case)."""
    def apply(settings: dict) -> dict:
        cfg = dict(settings.get("rep_targets") or {})
        if scope_key is None:
            cfg["defaults"] = {**(cfg.get("defaults") or {}), **values}
        else:
            users = dict(cfg.get("users") or {})
            users[scope_key] = {**(users.get(scope_key) or {}), **values}
            cfg["users"] = users
        return {**settings, "rep_targets": cfg}
    store.update("settings", apply)


# ---------------------------------------------------------------- what I did
def my_counts(store, names: set, since_day: str, until_day: Optional[str] = None) -> dict:
    """Calls, connects, emails, LinkedIn and WhatsApp done by this person from since_day to until_day (IST days)."""
    def in_range(at) -> bool:
        day = ist_day(at)
        return bool(day) and day >= since_day and (until_day is None or day <= until_day)
    counts = {k: 0 for k in TARGET_KEYS}
    for a in store.read("activities"):
        if a.get("by") not in names or not in_range(a.get("at")):
            continue
        if a.get("type") == "call":
            counts["calls"] += 1
            counts["connects"] += a.get("outcome") in CONNECTED
        elif a.get("type") in ("linkedin", "email", "whatsapp"):
            counts[{"linkedin": "linkedin", "email": "emails", "whatsapp": "whatsapp"}[a["type"]]] += 1
    for m in store.read("outbox"):
        if m.get("by") in names and m.get("status") != "failed" and in_range(m.get("sent_at")):
            counts["emails"] += 1
    for m in store.read("whatsapp_outbox"):
        if m.get("by") in names and m.get("status") != "failed" and in_range(m.get("sent_at")):
            counts["whatsapp"] += 1
    for lead in store.read("leads"):
        for e in lead.get("timeline") or []:
            if e.get("kind") == "linkedin" and e.get("by") in names and in_range(e.get("at")):
                counts["linkedin"] += 1
    return counts


# ---------------------------------------------------------------- next best action
def phone_ok(lead: dict) -> bool:
    flag = lead.get("phone_status") or {}
    return bool(lead.get("phone")) and not (flag.get("status") == "wrong_number" and flag.get("number") == lead.get("phone"))   # a corrected number counts again


def next_action(lead: dict, reply: Optional[dict] = None, task: Optional[dict] = None) -> dict:
    """One suggested move for this lead: call / email / whatsapp / open, with a short reason."""
    has_phone, has_email = phone_ok(lead), bool(lead.get("email"))
    label = (reply or {}).get("label")
    if task and task.get("kind") == "call" and has_phone:
        return {"action": "call", "label": "Call back" if task.get("source") == "callback" else "Call", "why": task.get("title") or "Call task due"}
    if label == "interested":
        return {"action": "call", "label": "Call", "why": "They said yes: call while it is warm."} if has_phone else \
            {"action": "email", "label": "Write email", "why": "They said yes but there is no working phone: reply and ask for a time."}
    if label in ("question", "other", "wrong_person", "not_now") and has_email:
        return {"action": "email", "label": "Reply", "why": "Answer their reply, then ask for a short call."}
    if lead.get("stage") == "interested" and lead.get("consent") and has_phone:
        return {"action": "whatsapp", "label": "WhatsApp", "why": "They agreed to hear from us: send the first WhatsApp."}
    if lead.get("stage") in CALL_STAGES and has_phone:
        return {"action": "call", "label": "Call", "why": "Said yes and waiting for a call."}
    if has_phone and lead.get("consent"):
        return {"action": "call", "label": "Call", "why": "Has a phone number and consent."}
    if has_email and lead.get("stage") not in CLOSED:
        return {"action": "email", "label": "Write email", "why": "No call possible yet: a short personal email."}
    return {"action": "open", "label": "Open", "why": "Check the lead and decide."}


# ---------------------------------------------------------------- call queue
def called_today(store_activities: list, lead: dict, today: str) -> bool:
    if any(a.get("lead_id") == lead["id"] and a.get("type") == "call" and ist_day(a.get("at")) == today for a in store_activities):
        return True
    return any(e.get("kind") == "call" and ist_day(e.get("at")) == today for e in lead.get("timeline") or [])


def _last_touch(lead: dict) -> Optional[str]:
    stamps = [e.get("at") for e in lead.get("timeline") or [] if e.get("kind") not in ("imported", "edit", "stage", "enriched", "verified")]
    stamps = [s for s in stamps if parse_at(s)]
    return max(stamps, key=parse_at) if stamps else lead.get("last_activity_at")


def lead_card(lead: dict, presented: Optional[dict] = None) -> dict:
    presented = presented or {}
    notes = [e for e in lead.get("timeline") or [] if e.get("kind") in ("note", "call", "consent", "reply")][-3:]
    return {"lead_id": lead["id"], "company": lead.get("company") or lead.get("domain") or "", "contact_name": lead.get("contact_name") or "",
            "title": lead.get("title") or "", "role": lead.get("role") or "", "phone": lead.get("phone") or "",
            "phone_ok": phone_ok(lead), "email": lead.get("email") or "", "stage": lead.get("stage"),
            "score": presented.get("score"), "band": presented.get("band"), "consent": bool(lead.get("consent")),
            "owner": lead.get("owner"), "last_touch": _last_touch(lead), "custom": lead.get("custom") or {},
            "recent_notes": [{"at": e.get("at"), "kind": e.get("kind"), "text": (e.get("text") or "")[:300], "by": e.get("by", "")} for e in reversed(notes)]}


def call_queue(store, present, now: datetime, names: Optional[set] = None) -> list:
    """Who to dial, best first: overdue callbacks, call tasks due today, leads who said yes, then leads with a phone
    and consent. Leads that are closed, on the do-not-contact list or with a phone marked wrong are left out.
    With `names`, only tasks owned by them (or nobody) and leads owned by them (or nobody) are kept."""
    ensure_task_ids(store)
    today = now.astimezone(IST).date().isoformat()
    leads = {l["id"]: l for l in store.read("leads")}
    suppressed = set(store.read("suppressed"))
    activities = store.read("activities")
    open_tasks = [t for t in store.read("tasks") if t.get("status", "open") == "open" and not t.get("done")]
    task_owner = {t.get("lead_id"): t.get("owner") for t in open_tasks if t.get("lead_id")}

    def usable(lead: Optional[dict]) -> bool:
        return bool(lead) and lead.get("stage") not in CLOSED and phone_ok(lead) and (lead.get("email") or "") not in suppressed

    def mine(owner) -> bool:
        return names is None or not owner or owner in names

    rows, seen = [], set()
    for t in open_tasks:
        if t.get("kind") != "call" or not mine(t.get("owner")):
            continue
        lead = leads.get(t.get("lead_id"))
        due = parse_at(t.get("due_at"))
        if not usable(lead) or (due and due.astimezone(IST).date().isoformat() > today):
            continue
        overdue = bool(due) and due < now
        callback = t.get("source") == "callback"
        rank = 0 if callback and overdue else 1 if callback else 2
        rows.append((rank, -(present(lead).get("score") or 0), t.get("due_at") or "", lead, t,
                     ("Call back (overdue)" if overdue else "Call back") if callback else ("Call task (overdue)" if overdue else "Call task due today")))
        seen.add(lead["id"])
    for lead in leads.values():
        if lead["id"] in seen or not usable(lead) or not mine(lead.get("owner") or task_owner.get(lead["id"])):
            continue
        if called_today(activities, lead, today):
            continue
        if lead.get("stage") in CALL_STAGES:
            rank, why = 3, "Said yes — waiting for a call"
        elif lead.get("consent"):
            rank, why = 4, "Has a phone and consent"
        else:
            continue
        rows.append((rank, -(present(lead).get("score") or 0), "", lead, None, why))
        seen.add(lead["id"])
    rows.sort(key=lambda r: (r[0], r[2] or "9999", r[1]))
    out = []
    for rank, _score, _due, lead, task, why in rows:
        p = present(lead)
        out.append({**lead_card(lead, p), "reason": why, "rank": rank, "task_id": task.get("id") if task else None,
                    "due_at": task.get("due_at") if task else None, "task_owner": task.get("owner") if task else None,
                    "next": next_action(lead, task=task)})
    return out


# ---------------------------------------------------------------- call outcomes
class OutcomeRefused(ValueError):
    """Not allowed; the message says why in plain words."""


def record_call_outcome(store, lead_id: str, outcome: str, notes: str, who: str, callback_at: Optional[datetime] = None,
                        now: Optional[datetime] = None) -> dict:
    """Log one call (by `who`) and do what the outcome means. Returns {activity, task, stage, consent_recorded, phone_flagged}."""
    from .privacy import record_consent
    now = now or now_utc()
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None:
        raise LookupError("Lead not found")
    if outcome == "call_back" and callback_at is None:
        raise OutcomeRefused("Pick when to call back.")
    if outcome == "call_back" and callback_at <= now - timedelta(minutes=1):
        raise OutcomeRefused("The call-back time is in the past.")
    activity = log_activity(store, lead_id, "call", {"outcome": outcome, "notes": notes, "by": who})
    result = {"activity": activity, "task": None, "stage": lead["stage"], "consent_recorded": False, "phone_flagged": False}
    stamp = iso(now)
    if outcome == "call_back":
        result["task"] = add_task(store, lead_id, "call", notes or "They asked to be called back.", due_at=callback_at, owner=who,
                                  priority="high", title="Call back", source="callback")
    elif outcome == "wrong_number":
        number = lead.get("phone") or ""
        store.update_lead(lead_id, lambda l: {**l, "phone_status": {"status": "wrong_number", "number": number, "at": stamp, "by": who},
                                              "timeline": [*l["timeline"], {"at": stamp, "kind": "phone_flag", "text": f"Phone {number} marked wrong number (by {who})", "by": who}]})
        result["phone_flagged"] = True
    elif outcome == "interested":
        def yes(l: dict) -> dict:
            changes = {}
            if not l.get("consent"):
                changes["consent"] = {"text": f"Said yes on a phone call with {who}" + (f": {notes[:300]}" if notes else ""), "at": stamp,
                                      "source": "phone call", "channel": "call"}
                changes["consent_withdrawn_at"] = None
                result["consent_recorded"] = True
            has_consent = bool(l.get("consent") or changes.get("consent"))
            before_yes = l["stage"] in STAGES and STAGES.index(l["stage"]) < STAGES.index("interested")
            if before_yes and can_move(l["stage"], "interested", has_consent=has_consent):
                changes["stage"] = "interested"
                changes["timeline"] = [*l["timeline"], {"at": stamp, "kind": "stage", "text": f"{l['stage']} → interested (call, by {who})", "by": who}]
            return {**l, **changes}
        saved = store.update_lead(lead_id, yes)
        if result["consent_recorded"]:
            record_consent(store, lead_id, "given", "call", saved["consent"]["text"], "phone call")
        result["stage"] = saved["stage"]
        result["task"] = add_task(store, lead_id, "follow_up", "Said yes on the call: send the demo link and set up their first role.",
                                  due_at=next_business_morning(now), owner=who, priority="high", title="Follow up after the yes", source="activity")
    elif outcome == "not_interested":
        def no(l: dict) -> dict:
            if l["stage"] in CLOSED or not can_move(l["stage"], "disqualified"):
                return None
            seq = l.get("sequence")
            return {**l, "stage": "disqualified", **({"sequence": {**seq, "stopped": seq.get("stopped") or "not_interested"}} if seq else {}),
                    "timeline": [*l["timeline"], {"at": stamp, "kind": "stage", "text": f"{l['stage']} → disqualified (not interested on a call, by {who})", "by": who}]}
        store.update_lead(lead_id, no)
        result["stage"] = next(l for l in store.read("leads") if l["id"] == lead_id)["stage"]
    return result


# ---------------------------------------------------------------- call script
def call_script(store) -> dict:
    saved = store.read("settings").get("call_script") or {}
    return {"script": saved.get("script") or DEFAULT_SCRIPT, "objections": saved.get("objections") or DEFAULT_OBJECTIONS,
            "note": PD_NOTE, "custom": bool(saved), "updated_by": saved.get("updated_by"), "updated_at": saved.get("updated_at")}


def clean_script(script: list, objections: list) -> tuple:
    if not isinstance(script, list) or not isinstance(objections, list) or len(script) > 30 or len(objections) > 60:
        raise ValueError("Keep the script to 30 lines and 60 answers at most.")
    lines = [{"title": str(s.get("title", "")).strip()[:80], "text": str(s.get("text", "")).strip()[:1500]} for s in script if isinstance(s, dict)]
    answers = [{"q": str(o.get("q", "")).strip()[:200], "a": str(o.get("a", "")).strip()[:1500]} for o in objections if isinstance(o, dict)]
    lines = [s for s in lines if s["text"]]
    answers = [o for o in answers if o["q"] and o["a"]]
    if not lines:
        raise ValueError("The script needs at least one line.")
    return lines, answers


# ---------------------------------------------------------------- stage moves with a reason
def stage_rule(current: str, target: str, has_consent: bool) -> str:
    """'yes' (a normal move), 'reason' (a back-move: allowed with a reason), 'owner' (only an owner, with a reason),
    or 'no' (never: unsubscribed is final, Interested needs recorded consent, unknown stage)."""
    if current not in ALL_STAGES or target not in ALL_STAGES or current == target or current in TERMINAL:
        return "no"
    if target == "unsubscribed":
        return "yes"
    if target == "interested" and not has_consent:
        return "no"
    if can_move(current, target, has_consent=has_consent):
        return "yes"
    if current in EXIT_STAGES and target in STAGES:
        return "reason"
    if current in STAGES and target in STAGES and STAGES.index(target) < STAGES.index(current):
        return "reason"
    return "owner"


def stage_options(lead: dict, is_owner: bool) -> list:
    consent = bool(lead.get("consent"))
    out = []
    for stage in ALL_STAGES:
        rule = stage_rule(lead["stage"], stage, consent)
        if stage == lead["stage"]:
            continue
        why = {"yes": "", "reason": "Going back: say why.", "owner": "Skips steps: only an owner can force it, with a reason.",
               "no": "Unsubscribed is final." if lead["stage"] in TERMINAL else "Needs recorded consent first." if stage == "interested" else "Not allowed."}[rule]
        out.append({"stage": stage, "rule": rule, "allowed": rule in ("yes", "reason") or (rule == "owner" and is_owner), "why": why})
    return out


__all__ = ["me_of", "actor_name", "call_queue", "record_call_outcome", "call_script", "clean_script", "stage_rule", "stage_options",
           "next_action", "my_counts", "targets_for", "save_targets", "clean_targets", "lead_card", "history_summary", "task_buckets",
           "ACTIVITY_TYPES", "OutcomeRefused"]
