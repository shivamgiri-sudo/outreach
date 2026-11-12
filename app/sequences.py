"""Email sequence: templates, variable cleaning, due-step logic, business-hours rule, delivery."""
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from .template_vars import custom_variables, is_custom_placeholder

IST = timezone(timedelta(hours=5, minutes=30))
BUSINESS_START_HOUR, BUSINESS_END_HOUR = 9, 18
DEFAULT_DAILY_CAP = 20
# Setting that switches on the Email 1 variant with the sample-screen link (for an A/B test).
INCLUDE_LINK_SETTING = "email1_include_link"
ROLE_FALLBACK = "open roles"
MAX_ROLE_CHARS = 40

# Executive outreach sequence supplied by the owner (BookMyInterview_Executive_Email_Sequence.docx): Day 1, 4, 8, 12, 16, 21.
# `day` counts from the first send, so Day 1 is 0. The website link sits in the identity footer every email carries.
_SIGN_OFF = "\n\n{{sign_off}}\n\nNot relevant? Unsubscribe: {{unsub_link}}"

DEFAULT_SEQUENCE = [
    {"day": 0, "name": "Email 1 — compromised hiring (Day 1)",
     "subject": "Are you closing vacancies or compromising hiring?",
     "body": ("{{greeting}}\n\nWhen hiring deadlines slip and the right candidates aren't available, organisations often end up "
              "choosing between two difficult options: keep the position vacant or compromise on the quality of the hire.\n\n"
              "Neither is good for business.\n\nThe pressure to fill critical positions can lead to rushed evaluations, relaxed "
              "selection criteria and hiring decisions that may not reflect the actual requirements of the role.\n\n"
              "A vacancy getting closed is a recruitment milestone. Getting the right person is a business outcome.\n\n"
              "We've put together a case study exploring how technology can bring greater structure to candidate assessment "
              "and interview management.\n\nWould you be interested in receiving a copy?" + _SIGN_OFF)},
    {"day": 3, "name": "Email 2 — shortage of suitable candidates (Day 4)",
     "subject": "100 applications. How many genuinely fit?",
     "body": ("{{greeting}}\n\nThe challenge isn't always getting applications. It's finding candidates who genuinely meet the "
              "requirements.\n\nRecruitment teams can spend hours reviewing resumes, screening profiles and coordinating "
              "preliminary evaluations, yet struggle to identify enough suitable candidates for interviews.\n\n"
              "The result? More time spent screening, fewer relevant candidates reaching hiring managers and continued pressure "
              "to close vacancies.\n\nAre your recruiters spending more time filtering candidates than evaluating talent?" + _SIGN_OFF)},
    {"day": 7, "name": "Email 3 — cost of delayed hiring (Day 8)",
     "subject": "How much is delayed hiring costing your business?",
     "body": ("{{greeting}}\n\nEvery unfilled position has a business consequence.\n\nExisting teams carry additional workloads. "
              "Managers wait for critical resources. Projects and business priorities can lose momentum.\n\n"
              "And when hiring takes longer than planned, the pressure to close positions often increases further.\n\n"
              "The real challenge is not simply filling vacancies faster. It is improving the process without sacrificing "
              "candidate suitability and hiring quality.\n\nIs meeting planned hiring timelines becoming increasingly "
              "difficult for your organisation?" + _SIGN_OFF)},
    {"day": 11, "name": "Email 4 — process gap (Day 12)",
     "subject": "Is manual hiring slowing down better decisions?",
     "body": ("{{greeting}}\n\nResume screening, preliminary assessments, candidate evaluations and interview scheduling are all "
              "important stages of recruitment.\n\nBut when these activities involve disconnected tools and repetitive manual "
              "coordination, the process becomes difficult to manage efficiently and consistently.\n\n"
              "The opportunity is to bring more structure to candidate evaluation before valuable interviewer time is "
              "invested.\n\nHas your organisation considered using technology to streamline these stages?" + _SIGN_OFF)},
    {"day": 15, "name": "Email 5 — introducing the product (Day 16)",
     "subject": "Technology for more informed hiring decisions",
     "body": ("{{greeting}}\n\nThis is the problem {{my_product}} is designed to address.\n\n"
              "{{my_product}} is a recruitment technology platform focused on candidate assessment and interview "
              "management.\n\nIt brings together:\n"
              "- Job description and resume parsing: assess candidate-role alignment.\n"
              "- Role-specific tests: evaluate relevant skills and competencies.\n"
              "- Audio and video assessments: support preliminary candidate evaluation.\n"
              "- Candidate self-booking: simplify interview scheduling and coordination.\n\n"
              "It isn't a recruitment agency or a job listing portal. It provides technology to help organisations structure "
              "and manage their existing hiring processes.\n\nThe objective is to help recruitment teams evaluate candidates "
              "more effectively, reduce repetitive coordination and make more informed hiring decisions.\n\n"
              "Would you be open to a 20-minute walkthrough?" + _SIGN_OFF)},
    {"day": 20, "name": "Email 6 — final follow-up (Day 21)",
     "subject": "The real cost isn't just an unfilled position",
     "body": ("{{greeting}}\n\nOne final thought before I close this conversation.\n\nAn unfilled position can delay business "
              "priorities. An unsuitable shortlist can waste valuable interview time. And a compromised hiring decision can "
              "create consequences long after the vacancy is closed.\n\nThe objective should not simply be to hire faster. "
              "It should be to make better-informed hiring decisions within the required timelines.\n\n"
              "If improving candidate evaluation and interview management is on your organisation's agenda, I'd be happy to "
              "show you how {{my_product}} approaches this.\n\nWould a brief conversation be worthwhile?" + _SIGN_OFF)},
]

_VAR = re.compile(r"\{\{(\w+(?:\.\w+)?)\}\}")   # {{name}} or {{custom.<key>}}

# ---- variable cleaning ----
ACRONYMS = {"BPO", "KPO", "IT", "ITES", "HR", "AI", "CRM", "ERP", "SAP", "MIS", "QA", "UI", "UX", "FMCG", "BFSI",
            "NBFC", "HDFC", "ICICI", "KPMG", "TCS", "SBI", "LIC", "CA", "CS", "GST", "US", "UK", "EV", "BDE", "BDM",
            "CSE", "CSR", "KYC", "SEO", "DSA", "AR", "AP", "RM"}
_COMMON_SHORT = {"THE", "AND", "OF", "FOR", "NEW", "ONE", "TOP", "BIG", "SUN", "CO", "OPS", "AT", "IN", "ON", "TO"}
_COMPANY_SUFFIX = re.compile(r"[\s,.\-]*\(?\b(pvt|private|ltd|limited|llp|opc|pte|p\W{0,3}ltd|india|inc|incorporated)\b\.?\)?[\s,.]*$", re.I)
_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "shri", "sri", "smt", "kumari", "km", "sh", "prof", "er", "ca", "adv", "capt"}
_GENERIC_NAMES = {"hr", "team", "admin", "hiring", "recruiter", "recruitment", "careers", "info", "sir", "madam",
                  "manager", "contact", "support"}
_ROLE_ABBREVIATIONS = ((r"\bsr\b\.?", "Senior"), (r"\bjr\b\.?", "Junior"), (r"\basst\b\.?", "Assistant"),
                       (r"\bmgr\b\.?", "Manager"))
_ROLE_NOISE = re.compile(r"\b(urgent(ly)?|walk[- ]?in|immediate joiners?|wanted|required|vacancy|openings?|hiring(\s+for)?|work from home|wfh)\b", re.I)
_ROLE_JUNK = {"na", "n/a", "none", "nil", "various", "multiple", "test", "tbd", "role", "roles", "job", "jobs"}


def _is_acronym(word: str, text_is_caps: bool) -> bool:
    """A word to keep in capitals: a known acronym, or an all-caps word inside mixed-case text."""
    letters = re.sub(r"[^A-Za-z]", "", word)
    if not letters or not letters.isupper():
        return False
    if letters in ACRONYMS:
        return True
    if not text_is_caps:
        return len(letters) >= 2
    vowels = sum(ch in "AEIOU" for ch in letters)
    return len(letters) <= 3 and vowels <= 1 and letters not in _COMMON_SHORT


def _title_word(word: str) -> str:
    return "-".join(part[:1].upper() + part[1:].lower() for part in word.split("-"))


def _is_all_caps(text: str) -> bool:
    letters = re.sub(r"[^A-Za-z]", "", text)
    return bool(letters) and letters.isupper()


def clean_company(name) -> str:
    """'ACME TECHNOLOGIES PVT LTD' -> 'Acme Technologies'. Mixed-case names keep their own casing."""
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    while True:
        trimmed = _COMPANY_SUFFIX.sub("", text).strip()
        if trimmed == text or not trimmed:
            break
        text = trimmed
    if not _is_all_caps(text):
        return text
    return " ".join(w if _is_acronym(w, True) else _title_word(w) for w in text.split(" "))


def clean_first_name(contact_name) -> str:
    """First name without honorifics, title-cased. Empty when it is not a real name."""
    raw = str(contact_name or "")
    head = raw.strip().strip(",").split(",", 1)
    if len(head) == 2 and len(head[0].split()) == 1 and head[1].split():
        after = head[1].split()[0].strip(".,;:")           # "Sharma, Rahul" is "Last, First"; "Ravi, MBA" is a credential
        if after and not after.isupper() and not re.search(r"[\d@]", after) and len(after) >= 2:
            raw = head[1]
    words = [w.strip(".,;:") for w in re.split(r"\s+" if "@" in raw else r"[\s.]+", raw)]   # "Dr.MEENA" too
    words = [w for w in words if w and w.lower() not in _HONORIFICS]
    if not words:
        return ""
    first = words[0]
    if len(first) < 2 or re.search(r"[@\d{}]", first) or any(unicodedata.category(ch) in ("So", "Cs", "Cn") for ch in first) or first.lower() in _GENERIC_NAMES:
        return ""
    return _title_word(first)


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit + 1].rsplit(" ", 1)[0]
    return re.sub(r"\s+(and|or)$", "", cut.rstrip(" -|/,&"))


def clean_role(role) -> str:
    """'SR. EXEC - BPO (NIGHT)' -> 'senior exec - BPO'. Empty when nothing usable is left."""
    text = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", str(role or ""))
    text = re.sub(r"#\S+|\b[A-Za-z]*[-_/]?\d{2,}\b", " ", text)
    text = _ROLE_NOISE.sub(" ", text)
    caps = _is_all_caps(text)
    for pattern, full in _ROLE_ABBREVIATIONS:
        text = re.sub(pattern, full, text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -|/,:;.&!")
    if "@" in text or "http" in text.lower() or len(re.sub(r"[^A-Za-z]", "", text)) < 2 or text.lower() in _ROLE_JUNK:
        return ""
    words = [w if _is_acronym(w, caps) else w.lower() for w in text.split(" ")]
    return _shorten(" ".join(words), MAX_ROLE_CHARS)


def greeting_for(first_name: str) -> str:
    """'Hi Ravi,' when we know a first name, otherwise 'Hello,'."""
    return f"Hi {first_name}," if first_name else "Hello,"


# ---- rendering ----
def render(text: str, variables: dict) -> tuple:
    """Fill {{vars}}; unknown ones stay in place and are returned as `missing`."""
    missing = []

    def sub(match):
        key = match.group(1)
        if variables.get(key) and not re.search(r"\{\{|\}\}", str(variables[key])):   # a leftover {{x}} in the data
            return str(variables[key])
        if is_custom_placeholder(key):
            return ""                                  # a custom lead field the lead has no value for: safe fallback
        missing.append(key)
        return match.group(0)

    return _VAR.sub(sub, text), missing


def _variant_suffixes(variables: dict) -> tuple:
    """Key suffixes to try, best first, for this lead's role and the link setting."""
    has_role = variables.get("has_role", bool(variables.get("role")))
    link = bool(variables.get("include_link") and variables.get("demo_link"))
    if link and has_role:
        return ("_with_link", "")
    if link:
        return ("_with_link_no_role", "_no_role", "")
    return ("",) if has_role else ("_no_role", "")


def pick_variant(step: dict, variables: dict) -> dict:
    """A copy of the step with the subject/body that fit this lead, plus a `variant` name for A/B tracking."""
    suffixes = _variant_suffixes(variables)

    def choose(field):
        return next((s for s in suffixes if step.get(field + s)), "")

    body_suffix = choose("body")
    variant = {"_with_link": "with_link", "_with_link_no_role": "with_link", "_no_role": "no_role"}.get(body_suffix, "default")
    return {**step, "subject": step["subject" + choose("subject")], "body": step["body" + body_suffix], "variant": variant}


def due_steps(enrolled_at: datetime, sent_days: list, now: datetime, sequence=DEFAULT_SEQUENCE,
              last_sent_at: datetime = None) -> list:
    """The single next unsent step that is due (never catches up in a burst).

    The first step is due once its day since enrolment has arrived. Each later step is spaced from the email
    actually sent before it (day 4 comes 3 days after Email 1 went out, day 8 four days after that), so a lead
    whose first email was delayed by a backlog still gets the intended rhythm.
    """
    for index, step in enumerate(sequence):
        if step["day"] in sent_days:
            continue
        if index == 0 or not sent_days or last_sent_at is None:
            return [step] if step["day"] <= (now - enrolled_at).days else []
        gap = step["day"] - sequence[index - 1]["day"]
        return [step] if now - last_sent_at >= timedelta(days=gap) else []
    return []


def identity_footer(settings: dict) -> str:
    """Who is writing: company name plus a postal address if set, else the website and contact email; the privacy
    notice link (settings.privacy_url, DPDP) follows on its own line when it is set.

    Indian rules don't require a postal address in a business email; US (CAN-SPAM) and Canadian (CASL) rules do, so
    the sequence engine skips leads in those countries while no address is set (see NEEDS_POSTAL_ADDRESS)."""
    contact = settings.get("sender_address") or " · ".join(
        str(settings.get(k, "")).strip() for k in ("sender_website", "sender_contact_email") if str(settings.get(k, "")).strip())
    identity = " · ".join(p for p in (str(settings.get("sender_company", "")).strip(), str(contact).strip()) if p)
    privacy = str(settings.get("privacy_url") or "").strip()
    return "\n".join(p for p in (identity, f"Privacy notice: {privacy}" if privacy else "") if p)


# Countries whose anti-spam laws require a physical postal address in commercial email.
NEEDS_POSTAL_ADDRESS = {"us", "usa", "united states", "united states of america", "ca", "can", "canada"}


def needs_postal_address(lead: dict) -> bool:
    return re.sub(r"[^a-z ]", "", str(lead.get("country") or "").lower()).strip() in NEEDS_POSTAL_ADDRESS   # "U.S.A." too


def in_business_hours(now: datetime) -> bool:
    local = now.astimezone(IST)
    return local.weekday() < 5 and BUSINESS_START_HOUR <= local.hour < BUSINESS_END_HOUR


def lead_variables(lead: dict, sender: str, base_url: str, extra: dict = None, secret: str = "", brand=None) -> dict:
    """Cleaned per-lead values for templates. `first_name` keeps 'there' as a fallback for old custom templates.

    With the server secret, unsub_link is the signed form (/u/<id>.<signature>) that every real message uses.
    `brand` is the store (or settings) whose brand/offer fill {{my_company}}, {{sign_off}} and the other brand
    placeholders (app/brand.py); without it the default brand is used."""
    from .brand import brand_variables
    from .unsubscribe import signature
    first = clean_first_name(lead.get("contact_name"))
    role = clean_role(lead.get("role"))
    return {
        **brand_variables(brand if brand is not None else {}),
        "first_name": first or "there", "greeting": greeting_for(first),
        "company": clean_company(lead.get("company")) or lead.get("domain") or "your company",
        "role": role or ROLE_FALLBACK, "has_role": bool(role), "sender": sender,
        "unsub_link": f"{base_url}/u/{lead['id']}" + (f".{signature(secret, str(lead['id']))}" if secret else ""),
        **custom_variables(lead),
        **(extra or {}),
    }
