"""Company names and places for matching a lead to the Indian company register (app/company_register.py).

normalise("M/s. Acme Technologies (OPC) Pvt. Ltd.") -> "acme technologies": lowercase, "&" -> "and", punctuation
dropped, legal suffixes (private limited, pvt ltd, limited, ltd, llp, opc, india, inc, llc, corporation, corp, co, ...)
stripped from the end, spaces collapsed. `score` compares a lead with one register row: name similarity (token overlap
and character similarity of the cleaned names), the lead's state / city against the registered state, RoC and address,
and the company status (Active preferred). `rank` turns the scored candidates into best + alternatives + confidence.

Confidence (the thresholds every caller uses):
  high    the cleaned names are identical (or >= 0.95 similar with the state agreeing), the state does not disagree, and
          no other candidate scores within 0.05 of it: attached to the lead automatically
  medium  score >= 0.75: shown as "Is this the company?" for one-click confirm / reject, never attached by itself
  low     below that: never attached; shown only as possible matches
"""
import re
from difflib import SequenceMatcher

HIGH_MARGIN = 0.05
MEDIUM_SCORE = 0.75
NEAR_EXACT = 0.95

_SUFFIXES = [s.split() for s in (
    "private limited", "pvt limited", "pvt ltd", "private ltd", "pvt", "private", "limited", "ltd", "llp", "opc",
    "india", "inc", "incorporated", "llc", "corporation", "corp", "co", "company", "plc", "and")]
_LEADING = ("m s", "ms", "the")
GENERIC = frozenset(
    "and of the services service solutions solution technologies technology tech infotech enterprises enterprise "
    "industries industry international global trading traders consultants consultancy consulting systems system "
    "ventures holdings group software products marketing management associates infra projects exports imports "
    "agro foods pharma healthcare logistics engineering engineers builders developers housing finance financial "
    "capital investments retail creations network networks digital media new".split())

STATES = {
    "AN": "andaman and nicobar islands", "AP": "andhra pradesh", "AR": "arunachal pradesh", "AS": "assam", "BR": "bihar",
    "CH": "chandigarh", "CT": "chhattisgarh", "CG": "chhattisgarh", "DN": "dadra and nagar haveli", "DD": "daman and diu",
    "DL": "delhi", "GA": "goa", "GJ": "gujarat", "HR": "haryana", "HP": "himachal pradesh", "JK": "jammu and kashmir",
    "JH": "jharkhand", "KA": "karnataka", "KL": "kerala", "LD": "lakshadweep", "LA": "ladakh", "MP": "madhya pradesh",
    "MH": "maharashtra", "MN": "manipur", "ML": "meghalaya", "MZ": "mizoram", "NL": "nagaland", "OR": "odisha",
    "OD": "odisha", "PY": "puducherry", "PB": "punjab", "RJ": "rajasthan", "SK": "sikkim", "TN": "tamil nadu",
    "TG": "telangana", "TS": "telangana", "TR": "tripura", "UP": "uttar pradesh", "UR": "uttarakhand", "UK": "uttarakhand",
    "UT": "uttarakhand", "WB": "west bengal"}
_STATE_ALIASES = {"orissa": "odisha", "pondicherry": "puducherry", "nct of delhi": "delhi", "new delhi": "delhi",
                  "uttaranchal": "uttarakhand", "delhi ncr": "delhi", "jammu kashmir": "jammu and kashmir",
                  "dadra and nagar haveli and daman and diu": "dadra and nagar haveli"}
_CITY_ALIASES = {"bengaluru": "bangalore", "bombay": "mumbai", "madras": "chennai", "calcutta": "kolkata",
                 "gurugram": "gurgaon", "poona": "pune", "trivandrum": "thiruvananthapuram", "baroda": "vadodara",
                 "mysuru": "mysore", "cochin": "kochi", "new delhi": "delhi"}
INACTIVE = ("strike", "struck", "dissolv", "liquidat", "amalgamat", "dormant", "converted", "not available for efiling",
            "under process of striking")
CIN_STATE = re.compile(r"^[LU]\d{5}([A-Z]{2})\d{4}[A-Z]{3}\d{6}$")


def _clean(text) -> str:
    text = str(text or "").lower().replace("&", " and ")
    text = re.sub(r"[^0-9a-zऀ-ॿ]+", " ", text.replace(".", " "))
    return re.sub(r"\s+", " ", text).strip()


def normalise(name) -> str:
    """The comparable form of a company name ('' when nothing usable is left)."""
    words = _clean(name).split()
    while len(words) > 1 and " ".join(words[:2]) in _LEADING:
        words = words[2:]
    while len(words) > 1 and words[0] in _LEADING:
        words = words[1:]
    changed = True
    while changed and len(words) > 1:
        changed = False
        for suffix in _SUFFIXES:
            n = len(suffix)
            if len(words) > n and words[-n:] == suffix:
                words, changed = words[:-n], True
                break
    return " ".join(words)[:190]


def tokens(key: str) -> list:
    return [t for t in key.split() if t]


def distinctive(key: str, n: int = 3) -> list:
    """The `n` most telling words of a cleaned name: non-generic first, longest first (used for the tok_key index)."""
    words = sorted(set(tokens(key)), key=lambda w: (w in GENERIC, -len(w), w))
    return words[:n]


def tok_key(key: str) -> str:
    best = distinctive(key, 1)
    return best[0][:60] if best else ""


def state_key(text) -> str:
    """A state name or two-letter code -> the lowercase state name ('' when unknown)."""
    raw = str(text or "").strip()
    if len(raw) == 2 and raw.upper() in STATES:
        return STATES[raw.upper()]
    key = _clean(raw)
    key = _STATE_ALIASES.get(key, key)
    return key if key in STATES.values() else ""


def state_from_cin(cin) -> str:
    m = CIN_STATE.match(str(cin or "").upper())
    return STATES.get(m.group(1), "") if m else ""


def city_key(text) -> str:
    key = _clean(text)
    return _CITY_ALIASES.get(key, key)


def _place_text(row: dict) -> str:
    text = f" {_clean(row.get('address'))} {_clean(row.get('roc'))} "
    for alias, canon in _CITY_ALIASES.items():
        text = text.replace(f" {alias} ", f" {canon} ")
    return text


def name_similarity(a: str, b: str) -> float:
    """1.0 only for identical cleaned names; otherwise the better of token overlap and character similarity, scaled 0.97."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a.replace(" ", "") == b.replace(" ", ""):
        return 0.98
    ta, tb = set(tokens(a)), set(tokens(b))
    dice = 2 * len(ta & tb) / (len(ta) + len(tb))
    ratio = SequenceMatcher(None, a, b).ratio()
    return round(max(dice, ratio) * 0.97, 4)


def is_inactive(status) -> bool:
    text = str(status or "").lower()
    return any(word in text for word in INACTIVE)


def score(lead: dict, row: dict, lead_key: str = "") -> dict:
    """{score, name, state ('agree'|'conflict'|''), city (bool), active (bool|None)} for one register row."""
    lead_key = lead_key or normalise(lead.get("company_legal") or lead.get("company"))
    sim = name_similarity(lead_key, row.get("name_key") or normalise(row.get("name")))
    total, state, city = sim, "", False
    want_state = state_key(lead.get("state"))
    have_state = state_key(row.get("state")) or state_from_cin(row.get("cin"))
    if want_state and have_state:
        state = "agree" if want_state == have_state else "conflict"
        total += 0.06 if state == "agree" else -0.12
    want_city = city_key(lead.get("city"))
    if want_city and len(want_city) >= 3 and f" {want_city} " in _place_text(row):
        city = True
        total += 0.02 if state == "agree" else 0.04
    status = row.get("status")
    active = None if not status else (str(status).strip().lower().startswith("active") and not is_inactive(status))
    if active:
        total += 0.02
    elif status and is_inactive(status):
        total -= 0.06
    return {"score": round(max(0.0, min(total, 1.0)), 4), "raw": total, "name": sim, "state": state, "city": city, "active": active}


def rank(lead: dict, rows: list, rejected=()) -> dict:
    """{confidence, best, alternatives (<= 3)} over the candidate rows; best is None when there is no candidate."""
    lead_key = normalise(lead.get("company_legal") or lead.get("company"))
    scored = []
    for row in rows:
        if row.get("cin") in rejected:
            continue
        s = score(lead, row, lead_key)
        scored.append({**row, "match": s})
    scored.sort(key=lambda r: (-r["match"]["raw"], -r["match"]["name"], r.get("cin") or ""))
    if not scored:
        return {"confidence": "none", "best": None, "alternatives": []}
    best, rest = scored[0], scored[1:]
    m = best["match"]
    rivals = [r for r in rest if r["match"]["raw"] >= m["raw"] - HIGH_MARGIN]
    short = len(lead_key.replace(" ", "")) <= 3
    if (m["name"] == 1.0 or (m["name"] >= NEAR_EXACT and m["state"] == "agree")) and m["state"] != "conflict" and not rivals and not short:
        confidence = "high"
    elif m["score"] >= MEDIUM_SCORE:
        confidence = "medium"
    else:
        confidence = "low"
    return {"confidence": confidence, "best": best, "alternatives": rest[:3]}
