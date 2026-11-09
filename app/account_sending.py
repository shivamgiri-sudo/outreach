"""Sending for one account: which settings a lead's messages use, whether they may go at all, and live or practice.

Mas Callnet ("mcn") leads use the global settings and switches exactly as before. A lead in another account's workspace
uses that account's own sender identity, brand, caps and its own "send for real" switch (accounts.settings_for), and:
  * its own sending keys (account_settings[id].sending: Resend api_key, from_email, from_name) when it has them, or
  * the platform's email provider only when the super admin granted it (integrations_granted: sending_domain /
    email_send) -- then the platform's own live switch must be on as well;
  * otherwise nothing is sent: accounts.hold_reason says "connect your sending domain or ask the platform admin...".
"""
from typing import Optional

from . import accounts


def for_lead(store, lead: Optional[dict], global_cfg: dict, global_live: bool, amap: Optional[dict] = None) -> dict:
    """{account, cfg, live, hold, own} for one lead's email."""
    amap = amap if amap is not None else accounts.ws_account_map(store)
    aid = accounts.account_of_lead(amap, lead or {})
    if aid == accounts.MCN:
        from .platform_controls import stop_reason
        return {"account": aid, "cfg": global_cfg, "live": global_live, "hold": stop_reason(store), "own": False}
    return for_account(store, aid, global_cfg, global_live)


def for_account(store, aid: str, global_cfg: dict, global_live: bool) -> dict:
    from .brand import sender_name
    from .sequence_runner import DEFAULT_DAILY_CAP, DEFAULT_PER_RUN_CAP
    raw = accounts.settings_for(store, aid)
    cfg = {"daily_cap": DEFAULT_DAILY_CAP, "per_run_cap": DEFAULT_PER_RUN_CAP, "live_sending": False, **raw,
           "sender_name": sender_name(raw)}
    cap = accounts.limit_of(store, aid, "daily_sends")
    if cap is not None:
        cfg["daily_cap"] = min(int(cfg["daily_cap"]), cap)
    own = bool(accounts.sending_config(store, aid))
    live = bool(cfg.get("live_sending")) and (own or global_live)
    return {"account": aid, "cfg": cfg, "live": live, "hold": accounts.hold_reason(store, aid, "email"), "own": own}


def stamp(message: dict, mode: dict) -> dict:
    """Mark a message with its account so the sender uses that account's keys (mcn messages are left as they were)."""
    if mode["account"] != accounts.MCN:
        message.update({"account_id": mode["account"], "account_live": mode["live"]})
    return message


def sent_today(outbox: list, aid: str, status: str, day: str) -> int:
    return sum(1 for m in outbox if m.get("account_id") == aid and m.get("status") == status and m.get("sent_at_ist_date") == day)


def sender_config(store, message: dict):
    """For the email sender: (own Resend config or None, refusal result or None). Only for non-mcn messages."""
    aid = message.get("account_id")
    if not aid or aid == accounts.MCN:
        return None, None
    own = accounts.sending_config(store, aid)
    if own:
        return own, (None if message.get("account_live") else {"status": "dry_run"})
    if any(accounts.granted(store, aid, k) for k in accounts.EMAIL_GRANTS) and accounts.account_active(store, aid):
        return None, None                      # the platform's provider, granted by the super admin
    return None, {"status": "held", "error": accounts.HOLD_EMAIL}
