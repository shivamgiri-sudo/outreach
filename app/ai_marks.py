"""AI-drafted text: remember what the AI wrote, recognise it when a person sends it, and mark the message.

- `remember` keeps each AI draft for a lead (collection `ai_drafts`, newest 500) and writes an "ai.draft" row to the
  change log (who asked for it).
- `match` says whether a message a person is about to send is (an edited copy of) one of those drafts.
- `mark` adds {ai_drafted: true, ai_approved_by, ai_source} to an outbox / WhatsApp row and the change log gets an
  "ai.sent" row with who approved and sent it.
Flow emails and SMS built from an "Ask Claude" step are marked by the flow engine (flow_actions.ai_text).
"""
import difflib
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

COLLECTION = "ai_drafts"
KEEP = 500
WINDOW = timedelta(days=30)
MIN_CHARS = 20
SIMILAR = 0.6


def _norm(text) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(text or "").lower())).strip()


def _rows(store) -> list:
    try:
        return store.read(COLLECTION)
    except KeyError:
        return []


def remember(store, lead_id: str, text: str, user=None, source: str = "reply draft") -> Optional[dict]:
    """Keep one AI draft and log who asked for it. Returns the saved row (None for an empty draft)."""
    if not str(text or "").strip():
        return None
    from . import audit
    actor = audit.actor_of(user) if not isinstance(user, str) else {"name": user, "email": "", "role": ""}
    row = {"id": uuid.uuid4().hex[:12], "lead_id": lead_id, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "by": actor["name"], "source": source, "text": str(text)[:4000]}
    try:
        store.update(COLLECTION, lambda rows: [*rows, row][-KEEP:])
    except KeyError:
        store.write(COLLECTION, [row])
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), {}) or {}
    audit.record(store, user, "ai.draft", lead.get("company") or lead_id, f"{source} written by the AI for a person to check",
                 {"lead_id": lead_id, "ai_draft_id": row["id"]})
    return row


def match(store, lead_id: str, text: str, now: Optional[datetime] = None) -> Optional[dict]:
    """The AI draft this text was made from (sent as written or edited), or None."""
    sent = _norm(text)
    if len(sent) < MIN_CHARS:
        return None
    now = now or datetime.now(timezone.utc)
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    for row in reversed(_rows(store)):
        if row.get("lead_id") != lead_id:
            continue
        try:
            if now - datetime.fromisoformat(row["at"]) > WINDOW:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        draft = _norm(row.get("text"))
        if len(draft) < MIN_CHARS:
            continue
        if draft[:120] in sent or sent in draft or difflib.SequenceMatcher(None, draft[:2000], sent[:2000]).ratio() >= SIMILAR:
            return row
    return None


def mark(message: dict, approved_by: str, source: str, draft_id: str = "") -> dict:
    message.update({"ai_drafted": True, "ai_approved_by": approved_by or "", "ai_source": source,
                    **({"ai_draft_id": draft_id} if draft_id else {})})
    return message


def log_sent(store, user, lead_id: str, channel: str, approved_by: str, source: str) -> None:
    from . import audit
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), {}) or {}
    audit.record(store, user or approved_by or "Unknown", "ai.sent", lead.get("company") or lead_id,
                 f"{channel} message drafted by the AI ({source}), approved and sent by {approved_by or 'an automatic flow'}",
                 {"lead_id": lead_id, "ai_drafted": True})
