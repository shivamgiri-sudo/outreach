"""Email guesses for any lead with a name and a company website, from the company's own email format.

1. Learn: each company domain's format is read from addresses already in our data that are known to work: a lead's
   address a verifier called valid or that an email was delivered to, other contacts we delivered to, and verified
   people in the prospect database. Bounced addresses, shared inboxes (info@, hr@), free mail (gmail.com ...) and our
   own guesses are never learned from.
2. Guess: ranked candidates, the learned format first, then common defaults (app/email_patterns.py).
3. Check without sending: the built-in checker (domain takes mail, never bounced; app/email_checker.py), then the
   self-hosted mailbox probe (app/smtp_probe.py) only where port 25 works. The probe asks the mail server and hangs up.
4. Store: the best candidate as `email_guess` with `email_guess_status` and source "pattern:<format>", NEVER as `email`,
   so nothing is ever sent to a guess (the same rule as the hiring engine). Statuses:
     confirmed     the company's mail server said this mailbox exists (still a guess: nothing was sent)
     unconfirmed   the domain takes mail; the mailbox could not be checked
     catch_all     the mail server accepts every address, so the mailbox cannot be confirmed
     rejected      every candidate was refused, or the domain takes no mail (no email_guess is stored)
"""
from collections import defaultdict
from typing import Callable, Optional

from . import email_checker, email_domain
from . import email_patterns as EP
from .email_checker_data import PERSONAL
from .hygiene import FREE_MAIL_DOMAINS, is_role_address, normalise_email
from .scraper import outreach_ok
from .sources.builtin_verifier import is_role

GUESS_SOURCES = ("guess:", "pattern:")
GUESS_FIELDS = ("email_guess", "email_guess_status", "email_guess_source", "email_guess_pattern", "email_guess_reason")
TRUSTED_CHECKS = ("valid",)
PDB_LIMIT = 200


def learnable(email: str, history: dict) -> Optional[str]:
    """The address when it can teach a company format (a person's work address that has not bounced), else None."""
    addr = normalise_email(email)
    if not addr or addr in history.get("bounced", ()):
        return None
    domain = addr.rsplit("@", 1)[1]
    if domain in FREE_MAIL_DOMAINS or domain in PERSONAL or is_role_address(addr) or is_role(addr) or not outreach_ok(addr):
        return None
    return addr


class PatternBook:
    """Every company's learned formats for one run, built once from the leads and delivery history."""

    def __init__(self, store, history: Optional[dict] = None):
        self.store = store
        self.history = history if history is not None else email_checker.build_history(store)
        self.pairs = defaultdict(dict)                       # domain -> {address: name}
        self._learned = {}
        delivered = self.history.get("delivered", set())
        for lead in store.read("leads"):
            if lead.get("is_test"):
                continue
            trusted = (lead.get("email_check") or {}).get("status") in TRUSTED_CHECKS or normalise_email(lead.get("email")) in delivered
            if trusted and not lead.get("email_is_role") and not str(lead.get("email_source") or "").startswith(GUESS_SOURCES):
                self._add(lead.get("contact_name"), lead.get("email"))
            for other in lead.get("other_contacts") or []:
                if normalise_email(other.get("email")) in delivered:
                    self._add(other.get("contact_name"), other.get("email"))

    def _add(self, name, email) -> None:
        addr = learnable(email, self.history)
        if addr and str(name or "").strip():
            self.pairs[addr.rsplit("@", 1)[1]].setdefault(addr, str(name).strip())

    def _prospect_db(self, domain: str) -> None:
        try:
            from .prospect_repo import repo_for
            rows = repo_for(self.store).q("SELECT full_name, email FROM pdb_people WHERE company_domain = ? AND suppressed = 0 "
                                          "AND email_status IN ('verified', 'valid') LIMIT ?", (domain, PDB_LIMIT))
        except Exception:                                    # noqa: BLE001 - no prospect database here: learn from leads only
            return
        for row in rows:
            self._add(row.get("full_name"), row.get("email"))

    def learned(self, domain: str) -> list:
        """[{"pattern", "count", "share", "confidence"}] for one domain, most used first."""
        if domain not in self._learned:
            self._prospect_db(domain)
            self._learned[domain] = EP.learn((name, addr.split("@", 1)[0]) for addr, name in self.pairs.get(domain, {}).items())
        return self._learned[domain]


class Checker:
    """Checks candidates without sending: built-in rules and DNS first, then the mailbox probe when it can run."""

    def __init__(self, history: dict, dns: Optional[Callable] = None, smtp: Optional[Callable] = None):
        self.history, self.dns, self.smtp = history, dns or email_domain.status_of_domain, smtp

    def pick(self, cands: list) -> dict:
        """{"candidate"?, "status", "reason"} for the best of the ranked candidates."""
        domain = cands[0]["email"].rsplit("@", 1)[1]
        dns = self.dns(domain)
        if dns in ("no_domain", "no_mail"):
            return {"status": "rejected", "reason": f"the domain {domain} {'does not exist' if dns == 'no_domain' else 'takes no mail'}"}
        for cand in cands:
            base = email_checker.check(cand["email"], self.history, dns=lambda _d: dns)
            if base["status"] == "invalid":
                continue                                     # bounced before, or otherwise refused by the rules
            if self.smtp is None:
                why = ("the company's domain could not be looked up right now" if dns == "unknown"
                       else "the company's mail server exists; the mailbox was not checked")
                return {"candidate": cand, "status": "unconfirmed", "reason": why}
            verdict = self.smtp(cand["email"])
            if verdict == "confirmed":
                return {"candidate": cand, "status": "confirmed", "reason": "the mail server confirmed this mailbox (nothing was sent)"}
            if verdict == "rejected":
                continue
            if verdict == "catch_all":
                return {"candidate": cand, "status": "catch_all",
                        "reason": "the mail server accepts every address, so this mailbox cannot be confirmed"}
            return {"candidate": cand, "status": "unconfirmed", "reason": "the mail server did not give a clear answer"}
        return {"status": "rejected", "reason": "every likely address was refused"}


def make_checker(store, transports: Optional[dict] = None, history: Optional[dict] = None) -> Checker:
    """The checker for a run. The mailbox probe runs only when port 25 works and a sender address is set; tests pass
    `smtp_probe` / `dns` fakes in the transports, and with domain checks off (tests) no probe is ever opened."""
    transports = transports or {}
    smtp = transports.get("smtp_probe")
    if smtp is None and email_domain.CHECK:
        try:
            from .senders import email_ready
            smtp = email_checker.smtp_step((email_ready(store) or {}).get("from_email", ""))
        except Exception:                                    # noqa: BLE001 - no probe: guesses stay unconfirmed
            smtp = None
    return Checker(history if history is not None else email_checker.build_history(store), transports.get("dns"), smtp)


def guess(full_name: str, domain: str, book: PatternBook, checker: Checker) -> Optional[dict]:
    """Lead fields for the best guess (only email_guess*), or None when there is nothing to guess from."""
    cands = EP.candidates(full_name, domain, book.learned(domain))
    if not cands:
        return None
    verdict = checker.pick(cands)
    if "candidate" not in verdict:
        return {"email_guess_status": "rejected", "email_guess_reason": verdict["reason"]}
    cand = verdict["candidate"]
    return {"email_guess": cand["email"], "email_guess_status": verdict["status"], "email_guess_source": f"pattern:{cand['pattern']}",
            "email_guess_pattern": cand["pattern"], "email_guess_reason": f"{cand['pattern']}, {cand['basis']}; {verdict['reason']}"}
