"""Campaigns: saved targeting + sources + rules, and the one-click run that fills, cleans and starts a lead list.

A run: discover (provider APIs, careers pages, attached imports) -> normalise -> dedupe against existing leads and the
suppression list -> add new leads tagged with the campaign -> enrich -> verify -> score -> qualify -> optionally enrol
qualifying leads through the same function as the Enrol button. Sending stays with the sequence engine (caps, warm-up,
business hours, one email per domain per day, suppression, practice mode). The caller gets a notification after each
run. A dry run discovers and scores but saves nothing.
"""
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import campaign_pipeline as pipe
from . import campaign_sources as src
from .activities import caller_name
from .lead_model import now_iso
from .providers import provider_status
from .reminders import notify
from .routes_outreach import EnrollRefused, enroll_lead
from .sources import registry as source_registry, runner as source_runner, waterfall as source_waterfall
from .sequences import IST, in_business_hours

STATUSES = ("draft", "running", "paused", "done")
MAX_RUNS_KEPT = 30
MAX_PREVIEW = 200
MAX_LEAD_IDS = 500
pipe.SOURCE_LABELS.update({k: v for k, v in source_registry.labels().items() if k not in pipe.SOURCE_LABELS})
_LOCKS: dict = {}
_LOCKS_GUARD = threading.Lock()


class CampaignBusy(RuntimeError):
    """This campaign is already running."""


def _lock(campaign_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(campaign_id, threading.Lock())


# ---------- saved campaigns ----------
def list_campaigns(store) -> list:
    return [summary(c) for c in reversed(store.read("campaigns"))]


def summary(campaign: dict) -> dict:
    """A campaign without its run history, plus the last run's numbers."""
    runs = campaign.get("runs") or []
    return {**{k: v for k, v in campaign.items() if k != "runs"}, "last_run": runs[-1] if runs else None, "run_count": len(runs)}


def get_campaign(store, campaign_id: str) -> Optional[dict]:
    return next((c for c in store.read("campaigns") if c["id"] == campaign_id), None)


def create_campaign(store, spec: dict) -> dict:
    campaign = {"id": uuid.uuid4().hex[:10], **spec, "status": "draft", "created_at": now_iso(), "updated_at": now_iso(),
                "stats": {"runs": 0, "leads_added": 0, "enrolled": 0, "last_run_at": None}, "runs": [], "last_run_date": None}
    store.update("campaigns", lambda rows: [*rows, campaign])
    return campaign


def _change(store, campaign_id: str, fn) -> Optional[dict]:
    found = {}

    def apply(rows: list) -> list:
        out = []
        for c in rows:
            if c["id"] == campaign_id:
                c = fn(c)
                found["c"] = c
            out.append(c)
        return out
    store.update("campaigns", apply)
    return found.get("c")


SECTIONS = ("target", "sources", "enrich", "rules")


def update_campaign(store, campaign_id: str, changes: dict) -> Optional[dict]:
    """Apply the given name / status and the given keys of each section; everything else keeps its saved value."""
    def fn(c: dict) -> dict:
        merged = {**c, **{k: v for k, v in changes.items() if k in ("name", "status") and v is not None}}
        for section in SECTIONS:
            if isinstance(changes.get(section), dict):
                merged[section] = {**(c.get(section) or {}), **changes[section]}
        return {**merged, "updated_at": now_iso()}
    return _change(store, campaign_id, fn)


def set_status(store, campaign_id: str, status: str) -> Optional[dict]:
    if status not in STATUSES:
        raise ValueError(f"Status must be one of {', '.join(STATUSES)}")
    return _change(store, campaign_id, lambda c: {**c, "status": status, "updated_at": now_iso()})


def delete_campaign(store, campaign_id: str) -> bool:
    """Remove the campaign. Its leads stay (tagged with the old campaign id) with everything they went through."""
    before = len(store.read("campaigns"))
    return len(store.update("campaigns", lambda rows: [c for c in rows if c["id"] != campaign_id])) < before


def campaign_leads(store, campaign_id: str) -> list:
    return [l for l in store.read("leads") if l.get("campaign_id") == campaign_id]


def sources_status(store) -> dict:
    """Which sources and enrichers can run now, and why not (shown in the campaign wizard)."""
    saved = store.read("integrations")

    def state(pid: str) -> dict:
        s = provider_status(saved, pid)
        return {"connected": s["configured"], "reason": s["reason"]}
    places = saved.get("google_places") or {}
    places_ok = places.get("mode") == "api" and bool((places.get("values") or {}).get("api_key"))
    verifier = src.verifier_config(saved)
    return {"apollo": state("apollo"), "hunter": state("hunter"), "firecrawl": state("firecrawl"),
            "crawler": {"connected": True, "reason": ""},
            "google_places": {"connected": places_ok, "reason": "" if places_ok else "Add a Places API key under Integrations > Lead data."},
            "email_verifier": {"connected": bool(verifier and verifier["supported"]),
                               "reason": "" if verifier and verifier["supported"] else
                               "No ZeroBounce or NeverBounce key: emails are checked for format and business domain only."}}


# ---------- discovery ----------
def _apollo_key(saved: dict, report: dict, source: str) -> Optional[str]:
    status = provider_status(saved, "apollo")
    if not status["configured"]:
        pipe.add_error(report, source, f"Apollo is not connected: {status['reason']}")
        return None
    return status["config"].get("api_key")


def discover(store, campaign: dict, transports: dict, report: dict, now: datetime, dry_run: bool) -> list:
    """Raw candidate rows from every ticked source. A failing source is reported and the others still run."""
    sources, target = campaign["sources"], campaign["target"]
    limit = sources.get("max_results", 25)
    saved = store.read("integrations")
    rows = []

    def record(source: str, found: list, requests: int = 0) -> None:
        report["sources"][source] = {"found": len(found), "requests": requests}
        report["credits_used_estimate"] += requests
        rows.extend(found)

    state = {"apollo_rejected": False}

    def attempt(source: str, call) -> None:
        try:
            out = call()
            record(source, out["rows"], out["requests"])
            if out.get("error"):
                pipe.add_error(report, source, out["error"])
            if out.get("failed_lookups"):
                pipe.add_error(report, source, f"Job postings could not be read for {out['failed_lookups']} company(ies).")
        except src.KeyRejected as exc:
            state["apollo_rejected"] = state["apollo_rejected"] or source.startswith("apollo")
            pipe.add_error(report, source, str(exc))
        except src.SourceError as exc:
            pipe.add_error(report, source, str(exc))
        except (OSError, ValueError) as exc:
            pipe.add_error(report, source, f"The search failed: {str(exc)[:200]}")

    wants_apollo = sources.get("apollo_search") or sources.get("apollo_hiring")
    key = _apollo_key(saved, report, "apollo_search" if sources.get("apollo_search") else "apollo_hiring") if wants_apollo else None
    if key and sources.get("apollo_search"):
        attempt("apollo_search", lambda: src.apollo_people(target, key, transports["post"], limit))
    if key and sources.get("apollo_hiring") and not state["apollo_rejected"]:
        attempt("apollo_hiring", lambda: src.apollo_hiring(target, key, transports["post"], transports["get"], limit, now))
    if sources.get("careers_pages") or sources.get("careers_html"):
        out = src.careers_postings(sources.get("careers_pages") or [], sources.get("careers_html") or [], transports["fetch"])
        for message in out["errors"]:
            pipe.add_error(report, "careers_page", message)
        record("careers_page", pipe.careers_rows(store, out["postings"], now, dry_run))
    if sources.get("google_places"):
        places = saved.get("google_places") or {}
        places_key = (places.get("values") or {}).get("api_key") if places.get("mode") == "api" else None
        if not places_key:
            pipe.add_error(report, "google_places", "Google Places is not connected. Add a Places API key under Integrations > Lead data.")
        else:
            attempt("google_places", lambda: src.google_places(target, places_key, transports["post"], limit))
    rows += source_runner.discover_rows(store, campaign, transports, report, now, pipe.add_error)   # app/sources: the extra sources
    report["discovered"] = len(rows)
    return rows


# ---------- one run ----------
def new_report(dry_run: bool, trigger: str, now: datetime) -> dict:
    return {"id": uuid.uuid4().hex[:10], "started_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "finished_at": None, "dry_run": dry_run, "trigger": trigger, "discovered": 0, "new": 0, "merged": 0,
            "attached": 0, "duplicates": 0, "suppressed_skipped": 0, "over_cap": 0, "skipped_no_contact": 0,
            "enriched_emails": 0, "enriched_phones": 0, "verified": 0, "verification": {}, "qualified": 0,
            "enrolled": 0, "errors": [], "credits_used_estimate": 0, "sources": {}, "lead_ids": []}


def _preview_rows(plan: dict, campaign: dict) -> list:
    rules, out = campaign["rules"], []
    for item in plan["preview"][:MAX_PREVIEW]:
        lead = item["lead"]
        score = pipe.present_score(lead)["score"] if item["would"] in ("add", "merge") else None
        reasons = pipe.qualify_reasons(lead, rules, score) if score is not None else []
        if reasons and not lead.get("email"):
            reasons = [r if r != "no email found yet" else "no email yet (the real run looks for one)" for r in reasons]
        out.append({"company": lead.get("company", ""), "contact_name": lead.get("contact_name", ""), "email": lead.get("email") or "",
                    "domain": lead.get("domain") or "", "phone": lead.get("phone") or "", "role": lead.get("role") or "",
                    "sources": [pipe.SOURCE_LABELS.get(s, s) for s in item["sources"]], "would": item["would"], "score": score,
                    "qualified": score is not None and not reasons, "reasons": reasons,
                    "would_enrol": bool(rules.get("auto_enrol")) and score is not None and not reasons})
    return out


def _enrol(store, campaign: dict, ids: list, report: dict) -> None:
    refused = {}
    for lead_id in ids:
        try:
            enroll_lead(store, lead_id, note=f"Enrolled in email sequence by campaign {campaign['name']}")
            report["enrolled"] += 1
        except (EnrollRefused, LookupError) as exc:
            refused[str(exc)] = refused.get(str(exc), 0) + 1
    for reason, n in refused.items():
        pipe.add_error(report, "sequence", f"{n} not started: {reason}")


def _notify_caller(store, campaign: dict, report: dict, sender) -> None:
    caller = (campaign["rules"].get("caller") or "").strip() or caller_name(store)
    lines = [f"Found {report['discovered']} rows; {report['new']} new leads added, {report['merged']} existing leads completed, "
             f"{report['duplicates']} already known, {report['suppressed_skipped']} skipped (unsubscribed, bounced or disqualified).",
             f"New emails found: {report['enriched_emails']}. Qualified: {report['qualified']}. "
             f"Started in the email sequence: {report['enrolled']}.",
             "Emails go out only through the sequence: practice or live mode, daily caps, warm-up and business hours apply."]
    lines += [f"{e['source']}: {e['message']}" for e in report["errors"][:5]]
    notify(store, caller, f"Campaign {campaign['name']}: {report['new']} new leads, {report['enrolled']} enrolled",
           "\n".join(lines), kind="campaign", sender=sender)


def _save_run(store, campaign_id: str, report: dict) -> None:
    kept = {k: v for k, v in report.items() if k != "preview"}

    def fn(c: dict) -> dict:
        stats = c.get("stats") or {}
        return {**c, "runs": [*(c.get("runs") or []), kept][-MAX_RUNS_KEPT:],
                "stats": {"runs": stats.get("runs", 0) + 1, "leads_added": stats.get("leads_added", 0) + report["new"],
                          "enrolled": stats.get("enrolled", 0) + report["enrolled"], "last_run_at": report["finished_at"]}}
    _change(store, campaign_id, fn)


def run_campaign(store, campaign_id: str, transports: dict, dry_run: bool = False, now: Optional[datetime] = None,
                 trigger: str = "manual", notify_sender=None) -> dict:
    """Run the pipeline once. Raises LookupError (no such campaign) or CampaignBusy (a run is in progress)."""
    now = now or datetime.now(timezone.utc)
    if get_campaign(store, campaign_id) is None:
        raise LookupError("Campaign not found")
    lock = _lock(campaign_id)
    if not lock.acquire(blocking=False):
        raise CampaignBusy("This campaign is already running. Wait for it to finish.")
    try:
        today = now.astimezone(IST).date().isoformat()
        if trigger == "auto" and get_campaign(store, campaign_id).get("last_run_date") == today:
            raise CampaignBusy("This campaign already ran today.")
        if not dry_run:
            _change(store, campaign_id, lambda c: {**c, "last_run_date": today})
        campaign = get_campaign(store, campaign_id)
        report = new_report(dry_run, trigger, now)
        candidates = pipe.merge_rows(discover(store, campaign, transports, report, now, dry_run))
        plan = pipe.plan_additions(store, campaign, candidates, report)
        if dry_run:
            report["preview"] = _preview_rows(plan, campaign)
            report["new"] = sum(1 for p in plan["preview"] if p["would"] == "add")
            report["merged"] = len(plan["merge"])
            report["qualified"] = sum(1 for p in report["preview"] if p["qualified"])
            report["enrolled"] = sum(1 for p in report["preview"] if p["would_enrol"])
            report["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return report
        added = store.add_leads(plan["add"])
        report["new"] = len(added)
        report["duplicates"] += len(plan["add"]) - len(added)        # taken by another import in the meantime
        report["merged"] = sum(pipe.merge_into_existing(store, lead_id, cand, campaign) for lead_id, cand in plan["merge"])
        if campaign["sources"].get("csv_import_ids"):
            pipe.attach_leads(store, campaign, campaign["sources"]["csv_import_ids"], report)
        pipe.enrich(store, campaign, transports, report)
        source_waterfall.enrich_campaign(store, campaign, transports, report, pipe.scope, pipe.add_error, now)
        pipe.verify(store, campaign, transports, report)
        qualified = pipe.qualify(store, campaign, report)
        if campaign["rules"].get("auto_enrol"):
            _enrol(store, campaign, qualified, report)
        report["lead_ids"] = [l["id"] for l in added][:MAX_LEAD_IDS]
        report["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _save_run(store, campaign_id, report)
        _notify_caller(store, campaign, report, notify_sender)
        return report
    finally:
        lock.release()


# ---------- background timer ----------
def run_due_campaigns(store, now: datetime, transports: dict, notify_sender=None, ignore_hours: bool = False) -> list:
    """Run each campaign marked 'running' at most once per IST day, Mon–Fri 09:00–18:00 IST only."""
    if not ignore_hours and not in_business_hours(now):
        return []
    today = now.astimezone(IST).date().isoformat()
    ran = []
    for campaign in store.read("campaigns"):
        if campaign.get("status") != "running" or campaign.get("last_run_date") == today:
            continue
        try:
            report = run_campaign(store, campaign["id"], transports, now=now, trigger="auto", notify_sender=notify_sender)
            ran.append({"campaign_id": campaign["id"], "new": report["new"], "enrolled": report["enrolled"]})
        except (CampaignBusy, LookupError):
            continue
    return ran
