"""Has anyone tried this lead yet, and how did it go? One rule used for leads and for prospects that are already leads.

never      no call, email, WhatsApp or LinkedIn attempt and no reply
tried      attempts made, nobody answered
connected  a call got through (connected / interested / meeting booked / call back)
replied    the person wrote back (auto-replies and bounces do not count)
The same rule is in web/src/contactStatus.js."""
from __future__ import annotations

from .analytics_range import CONTACT_KINDS
from .replies import is_person_reply

REACHED = ("connected", "interested", "meeting booked", "meeting_booked", "call back", "call_back")


def of(lead: dict) -> dict:
    """{status, attempts, last_at}."""
    events = lead.get("timeline") or []
    tries = [e for e in events if e.get("kind") in CONTACT_KINDS]
    replied = any(is_person_reply(e) for e in events)
    reached = any(e.get("kind") == "call" and any(w in str(e.get("text") or "").lower() for w in REACHED) for e in tries)
    status = "replied" if replied else "connected" if reached else "tried" if tries else "never"
    return {"status": status, "attempts": len(tries), "last_at": max((e.get("at") for e in tries if e.get("at")), default=None)}
