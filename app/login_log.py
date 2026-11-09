"""Login history: every sign-in attempt, sign-out and password reset, plus the failed-sign-in alert.

Rows live in the `login_history` collection (the newest MAX_ROWS are kept):
  {id, at (UTC ISO), event, login, email, result, reason, ip, agent, two_step, method}
  - event:  login | logout | password_reset | password_reset_request
  - login:  the email typed, or "admin password" for the OUTREACH_ADMIN_PASSWORD sign-in
  - result: success | failure | pending (password right, the two-step code still to come)
  - ip:     web_security.client_ip (X-Forwarded-For is used only when the peer is the trusted local proxy)

Alert: FAILS_FOR_ALERT failed sign-ins in a row for one login (an existing login or the admin password) within
WINDOW_MINUTES raise one bell notification (and an email when the transactional sender is set up) to every owner.
The row that raised it is marked `alerted`, so the same burst never alerts twice; a successful sign-in ends a burst.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from .hygiene import normalise_email

MAX_ROWS = 5000
FAILS_FOR_ALERT = 5
WINDOW_MINUTES = 15
ADMIN_LOGIN = "admin password"
AGENT_CHARS = 200
IST = timezone(timedelta(hours=5, minutes=30))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(at: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(at).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def record(store, request, event: str, login: str, result: str, reason: str = "", two_step: bool = False,
           method: str = "password", now: Optional[datetime] = None) -> dict:
    """Write one row (never raises into the caller's sign-in) and check for a failed-sign-in burst."""
    from .web_security import client_ip
    email = normalise_email(login) if login and login != ADMIN_LOGIN else ""
    row = {"id": uuid.uuid4().hex[:12], "at": (now or _now()).isoformat(timespec="seconds"), "event": event,
           "login": (email or str(login or "")[:120]) or ADMIN_LOGIN, "email": email, "result": result,
           "reason": str(reason or "")[:200], "ip": client_ip(request) if request is not None else "",
           "agent": (request.headers.get("user-agent", "") if request is not None else "")[:AGENT_CHARS],
           "two_step": bool(two_step), "method": method}
    try:
        store.update("login_history", lambda rows: [*rows[-(MAX_ROWS - 1):], row])
        if event == "login" and result == "failure":
            _maybe_alert(store, row, now or _now())
    except Exception:                       # the log must never stop anyone signing in
        pass
    return row


def _streak(rows: list, login: str, since: datetime) -> list:
    """The failed sign-ins in a row for this login (newest first), within the window."""
    out = []
    for r in reversed(rows):
        if r.get("event") != "login" or r.get("login") != login:
            continue
        at = _parse(r.get("at"))
        if r.get("result") == "success" or at is None or at < since:
            break
        if r.get("result") == "failure":
            out.append(r)
    return out


def _maybe_alert(store, row: dict, now: datetime) -> None:
    from . import auth
    login = row["login"]
    if login != ADMIN_LOGIN and not auth.find_user(store, login):
        return                              # an address with no login: nothing to protect
    streak = _streak(store.read("login_history"), login, now - timedelta(minutes=WINDOW_MINUTES))
    if len(streak) < FAILS_FOR_ALERT or any(r.get("alerted") for r in streak):
        return
    store.update("login_history", lambda rows: [{**r, "alerted": True} if r.get("id") == row["id"] else r for r in rows])
    ips = sorted({r.get("ip") for r in streak if r.get("ip")})
    title = f"{len(streak)} failed sign-ins: {login}"
    text = (f"{len(streak)} wrong sign-in attempts in a row for {login} within {WINDOW_MINUTES} minutes"
            f"{(' from ' + ', '.join(ips[:5])) if ips else ''}. If this was not them, reset the password and check "
            "Setup > Security > Login history.")
    alert_owners(store, title, text)


def owners(store) -> list:
    from . import auth
    return [u for u in auth.users(store) if u.get("role") == "owner"]


def alert_owners(store, title: str, text: str) -> None:
    from . import alerts
    from .reminders import _log_transactional, notify
    people = owners(store)
    if not people:
        notify(store, None, title, text, kind="security:failed_logins")
        return
    for u in people:
        notify(store, u.get("name") or u["email"], title, text, kind="security:failed_logins")
        if alerts.SENDER and store.read("settings").get("alerts_email", True) is not False:
            subject = f"[Outreach] {title}"
            try:
                result = alerts.SENDER({"to": u["email"], "subject": subject, "body": text, "kind": "internal"})
            except Exception as exc:
                result = {"status": "failed", "error": str(exc)[:200]}
            _log_transactional(store, u["email"], subject, text, "internal", None, result)


# ---------- reading ----------
def visible_logins(store, viewer: dict) -> Optional[set]:
    """None = every row (owners); else the logins a workspace admin manages; an empty set for anyone else."""
    from . import auth
    if viewer.get("role") == "owner":
        return None
    if viewer.get("role") != "admin":
        return set()
    mine = set(viewer.get("workspaces") or [])
    out = set()
    for u in auth.users(store):
        spaces = set(u.get("workspaces") or [])
        if u.get("role") in ("admin", "caller", "viewer") and spaces and spaces <= mine:
            out.add(u["email"])
    return out


def history(store, viewer: dict, person: str = "", result: str = "", event: str = "", date_from: str = "", date_to: str = "",
            limit: int = 200, offset: int = 0) -> dict:
    """Newest first. date_from / date_to are IST days (YYYY-MM-DD, inclusive), as on the Audit tab."""
    allowed = visible_logins(store, viewer)
    rows = list(reversed(store.read("login_history")))
    if allowed is not None:
        rows = [r for r in rows if r.get("email") and r["email"] in allowed]

    def day(r):
        at = _parse(r.get("at"))
        return at.astimezone(IST).date().isoformat() if at else ""
    people = sorted({r.get("login") for r in rows if r.get("login")})
    if person:
        rows = [r for r in rows if person.lower() in str(r.get("login", "")).lower()]
    if result:
        rows = [r for r in rows if r.get("result") == result]
    if event:
        rows = [r for r in rows if r.get("event") == event]
    if date_from:
        rows = [r for r in rows if day(r) >= date_from]
    if date_to:
        rows = [r for r in rows if day(r) and day(r) <= date_to]
    limit = max(1, min(int(limit or 200), 500))
    offset = max(0, int(offset or 0))
    return {"total": len(rows), "items": rows[offset:offset + limit], "people": people}


def wrong_codes(store, login: str, now: Optional[datetime] = None) -> int:
    """Wrong two-step codes in a row for this login within the window (only someone who knew the password made them)."""
    since = (now or _now()) - timedelta(minutes=WINDOW_MINUTES)
    return sum(1 for r in _streak(store.read("login_history"), login, since) if r.get("method") in ("code", "setup"))
