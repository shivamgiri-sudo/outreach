"""Has anyone tried this lead yet, and how did it go? One rule for the Leads page and for prospects that are already leads.

never      no call, email, WhatsApp, SMS or LinkedIn attempt and no reply
tried      attempts made, nobody answered
connected  a call got through (connected / interested / meeting booked / call back)
replied    the person wrote back (auto-replies and bounces do not count)

Attempts are read from everywhere the app records them, not just the lead's timeline: the email outbox (sent mail only, never
practice runs, held or failed sends), the WhatsApp outbox, logged activities, the LinkedIn sequence and agent log, the lead's
email sequence and the timeline. Replies come from the inbox and the timeline."""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .analytics_range import CONTACT_KINDS
from .replies import is_person_reply

REACHED = ("connected", "interested", "meeting booked", "meeting_booked", "call back", "call_back")
NOT_SENT = ("dry_run", "held", "failed", "provider_error", "error", "blocked", "skipped", "bounced_before_send")
OUT_ACTIVITY = ("call", "email", "whatsapp", "sms", "linkedin")


def index(store) -> dict:
    """{lead_id: {"tries": [iso...], "reply": bool, "reached": bool}} from every collection that records contact."""
    out: dict = defaultdict(lambda: {"tries": [], "reply": False, "reached": False})
    for m in store.read("outbox"):
        if m.get("lead_id") and m.get("status") not in NOT_SENT and m.get("sent_at"):
            out[m["lead_id"]]["tries"].append(m["sent_at"])
    for m in store.read("whatsapp_outbox"):
        if m.get("lead_id") and m.get("status") not in NOT_SENT and m.get("sent_at"):
            out[m["lead_id"]]["tries"].append(m["sent_at"])
    for a in store.read("activities"):
        if not a.get("lead_id") or a.get("type") not in OUT_ACTIVITY or a.get("direction", "out") != "out":
            continue
        out[a["lead_id"]]["tries"].append(a.get("at") or "")
        if a["type"] == "call" and str(a.get("outcome") or "").lower() in REACHED:
            out[a["lead_id"]]["reached"] = True
    for m in store.read("inbox"):
        if m.get("lead_id") and str(m.get("label") or "") not in ("auto_reply", "bounce"):
            out[m["lead_id"]]["reply"] = True
    for r in store.read("settings").get("linkedin_agent_log") or []:
        if r.get("lead_id") and r.get("kind") in ("connect", "message"):
            out[r["lead_id"]]["tries"].append(r.get("at") or "")
    return out


def of(lead: dict, idx: Optional[dict] = None) -> dict:
    """{status, attempts, last_at}. `idx` is index(store); without it only the lead's own record is read."""
    extra = (idx or {}).get(lead.get("id")) or {"tries": [], "reply": False, "reached": False}
    events = lead.get("timeline") or []
    tries = [e.get("at") or "" for e in events if e.get("kind") in CONTACT_KINDS] + list(extra["tries"])
    seq = lead.get("sequence") or {}
    li = lead.get("linkedin_seq") or {}
    if seq.get("sent_days"):
        tries.append(seq.get("last_sent_at") or seq.get("started_at") or "")
    if li.get("done"):
        tries.append(li.get("last_sent_at") or li.get("started_at") or "")
    replied = extra["reply"] or any(is_person_reply(e) for e in events)
    reached = extra["reached"] or any(e.get("kind") == "call" and any(w in str(e.get("text") or "").lower() for w in REACHED) for e in events)
    attempts = len(set(t for t in tries if t)) or len(tries)
    status = "replied" if replied else "connected" if reached else "tried" if tries else "never"
    return {"status": status, "attempts": attempts, "last_at": max((t for t in tries if t), default=None)}
