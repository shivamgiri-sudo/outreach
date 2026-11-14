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

Switch: settings['email_gate'] = {enabled, approve_risky}. When `enabled` was never saved the default is
default_enabled(), which is OFF unless env OUTREACH_EMAIL_GATE_DEFAULT is '1': the gate ships switched off so deploying it changes no sending, and an owner switches it on from the
Outbox card (the saved setting wins) once an email verifier is connected. Tests also set OUTREACH_EMAIL_GATE_DEFAULT=0 (tests/conftest.py); the new tests switch the gate on through settings.
"""
import os
from typing import Optional

from . import campaign_sources as src
from .hygiene import business_domain, is_role_address, normalise_email
from .lead_model import now_iso

NO_VERIFIER = "no email verifier is connected"
GATE_NOTES = ("the email address is waiting for a mailbox check", NO_VERIFIER, "the email address is risky and waits for a manager's approval")
STATUSES = ("valid", "invalid", "risky")


def default_enabled() -> bool:
    """Read the env var each time, so a test can flip it."""
    return os.environ.get("OUTREACH_EMAIL_GATE_DEFAULT", "0") == "1"


def settings_of(store) -> dict:
    saved = (store.read("settings") or {}).get("email_gate") or {}
    return {"enabled": bool(saved["enabled"]) if "enabled" in saved else default_enabled(),
            "approve_risky": bool(saved.get("approve_risky", False))}


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


def _check_status(lead: dict) -> str:
    """valid / invalid / risky when the lead carries a check made for its current address, else 'unverified'."""
    check = lead.get("email_check") or {}
    mine = str(check.get("email") or "").strip().lower()
    if mine and mine != str(lead.get("email") or "").strip().lower():
        return "unverified"
    return check.get("status") if check.get("status") in STATUSES else "unverified"


def decision(lead: dict, verifier_ready: bool, approve_risky: bool = False) -> tuple:
    """('send'|'check'|'hold'|'block', reason in plain words)."""
    status = _check_status(lead)
    if status == "valid":
        return "send", "a mailbox check says this address works"
    if status == "invalid":
        return "block", "the email address failed a mailbox check"
    if status == "risky":
        if (lead.get("email_check") or {}).get("approved") or approve_risky:
            return "send", "a manager approved this risky address"
        return "hold", GATE_NOTES[2]
    if verifier_ready:
        return "check", GATE_NOTES[0]
    return "hold", NO_VERIFIER


def for_run(store, live: bool) -> Optional[dict]:
    """None when the gate does not apply to this run (practice, or switched off); else what decide() needs."""
    cfg = settings_of(store)
    if not live or not cfg["enabled"]:
        return None
    return {"ready": verifier(store)["supported"], "approve_risky": cfg["approve_risky"]}


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


def check_pending(store, transports: dict, limit: int = 100, lead_ids: Optional[set] = None, background: bool = False) -> dict:
    """Verify up to `limit` leads waiting for a first email whose address needs a check. Never loops: a rejected key stops the run.
    `background` is True only for the timer thread: it allows the built-in checker's optional SMTP step (never on a request)."""
    info = verifier(store)
    out = {"checked": 0, "valid": 0, "invalid": 0, "risky": 0, "unverified": 0, "error": ""}
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
        budget = time.monotonic() + (120 if background else 20)       # DNS can be slow: stop and carry on next run
        builtin = {"budget": budget, "history": email_checker.build_history(store), "smtp": smtp, "check": email_checker.check}
    patches, failures = {}, 0
    for lead in store.read("leads"):
        if out["checked"] >= limit:
            break
        if lead_ids is not None and lead["id"] not in lead_ids:
            continue
        if not waiting(lead) or lead.get("stage") in ("unsubscribed", "disqualified") or _check_status(lead) != "unverified":
            continue
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
    out = {"waiting_for_check": 0, "valid": 0, "invalid": 0, "risky": 0, "unverified": 0, "held": 0}
    for lead in leads:
        if not lead.get("email"):
            continue
        status = _check_status(lead)
        out[status] += 1
        if waiting(lead) and cfg["enabled"]:
            what, _ = decision(lead, info["supported"], cfg["approve_risky"])
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
