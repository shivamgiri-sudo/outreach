"""Abuse and limit alerts for the super admins, with the auto-hold of a misbehaving account.

Every alert is one row of the feed settings["abuse_alerts"] (newest kept: FEED_KEEP), a bell entry + email to every
super admin (app/super_admin.py alert), and a change-log row (app/audit.py, stamped with the account). An alert has a
`key` that names what and when (the period or the hour), so the same thing never alerts twice. The console shows the
feed as its Alerts strip. Detectors, and the thresholds they use (all constants below):

  signup_surge     more than SIGNUPS_PER_HOUR new sign-ups in an hour, or more than SIGNUPS_PER_SOURCE_HOUR from one IP
                   address or one company email domain            (checked when a sign-up is confirmed: signup.create_pending)
  bounce_rate /    an account's emails in the last RATE_DAYS days bounced above BOUNCE_HOLD_RATE or drew spam complaints
  complaint_rate   above COMPLAINT_HOLD_RATE (once it sent MIN_SENDS_FOR_RATE): the account's sending is HELD (auto_hold on its
                   row; accounts.hold_reason says why) until a super admin releases it      (scan, like inbound._pause_if_unhealthy)
  limit_80/100     an account reached 80% / 100% of a limit (app/account_limits.py; also tells the account's own admins)
  failed_logins    FAILED_LOGINS wrong sign-ins in a row within FAILED_LOGIN_MINUTES on a client login (login_log.record)
  volume_spike     an account sent today more than SPIKE_FACTOR times its daily average of the last SPIKE_DAYS days
                   (and at least SPIKE_MIN today)                                           (scan)
"""
import time
import uuid
import weakref
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import accounts
from .lead_model import now_iso

FEED = "abuse_alerts"
FEED_KEEP = 300
SIGNUPS_PER_HOUR = 5               # more than this many sign-ups in an hour
SIGNUPS_PER_SOURCE_HOUR = 3        # more than this many from one IP address or one company email domain in an hour
BOUNCE_HOLD_RATE = 0.02            # 2% bounces ...
COMPLAINT_HOLD_RATE = 0.003        # ... or 0.3% spam complaints (the platform's own pause thresholds, app/inbound.py)
RATE_DAYS = 7
MIN_SENDS_FOR_RATE = 50
FAILED_LOGINS = 5                  # in a row ...
FAILED_LOGIN_MINUTES = 15          # ... within this many minutes
SPIKE_FACTOR = 5
SPIKE_DAYS = 14
SPIKE_MIN = 50                     # a spike needs at least this many sends today to matter
SCAN_EVERY_SECONDS = 600
IST = timezone(timedelta(hours=5, minutes=30))
LABELS = {"signup_surge": "Sign-up surge", "bounce_rate": "Bounce rate", "complaint_rate": "Complaint rate", "limit_80": "Limit at 80%",
          "limit_100": "Limit reached", "failed_logins": "Failed sign-ins", "volume_spike": "Sending spike"}
_LAST_SCAN = weakref.WeakKeyDictionary()
_KNOWN = weakref.WeakKeyDictionary()          # store -> keys already raised, so a repeat costs no store read or write


def _name(store, account_id: str) -> str:
    return (accounts.find(store, account_id) or {}).get("name") or account_id or ""


def raise_alert(store, key: str, kind: str, account_id: str, title: str, text: str, audit_action: str = "", to_account: bool = False) -> bool:
    """Record one alert once per `key`. Returns True when it was new (and the people were told)."""
    known = _KNOWN.setdefault(store, set())
    if key in known:
        return False
    known.add(key)
    row = {"id": uuid.uuid4().hex[:10], "key": key, "at": now_iso(), "kind": kind, "account_id": account_id or "", "account": _name(store, account_id) if account_id else "",
           "title": title[:200], "text": text[:600]}
    fresh = {}

    def apply(s: dict) -> dict:
        rows = [r for r in s.get(FEED) or [] if isinstance(r, dict)]
        if any(r.get("key") == key for r in rows):
            return s
        fresh["row"] = row
        return {**s, FEED: [*rows, row][-FEED_KEEP:]}
    store.update("settings", apply)
    if "row" not in fresh:
        return False
    from . import account_alerts, audit, super_admin
    try:
        if audit_action:
            audit.record(store, "System", audit_action, row["account"] or kind, f"{kind}: {text}"[:400],
                         extra={"account_id": account_id or accounts.MCN, "alert_kind": kind})
        if to_account and account_id:
            account_alerts.tell_admins(store, account_id, title, text, f"limit:{kind}")
        super_admin.alert(store, title, text)
    except Exception:
        import logging
        logging.getLogger("outreach.abuse_alerts").exception("abuse alert not fully sent")
    return True


def feed(store, limit: int = 30) -> list:
    rows = [r for r in (store.read("settings").get(FEED) or []) if isinstance(r, dict)]
    return [{**{k: r.get(k) for k in ("id", "at", "kind", "account_id", "account", "title", "text")}, "label": LABELS.get(r.get("kind"), r.get("kind"))}
            for r in reversed(rows[-limit:])]


# ---------------------------------------------------------------- sign-up surge
def check_signups(store, now: Optional[datetime] = None) -> int:
    """Alert on a surge of sign-ups. Returns how many new alerts were raised."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    hour = now.strftime("%Y-%m-%d %H")
    recent = []
    for a in accounts.all_accounts(store):
        sign = a.get("signup") or {}
        if sign.get("at", "") >= since:
            email = str(sign.get("email") or "")
            recent.append({"ip": (sign.get("consent") or {}).get("ip") or "", "domain": email.rsplit("@", 1)[-1].lower() if "@" in email else ""})
    raised = 0
    from .hygiene import FREE_MAIL_DOMAINS
    if len(recent) > SIGNUPS_PER_HOUR:
        raised += raise_alert(store, f"signup_surge|{hour}", "signup_surge", "", f"Sign-up surge: {len(recent)} in an hour",
                              f"{len(recent)} accounts signed up in the last hour (alert above {SIGNUPS_PER_HOUR}). Check Setup > Accounts for the pending ones.",
                              "abuse.signup_surge")
    for field, label in (("ip", "IP address"), ("domain", "company email domain")):
        counts = {}
        for r in recent:
            if r[field] and not (field == "domain" and r[field] in FREE_MAIL_DOMAINS):
                counts[r[field]] = counts.get(r[field], 0) + 1
        for value, n in counts.items():
            if n > SIGNUPS_PER_SOURCE_HOUR:
                raised += raise_alert(store, f"signup_{field}|{value}|{hour}", "signup_surge", "", f"Sign-up surge from one {label}: {value}",
                                      f"{n} accounts signed up from the same {label} ({value}) in the last hour (alert above {SIGNUPS_PER_SOURCE_HOUR}).",
                                      "abuse.signup_surge")
    return raised


# ---------------------------------------------------------------- bounce / complaint rate and the auto-hold
def _lead_maps(store) -> tuple:
    amap = accounts.ws_account_map(store)
    lead_account, email_account = {}, {}
    for lead in store.read("leads"):
        aid = accounts.account_of_lead(amap, lead)
        lead_account[lead.get("id")] = aid
        if lead.get("email"):
            email_account.setdefault(lead["email"], aid)
    return lead_account, email_account


def account_rates(store, now: Optional[datetime] = None) -> dict:
    """{account id: {sent, bounces, complaints, bounce_rate, complaint_rate}} over the last RATE_DAYS days."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=RATE_DAYS)).isoformat(timespec="seconds")
    lead_account, email_account = _lead_maps(store)
    out = {}
    row = lambda aid: out.setdefault(aid, {"sent": 0, "bounces": 0, "complaints": 0})
    for m in store.read("outbox"):
        if m.get("status") == "sent" and m.get("sent_at", "") >= since:
            row(m.get("account_id") or lead_account.get(m.get("lead_id")) or accounts.MCN)["sent"] += 1
    for e in store.read("delivery_events"):
        if e.get("type") in ("bounce", "complaint") and e.get("at", "") >= since:
            row(email_account.get(e.get("email"), accounts.MCN))["bounces" if e["type"] == "bounce" else "complaints"] += 1
    for r in out.values():
        r["bounce_rate"] = r["bounces"] / r["sent"] if r["sent"] else 0.0
        r["complaint_rate"] = r["complaints"] / r["sent"] if r["sent"] else 0.0
    return out


def mcn_only(store):
    """A test "does this delivery event's address belong to Mas Callnet?" for the platform's own pause, so a client's
    bounces never pause Mas Callnet's sending. None while no client account holds a workspace."""
    if all(a == accounts.MCN for a in accounts.ws_account_map(store).values()):
        return None
    _, email_account = _lead_maps(store)
    return lambda email: email_account.get(email, accounts.MCN) == accounts.MCN


def hold_account(store, account_id: str, reason: str, kind: str, rates: Optional[dict] = None) -> bool:
    """Hold all sending of a client account until a super admin releases it. True when it was newly held."""
    if account_id == accounts.MCN or accounts.find(store, account_id) is None:
        return False
    held = {}

    def apply(s: dict) -> dict:
        rows = []
        for a in s.get("accounts") or []:
            if isinstance(a, dict) and a.get("id") == account_id and not a.get("auto_hold"):
                held["yes"] = True
                a = {**a, "auto_hold": {"reason": reason, "kind": kind, "at": now_iso(), **({"rates": rates} if rates else {})}}
            rows.append(a)
        return {**s, "accounts": rows}
    store.update("settings", apply)
    if held:
        from . import audit
        audit.record(store, "System", "account.auto_hold", _name(store, account_id), f"id {account_id}; {kind}; {reason}"[:400],
                     extra={"account_id": account_id})
    return bool(held)


def release_hold(store, account_id: str, user) -> bool:
    released = {}

    def apply(s: dict) -> dict:
        rows = []
        for a in s.get("accounts") or []:
            if isinstance(a, dict) and a.get("id") == account_id and a.get("auto_hold"):
                released["yes"] = True
                a = {k: v for k, v in a.items() if k != "auto_hold"}
            rows.append(a)
        return {**s, "accounts": rows}
    store.update("settings", apply)
    if released:
        from . import audit
        audit.record(store, user, "account.release_hold", _name(store, account_id), f"id {account_id}; sending hold released", extra={"account_id": account_id})
    return bool(released)


def check_deliverability(store, now: Optional[datetime] = None) -> int:
    """Hold any client account whose bounce or complaint rate passed the thresholds. Returns accounts newly held."""
    now = now or datetime.now(timezone.utc)
    held = 0
    for aid, r in account_rates(store, now).items():
        if aid == accounts.MCN or r["sent"] < MIN_SENDS_FOR_RATE:
            continue
        kind = "bounce_rate" if r["bounce_rate"] > BOUNCE_HOLD_RATE else "complaint_rate" if r["complaint_rate"] > COMPLAINT_HOLD_RATE else ""
        if not kind:
            continue
        reason = (f"{r['bounces']} bounces and {r['complaints']} complaints from {r['sent']} emails in {RATE_DAYS} days "
                  f"({r['bounce_rate'] * 100:.1f}% bounced, {r['complaint_rate'] * 100:.2f}% complained). Check the list quality; "
                  "a platform admin releases the hold.")
        if hold_account(store, aid, reason, kind, {k: round(v, 4) for k, v in r.items()}):
            held += 1
            raise_alert(store, f"{kind}|{aid}|{now.astimezone(IST).date().isoformat()}", kind, aid,
                        f"{_name(store, aid)}: sending held ({LABELS[kind].lower()})",
                        f"{_name(store, aid)}: {reason} Its sending is held until you release it in the console.")
    return held


# ---------------------------------------------------------------- sudden volume
def check_volume(store, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(IST).date()
    first = (today - timedelta(days=SPIKE_DAYS)).isoformat()
    lead_account, _ = _lead_maps(store)
    per = {}

    def count(aid: str, day: str) -> None:
        if first <= day <= today.isoformat():
            per.setdefault(aid, {})[day] = per.setdefault(aid, {}).get(day, 0) + 1
    for m in store.read("outbox"):
        if m.get("status") == "sent":
            count(m.get("account_id") or lead_account.get(m.get("lead_id")) or accounts.MCN, m.get("sent_at_ist_date") or "")
    for m in store.read("whatsapp_outbox"):
        if m.get("status") == "sent":
            try:
                day = datetime.fromisoformat(str(m.get("sent_at"))).astimezone(IST).date().isoformat()
            except (TypeError, ValueError):
                continue
            count(lead_account.get(m.get("lead_id")) or accounts.MCN, day)
    raised = 0
    for aid, days in per.items():
        if aid == accounts.MCN:
            continue
        n_today = days.get(today.isoformat(), 0)
        average = sum(v for d, v in days.items() if d < today.isoformat()) / SPIKE_DAYS
        if n_today >= SPIKE_MIN and n_today > SPIKE_FACTOR * average:
            raised += raise_alert(store, f"volume_spike|{aid}|{today.isoformat()}", "volume_spike", aid, f"{_name(store, aid)}: sending spike",
                                  f"{_name(store, aid)} sent {n_today} messages today against a {SPIKE_DAYS}-day daily average of {average:.1f} "
                                  f"(alert above {SPIKE_FACTOR}x).", "abuse.volume_spike")
    return raised


# ---------------------------------------------------------------- failed sign-ins on client logins
def check_failed_login(store, row: dict, now: Optional[datetime] = None) -> bool:
    """After a failed sign-in: alert once per burst when it is a client account's login (reuses login_log's streak)."""
    from . import auth, login_log
    user = auth.find_user(store, row.get("login") or "")
    if not user or not accounts.is_client(user):
        return False
    now = now or datetime.now(timezone.utc)
    streak = login_log._streak(store.read("login_history"), row["login"], now - timedelta(minutes=FAILED_LOGIN_MINUTES))
    if len(streak) < FAILED_LOGINS:
        return False
    ips = sorted({r.get("ip") for r in streak if r.get("ip")})
    aid = accounts.of_user(user)
    return raise_alert(store, f"failed_logins|{row['login']}|{streak[-1].get('id')}", "failed_logins", aid, f"{len(streak)} failed sign-ins: {row['login']}",
                       f"{len(streak)} wrong sign-ins in a row for {row['login']} ({_name(store, aid)}) within {FAILED_LOGIN_MINUTES} minutes"
                       f"{(' from ' + ', '.join(ips[:5])) if ips else ''}.", "abuse.failed_logins")


# ---------------------------------------------------------------- the periodic scan
def scan(store, now: Optional[datetime] = None, force: bool = False) -> dict:
    """Deliverability and volume checks, at most every SCAN_EVERY_SECONDS (called from the minute timer)."""
    if not force and time.monotonic() - _LAST_SCAN.get(store, -1e9) < SCAN_EVERY_SECONDS:
        return {}
    _LAST_SCAN[store] = time.monotonic()
    return {"held": check_deliverability(store, now), "spikes": check_volume(store, now)}
