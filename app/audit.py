"""Manual corrections to a lead's contact details, with a log of who changed what.

Every change is written twice: to the lead's own timeline (so it shows in the lead journey) and to the `audit`
collection (one row per field, newest last) for the Change log. Email and phone are validated and normalised the same
way imports do it; a value that would make the lead a duplicate of another lead is refused.
"""
from typing import Optional

from .hygiene import business_domain, company_domain, dedupe_key, is_role_address, normalise_email, normalise_phone
from .lead_model import event, now_iso

EDITABLE = {"contact_name": "Contact name", "company": "Company", "title": "Title", "email": "Email",
            "phone": "Phone", "website": "Website", "country": "Country", "linkedin_url": "LinkedIn profile"}
MAX_LEN = 200
MAX_AUDIT_ROWS = 20000


class EditRefused(ValueError):
    """The change is not allowed; the message says why in plain words."""


def actor_of(user: Optional[dict]) -> dict:
    user = user or {}
    return {"name": user.get("name") or user.get("email") or "Unknown", "email": user.get("email") or "", "role": user.get("role") or ""}


def _clean(field: str, raw) -> Optional[str]:
    text = " ".join(str(raw or "").split())[:MAX_LEN]
    if field == "email":
        if not text:
            return ""
        email = normalise_email(text)
        if not email:
            raise EditRefused("That does not look like an email address.")
        return email
    if field == "phone":
        if not text:
            return ""
        phone = normalise_phone(text)
        if not phone:
            raise EditRefused("That is not a valid Indian mobile number (10 digits starting 6-9).")
        return phone
    if field == "linkedin_url" and text and "linkedin.com/" not in text.lower():
        raise EditRefused("That is not a LinkedIn profile link.")
    if field == "website" and text:
        return text if "." in text else (_ for _ in ()).throw(EditRefused("That does not look like a website."))
    return text


def edit_contact(store, lead_id: str, changes: dict, actor: dict, reason: str = "") -> dict:
    """Apply the given fields to one lead. Returns {"lead": saved, "changed": [...]}. Raises LookupError / EditRefused."""
    unknown = [k for k in changes if k not in EDITABLE]
    if unknown:
        raise EditRefused(f"Cannot edit: {', '.join(unknown)}")
    leads = store.read("leads")
    current = next((l for l in leads if l["id"] == lead_id), None)
    if current is None:
        raise LookupError("Lead not found")
    cleaned = {k: _clean(k, v) for k, v in changes.items()}
    cleaned = {k: v for k, v in cleaned.items() if (current.get(k) or "") != v}
    if not cleaned:
        return {"lead": current, "changed": []}
    probe = {**current, **cleaned}
    new_key = dedupe_key(probe)
    if not new_key:
        raise EditRefused("A lead needs at least an email, a phone or a website.")
    twin = next((l for l in leads if l["id"] != lead_id and l.get("dedupe_key") == new_key), None)
    if twin:
        raise EditRefused(f"Another lead already has this ({twin.get('company') or twin.get('contact_name') or twin['id']}). Merge them instead.")
    rows, stamp = [], now_iso()

    def apply(lead: dict):
        merged = {**lead}
        for field, value in cleaned.items():
            rows.append({"lead_id": lead_id, "field": field, "label": EDITABLE[field], "old": lead.get(field) or "", "new": value,
                         "by": actor["name"], "by_email": actor["email"], "at": stamp, "reason": reason[:300],
                         "company": lead.get("company") or ""})
            merged[field] = value
        if "email" in cleaned:
            merged["email_is_role"] = bool(cleaned["email"] and is_role_address(cleaned["email"]))
            merged["domain"] = business_domain(merged.get("website")) or business_domain(cleaned["email"]) or merged.get("domain")
            merged.pop("email_check", None)                      # the old mailbox check was for the old address
        if "website" in cleaned:
            merged["domain"] = business_domain(cleaned["website"]) or merged.get("domain")
        merged["dedupe_key"] = new_key
        lines = "; ".join(f"{EDITABLE[r['field']]}: {r['old'] or '(empty)'} → {r['new'] or '(empty)'}" for r in rows)
        why = f" Reason: {reason[:300]}" if reason else ""
        merged["timeline"] = [*lead["timeline"], {**event("edit", f"{actor['name']} edited {lines}.{why}"), "by": actor["name"]}]
        return merged
    saved = store.update_lead(lead_id, apply)
    if saved is None:
        raise LookupError("Lead not found")
    store.update("audit", lambda old: [*old, *rows][-MAX_AUDIT_ROWS:])
    return {"lead": saved, "changed": [r["field"] for r in rows]}


def log_for(store, lead_id: str = "", limit: int = 200) -> list:
    rows = store.read("audit")
    if lead_id:
        rows = [r for r in rows if r["lead_id"] == lead_id]
    return list(reversed(rows))[:limit]
