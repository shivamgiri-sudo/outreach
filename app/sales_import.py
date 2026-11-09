"""Sales funnel, part 5: the sales columns of imports, sheets and inbound leads.

Rajesh's sheet columns (Lead Status, MQL Date, SAL Date, SQL Date, Meeting Held Date, Proposal Date, Negotiation Date,
Closed Date, Recycled Date, Lost Stage, Lost Reason, Est. Monthly Value, Closed Monthly Value, Contract Tenure, Lead Owner,
Lead Source, Industry, Service Line, Lead Score (Fit), Engagement, Remarks) are read from any file or row, whatever the
importer: by header name (`from_row`) or by the importer's own column mapping (`from_mapped`, fields named sales_*).

Re-importing a lead that already has a sales record updates it WITHOUT overwriting stage dates that are already set.
"""
import re
from datetime import datetime
from typing import Optional

from . import sales_model as M

# Importer field -> header names (case, spaces and punctuation do not matter). "lead_status", "source_detail" (Lead Source),
# "sector" (Industry) and "notes" (Remarks) already exist in app/header_map.py and are used as they are.
ALIASES = {
    "sales_created": ["created date", "created on", "inquiry date", "enquiry date"],
    "sales_mql": ["mql date", "mql"],
    "sales_sal": ["sal date", "sal"],
    "sales_sql": ["sql date", "sql"],
    "sales_meeting": ["meeting held date", "meeting date", "discovery meeting date", "meeting held"],
    "sales_proposal": ["proposal date", "proposal submitted date", "proposal submitted"],
    "sales_negotiation": ["negotiation date", "negotiation commercials date"],
    "sales_closed": ["closed date", "close date", "won date"],
    "sales_recycled": ["recycled date", "nurture date", "recycled nurture date"],
    "sales_first_response": ["first response date", "first response"],
    "sales_lost_stage": ["lost stage", "lost at stage"],
    "sales_lost_reason": ["lost reason", "loss reason", "reason lost"],
    "sales_est_value": ["est monthly value", "estimated monthly value", "est monthly value inr", "est value", "estimated value"],
    "sales_closed_value": ["closed monthly value", "closed monthly value inr", "closed value", "won monthly value"],
    "sales_tenure": ["contract tenure months", "contract tenure", "tenure months", "tenure"],
    "sales_fit": ["lead score fit", "lead score", "icp fit"],
    "sales_engagement": ["engagement", "engagement level"],
    "sales_service": ["service line", "service", "services"],
    "sales_owner": ["lead owner", "sales owner", "lead owner name"],
    "sales_status": ["lead status", "sales status", "sales stage"],
    "sales_source": ["lead source"],
    "sales_industry": ["industry"],
}
LABELS = {
    "sales_created": "Created date", "sales_mql": "MQL date", "sales_sal": "SAL date", "sales_sql": "SQL date",
    "sales_meeting": "Meeting held date", "sales_proposal": "Proposal date", "sales_negotiation": "Negotiation date",
    "sales_closed": "Closed date", "sales_recycled": "Recycled date", "sales_first_response": "First response date",
    "sales_lost_stage": "Lost stage", "sales_lost_reason": "Lost reason", "sales_est_value": "Est. monthly value (₹)",
    "sales_closed_value": "Closed monthly value (₹)", "sales_tenure": "Contract tenure (months)", "sales_fit": "Lead score (fit A/B/C)",
    "sales_engagement": "Engagement (Hot/Warm/Cold)", "sales_service": "Service line", "sales_owner": "Lead owner",
}
# Fields the generic importer maps already, so header_map need not know them twice.
HEADER_MAP_FIELDS = tuple(LABELS)
DATE_FIELDS = {"sales_created": "inquiry", "sales_mql": "mql", "sales_sal": "sal", "sales_sql": "sql", "sales_meeting": "meeting",
               "sales_proposal": "proposal", "sales_negotiation": "negotiation", "sales_closed": "closed", "sales_recycled": "recycled"}
_BY_HEADER = {}
for _field, _names in ALIASES.items():
    for _name in _names:
        _BY_HEADER.setdefault(_name, _field)


def norm(header) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(header or "").lower().replace("&", " and ")).split())


def field_of(header) -> Optional[str]:
    return _BY_HEADER.get(norm(header))


def build(pairs: dict) -> tuple:
    """({"sales": record-so-far or {}, "owner": text}, [warnings]) from {importer field: raw text}."""
    s, warnings, owner = {}, [], ""
    dates = {}
    for field, raw in pairs.items():
        text = " ".join(str(raw).split()) if raw not in (None, "") else ""
        if not text:
            continue
        if field in DATE_FIELDS:
            dt = M.parse_dt(raw if isinstance(raw, datetime) else text)
            if dt is None:
                warnings.append(f"{LABELS.get(field, field)} '{text[:30]}' is not a date (use yyyy/mm/dd hh:mm); left out.")
            else:
                dates[DATE_FIELDS[field]] = M.iso(dt)
        elif field == "sales_first_response":
            dt = M.parse_dt(raw if isinstance(raw, datetime) else text)
            if dt is not None:
                s["first_response"] = M.iso(dt)
        elif field in ("sales_status", "lead_status"):
            status = M.clean_status(text)
            if status:
                s["status"] = status
            elif field == "sales_status":
                warnings.append(f"Lead Status '{text[:40]}' is not one of the sales stages; left out.")
        elif field == "sales_lost_stage":
            status = M.clean_status(text)
            if status in M.STATUSES:
                s["lost_stage"] = status
        elif field == "sales_lost_reason":
            match = next((r for r in M.LOST_REASONS if r.lower() == text.lower()), "")
            s["lost_reason"] = match or "Other"
        elif field in ("sales_est_value", "sales_closed_value", "sales_tenure"):
            value = M.money(text)
            key = {"sales_est_value": "est_value", "sales_closed_value": "closed_value", "sales_tenure": "tenure"}[field]
            if value > 0:
                s[key] = value
            else:
                warnings.append(f"{LABELS[field]} '{text[:20]}' is not a number; left out.")
        elif field == "sales_fit":
            letter = text.upper()[:1]
            if letter in M.FITS:
                s["fit"] = letter
        elif field == "sales_engagement":
            word = text.capitalize()
            if word in M.ENGAGEMENTS:
                s["engagement"] = word
        elif field == "sales_service":
            s["service"] = text[:80]
        elif field == "sales_source":
            s["source"] = text[:80]
        elif field == "sales_industry":
            s["industry"] = text[:80]
        elif field == "sales_owner":
            owner = text[:80]
    if dates:
        s["dates"] = dates
    return {"sales": s, "owner": owner}, warnings


def from_row(row: dict) -> tuple:
    """Read the sales columns of one raw row ({header: value}); other columns are ignored."""
    pairs = {}
    for header, value in (row or {}).items():
        field = field_of(header)
        if field and field not in pairs and value not in (None, ""):
            pairs[field] = value
    return build(pairs)


def from_mapped(row: dict, columns: list) -> tuple:
    """Same, through the importer's column mapping (fields named sales_*, plus the generic lead_status / source_detail / sector)."""
    pairs = {}
    for c in columns:
        field = c.get("field")
        raw = row.get(c.get("column"))
        if raw in (None, ""):
            continue
        if field in ALIASES and field not in pairs:
            pairs[field] = raw
        elif field == "lead_status" and "sales_status" not in pairs:
            pairs["sales_status"] = raw
        elif field == "source_detail" and "sales_source" not in pairs:
            pairs["sales_source"] = raw
        elif field == "sector" and "sales_industry" not in pairs:
            pairs["sales_industry"] = raw
    return build(pairs)


def has_data(parsed: dict) -> bool:
    return bool(parsed and (parsed.get("sales") or parsed.get("owner")))


def apply_incoming(existing: Optional[dict], incoming: dict, created: Optional[datetime], now: datetime, by: str = "import") -> tuple:
    """(new record, [warnings], changed) when `incoming` (from build()) meets a lead's `existing` sales record.

    Values the file gives replace blank or older values; stage dates only fill blanks. A new status goes through the
    status rules (so a Proposal without a Meeting date is refused with the reason); skipped stages are back-filled with
    the Created Date, because the real history is not in the file.
    """
    old = existing if isinstance(existing, dict) else {}
    inc = incoming or {}
    warnings = []
    cand = {**old}
    for key, value in inc.items():
        if key in ("dates", "status"):
            continue
        if value not in (None, "", 0):
            cand[key] = value
    cand["dates"] = {**(inc.get("dates") or {}), **{k: v for k, v in (old.get("dates") or {}).items() if v}}   # existing dates win
    new_status = inc.get("status")
    old_status = M.stored_status(old)
    if new_status and new_status != old_status:
        stamp = M.parse_dt(cand["dates"].get("inquiry")) or created or now
        result = M.apply_status(cand, new_status, old_status, stamp)
        if result["ok"]:
            cand = M.merge_set(cand, result["set"], new_status)
            cand["history"] = [*(old.get("history") or []), M.history_row(by, old_status, new_status, now)][-M.MAX_HISTORY:]
        else:
            warnings.append(f"Lead Status '{new_status}' not applied: {result['error']}")
    elif new_status and not old.get("status"):
        cand["status"] = new_status
    changed = cand != old
    return cand, warnings, changed


def reimport(leads: list, dedupe_key: str, row: dict, ok, now: datetime) -> bool:
    """A quick-import row that matches an existing lead (same dedupe key): update that lead's sales record from the row's
    sales columns and fill a missing owner. `ok(lead)` says whether the lead may be changed (its account and workspace)."""
    parsed, _notes = from_row(row)
    if not has_data(parsed):
        return False
    for i, lead in enumerate(leads):
        if lead.get("dedupe_key") != dedupe_key or not ok(lead):
            continue
        changed, out = False, {**lead}
        if parsed["sales"]:
            new, _warnings, did = apply_incoming(lead.get("sales"), parsed["sales"], M.parse_dt(lead.get("created_at")), now, "import")
            if did:
                out["sales"], changed = new, True
        if parsed["owner"] and not lead.get("owner"):
            out.update(owner=parsed["owner"], owner_at=M.iso(now))
            changed = True
        if changed:
            leads[i] = out
        return changed
    return False
