"""One inbox for every reply, scored 0-100 for how much it deserves a person's time now.

Replies already live in each lead's timeline as `[label] (channel, source) text` (see inbound.apply_reply).
Nothing here changes a lead: it only reads them, scores them and says what to do next.
"""
import re
from datetime import datetime, timezone
from typing import Optional

from .scoring import score_lead

_REPLY = re.compile(r"^\[(?P<label>[a-z_]+)\]\s*\((?P<channel>[^,)]*)(?:,\s*(?P<source>[^)]*))?\)\s*(?P<text>.*)$", re.DOTALL)

# What a reply of this kind is worth, before the small adjustments below.
BASE = {"interested": 88, "question": 66, "not_now": 42, "wrong_person": 38, "other": 30,
        "not_interested": 8, "auto_reply": 3, "unsubscribe": 0, "bounce": 0}
NEXT_STEP = {
    "interested": "Call or reply today and offer a time.",
    "question": "Answer the question, then ask for a short call.",
    "not_now": "Note the month they gave and follow up then.",
    "wrong_person": "Ask who handles hiring and reach out to them.",
    "other": "Read it and decide; the rules could not place it.",
    "not_interested": "Nothing to do. They are out of the sequence.",
    "auto_reply": "Nothing to do. Wait for the real reply.",
    "unsubscribe": "Nothing to do. They are blocked from sending.",
    "bounce": "Address is bad. Find another contact at the company.",
}
LABELS_SHOWN = tuple(BASE)
ACTION_LABELS = ("interested", "question", "not_now", "wrong_person", "other")
_BUYING = re.compile(r"\b(price|pricing|cost|quote|how much|demo|sample|call me|call|meeting|schedule|today|tomorrow|this week|"
                     r"\d{10}|\+91)\b", re.IGNORECASE)
_COOLING = re.compile(r"\b(not sure|maybe|think about|will see|let me check)\b", re.IGNORECASE)


def parse_reply(event: dict) -> Optional[dict]:
    """A timeline event of kind 'reply' -> {label, channel, source, text}; None when it isn't one we can read."""
    if event.get("kind") != "reply":
        return None
    m = _REPLY.match(str(event.get("text") or "").strip())
    if not m:
        return None
    return {"label": m["label"], "channel": (m["channel"] or "").strip() or "email", "source": (m["source"] or "").strip(),
            "text": (m["text"] or "").strip()}


def score_reply(label: str, text: str, lead_score: int = 0) -> int:
    """0-100. The label sets the range; buying words and a hot lead nudge it up, hedging nudges it down."""
    base = BASE.get(label, 30)
    if base <= 10:
        return base                                  # a refusal, bounce or auto-reply never climbs
    score = base
    if _BUYING.search(text or ""):
        score += 6
    if _COOLING.search(text or ""):
        score -= 8
    score += round(min(max(lead_score, 0), 100) / 100 * 6)      # a lead that already scored well is worth a little more
    return max(base - 10, min(99, score))


def _hours_since(stamp: str, now: datetime) -> float:
    try:
        then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0.0, (now - then).total_seconds() / 3600)


def reply_key(lead_id: str, at) -> str:
    """Stable id of one reply (lead + when it arrived), used to keep a person's triage of it (handled, assigned…)."""
    return f"{lead_id}|{at or ''}"


def _snoozed(st: dict, now: datetime) -> bool:
    try:
        until = datetime.fromisoformat(str(st.get("snoozed_until") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    return (until if until.tzinfo else until.replace(tzinfo=timezone.utc)) > now


def build_inbox(leads: list, now: Optional[datetime] = None, label: str = "", channel: str = "", only_action: bool = False,
                limit: int = 200, state: Optional[dict] = None, assignee: str = "") -> dict:
    """Every reply across all leads, best first. A lead's newest reply is `latest`; older ones stay visible in the thread.

    `state` is the team's triage per reply key (settings.reply_state): handled, read, assignee, snoozed_until and a
    label a person corrected. A handled or snoozed reply no longer needs a person; a corrected label replaces the guess."""
    now = now or datetime.now(timezone.utc)
    state = state or {}
    rows = []
    for lead in leads:
        replies = [(e, parse_reply(e)) for e in lead.get("timeline") or []]
        replies = [(e, p) for e, p in replies if p]
        if not replies:
            continue
        lead_pts = score_lead({**lead, "consent_yes": bool(lead.get("consent"))})["score"]
        for i, (event, parsed) in enumerate(replies):
            hours = _hours_since(event.get("at"), now)
            key = reply_key(lead["id"], event.get("at"))
            st = state.get(key) or {}
            guessed = parsed["label"]
            if st.get("label") in BASE:
                parsed = {**parsed, "label": st["label"]}
            rows.append({"key": key, "guessed_label": guessed, "label_overridden": parsed["label"] != guessed,
                "handled": bool(st.get("handled")), "handled_by": st.get("handled_by"), "handled_at": st.get("handled_at"),
                "read": bool(st.get("read") or st.get("handled")), "assignee": st.get("assignee") or "",
                "snoozed_until": st.get("snoozed_until") if _snoozed(st, now) else None,
                "lead_id": lead["id"], "company": lead.get("company") or "", "contact": lead.get("contact_name") or "",
                "title": lead.get("title") or "", "stage": lead.get("stage") or "", "at": event.get("at"),
                "label": parsed["label"], "channel": parsed["channel"], "source": parsed["source"], "text": parsed["text"],
                "score": score_reply(parsed["label"], parsed["text"], lead_pts), "next_step": NEXT_STEP.get(parsed["label"], ""),
                "latest": i == len(replies) - 1, "age_hours": round(hours, 1),
                "needs_action": parsed["label"] in ACTION_LABELS and i == len(replies) - 1 and lead.get("stage") not in ("won", "lost", "unsubscribed")
                and not st.get("handled") and not _snoozed(st, now)})
    counts = {}
    for r in rows:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    shown = [r for r in rows if r["latest"] or not only_action]
    if label:
        shown = [r for r in shown if r["label"] == label]
    if channel:
        shown = [r for r in shown if r["channel"] == channel]
    if assignee:
        shown = [r for r in shown if r["assignee"] in ("", assignee)] if assignee != "__unassigned" else [r for r in shown if not r["assignee"]]
    if only_action:
        shown = [r for r in shown if r["needs_action"]]
    shown.sort(key=lambda r: (not r["needs_action"], -r["score"], r["age_hours"]))
    return {"total": len(rows), "needs_action": sum(1 for r in rows if r["needs_action"]), "counts": counts,
            "unread": sum(1 for r in rows if r["latest"] and not r["read"]), "handled": sum(1 for r in rows if r["handled"]),
            "channels": sorted({r["channel"] for r in rows}), "replies": shown[:max(1, min(limit, 1000))]}
