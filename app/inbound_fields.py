"""Turn whatever a lead generator sent into plain lead fields, attribution, platform ids and consent answers.

Field names are matched loosely (name / full_name / first_name + last_name, email, phone / mobile / whatsapp, company /
organization / business name ...). A connector's `field_map` ({"email": "contact.mail_address"}) always wins and takes
dotted paths. Every attribution value is whitelisted, forced to text and length-capped.
"""
import re

MAX_VALUE = 500
MAX_CUSTOM = 20
MAX_PATH_DEPTH = 6
ATTRIBUTION_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "campaign_id", "campaign_name",
                    "adset_id", "adset_name", "ad_id", "ad_name", "form_id", "form_name", "platform", "click_id",
                    "landing_url", "referrer", "captured_at")
ATTRIBUTION_CAP = 300
STANDARD = ("name", "first_name", "last_name", "email", "phone", "company", "title", "city", "website", "message", "country")

ALIASES = {
    "name": ("name", "full_name", "fullname", "contact_name", "person_name", "your_name", "customer_name", "lead_name",
             "sender_name", "buyer_name"),
    "first_name": ("first_name", "firstname", "fname", "given_name"),
    "last_name": ("last_name", "lastname", "lname", "surname", "family_name"),
    "email": ("email", "e_mail", "email_address", "work_email", "business_email", "mail", "sender_email", "email_id",
              "your_email"),
    "phone": ("phone", "phone_number", "mobile", "mobile_number", "mobile_no", "whatsapp", "whatsapp_number", "contact_number",
              "telephone", "tel", "cell", "work_phone", "sender_mobile", "your_phone", "contact_no", "phone_no"),
    "company": ("company", "company_name", "organization", "organisation", "organization_name", "business_name", "business",
                "firm", "employer", "sender_company", "your_company", "sender_co"),
    "title": ("title", "job_title", "designation", "position", "role"),
    "city": ("city", "town", "sender_city", "location", "your_city"),
    "website": ("website", "company_website", "url_of_website", "web_site", "site", "company_url", "domain"),
    "message": ("message", "comments", "comment", "enquiry", "inquiry", "query", "requirement", "notes", "description",
                "how_can_we_help", "your_message"),
    "country": ("country", "country_name"),
}
ID_KEYS = ("leadgen_id", "lead_id", "leadid", "unique_query_id", "inquiry_id", "enquiry_id", "submission_id", "entry_id",
           "response_id", "request_id")
ATTRIBUTION_ALIASES = {
    "fbclid": "click_id", "gclid": "click_id", "gcl_id": "click_id", "ttclid": "click_id", "msclkid": "click_id",
    "page_url": "landing_url", "page": "landing_url", "landing_page": "landing_url", "source_url": "landing_url",
    "referer": "referrer", "referrer_url": "referrer", "adgroup_id": "adset_id", "adgroup_name": "adset_name",
    "creative_id": "ad_id", "creative_name": "ad_name", "campaign": "campaign_name",
}
YES = {"yes", "y", "true", "1", "on", "checked", "agree", "agreed", "accept", "accepted", "opt_in", "optin", "i_agree"}
NO = {"no", "n", "false", "0", "off", "unchecked", "disagree", "decline", "declined", "opt_out", "optout"}
WHATSAPP_OPTIN_KEYS = ("whatsapp_optin", "whatsapp_opt_in", "whatsapp_consent", "wa_optin", "wa_consent", "optin_whatsapp",
                       "opt_in_whatsapp", "whatsapp_updates", "contact_on_whatsapp")
EMAIL_OPTIN_KEYS = ("email_optin", "email_opt_in", "email_consent", "optin_email")
CALL_OPTIN_KEYS = ("call_optin", "call_opt_in", "call_consent", "phone_optin", "phone_consent")
GENERAL_CONSENT_KEYS = ("consent", "agree", "agreed", "accept_terms", "terms", "privacy", "privacy_policy", "marketing_consent",
                        "contact_consent", "i_agree", "gdpr")


PAIR_LISTS = ("field_data", "user_column_data", "fields", "form_fields", "answers", "data")


def _key(text) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")


def clip(value, limit: int = MAX_VALUE) -> str:
    if isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if v is not None)
    if isinstance(value, (dict, bytes)) or value is None:
        return ""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value)).strip()[:limit]


def dig(payload, path: str):
    """Value at a dotted path ("a.b.0.c"; list items by index), or None."""
    node = payload
    for part in str(path or "").split(".")[:MAX_PATH_DEPTH]:
        if isinstance(node, dict):
            node = node.get(part) if part in node else next((v for k, v in node.items() if _key(k) == _key(part)), None)
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
        if node is None:
            return None
    return node


def flatten(payload, prefix: str = "", out: dict = None, depth: int = 0) -> dict:
    """All scalar values of a nested payload keyed by their normalised key (the last part wins ties; top level first)."""
    out = {} if out is None else out
    if depth > MAX_PATH_DEPTH:
        return out
    if isinstance(payload, dict):
        for k, v in payload.items():
            if _key(k) in PAIR_LISTS and isinstance(v, list):
                continue                          # name/value lists are read by name_value_pairs, not as keys called "name"
            if isinstance(v, list) and v and all(not isinstance(i, (dict, list)) for i in v):
                out.setdefault(_key(k), v)
            elif isinstance(v, (dict, list)):
                flatten(v, _key(k), out, depth + 1)
            elif v is not None and v != "":
                out.setdefault(_key(k), v)
    elif isinstance(payload, list):
        for item in payload[:50]:
            if isinstance(item, dict):
                flatten(item, prefix, out, depth + 1)
    return out


def name_value_pairs(payload) -> dict:
    """Meta field_data [{"name","values"}], Google user_column_data [{"column_id","string_value"}] and Zapier-style
    [{"key","value"}] lists, as one plain dict."""
    out = {}
    for item in payload if isinstance(payload, list) else []:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("column_id") or item.get("key") or item.get("column_name")
        value = item.get("values") if "values" in item else item.get("string_value", item.get("value", item.get("text")))
        if name and value not in (None, "", []):
            out.setdefault(_key(name), value)
            label = item.get("column_name") or item.get("label")
            if label and item.get("column_id"):
                out.setdefault("label:" + _key(label), value)
    return out


def _lists_to_dict(payload: dict) -> dict:
    """Merge the name/value lists a platform nests (field_data, user_column_data, fields) into the top level."""
    merged = dict(payload) if isinstance(payload, dict) else {}
    for key in PAIR_LISTS:
        value = merged.get(key)
        if isinstance(value, list):
            for k, v in name_value_pairs(value).items():
                merged.setdefault(k, v)
    return merged


def truthy(value):
    """True / False for a yes / no answer, None when it says neither."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    text = _key(value)
    return True if text in YES else False if text in NO else None


def _first_of(flat: dict, keys) -> object:
    for key in keys:
        value = flat.get(_key(key))
        if value not in (None, "", []):
            return value
    return None


def consent_answers(flat: dict) -> dict:
    """{"whatsapp": True/False/None, "email": ..., "call": ..., "general": ...} from explicit opt-in fields."""
    def answer(keys):
        seen = [truthy(flat[k]) for k in keys if k in flat]
        seen = [s for s in seen if s is not None]
        return False if False in seen else True if True in seen else None
    return {"whatsapp": answer(WHATSAPP_OPTIN_KEYS), "email": answer(EMAIL_OPTIN_KEYS), "call": answer(CALL_OPTIN_KEYS),
            "general": answer(GENERAL_CONSENT_KEYS)}


def clean_attribution(raw: dict) -> dict:
    out = {}
    for key, value in (raw or {}).items():
        k = _key(key)
        k = ATTRIBUTION_ALIASES.get(k, k)
        if k in ATTRIBUTION_KEYS and not isinstance(value, dict) and clip(value, ATTRIBUTION_CAP):
            out.setdefault(k, clip(value, ATTRIBUTION_CAP))
    return out


def extract(payload, field_map: dict = None, id_path: str = "", extra_attribution: dict = None) -> dict:
    """{"fields", "custom", "attribution", "ids", "consent", "platform_id"} from one payload."""
    body = _lists_to_dict(payload if isinstance(payload, dict) else {})
    flat = flatten(body)
    for key in PAIR_LISTS:                                   # lists nested deeper (entry[].changes[].value.field_data)
        for k, v in name_value_pairs(dig(body, key) if isinstance(dig(body, key), list) else None).items():
            flat.setdefault(k, v)
    fields, attribution = {}, {}
    for target, path in (field_map or {}).items():
        value = dig(body, path) if "." in str(path) or str(path) in body else flat.get(_key(path))
        if value is None:
            value = flat.get(_key(path))
        if value in (None, "", []):
            continue
        if target.startswith("attribution."):
            attribution[target.split(".", 1)[1]] = value
        elif target.startswith("custom."):
            fields.setdefault("custom:" + target.split(".", 1)[1], value)
        else:
            fields[target] = value
    for target, names in ALIASES.items():
        if target not in fields:
            value = _first_of(flat, names)
            if value is not None:
                fields[target] = value
    first, last = clip(fields.pop("first_name", "")), clip(fields.pop("last_name", ""))
    if first or last:
        fields["name"] = clip(fields.get("name")) if fields.get("name") and not (first and last) else f"{first} {last}".strip()
    custom = {}
    for k, v in list(fields.items()):
        if k.startswith("custom:"):
            custom[k[7:]] = clip(v, 300)
            del fields[k]
    known = {_key(n) for names in ALIASES.values() for n in names} | set(ID_KEYS) | set(ATTRIBUTION_KEYS) | set(ATTRIBUTION_ALIASES) \
        | set(WHATSAPP_OPTIN_KEYS + EMAIL_OPTIN_KEYS + CALL_OPTIN_KEYS + GENERAL_CONSENT_KEYS) \
        | {"token", "capture_token", "form_key", "_form_key", "hp", "website_url_hp", "_hp", "_t", "_started", "idempotency_key",
           "google_key", "is_test", "api_version", "created_time", "entry", "object", "field_data", "user_column_data",
           "gcl_id", "adgroup_id", "creative_id", "page_id", "leadgen_id", "response", "code", "status"}
    mapped = {_key(str(p).split(".")[-1]) for p in (field_map or {}).values()} | {_key(p) for p in (field_map or {}).values()}
    for k, v in flat.items():
        if len(custom) >= MAX_CUSTOM:
            break
        if k in known or k.startswith("label:") or k in mapped:
            continue
        text = clip(v, 300)
        if text and len(k) <= 60:
            custom[k] = text
    for key in ATTRIBUTION_KEYS + tuple(ATTRIBUTION_ALIASES):
        if key in flat and key not in mapped and key not in ("captured_at",):
            attribution.setdefault(key, flat[key])
    for key, value in (extra_attribution or {}).items():
        attribution.setdefault(key, value)
    ids = {}
    for key in ID_KEYS:
        if flat.get(key):
            ids[key] = clip(flat[key], 100)
    platform_id = clip(dig(body, id_path), 100) if id_path else ""
    platform_id = platform_id or next(iter(ids.values()), "")
    return {"fields": {k: clip(v) for k, v in fields.items() if k in STANDARD}, "custom": custom,
            "attribution": clean_attribution(attribution), "ids": ids, "consent": consent_answers(flat), "platform_id": platform_id,
            "is_test": truthy(flat.get("is_test")) is True}
