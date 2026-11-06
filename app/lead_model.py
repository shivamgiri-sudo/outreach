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


def _linkedin(raw, kind: str) -> str:
    """A LinkedIn profile (/in/…) or company (/company/…) link, made https and stripped of tracking; anything else is dropped."""
    import re
    text = str(raw or "").strip()
    if not text:
        return ""
    if not text.startswith("http"):
        text = "https://" + text.lstrip("/")
    m = re.match(r"^https?://(?:[a-z]{2,3}\.)?(?:www\.)?linkedin\.com/(in|company|pub)/([^/?#\s]+)", text, re.I)
    if not m or (kind == "company") != (m.group(1).lower() == "company"):
        return ""
    return f"https://www.linkedin.com/{m.group(1).lower()}/{m.group(2)}"


def linkedin_profile_url(raw) -> str:
    """https://www.linkedin.com/in/<slug> for a LinkedIn person link (tracking and sub-pages dropped), else ""."""
    url = _linkedin(raw, "in")
    return url if url.startswith("https://www.linkedin.com/in/") else ""


def build_lead(row: dict, source: str, allow_company_key: bool = False, fallback_key: str = "") -> Optional[dict]:
    row = {k.strip().lower(): (v.strip() if isinstance(v, str) else "" if v is None else v) for k, v in row.items() if k}
    custom = {k[7:]: v for k, v in row.items() if k.startswith("custom:") and v not in ("", None)}   # see lead_import.quick_rows
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
        **{k: v for k, v in {"linkedin_url": _linkedin(row.get("linkedin_url"), "in"), "company_linkedin_url": _linkedin(row.get("company_linkedin_url"), "company"),
                             "city": row.get("city", "")[:80], "state": row.get("state", "")[:80], "sector": row.get("sector", "")[:120],
                             "role": row.get("role", "")[:120]}.items() if v},
        **({"custom": custom} if custom else {}),
    }
    lead["dedupe_key"] = dedupe_key(lead) or (f"company:{lead['company'].lower()}" if allow_company_key and lead["company"] else None) or fallback_key or None
    if not lead["dedupe_key"]:
        return None
    from .workspaces import stamp_new          # late import: workspaces imports this module
    return stamp_new(lead)


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
