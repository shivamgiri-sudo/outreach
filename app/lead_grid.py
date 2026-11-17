"""Leads grid filters shared by the Leads page list and the lead downloads (CSV / Excel), so "Download these N" agrees
with what the grid shows. The same rules live in web/src/pages/leadFilters.js (gridMatches / hasDetail).

  has=phone,mobile,email,linkedin,hiring   every ticked detail must be on the lead
      phone   any phone number
      mobile  a mobile: phone_line_type mobile or mobile_or_fixed, or (older leads without a line type) phone_type mobile
      email   a real address in `email` (a pattern guess lives in `email_guess` and never counts)
  min_rating=4      Google / directory rating at least this (a lead without a rating is left out)
  no_dnd=1          leave out leads marked DND (`dnc`) or whose number is on the do-not-contact list (`suppressed`)
  harvest=any|<job> only leads found by the lead harvest (any job, or one job id)
"""
import re

RATINGS = ("3", "3.5", "4", "4.5")
MOBILE_LINES = ("mobile", "mobile_or_fixed")
FILTERS = ("has", "min_rating", "no_dnd", "harvest")


def _text(params: dict, key: str) -> str:
    return str(params.get(key) or "").strip()


def is_mobile(lead: dict) -> bool:
    if not str(lead.get("phone") or "").strip():
        return False
    line = str(lead.get("phone_line_type") or "")
    return line in MOBILE_LINES if line else lead.get("phone_type") == "mobile"


def has_email(lead: dict) -> bool:
    return "@" in str(lead.get("email") or "")


def hiring_roles(lead: dict) -> int:
    try:
        return int(float((lead.get("hiring_signal") or {}).get("open_roles") or lead.get("open_roles") or 0))
    except (TypeError, ValueError):
        return 0


def has_detail(lead: dict, what: str) -> bool:
    if what == "phone":
        return bool(str(lead.get("phone") or "").strip())
    if what == "mobile":
        return is_mobile(lead)
    if what == "email":
        return has_email(lead)
    if what == "linkedin":
        return bool(re.search(r"linkedin\.com/(in|pub)/", str(lead.get("linkedin_url") or ""), re.I))
    if what == "hiring":
        return hiring_roles(lead) > 0
    return True


def rating_of(lead: dict):
    try:
        return float(lead.get("rating"))
    except (TypeError, ValueError):
        return None


def suppressed_phones(store) -> set:
    """Numbers on the do-not-contact list (E.164 values in `suppressed`; emails there are left out)."""
    return {v for v in store.read("suppressed") if str(v).startswith("+")}


def on_dnd(lead: dict, phones: set) -> bool:
    if lead.get("dnc"):
        return True
    return any(str(p) in phones for p in [lead.get("phone"), *(lead.get("phones") or [])] if p)


def from_harvest(lead: dict, job: str = "any") -> bool:
    jobs = lead.get("harvest_jobs") or []
    return bool(jobs) if job in ("", "any") else job in jobs


def parse(params: dict) -> dict:
    """The grid filters asked for in a request (unknown `has` words are ignored; a bad rating is an error)."""
    has = [w.strip().lower() for w in _text(params, "has").split(",") if w.strip()]
    rating = _text(params, "min_rating")
    if rating:
        try:
            rating = float(rating)
        except ValueError:
            raise ValueError("min_rating must be a number such as 4 or 4.5") from None
    return {"has": has, "min_rating": rating or None, "no_dnd": _text(params, "no_dnd").lower() in ("1", "true", "yes", "on"),
            "harvest": _text(params, "harvest")[:40]}


def active(grid: dict) -> bool:
    return bool(grid["has"] or grid["min_rating"] is not None or grid["no_dnd"] or grid["harvest"])


def matches(lead: dict, grid: dict, phones: set) -> bool:
    if not all(has_detail(lead, w) for w in grid["has"]):
        return False
    if grid["min_rating"] is not None and (rating_of(lead) is None or rating_of(lead) < grid["min_rating"]):
        return False
    if grid["no_dnd"] and on_dnd(lead, phones):
        return False
    return not grid["harvest"] or from_harvest(lead, grid["harvest"])


def mark_dnd(store, rows: list) -> list:
    """The Leads list gets `on_dnd` per lead, so the page can hide numbers on the uploaded DND list it cannot see."""
    phones = suppressed_phones(store)
    for r in rows:                                   # fresh dicts built for this response, so set in place
        r["on_dnd"] = on_dnd(r, phones)
    return rows
