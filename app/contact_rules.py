"""Owner rules on how a lead may be contacted, kept in one place.

1. Landlines are kept (office numbers with an STD code), and can be called, but never get WhatsApp or SMS.
2. Consent is per channel (email, call, whatsapp, sms): a yes on one channel never covers another.
   Older records without a channel (or "any") keep covering what the consent form offered then
   (email, call, WhatsApp) but never SMS, which was not on that form.
"""
from typing import Optional

CONSENT_CHANNELS = ("email", "call", "whatsapp", "sms")
LEGACY_ANY = ("email", "call", "whatsapp")
LANDLINE_REASON = "phone is a landline, no WhatsApp/SMS"


def phone_kind(phone) -> str:
    """'mobile', 'landline', 'intl' or '' for a stored number (e164 or as typed)."""
    from .standardise import parse_phone          # late import: standardise imports hygiene, which leads import
    if not phone:
        return ""
    found, _problem = parse_phone(str(phone))
    return found[1] if found else ""


def is_landline(phone) -> bool:
    return phone_kind(phone) == "landline"


def lead_is_landline(lead: dict) -> bool:
    """The lead's main phone is a landline (by its saved kind or by the number itself)."""
    phone = (lead or {}).get("phone")
    return bool(phone) and ((lead.get("phone_type") == "landline") or is_landline(phone))


BAD_PHONE_STATUSES = ("wrong_number", "inactive_number")
BAD_PHONE_REASON = "phone was marked wrong or inactive, so no calls, WhatsApp or SMS go to it"


def phone_is_invalid(lead: dict) -> bool:
    """The lead's main phone was marked wrong or inactive (by a caller or by a DialHalo outcome) and has not been changed since."""
    lead = lead or {}
    flag = lead.get("phone_status") or {}
    return bool(lead.get("phone")) and flag.get("status") in BAD_PHONE_STATUSES and flag.get("number") == lead.get("phone")


def messaging_block(phone) -> Optional[str]:
    """Why this number may not get WhatsApp/SMS, or None."""
    return LANDLINE_REASON if is_landline(phone) else None


def consent_channels(lead: dict) -> set:
    """The channels this lead agreed to be contacted on."""
    lead = lead or {}
    out = set()
    consent = lead.get("consent")
    if isinstance(consent, dict) and consent:
        listed = consent.get("channels")
        channel = str(consent.get("channel") or "").strip().lower()
        if isinstance(listed, list):
            out |= {str(c).lower() for c in listed if str(c).lower() in CONSENT_CHANNELS}
        elif channel in CONSENT_CHANNELS:
            out.add(channel)
        else:                                       # "any" or a record from before channels were saved
            out |= set(LEGACY_ANY)
    elif consent:                                   # a bare truthy value from very old data
        out |= set(LEGACY_ANY)
    for channel in (lead.get("inbound_consent") or {}):
        if channel in CONSENT_CHANNELS:
            out.add(channel)
    return out


def has_consent_for(lead: dict, channel: str) -> bool:
    return channel in consent_channels(lead)


def add_consent(lead: dict, record: dict) -> dict:
    """The consent record to save when a new yes is given: earlier channels stay, the new one is added."""
    channel = str(record.get("channel") or "").strip().lower()
    new = {channel} if channel in CONSENT_CHANNELS else set(LEGACY_ANY) if channel in ("", "any") else set()   # e.g. a LinkedIn yes
    keep = consent_channels({"consent": lead.get("consent")}) if lead.get("consent") else set()
    return {**record, "channels": [c for c in CONSENT_CHANNELS if c in keep | new]}


# A positive reply that names another channel ("yes, WhatsApp me on 98xxxxxxxx", "call me tomorrow")
# is written consent for that channel too. A plain "yes, interested" covers only the channel it came on.
import re as _re

_NAMED = {
    "whatsapp": _re.compile(r"\b(whats\s?app|wa\s+me|on\s+wa)\b", _re.I),
    "call": _re.compile(r"\b(call\s+me|give\s+(me\s+)?a\s+(call|ring)|ring\s+me|phone\s+me|you\s+can\s+call|happy\s+to\s+(take\s+a\s+)?call)\b", _re.I),
    "sms": _re.compile(r"\b(sms|text\s+me|send\s+(me\s+)?a\s+text)\b", _re.I),
}
_NEGATION = _re.compile(r"\b(no|not|don'?t|do\s+not|never|stop|without)\b[^.!?,;\n]{0,25}$", _re.I)


def channels_named_in_reply(text: str) -> set:
    """Channels a positive reply explicitly invites us to use, ignoring negated mentions ("no WhatsApp please")."""
    found = set()
    for channel, pattern in _NAMED.items():
        for hit in pattern.finditer(text or ""):
            before = (text or "")[max(0, hit.start() - 40):hit.start()]
            if not _NEGATION.search(before):
                found.add(channel)
                break
    return found
