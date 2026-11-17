"""One plain answer for every lead's email address: how sure are we that a mailbox is there?

States (the web app mirrors these rules in web/src/emailState.js; tests/test_email_state.py checks both give the same answer):
  candidate      only a guess: the lead has no real email but an `email_guess` (or its email came from a format guess,
                 email_source "guess:" / "pattern:") and nothing confirmed it
  domain_ready   the domain takes mail, but the mailbox itself is not confirmed: never checked, a check for another
                 address, unverified, risky (catch-all / unknown / shared inbox), or the built-in checker's "valid" (it
                 reads the domain's mail records and our history; it cannot look inside a mailbox)
  confirmed      a verifier said valid, the mailbox probe accepted it (built-in check with "the mail server confirmed this
                 mailbox"), or an email to it was delivered before
  bad            a check said invalid (no such mailbox, no mail server, typo, throwaway inbox) or an email to it bounced
  none           no address and no guess (no badge)

Order: bad beats confirmed (a bounce after a delivery is still a bounce), confirmed beats candidate.
`history` is {"bounced": set, "delivered": set} of lower-case addresses (email_checker.build_history gives it).
"""
from typing import Optional

STATES = ("confirmed", "domain_ready", "candidate", "bad", "none")
GUESS_SOURCES = ("guess:", "pattern:")
PROBE_CONFIRMED = "the mail server confirmed this mailbox"
DOMAIN_ONLY_PROVIDERS = ("builtin",)
LABELS = {"confirmed": "Mailbox confirmed", "domain_ready": "Domain ready", "candidate": "Guess only", "bad": "Bad address", "none": ""}


def _addr(value) -> str:
    return str(value or "").strip().lower()


def _empty() -> dict:
    return {"bounced": set(), "delivered": set()}


def mailbox_confirmed(check: dict) -> bool:
    """A 'valid' check that looked at the mailbox: any paid verifier or the probe; the built-in checker only when its
    SMTP step confirmed the mailbox."""
    if check.get("status") != "valid":
        return False
    if str(check.get("provider") or "") not in DOMAIN_ONLY_PROVIDERS:
        return True
    return any(PROBE_CONFIRMED in str(r) for r in check.get("reasons") or [])


def state_of(lead: dict, history: Optional[dict] = None) -> str:
    history = history or _empty()
    email = _addr(lead.get("email"))
    if not email:
        guess = _addr(lead.get("email_guess"))
        return "candidate" if guess and lead.get("email_guess_status") != "rejected" else "none"
    check = lead.get("email_check") or {}
    mine = _addr(check.get("email")) in ("", email)
    status = check.get("status") if mine else None
    if email in history.get("bounced", ()) or status == "invalid":
        return "bad"
    if email in history.get("delivered", ()) or (mine and mailbox_confirmed(check)):
        return "confirmed"
    if str(lead.get("email_source") or "").startswith(GUESS_SOURCES):
        return "candidate"
    return "domain_ready"


def why(lead: dict, state: str, history: Optional[dict] = None) -> str:
    """The tooltip: one plain sentence."""
    history = history or _empty()
    email = _addr(lead.get("email"))
    check = lead.get("email_check") or {}
    status = check.get("status") if _addr(check.get("email")) in ("", email) else None
    if state == "bad":
        return ("An email to this address bounced, so it is never emailed again." if email in history.get("bounced", ())
                else "A mailbox check said this address does not work (no such mailbox, no mail server or a typo).")
    if state == "confirmed":
        if email in history.get("delivered", ()):
            return "An email to this address was delivered before."
        return f"A mailbox check ({check.get('provider') or 'verifier'}) said this mailbox exists."
    if state == "candidate":
        return ("This address is only a guess from the company's email format. It is never emailed until someone confirms it."
                if not email else "This address came from an email format guess and nothing has confirmed it yet.")
    if state == "domain_ready":
        if status == "risky":
            return "The domain takes mail, but the mailbox could not be confirmed (it accepts every address, or the answer was unclear)."
        if status == "valid":
            return "The domain takes mail. The built-in check cannot look inside the mailbox, so the mailbox itself is not confirmed."
        if status == "unverified":
            return "The domain takes mail as far as we know, but the mailbox check gave no answer yet."
        return "Nobody has checked this mailbox yet. Emails to it are sent at a lower daily volume."
    return ""


def history_of(store) -> dict:
    """{bounced, delivered} addresses from delivery events and the outbox (all time), the same rules as the built-in checker."""
    from .email_checker import build_history
    full = build_history(store)
    return {"bounced": full["bounced"], "delivered": full["delivered"]}


def mark(store, rows: list, history: Optional[dict] = None) -> list:
    """Put `email_state` and `email_state_why` on presented lead dicts (in place; returns the list)."""
    history = history if history is not None else history_of(store)
    for row in rows:
        if isinstance(row, dict):
            state = state_of(row, history)
            row["email_state"] = state
            row["email_state_why"] = why(row, state, history)
    return rows


def mark_one(store, row: dict) -> dict:
    mark(store, [row])
    return row
