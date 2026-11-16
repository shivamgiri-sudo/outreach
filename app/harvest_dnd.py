"""Do-not-call numbers for the lead harvest, kept in the EXISTING do-not-contact list (`suppressed`).

`suppressed` has always held unsubscribed / bounced email addresses; DND phone numbers are added to it in E.164 form
("+919810012345"), so one list holds every "never contact" value, backups and the Suppressed export carry them, and an
address can never be mistaken for a number. The harvest flags matching leads (`dnc`) and counts them; nothing is dropped.
"""
import re

from . import phones

MAX_CHARS = 3_000_000
MAX_NUMBERS = 100_000
_CELLS = re.compile(r"[\n\r,;\t|]+")


def numbers_in(text: str, country: str = "IN") -> dict:
    """{numbers, invalid}: every valid number in a pasted list or CSV (any column; header words are ignored)."""
    if len(text or "") > MAX_CHARS:
        raise ValueError(f"The file is too big; keep it under {MAX_CHARS // 1_000_000} MB.")
    found, invalid, seen = [], 0, set()
    for cell in _CELLS.split((text or "").lstrip("﻿")):
        cell = cell.strip().strip('"').strip()
        if not cell or not re.search(r"\d{5,}", re.sub(r"[\s().-]", "", cell)):
            continue                                       # a header or a name, not a number
        hit = phones.parse(cell, country)
        if not hit:
            invalid += 1
        elif hit["e164"] not in seen:
            seen.add(hit["e164"])
            found.append(hit["e164"])
        if len(found) > MAX_NUMBERS:
            raise ValueError(f"More than {MAX_NUMBERS} numbers; upload them in smaller files.")
    return {"numbers": found, "invalid": invalid}


def add(store, numbers: list) -> dict:
    """Put numbers on the do-not-contact list. Returns {added, already}."""
    out = {}

    def apply(rows: list) -> list:
        have = set(rows)
        new = [n for n in numbers if n not in have]
        out.update(added=len(new), already=len(numbers) - len(new))
        return [*rows, *new]
    store.update("suppressed", apply)
    return out


def count(store) -> int:
    return sum(1 for v in store.read("suppressed") if str(v).startswith("+"))
