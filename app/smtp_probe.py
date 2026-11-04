"""Self-hosted email probe: asks the recipient's own mail server whether a mailbox exists, WITHOUT sending a message.

Per address: check the syntax, look up the domain's MX records (A record as a fallback), connect to the best MX on port 25,
say EHLO, MAIL FROM, RCPT TO <address>, note the reply, then QUIT. DATA is never sent. A second RCPT TO with a random
address tests for a catch-all domain (a server that accepts anything proves nothing).

Statuses: confirmed, rejected, catch_all, unknown_blocked, unknown_temporary, no_mail_server, invalid_syntax, error.
Only `confirmed` and `rejected` are answers. Everything else is "not confirmed", never "invalid".

Safeguards (hard limits, not settings): one probe per second overall, one connection at a time per MX host, at most 500
addresses per run, a domain is dropped after two policy blocks in a row, every probe is logged, and results are cached
by the callers for 7 days. It is for checking a short list of business contacts before one-to-one outreach.
"""
import random
import re
import smtplib
import socket
import string
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

SYNTAX = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@([A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+)$")
POLICY_WORDS = re.compile(r"(5\.7\.\d|access denied|blocked|blacklist|blocklist|spamhaus|reputation|policy|not permitted|"
                          r"refused|rejected due to|banned|ptr|reverse dns|rdns|service unavailable)", re.I)
MIN_GAP_SECONDS = 1.0
CONNECT_TIMEOUT = 10
MAX_PER_RUN = 500
DEFAULT_PER_RUN = 200
BLOCK_STREAK_LIMIT = 2
NOT_CONFIRMED = ("catch_all", "unknown_blocked", "unknown_temporary", "no_mail_server", "error")
STATUSES = ("confirmed", "rejected", "catch_all", "unknown_blocked", "unknown_temporary", "no_mail_server", "invalid_syntax", "error")


class ProbeRefused(Exception):
    """The probe cannot run at all here (limit reached, port 25 blocked): callers stop instead of retrying."""


def now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dns_mx(domain: str) -> Optional[list]:
    """[(priority, host)] sorted best first; the A record as a fallback (priority 0, the domain itself); None if neither exists."""
    import dns.exception
    import dns.resolver
    try:
        answer = dns.resolver.resolve(domain, "MX", lifetime=8)
        rows = sorted((r.preference, str(r.exchange).rstrip(".")) for r in answer)
        if rows and rows[0][1] not in ("", "."):
            return rows
        return None                                    # a null MX ("0 .") says the domain takes no mail
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
        pass
    except dns.exception.DNSException:
        raise OSError("DNS lookup failed")
    try:
        dns.resolver.resolve(domain, "A", lifetime=8)
        return [(0, domain)]
    except dns.exception.DNSException:
        return None


def smtp_connect(host: str, helo: str, timeout: int = CONNECT_TIMEOUT):
    server = smtplib.SMTP(timeout=timeout, local_hostname=helo)
    server.connect(host, 25)
    return server


def classify_reply(code: int, text: str) -> str:
    """Map one RCPT TO reply to confirmed / rejected / unknown_blocked / unknown_temporary / error."""
    text = text.decode("utf-8", "replace") if isinstance(text, bytes) else (text or "")
    if code in (250, 251):
        return "confirmed"
    if 400 <= code < 500:
        return "unknown_temporary"
    if POLICY_WORDS.search(text) and code not in (550, 551, 553) or re.search(r"5\.7\.\d", text):
        return "unknown_blocked"
    if code in (550, 551, 553, 511):
        return "rejected"
    if 500 <= code < 600:
        return "unknown_blocked"
    return "error"


def random_local() -> str:
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(16))


class Prober:
    """One prober holds the pacing, per-host locks, the block streaks and the log. Share one per process."""

    def __init__(self, helo: str, mail_from: str, log_path: Optional[str] = None, resolve: Callable = dns_mx,
                 connect: Callable = smtp_connect, sleep: Callable = time.sleep, clock: Callable = time.monotonic,
                 max_per_run: int = DEFAULT_PER_RUN):
        if not helo or "." not in helo:
            raise ValueError("Set a real hostname (with matching reverse DNS) to introduce the probe with.")
        if not mail_from or "@" not in mail_from:
            raise ValueError("Set a sender address at a domain you own.")
        self.helo, self.mail_from, self.log_path = helo, mail_from, log_path
        self.resolve, self.connect, self.sleep, self.clock = resolve, connect, sleep, clock
        self.max_per_run = min(int(max_per_run), MAX_PER_RUN)
        self.count = 0
        self._gate = threading.Lock()
        self._last = None
        self._host_locks = {}
        self._streak = {}
        self._mx_cache = {}

    # ---- pacing and limits
    def _pace(self) -> None:
        with self._gate:
            if self._last is not None:
                wait = MIN_GAP_SECONDS + random.uniform(0, 0.8) - (self.clock() - self._last)
                if wait > 0:
                    self.sleep(wait)
            self._last = self.clock()

    def _host_lock(self, host: str) -> threading.Lock:
        with self._gate:
            return self._host_locks.setdefault(host, threading.Lock())

    def _log(self, email: str, host: str, code, text: str, status: str) -> None:
        if not self.log_path:
            return
        line = f"{now_text()}\t{email}\t{host}\t{code}\t{status}\t{str(text)[:120]!r}\n"
        try:
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass

    # ---- one address
    def check(self, email: str) -> dict:
        email = (email or "").strip()
        base = {"email": email, "status": "", "smtp_code": "", "smtp_message": "", "mx_host": "", "is_catch_all": "", "checked_at": now_text()}
        match = SYNTAX.match(email)
        if not match:
            return {**base, "status": "invalid_syntax"}
        domain = match.group(1).lower()
        if self._streak.get(domain, 0) >= BLOCK_STREAK_LIMIT:
            return {**base, "status": "unknown_blocked", "smtp_message": "skipped: this domain refused us twice in a row"}
        if self.count >= self.max_per_run:
            raise ProbeRefused(f"Reached the limit of {self.max_per_run} addresses for this run.")
        try:
            if domain not in self._mx_cache:
                self._mx_cache[domain] = self.resolve(domain)
            mx = self._mx_cache[domain]
        except OSError as exc:
            return {**base, "status": "error", "smtp_message": str(exc)[:120]}
        if not mx:
            return {**base, "status": "no_mail_server"}
        host = mx[0][1]
        self.count += 1
        with self._host_lock(host):
            self._pace()
            return self._talk(base, email, domain, host)

    def _talk(self, base: dict, email: str, domain: str, host: str) -> dict:
        base = {**base, "mx_host": host}
        try:
            server = self.connect(host, self.helo)
        except (OSError, smtplib.SMTPException) as exc:
            self._log(email, host, "", f"connect: {exc}", "error")
            return {**base, "status": "error", "smtp_message": f"could not connect on port 25 ({str(exc)[:80]})"}
        try:
            code, text = server.ehlo(self.helo)
            if code >= 400 and server.helo(self.helo)[0] >= 400:
                return self._finish(base, email, domain, code, text, "unknown_blocked")
            code, text = server.mail(self.mail_from)
            if code >= 400:
                return self._finish(base, email, domain, code, text, classify_reply(code, text) if code >= 500 else "unknown_temporary", force_block=code >= 500)
            code, text = server.rcpt(email)
            text = text.decode("utf-8", "replace") if isinstance(text, bytes) else str(text)
            status = classify_reply(code, text)
            catch_all = ""
            if status == "confirmed":
                rcode, _rtext = server.rcpt(f"{random_local()}@{domain}")      # a made-up address: accepted means catch-all
                catch_all = rcode in (250, 251)
                if catch_all:
                    status = "catch_all"
            return self._finish(base, email, domain, code, text, status, catch_all)
        except (OSError, smtplib.SMTPException) as exc:
            self._log(email, host, "", f"smtp: {exc}", "error")
            return {**base, "status": "error", "smtp_message": f"mail server conversation failed ({str(exc)[:80]})"}
        finally:
            try:
                server.quit()                                                    # QUIT; DATA is never sent
            except (OSError, smtplib.SMTPException):
                pass

    def _finish(self, base: dict, email: str, domain: str, code: int, text: str, status: str, catch_all="", force_block=False) -> dict:
        if isinstance(text, bytes):
            text = text.decode("utf-8", "replace")
        if status == "unknown_blocked" or force_block:
            self._streak[domain] = self._streak.get(domain, 0) + 1
        else:
            self._streak[domain] = 0
        self._log(email, base["mx_host"], code, text, status)
        return {**base, "status": status, "smtp_code": code, "smtp_message": str(text)[:200], "is_catch_all": catch_all}


def port_25_open(host: str = "gmail-smtp-in.l.google.com", timeout: int = 6) -> bool:
    """Is outbound port 25 usable from here? Cloud providers usually block it."""
    try:
        with socket.create_connection((host, 25), timeout=timeout):
            return True
    except OSError:
        return False
