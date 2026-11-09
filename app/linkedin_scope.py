"""LinkedIn agent per client account (app/accounts.py): who owns which LinkedIn account, the per-account switch and caps,
the platform ceilings, the platform-wide kill switch and the pause alert.

Ownership (nothing heavy is ever rewritten; missing = "mcn", so everything made before accounts stays Mas Callnet's):
  settings["linkedin_agent_accounts"][slug]["account_id"]   the client account a LinkedIn account (profile slug) works for.
                                                            Set the first time an agent of that account reports it; an
                                                            agent of another account is then refused for that slug.
  settings["linkedin_cloud"]["slots"][i]["account_id"]       a cloud / office slot (linkedin_cloud)
  settings["linkedin_runners"][i]["account_id"]              an office computer pairing (linkedin_runner)
  settings["extension_tokens"][i]["account_id"]              a pairing / slot token (extension_api): the agent only sees and
                                                            claims leads of that account (workspaces.scope_dependency)
Settings:
  the "mcn" account keeps the old global settings["linkedin_agent"]; any other account has
  settings["linkedin_agent_by_account"][account_id] with the same keys plus daily_connections and auto_start.
  Caps are clamped (server side) to the account's ceilings: accounts limits linkedin_daily_connections_max,
  linkedin_weekly_connections_max, linkedin_daily_messages_max (set by an owner; blank = no ceiling).
  settings["linkedin_agent_kill"] {on, at, by}: the owner's platform-wide stop. While on, no account's agent acts.
"""
from typing import Optional

from . import accounts
from .activities import iso, now_utc

BY_ACCOUNT, KILL = "linkedin_agent_by_account", "linkedin_agent_kill"
CEILINGS = {"daily_connections": "linkedin_daily_connections_max", "weekly_connections": "linkedin_weekly_connections_max",
            "daily_messages": "linkedin_daily_messages_max"}
KILL_WHY = "the platform admin stopped every LinkedIn agent"


class NotYours(PermissionError):
    """A LinkedIn account, slot or office computer of another client account."""


# ---------------------------------------------------------------- whose is it
def account_of_row(row: Optional[dict]) -> str:
    return (row or {}).get("account_id") or accounts.MCN


def slug_account(settings: dict, slug: str) -> Optional[str]:
    """The client account a LinkedIn account (slug) works for, None when no agent has reported it yet."""
    seen = (settings.get("linkedin_agent_accounts") or {}).get(slug)
    return account_of_row(seen) if isinstance(seen, dict) else None


def check_slug(store, slug: str, account_id: str) -> None:
    """Raise NotYours when `slug` already works for another client account."""
    if not slug:
        return
    owner = slug_account(store.read("settings"), slug)
    if owner is not None and owner != (account_id or accounts.MCN):
        raise NotYours("This LinkedIn account is connected to another client account on this portal.")


def token_account(user: Optional[dict]) -> str:
    """The client account an agent (pairing / slot token), runner or login acts for."""
    return accounts.of_user(user)


def name_of(store, account_id: str) -> str:
    row = accounts.find(store, account_id or accounts.MCN)
    return (row or {}).get("name") or account_id


def target(store, user: Optional[dict], raw: str = "", allow_all: bool = False) -> Optional[str]:
    """The account a LinkedIn page request is about. A non-owner: always its own account. An owner: ?account= (or
    "all" for the overview when allowed), else the account picked in the header (accounts.current()), else "mcn"."""
    user = user or {"role": "owner"}
    if user.get("role") != "owner":
        return accounts.of_user(user)
    raw = str(raw or "").strip()[:40]
    if allow_all and raw == "all":
        return None
    if raw and accounts.find(store, raw) is not None:
        return raw
    return accounts.current() or accounts.MCN


def may_manage(user: Optional[dict]) -> bool:
    """Owners, and admins (of their own account): change the agent, slots and office computer."""
    return (user or {"role": "owner"}).get("role") in ("owner", "admin")


def ensure_mine(user: Optional[dict], row_account: str) -> None:
    """A non-owner may touch only rows of its own account (404-style refusal so ids of others say nothing)."""
    user = user or {"role": "owner"}
    if user.get("role") != "owner" and accounts.of_user(user) != (row_account or accounts.MCN):
        raise LookupError("Not found.")


# ---------------------------------------------------------------- ceilings and per-account settings
def ceilings(store, account_id: str) -> dict:
    """{cap key: ceiling or None}."""
    return {key: accounts.limit_of(store, account_id or accounts.MCN, limit) for key, limit in CEILINGS.items()}


def clamp(cfg: dict, ceil: dict) -> dict:
    out = dict(cfg)
    for key, top in ceil.items():
        if top is not None and isinstance(out.get(key), int):
            out[key] = min(out[key], top)
    return out


def own_config(settings: dict, account_id: str) -> dict:
    found = (settings.get(BY_ACCOUNT) or {}).get(account_id)
    return found if isinstance(found, dict) else {}


def save_own_config(store, account_id: str, merged: dict) -> None:
    store.update("settings", lambda s: {**s, BY_ACCOUNT: {**(s.get(BY_ACCOUNT) or {}), account_id: merged}})


# ---------------------------------------------------------------- the kill switch
def kill_state(settings: dict) -> dict:
    found = settings.get(KILL)
    return found if isinstance(found, dict) and found.get("on") else {}


def set_kill(store, on: bool, user: Optional[dict]) -> dict:
    who = (user or {}).get("name") or (user or {}).get("email") or "owner"
    row = {"on": True, "at": iso(now_utc()), "by": who} if on else {"on": False, "at": iso(now_utc()), "by": who}
    store.update("settings", lambda s: {**s, KILL: row})
    return row


# ---------------------------------------------------------------- the pause alert
def admins_of(store, account_id: str) -> list:
    from . import auth
    return [u for u in auth.users(store) if u.get("role") == "admin" and accounts.of_user(u) == (account_id or accounts.MCN)]


def alert_paused(store, account_id: str, slug: str, note: str) -> int:
    """LinkedIn paused one of an account's LinkedIn accounts: a bell entry (stamped with the account, so its client
    logins see it) and email to each admin of that account, plus the super admins. Never raises."""
    from . import super_admin
    from .reminders import notify
    account_id = account_id or accounts.MCN
    title = f"LinkedIn account {slug} paused" if slug else "LinkedIn agent paused"
    text = (f"The LinkedIn agent of {name_of(store, account_id)} for {slug or 'its LinkedIn account'} stopped itself: "
            f"{note or 'LinkedIn showed a checkpoint'}. Open LinkedIn as that account, sort it out, then press Resume for it on the LinkedIn page.")
    sent = 0
    try:
        for admin in admins_of(store, account_id):
            row = notify(store, admin.get("name") or admin["email"], title, text, kind="linkedin")
            store.update("notifications", lambda rows, rid=row["id"]: [{**r, "account_id": account_id} if r.get("id") == rid else r
                                                                       for r in rows])
            _email(store, admin.get("email"), title, text, account_id)
            sent += 1
        sent += super_admin.alert(store, title, text)
    except Exception:                                          # an alert must never break the agent's report
        import logging
        logging.getLogger("outreach.linkedin").exception("LinkedIn pause alert not sent")
    return sent


def _email(store, address: str, title: str, text: str, account_id: str = "") -> None:
    from . import account_alerts, alerts
    from .reminders import _log_transactional
    if not (alerts.SENDER and address and account_alerts.email_on(store, account_id or accounts.MCN)):
        return
    subject = f"[Outreach] {title}"
    try:
        result = alerts.SENDER({"to": address, "subject": subject, "body": text, "kind": "internal"})
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)[:200]}
    _log_transactional(store, address, subject, text, "internal", None, result)
