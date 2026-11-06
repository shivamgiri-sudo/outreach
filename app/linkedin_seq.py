"""LinkedIn sequence as a daily task queue for the person who owns the LinkedIn account.

LinkedIn messages are sent by a person (LinkedIn's terms forbid automation, and the account would be restricted).
The app does everything else: picks who to contact today, fills each message for that person (first name and a
hook for their role), schedules the next step once a step is marked sent, waits for the connection to be accepted
before any message, stops on a reply, and hands "yes to a meeting" to the meeting booking flow.

Sequence (from BookMyInterview_LinkedIn_Apollo_Sequence.xlsx): connection note on day 1; after acceptance,
messages on days 3, 7, 12, 16, 21 and 28 (spaced from the acceptance / previous message). Step 4 offers a case study:
it is skipped until a real case-study link is set, because no message may promise material that doesn't exist.
STEPS / PERSONAS below are the built-in defaults; the owner can replace them on the LinkedIn page (app/linkedin_config.py).
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from .activities import add_task, caller_name, iso, now_utc
from .sequences import clean_first_name

DEFAULT_DAILY_CONNECTIONS = 12
NOTE_LIMIT = 300                    # LinkedIn connection notes are limited to 300 characters
CLOSED = ("unsubscribed", "disqualified")

STEPS = [
    {"n": 1, "kind": "connect", "after": "start", "days": 0, "purpose": "Connection request (no pitch)",
     "text": "Hi {first_name}, came across your profile and noticed your involvement in business leadership and talent "
             "development. Always interesting to connect with people who are building and scaling teams. Thought I'd reach out."},
    {"n": 2, "kind": "message", "after": "accepted", "days": 2, "purpose": "Vacant roles delaying business plans",
     "text": "Thanks for connecting, {first_name}.\n\n{hook}\n\nCritical positions remaining open for weeks can put considerable "
             "pressure on existing teams and business timelines.\n\nIs this something you've come across in your organisation?"},
    {"n": 3, "kind": "message", "after": "previous", "days": 4, "purpose": "Candidate-pool quality",
     "text": "{first_name}, there's another interesting dimension to hiring.\n\nSometimes the challenge isn't the number of "
             "applications coming in, but the availability of candidates who genuinely match the role requirements.\n\n"
             "Recruiters screen numerous profiles, interview calendars get filled, and yet the final shortlist can be surprisingly "
             "small.\n\nUnder pressure to close positions, companies sometimes end up compromising on candidate quality.\n\n"
             "Have you noticed this becoming a bigger challenge lately?"},
    {"n": 4, "kind": "message", "after": "previous", "days": 5, "purpose": "Offer the case study", "needs": "case_study_url",
     "text": "{first_name}, this brings me to something I thought you might find interesting.\n\nWe've put together a case study "
             "that looks at hiring from a rather different perspective.\n\nIt explores how companies can rethink candidate "
             "screening, assessment and interview management, ensuring that employers spend more time meeting candidates who have "
             "already been evaluated against the role requirements.\n\nI'd be happy to share it. Would you be interested?"},
    {"n": 5, "kind": "message", "after": "previous", "days": 4, "purpose": "Introduce the platform and ask for a meeting",
     "text": "Thanks, {first_name}.\n\nWhat we've been building at BookMyInterview.ai brings together job description and candidate "
             "profile parsing, role-specific tests, audio/video assessments and candidate self-booking of interview slots.\n\n"
             "The idea is to help employers move beyond simply filling interview calendars and focus on meeting better-assessed "
             "candidates.\n\nWould you be open to a brief 20-minute conversation sometime next week?\n\nhttps://bookmyinterview.ai/"},
    {"n": 6, "kind": "message", "after": "previous", "days": 5, "purpose": "Polite meeting follow-up",
     "text": "Hi {first_name},\n\nJust following up on my earlier note.\n\nI thought a brief conversation could be useful, particularly "
             "around how organisations can improve the quality of candidates reaching the interview stage without slowing down the "
             "hiring process.\n\nIf this is relevant to your current priorities, perhaps we could find 15–20 minutes to connect.\n\n"
             "No pressure if the timing isn't right."},
    {"n": 7, "kind": "message", "after": "previous", "days": 7, "purpose": "Close respectfully",
     "text": "Hi {first_name},\n\nI'll leave it here rather than keep adding to your LinkedIn inbox.\n\nIf improving candidate screening "
             "and hiring quality becomes a priority, I'd be happy to exchange thoughts.\n\nWishing you and your team the best."},
]

BRANCHES = {
    "case_study_yes": "Absolutely, {first_name}. Sharing it here:\n\n{case_study_url}\n\nThe interesting part is how the process can be "
                      "structured so that candidates go through relevant screening and assessments before they reach the employer.\n\n"
                      "I'd be interested to hear what you think.",
    "what_does_it_do": "BookMyInterview.ai helps structure the pre-interview journey. Depending on the role and configured workflow, "
                       "candidates can be assessed through job description and profile parsing, relevant tests and audio/video "
                       "assessments, and book their own interview slots.\n\nThe aim is to give employers better assessment information "
                       "before they meet candidates.",
    "meeting_yes": "Thanks, {first_name}. Appreciate it.\n\nWould {option_1} or {option_2} work for you? I'll send across a calendar "
                   "invitation accordingly.",
    "slot_confirmed": "Perfect, {first_name}. I've noted {date} at {time}.\n\nI'll send across the calendar invitation shortly. "
                      "Looking forward to the conversation.",
}

# Values a person must type before a branch reply can be written (an empty slot would read "Would  or  work?").
BRANCH_NEEDS = {"meeting_yes": ("option_1", "option_2"), "slot_confirmed": ("date", "time")}

# Persona hooks (Personalisation tab): matched on the person's title. HR / talent titles are checked before the
# generic founder/director one, so "HR Director" gets the CHRO hook.
PERSONAS = [
    ("chro", r"\b(chro|chief people|hr director|director.{0,10}hr|vp.{0,10}(hr|people)|head of hr|hr head|people head|human resources)\b",
     "Something I've been thinking about lately: does the pressure to close positions on time ever make it harder to maintain "
     "hiring quality?"),
    ("ta_head", r"\b(talent acquisition|\bta\b|head of talent|talent lead|sourcing)\b",
     "Something I've been thinking about lately: are recruiters spending more time processing applications than identifying "
     "candidates worth interviewing?"),
    ("recruitment_head", r"\b(recruit|hiring manager|staffing)\w*",
     "Something I've been thinking about lately: how much recruiter time is lost when candidates don't attend or don't meet "
     "role requirements?"),
    ("ceo_founder", r"\b(ceo|founder|co-?founder|managing director|md|owner|proprietor|director)\b",
     "Something I've been thinking about lately: how often do business plans get held back simply because the right people "
     "aren't available when they're needed?"),
    ("business_head", r"\b(business head|head of (sales|operations|business)|bu head|vp|general manager|gm)\b",
     "Something I've been thinking about lately: when a critical role stays open, how much pressure does that put on the "
     "existing team?"),
]
DEFAULT_HOOK = ("Something I've been thinking about lately: how often do business plans get held back simply because the right "
                "people aren't available when they're needed?")


# Two openers for the connection request, shared out evenly by lead id so acceptance can be compared fairly.
CONNECT_VARIANTS = {
    "A": STEPS[0]["text"],
    "B": "Hi {first_name}, I work with hiring teams on getting interview time spent only on candidates already screened for "
         "the role. Would be good to connect and swap notes on hiring.",
}


def pick_variant(lead_id: str) -> str:
    return "A" if int(hashlib.sha1(str(lead_id).encode()).hexdigest()[:6], 16) % 2 == 0 else "B"


def persona(title: str, settings: Optional[dict] = None) -> tuple:
    """(persona key, hook) for a job title: the owner's personas when saved (settings "linkedin_sequence"), else the built-in ones."""
    if settings is not None:
        from .linkedin_config import persona_of
        return persona_of(title, settings)
    text = (title or "").lower()
    for key, pattern, hook in PERSONAS:
        if re.search(pattern, text):
            return key, hook
    return "general", DEFAULT_HOOK


def lead_steps(lead: dict, settings: dict) -> list:
    """The steps this lead follows (its own version of the sequence; see app/linkedin_config.py)."""
    from .linkedin_config import steps_for
    return steps_for(lead.get("linkedin_seq") or {}, settings)


def current_steps(settings: dict) -> list:
    from .linkedin_config import current
    return current(settings)["steps"]


def render(step: dict, lead: dict, settings: dict) -> str:
    from .linkedin_config import fill, variants
    first = clean_first_name(lead.get("contact_name")) or "there"
    _, hook = persona(lead.get("title", ""), settings)
    template = step["text"]
    if step["n"] == 1 and step["kind"] == "connect":
        chosen = (lead.get("linkedin_seq") or {}).get("variant")
        if chosen:
            template = variants(settings, [step]).get(chosen) or template
    text = fill(template, lead, settings, first, hook)
    if step["kind"] == "connect" and len(text) > NOTE_LIMIT:
        text = text[: NOTE_LIMIT - 1].rsplit(" ", 1)[0] + "…"
    return text


def render_branch(key: str, lead: dict, settings: dict, **values) -> str:
    first = clean_first_name(lead.get("contact_name")) or "there"
    defaults = {"option_1": "", "option_2": "", "date": "", "time": "", "case_study_url": settings.get("case_study_url", "")}
    return BRANCHES[key].format(first_name=first, **{**defaults, **values})


def _active_steps(settings: dict, steps: Optional[list] = None) -> list:
    return [s for s in (steps if steps is not None else current_steps(settings)) if not s.get("needs") or settings.get(s["needs"])]


def _next_step(state: dict, settings: dict) -> Optional[dict]:
    from .linkedin_config import steps_for
    done = set(state.get("done", []))
    for step in _active_steps(settings, steps_for(state, settings)):
        if step["n"] not in done and step["n"] > max(done, default=0):     # a skipped step never comes back later
            return step
    return None


def _owner(store) -> str:
    return store.read("settings").get("linkedin_owner") or caller_name(store)


def _schedule(store, lead: dict, step: dict, due: datetime, settings: dict) -> dict:
    """A task for the LinkedIn owner holding the ready-to-paste message for this step."""
    text = render(step, lead, settings)
    title = f"LinkedIn step {step['n']}: {'send connection request' if step['kind'] == 'connect' else 'send message'}"
    return add_task(store, lead["id"], "linkedin", text, due_at=due, owner=_owner(store), priority="normal", title=title,
                    source="linkedin_sequence")


def start(store, lead_id: str, now: Optional[datetime] = None) -> dict:
    """Put a lead on the LinkedIn sequence (needs a LinkedIn profile link; never restarts)."""
    now = now or now_utc()
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None:
        raise LookupError("Lead not found")
    if not re.match(r"^https?://([a-z]+\.)?linkedin\.com/", lead.get("linkedin_url") or "", re.I):
        raise ValueError("Add the person's LinkedIn profile link first.")
    if lead.get("stage") in ("unsubscribed", "disqualified"):
        raise ValueError(f"Lead is {lead['stage']}; no LinkedIn outreach.")
    if lead.get("linkedin_seq"):
        raise ValueError("This lead is already on the LinkedIn sequence; it is never restarted.")
    settings = store.read("settings")
    from .linkedin_config import current
    cfg = current(settings)
    state = {"started_at": iso(now), "done": [], "status": "waiting_to_connect", "persona": persona(lead.get("title", ""), settings)[0],
             "variant": pick_variant(lead_id), "steps_version": cfg.get("version", 1)}
    saved = store.update_lead(lead_id, lambda l: None if l.get("linkedin_seq") else {
        **l, "linkedin_seq": state, "timeline": [*l["timeline"], {"at": iso(now), "kind": "linkedin", "text": "Started LinkedIn sequence"}]})
    task = _schedule(store, saved, cfg["steps"][0], now, settings)
    return {"lead": saved, "task": task}


def mark_sent(store, lead_id: str, step_n: int, now: Optional[datetime] = None) -> dict:
    """The person sent step n on LinkedIn. Schedule the next one (message steps wait for the connection)."""
    now = now or now_utc()
    settings = store.read("settings")
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None or not lead.get("linkedin_seq"):
        raise LookupError("Lead is not on the LinkedIn sequence")
    state = lead["linkedin_seq"]
    if state.get("status") in ("stopped", "finished") or lead.get("stage") in CLOSED:
        raise ValueError("The LinkedIn sequence for this lead has ended.")
    _close_linkedin_tasks(store, lead_id, f"Step {step_n} sent")
    done = sorted(set(state.get("done", [])) | {step_n})
    status = "waiting_for_acceptance" if step_n == 1 and not state.get("accepted_at") else "messaging"
    new_state = {**state, "done": done, "status": status, "last_sent_at": iso(now)}
    nxt = _next_step(new_state, settings)
    if nxt is None:
        new_state["status"] = "finished"
    saved = store.update_lead(lead_id, lambda l: {**l, "linkedin_seq": new_state, "last_activity_at": iso(now),
                                                  "timeline": [*l["timeline"], {"at": iso(now), "kind": "linkedin",
                                                                                "text": f"LinkedIn step {step_n} sent"}]})
    task = None
    if nxt and (nxt["after"] != "accepted" or new_state.get("accepted_at")) and status == "messaging":
        task = _schedule(store, saved, nxt, now + timedelta(days=nxt["days"]), settings)
    return {"lead": saved, "next_task": task}


def mark_accepted(store, lead_id: str, now: Optional[datetime] = None) -> dict:
    """They accepted the connection: schedule the first message."""
    now = now or now_utc()
    settings = store.read("settings")
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None or not lead.get("linkedin_seq"):
        raise LookupError("Lead is not on the LinkedIn sequence")
    if lead["linkedin_seq"].get("status") in ("stopped", "finished") or lead.get("stage") in CLOSED:
        raise ValueError("The LinkedIn sequence for this lead has ended.")
    state = {**lead["linkedin_seq"], "accepted_at": iso(now), "status": "messaging", "done": sorted(set(lead["linkedin_seq"].get("done", [])) | {1})}
    saved = store.update_lead(lead_id, lambda l: {**l, "linkedin_seq": state,
                                                  "timeline": [*l["timeline"], {"at": iso(now), "kind": "linkedin", "text": "Connection accepted"}]})
    _close_linkedin_tasks(store, lead_id, "Connection accepted")
    nxt = _next_step(state, settings)
    task = _schedule(store, saved, nxt, now + timedelta(days=nxt["days"]), settings) if nxt else None
    return {"lead": saved, "next_task": task}


def mark_replied(store, lead_id: str, text: str = "", now: Optional[datetime] = None) -> dict:
    """They replied on LinkedIn: stop scheduled steps and ask the person to answer (reply reading runs as for email)."""
    from .inbound import apply_reply
    now = now or now_utc()
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None:
        raise LookupError("Lead not found")
    _close_linkedin_tasks(store, lead_id, "They replied")
    store.update_lead(lead_id, lambda l: {**l, "linkedin_seq": {**(l.get("linkedin_seq") or {}), "status": "stopped", "stopped": "replied",
                                                                "replied_at": iso(now)}})
    result = apply_reply(store, lead_id, text or "(replied on LinkedIn)", "linkedin", None, source="linkedin") if text else None
    if not text:
        add_task(store, lead_id, "follow_up", "They replied on LinkedIn: answer personally.", due_at=now + timedelta(hours=2),
                 owner=_owner(store), priority="high", title="Answer a LinkedIn reply", source="linkedin_sequence")
    return {"label": (result or {}).get("label"), "lead": next(l for l in store.read("leads") if l["id"] == lead_id)}


def stop(store, lead_id: str, why: str = "stopped by a person") -> dict:
    _close_linkedin_tasks(store, lead_id, why)
    return store.update_lead(lead_id, lambda l: {**l, "linkedin_seq": {**(l.get("linkedin_seq") or {}), "status": "stopped", "stopped": why}})


def _close_linkedin_tasks(store, lead_id: str, note: str) -> None:
    stamp = iso(now_utc())
    store.update("tasks", lambda rows: [{**t, "status": "done", "done": True, "done_at": stamp, "done_note": note}
                                        if t.get("lead_id") == lead_id and t.get("kind") == "linkedin" and t.get("status", "open") == "open"
                                        else t for t in rows])


def queue(store, now: Optional[datetime] = None) -> dict:
    """Today's LinkedIn work: due steps (with the filled message) and suggested new people to connect with."""
    now = now or now_utc()
    settings = store.read("settings")
    leads = {l["id"]: l for l in store.read("leads")}
    due = []
    for t in store.read("tasks"):
        if t.get("kind") != "linkedin" or t.get("status", "open") != "open":
            continue
        lead = leads.get(t.get("lead_id")) or {}
        if lead.get("stage") in CLOSED:
            continue                       # unsubscribed (e.g. by the email link) or disqualified: never contacted
        if t.get("due_at") and datetime.fromisoformat(t["due_at"]) > now + timedelta(hours=12):
            continue
        step_n = int(re.search(r"step (\d+)", t.get("title", "")).group(1)) if re.search(r"step (\d+)", t.get("title", "")) else None
        due.append({"task_id": t["id"], "lead_id": lead.get("id"), "company": lead.get("company"), "name": lead.get("contact_name"),
                    "title": lead.get("title"), "linkedin_url": lead.get("linkedin_url"), "open_profile": bool((lead.get("linkedin_profile") or {}).get("open_profile")), "step": step_n, "message": t.get("text", ""),
                    "due_at": t.get("due_at"), "persona": (lead.get("linkedin_seq") or {}).get("persona")})
    started_today = sum(1 for l in leads.values() if (l.get("linkedin_seq") or {}).get("started_at", "")[:10] == iso(now)[:10])
    limit = int(settings.get("linkedin_daily_connections") or DEFAULT_DAILY_CONNECTIONS)
    suggestions = []
    if started_today < limit:
        ranked = sorted((l for l in leads.values() if not l.get("linkedin_seq") and l.get("linkedin_url")
                         and l.get("stage") not in ("unsubscribed", "disqualified", "customer", "advocate")),
                        key=lambda l: -(l.get("open_roles") or 0))
        suggestions = [{"lead_id": l["id"], "company": l.get("company"), "name": l.get("contact_name"), "title": l.get("title"),
                        "persona": persona(l.get("title", ""), settings)[0]} for l in ranked[: limit - started_today]]
    waiting = [{"lead_id": l["id"], "company": l.get("company"), "name": l.get("contact_name"), "linkedin_url": l.get("linkedin_url"),
                "since": l["linkedin_seq"].get("last_sent_at")}
               for l in leads.values() if (l.get("linkedin_seq") or {}).get("status") == "waiting_for_acceptance"]
    return {"due": sorted(due, key=lambda d: d["due_at"] or ""), "suggestions": suggestions, "waiting_for_acceptance": waiting,
            "started_today": started_today, "daily_limit": limit, "case_study_set": bool(settings.get("case_study_url")),
            "owner": _owner(store)}
