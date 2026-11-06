"""Manual corrections to a lead's contact details, with a log of who changed what.

Every change is written twice: to the lead's own timeline (so it shows in the lead journey) and to the `audit`
collection (one row per field, newest last) for the Change log. Email and phone are validated and normalised the same
way imports do it; a value that would make the lead a duplicate of another lead is refused.
"""
from typing import Optional

from .hygiene import business_domain, company_domain, dedupe_key, is_role_address, normalise_email, normalise_phone
from .lead_model import event, now_iso
from .standardise import linkedin_url

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
    if field == "linkedin_url" and text:
        # Tidy to the https profile link the LinkedIn queue needs: "linkedin.com/in/x/" was saved as typed, so the lead
        # showed up in "Suggested people" but Start refused it ("Add the person's LinkedIn profile link first").
        tidy = linkedin_url(text, "in")
        if tidy:
            return tidy
        if "linkedin.com/" not in text.lower():
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


def contact_edits(rows: list) -> list:
    """Only the per-field contact corrections (the older row shape); action rows from `record` are left out."""
    return [r for r in rows if not r.get("action")]


def log_for(store, lead_id: str = "", limit: int = 200) -> list:
    rows = contact_edits(store.read("audit"))
    if lead_id:
        rows = [r for r in rows if r.get("lead_id") == lead_id]
    return list(reversed(rows))[:limit]


# ---------- the wider audit trail: who did what, when (owner-only Audit tab) ----------
ACTIONS = {
    "stage.change": "Stage changed", "live_sending.on": "Real sending switched on", "live_sending.off": "Real sending switched off",
    "bulk.list_email": "Email started for a list", "bulk.list_linkedin": "LinkedIn started for a list",
    "bulk.workspace_move": "Leads moved to a workspace", "lead.erase": "Person erased", "list.delete": "List deleted",
    "campaign.delete": "Campaign deleted", "user.add": "Login added", "user.change": "Login changed", "user.remove": "Login removed",
    "workspace.create": "Workspace created", "workspace.delete": "Workspace removed", "linkedin_agent.on": "LinkedIn agent switched on",
    "linkedin_agent.off": "LinkedIn agent switched off", "linkedin_agent.settings": "LinkedIn agent settings saved",
    "settings.save": "Settings saved", "contact.edit": "Contact details corrected",
}
MAX_DETAIL = 500


def record(store, user, action: str, target: str = "", detail: str = "") -> dict:
    """One audit row {at, by, action, target, detail}. Never put secret values in `detail` (key names only)."""
    actor = actor_of(user) if not isinstance(user, str) else {"name": user or "Unknown", "email": "", "role": ""}
    row = {"at": now_iso(), "by": actor["name"], "by_email": actor["email"], "by_role": actor["role"], "action": str(action)[:60],
           "target": str(target or "")[:200], "detail": " ".join(str(detail or "").split())[:MAX_DETAIL]}
    store.update("audit", lambda old: [*old, row][-MAX_AUDIT_ROWS:])
    return row


def record_stage(store, request, lead_id: str, stage: str) -> None:
    """A person moved a lead to another stage (the old stage is read from the event just written)."""
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None) or {}
    last = next((e for e in reversed(lead.get("timeline") or []) if e.get("kind") == "stage"), {})
    record(store, getattr(request.state, "user", None), "stage.change", lead.get("company") or lead.get("domain") or lead_id,
           f"{str(last.get('text') or stage).split(' (by ')[0]} (lead {lead_id})")


def as_action(row: dict) -> dict:
    """Any audit row in the {at, by, action, target, detail} shape (contact edits are mapped onto it)."""
    if row.get("action"):
        return {k: row.get(k, "") for k in ("at", "by", "by_email", "by_role", "action", "target", "detail")}
    return {"at": row.get("at", ""), "by": row.get("by", ""), "by_email": row.get("by_email", ""), "by_role": "", "action": "contact.edit",
            "target": row.get("company") or row.get("lead_id") or "",
            "detail": f"{row.get('label') or row.get('field')}: {row.get('old') or '(empty)'} -> {row.get('new') or '(empty)'}"
                      + (f" (reason: {row['reason']})" if row.get("reason") else "")}


def trail(store, action: str = "", by: str = "", q: str = "", date_from: str = "", date_to: str = "", limit: int = 500, offset: int = 0) -> dict:
    """Newest first, filtered. `action` matches exactly or by prefix ("live_sending" matches both switches);
    date_from / date_to are IST days (YYYY-MM-DD, inclusive)."""
    from datetime import datetime, timedelta, timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    rows = [as_action(r) for r in reversed(store.read("audit"))]

    def day(r):
        try:
            return datetime.fromisoformat(r["at"]).astimezone(ist).date().isoformat()
        except (TypeError, ValueError):
            return ""
    if action:
        rows = [r for r in rows if r["action"] == action or r["action"].startswith(action.rstrip(".") + ".")]
    if by:
        rows = [r for r in rows if by.lower() in f"{r['by']} {r['by_email']}".lower()]
    if q:
        rows = [r for r in rows if q.lower() in f"{r['target']} {r['detail']}".lower()]
    if date_from:
        rows = [r for r in rows if day(r) >= date_from]
    if date_to:
        rows = [r for r in rows if day(r) and day(r) <= date_to]
    people = sorted({r["by"] for r in rows if r["by"]})
    return {"total": len(rows), "items": rows[offset:offset + limit], "actions": ACTIONS, "people": people}
