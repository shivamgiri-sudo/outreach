"""Self-hosted mailbox probe as a free email verifier: RCPT TO against the recipient's own mail server, nothing is sent.
Needs outbound port 25 (cloud providers usually block it), a real hostname with matching reverse DNS, and a sender address at
a domain you own. See app/smtp_probe.py for the rules and docs/EMAIL_VERIFIER.md for setup and limits."""
import os
from pathlib import Path

from .. import smtp_probe
from ..campaign_sources import KeyRejected
from .base import SourceSpec, field, verdict

MAPPING = {"confirmed": "valid", "rejected": "invalid", "catch_all": "risky"}


def _log_path() -> str:
    return str(Path(os.environ.get("OUTREACH_DATA_FILE") or "data/store.json").with_name("probe.log"))


def _prober(ctx):
    prober = getattr(ctx, "_prober", None)
    if prober is None:
        if not smtp_probe.port_25_open():
            raise KeyRejected("Outbound port 25 is blocked from this server, so the mailbox probe cannot run here. "
                              "Run it from a host that allows port 25 (see docs/EMAIL_VERIFIER.md).")
        try:
            prober = smtp_probe.Prober(ctx.values.get("helo", ""), ctx.values.get("mail_from", ""), _log_path())
        except ValueError as exc:
            raise KeyRejected(str(exc)) from exc
        ctx._prober = prober
    return prober


def verify(ctx, email):
    def run():
        try:
            return _prober(ctx).check(email)
        except smtp_probe.ProbeRefused as exc:
            raise KeyRejected(str(exc)) from exc
    result = ctx.cached(f"probe:{email}", run, cost=0)           # cached 7 days: the same address is never probed twice a week
    status = MAPPING.get(result["status"], "unknown")
    reason = {"catch_all": "catch-all domain: the answer means nothing", "confirmed": "mail server accepted the mailbox"}.get(
        result["status"], result["status"].replace("_", " ") + (f" ({result['smtp_message']})" if result.get("smtp_message") else ""))
    return verdict("smtp_probe", status, reason, 0)


def test(ctx):
    if smtp_probe.port_25_open():
        return {"ok": True, "message": "Outbound port 25 is open from this server. Check that the hostname has matching reverse DNS before relying on results."}
    return {"ok": False, "message": "Outbound port 25 is blocked from this server (common on cloud hosts). The probe cannot run here."}


SPEC = SourceSpec(
    "smtp_probe", "Mailbox probe (self-hosted, no email sent)", ("verify_email",), "Email verification",
    "Asks the recipient's mail server whether the mailbox exists, then hangs up. No message is ever sent. Free.",
    "https://www.rfc-editor.org/rfc/rfc5321#section-3.3",
    "Connects to the company's own mail server on port 25. Keep to short lists of business contacts before one-to-one outreach; "
    "limited to 1 probe a second and 500 addresses a run.",
    ["Needs outbound port 25, a hostname with reverse DNS, and a sender address at a domain you own.",
     "Google Workspace and Microsoft 365 often accept every address, so many results stay 'not confirmed'."],
    fields=[field("helo", "Server hostname (with reverse DNS)", True, False, "mail.yourdomain.com"),
            field("mail_from", "Sender address at your domain", True, False, "verify@yourdomain.com")],
    cost=0, rate_per_min=60, cost_note="Free.", free_note="Free; needs port 25.",
    honest_status="built from RFC 5321; tested with mocked mail servers, not run against live servers (port 25 is blocked on the Oracle host)",
    verify=verify, test=test)
