"""Data hygiene: normalise and dedupe lead contact fields."""
import re
from typing import Optional
from urllib.parse import urlparse

EMAIL_RE = re.compile(r"^[a-z0-9._%+\-]+@[a-z0-9\-]+(\.[a-z0-9\-]+)+$")
ROLE_LOCAL_PARTS = frozenset(
    {"info", "contact", "hello", "admin", "support", "sales", "hr", "careers", "jobs", "office", "mail", "enquiry"}
)
INDIA_MOBILE_DIGITS = 10


def normalise_email(raw: Optional[str]) -> Optional[str]:
    email = (raw or "").strip().lower()
    return email if EMAIL_RE.match(email) else None


def normalise_phone(raw: Optional[str]) -> Optional[str]:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == INDIA_MOBILE_DIGITS + 2 and digits.startswith("91"):
        digits = digits[2:]
    digits = digits.lstrip("0") if len(digits) == INDIA_MOBILE_DIGITS + 1 else digits
    if len(digits) != INDIA_MOBILE_DIGITS or digits[0] not in "6789":
        return None
    return f"+91{digits}"


def company_domain(value: Optional[str]) -> Optional[str]:
    text = (value or "").strip().lower()
    if not text:
        return None
    try:
        host = text.split("@", 1)[1] if "@" in text else urlparse(text if "//" in text else f"//{text}").netloc
    except ValueError:                       # e.g. a full-width ＠ or ／ that NFKC-normalises into the host part
        return None
    host = host.split("/")[0].split(":")[0]
    host = host[4:] if host.startswith("www.") else host
    return host or None


def is_role_address(email: str) -> bool:
    return email.split("@", 1)[0] in ROLE_LOCAL_PARTS


def dedupe_key(lead: dict) -> Optional[str]:
    email = normalise_email(lead.get("email"))
    if email:
        return f"email:{email}"
    phone = normalise_phone(lead.get("phone"))
    if phone:
        return f"phone:{phone}"
    domain = company_domain(lead.get("website"))
    return f"domain:{domain}" if domain else None


FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "yahoo.com", "yahoo.co.in", "outlook.com", "hotmail.com", "rediffmail.com", "live.com",
    "icloud.com", "proton.me", "protonmail.com", "aol.com", "ymail.com",
})


def business_domain(value: Optional[str]) -> Optional[str]:
    """Company website domain; free-mail addresses do not identify a company."""
    domain = company_domain(value)
    return None if domain in FREE_MAIL_DOMAINS else domain


def safe_http_url(raw) -> str:
    """The URL if it is a plain http(s) link, else "". Blocks javascript:, data: and similar links from imports."""
    url = str(raw or "").strip()
    return url if re.match(r"^https?://[^\s<>\"']+$", url, re.IGNORECASE) else ""


def csv_safe(value) -> str:
    """A spreadsheet cell that can't run as a formula when the CSV is opened in Excel or Sheets."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text
