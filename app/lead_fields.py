"""Custom lead fields: the owner adds the details their industry tracks (budget, property type, seats, course...).

Definitions live in settings "lead_fields" as a list of
    {key, label, type, options, show_in_table, filterable}
type is one of FIELD_TYPES. Values are saved on the lead under `custom` ({key: value}); numbers are stored as numbers
and dates as YYYY-MM-DD so flow conditions can compare them. A value for a field that was later deleted stays on the
lead (nothing is thrown away) but is no longer shown or exported.

Edits by hand go through `edit_custom`, which logs each change like a contact correction (app/audit.py): one audit row
per field and one line on the lead's timeline. Templates use the values as {{custom.<key>}} (app/template_vars.py).
"""
import re
from datetime import date, datetime

from .audit import MAX_AUDIT_ROWS, EditRefused
from .lead_model import event, now_iso
from .template_vars import CUSTOM_KEY

SETTINGS_KEY = "lead_fields"
FIELD_TYPES = ("text", "number", "date", "select", "url", "phone", "email")
MAX_FIELDS, MAX_OPTIONS, MAX_LABEL, MAX_OPTION, MAX_VALUE = 40, 50, 60, 80, 500
# Keys the lead already uses for its own details: a custom field may not shadow them in imports or exports.
RESERVED = frozenset({"id", "company", "contact_name", "first_name", "last_name", "title", "email", "phone", "website",
                      "domain", "country", "city", "state", "stage", "score", "band", "source", "tags", "owner", "notes",
                      "custom", "role", "sector", "open_roles", "company_size", "linkedin_url"})
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


def key_from_label(label: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", str(label or "").lower()).strip("_")
    key = re.sub(r"^[0-9_]+", "", key)[:40].rstrip("_")
    return key or "field"


def list_fields(store) -> list:
    raw = store.read("settings").get(SETTINGS_KEY)
    return [f for f in raw if isinstance(f, dict) and f.get("key")] if isinstance(raw, list) else []


def clean_fields(raw) -> list:
    """Validated field definitions, or ValueError in plain words."""
    if not isinstance(raw, list):
        raise ValueError("Send the fields as a list.")
    if len(raw) > MAX_FIELDS:
        raise ValueError(f"At most {MAX_FIELDS} custom fields.")
    out, keys = [], set()
    for i, item in enumerate(raw):
        where = f"Field {i + 1}"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be an object.")
        label = " ".join(str(item.get("label") or "").split())[:MAX_LABEL]
        if not label:
            raise ValueError(f"{where}: give it a name.")
        key = str(item.get("key") or "").strip().lower() or key_from_label(label)
        if not CUSTOM_KEY.match(key):
            raise ValueError(f"{where}: the key '{key}' must start with a letter and use only a-z, 0-9 and _ (up to 40).")
        if key in RESERVED:
            raise ValueError(f"{where}: '{key}' is already a built-in lead detail; pick another key.")
        if key in keys:
            raise ValueError(f"{where}: the key '{key}' is used twice.")
        keys.add(key)
        kind = str(item.get("type") or "text").lower()
        if kind not in FIELD_TYPES:
            raise ValueError(f"{where}: the type must be one of {', '.join(FIELD_TYPES)}.")
        options = []
        if kind == "select":
            raw_options = item.get("options") or []
            if isinstance(raw_options, str):
                raw_options = raw_options.split(",")
            for o in raw_options:
                text = " ".join(str(o or "").split())[:MAX_OPTION]
                if text and text not in options:
                    options.append(text)
            if not options:
                raise ValueError(f"{where}: a choice list needs at least one option.")
            if len(options) > MAX_OPTIONS:
                raise ValueError(f"{where}: at most {MAX_OPTIONS} options.")
        out.append({"key": key, "label": label, "type": kind, "options": options,
                    "show_in_table": bool(item.get("show_in_table")), "filterable": bool(item.get("filterable"))})
    return out


def save_fields(store, raw, by: str = "") -> list:
    fields = clean_fields(raw)
    store.update("settings", lambda s: {**s, SETTINGS_KEY: fields, "lead_fields_updated": {"at": now_iso(), "by": by}})
    return fields


def coerce(field: dict, raw):
    """The stored form of one value ("" clears it). Raises EditRefused with a plain reason."""
    label, kind = field["label"], field["type"]
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return ""
    text = " ".join(str(raw).split())[:MAX_VALUE]
    if kind == "number":
        cleaned = re.sub(r"[,\s₹$]", "", text)
        try:
            number = float(cleaned)
        except ValueError:
            raise EditRefused(f"{label}: '{text}' is not a number.") from None
        if number != number or abs(number) > 1e15:
            raise EditRefused(f"{label}: '{text}' is not a usable number.")
        return int(number) if number.is_integer() else round(number, 4)
    if kind == "date":
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(text, fmt).date().isoformat()
            except ValueError:
                continue
        raise EditRefused(f"{label}: '{text}' is not a date (use YYYY-MM-DD or DD/MM/YYYY).")
    if kind == "select":
        match = next((o for o in field.get("options") or [] if o.lower() == text.lower()), None)
        if match is None:
            raise EditRefused(f"{label}: '{text}' is not one of the choices ({', '.join(field.get('options') or [])}).")
        return match
    if kind == "url":
        link = text if re.match(r"^https?://", text, re.I) else f"https://{text}"
        if not re.match(r"^https?://[^\s/]+\.[^\s/]+", link, re.I):
            raise EditRefused(f"{label}: '{text}' is not a web link.")
        return link
    if kind == "email":
        if not _EMAIL.match(text):
            raise EditRefused(f"{label}: '{text}' is not an email address.")
        return text.lower()
    if kind == "phone":
        digits = re.sub(r"[^\d+]", "", text)
        if len(re.sub(r"\D", "", digits)) < 6:
            raise EditRefused(f"{label}: '{text}' is not a phone number.")
        return digits
    return text


def coerce_soft(field: dict, raw):
    """For imports: the stored form, or None when the value can't be read (the row is kept, the value skipped)."""
    try:
        return coerce(field, raw)
    except EditRefused:
        return None


def edit_custom(store, lead_id: str, values: dict, actor: dict, reason: str = "") -> dict:
    """Change custom values on one lead. Returns {"lead", "changed"}. Raises LookupError / EditRefused."""
    if not isinstance(values, dict) or not values:
        raise EditRefused("Nothing to change.")
    fields = {f["key"]: f for f in list_fields(store)}
    unknown = [k for k in values if k not in fields]
    if unknown:
        raise EditRefused(f"Unknown field(s): {', '.join(map(str, unknown[:5]))}. Add them under Setup → Industry & fields first.")
    cleaned = {k: coerce(fields[k], v) for k, v in values.items()}
    current = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if current is None:
        raise LookupError("Lead not found")
    before = current.get("custom") or {}
    cleaned = {k: v for k, v in cleaned.items() if before.get(k, "") != v}
    if not cleaned:
        return {"lead": current, "changed": []}
    rows, stamp = [], now_iso()

    def apply(lead: dict):
        old = dict(lead.get("custom") or {})
        rows.clear()
        for key, value in cleaned.items():
            rows.append({"lead_id": lead_id, "field": f"custom.{key}", "label": fields[key]["label"], "old": _shown(old.get(key)),
                         "new": _shown(value), "by": actor["name"], "by_email": actor["email"], "at": stamp,
                         "reason": reason[:300], "company": lead.get("company") or ""})
            if value == "":
                old.pop(key, None)
            else:
                old[key] = value
        lines = "; ".join(f"{r['label']}: {r['old'] or '(empty)'} → {r['new'] or '(empty)'}" for r in rows)
        why = f" Reason: {reason[:300]}" if reason else ""
        return {**lead, "custom": old,
                "timeline": [*lead["timeline"], {**event("edit", f"{actor['name']} edited {lines}.{why}"), "by": actor["name"]}]}
    saved = store.update_lead(lead_id, apply)
    if saved is None:
        raise LookupError("Lead not found")
    store.update("audit", lambda old: [*old, *rows][-MAX_AUDIT_ROWS:])
    return {"lead": saved, "changed": [r["field"] for r in rows]}


def _shown(value) -> str:
    return "" if value is None else str(value)


def match_header(fields: list, header: str):
    """The custom field a file column belongs to (by key or label, ignoring case, spaces and punctuation), or None."""
    from .header_map import loose_header, norm_header
    wanted = {norm_header(header), loose_header(header)}
    for f in fields:
        if {norm_header(f["key"].replace("_", " ")), norm_header(f["label"]), loose_header(f["label"])} & wanted:
            return f
    return None


def sample_custom(store) -> dict:
    """Example values for template previews (a sample person has no real custom data)."""
    examples = {"number": 25, "date": date.today().isoformat(), "url": "https://example.com", "phone": "+91 98765 43210",
                "email": "alex@example.com"}
    out = {}
    for f in list_fields(store):
        out[f["key"]] = (f["options"][0] if f["type"] == "select" and f.get("options") else
                         examples.get(f["type"], f"(your {f['label'].lower()})"))
    return out
