"""The enrichment waterfall: for each campaign lead without a verified email, try the chosen sources cheapest first and stop as
soon as one email is verified. Free sources run before the owner's paid keys.

For one lead (domain + optional contact name):
  1. finders: with a contact name -> find_email(name, domain); without -> find_people(domain, titles);
  2. every candidate email goes through the chosen verifiers, free built-in check first, then paid ones cheapest first;
     an email the finder itself verified (for example Hunter 'valid') counts as verified;
  3. stop at the first verified email; otherwise keep the best unverified one (marked as such), never an invalid one.
Never touched: suppressed addresses, addresses another lead already has, and anything at a different domain.
Credits are capped per run; a source that fails is skipped for the rest of the run.
"""
from ..campaign_sources import DEFAULT_PERSON_TITLES
from ..hygiene import business_domain, is_role_address, normalise_email
from ..lead_model import event, now_iso
from ..providers import valid_domain
from . import registry
from .base import CreditCapReached, KeyRejected, RateLimited, split_name
from .runner import credit_cap, describe_error
from .site_discover import crawl

MARKERS = {"hunter": "hunter_checked_at", "apollo": "apollo_checked_at"}
RANK = {"valid": 0, "plausible": 1, "unknown": 1, "risky": 2}


def _same_company(email: str, domain: str) -> bool:
    host = (email or "").split("@", 1)[-1].lower()
    return host == domain or host.endswith("." + domain)


class Run:
    """State shared by the leads of one enrichment pass: bound contexts, disabled sources and errors."""

    def __init__(self, store, campaign, transports, report, finders, verifiers, now):
        saved = store.read("integrations")
        self.ctx = registry.make_ctx(store, transports, credit_cap(campaign), now, spent=report.get("source_credits", 0.0))
        self.bound, self.off, self.errors = {}, set(), {}
        self.params = campaign["enrich"].get("extra_params") or {}
        for spec in {s.id: s for s in [*finders, *verifiers]}.values():
            conn = registry.connection(spec, saved)
            if conn["connected"]:
                self.bound[spec.id] = self.ctx.bind(spec, conn["values"], self.params.get(spec.id) or {})
            else:
                self.off.add(spec.id)
                self.errors[spec.id] = f"Not connected: {conn['reason']}"
        self.finders, self.verifiers = finders, verifiers

    def guard(self, spec, fn, default):
        if spec.id in self.off:
            return default
        try:
            return fn(self.bound[spec.id])
        except CreditCapReached as exc:
            self.errors.setdefault("credit_cap", str(exc))
            return default
        except (KeyRejected, RateLimited) as exc:
            self.off.add(spec.id)
            self.errors[spec.id] = str(exc)
        except Exception as exc:                      # noqa: BLE001 - failure isolation
            self.errors[spec.id] = describe_error(spec, exc)
        return default

    def check(self, email: str) -> dict:
        """Best verdict for one address across the verifiers: 'valid' or 'invalid' ends the chain at once."""
        best = None
        for spec in self.verifiers:
            v = self.guard(spec, lambda c: spec.verify(c, email), None)
            if not v:
                continue
            if v["status"] in ("valid", "invalid"):
                return v
            if best is None or RANK.get(v["status"], 1) > RANK.get(best["status"], 1):
                best = v
        return best or {"status": "unknown", "provider": "", "reason": ""}


def find_one(run: Run, lead: dict, titles: list, taken: set, suppressed: set):
    """(candidate or None, extras). candidate = {"person", "status", "provider"}."""
    domain = valid_domain(lead.get("domain")) or ""
    company = lead.get("company", "")
    first, last = split_name(lead.get("contact_name"))
    candidates, extras = [], {}
    for spec in run.finders:
        hits = []
        if first and spec.find_email:
            hits = run.guard(spec, lambda c: spec.find_email(c, first, last, domain, company), [])
        elif spec.find_people and not lead.get(MARKERS.get(spec.id, "")):         # the Hunter/Apollo checkboxes already did this search
            hits = run.guard(spec, lambda c: spec.find_people(c, domain, company, titles, 5), [])
        if spec.id == "website_deep" and spec.id in run.bound:                  # cached by the call above: no new fetch
            page = run.guard(spec, lambda c: crawl(c, domain), {})
            if page.get("company_linkedin"):
                extras["company_linkedin_url"] = page["company_linkedin"]
        for hit in hits:
            email = normalise_email(hit.get("email"))
            if not email or not _same_company(email, domain) or email in suppressed or f"email:{email}" in taken:
                continue
            if hit.get("verified"):
                verdict = {"status": "valid", "provider": hit["source"], "reason": "confirmed by the finder"}
            else:
                verdict = run.check(email)
            if verdict["status"] == "invalid":
                continue
            candidates.append({"person": {**hit, "email": email}, "status": verdict["status"], "provider": verdict.get("provider", ""),
                               "reason": verdict.get("reason", "")})
            if verdict["status"] == "valid":
                return candidates[-1], extras
    candidates.sort(key=lambda c: (RANK.get(c["status"], 1), is_role_address(c["person"]["email"])))
    return (candidates[0] if candidates else None), extras


def enrich_campaign(store, campaign: dict, transports: dict, report: dict, scope, add_error, now=None) -> None:
    """Fill missing emails for the campaign's open leads from the ticked extra sources."""
    chosen = [s for s in (campaign["enrich"].get("extra") or []) if s in registry.BY_ID]
    if not chosen:
        return
    finders = [s for s in registry.plan("find_email", chosen)]
    finders += [s for s in registry.plan("discover_people", chosen) if s not in finders and s.find_people]
    finders.sort(key=lambda s: (not s.free, s.cost, s.id))
    verifiers = registry.plan("verify_email", chosen)
    if not finders:
        return
    run = Run(store, campaign, transports, report, finders, verifiers, now)
    titles = campaign["target"].get("person_titles") or DEFAULT_PERSON_TITLES
    cap = campaign["rules"]["max_new_leads_per_run"]
    todo = [l for l in scope(store, campaign["id"]) if not l.get("email") and not l.get("waterfall_at") and valid_domain(l.get("domain"))][:cap]
    everyone = store.read("leads")
    taken = {l.get("dedupe_key") for l in everyone}
    suppressed = set(store.read("suppressed"))
    patches, found_count = {}, 0
    for lead in todo:
        before = run.ctx.budget.spent
        candidate, extras = find_one(run, lead, titles, taken, suppressed)
        report["credits_used_estimate"] += run.ctx.budget.spent - before
        changes = {"waterfall_at": now_iso(), **extras}
        notes = []
        if candidate:
            p = candidate["person"]
            email = p["email"]
            source = p["source"]
            checked = {"valid": "valid", "risky": "risky"}.get(candidate["status"], "unverified")
            changes.update(email=email, email_is_role=is_role_address(email), email_source=f"{source}:{p['source_url']}",
                           email_check={"email": email, "syntax_ok": True, "business": bool(business_domain(email)), "role": is_role_address(email),
                                        "provider": candidate["provider"] or source, "at": now_iso(), "status": checked})
            if f"email:{email}" not in taken:
                changes["dedupe_key"] = f"email:{email}"
            taken.add(f"email:{email}")
            prov = {"email": f"{source}:{p['source_url']}"}
            for field, key in (("contact_name", "name"), ("title", "title"), ("linkedin_url", "linkedin_url")):
                if p.get(key) and not lead.get(field):
                    changes[field] = p[key]
                    prov[field] = f"{source}:{p['source_url']}"
            changes["provenance"] = {**(lead.get("provenance") or {}), **prov}
            notes.append(f"email {email} ({checked}, via {source})")
            found_count += 1
        patches[lead["id"]] = {"set": changes, "events": [event("enrichment", f"Campaign {campaign['name']}, source waterfall: " + ", ".join(notes))] if notes else []}
    store.patch_leads(patches)
    report["enriched_emails"] += found_count
    report["source_credits"] = run.ctx.budget.spent
    run.ctx.cache.flush()
    for sid, message in run.errors.items():
        add_error(report, sid if sid in registry.BY_ID else "sequence", message)
