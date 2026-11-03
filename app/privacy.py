"""India's DPDP Act in practice: erasure with a hashed "never again" list, the consent ledger, erasure requests.

- After a person's data is erased, only a SHA-256 hash of their normalised email and phone numbers is kept
  (`suppressed_hashes`). Every lead save goes through `guard_leads`, so no import, merge or enrichment can bring the
  person back, and the senders check `is_blocked` before anything goes out.
- `consent_ledger` is append-only: one row each time consent is given or withdrawn (lead_id, channel, text, source,
  at, withdrawn_at). The only change ever made to old rows is redaction when the person is erased.
- A reply that asks us to delete their data creates an "Erasure request" task for the owner.
"""
import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from .hygiene import normalise_email, normalise_phone

ERASURE_PATTERNS = re.compile(
    r"\b(delete|remove|erase|wipe|purge|destroy)\b[^.!?\n]{0,40}\b(my|our|all)\b[^.!?\n]{0,25}"
    r"\b(data|details|information|info|personal|contact|number|email|record|records)\b"
    r"|\bforget (me|us|my)\b|\bright to (be forgotten|erasure)\b"
    r"|\b(mera|meri|mere|hamara|hamari)\b[^.!?\n]{0,25}\b(data|details|number|jankari)\b[^.!?\n]{0,25}\b(delete|hata|hatao|mita|remove)",
    re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- hashed suppression ----------
def contact_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hashes_for(email=None, phones=()) -> set:
    out = set()
    if normalise_email(email):
        out.add(contact_hash(normalise_email(email)))
    for phone in phones:
        if normalise_phone(phone):
            out.add(contact_hash(normalise_phone(phone)))
    return out


def lead_hashes(lead: dict) -> set:
    return hashes_for(lead.get("email"), [lead.get("phone"), *(lead.get("phones") or [])])


def is_blocked(store, email: Optional[str] = None, phone: Optional[str] = None) -> bool:
    """True when this address or number must never be contacted: suppressed (unsubscribe, bounce) or erased."""
    if normalise_email(email) and normalise_email(email) in set(store.read("suppressed")):
        return True
    erased = set(store.read("suppressed_hashes"))
    return bool(erased and hashes_for(email, [phone] if phone else []) & erased)


def _contact_ok(contact, erased: set) -> bool:
    return not (isinstance(contact, dict) and hashes_for(contact.get("email"), [contact.get("phone")]) & erased)


def _erased_value(key: str, value, erased: set) -> bool:
    return bool((hashes_for(value) if key == "email" else hashes_for(None, [value])) & erased)


def guard_leads(old: list, new: list, erased: set) -> list:
    """Called by the store on every save of `leads`: drop new leads for an erased person, and undo any merge or
    enrichment that would fill an erased email or phone into an existing lead."""
    if not erased:
        return new
    before = {lead.get("id"): lead for lead in old}
    out = []
    for lead in new:
        prev = before.get(lead.get("id"))
        if prev is None:
            if lead_hashes(lead) & erased:
                continue                                   # an erased person is never imported again
            prev = {}
        fixed = dict(lead)
        for key in ("email", "phone"):
            if fixed.get(key) and fixed[key] != prev.get(key) and _erased_value(key, fixed[key], erased):
                fixed[key] = prev.get(key) or ("" if key == "phone" else None)
        if fixed.get("phones") and fixed["phones"] != prev.get("phones"):
            fixed["phones"] = [p for p in fixed["phones"] if p in (prev.get("phones") or []) or not _erased_value("phone", p, erased)]
        if fixed.get("other_contacts") and fixed["other_contacts"] != prev.get("other_contacts"):
            fixed["other_contacts"] = [c for c in fixed["other_contacts"] if c in (prev.get("other_contacts") or []) or _contact_ok(c, erased)]
        out.append(fixed)
    return out


# ---------- consent ledger ----------
def record_consent(store, lead_id: str, event: str, channel: str = "any", text: str = "", source: str = "") -> dict:
    """Append one consent event ("given" or "withdrawn"). Rows are never edited afterwards (except erasure)."""
    at = now_iso()
    row = {"id": uuid.uuid4().hex[:12], "lead_id": lead_id, "event": event, "channel": channel or "any",
           "text": (text or "")[:500], "source": source or "", "at": at, "withdrawn_at": at if event == "withdrawn" else None}
    store.update("consent_ledger", lambda rows: [*rows, row])
    return row


def asks_for_erasure(text: str) -> bool:
    return bool(ERASURE_PATTERNS.search(text or ""))
