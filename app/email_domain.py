"""Does the domain of an email address receive email at all? A DNS check (MX, else A/AAAA as mail servers fall back to
it), cached for CACHE_SECONDS. Used when a person types an email (edit / extra address) and before every send, so an
address on a domain that does not exist (a typo such as "tvalueserve.com") is caught before it bounces.

status(): "ok" | "no_domain" (the domain does not exist) | "no_mail" (exists, but has no mail server) | "unknown" (DNS
did not answer in time: never block on that). CHECK = False turns the lookups off (tests set it; no network there).
"""
import os
import time
from typing import Optional

CHECK = os.environ.get("OUTREACH_EMAIL_DOMAIN_CHECK", "1") != "0"     # 0 only on local test rigs with made-up domains
CACHE_SECONDS = 6 * 3600
TIMEOUT = 3.0
_CACHE: dict = {}


def _lookup(domain: str) -> str:
    import dns.exception
    import dns.resolver
    resolver = dns.resolver.Resolver()
    resolver.lifetime = TIMEOUT
    try:
        if any(str(r.exchange).strip(".") for r in resolver.resolve(domain, "MX")):
            return "ok"
        return "no_mail"                         # a "null MX" (RFC 7505): the domain says it takes no mail
    except dns.resolver.NXDOMAIN:
        return "no_domain"
    except dns.resolver.NoAnswer:
        pass
    except (dns.exception.Timeout, dns.resolver.NoNameservers, dns.exception.DNSException):
        return "unknown"
    for kind in ("A", "AAAA"):
        try:
            if resolver.resolve(domain, kind):
                return "ok"
        except dns.resolver.NXDOMAIN:
            return "no_domain"
        except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
            continue
        except dns.exception.DNSException:
            return "unknown"
    return "no_mail"


def status(email: str) -> str:
    if not CHECK:
        return "ok"
    domain = (email or "").rsplit("@", 1)[-1].strip().lower().rstrip(".")
    if "@" not in (email or "") or "." not in domain:
        return "unknown"                         # not an address at all: other checks refuse it
    return status_of_domain(domain)


def status_of_domain(domain: str) -> str:
    if not CHECK:
        return "ok"
    hit = _CACHE.get(domain)
    if hit and time.time() - hit[1] < CACHE_SECONDS:
        return hit[0]
    result = _lookup(domain)
    if result != "unknown":
        _CACHE[domain] = (result, time.time())
    return result


def problem(email: str) -> Optional[str]:
    """A plain sentence when the address cannot receive email, else None."""
    domain = (email or "").rsplit("@", 1)[-1].lower()
    s = status(email)
    if s == "no_domain":
        return f"the domain {domain} does not exist (check the spelling), so this address cannot receive email"
    if s == "no_mail":
        return f"the domain {domain} has no mail server, so this address cannot receive email"
    return None
