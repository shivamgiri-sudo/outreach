"""Verify before the first email: a live first email only goes to an address a mailbox check said is good.

Why: many emails bounced because leads that came from CSV imports, prospects, sheets or Meta never had a mailbox
check (only campaign runs make one, and only when a verifier is connected). The sequence runner used to send to
anything not marked 'invalid'.

Rules (decision()):
  valid                              -> send
  invalid                            -> block (as before)
  risky                              -> hold, unless a manager approved this address (POST /api/leads/{id}/email-approve),
                                        or settings.email_gate.approve_risky is on
  unverified / never checked / the check was made for a different address
                                     -> 'check' when a supported verifier is connected (the background timer or
                                        "Check now" does it), else 'hold': "no email verifier is connected"
The gate applies only to LIVE sends, only to a lead's FIRST email (a lead that already got a real email keeps its
sequence) and only when switched on. A held lead is looked at again on every run and never counts against the
day's or the run's send caps.

Fresh checks (item "re-verify stale addresses"): with settings.email_gate.recheck_enabled (default ON) a check older than
recheck_days (default 90) counts as 'unverified' again. A first email then waits for a new check as above; a LATER step of
a running sequence waits too ('check'), but only while a supported verifier is connected, so sequences never stall when
nothing could do the check. check_pending() re-checks those leads with the same verifier (no new SMTP traffic beyond what
the verifier / built-in checker already does), and recheck_stale() is the owner's "Re-check now (max 500)" run.

Switch: settings['email_gate'] = {enabled, approve_risky, recheck_enabled, recheck_days}. When `enabled` was never saved the default is
default_enabled(), which is OFF unless env OUTREACH_EMAIL_GATE_DEFAULT is '1': the gate ships switched off so deploying it changes no sending, and an owner switches it on from the
Outbox card (the saved setting wins) once an email verifier is connected. Tests also set OUTREACH_EMAIL_GATE_DEFAULT=0 (tests/conftest.py); the new tests switch the gate on through settings.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from . import campaign_sources as src
from .hygiene import business_domain, is_role_address, normalise_email
from .lead_model import now_iso

NO_VERIFIER = "no email verifier is connected"
STALE_NOTE = "the email address is waiting for a fresh mailbox check (the last one is too old)"
GATE_NOTES = ("the email address is waiting for a mailbox check", NO_VERIFIER, "the email address is risky and waits for a manager's approval",
              STALE_NOTE)
STATUSES = ("valid", "invalid", "risky")
RECHECK_DAYS = 90
RECHECK_MIN_DAYS, RECHECK_MAX_DAYS = 7, 365
CLOSED_STAGES = ("unsubscribed", "disqualified")


def default_enabled() -> bool:
    """Read the env var each time, so a test can flip it."""
    return os.environ.get("OUTREACH_EMAIL_GATE_DEFAULT", "0") == "1"


def settings_of(store) -> dict:
    saved = (store.read("settings") or {}).get("email_gate") or {}
    return {"enabled": bool(saved["enabled"]) if "enabled" in saved else default_enabled(),
            "approve_risky": bool(saved.get("approve_risky", False)),
            "recheck_enabled": bool(saved.get("recheck_enabled", True)), "recheck_days": recheck_days(saved.get("recheck_days"))}


def recheck_days(value) -> int:
    try:
        return max(RECHECK_MIN_DAYS, min(RECHECK_MAX_DAYS, int(value)))
    except (TypeError, ValueError):
        return RECHECK_DAYS


def stale_days_of(cfg: dict) -> Optional[int]:
    return cfg["recheck_days"] if cfg.get("recheck_enabled") else None


def _when(text) -> Optional[datetime]:
    try:
        at = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def is_stale(lead: dict, days: int, now: Optional[datetime] = None, undated: bool = False) -> bool:
    """The lead has an address whose mailbox check is missing, made for another address or older than `days`. A check
    without a date (older data) counts as stale only when `undated` is True (the owner's count and "Re-check now"); the
    send gate leaves it alone, so switching the rule on never holds a whole old database at once."""
    email = str(lead.get("email") or "").strip().lower()
    if not email:
        return False
    check = lead.get("email_check") or {}
    if str(check.get("email") or email).strip().lower() != email or check.get("status") not in STATUSES:
        return True
    at = _when(check.get("at"))
    if at is None:
        return undated
    return (now or datetime.now(timezone.utc)) - at > timedelta(days=days)


def builtin_on(store) -> bool:
    """The owner chose the built-in checker (default off). Only used while no paid verifier is connected."""
    return bool(((store.read("settings") or {}).get("email_gate") or {}).get("builtin", False))


def verifier(store) -> dict:
    """{connected, provider, supported, config}: config is what campaign_pipeline._verify_one needs (None when unusable)."""
    config = src.verifier_config(store.read("integrations"))
    if not config:
        if builtin_on(store):             # no paid verifier: the owner switched on the built-in checker (app/email_checker.py)
            return {"connected": True, "provider": "builtin", "supported": True, "builtin": True, "config": {"builtin": True}}
        return {"connected": False, "provider": "", "supported": False, "config": None}
    usable = config if config["supported"] else None
    if usable is None:
        from .sources import registry                     # Kickbox, MillionVerifier, Bouncer, Emailable...
        spec = registry.BY_ID.get(config["provider"])
        if spec is not None and spec.verify is not None:
            usable = {**config, "supported": True, "spec": spec}
    return {"connected": True, "provider": config["provider"], "supported": usable is not None, "config": usable}


def _check_status(lead: dict, stale_days: Optional[int] = None, now: Optional[datetime] = None) -> str:
    """valid / invalid / risky when the lead carries a check made for its current address, else 'unverified'. With
    `stale_days`, a valid or risky check older than that counts as 'unverified' (an invalid address stays invalid)."""
    check = lead.get("email_check") or {}
    mine = str(check.get("email") or "").strip().lower()
    if mine and mine != str(lead.get("email") or "").strip().lower():
        return "unverified"
    status = check.get("status") if check.get("status") in STATUSES else "unverified"
    if stale_days and status in ("valid", "risky") and is_stale(lead, stale_days, now):
        return "unverified"
    return status


def decision(lead: dict, verifier_ready: bool, approve_risky: bool = False, stale_days: Optional[int] = None,
             now: Optional[datetime] = None) -> tuple:
    """('send'|'check'|'hold'|'block', reason in plain words)."""
    status = _check_status(lead, stale_days, now)
    if status == "valid":
        return "send", "a mailbox check says this address works"
    if status == "invalid":
        return "block", "the email address failed a mailbox check"
    if status == "risky":
        if (lead.get("email_check") or {}).get("approved") or approve_risky:
            return "send", "a manager approved this risky address"
        return "hold", GATE_NOTES[2]
    if verifier_ready:
        return "check", STALE_NOTE if (lead.get("email_check") or {}).get("status") in ("valid", "risky") else GATE_NOTES[0]
    return "hold", NO_VERIFIER


def needs_recheck(lead: dict, gate: dict, now: Optional[datetime] = None) -> bool:
    """A LATER step of a running sequence waits for a fresh check: only while the rule is on and a verifier can do it."""
    return bool(gate.get("stale_days") and gate.get("ready") and is_stale(lead, gate["stale_days"], now))


def for_run(store, live: bool) -> Optional[dict]:
    """None when the gate does not apply to this run (practice, or switched off); else what decide() needs."""
    cfg = settings_of(store)
    if not live or not cfg["enabled"]:
        return None
    return {"ready": verifier(store)["supported"], "approve_risky": cfg["approve_risky"], "stale_days": stale_days_of(cfg)}


def is_first_email(lead: dict) -> bool:
    return not (lead.get("sequence") or {}).get("sent_days")


def waiting(lead: dict) -> bool:
    """An enrolled, running lead with an address that has not had its first real email."""
    seq = lead.get("sequence")
    return bool(seq and not seq.get("stopped") and lead.get("email") and is_first_email(lead))


def clear_stale_note(store, lead: dict) -> None:
    """The lead passed the gate now: take off the gate's own 'blocked' note (other notes are left alone)."""
    if ((lead.get("sequence") or {}).get("blocked")) in GATE_NOTES:
        store.update_lead(lead["id"], lambda r: {**r, "sequence": {k: v for k, v in r["sequence"].items() if k != "blocked"}})


def wants_check(lead: dict, stale_days: Optional[int], now: Optional[datetime] = None) -> bool:
    """A running lead whose address needs a (fresh) check before its next email."""
    if lead.get("stage") in CLOSED_STAGES:
        return False
    if waiting(lead):
        return _check_status(lead, stale_days, now) == "unverified"
    seq = lead.get("sequence")
    return bool(stale_days and seq and not seq.get("stopped") and lead.get("email")
                and (lead.get("email_check") or {}).get("status") != "invalid" and is_stale(lead, stale_days, now))


def check_pending(store, transports: dict, limit: int = 100, lead_ids: Optional[set] = None, background: bool = False,
                  select: Optional[Callable] = None, budget_seconds: Optional[int] = None, builtin_fallback: bool = False,
                  progress: Optional[Callable] = None, now: Optional[datetime] = None) -> dict:
    """Verify up to `limit` leads whose address needs a check before their next email (wants_check, or `select(lead)`).
    Never loops: a rejected key stops the run. `background` is True only for the timer thread: it allows the built-in
    checker's optional SMTP step (never on a request, never for recheck_stale). `builtin_fallback` uses the free built-in
    checker (no SMTP) when no verifier is usable."""
    info = verifier(store)
    out = {"checked": 0, "valid": 0, "invalid": 0, "risky": 0, "unverified": 0, "error": ""}
    if not info["supported"] and builtin_fallback:
        info = {"connected": True, "provider": "builtin", "supported": True, "builtin": True, "config": {"builtin": True}}
    if not info["supported"]:
        out["error"] = NO_VERIFIER if not info["connected"] else (
            f"'{info['provider'] or 'unnamed'}' cannot be called automatically yet (ZeroBounce, NeverBounce, Kickbox, MillionVerifier, Bouncer and Emailable can).")
        _status(store, out)
        return out
    from .campaign_pipeline import _verify_one
    builtin = None
    if info.get("builtin"):
        from . import email_checker
        from .senders import email_ready
        smtp = email_checker.smtp_step((email_ready(store) or {}).get("from_email", "")) if background else None
        import time
        budget = time.monotonic() + (budget_seconds or (120 if background else 20))   # DNS can be slow: stop, carry on next run
        builtin = {"budget": budget, "history": email_checker.build_history(store), "smtp": smtp, "check": email_checker.check}
    patches, failures = {}, 0
    stale = stale_days_of(settings_of(store))
    now = now or datetime.now(timezone.utc)
    pick = select or (lambda lead: wants_check(lead, stale, now))
    for lead in store.read("leads"):
        if out["checked"] >= limit:
            break
        if lead_ids is not None and lead["id"] not in lead_ids:
            continue
        if not pick(lead):
            continue
        if progress:
            progress(out["checked"])
        email = lead["email"]
        check = {"email": email, "syntax_ok": bool(normalise_email(email)), "business": bool(business_domain(email)),
                 "role": is_role_address(email), "provider": "", "at": now_iso()}
        if builtin is not None:
            import time
            if time.monotonic() > builtin["budget"]:
                break
            out["checked"] += 1
            verdict = builtin["check"](email, builtin["history"], smtp=builtin["smtp"], company_lead=bool(lead.get("company")))
            if verdict["status"] == "unverified":          # DNS did not answer: no verdict, try again next run
                out["unverified"] += 1
                continue
            check.update({k: v for k, v in verdict.items() if k != "status"})
            status = verdict["status"]
        elif not check["syntax_ok"]:
            status = "invalid"
        else:
            try:
                out["checked"] += 1
                status, check["provider"] = _verify_one(store, transports, info["config"], email), info["provider"]
            except (src.KeyRejected, PermissionError) as exc:
                out["error"] = f"Stopped: {str(exc) or 'the verifier rejected the API key'}."
                break
            except (OSError, ValueError):
                failures += 1
                continue
        check["status"] = status
        out[status] += 1
        patches[lead["id"]] = {"set": {"email_check": check}, "events": []}
    if patches:
        store.patch_leads(patches)
    if failures and not out["error"]:
        out["error"] = f"{failures} check(s) failed; those addresses will be tried again."
    _status(store, out)
    return out


def _status(store, out: dict) -> None:
    row = {"at": now_iso(), "error": out["error"], "checked": out["checked"]}
    store.update("settings", lambda s: {**s, "email_gate_status": row})


def counts(store, leads: list) -> dict:
    """Plain numbers over the given leads (the caller already cut them to what the person may see)."""
    cfg, info = settings_of(store), verifier(store)
    stale, now = stale_days_of(cfg), datetime.now(timezone.utc)
    out = {"waiting_for_check": 0, "valid": 0, "invalid": 0, "risky": 0, "unverified": 0, "held": 0, "stale": 0}
    for lead in leads:
        if not lead.get("email"):
            continue
        status = _check_status(lead)
        out[status] += 1
        if lead.get("stage") not in CLOSED_STAGES and status != "invalid" and is_stale(lead, cfg["recheck_days"], now, undated=True):
            out["stale"] += 1                  # "N addresses not checked in 90 days" (missing or older checks)
        if waiting(lead) and cfg["enabled"]:
            what, _ = decision(lead, info["supported"], cfg["approve_risky"], stale, now)
            if what == "check":
                out["waiting_for_check"] += 1
            if what in ("check", "hold"):
                out["held"] += 1
    return out


def approve(store, lead_id: str, user: dict) -> dict:
    """A manager approves a risky address. Raises ValueError (plain words) for an invalid one."""
    from . import audit
    from .lead_model import event
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None:
        raise LookupError("Lead not found")
    status = _check_status(lead)
    if status == "invalid":
        raise ValueError("This address failed a mailbox check and cannot be approved. Fix the address instead.")
    if status != "risky":
        raise ValueError("Only an address the mailbox check marked risky needs approval.")
    who = audit.actor_of(user)
    mark = {**(lead.get("email_check") or {}), "approved": True, "approved_by": who["name"], "approved_at": now_iso()}
    store.patch_leads({lead_id: {"set": {"email_check": mark}, "events": [event("note", f"Risky email address approved for sending by {who['name']}")]}})
    audit.record(store, user, "email_gate.approve", lead.get("company") or lead_id, f"risky address approved (lead {lead_id})")
    return {"ok": True, "lead_id": lead_id}


RECHECK_MAX = 500


def stale_select(cfg: dict, now: Optional[datetime] = None) -> Callable:
    """The owner's "Re-check now": open leads whose address was not checked within recheck_days (invalid ones stay invalid)."""
    now = now or datetime.now(timezone.utc)
    return lambda lead: (lead.get("stage") not in CLOSED_STAGES and (lead.get("email_check") or {}).get("status") != "invalid"
                         and is_stale(lead, cfg["recheck_days"], now, undated=True))


def recheck_stale(store, transports: dict, lead_ids: set, progress: Optional[Callable] = None) -> dict:
    """Re-check up to RECHECK_MAX stale addresses with the connected verifier, else the free built-in checker (no SMTP)."""
    return check_pending(store, transports, RECHECK_MAX, lead_ids=lead_ids, select=stale_select(settings_of(store)),
                         budget_seconds=600, builtin_fallback=True, progress=progress)
