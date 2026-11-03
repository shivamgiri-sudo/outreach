"""Phone number helpers shared by WhatsApp, SMS and calling adapters."""
import base64
import re
from typing import Optional

from ..hygiene import normalise_phone

SAFE_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,80}$")


def e164(raw) -> Optional[str]:
    """+91XXXXXXXXXX for Indian mobiles; any other '+<8-15 digits>' number as typed; else None."""
    indian = normalise_phone(raw)
    if indian:
        return indian
    text = str(raw or "").strip()
    digits = re.sub(r"\D", "", text)
    return f"+{digits}" if text.startswith("+") and 8 <= len(digits) <= 15 else None


def digits(raw) -> str:
    return re.sub(r"\D", "", str(raw or ""))


def national(number: str, country_code: str = "+91") -> str:
    """The number without its country code (for APIs that take them apart)."""
    d, cc = digits(number), digits(country_code)
    return d[len(cc):] if cc and d.startswith(cc) and len(d) > len(cc) + 6 else d


def basic_auth(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def safe_id(value: str, label: str) -> str:
    """IDs that go into an API path must be plain (letters, digits, - _ .), so they cannot change the address."""
    text = str(value or "").strip()
    if not SAFE_ID.match(text):
        raise ValueError(f"{label} looks wrong (only letters, numbers, - _ . allowed).")
    return text


def values_list(variables) -> list:
    if isinstance(variables, dict):
        return [str(v) for v in variables.values()]
    return [str(v) for v in (variables or [])]
