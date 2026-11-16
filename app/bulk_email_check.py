"""Bulk email checker: a pasted list or a CSV of up to MAX_ADDRESSES addresses, each run through the built-in checker
(app/email_checker.check) and compared with our own data (leads, bounces, do-not-contact list) of the caller's workspaces.

Not tied to leads: nothing is saved on a lead. No new outbound mail-server (SMTP) traffic: a mailbox verdict is only
reused when an earlier email check of one of our leads already holds one (catch-all / confirmed / rejected); the DNS
lookups go through app/email_domain (cached for hours) and each domain is looked up once per run.

Statuses (one per row, first match wins):
  invalid_syntax  not an email address
  duplicate       the same address appeared earlier in this list
  suppressed      on our do-not-contact / erased list: never contact
  disposable      a throwaway inbox service
  typo            looks like a typo of a popular domain (suggest holds the fix)
  bounced         an email to it bounced before
  no_mail_server  the domain does not exist or takes no mail
  unknown         the domain could not be looked up right now (never treated as valid)
  catch_all       the mail server accepts every address, so the mailbox cannot be confirmed (never treated as valid)
  risky           shared mailbox (info@, hr@) or a domain that keeps bouncing
  valid           passed every check
Flags (any number): role, free, already_lead, suppressed, bounced, typo.

Runs work in a background thread (Runner.start) with progress; Runner.run_now does the same on the calling thread.
"""
import csv
import io
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Optional

from . import email_checker, email_domain
from .email_checker_data import DISPOSABLE, PERSONAL
from .hygiene import FREE_MAIL_DOMAINS, csv_safe, is_role_address, normalise_email
from .privacy import hashes_for

MAX_ADDRESSES = 10_000
MAX_TEXT_CHARS = 5_000_000
SCREEN_ROWS = 200
DNS_WORKERS = 8
MAX_RUNNING = 2                    # runs working at the same time, across all logins
KEEP_SECONDS = 6 * 3600            # finished runs (and their rows) are kept in memory this long
MAX_KEPT = 30
STATUSES = ("valid", "risky", "catch_all", "unknown", "no_mail_server", "bounced", "typo", "disposable", "suppressed",
            "duplicate", "invalid_syntax")
FLAGS = ("role", "free", "already_lead", "suppressed", "bounced", "typo")
CSV_COLUMNS = ("email", "status", "flags", "suggestion", "reason")
_SPLIT = re.compile(r"[\s,;]+")
_EMAILISH = re.compile(r"[^\s,;<>\"'()\[\]]+@[^\s,;<>\"'()\[\]]+")


class Busy(Exception):
    """Too many runs are working right now."""


class TooMany(ValueError):
    """The list holds more than MAX_ADDRESSES addresses."""


def now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- reading the input ----------
def _csv_addresses(text: str) -> list:
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    col = next((i for i, h in enumerate(header) if h in ("email", "e-mail", "email address", "mail", "work email")), None)
    if col is not None:
        return [r[col].strip() for r in rows[1:] if len(r) > col and r[col].strip()]
    out = []
    for r in rows:
        cell = next((c.strip() for c in r if "@" in c), None)
        if cell:
            out.append(cell)
    return out


def read_addresses(text: str) -> list:
    """The addresses in a pasted list (one per line, or separated by commas / semicolons / spaces) or a CSV (the
    'email' column, else the first cell holding an @ in each row). Raises TooMany above MAX_ADDRESSES."""
    text = (text or "").replace("\r\n", "\n").replace("﻿", "")
    first = text.split("\n", 1)[0]
    if "," in first and ("@" not in first or "email" in first.lower()) and "\n" in text.strip():
        found = _csv_addresses(text)
    else:
        found = [p.strip().strip("<>\"'") for p in _SPLIT.split(text) if p.strip()]
        found = [p for p in found if "@" in p or not _EMAILISH.search(text)]   # drop stray words when the paste has addresses
    if len(found) > MAX_ADDRESSES:
        raise TooMany(f"That list has {len(found):,} addresses. Check at most {MAX_ADDRESSES:,} at a time: split the file.")
    return [a[:320] for a in found]


# ---------- our own data, cut to the caller's workspaces ----------
class _Rows:
    """A store look-alike for email_checker.build_history that only shows some rows."""

    def __init__(self, tables: dict):
        self.tables = tables

    def read(self, name: str):
        return self.tables.get(name, [])


def known_data(store, scoped_leads: list) -> dict:
    """Lead addresses, bounce/delivery history and earlier mailbox verdicts of the leads the caller may see, plus the
    do-not-contact list. Bounces of other workspaces' leads are left out."""
    emails, ids, verdicts = set(), set(), {}
    for lead in scoped_leads:
        ids.add(lead.get("id"))
        for e in [lead.get("email"), *[c.get("email") for c in lead.get("other_contacts") or [] if isinstance(c, dict)]]:
            e = normalise_email(e)
            if e:
                emails.add(e)
        check = lead.get("email_check") or {}
        mail = normalise_email(check.get("email") or lead.get("email"))
        text = " ".join(check.get("reasons") or []).lower()
        if mail and "catch-all" in text:
            verdicts[mail] = "catch_all"
        elif mail and "confirmed this mailbox" in text:
            verdicts[mail] = "confirmed"
        elif mail and "mailbox does not exist" in text:
            verdicts[mail] = "rejected"
    events = [e for e in store.read("delivery_events") if normalise_email(e.get("email")) in emails]
    outbox = [m for m in store.read("outbox") if m.get("lead_id") in ids]
    history = email_checker.build_history(_Rows({"delivery_events": events, "outbox": outbox}))
    return {"leads": emails, "history": history, "verdicts": verdicts,
            "suppressed": {str(e).strip().lower() for e in store.read("suppressed")},
            "erased": set(store.read("suppressed_hashes"))}


# ---------- one list ----------
def _empty_known() -> dict:
    return {"leads": set(), "history": None, "verdicts": {}, "suppressed": set(), "erased": set()}


def check_list(addresses: list, known: Optional[dict] = None, dns: Optional[Callable] = None,
               progress: Optional[Callable] = None) -> list:
    """Rows for every address, in the order given. `dns(domain)` as email_checker.check takes it."""
    known = known or _empty_known()
    history = known.get("history") or {"bounced": set(), "delivered": set(), "domains": {}}
    dns = dns or email_domain.status_of_domain
    domains = sorted({a.rsplit("@", 1)[1] for a in (normalise_email(x) for x in addresses) if a
                      and a.rsplit("@", 1)[1] not in DISPOSABLE})
    answers = {}
    total = len(domains) + len(addresses)

    def look(domain):
        try:
            answers[domain] = dns(domain)
        except Exception:
            answers[domain] = "unknown"
    if domains:
        with ThreadPoolExecutor(max_workers=min(DNS_WORKERS, len(domains))) as pool:
            for i, _ in enumerate(pool.map(look, domains), 1):
                if progress and (i % 25 == 0 or i == len(domains)):
                    progress(i, total)
    cached_dns = lambda d: answers.get(d) or dns(d)
    verdicts = known.get("verdicts") or {}
    seen, rows = set(), []
    for n, raw in enumerate(addresses, 1):
        rows.append(_one(raw, seen, known, history, cached_dns, verdicts))
        if progress and (n % 200 == 0 or n == len(addresses)):
            progress(len(domains) + n, total)
    return rows


def _one(raw: str, seen: set, known: dict, history: dict, dns: Callable, verdicts: dict) -> dict:
    addr = normalise_email(raw)
    row = {"email": raw, "status": "", "flags": [], "suggest": "", "reason": ""}
    if not addr:
        return {**row, "status": "invalid_syntax", "reason": "this is not a valid email address"}
    domain = addr.rsplit("@", 1)[1]
    flags = []
    if is_role_address(addr):
        flags.append("role")
    if domain in PERSONAL or domain in FREE_MAIL_DOMAINS:
        flags.append("free")
    if addr in known["leads"]:
        flags.append("already_lead")
    blocked = addr in known["suppressed"] or bool(hashes_for(addr) & known["erased"])
    if blocked:
        flags.append("suppressed")
    if addr in history["bounced"]:
        flags.append("bounced")
    if addr in seen:
        return {**row, "status": "duplicate", "flags": flags, "reason": "this address is already earlier in the list"}
    seen.add(addr)
    dns_seen = {}

    def dns_watch(d):
        dns_seen["status"] = dns(d)
        return dns_seen["status"]
    smtp = (lambda _e: verdicts.get(addr)) if verdicts.get(addr) else None
    verdict = email_checker.check(addr, history, dns=dns_watch, smtp=smtp, company_lead=False)
    reasons = list(verdict.get("reasons") or [])
    if verdict.get("suggest"):
        flags.append("typo")
        row["suggest"] = verdict["suggest"]
    if blocked:
        status = "suppressed"
        reasons.insert(0, "on your do-not-contact list: never email this address")
    elif domain in DISPOSABLE:
        status = "disposable"
    elif verdict.get("suggest"):
        status = "typo"
    elif "bounced" in flags:
        status = "bounced"
    elif dns_seen.get("status") in ("no_domain", "no_mail"):
        status = "no_mail_server"
    elif verdict["status"] == "invalid":
        status = "invalid_syntax" if not reasons else "no_mail_server" if "mailbox does not exist" in " ".join(reasons) else "risky"
    elif verdict["status"] == "unverified" or dns_seen.get("status") == "unknown":
        status = "unknown"
    elif verdicts.get(addr) == "catch_all":
        status = "catch_all"              # an earlier mailbox check said catch-all: it stays unresolved, never "valid"
    elif verdict["status"] == "risky":
        status = "risky"
    else:
        status = "valid"
    if "already_lead" in flags:
        reasons.append("already one of your leads")
    return {**row, "status": status, "flags": flags, "reason": "; ".join(dict.fromkeys(reasons))}


def summary(rows: list) -> dict:
    counts = {s: 0 for s in STATUSES}
    flags = {f: 0 for f in FLAGS}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        for f in r["flags"]:
            flags[f] = flags.get(f, 0) + 1
    return {"total": len(rows), "statuses": counts, "flags": flags}


def to_csv(rows: list) -> str:
    """Every row, each cell neutralised so a spreadsheet never runs it as a formula."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(CSV_COLUMNS)
    for r in rows:
        w.writerow([csv_safe(r["email"]), csv_safe(r["status"]), csv_safe(" ".join(r["flags"])), csv_safe(r["suggest"]),
                    csv_safe(r["reason"])])
    return out.getvalue()


def valid_addresses(rows: list) -> list:
    """Addresses worth adding as leads: valid, not already a lead, not suppressed."""
    return [normalise_email(r["email"]) for r in rows
            if r["status"] == "valid" and "already_lead" not in r["flags"] and "suppressed" not in r["flags"]]


# ---------- background runs ----------
class Runner:
    """Runs in background threads (at most MAX_RUNNING at once), kept in memory for KEEP_SECONDS. A run belongs to the
    login that started it and to that request's workspace scope; only that login reads it back."""

    def __init__(self, clock: Callable = time.time):
        self._lock, self._runs, self.clock = threading.Lock(), {}, clock

    def _prune(self) -> None:
        cutoff = self.clock() - KEEP_SECONDS
        done = [k for k, r in self._runs.items() if r["status"] != "running" and r["_at"] < cutoff]
        for k in done:
            self._runs.pop(k, None)
        extra = sorted((r["_at"], k) for k, r in self._runs.items() if r["status"] != "running")
        for _, k in extra[:max(0, len(self._runs) - MAX_KEPT)]:
            self._runs.pop(k, None)

    def _new(self, owner: str, addresses: list) -> dict:
        with self._lock:
            self._prune()
            if sum(1 for r in self._runs.values() if r["status"] == "running") >= MAX_RUNNING:
                raise Busy("Other email checks are working right now. Try again in a minute.")
            run = {"id": uuid.uuid4().hex[:12], "owner": owner, "status": "running", "done": 0, "total": len(addresses) or 1,
                   "started_at": now_text(), "finished_at": "", "error": "", "rows": [], "_at": self.clock()}
            self._runs[run["id"]] = run
        return run

    def _work(self, run: dict, addresses: list, known: dict, dns: Optional[Callable]) -> None:
        def progress(done, total):
            run.update(done=done, total=total)
        try:
            rows = check_list(addresses, known, dns, progress)
            run.update(rows=rows, status="done", done=run["total"], finished_at=now_text(), _at=self.clock())
        except Exception as exc:                 # the page must always get an answer
            run.update(status="failed", error=f"The check stopped because of an unexpected problem: {str(exc)[:200]}",
                       finished_at=now_text(), _at=self.clock())

    def start(self, owner: str, addresses: list, known: dict, dns: Optional[Callable] = None) -> dict:
        run = self._new(owner, addresses)
        threading.Thread(target=self._work, args=(run, addresses, known, dns), daemon=True).start()
        return self.view(run)

    def run_now(self, owner: str, addresses: list, known: dict, dns: Optional[Callable] = None) -> dict:
        run = self._new(owner, addresses)
        self._work(run, addresses, known, dns)
        return self.view(run)

    def get(self, run_id: str, owner: str) -> Optional[dict]:
        with self._lock:
            run = self._runs.get(run_id)
        return run if run is not None and run["owner"] == owner else None

    @staticmethod
    def view(run: dict) -> dict:
        out = {k: v for k, v in run.items() if k not in ("rows", "owner", "_at")}
        if run["status"] == "done":
            out.update(summary=summary(run["rows"]), rows=run["rows"][:SCREEN_ROWS], shown=min(SCREEN_ROWS, len(run["rows"])),
                       valid_new=len(valid_addresses(run["rows"])))
        return out
