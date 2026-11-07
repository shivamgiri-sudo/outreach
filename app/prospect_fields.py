"""Field cleaning, classification and the provenance / best-value rules of the prospect database.

Provenance: every scalar field keeps a list of entries {v, s, at, c, st?, vf?}:
  v value, s source id, at seen_at (ISO UTC), c confidence (0-100), st email status (email only), vf "verified" flag.
List fields (phones, emails, technologies, keywords, aliases) keep items {v, s: [sources], first, last}.

BEST VALUE RULE (documented contract):
  1. entries from sources the owner disabled are ignored;
  2. a manual edit (source "manual") always wins; the newest manual edit if there are several;
  3. otherwise the most *verified* entry wins (email: verified > valid > catch_all > unknown/risky > invalid;
     other fields: an entry the source marked verified beats one it did not);
  4. ties go to the most *recent* entry (by day seen), then to the *higher-confidence* source, then the newer timestamp.
"""
import re
from datetime import datetime, timezone

from .hygiene import business_domain, is_role_address, normalise_email, normalise_phone, safe_http_url
from .sheet_import import company_match_key

SENIORITIES = ("owner", "founder", "c_suite", "vp", "head", "director", "manager", "senior", "entry")
DEPARTMENTS = ("hr", "talent", "sales", "marketing", "engineering", "finance", "operations", "it", "leadership", "other")
EMAIL_STATUSES = ("verified", "valid", "catch_all", "risky", "invalid", "unknown")
SIGNAL_KINDS = ("hiring", "funding", "news", "tech_change", "expansion", "leadership_change")
STATUS_RANK = {"verified": 4, "valid": 3, "catch_all": 2, "unknown": 1, "risky": 1, None: 1, "": 1, "invalid": -1}
STATUS_CONFIDENCE = {"verified": 95, "valid": 85, "catch_all": 60, "risky": 40, "unknown": 50, "invalid": 0}
SENIORITY_POINTS = {"owner": 20, "founder": 20, "c_suite": 20, "vp": 16, "head": 14, "director": 12, "manager": 8, "senior": 4, "entry": 2}
STATUS_POINTS = {"verified": 40, "valid": 30, "catch_all": 15, "unknown": 10, "risky": 5, "invalid": 0}
SOURCE_CONFIDENCE = {"manual": 100, "crm": 70, "csv": 60, "apollo": 80, "hunter": 75, "peopledatalabs": 75, "lusha": 75,
                     "rocketreach": 75, "snov": 70, "prospeo": 70, "dropcontact": 75, "google_places": 70, "opencorporates": 85,
                     "data_gov_in": 85, "wikidata": 65, "website": 65, "site_discover": 65, "careers_page": 70, "osm_overpass": 55,
                     "osm_nominatim": 55, "bounce": 95, "verifier": 90}
MAX_ENTRIES = 12
MAX_LIST = 40
SOURCE_ALIASES = {"apollo_search": "apollo", "apollo_hiring": "apollo", "apollo_match": "apollo", "google_maps": "google_places",
                  "crawler": "website", "website_deep": "site_discover", "smart_import": "csv", "import": "csv"}

_SENIORITY_RULES = (
    ("founder", {"founder", "cofounder", "co-founder"}),
    ("owner", {"owner", "proprietor", "partner", "promoter"}),
    ("c_suite", {"ceo", "cto", "cfo", "coo", "cmo", "chro", "cio", "cpo", "chief", "md", "chairman", "president"}),
    ("vp", {"vp", "svp", "evp", "avp"}),
    ("head", {"head"}),
    ("director", {"director", "gm"}),
    ("manager", {"manager", "lead", "supervisor", "incharge"}),
    ("senior", {"senior", "sr", "principal", "specialist", "consultant"}),
    ("entry", {"executive", "associate", "assistant", "intern", "trainee", "junior", "jr", "coordinator", "analyst", "officer", "recruiter"}),
)
_DEPT_RULES = (
    ("talent", {"talent", "recruiter", "recruiting", "recruitment", "ta", "hiring", "staffing", "sourcing", "acquisition"}),
    ("hr", {"hr", "hrbp", "chro", "people", "personnel", "payroll", "culture", "human"}),
    ("leadership", {"founder", "cofounder", "owner", "ceo", "md", "chairman", "president", "partner", "proprietor", "director"}),
    ("sales", {"sales", "business", "bd", "account", "revenue", "commercial"}),
    ("marketing", {"marketing", "brand", "growth", "content", "seo", "cmo", "digital"}),
    ("engineering", {"engineering", "engineer", "developer", "software", "cto", "product", "technology", "tech"}),
    ("it", {"it", "infrastructure", "systems", "network", "cio", "security"}),
    ("finance", {"finance", "accounts", "accounting", "cfo", "treasury", "audit", "tax"}),
    ("operations", {"operations", "ops", "coo", "plant", "production", "admin", "logistics", "supply", "facility", "branch"}),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def at_or_now(value) -> str:
    text = str(value or "").strip()
    if not text:
        return now_iso()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return now_iso()


def source_key(raw) -> str:
    head = str(raw or "").strip().split(":", 1)[0].lower()
    head = re.sub(r"[^a-z0-9_]+", "_", head).strip("_")[:40] or "unknown"
    return SOURCE_ALIASES.get(head, head)


def _tokens(title: str) -> set:
    return set(re.findall(r"[a-z]+", str(title or "").lower().replace("co-founder", "cofounder")))


def seniority_of(title: str) -> str:
    t = _tokens(title)
    if not t:
        return ""
    if "vice" in t and "president" in t:
        return "vp"
    return next((name for name, words in _SENIORITY_RULES if t & words), "")


def department_of(title: str) -> str:
    t = _tokens(title)
    if not t:
        return ""
    return next((name for name, words in _DEPT_RULES if t & words), "other")


def text(value, limit: int = 300) -> str:
    return " ".join(str(value if value is not None else "").split())[:limit]


def low(value) -> str:
    return text(value, 120).lower()


def to_int(value):
    try:
        n = int(float(str(value).replace(",", "")))
        return n if n >= 0 else None
    except (TypeError, ValueError):
        return None


def clean_phone(value) -> str:
    """A mobile, an office landline with its STD code (kept for calls), or a +country number; else ""."""
    phone = normalise_phone(str(value or ""))
    if phone:
        return phone
    from .lead_model import source_phone
    landline, kind = source_phone(value)
    if kind == "landline":
        return landline
    digits = re.sub(r"[^\d+]", "", str(value or ""))
    return digits if re.fullmatch(r"\+\d{8,15}", digits) else ""


def clean_domain(value) -> str:
    from .sources.base import valid_host
    return valid_host(business_domain(str(value or "")) or "")


def linkedin(value, kind: str) -> str:
    from .lead_model import _linkedin
    return _linkedin(value, kind)


def name_key(value) -> str:
    words = re.findall(r"[a-z]+", str(value or "").lower())
    words = [w for w in words if w not in ("mr", "mrs", "ms", "dr", "shri", "smt", "km", "prof")]
    return " ".join(words)[:120]


def company_key(name) -> str:
    return company_match_key(str(name or ""))[:120]


def emp_bounds(employees, range_text) -> tuple:
    if employees:
        return employees, employees
    nums = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", str(range_text or ""))]
    if not nums:
        return None, None
    if len(nums) == 1:
        return nums[0], (10 ** 9 if "+" in str(range_text) else nums[0])
    return min(nums[:2]), max(nums[:2])


# ---------------------------------------------------------------- provenance
def add_entry(fields: dict, name: str, value, source: str, at: str, conf: int, status=None, verified=None) -> None:
    if value in (None, "", [], {}):
        return
    entries = fields.setdefault(name, [])
    hit = next((e for e in entries if e["s"] == source and e["v"] == value), None)
    if hit:
        if at >= hit["at"]:
            hit["at"] = at
            if status:
                hit["st"] = status
        hit["c"] = max(hit.get("c", 0), conf)
        if verified:
            hit["vf"] = True
    else:
        entry = {"v": value, "s": source, "at": at, "c": conf}
        if status:
            entry["st"] = status
        if verified:
            entry["vf"] = True
        entries.append(entry)
    if len(entries) > MAX_ENTRIES:
        keep = sorted(entries, key=lambda e: (e["s"] == "manual", e["at"]), reverse=True)[:MAX_ENTRIES]
        fields[name] = [e for e in entries if e in keep]


def add_items(lists: dict, name: str, values, source: str, at: str) -> None:
    items = lists.setdefault(name, [])
    for value in values or []:
        if value in (None, ""):
            continue
        hit = next((i for i in items if i["v"] == value), None)
        if hit:
            if source not in hit["s"]:
                hit["s"].append(source)
            hit["last"] = max(hit["last"], at)
        elif len(items) < MAX_LIST:
            items.append({"v": value, "s": [source], "first": at, "last": at})


def _rank(entry: dict) -> tuple:
    verified = STATUS_RANK.get(entry.get("st"), 1) if "st" in entry else (2 if entry.get("vf") else 1)
    return (verified, entry["at"][:10], entry.get("c", 0), entry["at"])


def best(entries: list, disabled=frozenset()):
    """The winning entry per the BEST VALUE RULE above, or None."""
    live = [e for e in entries or [] if e["s"] not in disabled]
    if not live:
        return None
    manual = [e for e in live if e["s"] == "manual"]
    if manual:
        return max(manual, key=lambda e: e["at"])
    return max(live, key=_rank)


def list_values(items: list, disabled=frozenset()) -> list:
    return [i["v"] for i in items or [] if any(s not in disabled for s in i["s"])]


def field_view(entries: list, disabled=frozenset()) -> dict:
    """What the 'where did this come from' panel shows for one field."""
    win = best(entries, disabled)
    verified_at = [e["at"] for e in entries or [] if e.get("vf") or e.get("st") in ("verified", "valid")]
    return {"value": win["v"] if win else None,
            "last_verified": max(verified_at) if verified_at else None,
            "entries": [{"value": e["v"], "source": e["s"], "seen_at": e["at"], "confidence": e.get("c"),
                         **({"status": e["st"]} if e.get("st") else {}), "disabled": e["s"] in disabled,
                         "chosen": e is win} for e in sorted(entries or [], key=lambda e: e["at"], reverse=True)]}


def is_role(email: str) -> bool:
    return bool(email) and is_role_address(email)


__all__ = ["normalise_email", "safe_http_url"]
