"""Phone numbers read with Google's libphonenumber (the `phonenumbers` package): E.164 plus the line type.

    parse("098100 12345")          -> {"e164": "+919810012345", "type": "mobile", "region": "IN"}
    parse("080-4000-1500")         -> {"e164": "+918040001500", "type": "fixed_line", ...}
    parse("1800 11 0011")          -> {"e164": "+911800110011", "type": "toll_free", ...}
    parse("12345")                 -> None   (invalid or incomplete numbers are dropped)

Line types: mobile, fixed_line, toll_free, voip, mobile_or_fixed (libphonenumber cannot tell), unknown.
`compat_type` maps that onto the older lead vocabulary ("mobile" / "landline" / "intl") that the sending rules read
(app/contact_rules.py: a "landline" never gets WhatsApp or SMS), so anything that is not surely a mobile stays off
WhatsApp/SMS. New code may adopt `parse` / `parse_many`; app/calltracker.py norm_phone and app/standardise.py are untouched.
"""
import re
from typing import Optional

import phonenumbers
from phonenumbers import PhoneNumberType as T

DEFAULT_REGION = "IN"
MAX_CHARS = 40
_SPLIT = re.compile(r"[,;/|\n]+|\s+or\s+", re.IGNORECASE)
_TYPES = {T.MOBILE: "mobile", T.FIXED_LINE: "fixed_line", T.TOLL_FREE: "toll_free", T.VOIP: "voip",
          T.FIXED_LINE_OR_MOBILE: "mobile_or_fixed"}
LINE_TYPES = ("mobile", "fixed_line", "toll_free", "voip", "mobile_or_fixed", "unknown")


def _region(country: str) -> str:
    code = str(country or "").strip().upper()
    return code if re.fullmatch(r"[A-Z]{2}", code) else DEFAULT_REGION


def parse(raw, country: str = DEFAULT_REGION) -> Optional[dict]:
    """{e164, type, region} for one number, or None when it is empty, invalid or incomplete. `country` is the default
    region for numbers written without +<country code> (ISO 3166 alpha-2, default IN)."""
    text = str(raw or "").strip()[:MAX_CHARS]
    if not text or not re.search(r"\d", text):
        return None
    if text.startswith("00"):
        text = "+" + text[2:]
    try:
        number = phonenumbers.parse(text, _region(country))
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(number):
        return None
    kind = _TYPES.get(phonenumbers.number_type(number), "unknown")
    return {"e164": phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164), "type": kind,
            "region": phonenumbers.region_code_for_number(number) or ""}


def parse_many(raw, country: str = DEFAULT_REGION) -> list:
    """Every valid number in one cell or list ('98100 12345 / 011-2345 6789'), de-duplicated, in the order given."""
    parts = raw if isinstance(raw, (list, tuple)) else _SPLIT.split(str(raw or ""))
    out, seen = [], set()
    for part in parts:
        found = parse(part, country)
        if found and found["e164"] not in seen:
            seen.add(found["e164"])
            out.append(found)
    return out


def best(numbers: list) -> Optional[dict]:
    """The number to call first: a mobile, then mobile_or_fixed, fixed line, VoIP, toll-free, unknown."""
    order = {"mobile": 0, "mobile_or_fixed": 1, "fixed_line": 2, "voip": 3, "toll_free": 4, "unknown": 5}
    return min(numbers, key=lambda n: order.get(n["type"], 9)) if numbers else None


def compat_type(found: Optional[dict]) -> str:
    """The older lead phone_type for a parsed number: "mobile" (Indian mobile), "intl" (a mobile abroad), else "landline"."""
    if not found:
        return ""
    if found["type"] == "mobile":
        return "mobile" if found["e164"].startswith("+91") else "intl"
    return "landline"


def e164(raw, country: str = DEFAULT_REGION) -> str:
    """Just the E.164 text, or ''."""
    found = parse(raw, country)
    return found["e164"] if found else ""
