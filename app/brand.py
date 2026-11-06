"""White label: who is selling (settings "brand"), what they sell (settings "offer"), how stages and lead numbers are
named (settings "stage_labels", "lead_terms"), and the template variables built from them.

Everything has a default that matches the original single-business set-up (BookMyInterview.ai by Mas Callnet), so an
install that never opens the Brand page looks and sends exactly as before. `brand_variables` is the one place the
{{my_*}} placeholders are built; sequences.lead_variables adds them for every channel.
"""
import base64
import hashlib
import re
from typing import Optional
from xml.etree import ElementTree

DEFAULT_BRAND = {
    "app_name": "MCN Outreach",
    "company_name": "Mas Callnet",
    "product_name": "BookMyInterview.ai",
    "tagline": "Find hiring employers, start honest conversations and book demos.",
    "logo_url": "",                       # set when a logo is uploaded (/api/brand/logo?v=…); empty = the built-in logos
    "primary_color": "#0369a1",
    "accent_color": "#e8552b",
    "website": "https://bookmyinterview.ai",
    "support_email": "",
    "sender_name": "BookMyInterview team",
    "sign_off": "Regards,\nTeam BookMyInterview.ai",
    "postal_address": "",
    "industry": "hiring",
}
# Built-in pictures used while no logo is uploaded: the app's own mark in the menu, the product logo in emails.
DEFAULT_HEADER_LOGO = "/mcn-logo.png"
DEFAULT_EMAIL_LOGO = "/bmi-logo.png"

DEFAULT_OFFER_PROFILE = {
    "what": "candidates are screened once by a short voice answer and a few MCQs, and the best ones book their own interview slot.",
    "who": "employers",
    "seller": "an Indian hiring-tech team",
    "status_note": "in beta",
    "value_1": "Every applicant screened once, by a short voice answer and a few MCQs",
    "value_2": "The best candidates book their own interview slot",
    "value_3": "Your first role is free to try",
    "cta": "Would a brief conversation be worthwhile?",
    "booking_link": "https://bookmyinterview.ai/register",
    "goal": "offer to draft a free sample screen for their open role",
    "need_label": "hiring for",           # "The lead is hiring for: …" in AI prompts
    "pain": "their hiring problem",
    "topic": "hiring",                    # "who is the right person to speak to about hiring?"
    "next_step": "Shall I set up your first role for you?",
    "offer_line": "",                     # empty = the built-in free-trial sentence (growth_core.offer_line)
    "demo_kind": "screening",             # screening = sample screening kit; overview = product overview page
}
# What a rebranded install starts from for any offer field its owner has not filled in: never the hiring wording.
GENERIC_OFFER_PROFILE = {
    "what": "", "who": "businesses", "seller": "", "status_note": "", "value_1": "", "value_2": "", "value_3": "",
    "cta": "Would a short call be useful?", "booking_link": "", "goal": "start a conversation and offer a short call",
    "need_label": "interested in", "pain": "their needs", "topic": "this", "next_step": "Shall we set up a short call?",
    "offer_line": "Happy to share more on a short call.", "demo_kind": "overview",
}
DEFAULT_LEAD_TERMS = {"open_roles": "Open roles", "role": "Role"}

BRAND_FIELDS = tuple(DEFAULT_BRAND)
OFFER_FIELDS = tuple(DEFAULT_OFFER_PROFILE)
TEXT_LIMITS = {"tagline": 160, "sign_off": 200, "postal_address": 300, "what": 400, "seller": 120, "goal": 200,
               "value_1": 140, "value_2": 140, "value_3": 140, "cta": 160, "offer_line": 300, "pain": 120, "next_step": 160}
DEFAULT_TEXT_LIMIT = 80
DEMO_KINDS = ("screening", "overview")
BRAND_PLACEHOLDERS = ("my_company", "my_product", "my_tagline", "my_value_1", "my_value_2", "my_value_3", "my_cta",
                      "my_website", "sender", "sign_off")
LOGO_MAX_BYTES = 1024 * 1024
LOGO_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/svg+xml": "svg"}
MIN_CONTRAST = 3.0                         # white button text on the primary colour (WCAG large text / UI parts)
_HEX = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _settings(source) -> dict:
    """A settings dict from either a store or a settings dict."""
    if hasattr(source, "read"):
        return source.read("settings") or {}
    return source or {}


def _clean(value) -> str:
    return re.sub(r"[ \t]+", " ", str(value or "")).strip()


def _generic_brand(saved: dict) -> dict:
    """What a rebranded install shows for fields its owner left blank: built from their own names, never the
    original business's website, sign-off or logo."""
    company = _clean(saved.get("company_name")) or DEFAULT_BRAND["company_name"]
    product = _clean(saved.get("product_name")) or company
    return {**DEFAULT_BRAND, "company_name": company, "product_name": product, "tagline": "", "website": "",
            "sender_name": f"{product} team", "sign_off": f"Regards,\nTeam {product}", "industry": "",
            "primary_color": DEFAULT_BRAND["primary_color"], "accent_color": DEFAULT_BRAND["accent_color"]}


def get_brand(source) -> dict:
    """The brand with every field filled: saved values win; blank ones fall back to the original brand while nothing
    is saved, else to wording built from the owner's own names."""
    saved = _settings(source).get("brand") or {}
    base = DEFAULT_BRAND if brand_is_default({"brand": saved}) else _generic_brand(saved)
    out = {k: (saved.get(k) if str(saved.get(k) or "").strip() else v) for k, v in base.items()}
    out["logo_url"] = saved.get("logo_url") or ""
    return out


def get_offer_profile(source) -> dict:
    settings = _settings(source)
    saved = settings.get("offer") or {}
    saved = saved if isinstance(saved, dict) else {}
    base = DEFAULT_OFFER_PROFILE if brand_is_default(settings) else GENERIC_OFFER_PROFILE
    return {k: (saved[k] if k in saved and saved[k] is not None else v) for k, v in base.items()}


def lead_terms(source) -> dict:
    saved = _settings(source).get("lead_terms") or {}
    return {k: (str(saved.get(k)).strip() or v) if saved.get(k) else v for k, v in DEFAULT_LEAD_TERMS.items()}


def stage_labels(source) -> dict:
    """Only the stages the owner renamed: {stage_id: label}."""
    saved = _settings(source).get("stage_labels") or {}
    return {k: v for k, v in saved.items() if isinstance(v, str) and v.strip()}


def stage_label(source, stage: str) -> str:
    """The word shown for a stage id (ids stay fixed in the backend; only the words change)."""
    return stage_labels(source).get(stage) or DEFAULT_STAGE_LABELS.get(stage) or str(stage or "")


DEFAULT_STAGE_LABELS = {
    "new": "New", "verified": "Verified", "enriched": "Enriched", "contacted": "Contacted", "engaged": "Engaged",
    "interested": "Interested", "whatsapp_active": "WhatsApp active", "call_done": "Call done", "trial": "Trial",
    "customer": "Customer", "advocate": "Advocate", "not_now": "Not now", "unsubscribed": "Unsubscribed",
    "disqualified": "Disqualified",
}


def sender_name(source) -> str:
    """Who signs: the saved sender name (older installs keep it at the top of settings), else the brand's."""
    settings = _settings(source)
    if "sender_name" in settings:          # saved, even blank: a blank sender holds emails as unfilled, never guessed
        return str(settings.get("sender_name") or "")
    return str(get_brand(settings)["sender_name"])


def brand_variables(source) -> dict:
    """{{my_company}}, {{my_product}}, … for templates in every channel. Blank values are left out, so a template
    that uses them is flagged as having an unfilled placeholder instead of sending a gap."""
    settings = _settings(source)
    brand, offer = get_brand(settings), get_offer_profile(settings)
    values = {
        "my_company": brand["company_name"], "my_product": brand["product_name"], "my_tagline": brand["tagline"],
        "my_value_1": offer["value_1"], "my_value_2": offer["value_2"], "my_value_3": offer["value_3"],
        "my_cta": offer["cta"], "my_website": brand["website"], "sender": sender_name(settings),
        "sign_off": brand["sign_off"],
    }
    return {k: str(v).strip() for k, v in values.items() if str(v or "").strip()}


def fill_brand_placeholders(text: str, source, braces_for_format: bool = False) -> str:
    """Fill only the brand placeholders in a text (for templates that use another syntax for their own values, such
    as the LinkedIn steps). With braces_for_format, braces in the values are doubled so str.format leaves them."""
    values = brand_variables(source)

    def sub(match):
        value = values.get(match.group(1))
        if value is None:
            return match.group(0)
        return value.replace("{", "{{").replace("}", "}}") if braces_for_format else value
    return re.sub(r"\{\{(" + "|".join(BRAND_PLACEHOLDERS) + r")\}\}", sub, str(text or ""))


# ---- AI prompt context ----
def prompt_profile(source) -> dict:
    """What the AI writer is told about the seller, from the brand and the offer."""
    settings = _settings(source)
    brand, offer = get_brand(settings), get_offer_profile(settings)
    product = brand["product_name"]
    short = re.sub(r"\.(ai|com|in|io|co)$", "", product, flags=re.I) if product == DEFAULT_BRAND["product_name"] else product
    site = re.sub(r"^https?://|/$", "", brand["website"] or "")
    status = f" is {offer['status_note']}" if offer["status_note"].strip() else ""
    what = offer["what"].strip() or f"what {brand['company_name']} offers; give no details beyond the facts below."
    product_line = (f"{short}{f' ({site})' if site else ''}{status or ' is what you offer'}. "
                    f"Describe it only like this: {what}")
    honesty = (f"- Say plainly that you are from {short}" + (f" and that it is {offer['status_note']}" if status else "")
               + f". Describe it only as: {what.rstrip('.')}.")
    saved = settings.get("offer") or {}
    if not saved and product == DEFAULT_BRAND["product_name"]:
        honesty = ("- Say plainly that you are from BookMyInterview and that it is in beta. Describe it only as voice and MCQ "
                   "screening with self-booked interview slots.")
    return {"seller": offer["seller"] or f"the team at {brand['company_name']}", "audience": offer["who"] or "businesses",
            "need_label": offer["need_label"] or "interested in", "goal": offer["goal"] or GENERIC_OFFER_PROFILE["goal"],
            "pain": offer["pain"] or "their needs", "product_line": product_line, "honesty": honesty, "product": short}


# ---- validation ----
def _limit(field: str) -> int:
    return TEXT_LIMITS.get(field, DEFAULT_TEXT_LIMIT)


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    channels = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return round((la + 0.05) / (lb + 0.05), 2)


def with_scheme(text: str) -> str:
    """People type "skylinehomes.in" or "www.cal.com/you"; add https:// to a bare domain instead of refusing it."""
    if re.match(r"^(?![a-z][a-z0-9+.-]*://)[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?$", text, re.I):
        return "https://" + text
    return text


def validate_brand(changes: dict) -> dict:
    """Cleaned brand fields; raises ValueError with a sentence a person can act on."""
    out = {}
    for key, value in (changes or {}).items():
        if key not in BRAND_FIELDS or key == "logo_url" or value is None:
            continue
        text = str(value).replace("\r\n", "\n").strip() if key in ("sign_off", "postal_address") else _clean(value)
        if len(text) > _limit(key):
            raise ValueError(f"{key.replace('_', ' ').capitalize()} is too long (at most {_limit(key)} characters).")
        if "{{" in text or "}}" in text or "<" in text:
            raise ValueError(f"{key.replace('_', ' ').capitalize()} cannot contain {{{{ }}}} or < characters.")
        if key in ("primary_color", "accent_color") and text:
            if not _HEX.match(text):
                raise ValueError("Colours must be hex codes such as #0369a1.")
            text = text.lower()
        if key == "website" and text:
            text = with_scheme(text)
            if not re.match(r"^https?://[^\s/]+\.[^\s]+$", text):
                raise ValueError("The website must be a full address such as https://yourcompany.com.")
        if key == "support_email" and text and not _EMAIL.match(text):
            raise ValueError("The support email does not look like an email address.")
        out[key] = text
    if out.get("primary_color") and contrast_ratio(out["primary_color"], "#ffffff") < MIN_CONTRAST:
        raise ValueError(f"The main colour is too light for white button text (contrast "
                         f"{contrast_ratio(out['primary_color'], '#ffffff')}:1, needs {MIN_CONTRAST}:1). Pick a darker shade.")
    return out


def validate_offer(changes: dict) -> dict:
    out = {}
    for key, value in (changes or {}).items():
        if key not in OFFER_FIELDS or value is None:
            continue
        text = _clean(value)
        if len(text) > _limit(key):
            raise ValueError(f"{key.replace('_', ' ').capitalize()} is too long (at most {_limit(key)} characters).")
        if "{{" in text or "}}" in text or "<" in text:
            raise ValueError(f"{key.replace('_', ' ').capitalize()} cannot contain {{{{ }}}} or < characters.")
        if key == "booking_link" and text:
            text = with_scheme(text)
        if key == "booking_link" and text and not re.match(r"^https?://[^\s/]+\.[^\s]+$", text):
            raise ValueError("The demo / booking link must be a full address such as https://cal.com/you.")
        if key == "demo_kind" and text not in DEMO_KINDS:
            raise ValueError("Demo page style must be 'screening' or 'overview'.")
        out[key] = text
    return out


def validate_labels(stage_changes: Optional[dict], term_changes: Optional[dict]) -> tuple:
    from .stages import ALL_STAGES
    stages = {}
    for key, value in (stage_changes or {}).items():
        if key not in ALL_STAGES:
            raise ValueError(f"Unknown stage '{key}'.")
        text = _clean(value)
        if len(text) > 40 or "<" in text:
            raise ValueError("Stage names must be at most 40 characters, without < characters.")
        if text and text != DEFAULT_STAGE_LABELS.get(key):
            stages[key] = text
    terms = {}
    for key, value in (term_changes or {}).items():
        if key not in DEFAULT_LEAD_TERMS:
            raise ValueError(f"Unknown term '{key}'.")
        text = _clean(value)
        if len(text) > 40 or "<" in text:
            raise ValueError("Names must be at most 40 characters, without < characters.")
        if text:
            terms[key] = text
    return stages, terms


# ---- logo ----
_SVG_BAD_TAGS = {"script", "foreignobject", "iframe", "embed", "object", "audio", "video", "handler", "listener"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def check_svg(raw: bytes) -> None:
    """Raise ValueError unless the SVG is a plain drawing: no scripts, event handlers, external or javascript links,
    embedded HTML or entity declarations. Unsafe SVGs are refused, never served."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("The SVG must be UTF-8 text.")
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.I):
        raise ValueError("SVG logos with DOCTYPE or ENTITY declarations are not allowed.")
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        raise ValueError("The SVG file could not be read.")
    if _local(root.tag) != "svg":
        raise ValueError("The file is not an SVG image.")
    for el in root.iter():
        if _local(el.tag) in _SVG_BAD_TAGS:
            raise ValueError("SVG logos with scripts or embedded content are not allowed. Export a plain SVG or a PNG.")
        for name, value in el.attrib.items():
            attr, val = _local(name), re.sub(r"\s+", "", str(value)).lower()
            if attr.startswith("on"):
                raise ValueError("SVG logos with event handlers (onload, onclick…) are not allowed.")
            if attr == "href" and not val.startswith(("#", "data:image/png", "data:image/jpeg")):
                raise ValueError("SVG logos may not link to other files or addresses.")
            if "javascript:" in val or "url(http" in val or "@import" in val or "expression(" in val:
                raise ValueError("SVG logos may not load scripts or outside files.")
        if _local(el.tag) == "style" and re.search(r"@import|url\(\s*['\"]?(https?:|//|javascript:)", el.text or "", re.I):
            raise ValueError("SVG logos may not load outside files from their styles.")


def check_logo(content_type: str, data_b64: str) -> tuple:
    """(content_type, raw bytes) after checking type, size and the file's real format."""
    content_type = (content_type or "").split(";")[0].strip().lower()
    if content_type == "image/jpg":
        content_type = "image/jpeg"
    if content_type not in LOGO_TYPES:
        raise ValueError("The logo must be a PNG, JPG or SVG image.")
    try:
        raw = base64.b64decode(str(data_b64 or "").split(",")[-1], validate=True)
    except (ValueError, TypeError):
        raise ValueError("The logo file could not be read.")
    if not raw:
        raise ValueError("The logo file is empty.")
    if len(raw) > LOGO_MAX_BYTES:
        raise ValueError(f"The logo is {len(raw) // 1024} KB; the limit is 1 MB.")
    if content_type == "image/png" and not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("The file is not a real PNG image.")
    if content_type == "image/jpeg" and not raw.startswith(b"\xff\xd8\xff"):
        raise ValueError("The file is not a real JPG image.")
    if content_type == "image/svg+xml":
        check_svg(raw)
    return content_type, raw


def logo_record(content_type: str, raw: bytes, now_iso: str) -> dict:
    return {"content_type": content_type, "data": base64.b64encode(raw).decode("ascii"),
            "sha": hashlib.sha256(raw).hexdigest()[:12], "size": len(raw), "updated_at": now_iso}


def public_brand(source, logo: Optional[dict] = None) -> dict:
    """What any page (even the sign-in page) may show: names, tagline, colours and logo addresses."""
    brand = get_brand(source)
    uploaded = brand["logo_url"] or ""
    default = brand_is_default(source)
    email_logo = (uploaded if (logo or {}).get("content_type") != "image/svg+xml" else "") if uploaded else (DEFAULT_EMAIL_LOGO if default else "")
    return {k: brand[k] for k in ("app_name", "company_name", "product_name", "tagline", "primary_color", "accent_color",
                                  "website", "industry")} | {
        "logo_url": uploaded, "header_logo": uploaded or DEFAULT_HEADER_LOGO, "email_logo": email_logo,
        "custom_logo": bool(uploaded), "is_default": default}


def brand_is_default(source) -> bool:
    """True while the owner has saved no brand names and no offer (the original business's wording applies)."""
    settings = _settings(source)
    named = any(str(v or "").strip() and str(v).strip() != str(DEFAULT_BRAND.get(k, "")).strip()
                for k, v in (settings.get("brand") or {}).items() if k != "logo_url")
    offered = any(v is not None and str(v).strip() != str(DEFAULT_OFFER_PROFILE.get(k, "")).strip()
                  for k, v in (settings.get("offer") or {}).items())
    return not named and not offered


# The few brand facts needed on hot paths that have no store at hand (the per-lead suggestion in main._present).
# Refreshed when the app starts and whenever the brand or offer is saved.
CURRENT = {"generic": False, "product": "BookMyInterview", "claim_url": ""}


def remember(store) -> dict:
    settings = store.read("settings")
    CURRENT.update({"generic": not brand_is_default(settings), "product": prompt_profile(settings)["product"],
                    "claim_url": get_offer_profile(settings)["booking_link"]})
    return CURRENT
