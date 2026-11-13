"""Has anyone tried this lead yet, and how did it go? One rule for the Leads page and for prospects that are already leads.

invalid / consultant   a caller marked the lead (a quality verdict, kept apart from the attempt counts)
never      no call, email, WhatsApp, SMS or LinkedIn attempt and no reply
tried      attempts made, nobody answered
connected  a call got through (connected / interested / meeting booked / call back)
replied    the person wrote back (auto-replies and bounces do not count)

Attempts are read from everywhere the app records them, not just the lead's timeline: the email outbox (sent mail only, never
practice runs, held or failed sends), the WhatsApp outbox, logged activities, the LinkedIn sequence and agent log, the lead's
email sequence and the timeline. Replies come from the inbox and the timeline."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

from .analytics_range import CONTACT_KINDS
from .replies import is_person_reply

REACHED = ("connected", "interested", "meeting booked", "meeting_booked", "call back", "call_back")
# Word boundaries: "call disconnected" must not read as "connected".
REACHED_TEXT = re.compile(r"\b(connected|interested|meeting[ _]booked|call[ _]back)\b", re.I)
NOT_SENT = ("dry_run", "held", "failed", "provider_error", "error", "blocked", "skipped", "bounced_before_send")
OUT_ACTIVITY = ("call", "email", "whatsapp", "sms", "linkedin")
MARKER_OUTCOMES = ("invalid_lead", "consultant_lead")     # marking a lead is not a contact attempt
MARKER_TEXT = ("call — invalid lead", "call — consultant lead")


def index(store) -> dict:
    """{lead_id: {"tries": [(kind, iso)...], "reply": bool, "reached": bool}} from every collection that records contact."""
    out: dict = defaultdict(lambda: {"tries": [], "reply": False, "reached": False})
    for m in store.read("outbox"):
        if m.get("lead_id") and m.get("status") not in NOT_SENT and m.get("sent_at"):
            out[m["lead_id"]]["tries"].append(("email", m["sent_at"]))
    for m in store.read("whatsapp_outbox"):
        if m.get("lead_id") and m.get("status") not in NOT_SENT and m.get("sent_at"):
            out[m["lead_id"]]["tries"].append(("whatsapp", m["sent_at"]))
    for a in store.read("activities"):
        if not a.get("lead_id") or a.get("type") not in OUT_ACTIVITY or a.get("direction", "out") != "out" or a.get("outcome") in MARKER_OUTCOMES:
            continue
        out[a["lead_id"]]["tries"].append((a["type"], a.get("at") or ""))
        if a["type"] == "call" and str(a.get("outcome") or "").lower() in REACHED:
            out[a["lead_id"]]["reached"] = True
    for m in store.read("inbox"):
        if m.get("lead_id") and str(m.get("label") or "") not in ("auto_reply", "bounce"):
            out[m["lead_id"]]["reply"] = True
    for r in store.read("settings").get("linkedin_agent_log") or []:
        if r.get("lead_id") and r.get("kind") in ("connect", "message"):
            out[r["lead_id"]]["tries"].append(("linkedin", r.get("at") or ""))
    return out


def of(lead: dict, idx: Optional[dict] = None) -> dict:
    """{status, attempts, last_at}. `idx` is index(store); without it only the lead's own record is read."""
    extra = (idx or {}).get(lead.get("id")) or {"tries": [], "reply": False, "reached": False}
    events = lead.get("timeline") or []
    in_timeline = defaultdict(list)
    for e in events:
        if e.get("kind") in CONTACT_KINDS and not str(e.get("text") or "").lower().startswith(MARKER_TEXT):
            in_timeline[e["kind"]].append(e.get("at") or "")
    elsewhere = extra["tries"]                                   # [(kind, at)] from the outbox, activities and logs
    kinds = set(in_timeline) | {k for k, _ in elsewhere}
    tries = []
    for k in kinds:                                              # one attempt recorded in two places counts once
        a, b = in_timeline.get(k, []), [t for kk, t in elsewhere if kk == k]
        tries += a if len(a) >= len(b) else b
    seq = lead.get("sequence") or {}
    li = lead.get("linkedin_seq") or {}
    if seq.get("sent_days") and not any(k == "email" for k in kinds):
        tries.append(seq.get("last_sent_at") or seq.get("started_at") or "")
    if li.get("done") and not any(k == "linkedin" for k in kinds):
        tries.append(li.get("last_sent_at") or li.get("started_at") or "")
    replied = extra["reply"] or any(is_person_reply(e) for e in events)
    reached = extra["reached"] or any(e.get("kind") == "call" and REACHED_TEXT.search(str(e.get("text") or "")) for e in events)
    attempts = len(tries)
    status = "replied" if replied else "connected" if reached else "tried" if tries else "never"
    quality = (lead.get("lead_quality") or {}).get("kind")
    if quality in ("invalid", "consultant"):
        status = quality                                          # a verdict, not an attempt: its own bucket
    return {"status": status, "attempts": attempts, "last_at": max((t for t in tries if t), default=None)}
