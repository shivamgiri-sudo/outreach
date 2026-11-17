"""Unconfirmed addresses at a lower volume: live sequence emails to an address whose mailbox is not confirmed
(app/email_state.py "domain_ready": catch-all, unknown, risky, never checked, built-in DNS-only check; and "candidate":
an address that came from a format guess) may use only a share of each sending mailbox's daily sends.

The app sends from one mailbox per account (Mas Callnet's email provider, or a client account's own sending keys), so
"each mailbox's daily sends" is that account's daily cap: Mas Callnet's effective cap after the warm-up ramp, a client
account's own daily cap. Over the share, the lead waits (its step stays due, a plain note says why) and goes on a later
run or day: nothing is dropped. Confirmed addresses are never held by this rule. Practice runs are never limited.

Setting: settings["email_volume"]["domain_ready_share"], 0-100, default DEFAULT_SHARE (0 = never send to them,
100 = no limit). Today's count comes from outbox rows stamped with `email_state` when they were sent (rows sent before
this rule existed carry no stamp and are not counted).
"""
from typing import Optional

from . import email_state

SETTING = "email_volume"
DEFAULT_SHARE = 100                      # off until the owner lowers it (20 recommended once addresses are verified by a real mailbox check)
LIMITED = ("domain_ready", "candidate")
NOTE = "the mailbox of this address is not confirmed, and today's share for unconfirmed addresses is used up (it goes on a later day)"


def share_of(store) -> int:
    raw = ((store.read("settings") or {}).get(SETTING) or {}).get("domain_ready_share", DEFAULT_SHARE)
    try:
        return max(0, min(100, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_SHARE


def save_share(store, value: int) -> int:
    share = max(0, min(100, int(value)))
    store.update("settings", lambda s: {**s, SETTING: {**(s.get(SETTING) or {}), "domain_ready_share": share}})
    return share


def allowance(daily_cap: int, share: int) -> int:
    """How many unconfirmed addresses a mailbox may email today. At least 1 when the share is above 0 and the cap allows any."""
    cap = max(0, int(daily_cap or 0))
    if share >= 100:
        return cap
    if share <= 0 or cap == 0:
        return 0
    return max(1, cap * share // 100)


def view(store) -> dict:
    return {"domain_ready_share": share_of(store), "default_share": DEFAULT_SHARE}


class Limit:
    """One send run's view of the rule: how many unconfirmed sends each account already made today."""

    def __init__(self, store, today: str, outbox: Optional[list] = None):
        self.store, self.share = store, share_of(store)
        self.history = email_state.history_of(store) if self.share < 100 else None
        self.used = {}
        for m in outbox if outbox is not None else store.read("outbox"):
            if m.get("status") == "sent" and m.get("sent_at_ist_date") == today and m.get("email_state") in LIMITED:
                key = m.get("account_id") or "mcn"
                self.used[key] = self.used.get(key, 0) + 1

    def waits(self, lead: dict, account: str, live: bool, daily_cap: int, message: dict, counts: dict) -> bool:
        """Stamp the message with the address's state; True (and a note on the lead) when it must wait for a later day."""
        state = email_state.state_of(lead, self.history) if self.history is not None else email_state.state_of(lead)
        message["email_state"] = state
        if not live or self.share >= 100 or state not in LIMITED:
            return False
        if self.used.get(account, 0) < allowance(daily_cap, self.share):
            return False
        counts["held"] = counts.get("held", 0) + 1
        counts["waiting_unconfirmed"] = counts.get("waiting_unconfirmed", 0) + 1
        if (lead.get("sequence") or {}).get("blocked") != NOTE:
            self.store.update_lead(lead["id"], lambda r: {**r, "sequence": {**r["sequence"], "blocked": NOTE}} if r.get("sequence") else None)
        return True

    def sent(self, account: str, message: dict) -> None:
        if message.get("status") == "sent" and message.get("email_state") in LIMITED:
            self.used[account] = self.used.get(account, 0) + 1
