"""Hiring-lead engine: one pass over the leads you already have that turns a hiring signal into a person and an email,
and says honestly how far each result can be trusted.

  1. Hiring signal     open roles, job age, reposts (already on each lead from the job sources).
  2. Who posted it     employer / agency / unknown: staffing firms and recruiters are flagged, not treated as the employer.
  3. Decision maker    HR head, TA head, CHRO, CEO (found by Find & enrich and the finder sources; this engine only uses them).
  4. Email             a published address is used as is. Otherwise the company's own format is inferred from the
                       published addresses already known at that domain, and candidate addresses are built from it.
                       A guess is stored as `email_guess`, never as `email`, so nothing is ever sent to a guess.
  5. Verify + compile  each guess is checked without sending anything (mail-server lookup, the self-hosted mailbox probe when port 25 is available, and any connected verifier).
                       Only a mailbox a verifier calls valid is promoted to `email`. The sheet carries a trust column.
"""
import re
from collections import Counter
from typing import Optional

from .hygiene import business_domain, is_role_address, normalise_email
from .providers import valid_domain
from .sources import registry, waterfall
from .sources.base import split_name

AGENCY_WORDS = re.compile(
    r"\b(staffing|manpower|man power|recruit(?:er|ers|ment|ing)?|placement|headhunt\w*|executive search|search firm|"
    r"hr (?:solutions?|services|consult\w*|outsourc\w*)|human resource (?:solutions?|services|consult\w*)|"
    r"talent (?:solutions?|acquisition services|partners?|hunt)|workforce (?:solutions?|services)|"
    r"outsourc\w*|job ?(?:consult\w*|portal|zone|hub)|careers? consult\w*|payroll services|rpo)\b", re.I)
KNOWN_AGENCIES = ("teamlease", "randstad", "adecco", "quess", "ikya", "manpowergroup", "kelly services", "vision india",
                  "naukri", "indeed", "timesjobs", "monster", "shine.com", "apna", "workindia", "foundit", "justjob")
DOMAIN_HINTS = ("staffing", "recruit", "manpower", "placement", "headhunt", "jobs", "hrsolutions", "talent")

PATTERNS = {
    "first.last": lambda f, l: f"{f}.{l}" if l else "",
    "first": lambda f, l: f,
    "flast": lambda f, l: f"{f[0]}{l}" if l else "",
    "firstl": lambda f, l: f"{f}{l[0]}" if l else "",
    "first_last": lambda f, l: f"{f}_{l}" if l else "",
    "firstlast": lambda f, l: f"{f}{l}" if l else "",
    "last.first": lambda f, l: f"{l}.{f}" if l else "",
    "last": lambda f, l: l,
}
COMMON_ORDER = ("first.last", "first", "flast")          # when the company's own format is unknown
VERIFY_STATUS = {"valid": "confirmed", "risky": "risky", "invalid": "rejected"}

TRUST_LABELS = {
    "confirmed": "Confirmed: a verifier says this mailbox exists",
    "published": "Published: found on a company page or source, mailbox not checked",
    "guess_risky": "Guess: the mailbox looks risky, do not rely on it",
    "guess_mx": "Guess: the company's mail server exists, the mailbox is unconfirmed",
    "guess_rejected": "No valid address: every guess was rejected",
    "none": "No email yet",
}


def _clean(text: str) -> str:
    return re.sub(r"[^a-z]", "", (text or "").lower())


# ---------- step 2: who posted it ----------
def classify_poster(company: str = "", domain: str = "", title: str = "") -> dict:
    """{"kind": employer|agency|unknown, "reason"}. An agency is flagged by name, domain or a known job-board brand."""
    name = (company or "").strip()
    low = f"{name} {title}".lower()
    host = (domain or "").lower()
    for brand in KNOWN_AGENCIES:
        if brand in low or brand.replace(" ", "") in host:
            return {"kind": "agency", "reason": f"known staffing or job-board brand ({brand})"}
    match = AGENCY_WORDS.search(name)
    if match:
        return {"kind": "agency", "reason": f"company name says \"{match.group(0).lower()}\""}
    if any(hint in host.split(".")[0] for hint in DOMAIN_HINTS):
        return {"kind": "agency", "reason": "website name looks like a staffing or jobs site"}
    if name and host:
        return {"kind": "employer", "reason": "no staffing or recruiter wording found"}
    return {"kind": "unknown", "reason": "not enough to tell"}


# ---------- step 4: email format ----------
def which_pattern(first: str, last: str, local: str) -> Optional[str]:
    f, l = _clean(first), _clean(last)
    if not f or not local:
        return None
    for pid, build in PATTERNS.items():
        if l or pid == "first":
            if build(f, l) == local.lower():
                return pid
    return None


def infer_pattern(leads: list, domain: str) -> Optional[str]:
    """The company's own format, read from published person-addresses at the same domain (role addresses ignored)."""
    seen = Counter()
    for lead in leads:
        email = normalise_email(lead.get("email"))
        if not email or not email.endswith("@" + domain) or is_role_address(email) or lead.get("email_is_role"):
            continue
        if str(lead.get("email_source", "")).startswith("guess:"):
            continue                                   # never learn a format from our own guesses
        first, last = split_name(lead.get("contact_name"))
        pid = which_pattern(first, last, email.split("@", 1)[0])
        if pid:
            seen[pid] += 1
    return seen.most_common(1)[0][0] if seen else None


def guess_emails(first: str, last: str, domain: str, preferred: Optional[str] = None, limit: int = 3) -> list:
    f, l = _clean(first), _clean(last)
    if not f or not domain:
        return []
    order = [preferred] if preferred else list(COMMON_ORDER)
    out, seen = [], set()
    for pid in order:
        local = PATTERNS[pid](f, l)
        if not local or local in seen or (not l and pid != "first"):
            continue
        seen.add(local)
        out.append({"email": f"{local}@{domain}", "pattern": pid,
                    "basis": "the company's own format" if preferred else "a common format"})
    return out[:limit]


# ---------- trust ----------
def trust(lead: dict) -> str:
    check = lead.get("email_check") or {}
    if lead.get("email"):
        if check.get("status") == "valid":
            return "confirmed"
        return "published"
    status = lead.get("email_guess_status")
    if lead.get("email_guess"):
        return "guess_risky" if status == "risky" else "guess_mx"
    return "guess_rejected" if status == "rejected" else "none"


# ---------- the engine ----------
def _verifier_run(store, transports: dict, max_credits: int, now):
    ids = [s.id for s in registry.SOURCES]
    specs = registry.plan("verify_email", ids)
    campaign = {"enrich": {"extra": [s.id for s in specs], "extra_params": {}}, "rules": {"credit_cap": max_credits}}
    return waterfall.Run(store, campaign, transports, {}, [], specs, now)


def candidates(store, max_leads: int = 50) -> list:
    """Leads that need an email guess checked: a named decision maker at a real employer's domain, no email yet.
    [{"lead_id", "company", "contact_name", "guesses": [{"email", "pattern", "basis"}]}]. Used by checkers that run elsewhere."""
    leads = [l for l in store.read("leads") if not l.get("is_test")]
    queue = sorted(leads, key=lambda l: (-(l.get("open_roles") or 0), -(l.get("max_job_age_days") or 0)))
    out = []
    for lead in queue:
        if len(out) >= max_leads:
            break
        if classify_poster(lead.get("company", ""), lead.get("domain") or "", lead.get("title") or "")["kind"] == "agency":
            continue
        if lead.get("email") or lead.get("email_guess") or lead.get("email_guess_status") == "rejected":
            continue
        domain = valid_domain(lead.get("domain")) or ""
        first, last = split_name(lead.get("contact_name"))
        if not (first and domain):
            continue
        guesses = guess_emails(first, last, domain, infer_pattern([l for l in leads if l["id"] != lead["id"]], domain))
        if guesses:
            out.append({"lead_id": lead["id"], "company": lead.get("company", ""), "contact_name": lead.get("contact_name", ""), "guesses": guesses})
    return out


class _Answers:
    """Verifier stand-in that replays checks done elsewhere (status words of app/smtp_probe.py)."""
    MAP = {"confirmed": "valid", "rejected": "invalid", "catch_all": "risky"}

    def __init__(self, checks: dict):
        self.checks = checks

    def check(self, email: str) -> dict:
        status = self.MAP.get(self.checks.get(email, ""), "unknown")
        return {"status": status, "provider": "smtp_probe (run on your own machine)", "reason": self.checks.get(email, "not checked")}


def apply_checks(store, results: list) -> dict:
    """Save mailbox checks done elsewhere: [{"lead_id", "checks": [{"email", "status"}]}]. Same rules as the engine."""
    report = {"applied": 0, "confirmed": 0, "kept_as_guess": 0, "rejected": 0, "ignored": 0}
    leads = {l["id"]: l for l in store.read("leads")}
    patches = {}
    for item in results:
        lead = leads.get(item.get("lead_id"))
        checks = {str(c.get("email", "")).lower(): str(c.get("status", "")) for c in item.get("checks") or []}
        if not lead or lead.get("email") or not checks:
            report["ignored"] += 1
            continue
        domain = valid_domain(lead.get("domain")) or ""
        first, last = split_name(lead.get("contact_name"))
        pattern = infer_pattern([l for l in leads.values() if l["id"] != lead["id"]], domain)
        guesses = [g for g in guess_emails(first, last, domain, pattern) if g["email"] in checks]
        if not guesses:
            report["ignored"] += 1
            continue
        patches[lead["id"]] = {"set": _verify_guesses(_Answers(checks), guesses, report)}
        report["applied"] += 1
    store.patch_leads(patches)
    return report


def run_engine(store, transports: dict, now, max_leads: int = 50, max_credits: int = 30, dry_run: bool = False) -> dict:
    """Classify the posters, then build and verify email guesses for decision makers who have no email."""
    leads = [l for l in store.read("leads") if not l.get("is_test")]
    report = {"checked": 0, "agencies": 0, "employers": 0, "guessed": 0, "confirmed": 0, "kept_as_guess": 0, "rejected": 0,
              "skipped_no_name_or_domain": 0, "credits_used": 0, "errors": []}
    run = None if dry_run else _verifier_run(store, transports, max_credits, now)
    queue = sorted(leads, key=lambda l: (-(l.get("open_roles") or 0), -(l.get("max_job_age_days") or 0)))[:max_leads]
    patches = {}
    for lead in queue:
        report["checked"] += 1
        poster = classify_poster(lead.get("company", ""), lead.get("domain") or "", lead.get("title") or "")
        changes = {"poster_kind": poster["kind"], "poster_reason": poster["reason"]}
        report["agencies" if poster["kind"] == "agency" else "employers"] += 1
        domain = valid_domain(lead.get("domain")) or ""
        first, last = split_name(lead.get("contact_name"))
        needs_email = not lead.get("email") and not lead.get("email_guess") and lead.get("email_guess_status") != "rejected"
        if poster["kind"] != "agency" and needs_email:
            if not (first and domain):
                report["skipped_no_name_or_domain"] += 1
            else:
                pattern = infer_pattern([l for l in leads if l["id"] != lead["id"]], domain)
                guesses = guess_emails(first, last, domain, pattern)
                report["guessed"] += len(guesses)
                if guesses and not dry_run:
                    changes.update(_verify_guesses(run, guesses, report))
        patches[lead["id"]] = {"set": changes}
    if not dry_run:
        store.patch_leads(patches)
        report["credits_used"] = run.ctx.budget.spent
        for sid, message in run.errors.items():
            if "Not connected" not in message:
                report["errors"].append(f"{sid}: {message}")
        run.ctx.cache.flush()
    return report


def _verify_guesses(run, guesses: list, report: dict) -> dict:
    best = None
    for guess in guesses:
        verdict = run.check(guess["email"])
        status = verdict["status"]
        if status == "invalid":
            continue
        if status == "valid":
            report["confirmed"] += 1
            return {"email": guess["email"], "email_is_role": False,
                    "email_source": f"guess:{guess['pattern']}",
                    "email_check": {"email": guess["email"], "syntax_ok": True, "business": bool(business_domain(guess["email"])),
                                    "role": False, "provider": verdict.get("provider", ""), "status": "valid"},
                    "email_guess_status": "confirmed"}
        if best is None or (status == "unknown" and best[1] == "risky") or status == best[1]:
            best = best or (guess, status)
    if best:
        report["kept_as_guess"] += 1
        return {"email_guess": best[0]["email"], "email_guess_pattern": best[0]["pattern"],
                "email_guess_status": "risky" if best[1] == "risky" else "unconfirmed"}
    report["rejected"] += 1
    return {"email_guess_status": "rejected"}


# ---------- step 5: the sheet ----------
SHEET_COLUMNS = ["company", "website", "open_roles", "oldest_open_role_days", "posted_by", "posted_by_reason", "decision_maker",
                 "title", "linkedin", "email", "guessed_email", "email_trust", "email_trust_detail", "phone", "source", "score"]


def sheet_rows(store) -> list:
    rows = []
    for lead in sorted((l for l in store.read("leads") if not l.get("is_test")),
                       key=lambda l: (-(l.get("open_roles") or 0), -(l.get("max_job_age_days") or 0))):
        level = trust(lead)
        rows.append({"company": lead.get("company"), "website": lead.get("website") or lead.get("domain"),
                     "open_roles": lead.get("open_roles") or 0, "oldest_open_role_days": lead.get("max_job_age_days") or 0,
                     "posted_by": lead.get("poster_kind") or "not checked", "posted_by_reason": lead.get("poster_reason") or "",
                     "decision_maker": lead.get("contact_name"), "title": lead.get("title"), "linkedin": lead.get("linkedin_url"),
                     "email": lead.get("email") or "", "guessed_email": lead.get("email_guess") or "",
                     "email_trust": level, "email_trust_detail": TRUST_LABELS[level],
                     "phone": lead.get("phone") or lead.get("company_phone") or "", "source": lead.get("source"),
                     "score": lead.get("score")})
    return rows
