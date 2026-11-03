"""Lead construction and merging, shared by every import path."""
import uuid
from datetime import datetime, timezone
from typing import Optional

from .source_names import add_touch, canonical
from .hygiene import business_domain, dedupe_key, is_role_address, normalise_email, normalise_phone

NUMERIC_FIELDS = ("open_roles", "company_size", "max_job_age_days", "repost_count")
MERGE_FILL_FIELDS = ("email", "email_is_role", "phone", "contact_name", "title", "website", "domain")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def event(kind: str, text: str) -> dict:
    return {"at": now_iso(), "kind": kind, "text": text}


def to_int(value) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def build_lead(row: dict, source: str, allow_company_key: bool = False) -> Optional[dict]:
    row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
    email = normalise_email(row.get("email"))
    lead = {
        "id": uuid.uuid4().hex[:10],
        "company": row.get("company", ""),
        "contact_name": row.get("contact_name", ""),
        "title": row.get("title", ""),
        "email": email,
        "email_is_role": bool(email and is_role_address(email)),
        "phone": normalise_phone(row.get("phone")),
        "website": row.get("website", ""),
        "domain": business_domain(row.get("website")) or business_domain(email),
        "country": row.get("country", ""),
        "source": source,
        "source_id": canonical(source)[0],
        "stage": "new",
        "consent": None,
        "engagement": {"opens": 0, "clicks": 0, "replies": 0},
        "timeline": [event("created", f"Imported from {source}")],
        "created_at": now_iso(),
        **{f: to_int(row.get(f)) for f in NUMERIC_FIELDS},
    }
    lead["dedupe_key"] = dedupe_key(lead) or (f"company:{lead['company'].lower()}" if allow_company_key and lead["company"] else None)
    return lead if lead["dedupe_key"] else None


def contactless_match(leads: list, new: dict) -> Optional[int]:
    """Index of an existing lead for the same company that the new row can complete (email or website)."""
    for i, lead in enumerate(leads):
        same_company = (new.get("domain") and lead.get("domain") == new["domain"]) or (
            new.get("company") and (lead.get("company") or "").lower() == new["company"].lower())
        if not same_company:
            continue
        if (new.get("email") and not lead.get("email")) or (new.get("domain") and not lead.get("domain")):
            return i
    return None


def merge_contact(existing: dict, new: dict) -> dict:
    """Fill the gaps of the existing company lead from the new row, keeping its job signals."""
    filled = {f: existing.get(f) or new.get(f) for f in MERGE_FILL_FIELDS}
    merged = {**existing, **filled}
    merged["dedupe_key"] = dedupe_key(merged) or existing["dedupe_key"]
    merged["timeline"] = [*existing["timeline"], event("merged", f"Details added from {new['source']}")]
    add_touch(merged, new["source"])
    return merged
