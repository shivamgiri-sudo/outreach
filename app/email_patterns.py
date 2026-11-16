"""Company email formats, shared by the hiring engine and Find & enrich.

  * clean / parse_name   turn a person's name into the plain letters an address is made of: accents are transliterated
                         (José -> jose, Müller -> muller), titles (Mr, Dr, Shri) dropped, "Last, First" read the right way
                         round, compound surnames kept whole and split (de la Cruz -> delacruz or cruz; García-López ->
                         garcialopez, garcia or lopez), initials kept as initials (A. K. Sharma, Rahul K.), single names allowed.
  * which_pattern        which format a known (name, address) pair follows (first.last, flast, first ...).
  * learn                a company's formats with counts and a confidence, from (name, address) pairs at its domain.
  * candidates           ranked addresses for a name: the learned formats first, then common defaults.

Nothing here does any network call or decides whether a mailbox exists.
"""
import re
import unicodedata
from collections import Counter
from typing import Optional

# Order matters: when two formats give the same address for a name, the first one wins.
PATTERNS = {
    "first.last": lambda f, l: f"{f}.{l}" if l else "",
    "first": lambda f, l: f,
    "flast": lambda f, l: f"{f[0]}{l}" if l else "",
    "firstl": lambda f, l: f"{f}{l[0]}" if l else "",
    "first_last": lambda f, l: f"{f}_{l}" if l else "",
    "firstlast": lambda f, l: f"{f}{l}" if l else "",
    "last.first": lambda f, l: f"{l}.{f}" if l else "",
    "last": lambda f, l: l,
    "f.last": lambda f, l: f"{f[0]}.{l}" if l else "",
    "first.l": lambda f, l: f"{f}.{l[0]}" if l else "",
    "first-last": lambda f, l: f"{f}-{l}" if l else "",
    "lastf": lambda f, l: f"{l}{f[0]}" if l else "",
    "lastfirst": lambda f, l: f"{l}{f}" if l else "",
    "last_first": lambda f, l: f"{l}_{f}" if l else "",
}
HIRING_PATTERNS = ("first.last", "first", "flast", "firstl", "first_last", "firstlast", "last.first", "last")
DEFAULT_ORDER = ("first.last", "first", "flast", "firstlast", "f.last", "first_last", "firstl", "last.first")
MAX_CANDIDATES = 5

HONORIFICS = frozenset("mr mrs ms miss mx dr prof shri sri smt kumari km er ca cs adv capt col sir madam".split())
SUFFIXES = frozenset("jr sr ii iii iv phd md mba".split())
PARTICLES = frozenset("de da del della der den van von la le di du dos das do bin binti al el ter ten st".split())
SPECIAL = str.maketrans({"ß": "ss", "æ": "ae", "Æ": "ae", "ø": "o", "Ø": "o", "ł": "l", "Ł": "l", "đ": "d", "Đ": "d",
                         "œ": "oe", "Œ": "oe", "þ": "th", "ı": "i"})


def ascii_fold(text: str) -> str:
    """Accents removed and special letters spelled out: "Zoë Łukasz Strauß" -> "Zoe Lukasz Strauss"."""
    text = str(text or "").translate(SPECIAL)
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def clean(text: str) -> str:
    """Only the letters a-z, lower case, accents transliterated (the hiring engine's cleaner, made accent-safe)."""
    return re.sub(r"[^a-z]", "", ascii_fold(text).lower())


def parse_name(full: str) -> dict:
    """{"first", "last", "last_alts"} as plain letters. last is "" for a single name; first may be one letter (an initial)."""
    text = re.sub(r"\(.*?\)", " ", ascii_fold(full or ""))
    if text.count(",") == 1:
        last_part, first_part = text.split(",")
        given = [clean(w) for w in first_part.split() if clean(w)]
        if given and not all(w in SUFFIXES for w in given):          # "Sharma, Rahul" (but not "Rahul Sharma, PhD")
            text = f"{first_part} {last_part}"
    tokens = [t for t in re.split(r"[\s.]+", text.replace(",", " ")) if t]
    words = []
    for t in tokens:
        low = clean(t)
        if not low or (not words and low in HONORIFICS) or low in SUFFIXES:
            continue
        words.append(t)
    if not words:
        return {"first": "", "last": "", "last_alts": []}
    first = clean(words[0])
    if len(words) == 1:
        return {"first": first, "last": "", "last_alts": []}
    tail = [words[-1]]
    i = len(words) - 2
    while i >= 1 and clean(words[i]) in PARTICLES:          # "Maria de la Cruz": the surname is "de la Cruz"
        tail.insert(0, words[i])
        i -= 1
    last = clean("".join(tail))
    alts = []
    if len(tail) > 1:
        alts.append(clean(tail[-1]))
    for part in tail[-1].split("-") if "-" in tail[-1] else []:
        alts.append(clean(part))
    alts = [a for a in dict.fromkeys(alts) if a and a != last]
    return {"first": first, "last": last, "last_alts": alts}


def build(pattern: str, first: str, last: str) -> str:
    """The local part for one format ("" when the format needs a surname the name does not have)."""
    if not first or pattern not in PATTERNS:
        return ""
    if not last and pattern != "first":
        return ""
    return PATTERNS[pattern](first, last)


def which_pattern(first: str, last: str, local: str, patterns=None) -> Optional[str]:
    """The format `local` follows for this first and last name, else None. Names are cleaned here."""
    f, l = clean(first), clean(last)
    local = (local or "").lower()
    if not f or not local:
        return None
    for pid in patterns or PATTERNS:
        if (l or pid == "first") and build(pid, f, l) == local:
            return pid
    return None


def pattern_of(full_name: str, local: str) -> Optional[str]:
    """which_pattern for a full name, trying the surname variants too (de la Cruz, García-López)."""
    parts = parse_name(full_name)
    if not parts["first"] or not parts["last"]:
        return None                                  # a single name says nothing about the company's format
    for last in [parts["last"], *parts["last_alts"]]:
        pid = which_pattern(parts["first"], last, local)
        if pid:
            return pid
    return None


def learn(pairs) -> list:
    """[(full name, local part)] at one domain -> [{"pattern", "count", "share", "confidence"}], most used first.

    confidence (0-1) grows with both the share of addresses that follow the format and how many there are (3 or more
    is full weight), so one example gives 0.33 at most."""
    seen, total = Counter(), 0
    for name, local in pairs:
        pid = pattern_of(name, local)
        if pid:
            seen[pid] += 1
            total += 1
    out = []
    for pid, count in seen.most_common():
        share = count / total
        out.append({"pattern": pid, "count": count, "share": round(share, 2), "confidence": round(share * min(1.0, count / 3), 2)})
    return out


def confidence_label(value: float) -> str:
    return "high" if value >= 0.6 else "medium" if value >= 0.3 else "low"


def candidates(full_name: str, domain: str, learned=(), limit: int = MAX_CANDIDATES) -> list:
    """Ranked guesses [{"email", "pattern", "basis", "confidence"}] for a name at `domain`: the company's learned formats
    first (most used first), then the common defaults. A single name only gets first@."""
    parts = parse_name(full_name)
    if not parts["first"] or not domain:
        return []
    known = {row["pattern"]: row for row in learned or []}
    order = [*known, *(p for p in DEFAULT_ORDER if p not in known)]
    out, seen = [], set()
    for pid in order:
        lasts = [parts["last"], *(parts["last_alts"] if pid in known else [])]
        for last in lasts:
            local = build(pid, parts["first"], last)
            if not local or local in seen:
                continue
            seen.add(local)
            row = known.get(pid)
            out.append({"email": f"{local}@{domain}", "pattern": pid,
                        "basis": f"the company's own format ({row['count']} known address{'es' if row['count'] != 1 else ''})" if row else "a common format",
                        "confidence": row["confidence"] if row else 0.0})
    return out[:limit]
