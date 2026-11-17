"""Bounce meter per sending mailbox, and the per-mailbox auto-pause.

The app sends from one mailbox per account: Mas Callnet's email provider address, or a client account's own sending
address. Every outbox row records the mailbox it went from (`from_email`, stamped by account_sending.stamp). Rows sent
before that stamp existed are counted for the account's CURRENT address (the provider address rarely changes).

meter(): per mailbox, emails sent and hard bounces (and spam complaints) over the last 7 and 30 days. A bounce or
complaint counts for the mailbox of the last email sent to that address before it. Levels: green under WARN_RATE,
amber from WARN_RATE (2%), red above PAUSE_RATE (5%) once the mailbox sent MIN_SENDS (the same minimum as the platform's
own pause, app/inbound.py) in 7 days, or while it is paused.

Auto-pause (inbound._pause_if_unhealthy calls pause_unhealthy): only for Mas Callnet's own mailboxes, like the global
pause it extends.
  * one mailbox sent in the last 7 days (the usual case): the global pause is used exactly as before (above 2% bounces
    or 0.3% complaints with 50 sends in 7 days -> settings.sending_paused, "Resume" on the Outbox page);
  * more than one mailbox sent in the last 7 days (the from address was changed): each mailbox is judged on its own: above
    PAUSE_RATE bounces (or the complaint level) with MIN_SENDS sends in 7 days, THAT mailbox is paused
    (settings.mailbox_paused[address]); the others keep sending. Sending from a paused mailbox stops until an owner
    clicks "Resume" on its row (POST /api/deliverability/senders/resume) or on the Outbox page.
Client accounts keep their own account hold (app/abuse_alerts.py, the same 2% / 50-sends rule); their mailboxes are
shown on the meter but never paused from here.
"""
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

WARN_RATE = 0.02
PAUSE_RATE = 0.05
WINDOWS = (7, 30)
SETTING = "mailbox_paused"
UNKNOWN = "(sending address not set)"


def _limits() -> tuple:
    from .inbound import COMPLAINT_PAUSE_RATE, MIN_SENDS_FOR_RATE
    return MIN_SENDS_FOR_RATE, COMPLAINT_PAUSE_RATE


def _addr(value) -> str:
    return str(value or "").strip().lower()


def is_mcn(m: dict) -> bool:
    return (m.get("account_id") or "mcn") == "mcn"


def current_sender(store) -> str:
    from .senders import email_ready
    return _addr((email_ready(store) or {}).get("from_email"))


def _fallbacks(store) -> Callable:
    """The key for a row sent before `from_email` was stamped: the account's current sending address."""
    mcn, cache = current_sender(store) or UNKNOWN, {}

    def key_of(m: dict) -> str:
        if _addr(m.get("from_email")):
            return _addr(m["from_email"])
        if is_mcn(m):
            return mcn
        aid = m["account_id"]
        if aid not in cache:
            from . import accounts
            cache[aid] = _addr(accounts.sending_config(store, aid).get("from_email")) or mcn
        return cache[aid]
    return key_of


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def tally(store, now: Optional[datetime] = None, keep: Optional[Callable] = None) -> dict:
    """{mailbox: {account, sent_7, bounced_7, complaints_7, sent_30, bounced_30, complaints_30}} over sent outbox rows
    that `keep(row)` accepts (default: all)."""
    now = now or datetime.now(timezone.utc)
    since = {d: (now - timedelta(days=d)).isoformat(timespec="seconds") for d in WINDOWS}
    oldest = since[max(WINDOWS)]
    key_of, out, sent_to = _fallbacks(store), {}, {}

    def row(key: str, account: str) -> dict:
        return out.setdefault(key, {"account": account, **{f"{k}_{d}": 0 for d in WINDOWS for k in ("sent", "bounced", "complaints")}})
    for m in store.read("outbox"):
        at = m.get("sent_at") or ""
        if m.get("status") != "sent" or at < oldest or (keep is not None and not keep(m)):
            continue
        key = key_of(m)
        r = row(key, m.get("account_id") or "mcn")
        for d in WINDOWS:
            r[f"sent_{d}"] += at >= since[d]
        sent_to.setdefault(_addr(m.get("to")), []).append((at, key))
    for e in store.read("delivery_events"):
        at, kind = e.get("at") or "", e.get("type")
        if kind not in ("bounce", "complaint") or at < oldest:
            continue
        before = [(sat, k) for sat, k in sent_to.get(_addr(e.get("email")), []) if sat <= at]
        if not before:
            continue                          # no email of ours went to it in the window: not this meter's bounce
        key = max(before)[1]
        field = "bounced" if kind == "bounce" else "complaints"
        for d in WINDOWS:
            out[key][f"{field}_{d}"] += at >= since[d]
    return out


def _paused_map(store) -> dict:
    return dict((store.read("settings") or {}).get(SETTING) or {})


def level(r: dict, paused: bool) -> str:
    min_sends, complaint_rate = _limits()
    if paused:
        return "red"
    if not r["sent_7"] and not r["sent_30"]:
        return "none"
    if r["sent_7"] >= min_sends and (r["rate_7"] > PAUSE_RATE or _rate(r["complaints_7"], r["sent_7"]) > complaint_rate):
        return "red"
    if r["rate_7"] >= WARN_RATE or r["rate_30"] >= WARN_RATE:
        return "amber"
    return "green"


def meter(store, now: Optional[datetime] = None, keep: Optional[Callable] = None) -> list:
    """Rows for the deliverability page's per-sender table, worst first."""
    counts, paused, current = tally(store, now, keep), _paused_map(store), current_sender(store)
    global_pause = (store.read("settings") or {}).get("sending_paused")
    rows = []
    for key, c in counts.items():
        r = {"sender": key, **c, "rate_7": _rate(c["bounced_7"], c["sent_7"]), "rate_30": _rate(c["bounced_30"], c["sent_30"]),
             "current": c["account"] == "mcn" and key == current}
        pause = paused.get(key) or (dict(global_pause, scope="all") if r["current"] and global_pause else None)
        r["paused"] = {"reason": pause.get("reason") or "", "at": pause.get("at") or "", "scope": pause.get("scope") or "mailbox"} if pause else None
        r["level"] = level(r, bool(pause))
        rows.append(r)
    order = {"red": 0, "amber": 1, "green": 2, "none": 3}
    return sorted(rows, key=lambda r: (order[r["level"]], -r["rate_7"], -r["sent_30"], r["sender"]))


def thresholds() -> dict:
    min_sends, complaint_rate = _limits()
    return {"warn_rate": WARN_RATE, "pause_rate": PAUSE_RATE, "min_sends": min_sends, "complaint_rate": complaint_rate, "windows": list(WINDOWS)}


def multi_mailbox(store, now: Optional[datetime] = None) -> bool:
    """More than one of Mas Callnet's mailboxes sent in the last 7 days."""
    return sum(1 for c in tally(store, now, is_mcn).values() if c["sent_7"]) > 1


def pause_unhealthy(store, now: Optional[datetime] = None) -> Optional[str]:
    """Pause each of Mas Callnet's mailboxes over the limit (multi-mailbox mode). Returns the reason text, or None."""
    now = now or datetime.now(timezone.utc)
    min_sends, complaint_rate = _limits()
    paused, texts = _paused_map(store), []
    for key, c in tally(store, now, is_mcn).items():
        if key in paused or c["sent_7"] < min_sends:
            continue
        if _rate(c["bounced_7"], c["sent_7"]) > PAUSE_RATE or _rate(c["complaints_7"], c["sent_7"]) > complaint_rate:
            text = (f"{key}: {c['bounced_7']} bounces and {c['complaints_7']} complaints from {c['sent_7']} emails in 7 days. "
                    "Check the list quality, then resume this mailbox on the Deliverability card.")
            paused[key] = {"reason": text, "at": now.isoformat(timespec="seconds"), "sent_7": c["sent_7"], "bounced_7": c["bounced_7"]}
            texts.append(text)
    if texts:
        store.update("settings", lambda s: {**s, SETTING: {**(s.get(SETTING) or {}), **{k: v for k, v in paused.items()}}})
    return " ".join(texts) or None


def mailbox_pause(store) -> Optional[dict]:
    """The pause on the mailbox Mas Callnet sends from now (sequence_runner.sending_paused uses it), or None."""
    current = current_sender(store)
    pause = _paused_map(store).get(current) if current else None
    return {"reason": pause.get("reason") or "", "at": pause.get("at") or "", "resume_at": None, "mailbox": current} if pause else None


def resume(store, sender: str) -> str:
    """An owner's click: lift that mailbox's pause ('mailbox'), or the global pause when it is the current mailbox and
    the global pause is what holds it ('all'). '' when nothing was paused."""
    key, done = _addr(sender), {}

    def apply(s: dict) -> dict:
        boxes = dict(s.get(SETTING) or {})
        if key in boxes:
            boxes.pop(key)
            done["what"] = "mailbox"
            return {**s, SETTING: boxes}
        if s.get("sending_paused") and key and key == current:
            done["what"] = "all"
            return {k: v for k, v in s.items() if k != "sending_paused"}
        return s
    current = current_sender(store)
    store.update("settings", apply)
    return done.get("what", "")


def resume_current(store) -> None:
    """The Outbox page's "Resume sending" also lifts the pause on the mailbox in use."""
    current = current_sender(store)
    if current and current in _paused_map(store):
        store.update("settings", lambda s: {**s, SETTING: {k: v for k, v in (s.get(SETTING) or {}).items() if k != current}})
