"""Deliverability report: which source, domain and step bounces. Counts and domains only: no addresses, no names.

report(store, leads, days) works on the leads the caller may see (the route cuts them by workspace and ownership):
sent = outbox rows with status 'sent' in the window; bounced / complaints / delivered = delivery events in the window, matched
to a visible lead by address. Pure over the store's rows, so tests seed the store and need no network.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from .email_checker_data import PERSONAL
from .hygiene import is_role_address
from .inbound import BOUNCE_PAUSE_RATE, COMPLAINT_PAUSE_RATE, MIN_SENDS_FOR_RATE

TOP_DOMAINS = 15
MAX_DAYS = 365


def rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def verdict(sent: int, bounce_rate: float, complaint_rate: float) -> dict:
    """green under 1%, amber 1-2%, red above 2% (the level at which sending pauses by itself)."""
    if not sent:
        return {"level": "none", "text": "Nothing was sent in this period, so there is no rate to judge yet."}
    pct = f"{bounce_rate * 100:.1f}%"
    if bounce_rate > BOUNCE_PAUSE_RATE or complaint_rate > COMPLAINT_PAUSE_RATE:
        return {"level": "bad", "text": f"{pct} of emails bounced. Above {BOUNCE_PAUSE_RATE * 100:.0f}% (or {COMPLAINT_PAUSE_RATE * 100:.1f}% spam complaints) "
                                        f"sending pauses by itself once there are {MIN_SENDS_FOR_RATE} sends in 7 days, to protect your sender name. Clean the list first."}
    if bounce_rate >= 0.01:
        return {"level": "warn", "text": f"{pct} of emails bounced. That is getting close to the {BOUNCE_PAUSE_RATE * 100:.0f}% level that pauses sending. Check the worst sources below."}
    return {"level": "good", "text": f"{pct} of emails bounced. That is healthy (under 1%)."}


def _group(rows: dict, limit: Optional[int] = None) -> list:
    out = [{"key": k, "sent": v["sent"], "bounced": v["bounced"], "complaints": v.get("complaints", 0),
            "delivered": v.get("delivered", 0), "bounce_rate": rate(v["bounced"], v["sent"])} for k, v in rows.items()]
    out.sort(key=lambda r: (-r["bounce_rate"] if r["sent"] else 0, -r["bounced"], -r["sent"], str(r["key"])))
    return out[:limit] if limit else out


def _bump(rows: dict, key, field: str) -> None:
    rows.setdefault(key, {"sent": 0, "bounced": 0, "complaints": 0, "delivered": 0})[field] += 1


def report(store, leads: list, days: int = 30, now: Optional[datetime] = None) -> dict:
    days = max(1, min(int(days), MAX_DAYS))
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).isoformat(timespec="seconds")
    by_id = {l["id"]: l for l in leads}
    by_email = {str(l.get("email") or "").strip().lower(): l for l in leads if l.get("email")}
    total = {"sent": 0, "delivered": 0, "bounced": 0, "complaints": 0}
    day_rows = {(now - timedelta(days=i)).date().isoformat(): {"sent": 0, "bounced": 0} for i in range(days - 1, -1, -1)}
    groups = {name: {} for name in ("source", "domain", "step", "check", "address", "mailbox")}
    sent_rows = []                                               # (to, at, step) for matching a bounce to the step before it
    for m in store.read("outbox"):
        lead = by_id.get(m.get("lead_id"))
        if lead is None or m.get("status") != "sent" or (m.get("sent_at") or "") < since:
            continue
        email = str(m.get("to") or lead.get("email") or "").strip().lower()
        total["sent"] += 1
        sent_rows.append((email, m.get("sent_at") or "", m.get("step") or "Email"))
        _tally(groups, lead, email, m.get("step") or "Email", "sent")
        day = (m.get("sent_at") or "")[:10]
        if day in day_rows:
            day_rows[day]["sent"] += 1
    sent_rows.sort(key=lambda r: r[1])
    for e in store.read("delivery_events"):
        kind, at = e.get("type"), e.get("at") or ""
        email = str(e.get("email") or "").strip().lower()
        lead = by_email.get(email)
        if kind not in ("bounce", "complaint", "delivered") or at < since or lead is None:
            continue
        field = {"bounce": "bounced", "complaint": "complaints", "delivered": "delivered"}[kind]
        total[field] += 1
        step = next((s for to, sat, s in reversed(sent_rows) if to == email and sat <= at), None) or "Unknown"
        _tally(groups, lead, email, step, field)
        if kind == "bounce" and at[:10] in day_rows:
            day_rows[at[:10]]["bounced"] += 1
    bounce_rate, complaint_rate = rate(total["bounced"], total["sent"]), rate(total["complaints"], total["sent"])
    paused = (store.read("settings") or {}).get("sending_paused") or {}
    return {"days": days, "since": since[:10], **total, "bounce_rate": bounce_rate, "complaint_rate": complaint_rate,
            "verdict": verdict(total["sent"], bounce_rate, complaint_rate),
            "by_day": [{"day": d, **v} for d, v in day_rows.items()],
            "by_source": _group(groups["source"]), "by_domain": _group(groups["domain"], TOP_DOMAINS), "by_step": _group(groups["step"]),
            "by_check": _group(groups["check"]), "by_address_type": _group(groups["address"]), "by_mailbox_type": _group(groups["mailbox"]),
            "sending": {"paused": bool(paused), "reason": paused.get("reason") or "", "since": paused.get("at") or "",
                        "pause_above_bounce_rate": BOUNCE_PAUSE_RATE, "pause_above_complaint_rate": COMPLAINT_PAUSE_RATE,
                        "min_sends_for_pause": MIN_SENDS_FOR_RATE}}


def _tally(groups: dict, lead: dict, email: str, step: str, field: str) -> None:
    domain = email.rsplit("@", 1)[-1] if "@" in email else "unknown"
    check = lead.get("email_check") or {}
    checked = "Not checked" if not check.get("status") else f"{check['status'].capitalize()} ({check.get('provider') or 'verifier'})"
    _bump(groups["source"], (str(lead.get("source") or "unknown").split(":")[0].strip().lower() or "unknown"), field)
    _bump(groups["domain"], domain, field)
    _bump(groups["step"], step, field)
    _bump(groups["check"], checked, field)
    _bump(groups["address"], "Shared mailbox (info@, hr@)" if "@" in email and is_role_address(email) else "Named person", field)
    _bump(groups["mailbox"], "Personal (gmail, yahoo...)" if domain in PERSONAL else "Company domain", field)
