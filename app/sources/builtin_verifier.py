"""Built-in free email checker: syntax, role address, disposable domain and a real MX lookup. No key, no cost.
The MX lookup uses DNS-over-HTTPS (Cloudflare's public JSON resolver, https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/)
through the guarded HTTP helper, so no DNS library is needed. GET https://cloudflare-dns.com/dns-query?name=<domain>&type=MX
header Accept: application/dns-json -> {Status: 0, Answer: [{type: 15, data: "10 mx.example.com."}]}; Status 3 = the domain does not exist.
It cannot see whether a particular mailbox exists, so a pass is "plausible", never "valid": a paid verifier is still needed to confirm a mailbox."""
from ..hygiene import is_role_address, normalise_email
from .base import SourceSpec, valid_host, verdict

DOH = "https://cloudflare-dns.com/dns-query"
# Small bundled list of common throwaway-mail domains (not exhaustive).
DISPOSABLE = frozenset("""mailinator.com guerrillamail.com guerrillamail.net guerrillamail.org sharklasers.com grr.la 10minutemail.com 10minutemail.net
tempmail.com temp-mail.org temp-mail.io tempmail.net tempmailo.com throwawaymail.com trashmail.com trashmail.net yopmail.com yopmail.net yopmail.fr
getnada.com nada.email dispostable.com maildrop.cc mailnesia.com mintemail.com fakeinbox.com fakemail.net spamgourmet.com spambox.us mohmal.com
emailondeck.com moakt.com mytemp.email tempinbox.com burnermail.io discard.email discardmail.com anonbox.net mailcatch.com mailsac.com
33mail.com e4ward.com incognitomail.com jetable.org tmail.ws tmailor.com mail.tm 1secmail.com 1secmail.net 1secmail.org wegwerfmail.de
harakirimail.com inboxkitten.com minuteinbox.com luxusmail.org emailfake.com fakemailgenerator.com dropmail.me gmailnator.com
trbvm.com cuvox.de dayrep.com einrot.com fleckens.hu gustr.com jourrapide.com rhyta.com superrito.com teleworm.us armyspy.com""".split())
EXTRA_ROLES = frozenset("""noreply no-reply donotreply do-not-reply postmaster webmaster abuse billing accounts accounting marketing press media
help team enquiries enquiry feedback hello career recruitment recruit hiring talent""".split())


def is_disposable(domain: str) -> bool:
    return domain in DISPOSABLE or any(domain.endswith("." + d) for d in DISPOSABLE)


def is_role(email: str) -> bool:
    return is_role_address(email) or email.split("@", 1)[0] in EXTRA_ROLES


def has_mail_server(ctx, domain: str) -> bool:
    """True when the domain has an MX record (a null MX '0 .' counts as none) or, failing that, an address record."""
    def lookup():
        mx = ctx.get(DOH, headers={"Accept": "application/dns-json"}, params={"name": domain, "type": "MX"})
        if mx.get("Status") == 3:
            return "none"
        hosts = [str(a.get("data", "")).split()[-1] for a in mx.get("Answer") or [] if isinstance(a, dict) and a.get("type") == 15]
        if any(h not in ("", ".") for h in hosts):
            return "mx"
        if hosts:
            return "none"                                   # only a null MX: the domain says it takes no mail
        a = ctx.get(DOH, headers={"Accept": "application/dns-json"}, params={"name": domain, "type": "A"})
        return "mx" if any(isinstance(r, dict) and r.get("type") == 1 for r in a.get("Answer") or []) else "none"
    return ctx.cached(f"mx:{domain}", lookup) == "mx"


def verify(ctx, email):
    mail = normalise_email(email)
    if not mail:
        return verdict("builtin", "invalid", "not a valid address", 0)
    domain = mail.split("@", 1)[1]
    if not valid_host(domain):
        return verdict("builtin", "invalid", "not a valid domain", 0)
    if is_disposable(domain):
        return verdict("builtin", "invalid", "disposable mail domain", 0)
    if not has_mail_server(ctx, domain):
        return verdict("builtin", "invalid", "the domain has no mail server", 0)
    if is_role(mail):
        return verdict("builtin", "risky", "shared mailbox (info@, hr@ ...); the domain accepts mail", 0)
    return verdict("builtin", "plausible", "format ok, domain accepts mail; mailbox not confirmed", 0)


def test(ctx):
    ok = has_mail_server(ctx, "gmail.com")
    return {"ok": ok, "message": "The DNS-over-HTTPS resolver answered (MX lookup for gmail.com)." if ok else "The resolver answered, but without an MX for gmail.com."}


SPEC = SourceSpec(
    "builtin_verifier", "Built-in email check (free)", ("verify_email",), "Email verification",
    "Free first check: format, shared-mailbox and throwaway-domain lists, and a real mail-server (MX) lookup.",
    "https://developers.cloudflare.com/1.1.1.1/encryption/dns-over-https/make-api-requests/dns-json/",
    "Queries Cloudflare's public DNS-over-HTTPS resolver for a domain name only; no email address leaves the app.",
    ["Nothing to set up. It runs first in every enrichment waterfall."], keyless=True, rate_per_min=120, cost_note="Free.",
    free_note="Free. It cannot confirm that a specific mailbox exists.", honest_status="built in; the DNS-over-HTTPS call is coded from the resolver's docs and was not run live",
    verify=verify, test=test)
