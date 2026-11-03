"""The steps of a campaign run after discovery: merge, dedupe, add, enrich, verify, score and qualify.

Every write goes through the store's atomic helpers (add_leads, update_lead, patch_leads), so a reply, unsubscribe
or send saved while a run is working is never overwritten. Leads past the early stages are never touched.
"""
from datetime import datetime

from . import campaign_sources as src
from .apollo import match_person_by_id
from .hygiene import business_domain, dedupe_key, is_role_address, normalise_email, normalise_phone
from .jobs import job_domain, job_key
from .lead_model import build_lead, event, now_iso
from .providers import PROVIDERS, contact_changes, provider_status, valid_domain
from .routes_jobs import _signals as job_signals, _upsert_jobs as upsert_jobs
from .scoring import score_lead
from .sheet_import import company_match_key

EARLY_STAGES = ("new", "verified", "enriched")
CLOSED_STAGES = ("unsubscribed", "disqualified")
SOURCE_LABELS = {"apollo_search": "Apollo people search", "apollo_hiring": "Apollo hiring companies",
                 "careers_page": "Careers page", "google_places": "Google Places", "attached": "Attached leads",
                 "website": "Company website", "hunter": "Hunter", "firecrawl": "Firecrawl", "apollo": "Apollo match",
                 "email_verifier": "Email verifier", "sequence": "Email sequence"}
PROVENANCE_FIELDS = ("company", "contact_name", "title", "email", "phone", "company_phone", "website", "domain", "linkedin_url",
                     "city", "country", "company_size", "address", "open_roles", "role", "max_job_age_days")
PERSON_FIELDS = ("contact_name", "title", "email", "linkedin_url", "apollo_person_id")
EXTRA_FIELDS = ("city", "linkedin_url", "role", "company_phone", "phone_type", "address", "apollo_person_id", "apollo_org_id",
                "google_place_id", "source_url")
FILL_ON_MERGE = ("email", "phone", "contact_name", "title", "website", "domain", "city", "linkedin_url", "role", "company_phone")
SIGNAL_FIELDS = ("open_roles", "max_job_age_days", "repost_count", "company_size")
MAX_SCOPE = 500
# Enrichment order: free first. (provider id, campaign flag, "already checked" marker on the lead)
ENRICHERS = (("website", "crawler", "enrichment_attempted_at"), ("hunter", "hunter", "hunter_checked_at"),
             ("firecrawl", "firecrawl", "firecrawl_checked_at"), ("apollo", "apollo_match", "apollo_checked_at"))


def add_error(report: dict, source: str, message: str) -> None:
    report["errors"].append({"source": SOURCE_LABELS.get(source, source), "message": message})


# ---------- careers pages -> rows ----------
def careers_rows(store, postings: list, now: datetime, dry_run: bool) -> list:
    """Company rows with hiring signals, using the same job store and signals as the Read job pages tool."""
    by_page = {}
    for posting, url in postings:
        by_page.setdefault(url, []).append(posting)
    result = {"jobs": []}

    def apply(current: list) -> list:
        merged = current
        for url, found in by_page.items():
            merged, _, _ = upsert_jobs(merged, found, url, now)
        result["jobs"] = merged
        return merged
    if not by_page:
        return []
    apply(store.read("jobs")) if dry_run else store.update("jobs", apply)
    touched = {job_key(p, job_domain(p, url)) for p, url in postings}
    jobs = result["jobs"]
    rows = []
    for company, domain in dict.fromkeys((j["company"], j["domain"]) for j in jobs if j["key"] in touched):
        mine = [j for j in jobs if j["company"].lower() == company.lower() or (domain and j["domain"] == domain)]
        website, own = src.own_site(f"https://{domain}") if domain else ("", "")
        page = next((j.get("url") for j in mine if j["key"] in touched and j.get("url")), "")
        rows.append({"company": company, "website": website, "domain": own, "city": mine[0].get("location", ""),
                     "source": "careers_page", "source_url": page, **job_signals(mine, now)})
    return rows


# ---------- merging rows that describe the same company ----------
def with_provenance(row: dict) -> dict:
    origin = f"{row['source']}:{row['source_url']}" if row.get("source_url") else row["source"]
    return {**row, "sources": [row["source"]], "other_contacts": [],
            "provenance": {f: origin for f in PROVENANCE_FIELDS if row.get(f)}}


def _person(row: dict) -> dict:
    return {"contact_name": row.get("contact_name", ""), "title": row.get("title", ""), "email": row.get("email", ""),
            "linkedin_url": row.get("linkedin_url", ""), "found_at": f"{row['source']}:{row.get('source_url', '')}",
            "company": row.get("company", "")}


def _has_person(row: dict) -> bool:
    return bool(row.get("contact_name") or row.get("email"))


def _merge(base: dict, row: dict) -> dict:
    if _has_person(row) and _has_person(base):
        if not base.get("email") and row.get("email"):          # the person with an email becomes the main contact
            base["other_contacts"].append(_person(base))
            base.update({f: row.get(f, "") for f in PERSON_FIELDS})
            base["provenance"].update({f: v for f, v in row["provenance"].items() if f in PERSON_FIELDS})
        elif (row.get("email"), row.get("contact_name")) != (base.get("email"), base.get("contact_name")):
            base["other_contacts"].append(_person(row))
    for field, value in row.items():
        if field in ("sources", "provenance", "other_contacts", "source", "source_url") or (field in PERSON_FIELDS and _has_person(base)):
            continue
        if field in SIGNAL_FIELDS and isinstance(value, int) and value > (base.get(field) or 0):
            base[field] = value
            base["provenance"][field] = row["provenance"].get(field, row["source"])
        elif value and not base.get(field):
            base[field] = value
            if field in row["provenance"]:
                base["provenance"][field] = row["provenance"][field]
    base["sources"] = list(dict.fromkeys([*base["sources"], row["source"]]))
    return base


def merge_rows(rows: list) -> list:
    """One candidate per company: rows with the same domain (or the same name when a domain is missing) are combined;
    extra people at the company are kept as other contacts."""
    groups, by_domain, by_name = [], {}, {}
    for raw in rows:
        row = with_provenance(raw)
        name = company_match_key(row.get("company") or "")
        target = by_domain.get(row["domain"]) if row.get("domain") else None
        if target is None and name and name in by_name and (not row.get("domain") or not groups[by_name[name]].get("domain")):
            target = by_name[name]
        if target is None:
            groups.append(row)
            target = len(groups) - 1
        else:
            groups[target] = _merge(groups[target], row)
        if groups[target].get("domain"):
            by_domain.setdefault(groups[target]["domain"], target)
        if name:
            by_name.setdefault(name, target)
    return groups


# ---------- dedupe against existing leads ----------
def build_candidate(cand: dict, campaign: dict):
    row = {k: str(cand.get(k) or "") for k in ("company", "contact_name", "title", "email", "phone", "website", "country",
                                              "open_roles", "company_size", "max_job_age_days", "repost_count")}
    lead = build_lead(row, cand["sources"][0], allow_company_key=True)
    if lead is None:
        return None
    labels = ", ".join(SOURCE_LABELS.get(s, s) for s in cand["sources"])
    prov = cand["provenance"]
    extras = {k: cand[k] for k in EXTRA_FIELDS if cand.get(k)}
    if cand.get("other_contacts"):
        extras["other_contacts"] = cand["other_contacts"]
    lead.update(extras, campaign_id=campaign["id"], provenance=prov, sources=cand["sources"],
                domain=lead["domain"] or cand.get("domain") or None,
                timeline=[event("created", f"Added by campaign {campaign['name']} from {labels}")])
    if lead.get("email") and prov.get("email"):
        lead["email_source"] = prov["email"]
    if lead.get("phone") and prov.get("phone"):
        lead["phone_source"] = prov["phone"]
    return lead


def _matches(cand_lead: dict, index: dict) -> list:
    found = {}
    for key in {cand_lead.get("dedupe_key"), dedupe_key({"email": cand_lead.get("email")}),
                dedupe_key({"phone": cand_lead.get("phone")})} - {None}:
        for existing in index["key"].get(key, []):
            found[existing["id"]] = existing
    if cand_lead.get("domain"):
        for existing in index["domain"].get(cand_lead["domain"], []):
            found[existing["id"]] = existing
    name = company_match_key(cand_lead.get("company") or "")
    if name:
        for existing in index["name"].get(name, []):
            if not (existing.get("domain") and cand_lead.get("domain")):
                found[existing["id"]] = existing
    return list(found.values())


def _index(leads: list) -> dict:
    index = {"key": {}, "domain": {}, "name": {}}
    for lead in leads:
        for key in {lead.get("dedupe_key"), dedupe_key({"email": lead.get("email")}), dedupe_key({"phone": lead.get("phone")})} - {None}:
            index["key"].setdefault(key, []).append(lead)
        if lead.get("domain"):
            index["domain"].setdefault(lead["domain"], []).append(lead)
        name = company_match_key(lead.get("company") or "")
        if name:
            index["name"].setdefault(name, []).append(lead)
    return index


def plan_additions(store, campaign: dict, candidates: list, report: dict) -> dict:
    """Decide for each candidate: add, fill gaps of an early-stage lead (merge), duplicate, suppressed or over the cap."""
    leads = store.read("leads")
    suppressed = set(store.read("suppressed"))
    index = _index(leads)
    cap = campaign["rules"]["max_new_leads_per_run"]
    plan = {"add": [], "merge": [], "preview": []}
    seen = set()
    for cand in candidates:
        lead = build_candidate(cand, campaign)
        verdict, target = "add", None
        emails = {lead.get("email") if lead else cand.get("email")} | {c.get("email") for c in cand.get("other_contacts", [])}
        if lead is None:
            verdict = "no_contact"
        elif emails & suppressed:
            verdict = "suppressed"
        else:
            matches = _matches(lead, index)
            if any(m["stage"] in CLOSED_STAGES for m in matches):
                verdict = "suppressed"
            elif matches:
                open_ones = [m for m in matches if m["stage"] in EARLY_STAGES and not m.get("sequence")]
                target = next((m for m in open_ones if not m.get("email") and lead.get("email")), None)
                verdict = "merge" if target and len(open_ones) == len(matches) else "duplicate"
            elif lead["dedupe_key"] in seen:
                verdict = "duplicate"
            elif len(plan["add"]) >= cap:
                verdict = "over_cap"
        if verdict == "add":
            seen.add(lead["dedupe_key"])
            plan["add"].append(lead)
        elif verdict == "merge":
            plan["merge"].append((target["id"], cand))
        report["duplicates"] += verdict == "duplicate"
        report["suppressed_skipped"] += verdict == "suppressed"
        report["over_cap"] += verdict == "over_cap"
        report["skipped_no_contact"] += verdict == "no_contact"
        plan["preview"].append({"lead": lead or {**cand, "id": None}, "would": verdict, "sources": cand["sources"]})
    return plan


def merge_into_existing(store, lead_id: str, cand: dict, campaign: dict) -> bool:
    """Fill only the empty fields of an early-stage lead that is not in the sequence. Returns True when it changed."""
    done = {}

    def change(lead: dict):
        if lead["stage"] not in EARLY_STAGES or lead.get("sequence"):
            return None
        filled = {f: cand[f] for f in FILL_ON_MERGE if cand.get(f) and not lead.get(f)}
        if filled.get("email"):
            filled["email"] = normalise_email(filled["email"])
            filled["email_is_role"] = bool(filled["email"]) and is_role_address(filled["email"])
            filled["email_source"] = cand["provenance"].get("email", cand["sources"][0])
        if filled.get("phone"):
            filled["phone"] = normalise_phone(filled["phone"])
        filled = {k: v for k, v in filled.items() if v not in (None, "")}
        if not filled:
            return None
        done["ok"] = True
        prov = {**(lead.get("provenance") or {}), **{f: cand["provenance"][f] for f in filled if f in cand["provenance"]}}
        labels = ", ".join(SOURCE_LABELS.get(s, s) for s in cand["sources"])
        out = {**lead, **filled, "provenance": prov, "campaign_id": lead.get("campaign_id") or campaign["id"],
               "timeline": [*lead["timeline"], event("merged", f"Details added by campaign {campaign['name']} from {labels}: "
                                                     + ", ".join(sorted(filled)))]}
        out["dedupe_key"] = dedupe_key(out) or lead["dedupe_key"]
        return out
    store.update_lead(lead_id, change)
    return bool(done)


def attach_leads(store, campaign: dict, ids: list, report: dict) -> None:
    """Existing leads picked by id (an earlier import) join the campaign, when still early-stage and not emailed."""
    suppressed = set(store.read("suppressed"))
    attached = skipped = 0
    for lead_id in dict.fromkeys(ids):
        ok = {}

        def change(lead: dict):
            if (lead.get("campaign_id") not in (None, campaign["id"]) or lead["stage"] not in EARLY_STAGES
                    or lead.get("sequence") or (lead.get("email") and lead["email"] in suppressed)):
                return None
            ok["joined"] = lead.get("campaign_id") != campaign["id"]
            ok["fine"] = True
            if not ok["joined"]:
                return None
            return {**lead, "campaign_id": campaign["id"],
                    "timeline": [*lead["timeline"], event("campaign", f"Added to campaign {campaign['name']}")]}
        store.update_lead(lead_id, change)
        attached += bool(ok.get("joined"))
        skipped += not ok.get("fine")
    report["attached"] = attached
    if skipped:
        add_error(report, "attached", f"{skipped} picked lead(s) were left out: not found, in another campaign, past the "
                                      "early stages, already emailed, or suppressed.")


def scope(store, campaign_id: str) -> list:
    """Open leads of this campaign: early stage and not yet in the sequence."""
    return [l for l in store.read("leads") if l.get("campaign_id") == campaign_id and l["stage"] in EARLY_STAGES
            and not l.get("sequence")][:MAX_SCOPE]


# ---------- enrichment ----------
def _apollo_by_id(lead: dict, config: dict, post) -> dict:
    person = match_person_by_id(config.get("api_key", ""), lead["apollo_person_id"], post) or {}
    email = normalise_email(person.get("email"))
    if email and email.startswith(src.APOLLO_LOCKED_PREFIX):
        email = None
    found = [{"value": email, "source": "api.apollo.io", "name": person.get("name") or "", "title": person.get("title") or ""}] if email else []
    return {"emails": found, "phones": [], "credits_used": 1}


def _wants(pid: str, lead: dict) -> bool:
    """Paid lookups only for a missing email; the free website crawler also looks for a missing phone."""
    if pid == "apollo":
        return not lead.get("email") and bool(lead.get("apollo_person_id") or lead.get("contact_name"))
    return bool(valid_domain(lead.get("domain"))) and (not lead.get("email") or (pid == "website" and not lead.get("phone")))


def enrich(store, campaign: dict, transports: dict, report: dict) -> None:
    settings, cap = campaign["enrich"], campaign["rules"]["max_new_leads_per_run"]
    saved = store.read("integrations")
    for pid, flag, marker in ENRICHERS:
        if not settings.get(flag):
            continue
        title = SOURCE_LABELS[pid]
        status = provider_status(saved, pid)
        if not status["configured"]:
            add_error(report, pid, f"Skipped: {status['reason']}")
            continue
        todo = [l for l in scope(store, campaign["id"]) if not l.get(marker) and _wants(pid, l)][:cap]
        everyone = store.read("leads")
        taken = {l.get("dedupe_key") for l in everyone}
        suppressed = set(store.read("suppressed"))
        patches, failures, last_failure = {}, 0, ""
        for lead in todo:
            domain = valid_domain(lead.get("domain")) or ""
            try:
                if pid == "apollo" and lead.get("apollo_person_id"):
                    found = _apollo_by_id(lead, status["config"], transports["post"])
                else:
                    found = PROVIDERS[pid]["find"](domain, lead.get("company", ""), status["config"], transports, lead.get("contact_name") or "")
            except PermissionError:
                add_error(report, pid, f"{title} rejected the API key, so it was stopped for this run.")
                break
            except (OSError, ValueError) as exc:
                failures, last_failure = failures + 1, str(exc)[:160]
                patches[lead["id"]] = {"set": {marker: now_iso()}, "events": []}
                continue
            report["credits_used_estimate"] += found.get("credits_used", 0)
            emails = found.get("emails") or []
            if pid == "apollo" and domain:         # an Apollo match at another company is not kept
                emails = [e for e in emails if business_domain(e["value"]) in (None, domain)]
            changes, notes = contact_changes(lead, domain, emails, found.get("phones") or [], pid, taken, suppressed)
            chosen = next((e for e in emails if e["value"] == changes.get("email")), None)
            if chosen and chosen.get("name") and not lead.get("contact_name"):
                changes.update(contact_name=chosen["name"], title=lead.get("title") or chosen.get("title") or "")
            if changes.get("email"):
                taken.add(f"email:{changes['email']}")
                report["enriched_emails"] += 1
            report["enriched_phones"] += "phone" in changes
            prov = {f: changes[f"{f}_source"] for f in ("email", "phone") if changes.get(f"{f}_source")}
            if prov:
                changes["provenance"] = {**(lead.get("provenance") or {}), **prov}
            changes[marker] = now_iso()
            events = [event("enrichment", f"Campaign {campaign['name']}, {title}: " + ", ".join(notes))] if notes else []
            patches[lead["id"]] = {"set": changes, "events": events}
        store.patch_leads(patches)
        if failures:
            add_error(report, pid, f"{failures} lookup(s) failed (last: {last_failure}).")


# ---------- verification, scoring, qualification ----------
def verify(store, campaign: dict, transports: dict, report: dict) -> None:
    """Syntax, business domain and shared-mailbox checks; the connected email verifier when it can be called."""
    config = src.verifier_config(store.read("integrations"))
    if config and not config["supported"]:
        add_error(report, "email_verifier", f"'{config['provider'] or 'unnamed'}' cannot be called automatically yet "
                                            "(ZeroBounce and NeverBounce can). Emails are marked unverified.")
    usable = config if config and config["supported"] else None
    cap = campaign["rules"]["max_new_leads_per_run"]
    counts = {"valid": 0, "invalid": 0, "risky": 0, "unverified": 0}
    patches, calls, failures, stopped = {}, 0, 0, False
    for lead in scope(store, campaign["id"]):
        email = lead.get("email")
        if not email:
            continue
        prior = lead.get("email_check") or {}
        check = {"email": email, "syntax_ok": bool(normalise_email(email)), "business": bool(business_domain(email)),
                 "role": is_role_address(email), "provider": "", "at": now_iso()}
        if not check["syntax_ok"]:
            status = "invalid"
        elif prior.get("email") == email and prior.get("status") in ("valid", "invalid", "risky"):
            status, check["provider"], check["at"] = prior["status"], prior.get("provider", ""), prior.get("at", check["at"])
        elif usable and not stopped and calls < cap:
            try:
                calls += 1
                status, check["provider"] = src.verify_email(email, usable, transports["get"]), usable["provider"]
                report["credits_used_estimate"] += 1
            except (src.KeyRejected, PermissionError) as exc:
                stopped, status = True, "unverified"
                add_error(report, "email_verifier", f"Stopped: {exc if str(exc) else 'the verifier rejected the API key'}.")
            except (OSError, ValueError):
                failures, status = failures + 1, "unverified"
        else:
            status = "unverified"
        check["status"] = status
        counts[status] += 1
        patches[lead["id"]] = {"set": {"email_check": check}, "events": []}
    store.patch_leads(patches)
    if failures:
        add_error(report, "email_verifier", f"{failures} check(s) failed; those emails stay unverified.")
    report["verification"] = counts
    report["verified"] = counts["valid"] + counts["unverified"]


def present_score(lead: dict) -> dict:
    return score_lead({**lead, "consent_yes": bool(lead.get("consent"))})


def qualify_reasons(lead: dict, rules: dict, score: int) -> list:
    """Why a lead is not ready for the email sequence (empty list = ready)."""
    reasons = []
    if score < rules["min_score"]:
        reasons.append(f"score {score} is below {rules['min_score']}")
    email = lead.get("email")
    check = lead.get("email_check") or {}
    if not email:
        reasons.append("no email found yet")
        return reasons
    if not normalise_email(email):
        reasons.append("the email address is not valid")
    if rules.get("require_business_email", True) and not business_domain(email):
        reasons.append("free-mail address, not a business email")
    if rules.get("skip_role_addresses") and (lead.get("email_is_role") or is_role_address(email)):
        reasons.append("shared mailbox (hr@, info@…)")
    if check.get("email") == email and check.get("status") == "invalid":
        reasons.append("the email verifier says this address does not work")
    if check.get("email") == email and check.get("status") == "risky":
        reasons.append("the email verifier could not confirm this address (catch-all or unknown)")
    return reasons


def qualify(store, campaign: dict, report: dict) -> list:
    """Score every open campaign lead, store the verdict on it, and return the ids that qualify."""
    rules, patches, qualified = campaign["rules"], {}, []
    for lead in scope(store, campaign["id"]):
        scored = present_score(lead)
        reasons = qualify_reasons(lead, rules, scored["score"])
        patches[lead["id"]] = {"set": {"campaign_check": {"score": scored["score"], "qualified": not reasons,
                                                           "reasons": reasons, "at": now_iso()}}, "events": []}
        if not reasons:
            qualified.append(lead["id"])
    store.patch_leads(patches)
    report["qualified"] = len(qualified)
    return qualified
