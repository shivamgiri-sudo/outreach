"""Built-in email checker: decides valid / invalid / risky / unverified for one address from rules, DNS and our own history.

No paid verifier and no mailbox probe is needed (the server's port 25 is blocked). Layers, cheapest first:
  1 syntax (hygiene.normalise_email)         -> invalid
  2 throwaway-inbox domains (embedded list)  -> invalid
  3 typo of a popular domain (gmial.com)     -> invalid, with email_check['suggest']
  4 this address bounced before              -> invalid
  5 the domain's mail records (app/email_domain: no_domain / no_mail -> invalid; unknown -> unverified, never blocks)
  6 role address (info@, hr@)                -> risky, unless a mail to it was delivered before
  7 company domain with >=3 bounces and no delivery in 90 days -> risky
  8 optional SMTP RCPT step (app/smtp_probe), only when a connect test to port 25 works; never on the request thread
Good history (>=2 delivered, no bounce on the domain) only adds confidence; it never turns a bad layer into valid.

check() is a pure function: DNS and the SMTP step are passed in, so tests need no network.
Result: {status, provider:'builtin', reasons:[plain words], suggest?, confidence?}.
"""
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import email_domain
from .email_checker_data import DISPOSABLE, KNOWN_TYPOS, PERSONAL, POPULAR
from .hygiene import is_role_address, normalise_email

PROVIDER = "builtin"
HISTORY_DAYS = 90
DOMAIN_BOUNCES_RISKY = 3
DOMAIN_DELIVERED_GOOD = 2
SMTP_PORT_CACHE_SECONDS = 3600
_ORDER = {"valid": 0, "unverified": 0, "risky": 1, "invalid": 2}


def edit_distance(a: str, b: str, cap: int = 3) -> int:
    """Levenshtein distance, giving up (returns cap) when it is clearly larger."""
    if abs(len(a) - len(b)) >= cap:
        return cap
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) >= cap:
            return cap
        prev = cur
    return min(prev[-1], cap)


def typo_of(domain: str) -> Optional[str]:
    """The popular domain this one is probably a typo of, else None."""
    if domain in POPULAR:
        return None
    if domain in KNOWN_TYPOS:
        return KNOWN_TYPOS[domain]
    best = None
    for good in POPULAR:
        if len(good) < 9:                                  # short names (me.com, msn.com) sit one letter from real companies
            continue
        limit = 2 if len(good) >= 11 and domain.split(".")[-1] == good.split(".")[-1] else 1
        d = edit_distance(domain, good, limit + 1)
        if d <= limit and (best is None or d < best[0]):
            best = (d, good)
    return best[1] if best else None


def build_history(store, now: Optional[datetime] = None) -> dict:
    """{'bounced': {addr}, 'delivered': {addr}, 'domains': {domain: {'bounces', 'delivered'}}} from delivery events and the outbox.
    Addresses use all time; domain counts use the last HISTORY_DAYS days."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=HISTORY_DAYS)).isoformat(timespec="seconds")
    out = {"bounced": set(), "delivered": set(), "domains": {}}

    def add(email, kind, at):
        email = str(email or "").strip().lower()
        if "@" not in email:
            return
        bucket = "bounced" if kind == "bounce" else "delivered"
        out[bucket].add(email)
        if (at or "") >= since:
            row = out["domains"].setdefault(email.rsplit("@", 1)[1], {"bounces": 0, "delivered": 0})
            row["bounces" if kind == "bounce" else "delivered"] += 1

    seen = set()
    for e in store.read("delivery_events"):
        if e.get("type") in ("bounce", "delivered"):
            add(e.get("email"), e["type"], e.get("at"))
            if e.get("provider_id"):
                seen.add(e["provider_id"])
    for m in store.read("outbox"):                      # a delivered mail whose provider event was not kept as an event
        if m.get("delivered_at") and m.get("provider_id") not in seen:
            add(m.get("to"), "delivered", m["delivered_at"])
    return out


def check(email: str, history: Optional[dict] = None, dns: Optional[Callable] = None, smtp: Optional[Callable] = None,
          company_lead: bool = True) -> dict:
    """One address. `dns(domain)` returns 'ok' | 'no_domain' | 'no_mail' | 'unknown'; `smtp(email)` returns an smtp_probe status or None."""
    history = history or {"bounced": set(), "delivered": set(), "domains": {}}
    dns = dns or email_domain.status_of_domain
    reasons, status, out = [], "valid", {"provider": PROVIDER}

    def worse(new: str) -> None:
        nonlocal status
        if _ORDER[new] > _ORDER[status]:
            status = new

    addr = normalise_email(email)
    if not addr:
        return {**out, "status": "invalid", "reasons": ["this is not a valid email address"]}
    domain = addr.rsplit("@", 1)[1]
    domain_history = history["domains"].get(domain, {"bounces": 0, "delivered": 0})
    delivered_before = addr in history["delivered"]

    if domain in DISPOSABLE:
        return {**out, "status": "invalid", "reasons": ["a throwaway inbox service: nobody reads this address"]}
    fix = typo_of(domain) if not delivered_before else None
    if fix:
        return {**out, "status": "invalid", "reasons": [f"looks like a typo of {fix}"], "suggest": f"{addr.split('@')[0]}@{fix}"}
    if addr in history["bounced"]:
        return {**out, "status": "invalid", "reasons": ["an email to this address bounced before"]}

    dns_status = dns(domain)
    if dns_status == "no_domain":
        return {**out, "status": "invalid", "reasons": [f"the domain {domain} does not exist"]}
    if dns_status == "no_mail":
        return {**out, "status": "invalid", "reasons": [f"the domain {domain} has no mail server"]}

    if is_role_address(addr) and not delivered_before:
        worse("risky")
        reasons.append("shared mailbox (info@, hr@ and similar): bounces and complaints are more common")
    if domain not in PERSONAL:
        if domain_history["bounces"] >= DOMAIN_BOUNCES_RISKY and domain_history["delivered"] == 0:
            worse("risky")
            reasons.append(f"this company's domain bounced {domain_history['bounces']} times and delivered none in {HISTORY_DAYS} days")
        elif domain_history["delivered"] >= DOMAIN_DELIVERED_GOOD and domain_history["bounces"] == 0:
            reasons.append(f"this domain delivered {domain_history['delivered']} emails before")
            out["confidence"] = "high"
    elif company_lead:
        reasons.append("personal mailbox")
    if delivered_before:
        reasons.append("an email to this address was delivered before")
        out["confidence"] = "high"

    if smtp is not None and status != "invalid":
        verdict = smtp(addr)
        if verdict == "rejected":
            return {**out, "status": "invalid", "reasons": [*reasons, "the mail server says this mailbox does not exist"]}
        if verdict == "confirmed":
            reasons.append("the mail server confirmed this mailbox")
            out["confidence"] = "high"
        elif verdict == "catch_all":
            reasons.append("the mail server accepts every address (catch-all), so this mailbox cannot be confirmed")

    if dns_status == "unknown" and status == "valid":
        return {**out, "status": "unverified", "reasons": [*reasons, "the domain could not be looked up right now; it will be tried again"]}
    return {**out, "status": status, "reasons": reasons}


# ---------- the optional SMTP step: only where outbound port 25 works, never on a request thread ----------
_PORT = {"at": 0.0, "open": False}
_PORT_LOCK = threading.Lock()


def port_25_usable(open_test: Optional[Callable] = None, now: Callable = time.time) -> bool:
    """A quick connect test to port 25, cached for an hour. False on any error."""
    with _PORT_LOCK:
        if now() - _PORT["at"] < SMTP_PORT_CACHE_SECONDS and _PORT["at"]:
            return _PORT["open"]
    try:
        if open_test is None:
            from .smtp_probe import port_25_open
            open_test = lambda: port_25_open(timeout=3)
        result = bool(open_test())
    except Exception:
        result = False
    with _PORT_LOCK:
        _PORT.update(at=now(), open=result)
    return result


def smtp_step(from_email: str, open_test: Optional[Callable] = None) -> Optional[Callable]:
    """smtp(email) -> probe status, or None when port 25 is closed or no sender address is set (the step is then skipped)."""
    if not from_email or "@" not in from_email or not port_25_usable(open_test):
        return None
    from .smtp_probe import Prober
    domain = from_email.rsplit("@", 1)[1]
    try:
        prober = Prober(helo=domain, mail_from=from_email, max_per_run=100)
    except ValueError:
        return None

    def probe(email: str) -> Optional[str]:
        try:
            return prober.check(email).get("status")
        except Exception:
            return None
    return probe
