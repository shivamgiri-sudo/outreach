"""Sheet import, public-web enrichment, Apollo export list and data-quality counts."""
import csv
import io
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .apollo import match_person
from .http_util import post_json
from .enrich import find_public_contacts, needs, pick_best_email
from .hygiene import business_domain, csv_safe, is_role_address, normalise_email, safe_http_url
from .lead_model import contactless_match, event, merge_contact, now_iso
from .privacy import lead_hashes
from .sheet_import import company_match_key, decision_makers, demo_leads, read_rows, sheet_leads

APOLLO_COLUMNS = ["company", "contact_name", "title", "domain", "city", "lead_id"]
DEFAULT_ENRICH_LIMIT = 20


class SheetBody(BaseModel):
    csv: str
    tab: str = "leads"


class EnrichBody(BaseModel):
    limit: int = Field(default=DEFAULT_ENRICH_LIMIT, ge=1, le=200)


def make_router(store, fetcher, apollo_transport=post_json) -> APIRouter:
    router = APIRouter()

    @router.post("/api/leads/import-sheet")
    def import_sheet(body: SheetBody):
        if body.tab not in ("leads", "demo", "decision_makers"):
            raise HTTPException(400, "tab must be 'leads', 'demo' or 'decision_makers'")
        rows = read_rows(body.csv)
        if body.tab == "decision_makers":
            return attach_people(decision_makers(rows))
        incoming = demo_leads(rows) if body.tab == "demo" else sheet_leads(rows)
        stats = {"created": 0, "duplicates": 0, "merged": 0}
        erased = set(store.read("suppressed_hashes"))
        blocked = set(store.read("suppressed"))

        def merge_in(leads: list) -> list:
            seen = {lead["dedupe_key"] for lead in leads}
            for lead in incoming:
                if lead_hashes(lead) & erased or lead.get("email") in blocked:
                    stats["suppressed"] = stats.get("suppressed", 0) + 1     # erased or do-not-contact: never imported again
                elif lead["dedupe_key"] in seen:
                    stats["duplicates"] += 1
                elif (target := contactless_match(leads, lead)) is not None:
                    leads[target] = merge_contact(leads[target], lead)
                    seen.add(lead["dedupe_key"])
                    stats["merged"] += 1
                else:
                    seen.add(lead["dedupe_key"])
                    leads.append(lead)
                    stats["created"] += 1
            return leads
        store.update("leads", merge_in)
        created, duplicates, merged = stats["created"], stats["duplicates"], stats["merged"]
        extra = {"suppressed": stats["suppressed"]} if stats.get("suppressed") else {}
        return {"created": created, "duplicates": duplicates, "merged": merged, "rows": len(rows), **extra}

    def attach_people(people: list) -> dict:
        """Give each hand-found person to the matching company: first one fills empty fields, the rest are kept as other contacts."""
        stats = {"matched": 0, "filled": 0, "unmatched": []}

        def apply(leads: list) -> list:
            index = {}
            for i, lead in enumerate(leads):
                index.setdefault(company_match_key(lead.get("company") or ""), i)
            for person in people:
                i = index.get(company_match_key(person["company"]))
                if i is None:
                    if person["company"] not in stats["unmatched"]:
                        stats["unmatched"].append(person["company"])
                    continue
                stats["matched"] += 1
                lead = leads[i]
                known = {lead.get("contact_name") or ""} | {c["contact_name"] for c in lead.get("other_contacts", [])}
                if person["contact_name"] in known:
                    continue
                note = f"{person['contact_name']} ({person['title'] or 'role not shown'}, confidence {person['confidence'] or 'unknown'}) from the team sheet"
                if not lead.get("contact_name"):
                    stats["filled"] += 1
                    lead = {**lead, "contact_name": person["contact_name"], "title": lead.get("title") or person["title"],
                            "linkedin_url": lead.get("linkedin_url") or person["linkedin_url"],
                            "contact_confidence": person["confidence"], "contact_source": f"team sheet: {person['found_at']}"}
                else:
                    lead = {**lead, "other_contacts": [*lead.get("other_contacts", []), person]}
                leads[i] = {**lead, "timeline": [*lead["timeline"], event("enrichment", "Decision maker added: " + note)]}
            return leads

        store.update("leads", apply)
        return stats

    @router.post("/api/enrich/run")
    def enrich(body: EnrichBody):
        leads = store.read("leads")
        todo = [i for i, lead in enumerate(leads) if needs(lead) and not lead.get("enrichment_attempted_at")]
        no_site = [i for i in todo if not leads[i].get("domain")]
        todo = [i for i in todo if leads[i].get("domain")][: body.limit]
        enriched = 0
        patches = {}
        for i in todo:
            lead = leads[i]
            found = find_public_contacts(lead["domain"], fetcher)
            changes = {"enrichment_attempted_at": now_iso(),
                       "enrichment": {"emails": [{"value": e, "source_url": u} for e, u in found["emails"].items()],
                                      "phones": [{"value": p, "source_url": u} for p, u in found["phones"].items()],
                                      "pages_checked": found["pages_checked"], "at": now_iso()}}
            notes = []
            email = None if lead.get("email") else pick_best_email(list(found["emails"]), lead["domain"])
            if email:
                taken = any(other.get("dedupe_key") == f"email:{email}" for other in leads)
                changes.update(email=email, email_is_role=is_role_address(email), email_source=f"enrichment:{found['emails'][email]}")
                if not taken:
                    changes["dedupe_key"] = f"email:{email}"
                notes.append(f"email {email}")
            if not lead.get("phone") and found["phones"]:
                phone, source = next(iter(found["phones"].items()))
                changes.update(phone=phone, phone_source=f"enrichment:{source}")
                notes.append(f"phone {phone}")
            events = []
            if notes:
                enriched += 1
                events.append(event("enrichment", "Found on company website: " + ", ".join(notes)))
            patches[lead["id"]] = {"set": changes, "events": events}
        store.patch_leads(patches)
        return {"checked": len(todo), "enriched": enriched, "skipped_no_website": len(no_site)}

    @router.post("/api/enrich/apollo")
    def enrich_with_apollo(body: EnrichBody):
        saved = store.read("integrations").get("apollo", {})
        key = saved.get("values", {}).get("api_key")
        if saved.get("mode") != "api" or not key:
            raise HTTPException(400, "Connect Apollo in Setup (direct API mode) first. Or use the CSV route.")
        leads = store.read("leads")
        todo = [i for i, lead in enumerate(leads)
                if not lead.get("email") and lead.get("contact_name") and (lead.get("domain") or lead.get("company"))
                and not lead.get("apollo_checked_at") and lead["stage"] not in ("unsubscribed", "disqualified")][: body.limit]
        calls = matched = errors = 0
        patches = {}
        for i in todo:
            lead = leads[i]
            first, _, last = lead["contact_name"].strip().partition(" ")
            try:
                calls += 1
                person = match_person(key, first, last, lead.get("domain"), lead.get("company"), apollo_transport)
            except PermissionError:
                store.patch_leads(patches)
                raise HTTPException(400, "Apollo rejected the API key. Check it in Setup.")
            except (OSError, ValueError):
                errors += 1
                continue
            changes, events = {"apollo_checked_at": now_iso()}, []
            email = normalise_email(person.get("email")) if person else None
            lead_domain = (lead.get("domain") or "").lower()
            if email and lead_domain and business_domain(email) and business_domain(email) != lead_domain:
                email = None            # Apollo matched someone at a different company; don't store it
            if email and "email_not_unlocked" in email:
                email = None
            if email:
                taken = any(other.get("dedupe_key") == f"email:{email}" for other in leads)
                changes.update(email=email, email_is_role=is_role_address(email), email_source="apollo",
                               title=lead.get("title") or person.get("title") or "",
                               linkedin_url=lead.get("linkedin_url") or safe_http_url(person.get("linkedin_url")))
                events.append(event("enrichment", f"Apollo matched {email}"))
                if not taken:
                    changes["dedupe_key"] = f"email:{email}"
                matched += 1
            patches[lead["id"]] = {"set": changes, "events": events}
        store.patch_leads(patches)
        return {"checked": len(todo), "matched": matched, "errors": errors, "credits_used_estimate": calls}

    @router.get("/api/enrich/apollo-export", response_class=PlainTextResponse)
    def apollo_export():
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(APOLLO_COLUMNS)
        for lead in store.read("leads"):
            if not lead.get("email") and lead["stage"] not in ("disqualified", "unsubscribed", "customer"):
                writer.writerow([csv_safe(v) for v in (lead.get("company", ""), lead.get("contact_name", ""), lead.get("title", ""),
                                                       lead.get("domain") or "", lead.get("city", ""), lead["id"])])
        return out.getvalue()

    @router.get("/api/quality")
    def quality():
        leads = store.read("leads")
        return {
            "total": len(leads),
            "missing_email": sum(1 for lead in leads if not lead.get("email")),
            "missing_phone": sum(1 for lead in leads if not lead.get("phone")),
            "missing_decision_maker": sum(1 for lead in leads if not lead.get("contact_name")),
            "missing_website": sum(1 for lead in leads if not lead.get("domain")),
            "enrichable_now": sum(1 for lead in leads if needs(lead) and lead.get("domain") and not lead.get("enrichment_attempted_at")),
        }

    return router
