"""Free-experience activation: where each employer is on the path to a real result."""
from datetime import datetime
from typing import Optional

STEPS = ("invited", "opened", "drafted", "published", "first_candidate", "first_booking", "converted")
EVENT_STEP = {
    "demo_opened": "opened",
    "role_drafted": "drafted",
    "role_published": "published",
    "candidate_screened": "first_candidate",
    "interview_booked": "first_booking",
    "converted": "converted",
}
USAGE_EVENTS = {"candidate_screened": "candidates", "interview_booked": "bookings"}
CONVERSION_WARNING_RATIO = 0.8
WARNING_DAYS_BEFORE_END = 7
TRIAL_CLOCK_STEPS = STEPS[STEPS.index("published"):]

# (minimum days in this step, text shown to the person working the lead)
NUDGES = {
    "invited": (3, "They have not opened the demo. Resend the demo link, on WhatsApp if they said yes."),
    "opened": (1, "They looked at the demo but did not draft a role. Offer a 10-minute walkthrough (walkthrough call)."),
    "drafted": (1, "Role is drafted but not live. Help them publish it today."),
    "published": (5, "No candidates yet. Share the apply link, and offer to screen their existing applicants free."),
    "first_candidate": (2, "Candidates are screened. Show the ranked shortlist and help book the first interview."),
    "first_booking": (0, "First interview booked. Ask how it went and request a short story (with their permission)."),
}


def new_activation(now: datetime) -> dict:
    return {"step": "invited", "events": [], "usage": {"candidates": 0, "bookings": 0}, "since": now.isoformat()}


def apply_event(activation: dict, event: str, now: datetime, count: int = 1) -> dict:
    """Return a new activation record; the step only ever moves forward."""
    if event not in EVENT_STEP:
        raise ValueError(f"Unknown event: {event}")
    usage = dict(activation["usage"])
    if event in USAGE_EVENTS:
        usage[USAGE_EVENTS[event]] += max(0, count)
    target = EVENT_STEP[event]
    moved = STEPS.index(target) > STEPS.index(activation["step"])
    step = target if moved else activation["step"]
    updated = {**activation, "step": step, "usage": usage,
               "events": [*activation["events"], {"at": now.isoformat(), "event": event, "count": count}]}
    if moved:
        updated["since"] = now.isoformat()
        if step == "published" and not activation.get("started"):
            updated["started"] = now.isoformat()
    return updated


def _days_since(iso: str, now: datetime) -> int:
    return (now - datetime.fromisoformat(iso)).days


def next_nudge(activation: dict, now: datetime) -> Optional[dict]:
    rule = NUDGES.get(activation["step"])
    if not rule:
        return None
    min_days, text = rule
    return {"text": text} if _days_since(activation["since"], now) >= min_days else None


def conversion_due(activation: dict, offer: dict, now: datetime, bonus: int = 0) -> Optional[dict]:
    """Early, honest heads-up that the free allowance is nearly used or time is nearly up."""
    if activation["step"] == "converted":
        return None
    usage = activation["usage"]
    cand_cap = offer["candidate_cap"] + bonus
    if usage["candidates"] >= CONVERSION_WARNING_RATIO * cand_cap:
        return {"reason": f"{usage['candidates']} of {cand_cap} free candidates used",
                "text": "Tell them early that the free allowance is almost used and explain simply what continuing costs."}
    if usage["bookings"] >= CONVERSION_WARNING_RATIO * offer["booking_cap"]:
        return {"reason": f"{usage['bookings']} of {offer['booking_cap']} free interviews booked",
                "text": "Tell them early that the free interviews are almost used and explain simply what continuing costs."}
    if activation["step"] in TRIAL_CLOCK_STEPS:
        days = _days_since(activation.get("started") or activation["since"], now)
        if days >= offer["trial_days"] - WARNING_DAYS_BEFORE_END:
            return {"reason": f"{days} days into the {offer['trial_days']} day free period",
                    "text": "The free period ends soon. Ask how it went, then explain the options honestly."}
    return None
