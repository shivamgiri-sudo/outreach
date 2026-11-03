"""Pure clean-up rules for imported lead fields: names, companies, titles, emails, phones, domains, places, sizes.

No I/O and no network. Every function takes a raw value and returns the cleaned value, so each rule is unit-tested.
"""
import re
from typing import Optional
from urllib.parse import urlparse

from .hygiene import EMAIL_RE

NULL_TOKENS = frozenset({"", "-", "--", "—", "–", ".", "na", "n/a", "n.a.", "n.a", "nil", "null", "none", "nan",
                         "not available", "#n/a", "unknown", "not known", "not verified", "tbd", "xx", "xxx"})
_INVISIBLE = re.compile("[​‌‍⁠﻿­]")


def clean_text(value) -> str:
    """Trim, collapse spaces, drop invisible characters; placeholder values like 'N/A' or '-' become ''."""
    text = re.sub(r"\s+", " ", _INVISIBLE.sub("", "" if value is None else str(value))).strip()
    return "" if text.lower() in NULL_TOKENS else text


# ---------------------------------------------------------------- letter case
VOWELS = frozenset("aeiouy")
ACRONYMS = frozenset({
    "HR", "TA", "HRBP", "CEO", "CFO", "CTO", "COO", "CHRO", "CXO", "CMO", "CIO", "MD", "VP", "AVP", "SVP", "EVP", "GM",
    "AGM", "DGM", "IT", "ITES", "L&D", "BD", "R&D", "QA", "QC", "MIS", "BPO", "KPO", "US", "UK", "UAE", "APAC", "EMEA",
    "SEO", "PR", "ERP", "SAP", "AI", "ML", "UI", "UX", "HSE", "EHS", "F&A", "P&L", "TL", "NBFC", "BFSI", "FMCG", "EV",
    "ICICI", "HDFC", "SBI", "LIC", "TCS", "HCL", "IBM", "L&T", "ITC", "ONGC", "BHEL", "NTPC", "GAIL", "IOCL", "BPCL",
    "HPCL", "MRF", "DLF", "ACC", "ABB", "GE", "HP", "EY", "KPMG", "PWC", "BSNL", "MTNL", "IIT", "IIM", "NIIT", "ITI",
    "LLP", "OPC", "CA", "CS", "NGO", "SME", "MSME", "B2B", "B2C", "SAAS", "IOT", "AR", "VR",
})
NOT_ACRONYMS = frozenset({"pvt", "ltd", "mr", "ms", "mrs", "dr", "st", "jr", "sr", "smt", "shri", "km", "nr"})
SMALL_WORDS = frozenset({"and", "of", "the", "for", "in", "on", "at", "by", "to", "with"})


def _is_one_case(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and (all(c.isupper() for c in letters) or all(c.islower() for c in letters))


def _cap_runs(token: str) -> str:
    """'o'brien' -> "O'Brien", 'r.k.' -> 'R.K.', 'anna-maria' -> 'Anna-Maria'."""
    return re.sub(r"[A-Za-z]+", lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), token)


def smart_title(text: str, keep_small_words: bool = False) -> str:
    """Title Case for ALL CAPS or all lower text, keeping acronyms (HR, ICICI, L&T, short consonant-only tokens)."""
    if not _is_one_case(text):
        return text
    out = []
    for i, token in enumerate(text.split(" ")):
        core = re.sub(r"[^A-Za-z0-9&]", "", token)
        lower = core.lower()
        if not core:
            out.append(token)
        elif core.upper() in ACRONYMS or any(ch.isdigit() for ch in core) or (
                "&" in core) or (len(core) <= 4 and lower not in NOT_ACRONYMS and not set(lower) & VOWELS and core.isalpha()):
            out.append(token.upper())
        elif keep_small_words and i > 0 and lower in SMALL_WORDS:
            out.append(token.lower())
        else:
            out.append(_cap_runs(token))
    return " ".join(out)


# ---------------------------------------------------------------- people
_HONORIFIC = re.compile(r"^(?:mr|mrs|ms|miss|mx|dr|shri|smt|kumari|km|ca|er|adv|prof|capt)\.?\s+", re.IGNORECASE)
_CREDENTIALS = re.compile(r",\s*(?:[A-Z][A-Za-z]{0,1}[A-Z][A-Z\-]*\.?\s*)+$")


def clean_person_name(raw) -> str:
    """Strip honorifics (Mr, Ms, Dr, Shri, Smt, CA…), bracketed notes and trailing credentials; fix ALL CAPS."""
    text = clean_text(raw)
    text = _CREDENTIALS.sub("", text)
    text = clean_text(re.sub(r"\s*\([^)]*\)\s*", " ", text))
    for _ in range(3):
        stripped = _HONORIFIC.sub("", text)
        if stripped == text or not stripped.strip():
            break
        text = stripped.strip()
    return smart_title(text.strip(" ,.-"))


def combine_name(first, last, full="") -> str:
    if clean_text(full):
        return clean_person_name(full)
    return " ".join(p for p in (clean_person_name(first), clean_person_name(last)) if p)


def person_key(name) -> str:
    """Order-free key for comparing two spellings of one person ('Ravi Kumar' == 'KUMAR, Ravi')."""
    tokens = re.sub(r"[^a-z ]+", " ", clean_person_name(name).lower()).split()
    return " ".join(sorted(t for t in tokens if len(t) > 1 or len(tokens) == 1))


def names_compatible(a, b) -> bool:
    """'Ravi K', 'R Kumar' and 'Ravi' can all be 'Ravi Kumar'; 'Anita Rao' cannot. Unknown names are compatible."""
    ta = re.sub(r"[^a-z ]+", " ", clean_person_name(a).lower()).split()
    tb = re.sub(r"[^a-z ]+", " ", clean_person_name(b).lower()).split()
    if not ta or not tb:
        return True
    short, long_ = sorted((ta, tb), key=len)
    used = set()
    for token in short:
        hit = next((j for j, other in enumerate(long_) if j not in used and (
            other == token or (len(token) == 1 and other.startswith(token)) or (len(other) == 1 and token.startswith(other)))),
            None)
        if hit is None:
            return False
        used.add(hit)
    return True


# ---------------------------------------------------------------- companies
_MS_PREFIX = re.compile(r"^m\s*/\s*s\.?\s+", re.IGNORECASE)
_LEGAL_SUFFIX = re.compile(
    r"[\s,]*(?:\(\s*opc\s*\)|\(\s*p\s*\)\s*\.?\s*(?:ltd|limited)|opc\s+(?:pvt|private)\.?\s*(?:ltd|limited)"
    r"|(?:pvt|private)\.?\s*(?:ltd|limited)|p\.?\s*ltd|pte\.?\s*ltd|limited|ltd|l\.?l\.?p|inc|incorporated|opc|plc|gmbh|pvt)"
    r"\s*\.?\s*$", re.IGNORECASE)


def clean_company(raw) -> tuple:
    """(display name, legal name). 'ACME RETAIL PVT. LTD.' -> ('Acme Retail', 'Acme Retail Pvt. Ltd.')."""
    text = smart_title(_MS_PREFIX.sub("", clean_text(raw)), keep_small_words=True)
    if not text:
        return "", ""
    display = text
    for _ in range(3):
        trimmed = _LEGAL_SUFFIX.sub("", display).strip(" ,.-&")
        if trimmed == display or not trimmed:
            break
        display = trimmed
    return display, (text if display != text else "")


_KEY_NOISE = re.compile(r"\b(?:pvt|private|ltd|limited|llp|inc|opc|plc|india|the|m s)\b")
GENERIC_COMPANY_WORDS = frozenset({
    "technologies", "technology", "tech", "solutions", "services", "systems", "group", "enterprises", "enterprise",
    "industries", "global", "international", "consulting", "consultants", "infotech", "software", "labs", "ventures",
    "corporation", "corp", "company", "co",
})


def company_key(name) -> str:
    """Comparison key: lower case, no punctuation, no legal suffix, '&' == 'and', no brackets."""
    base = re.sub(r"\([^)]*\)", " ", clean_text(name).lower()).replace("&", " and ")
    base = re.sub(r"[^a-z0-9]+", " ", base)
    return re.sub(r"\s+", " ", _KEY_NOISE.sub(" ", base)).strip()


def company_similarity(a, b) -> float:
    """Token-set similarity (difflib). A subset only scores 1.0 when the extra words are generic ('Acme Technologies')."""
    from difflib import SequenceMatcher
    ka, kb = company_key(a), company_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    ta, tb = set(ka.split()), set(kb.split())
    common = sorted(ta & tb)
    s1 = " ".join(common + sorted(ta - tb))
    s2 = " ".join(common + sorted(tb - ta))
    score = SequenceMatcher(None, s1, s2).ratio()
    if common and ((ta - tb) <= GENERIC_COMPANY_WORDS and (tb - ta) <= GENERIC_COMPANY_WORDS):
        score = max(score, 0.95)
    return round(score, 3)


# ---------------------------------------------------------------- titles
_TITLE_PHRASES = [
    (r"\bchief human resources? officer\b|\bchief hr officer\b", "chro"), (r"\bchief executive officer\b", "ceo"),
    (r"\bchief operating officer\b", "coo"), (r"\bchief financial officer\b", "cfo"), (r"\bchief technology officer\b", "cto"),
    (r"\bhuman resources?\b", "hr"), (r"\btalent acquisition\b", "ta"), (r"\bassistant vice president\b", "avp"),
    (r"\bsenior vice president\b", "svp"), (r"\bvice president\b", "vp"), (r"\bmanaging director\b", "md"),
    (r"\bgeneral manager\b", "gm"), (r"\bco\s*-?\s*founder\b", "cofounder"), (r"\bbusiness partner\b", "bp"),
    (r"\bhead of\b", "head"), (r"\bhr\s*bp\b", "hrbp"),
]
_TITLE_ABBREV = {"sr": "senior", "snr": "senior", "jr": "junior", "asst": "assistant", "assoc": "associate", "mgr": "manager",
                 "exec": "executive", "exe": "executive", "dy": "deputy", "hod": "head", "recruitment": "recruiter"}
_TITLE_STOP = frozenset({"and", "of", "the", "for", "at", "in"})
_F = frozenset
TITLE_CANON = {
    _F({"hr", "head"}): "HR Head", _F({"ta", "head"}): "TA Head", _F({"ta", "lead"}): "TA Lead", _F({"hr", "lead"}): "HR Lead",
    _F({"hrbp"}): "HRBP", _F({"hr", "bp"}): "HRBP", _F({"senior", "hrbp"}): "Senior HRBP",
    _F({"founder", "ceo"}): "Founder & CEO", _F({"cofounder", "ceo"}): "Co-founder & CEO", _F({"founder"}): "Founder",
    _F({"cofounder"}): "Co-founder", _F({"founder", "md"}): "Founder & MD", _F({"founder", "director"}): "Founder & Director",
    _F({"ceo"}): "CEO", _F({"md"}): "Managing Director", _F({"chro"}): "CHRO", _F({"vp", "hr"}): "VP HR",
    _F({"avp", "hr"}): "AVP HR", _F({"hr", "manager"}): "HR Manager", _F({"ta", "manager"}): "TA Manager",
    _F({"hr", "executive"}): "HR Executive", _F({"ta", "executive"}): "TA Executive", _F({"ta", "specialist"}): "TA Specialist",
    _F({"hr", "director"}): "HR Director", _F({"hr", "generalist"}): "HR Generalist", _F({"hr"}): "HR",
    _F({"ta"}): "Talent Acquisition", _F({"recruiter"}): "Recruiter", _F({"senior", "recruiter"}): "Senior Recruiter",
    _F({"senior", "hr", "manager"}): "Senior HR Manager", _F({"senior", "hr", "executive"}): "Senior HR Executive",
    _F({"owner"}): "Owner", _F({"proprietor"}): "Proprietor", _F({"head", "people"}): "Head of People",
    _F({"head", "hr", "ta"}): "Head HR & TA",
}


def _title_tokens(text: str) -> list:
    low = text.lower().replace("&", " and ").replace("+", " and ")
    for pattern, repl in _TITLE_PHRASES:
        low = re.sub(pattern, repl, low)
    low = re.sub(r"[./,|\-–—:()]+", " ", low)
    return [_TITLE_ABBREV.get(t, t) for t in low.split() if t not in _TITLE_STOP]


def clean_title(raw) -> str:
    """'head - human resources' -> 'HR Head', 'FOUNDER/CEO' -> 'Founder & CEO', 'Sr. HR Exec' -> 'Senior HR Executive'."""
    text = clean_text(raw)
    if not text:
        return ""
    canon = TITLE_CANON.get(frozenset(_title_tokens(text)))
    if canon:
        return canon
    text, words = smart_title(text, keep_small_words=True), []
    for word in text.split(" "):
        bare = word.lower().strip(".,")
        words.append(_TITLE_ABBREV[bare].capitalize() if bare in _TITLE_ABBREV and bare != "recruitment" else word)
    return " ".join(words)


SENIORITY_RULES = (
    ("owner", {"founder", "cofounder", "owner", "proprietor", "promoter", "chairman", "partner"}),
    ("c-level", {"ceo", "cfo", "coo", "cto", "chro", "cxo", "cmo", "cio", "chief", "president", "md"}),
    ("vp", {"vp", "avp", "svp", "evp"}),
    ("head", {"head", "director", "gm", "lead", "principal"}),
    ("manager", {"manager", "agm", "dgm", "supervisor", "hrbp", "bp", "incharge"}),
    ("executive", {"executive", "associate", "specialist", "recruiter", "coordinator", "analyst", "officer", "assistant",
                   "intern", "trainee", "generalist", "consultant", "partner_"}),
)


def seniority_of(title) -> str:
    """owner / c-level / vp / head / manager / executive, or '' when the title does not say."""
    tokens = set(_title_tokens(clean_text(title)))
    if "team" in tokens and "lead" in tokens:
        return "manager"
    if "vice" in tokens and "president" in tokens:
        return "vp"
    return next((bucket for bucket, words in SENIORITY_RULES if tokens & words), "")


def department_of(title) -> str:
    """Founder / TA / HR / Ops / Other — who in the company this person is."""
    tokens = set(_title_tokens(clean_text(title)))
    if not tokens:
        return ""
    if tokens & {"founder", "cofounder", "owner", "proprietor", "promoter", "partner", "ceo", "md", "chairman", "president"}:
        return "Founder"
    if tokens & {"ta", "recruiter", "recruiting", "hiring", "staffing", "sourcing", "talent"}:
        return "TA"
    if tokens & {"hr", "hrbp", "chro", "people", "personnel", "payroll", "l", "culture"}:
        return "HR"
    if tokens & {"operations", "ops", "coo", "plant", "production", "admin", "administration", "facility", "facilities",
                 "branch", "store", "warehouse", "logistics", "gm"}:
        return "Ops"
    return "Other"


# ---------------------------------------------------------------- email
FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.in", "ymail.com", "rocketmail.com", "outlook.com",
    "outlook.in", "hotmail.com", "hotmail.co.in", "live.com", "live.in", "msn.com", "rediffmail.com", "rediff.com",
    "icloud.com", "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "zohomail.in", "zohomail.com",
    "yandex.com", "mail.com", "gmx.com", "inbox.com", "sify.com", "vsnl.net", "vsnl.com", "in.com", "indiatimes.com",
})
ROLE_PARTS = frozenset({
    "info", "contact", "contactus", "hello", "hi", "admin", "support", "sales", "hr", "hrd", "careers", "career", "jobs",
    "job", "office", "mail", "enquiry", "enquiries", "inquiry", "team", "accounts", "account", "noreply", "no-reply",
    "marketing", "billing", "help", "care", "customercare", "feedback", "webmaster", "recruitment", "recruit", "hiring",
    "talent", "resume", "resumes", "cv", "placement", "placements", "hrteam", "hrdept", "humanresources", "general",
})
EMAIL_TYPOS = {"gmial.com": "gmail.com", "gmai.com": "gmail.com", "gamil.com": "gmail.com", "gmail.co": "gmail.com",
               "gmail.con": "gmail.com", "gmail.cm": "gmail.com", "gnail.com": "gmail.com", "yahoo.con": "yahoo.com",
               "yaho.com": "yahoo.com", "hotmail.con": "hotmail.com", "outlook.con": "outlook.com", "rediffmail.con": "rediffmail.com"}


def clean_email(raw) -> tuple:
    """(email or None, problem or None). Lower-cased and trimmed; typos in common free-mail domains are fixed."""
    text = clean_text(raw).lower()
    text = re.sub(r"^mailto:", "", text).strip(" <>\"'.,;:")
    if not text:
        return None, None
    local, _, domain = text.partition("@")
    if domain in EMAIL_TYPOS:
        text = f"{local}@{EMAIL_TYPOS[domain]}"
    ok = (EMAIL_RE.match(text) and ".." not in text and not local.startswith(".") and not local.endswith(".")
          and re.search(r"\.[a-z]{2,}$", text))
    if not ok:
        return None, "invalid_email"
    return text, ("typo_fixed" if domain in EMAIL_TYPOS else None)


def split_emails(raw) -> list:
    return [part for part in re.split(r"[,;/\s]+", clean_text(raw)) if part]


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[1] if email and "@" in email else ""


def is_free_mail(email: str) -> bool:
    return email_domain(email) in FREE_MAIL


def is_role_email(email: str) -> bool:
    local = (email or "").split("@", 1)[0]
    first = re.split(r"[._+\-]", local)[0]
    return local in ROLE_PARTS or (first in ROLE_PARTS and len(local) <= len(first) + 8)


def email_alias_key(email: str) -> str:
    """Gmail ignores dots and '+tags': r.avi+jobs@googlemail.com and ravi@gmail.com reach one inbox. Used for dedupe only."""
    local, _, domain = (email or "").lower().partition("@")
    if domain in ("gmail.com", "googlemail.com"):
        return f"{local.split('+', 1)[0].replace('.', '')}@gmail.com"
    return (email or "").lower()


# ---------------------------------------------------------------- phones
_PHONE_SPLIT = re.compile(r"[,;/|\n]+|\s+or\s+", re.IGNORECASE)
_EXTENSION = re.compile(r"\s*(?:ext\.?|extn\.?|x)\s*\d{1,5}$", re.IGNORECASE)
_STD_LANDLINE = re.compile(r"^(?:\+91[\s\-]*)?\(?(0?\d{2,4})\)?[\s\-]+([\d\s\-]{6,10})$")
FAKE_NUMBERS = frozenset({"1234567890", "0123456789"})


def _national(national: str) -> Optional[tuple]:
    if len(national) != 10 or national[0] == "0":
        return None
    if national in FAKE_NUMBERS or len(set(national)) == 1:
        return ("junk", national)
    return ("+91" + national, "mobile" if national[0] in "6789" else "landline")


def parse_phone(part: str) -> tuple:
    """One number -> ((e164, type) or None, problem or None). Types: mobile, landline, intl."""
    text = _EXTENSION.sub("", clean_text(part))
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None, ("invalid_phone" if text else None)
    plus = text.startswith("+") or digits.startswith("00")
    if digits.startswith("00"):
        digits = digits[2:]
    std = _STD_LANDLINE.match(text)
    if std and (text.startswith("0") or text.startswith("(0") or text.startswith("+91")):
        national = (std.group(1) + re.sub(r"\D", "", std.group(2))).lstrip("0")
        sub_len = len(re.sub(r"\D", "", std.group(2)))
        if len(national) == 10 and 6 <= sub_len <= 8 and national not in FAKE_NUMBERS:
            return ("+91" + national, "landline"), None
    if plus and not digits.startswith("91"):
        return (("+" + digits, "intl"), None) if 8 <= len(digits) <= 15 else (None, "invalid_phone")
    if len(digits) == 12 and digits.startswith("91"):
        national = digits[2:]
    elif len(digits) == 13 and digits.startswith("091"):
        national = digits[3:]
    elif len(digits) == 11 and digits.startswith("1800"):
        return ("+91" + digits, "landline"), None
    elif len(digits) == 11 and digits.startswith("0"):
        national = digits[1:]
    elif len(digits) == 10:
        national = digits
    else:
        return None, ("landline_without_std" if len(digits) in (6, 7, 8) else "invalid_phone")
    result = _national(national)
    if result is None:
        return None, "invalid_phone"
    if result[0] == "junk":
        return None, "junk_phone"
    return result, None


def parse_phones(raw) -> dict:
    """All numbers in one cell ('98100 12345 / 011-2345 6789; +44 20 7946 0958') -> numbers + problems."""
    numbers, problems = [], []
    for part in _PHONE_SPLIT.split(clean_text(raw)):
        if not part.strip():
            continue
        found, problem = parse_phone(part)
        if found and found[0] not in [n[0] for n in numbers]:
            numbers.append(found)
        if problem:
            problems.append((problem, part.strip()))
    return {"numbers": numbers, "problems": problems}


def best_phone(numbers: list) -> tuple:
    """Primary number: an Indian mobile first, then an international number, then a landline."""
    for kind in ("mobile", "intl", "landline"):
        for e164, t in numbers:
            if t == kind:
                return e164, t
    return None, None


def phone_type(e164: str) -> str:
    if not e164:
        return ""
    if not e164.startswith("+91"):
        return "intl"
    return "mobile" if e164[3:4] in ("6", "7", "8", "9") and len(e164) == 13 else "landline"


# ---------------------------------------------------------------- domains
TWO_LEVEL_SUFFIXES = frozenset({
    "co.in", "net.in", "org.in", "gen.in", "firm.in", "ind.in", "ac.in", "edu.in", "res.in", "gov.in", "nic.in", "mil.in",
    "co.uk", "org.uk", "ac.uk", "gov.uk", "ltd.uk", "plc.uk", "me.uk", "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "co.nz", "com.sg", "edu.sg", "gov.sg", "com.my", "co.za", "com.br", "co.jp", "ne.jp", "or.jp", "com.cn", "com.hk",
    "co.id", "com.ph", "com.pk", "com.bd", "com.np", "com.lk", "co.ke", "com.ng", "com.mx", "com.tr", "co.il", "co.kr",
    "com.sa", "co.ae", "net.ae", "org.ae", "gov.ae", "com.qa", "com.eg", "com.vn", "co.th", "com.tw",
})
NOT_COMPANY_SITES = frozenset({
    "linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com", "wa.me", "whatsapp.com",
    "google.com", "goo.gl", "bit.ly", "justdial.com", "indiamart.com", "naukri.com", "sulekha.com", "tradeindia.com",
    "glassdoor.com", "glassdoor.co.in", "indeed.com", "shine.com", "apna.co", "foundit.in", "wikipedia.org",
})


def registrable_domain(host: str) -> str:
    """eTLD+1 from a small suffix list: careers.acme.co.in -> acme.co.in, blog.acme.com -> acme.com."""
    parts = [p for p in (host or "").lower().strip(".").split(".") if p]
    if len(parts) < 2:
        return ""
    keep = 3 if ".".join(parts[-2:]) in TWO_LEVEL_SUFFIXES else 2
    return ".".join(parts[-keep:]) if len(parts) >= keep else ""


def host_of(raw) -> str:
    text = clean_text(raw).lower()
    if not text:
        return ""
    if "@" in text and "//" not in text:
        text = text.rsplit("@", 1)[1]
    host = urlparse(text if "//" in text else f"//{text}").netloc
    host = host.rsplit("@", 1)[-1].split(":")[0].strip(".")
    host = re.sub(r"^www\d*\.", "", host)
    if not re.fullmatch(r"[a-z0-9\-]+(\.[a-z0-9\-]+)*\.[a-z]{2,}", host):
        return ""
    return host


def website_domain(raw) -> tuple:
    """(registrable domain, clean https website, problem). Free-mail, social and job-board hosts are not company sites."""
    host = host_of(raw)
    if not host:
        return "", "", ("invalid_website" if clean_text(raw) else None)
    domain = registrable_domain(host)
    if not domain:
        return "", "", "invalid_website"
    if domain in FREE_MAIL:
        return "", "", "free_mail_domain"
    if domain in NOT_COMPANY_SITES:
        return "", "", "not_company_site"
    return domain, f"https://{host}", None


def linkedin_url(raw, kind: str = "in") -> str:
    """A tidy https LinkedIn profile (kind 'in') or company (kind 'company') link, or ''."""
    text = clean_text(raw)
    match = re.search(rf"(?:https?://)?(?:[a-z]{{2,3}}\.)?linkedin\.com/{kind}/([A-Za-z0-9\-_%.]+)", text, re.IGNORECASE)
    return f"https://www.linkedin.com/{kind}/{match.group(1).rstrip('/')}" if match else ""
