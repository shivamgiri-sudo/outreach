""""Where this came from": one read-only list of every filled field on a lead with its source, date and type.

Built only from what the lead already records, newest first where several apply:
  1 a manual edit       the change log (store "audit", rows with lead_id + field) when its new value is today's value
  2 a per-field record  lead.provenance[field] (Find & enrich, site markup, company register, Gravatar, prospect DB ...),
                        lead.field_sources[field] (import conflicts, merges, the Chrome assistant), email_source /
                        phone_source, email_guess_source
  3 nothing             "not recorded" (never guessed). The lead's first source (import file, harvest, form) is shown
                        once at the top: fields with no own record most likely came with it, but it is not claimed per field.
Dates: the manual edit's time, the mailbox check's time, the register match's time, else the first history line that
mentions the value (`at_basis: "history"`), else none. Types: found, guessed, confirmed, typed (by a person).
"""
from typing import Optional

from . import email_state
from .source_names import label_of

FIELDS = (("email", "Email"), ("email_guess", "Guessed email"), ("phone", "Phone"), ("contact_name", "Name"), ("title", "Job title"),
          ("company", "Company"), ("company_legal", "Legal name"), ("linkedin_url", "LinkedIn profile"),
          ("company_linkedin_url", "Company LinkedIn"), ("website", "Website"), ("address", "Address"), ("city", "City"),
          ("state", "State"), ("logo_url", "Logo"), ("photo_url", "Photo"), ("gravatar_url", "Gravatar profile"))
SOCIAL = {"x": "X (Twitter)", "facebook": "Facebook", "instagram": "Instagram", "youtube": "YouTube"}
TAG_LABELS = {
    "site-markup": "Company website markup", "mca-master-data": "Company register (MCA master data)", "gravatar": "Gravatar public profile",
    "pattern": "Email format guess", "guess": "Email format guess", "prospect_db": "Prospect database", "website": "Company website",
    "enrichment": "Company website", "team_pages": "Company team pages", "linkedin_manual": "LinkedIn profile (saved with the Chrome assistant)",
    "harvest": "Lead harvest", "manual": "Typed by a person", "apollo": "Apollo", "hunter": "Hunter",
}
GUESS_TAGS = ("pattern", "guess")
NOT_RECORDED = "not recorded"
VALUE_MAX = 200


def parse_source(raw) -> dict:
    """'pattern:first.last' -> {tag, label, detail}. A bare URL is the company website."""
    text = str(raw or "").strip()
    if not text:
        return {"tag": "", "label": NOT_RECORDED, "detail": ""}
    if text.lower().startswith(("http://", "https://")):
        return {"tag": "website", "label": TAG_LABELS["website"], "detail": text}
    tag, _, detail = text.partition(":")
    tag = tag.strip()
    label = TAG_LABELS.get(tag.lower()) or label_of(tag)
    if tag.lower() == "prospect_db" and detail:
        label, detail = f"{label} ({label_of(detail)})", ""
    return {"tag": tag.lower(), "label": label, "detail": detail.strip()}


def _edits(store, lead_id: str) -> dict:
    """field -> the latest change-log row for this lead."""
    out = {}
    for row in store.read("audit") or []:
        if isinstance(row, dict) and row.get("lead_id") == lead_id and row.get("field"):
            if row.get("at", "") >= out.get(row["field"], {}).get("at", ""):
                out[row["field"]] = row
    return out


def _mentioned(lead: dict, value: str) -> Optional[str]:
    """The time of the first history line that mentions the value (values of 5+ characters only)."""
    needle = str(value or "").strip().lower()
    if len(needle) < 5:
        return None
    return next((e.get("at") for e in lead.get("timeline") or [] if needle in str(e.get("text") or "").lower() and e.get("at")), None)


def _record(lead: dict, field: str) -> str:
    if field == "email_guess":
        return lead.get("email_guess_source") or (lead.get("provenance") or {}).get(field) or ""
    return ((lead.get("provenance") or {}).get(field) or (lead.get("field_sources") or {}).get(field)
            or (lead.get(f"{field}_source") if field in ("email", "phone") else "") or "")


def _row(lead: dict, field: str, label: str, value, edits: dict, history: dict) -> dict:
    shown = str(value)[:VALUE_MAX]
    row = {"field": field, "label": label, "value": shown, "source": "", "source_label": NOT_RECORDED, "detail": "",
           "kind": "", "at": None, "at_basis": "", "by": ""}
    edit = edits.get(field)
    if edit and str(edit.get("new") or "") == str(value):
        row.update(source="manual", source_label="Typed by a person", kind="typed", at=edit.get("at"), at_basis="edit", by=edit.get("by") or "")
    else:
        src = parse_source(_record(lead, field))
        if src["tag"]:
            row.update(source=src["tag"], source_label=src["label"], detail=src["detail"],
                       kind="guessed" if src["tag"] in GUESS_TAGS or field == "email_guess" else "found")
            if src["tag"] == "mca-master-data":
                row.update(at=(lead.get("registry") or {}).get("matched_at"), at_basis="register match")
        if field == "email_guess" and not src["tag"]:
            row["kind"] = "guessed"
    if not row["at"]:
        mentioned = _mentioned(lead, shown)
        if mentioned:
            row.update(at=mentioned, at_basis="history")
    if field == "email":
        check = lead.get("email_check") or {}
        state = email_state.state_of(lead, history)
        if state == "confirmed":
            row["kind"] = "confirmed"
        if check.get("status") and str(check.get("email") or value).strip().lower() == str(value).strip().lower():
            row["check"] = {"status": check["status"], "provider": check.get("provider") or "", "at": check.get("at") or ""}
        row["email_state"] = state
    if field == "email_guess":
        row["check"] = {"status": lead.get("email_guess_status") or "", "provider": "pattern guess", "at": ""}
    return row


def origin(lead: dict) -> dict:
    touches = [t for t in lead.get("sources") or [] if isinstance(t, dict) and t.get("id")]
    first = touches[0] if touches else {}
    out = {"source": label_of(first.get("id") or lead.get("source") or ""), "raw": str(lead.get("source") or "")[:200],
           "at": first.get("at") or lead.get("created_at") or "", "detail": str(first.get("detail") or "")[:200]}
    if lead.get("source_url"):
        out["source_url"] = str(lead["source_url"])[:300]
    if lead.get("harvest_jobs"):
        out["harvest_jobs"] = list(lead["harvest_jobs"])[:20]
    return out


def build(store, lead: dict, history: Optional[dict] = None) -> dict:
    history = history if history is not None else email_state.history_of(store)
    edits = _edits(store, lead["id"])
    rows = [_row(lead, f, label, lead[f], edits, history) for f, label in FIELDS if str(lead.get(f) or "").strip()]
    social = lead.get("social") or {}
    for key, url in (social.items() if isinstance(social, dict) else []):
        if url:                                          # site markup records each one as provenance["social.<key>"]
            rows.append(_row(lead, f"social.{key}", SOCIAL.get(key, str(key).capitalize()), url, edits, history))
    reg = lead.get("registry") or {}
    if reg.get("cin"):
        rows.append({"field": "registry", "label": "Company register record", "value": f"{reg.get('name') or ''} (CIN {reg['cin']})".strip(),
                     "source": "mca-master-data", "source_label": TAG_LABELS["mca-master-data"], "detail": reg.get("status") or "",
                     "kind": "confirmed" if reg.get("confidence") == "confirmed" else "found", "at": reg.get("matched_at"),
                     "at_basis": "register match", "by": reg.get("matched_by") or ""})
    return {"lead_id": lead["id"], "origin": origin(lead), "fields": rows,
            "not_recorded": sum(1 for r in rows if r["source_label"] == NOT_RECORDED)}
