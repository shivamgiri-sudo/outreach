"""Email sequence: templates, variable cleaning, due-step logic, business-hours rule, delivery."""
import re
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
BUSINESS_START_HOUR, BUSINESS_END_HOUR = 9, 18
DEFAULT_DAILY_CAP = 20
# Setting that switches on the Email 1 variant with the sample-screen link (for an A/B test).
INCLUDE_LINK_SETTING = "email1_include_link"
ROLE_FALLBACK = "open roles"
MAX_ROLE_CHARS = 40

_SIGN_OFF = "\n\n— {{sender}}\n\nUnsubscribe: {{unsub_link}}"
_EMAIL1_INTRO = ("BookMyInterview screens each applicant once (a short voice answer plus a few MCQs), and the best "
                 "ones book their own interview slot. We're in beta. {{offer_line}}")

DEFAULT_SEQUENCE = [
    {"day": 0, "name": "Email 1 — sample screen",
     "subject": "{{role}} hiring at {{company}}",
     "subject_no_role": "Hiring at {{company}}",
     "body": ("{{greeting}}\n\nSaw that {{company}} is hiring for {{role}}.\n\n" + _EMAIL1_INTRO +
              "\n\nI can draft a sample screen for this role. Want me to send it?"
              "\n\n— {{sender}}\n\nNot relevant? Unsubscribe: {{unsub_link}}"),
     "body_no_role": ("{{greeting}}\n\nSaw that {{company}} is hiring.\n\n" + _EMAIL1_INTRO +
                      "\n\nI can draft a sample screen for one of your openings. "
                      "Want me to send it?\n\n— {{sender}}\n\nNot relevant? Unsubscribe: {{unsub_link}}"),
     "body_with_link": ("{{greeting}}\n\nSaw that {{company}} is hiring for {{role}}.\n\n" + _EMAIL1_INTRO +
                        "\n\nI drafted a sample screen for this role: {{demo_link}}"
                        "\n\nWorth a look?\n\n— {{sender}}\n\nNot relevant? Unsubscribe: {{unsub_link}}"),
     "body_with_link_no_role": ("{{greeting}}\n\nSaw that {{company}} is hiring.\n\n" + _EMAIL1_INTRO +
                                "\n\nI drafted a sample screen for you: {{demo_link}}"
                                "\n\nWorth a look?\n\n— {{sender}}\n\nNot relevant? Unsubscribe: {{unsub_link}}")},
    {"day": 3, "name": "Email 2 — useful tip",
     "subject": "A screening tip for {{role}}",
     "subject_no_role": "A screening tip for high-volume hiring",
     "body": ("{{greeting}}\n\nOne thing that helps with high-volume roles like {{role}}: before any call, ask every "
              "applicant the same 3 short questions (one about the job, one situation, one on notice period and pay). "
              "Then compare the answers side by side instead of reading CVs top to bottom. Weak fits show up fast."
              "\n\nHappy to draft those 3 questions for your opening. Just reply." + _SIGN_OFF),
     "body_no_role": ("{{greeting}}\n\nOne thing that helps with high-volume hiring: before any call, ask every "
                      "applicant the same 3 short questions (one about the job, one situation, one on notice period "
                      "and pay). Then compare the answers side by side instead of reading CVs top to bottom. "
                      "Weak fits show up fast.\n\nHappy to draft those 3 questions for your opening. Just reply."
                      + _SIGN_OFF)},
    {"day": 7, "name": "Email 3 — right person?",
     "subject": "Right person for hiring at {{company}}?",
     "body": "{{greeting}}\n\nIf someone else handles hiring at {{company}}, could you point me to them? Thank you." + _SIGN_OFF},
    {"day": 14, "name": "Email 4 — permission close",
     "subject": "Should I stop writing?",
     "body": ("{{greeting}}\n\nI don't want to clutter your inbox, so this is my last note. If hiring help isn't a "
              "priority, no reply needed. If you'd like a sample screen drafted for {{role}}, reply \"sample\" and "
              "I'll send it." + _SIGN_OFF),
     "body_no_role": ("{{greeting}}\n\nI don't want to clutter your inbox, so this is my last note. If hiring help "
                      "isn't a priority, no reply needed. If you'd like a sample screen drafted for one of your "
                      "openings, reply \"sample\" and I'll send it." + _SIGN_OFF)},
]

_VAR = re.compile(r"\{\{(\w+)\}\}")

# ---- variable cleaning ----
ACRONYMS = {"BPO", "KPO", "IT", "ITES", "HR", "AI", "CRM", "ERP", "SAP", "MIS", "QA", "UI", "UX", "FMCG", "BFSI",
            "NBFC", "HDFC", "ICICI", "KPMG", "TCS", "SBI", "LIC", "CA", "CS", "GST", "US", "UK", "EV", "BDE", "BDM",
            "CSE", "CSR", "KYC", "SEO", "DSA", "AR", "AP", "RM"}
_COMMON_SHORT = {"THE", "AND", "OF", "FOR", "NEW", "ONE", "TOP", "BIG", "SUN", "CO", "OPS", "AT", "IN", "ON", "TO"}
_COMPANY_SUFFIX = re.compile(r"[\s,.\-]*\(?\b(pvt|private|ltd|limited|llp|india|inc|incorporated)\b\.?\)?[\s,.]*$", re.I)
_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "shri", "sri", "smt", "kumari"}
_GENERIC_NAMES = {"hr", "team", "admin", "hiring", "recruiter", "recruitment", "careers", "info", "sir", "madam",
                  "manager", "contact", "support"}
_ROLE_ABBREVIATIONS = ((r"\bsr\b\.?", "Senior"), (r"\bjr\b\.?", "Junior"), (r"\basst\b\.?", "Assistant"),
                       (r"\bmgr\b\.?", "Manager"))
_ROLE_NOISE = re.compile(r"\b(urgent(ly)?|walk[- ]?in|immediate joiners?|wanted|required|vacancy|openings?)\b", re.I)
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
    words = [w.strip(".,;:") for w in str(contact_name or "").split()]
    words = [w for w in words if w and w.lower() not in _HONORIFICS]
    if not words:
        return ""
    first = words[0]
    if len(first) < 2 or re.search(r"[@\d]", first) or first.lower() in _GENERIC_NAMES:
        return ""
    return _title_word(first)


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit + 1].rsplit(" ", 1)[0]
    return cut.rstrip(" -|/,&")


def clean_role(role) -> str:
    """'SR. EXEC - BPO (NIGHT)' -> 'senior exec - BPO'. Empty when nothing usable is left."""
    text = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", str(role or ""))
    text = re.sub(r"#\S+|\b[A-Za-z]*[-_/]?\d{2,}\b", " ", text)
    text = _ROLE_NOISE.sub(" ", text)
    caps = _is_all_caps(text)
    for pattern, full in _ROLE_ABBREVIATIONS:
        text = re.sub(pattern, full, text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" -|/,:;.&")
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
        if variables.get(key):
            return str(variables[key])
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
    actually sent before it (day 3 comes 3 days after Email 1 went out, day 7 four days after that), so a lead
    whose first email was delayed by a backlog still gets the intended 3/4/7-day rhythm.
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


def lead_variables(lead: dict, sender: str, base_url: str, extra: dict = None, secret: str = "") -> dict:
    """Cleaned per-lead values for templates. `first_name` keeps 'there' as a fallback for old custom templates.

    With the server secret, unsub_link is the signed form (/u/<id>.<signature>) that every real message uses."""
    from .unsubscribe import signature
    first = clean_first_name(lead.get("contact_name"))
    role = clean_role(lead.get("role"))
    return {
        "first_name": first or "there", "greeting": greeting_for(first),
        "company": clean_company(lead.get("company")) or lead.get("domain") or "your company",
        "role": role or ROLE_FALLBACK, "has_role": bool(role), "sender": sender,
        "unsub_link": f"{base_url}/u/{lead['id']}" + (f".{signature(secret, str(lead['id']))}" if secret else ""),
        **(extra or {}),
    }
