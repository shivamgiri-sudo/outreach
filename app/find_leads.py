"""One click: find new decision makers and fill in the contacts of leads you already have.

"Find" runs a saved campaign called "One-click: find & enrich" (Apollo people search, Google Places and any careers pages
given, then the usual dedupe, enrich, verify and qualify steps). "Fill in" walks early-stage leads that have no email
(app/find_enrich.py). Compliant sources only: provider APIs the owner connected, company websites through the polite
robots.txt-respecting crawler, careers pages (JSON-LD). LinkedIn, Naukri and Indeed pages are never fetched.

A run works in a background thread and reports progress; only one run at a time. If the app restarts mid-run the run is
marked interrupted; leads already checked are stamped, so running again carries on with the rest. A preview (dry run)
saves no leads and no campaign and makes no lookups for existing leads.
"""
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import campaign_pipeline as pipe
from . import campaigns
from . import find_enrich as fe
from .campaign_sources import DEFAULT_PERSON_TITLES, verifier_config
from .providers import provider_status
from .routes_campaigns import MAX_ITEM_CHARS, MAX_ITEMS

log = logging.getLogger(__name__)
CAMPAIGN_NAME = "One-click: find & enrich"
MODES = ("find_new", "enrich_existing", "both")
DEFAULT_MAX_NEW, DEFAULT_MAX_ENRICH, DEFAULT_MAX_CREDITS = 25, 30, 40
CAP_NEW, CAP_ENRICH, CAP_CREDITS = 100, 100, 500
MAX_RUNS_KEPT = 20
PERSIST_EVERY = 5
LIVE_STATES = ("queued", "running")
QUALIFY_RULES = {"min_score": 40, "require_business_email": True, "skip_role_addresses": False}


class Busy(RuntimeError):
    """A run is already working."""


def _clean(values) -> list:
    return list(dict.fromkeys(str(v).strip()[:MAX_ITEM_CHARS] for v in values or [] if str(v).strip()))[:MAX_ITEMS]


def _clamp(value, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def normalise(body: dict) -> dict:
    mode = body.get("mode") or "both"
    if mode not in MODES:
        raise ValueError(f"mode must be one of: {', '.join(MODES)}")
    return {"mode": mode, "cities": _clean(body.get("cities")), "titles": _clean(body.get("titles")) or list(DEFAULT_PERSON_TITLES),
            "industries": _clean(body.get("industries")), "careers_pages": _clean(body.get("careers_pages")),
            "max_new": _clamp(body.get("max_new"), DEFAULT_MAX_NEW, 0, CAP_NEW),
            "max_enrich": _clamp(body.get("max_enrich"), DEFAULT_MAX_ENRICH, 0, CAP_ENRICH),
            "max_credits": _clamp(body.get("max_credits"), DEFAULT_MAX_CREDITS, 0, CAP_CREDITS), "dry_run": bool(body.get("dry_run"))}


# ---------- what can run now ----------
def connections(store) -> dict:
    status = campaigns.sources_status(store)
    return {pid: status[pid] for pid in ("apollo", "hunter", "firecrawl", "google_places", "crawler", "email_verifier")}


def plan_find(store, params: dict) -> dict:
    """The campaign this run would use, how many new leads fit the credit limit, and what cannot run (in plain words)."""
    status, problems = connections(store), []
    apollo, places = status["apollo"]["connected"], status["google_places"]["connected"] and bool(params["industries"])
    hunter = status["hunter"]["connected"]
    budget = params["max_credits"] if params["mode"] == "find_new" else params["max_credits"] // 2
    searches = (1 if apollo else 0)
    per_lead = (1 if apollo else 0) + (1 if hunter else 0) + (1 if status["email_verifier"]["connected"] else 0)
    cap = params["max_new"]
    if per_lead:
        cap = min(cap, max(0, (budget - searches) // per_lead))
    if status["google_places"]["connected"] and not params["industries"]:
        problems.append("Google Places needs an industry (for example BPO) to search for.")
    if not (apollo or places or params["careers_pages"]):
        problems.append("Nothing to search with. Connect Apollo or Google Places under Integrations, or add careers page links.")
    elif cap == 0:
        problems.append("The credit limit leaves no room for new leads. Raise the credit limit or lower the other limits.")
    spec = {"target": {"industries": params["industries"], "cities": params["cities"], "roles_hiring": [], "company_size_buckets": [],
                       "keywords": [], "person_titles": params["titles"]},
            "sources": {"careers_pages": params["careers_pages"], "careers_html": [], "apollo_search": apollo, "apollo_hiring": False,
                        "google_places": places, "csv_import_ids": [], "max_results": min(100, max(1, cap * 2))},   # ask for extra: some are already known
            "enrich": {"crawler": True, "hunter": hunter, "firecrawl": False, "apollo_match": apollo and budget > searches},
            "rules": {**QUALIFY_RULES, "max_new_leads_per_run": max(1, cap), "auto_enrol": False, "caller": ""}}
    return {"spec": spec, "new_cap": cap, "problems": problems, "runnable": not problems, "status": status,
            "searches": [n for n, on in (("Apollo people search", apollo), ("Google Places", places),
                                         ("Careers pages", bool(params["careers_pages"]))) if on],
            "credits_max": searches + (cap * per_lead if cap else 0)}


def estimate(store, params: dict, now: Optional[datetime] = None) -> dict:
    """What a real run would do and the most credits it could use, shown before the run."""
    find = plan_find(store, params) if params["mode"] != "enrich_existing" else None
    picks = fe.pick_leads(store, params["max_enrich"], now) if params["mode"] != "find_new" else None
    status = connections(store)
    fill_credits = fe.worst_case_credits(picks["leads"], status) if picks else 0
    find_credits = find["credits_max"] if find and find["runnable"] else 0
    return {"mode": params["mode"], "connections": status, "credit_limit": params["max_credits"],
            "find": None if find is None else {k: find[k] for k in ("new_cap", "problems", "runnable", "searches")},
            "fill": None if picks is None else {"will_check": len(picks["leads"]), "with_website": picks["with_website"],
                                               "name_only": picks["name_only"], "available": picks["available"]},
            "credits_find_max": find_credits, "credits_fill_max": fill_credits,
            "credits_estimate_max": min(params["max_credits"], find_credits + fill_credits),
            "over_limit": find_credits + fill_credits > params["max_credits"]}


# ---------- run records ----------
def _blank(params: dict) -> dict:
    return {"id": uuid.uuid4().hex[:10], "status": "queued", "mode": params["mode"], "dry_run": params["dry_run"], "params": params,
            "phase": "Starting", "started_at": _now_text(), "finished_at": None, "done": 0, "total": 0, "found": 0, "enriched": 0,
            "credits": 0, "credit_limit": params["max_credits"], "errors": [], "report": None, "preview": None}


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_runs(store) -> list:
    try:
        return store.read("find_runs")
    except KeyError:
        return []


def _save(store, run: dict) -> None:
    try:
        store.read("find_runs")
    except KeyError:
        store.write("find_runs", [])
    store.update("find_runs", lambda rows: [*[r for r in rows if r["id"] != run["id"]], run][-MAX_RUNS_KEPT:])


def mark_interrupted(store) -> int:
    """Runs that were working when the app stopped can never finish: say so, so the page does not wait for them."""
    changed = [r for r in read_runs(store) if r.get("status") in LIVE_STATES]
    if changed:
        note = {"source": "Find & enrich", "message": "The app was restarted while this run was working. Run it again: leads "
                                                      "already checked are skipped, so it carries on with the rest."}
        store.update("find_runs", lambda rows: [{**r, "status": "interrupted", "finished_at": _now_text(), "phase": "Interrupted",
                                                 "errors": [*r.get("errors", []), note]} if r.get("status") in LIVE_STATES else r
                                                for r in rows])
    return len(changed)


def summary(run: dict) -> dict:
    return {k: v for k, v in run.items() if k != "preview"}


# ---------- the work ----------
def _qualified_ids(store, ids: list) -> list:
    wanted, suppressed = set(ids), set(store.read("suppressed"))
    out = []
    for lead in store.read("leads"):
        if (lead["id"] in wanted and lead.get("stage") in pipe.EARLY_STAGES and not lead.get("sequence")
                and lead.get("email") not in suppressed and not pipe.qualify_reasons(lead, QUALIFY_RULES, pipe.present_score(lead)["score"])):
            out.append(lead["id"])
    return out


def _ensure_campaign(store, spec: dict) -> dict:
    existing = next((c for c in store.read("campaigns") if c.get("name") == CAMPAIGN_NAME), None)
    if existing is None:
        return campaigns.create_campaign(store, {"name": CAMPAIGN_NAME, **spec})
    return campaigns.update_campaign(store, existing["id"], spec)


def _fill_steps(lead: dict, status: dict) -> list:
    steps = ["Read the website's public contact pages"] if fe.valid_domain(lead.get("domain")) else ["No website known"]
    if status["apollo"]["connected"]:
        steps.append("Apollo: find the HR / talent / founder contact" + ("" if fe.valid_domain(lead.get("domain")) else " (and the website)"))
    for pid, label in (("hunter", "Hunter"), ("firecrawl", "Firecrawl")):
        if status[pid]["connected"] and fe.valid_domain(lead.get("domain")):
            steps.append(f"{label} if still no email")
    return steps


class Runner:
    """Starts runs in the background, one at a time, and answers progress questions."""

    def __init__(self, store, transports: dict, notify_sender=None):
        self.store, self.transports, self.notify_sender = store, transports, notify_sender
        self._slot, self._guard, self._live = threading.Lock(), threading.Lock(), {}
        mark_interrupted(store)

    # --- progress ---
    def _set(self, run_id: str, persist: bool = False, **fields) -> None:
        with self._guard:
            run = self._live[run_id]
            run.update(fields)
            snapshot = dict(run)
        if persist:
            _save(self.store, snapshot)

    def _error(self, run_id: str, source: str, message: str) -> None:
        with self._guard:
            errors = self._live[run_id]["errors"]
            if not any(e["source"] == source and e["message"] == message for e in errors):
                errors.append({"source": source, "message": message})

    def get(self, run_id: str) -> Optional[dict]:
        with self._guard:
            live = self._live.get(run_id)
            if live is not None:
                return {**live, "errors": list(live["errors"])}
        return next((r for r in read_runs(self.store) if r["id"] == run_id), None)

    def active(self) -> Optional[dict]:
        with self._guard:
            live = next((r for r in self._live.values() if r["status"] in LIVE_STATES), None)
            return {**live, "errors": list(live["errors"])} if live else None

    # --- starting ---
    def start(self, params: dict, now: Optional[datetime] = None) -> dict:
        if not self._slot.acquire(blocking=False):
            raise Busy("A find & enrich run is already working. Wait for it to finish.")
        try:
            run = _blank(params)
            with self._guard:
                self._live[run["id"]] = run
            _save(self.store, run)
            threading.Thread(target=self._thread, args=(run["id"], params, now or datetime.now(timezone.utc)), daemon=True).start()
        except BaseException:
            self._slot.release()
            raise
        return summary(run)

    def run_now(self, params: dict, now: Optional[datetime] = None) -> dict:
        """The same run, on the calling thread (tests and scripts)."""
        if not self._slot.acquire(blocking=False):
            raise Busy("A find & enrich run is already working. Wait for it to finish.")
        run = _blank(params)
        with self._guard:
            self._live[run["id"]] = run
        self._thread(run["id"], params, now or datetime.now(timezone.utc))
        return self.get(run["id"])

    def _thread(self, run_id: str, params: dict, now: datetime) -> None:
        try:
            self._set(run_id, status="running", phase="Working")
            report = self._execute(run_id, params, now)
            self._set(run_id, status="done", phase="Finished", report=report, finished_at=_now_text(), persist=True)
        except Exception as exc:                       # the page must always get an answer, never a thread that dies silently
            log.exception("find & enrich run failed")
            self._error(run_id, "Find & enrich", f"The run stopped because of an unexpected problem: {str(exc)[:200]}")
            self._set(run_id, status="failed", phase="Failed", finished_at=_now_text(), persist=True)
        finally:
            self._slot.release()

    # --- phases ---
    def _execute(self, run_id: str, params: dict, now: datetime) -> dict:
        dry = params["dry_run"]
        report = {"mode": params["mode"], "dry_run": dry, "new_leads": 0, "existing_completed": 0, "checked": 0, "emails_found": 0,
                  "phones_found": 0, "decision_makers": 0, "credits_used": 0, "credit_limit": params["max_credits"],
                  "stopped_reason": "", "lead_ids": [], "qualified_ids": [], "campaign_id": None, "searches": [], "errors": []}
        picks = fe.pick_leads(self.store, params["max_enrich"], now)["leads"] if params["mode"] != "find_new" else []
        self._set(run_id, total=(1 if params["mode"] != "enrich_existing" else 0) + len(picks), persist=True)
        found_ids: list = []
        preview: list = []
        spent = 0
        if params["mode"] != "enrich_existing":
            spent, found_ids = self._find(run_id, params, report, preview, now)
        if params["mode"] != "find_new":
            self._fill(run_id, params, picks, set(found_ids), report, preview, spent, now)
        report["lead_ids"] = list(dict.fromkeys(report["lead_ids"]))[:campaigns.MAX_LEAD_IDS]
        if not dry:
            report["qualified_ids"] = _qualified_ids(self.store, report["lead_ids"])
        with self._guard:
            run = self._live[run_id]
            report["errors"] = list(run["errors"])
            report["credits_used"] = run["credits"]
        self._set(run_id, preview=preview if dry else None)
        return report

    def _find(self, run_id: str, params: dict, report: dict, preview: list, now: datetime) -> tuple:
        self._set(run_id, phase="Searching for new decision makers")
        plan = plan_find(self.store, params)
        report["searches"] = plan["searches"]
        for message in plan["problems"]:
            self._error(run_id, "Find new", message)
        if not plan["runnable"]:
            self._set(run_id, done=1, persist=True)
            return 0, []
        spec = plan["spec"]
        if params["dry_run"]:
            campaign = {"id": "one-click-preview", "name": CAMPAIGN_NAME, "status": "draft", **spec}
            base = campaigns.new_report(True, "manual", now)
            candidates = pipe.merge_rows(campaigns.discover(self.store, campaign, self.transports, base, now, True))
            planned = pipe.plan_additions(self.store, campaign, candidates, base)
            rows = campaigns._preview_rows(planned, campaign)
            preview.extend({**r, "kind": "find"} for r in rows)
            report["new_leads"] = sum(1 for p in planned["preview"] if p["would"] == "add")
            report["existing_completed"] = len(planned["merge"])
            self._set(run_id, found=report["new_leads"], enriched=report["existing_completed"], credits=base["credits_used_estimate"],
                      done=1, persist=True)
            for e in base["errors"]:
                self._error(run_id, e["source"], e["message"])
            return base["credits_used_estimate"], []
        campaign = _ensure_campaign(self.store, spec)
        report["campaign_id"] = campaign["id"]
        try:
            run = campaigns.run_campaign(self.store, campaign["id"], self.transports, dry_run=False, now=now, trigger="manual",
                                         notify_sender=self.notify_sender)
        except campaigns.CampaignBusy:
            self._error(run_id, "Find new", "The saved campaign is running right now. Try again in a minute.")
            self._set(run_id, done=1, persist=True)
            return 0, []
        for e in run["errors"]:
            self._error(run_id, e["source"], e["message"])
        report.update(new_leads=run["new"], existing_completed=run["merged"], emails_found=run["enriched_emails"],
                      phones_found=run["enriched_phones"])
        report["lead_ids"] = list(run["lead_ids"])
        spent = run["credits_used_estimate"]
        self._set(run_id, found=run["new"], enriched=run["merged"], credits=spent, done=1, persist=True)
        return spent, [*run["lead_ids"]]

    def _fill(self, run_id: str, params: dict, picks: list, skip: set, report: dict, preview: list, spent: int, now: datetime) -> None:
        status = connections(self.store)
        picks = [l for l in picks if l["id"] not in skip]
        with self._guard:
            base_done = self._live[run_id]["done"]
        if params["dry_run"]:
            preview.extend({"kind": "fill", "lead_id": l["id"], "company": l.get("company", ""), "domain": l.get("domain") or "",
                            "contact_name": l.get("contact_name") or "", "steps": _fill_steps(l, status)} for l in picks)
            self._set(run_id, done=base_done + len(picks), total=base_done + len(picks), persist=True)
            report["checked"] = len(picks)
            return
        self._set(run_id, phase="Filling in contacts for leads you already have")
        saved = self.store.read("integrations")
        ctx = fe.Context(self.transports, {pid: provider_status(saved, pid) for pid in ("apollo", "hunter", "firecrawl")},
                         params["titles"], max(0, params["max_credits"] - spent), {l.get("dedupe_key") for l in self.store.read("leads")},
                         set(self.store.read("suppressed")), set(self.store.read("suppressed_hashes")),
                         meter=fe.make_meter(self.store, now))         # cache first, owner's order, monthly budget, spend ledger
        for n, lead in enumerate(picks, start=1):
            out = fe.enrich_lead(lead, ctx)
            if fe.save_outcome(self.store, lead["id"], out, CAMPAIGN_NAME):
                report["existing_completed"] += 1
                report["lead_ids"].append(lead["id"])
            report["checked"] += 0 if out.stop else 1
            report["emails_found"] += out.emails
            report["phones_found"] += out.phones
            report["decision_makers"] += 1 if out.people else 0
            self._set(run_id, done=base_done + n, enriched=report["existing_completed"], credits=spent + ctx.credits,
                      persist=n % PERSIST_EVERY == 0)
            if out.stop:
                limit = "the monthly lookup budget" if ctx.budget_hit else f"the credit limit ({params['max_credits']})"
                report["stopped_reason"] = (f"Stopped at {limit}. {len(picks) - n + 1} lead(s) were not "
                                            "fully checked; run again to carry on with them.")
                break
        for (source, message), count in ctx.errors.items():
            self._error(run_id, source, message + (f" ({count} times)" if count > 1 else ""))
        from .prospect_ingest import safely, sync_crm
        safely(sync_crm, self.store)                 # filled-in contacts -> prospect DB (credited to their source)
        self._set(run_id, persist=True)
