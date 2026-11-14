"""Does the sending domain have the DNS records that keep mail out of spam? MX, SPF, DKIM, DMARC and the return-path note.

The domain is the one of the connected from-address (senders.email_ready; the API key is never read into the result).
Each check is {name, status: ok|warn|fail|unknown, found, advice}. Where this code cannot be sure (providers differ),
the advice says "check with your provider". Results are cached for 10 minutes. DNS is injectable:
a resolver is a function resolve(name, rdtype) -> list of record texts (TXT strings joined, MX as "10 host", CNAME as the target);
it returns [] when the name has no such record and raises OSError when DNS did not answer (the check is then 'unknown').
"""
import re
import time
from typing import Callable, Optional

CACHE_SECONDS = 600
DKIM_SELECTORS = ("resend", "google", "selector1", "selector2", "default")
LOOKUP_LIMIT = 10
_CACHE: dict = {}


def dns_resolver(name: str, rdtype: str) -> list:
    import dns.exception
    import dns.resolver
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 3.0
    try:
        answer = resolver.resolve(name, rdtype)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except dns.exception.DNSException as exc:
        raise OSError(str(exc)) from None
    out = []
    for r in answer:
        if rdtype == "TXT":
            out.append(b"".join(r.strings).decode("utf-8", "replace"))
        elif rdtype == "MX":
            out.append(f"{r.preference} {str(r.exchange).rstrip('.') or '.'}")
        else:
            out.append(str(r.target).rstrip(".") if hasattr(r, "target") else str(r))
    return out


def _check(name: str, status: str, found, advice: str) -> dict:
    return {"name": name, "status": status, "found": found, "advice": advice}


def _safe(resolve: Callable, name: str, rdtype: str):
    """(records, answered). answered=False when DNS did not reply."""
    try:
        return resolve(name, rdtype), True
    except OSError:
        return [], False


def _unknown(name: str) -> dict:
    return _check(name, "unknown", [], "The DNS lookup did not answer in time. Try again in a minute.")


def check_mx(resolve, domain: str) -> dict:
    rows, ok = _safe(resolve, domain, "MX")
    if not ok:
        return _unknown("Mail servers (MX)")
    if not rows:
        return _check("Mail servers (MX)", "warn", [], "No mail server is set for this domain, so replies to your emails cannot arrive. Add MX records at your DNS host.")
    if any(r.split(" ", 1)[-1].strip() == "." for r in rows):
        return _check("Mail servers (MX)", "fail", rows, "The domain says it accepts no mail (a null MX). Replies cannot arrive and many receivers distrust such senders.")
    return _check("Mail servers (MX)", "ok", rows, "The domain can receive replies.")


def _spf_lookups(spf: str) -> int:
    return len(re.findall(r"(?:^|\s)[+\-~?]?(?:include:|a(?:[:/\s]|$)|mx(?:[:/\s]|$)|ptr|exists:|redirect=)", spf))


def check_spf(resolve, domain: str, provider: str) -> dict:
    name = "SPF (who may send for you)"
    rows, ok = _safe(resolve, domain, "TXT")
    if not ok:
        return _unknown(name)
    spf = [r for r in rows if r.lower().startswith("v=spf1")]
    if not spf:
        return _check(name, "fail", [], f"No SPF record. Add a TXT record on {domain} that starts with v=spf1 and names your email provider. Without it mail is often filed as spam.")
    if len(spf) > 1:
        return _check(name, "fail", spf, "There is more than one SPF record. Receivers reject that. Merge them into a single TXT record.")
    text = spf[0].lower()
    if re.search(r"(^|\s)\+?all(\s|$)", text):
        return _check(name, "fail", spf, "The record ends in +all, which lets anyone in the world send as you. Change it to ~all or -all.")
    if _spf_lookups(text) > LOOKUP_LIMIT:
        return _check(name, "warn", spf, f"The record seems to use more than {LOOKUP_LIMIT} lookups, which breaks SPF. Remove services you no longer use (this is an estimate).")
    if provider == "resend":
        send_rows, send_ok = _safe(resolve, f"send.{domain}", "TXT")
        send_spf = " ".join(send_rows).lower()
        if "amazonses.com" not in text and "amazonses.com" not in send_spf:
            return _check(name, "warn", spf, f"Resend normally adds include:amazonses.com to the SPF record on send.{domain} (or on {domain}). I could not see it. Check with your provider that the sending domain is verified.")
    return _check(name, "ok", spf, "SPF is present and looks sound." + ("" if provider == "resend" else " Check with your provider that it lists their servers."))


def check_dkim(resolve, domain: str, provider: str) -> dict:
    name = "DKIM (your signature)"
    found, answered = [], 0
    for sel in DKIM_SELECTORS:
        host = f"{sel}._domainkey.{domain}"
        hit = False
        for kind in ("TXT", "CNAME"):
            rows, ok = _safe(resolve, host, kind)
            answered += ok
            hit = hit or bool(rows)
        if hit:
            found.append(sel)
    if not answered:
        return _unknown(name)
    if provider == "resend":
        if "resend" in found:
            return _check(name, "ok", found, "Resend's DKIM record (resend._domainkey) is published.")
        return _check(name, "fail", found, f"Resend's DKIM record resend._domainkey.{domain} was not found. Add it from the Domains page in Resend, then verify the domain there.")
    if found:
        return _check(name, "ok", found, "A DKIM record was found. Check with your provider that it is the one your emails are signed with.")
    return _check(name, "warn", [], "None of the common DKIM names exist (resend, google, selector1, selector2, default). Your provider may use another name: check with your provider that DKIM is switched on.")


def check_dmarc(resolve, domain: str) -> dict:
    name = "DMARC (what to do with fakes)"
    rows, ok = _safe(resolve, f"_dmarc.{domain}", "TXT")
    if not ok:
        return _unknown(name)
    rec = [r for r in rows if r.lower().replace(" ", "").startswith("v=dmarc1")]
    if not rec:
        return _check(name, "fail", [], f"No DMARC record. Gmail and Yahoo now expect one from senders. Add a TXT record on _dmarc.{domain}: v=DMARC1; p=none; rua=mailto:you@{domain}")
    tags = {k.strip().lower(): v.strip().lower() for k, _, v in (p.partition("=") for p in rec[0].split(";")) if k.strip()}
    policy, rua = tags.get("p", ""), tags.get("rua", "")
    if policy not in ("none", "quarantine", "reject"):
        return _check(name, "warn", rec, "The DMARC record has no valid p= policy. Use p=none, p=quarantine or p=reject.")
    note = "" if rua else " It has no rua= address, so you will not get reports about who sends as you."
    if policy == "none":
        return _check(name, "warn", rec, "DMARC only watches (p=none). It is a fine start. Once reports look clean, move to p=quarantine." + note)
    return _check(name, "ok", rec, f"DMARC is enforcing (p={policy})." + note)


def check_mail_from(resolve, domain: str, provider: str) -> dict:
    name = "Return-path / mail-from"
    if provider != "resend":
        return _check(name, "unknown", [], "Providers set the return-path differently. Check with your provider that it is on your own domain, so it lines up with your from-address.")
    rows, ok = _safe(resolve, f"send.{domain}", "MX")
    if not ok:
        return _unknown(name)
    if rows:
        return _check(name, "ok", rows, f"Resend's return-path is on send.{domain}, a subdomain of your from-domain, so it lines up (relaxed alignment).")
    return _check(name, "warn", [], f"I did not find send.{domain}. Resend uses it as the return-path. If you did not set up a custom return-path, check with your provider.")


def _from_email(store) -> tuple:
    """(from address, provider) of the connected sending email; ('', '') when none. The API key stays inside senders."""
    from .senders import _hub_email_provider, email_ready
    cfg = email_ready(store) or {}
    address = str(cfg.get("from_email") or "").strip().lower()
    hub = _hub_email_provider(store)
    return address, (str(hub).lower() if hub else "resend")


def report(store, resolver: Optional[Callable] = None, own_account: bool = False, now: Callable = time.time) -> dict:
    address, provider = _from_email(store)
    if own_account or "@" not in address:
        return {"configured": False, "message": "No sending email is connected yet.", "domain": "", "provider": "", "checks": []}
    domain = address.rsplit("@", 1)[1]
    key = (domain, provider, id(resolver))
    hit = _CACHE.get(key)
    if hit and now() - hit[0] < CACHE_SECONDS:
        return hit[1]
    resolve = resolver or dns_resolver
    out = {"configured": True, "message": "", "domain": domain, "provider": provider,
           "checks": [check_mx(resolve, domain), check_spf(resolve, domain, provider), check_dkim(resolve, domain, provider),
                      check_dmarc(resolve, domain), check_mail_from(resolve, domain, provider)]}
    _CACHE[key] = (now(), out)
    return out
