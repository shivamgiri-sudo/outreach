"""Junk check for captured leads: a phone that cannot be a real number, or a name that is obviously not a name.

A flagged lead is still captured (never dropped): it gets the `check-junk` tag, a note saying why, and a low-priority
call task, so a person looks before spending a call on it. Ad forms collect many of these ("+917627912779339---",
"+91821873,4*,0981", "asdfgh", "Test 123").
"""
import re

from . import phones

TAG = "check-junk"
_ALLOWED_PHONE = re.compile(r"^[\d+\-() .]+$")
_SEQUENCES = ("0123456789", "1234567890", "9876543210", "0987654321")
_VOWELS = set("aeiouy")


def _phone_text(raw) -> str:
    text = str(raw or "").strip()
    return text[2:].strip() if text.lower().startswith("p:") else text      # Meta's sheet export writes "p:+91..."


def phone_problem(raw) -> str:
    """Why this typed phone is junk, or "" when it looks like a real number (or nothing was typed)."""
    text = _phone_text(raw)
    if not text:
        return ""
    if not _ALLOWED_PHONE.match(text) or re.search(r"--|\.\.|\+.*\+", text):
        return "the phone number has stray characters"
    found = phones.parse(text)
    if not found:
        return "the phone number is not a valid number"
    national = re.sub(r"\D", "", found["e164"])[-10:]
    if len(set(national)) <= 2 or national in _SEQUENCES:
        return "the phone number looks made up"
    return ""


def name_problem(raw) -> str:
    """Why this typed name is junk, or "" when it could be a real name (or nothing was typed)."""
    text = " ".join(str(raw or "").split())
    if not text:
        return ""
    letters = [ch for ch in text if ch.isalpha()]
    if re.search(r"\d", text):
        return "the name has digits in it"
    if len(letters) < 2:
        return "the name has no letters"
    if re.search(r"(.)\1{3,}", text.lower()):
        return "the name repeats one character"
    if text.isascii():
        for word in re.findall(r"[A-Za-z]+", text):
            if len(word) >= 6 and not (set(word.lower()) & _VOWELS):
                return "the name looks like random typing"
    if sum(1 for ch in text if not (ch.isalpha() or ch in " .'-")) > max(2, len(text) // 4):
        return "the name is mostly symbols"
    return ""


def reasons(fields: dict) -> list:
    """Every junk reason for a lead's raw typed fields ({"phone", "name", ...})."""
    return [r for r in (phone_problem((fields or {}).get("phone")), name_problem((fields or {}).get("name"))) if r]
