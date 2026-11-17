"""Domain trust signals on every lead: cheap, automatic, from public DNS and RDAP only (no website fetch, no keys).

    lead.domain_signals = {mx: <provider>|"own / other"|"none"|"unknown", spf: "present"|"missing"|"unknown",
                           dmarc: "none"|"quarantine"|"reject"|"missing"|"unknown", age_days: int|None,
                           registered_at: "YYYY-MM-DD"|None, checked_at}

Built from app/company_health.py's own parsers (email_setup: MX provider, SPF, DMARC policy; domain_age: the RDAP
registration date), so the drawer's Company health report and this compact row always agree. A lookup that failed
(timeout, server error, no RDAP data) is "unknown" / None: never guessed, never counted by a filter.

Why it matters (shown in the drawer and the help): a domain registered less than a year ago, or one with no SPF / DMARC
mail records, is more often a fake, inactive or brand-new company, and mail to it bounces more often.

Runs: a free step in Find & enrich ("domain_signals", on by default), the owner's batch on the Buying signals page
(Batch, at most BATCH_MAX domains, in the background), and the monthly website re-crawl (app/recrawl.py) for the domains
it reads. Results are cached per domain for CACHE_DAYS in the "domain_signals_cache" collection (at most MAX_CACHED rows).
LIVE=False (tests) means no real DNS / RDAP unless the caller passes its own resolvers.
"""
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from . import company_health as CH
from .lead_model import now_iso

CACHE_KEY = "domain_signals_cache"
CACHE_DAYS = 30
MAX_CACHED = 2000
BATCH_MAX = 500
YOUNG_DAYS = 365
LIVE = True
CLOSED_STAGES = ("unsubscribed", "disqualified")
WHY = ("Very new domains, or domains with no SPF / DMARC mail records, more often belong to a fake, inactive or brand-new "
       "company, and email to them bounces more often.")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ default resolvers (a failure raises -> "unknown")
def dns_mx(name: str) -> list:
    import dns.resolver
    try:
        answers = dns.resolver.resolve(name, "MX", lifetime=5)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []                                   # the domain answers, and has no such record
    return sorted(str(r.exchange).rstrip(".").lower() for r in answers)


def dns_txt(name: str) -> list:
    import dns.resolver
    try:
        answers = dns.resolver.resolve(name, "TXT", lifetime=5)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    return [b"".join(r.strings).decode("utf-8", "replace") for r in answers]


def _offline(*_a):
    raise OSError("network lookups are off")


def resolvers() -> dict:
    return {"mx": dns_mx, "txt": dns_txt, "get": CH.http_get} if LIVE else {"mx": _offline, "txt": _offline, "get": _offline}


# ------------------------------------------------------------------ one domain
def check(domain: str, mx: Optional[Callable] = None, txt: Optional[Callable] = None, get: Optional[Callable] = None,
          now: Optional[datetime] = None) -> dict:
    """The signals for one domain. Never raises."""
    now, base = now or now_utc(), resolvers()
    mx, txt, get = mx or base["mx"], txt or base["txt"], get or base["get"]
    failed = set()

    def safe_mx(name):
        try:
            return list(mx(name) or [])
        except Exception:                               # noqa: BLE001 - timeouts, servers down: not known
            failed.add("mx")
            return []

    def safe_txt(name):
        try:
            return list(txt(name) or [])
        except Exception:                               # noqa: BLE001
            failed.add("dmarc" if name.startswith("_dmarc.") else "spf")
            return []
    mail = CH.email_setup(domain, safe_mx, safe_txt)
    out = {"mx": "unknown" if "mx" in failed else (mail["provider"] or "none"),
           "spf": "unknown" if "spf" in failed else ("present" if mail["spf"] else "missing"),
           "dmarc": "unknown" if "dmarc" in failed else (dmarc_word(mail["dmarc_policy"]) if mail["dmarc"] else "missing"),
           "age_days": None, "registered_at": None, "checked_at": now.isoformat(timespec="seconds")}
    try:
        age = CH.domain_age(domain, get)
        if age.get("ok"):
            registered = date.fromisoformat(age["registered"])
            out.update(registered_at=registered.isoformat(), age_days=max(0, (now.date() - registered).days))
    except Exception:                                   # noqa: BLE001 - an odd RDAP date: not known
        pass
    return out


def dmarc_word(policy: str) -> str:
    """A published DMARC record's policy; a record without a valid p= enforces nothing, like p=none."""
    return policy if policy in ("quarantine", "reject") else "none"


def known(signals: Optional[dict]) -> bool:
    s = signals or {}
    return any(s.get(k) not in (None, "", "unknown") for k in ("mx", "spf", "dmarc")) or s.get("age_days") is not None


def is_young(signals: Optional[dict]) -> bool:
    age = (signals or {}).get("age_days")
    return isinstance(age, int) and age < YOUNG_DAYS


def no_mail_auth(signals: Optional[dict]) -> bool:
    s = signals or {}
    return s.get("spf") == "missing" or s.get("dmarc") == "missing"


# ------------------------------------------------------------------ cache (its own collection, bounded)
def _rows(store) -> list:
    try:
        return store.read(CACHE_KEY)
    except KeyError:                                    # data files from before this collection existed
        return []


def cached(store, domain: str, now: Optional[datetime] = None) -> Optional[dict]:
    row = next((r for r in _rows(store) if r.get("d") == domain), None)
    try:
        fresh = (now or now_utc()) - datetime.fromisoformat(row["at"]) < timedelta(days=CACHE_DAYS)
    except (TypeError, KeyError, ValueError):
        return None
    return row.get("s") if fresh else None


def _remember(store, domain: str, signals: dict) -> None:
    row = {"d": domain, "at": signals["checked_at"], "s": signals}

    def keep(rows):
        return [*(r for r in rows if r.get("d") != domain), row][-MAX_CACHED:]
    try:
        store.update(CACHE_KEY, keep)
    except KeyError:
        store.write(CACHE_KEY, [row])


def lookup(store, domain: str, now: Optional[datetime] = None, **res) -> dict:
    """The cached signals when younger than CACHE_DAYS, else a fresh check (cached only when something was learnt)."""
    hit = cached(store, domain, now)
    if hit is not None:
        return hit
    signals = check(domain, now=now, **res)
    if known(signals):
        _remember(store, domain, signals)
    return signals


def apply(store, lead_ids, signals: dict) -> int:
    """Write the signals onto these leads (nothing is written when nothing is known). Returns how many were written."""
    if not known(signals):
        return 0
    n = 0
    for lead_id in lead_ids:
        store.update_lead(lead_id, lambda l: {**l, "domain_signals": dict(signals)})
        n += 1
    return n


def refresh_domain(store, domain: str, now: Optional[datetime] = None, **res) -> dict:
    """Check one domain (cache first) and write it onto every lead on that domain."""
    signals = lookup(store, domain, now, **res)
    ids = [l["id"] for l in store.read("leads") if l.get("domain") == domain]
    apply(store, ids, signals)
    return signals


# ------------------------------------------------------------------ owner's batch
def due(lead: dict, now: datetime) -> bool:
    if not lead.get("domain") or lead.get("stage") in CLOSED_STAGES:
        return False
    at = str((lead.get("domain_signals") or {}).get("checked_at") or "")
    return at < (now - timedelta(days=CACHE_DAYS)).isoformat(timespec="seconds")


def pick(leads: list, limit: int = BATCH_MAX, now: Optional[datetime] = None) -> list:
    """[(domain, [lead ids])] for up to `limit` domains (BATCH_MAX at most) with a lead not checked in CACHE_DAYS."""
    now = now or now_utc()
    by_domain: dict = {}
    for lead in leads:
        if due(lead, now):
            by_domain.setdefault(lead["domain"], []).append(lead["id"])
    return list(by_domain.items())[:max(1, min(int(limit), BATCH_MAX))]


class Batch:
    """One batch at a time, in a background thread, with progress."""

    def __init__(self, store, res: Optional[dict] = None):
        self.store, self.res, self._lock = store, res or {}, threading.Lock()
        self.state = {"status": "idle"}

    def start(self, work: list, background: bool = True) -> dict:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A domain check is already running. Wait for it to finish.")
        self.state = {"status": "running", "done": 0, "total": len(work), "young": 0, "no_mail_auth": 0, "unknown": 0,
                      "started_at": now_iso(), "finished_at": ""}
        if background:
            threading.Thread(target=self._run, args=(work,), daemon=True, name="domain-signals").start()
        else:
            self._run(work)
        return dict(self.state)

    def _run(self, work: list) -> None:
        try:
            for domain, ids in work:
                try:
                    signals = lookup(self.store, domain, **self.res)
                    apply(self.store, ids, signals)
                    self.state["young"] += is_young(signals)
                    self.state["no_mail_auth"] += no_mail_auth(signals)
                    self.state["unknown"] += not known(signals)
                except Exception:                       # noqa: BLE001 - one domain never stops the batch
                    self.state["unknown"] += 1
                self.state["done"] += 1
            self.state["status"] = "done"
        finally:
            self.state["finished_at"] = now_iso()
            if self.state["status"] == "running":
                self.state["status"] = "failed"
            self._lock.release()
