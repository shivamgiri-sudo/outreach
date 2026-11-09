"""Who is told, and which address a link in their email points to, for one account.

Alerts, reports and reminders must reach only the account they are about. Mas Callnet ("mcn") keeps the old global
behaviour (settings.alert_managers, the team caller, settings.public_base_url). A client account has:
  * its admins (role "admin" logins of the account) as the people of last resort when a lead has no owner,
  * its own managers list      settings["account_settings"][id]["alert_managers"]  (names of its own logins),
  * its own alerts-email switch settings["account_settings"][id]["alerts_email"]    (default on),
  * its own public address     settings["account_settings"][id]["public_base_url"]  (else the platform's).
A bell entry made for a client account is stamped with its account_id, so only that account's logins see it.
"""
from typing import Optional

from . import accounts


def own(store, account_id: str) -> dict:
    return accounts.own_settings(store, account_id or accounts.MCN)


def base_url(store, account_id: str, default: str = "") -> str:
    """The address links in this account's emails point to: its own public address if set, else the platform's."""
    mine = str(own(store, account_id).get("public_base_url") or "").strip() if account_id and account_id != accounts.MCN else ""
    return (mine or (store.read("settings").get("public_base_url") or default or "")).rstrip("/")


def email_on(store, account_id: str) -> bool:
    if not account_id or account_id == accounts.MCN:
        return store.read("settings").get("alerts_email", True) is not False
    return own(store, account_id).get("alerts_email", True) is not False


def users_of(store, account_id: str) -> list:
    from . import auth
    return [u for u in auth.users(store) if accounts.of_user(u) == (account_id or accounts.MCN)]


def managers(store, account_id: str) -> list:
    if not account_id or account_id == accounts.MCN:
        names = store.read("settings").get("alert_managers") or []
    else:
        names = own(store, account_id).get("alert_managers") or []
    return [n for n in names if isinstance(n, str) and n]


def default_person(store, account_id: str) -> str:
    """Who gets an alert for a lead with no owner: the team caller for Mas Callnet, else the account's first admin."""
    if not account_id or account_id == accounts.MCN:
        from .activities import caller_name
        return caller_name(store)
    for u in users_of(store, account_id):
        if u.get("role") == "admin":
            return u.get("name") or u["email"]
    return ""


def address_of(store, name: str, account_id: str) -> str:
    """The email of a person by name or address, looked up only among the account's own logins (and, for Mas Callnet,
    its team list)."""
    for u in users_of(store, account_id):
        if name in ((u.get("name") or ""), u.get("email") or ""):
            return u.get("email") or ""
    if not account_id or account_id == accounts.MCN:
        from .activities import team
        return next((m.get("email") or "" for m in team(store) if m.get("name") == name), "")
    return ""


def is_member(store, name: str, account_id: str) -> bool:
    return any(name in ((u.get("name") or ""), u.get("email") or "") for u in users_of(store, account_id))


def _mail(store, address: str, title: str, text: str) -> None:
    from . import alerts
    from .reminders import _log_transactional
    if not (alerts.SENDER and address):
        return
    subject = f"[Outreach] {title}"
    try:
        result = alerts.SENDER({"to": address, "subject": subject, "body": text, "kind": "internal"})
    except Exception as exc:                       # an alert must never break the action that raised it
        result = {"status": "failed", "error": str(exc)[:200]}
    _log_transactional(store, address, subject, text, "internal", None, result)


def tell_admins(store, account_id: str, title: str, text: str, kind: str) -> int:
    """Bell (stamped with the account) and email to each admin of a client account. Never raises. Returns people told."""
    sent = 0
    try:
        from .reminders import notify
        for admin in [u for u in users_of(store, account_id) if u.get("role") == "admin"]:
            notify(store, admin.get("name") or admin["email"], title, text, kind=kind, account_id=account_id)
            if email_on(store, account_id):
                _mail(store, admin.get("email"), title, text)
            sent += 1
    except Exception:
        import logging
        logging.getLogger("outreach.account_alerts").exception("account alert not sent")
    return sent


def tell(store, account_id: Optional[str], title: str, text: str, kind: str, platform: bool = True) -> int:
    """The account's admins and (platform=True) the super admins."""
    sent = tell_admins(store, account_id, title, text, kind) if account_id and account_id != accounts.MCN else 0
    if platform:
        from . import super_admin
        sent += super_admin.alert(store, title, text)
    return sent
