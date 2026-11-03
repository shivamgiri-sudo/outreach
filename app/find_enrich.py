"""Fill in contacts for leads the owner already has (the second half of "Find & enrich leads").

Per lead, cheapest first: the company's own public contact pages (robots.txt respected, free), then Apollo (organisation
lookup, people search by domain for HR / talent / founder titles, one email reveal), then Hunter and Firecrawl when they
are connected. Only empty fields are filled, every value keeps where it came from, closed leads are never picked, and
paid calls stop at the credit limit. Apollo and Hunter request shapes follow their public docs and are tested with fakes
only: confirm with one small live run before relying on them.
"""
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import campaign_pipeline as pipe
from . import campaign_sources as src
from .apollo import match_person_by_id
from .enrich import find_public_contacts
from .hygiene import business_domain, is_role_address, normalise_email
from .lead_model import event, now_iso
from .privacy import hashes_for, lead_hashes
from .providers import PROVIDERS, contact_changes, valid_domain
from .sheet_import import company_match_key

CHECKED_MARKER = "find_checked_at"
RECHECK_DAYS = 14                 # a lead checked in the last two weeks is not looked up (and paid for) again
ORG_LOOKUP_CREDITS = 1
PEOPLE_SEARCH_CREDITS = 1
REVEAL_CREDITS = 1
MAX_PEOPLE = 5
MAX_OTHER_CONTACTS = 5
NETWORK_ERRORS = (OSError, ValueError)
APOLLO_ORG_ENRICH_URL = f"{src.APOLLO_BASE}/organizations/enrich"
SEARCH_FIELDS = ("contact_name", "title", "linkedin_url", "apollo_person_id")


class Context:
    """Everything one run shares: transports, connected providers, the credit budget, and what went wrong."""

    def __init__(self, transports: dict, status: dict, titles: list, cap: int, taken: set, suppressed: set, erased: set):
        self.transports, self.status, self.titles, self.cap = transports, status, titles, cap
        self.taken, self.suppressed, self.erased = taken, suppressed, erased
        self.credits, self.disabled, self.out_of_budget = 0, set(), False
        self.errors: dict = {}                  # (source, message) -> how many times

    def usable(self, pid: str) -> bool:
        return bool(self.status.get(pid, {}).get("configured")) and pid not in self.disabled

    def can_spend(self, n: int) -> bool:
        if self.credits + n > self.cap:
            self.out_of_budget = True
            return False
        return True

    def error(self, source: str, message: str) -> None:
        key = (pipe.SOURCE_LABELS.get(source, source), message)
        self.errors[key] = self.errors.get(key, 0) + 1

    def reject(self, pid: str) -> None:
        self.disabled.add(pid)
        self.error(pid, f"{pipe.SOURCE_LABELS[pid]} rejected the API key, so it was skipped for the rest of this run. Check it under Integrations.")


# ---------- who to look at ----------
def _recently_checked(lead: dict, now: datetime) -> bool:
    try:
        when = datetime.fromisoformat(str(lead.get(CHECKED_MARKER) or ""))
    except ValueError:
        return False
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)) > now - timedelta(days=RECHECK_DAYS)


def pick_leads(store, limit: int, now: Optional[datetime] = None, exclude: frozenset = frozenset()) -> dict:
    """Early-stage leads with no email, not yet in the sequence, not erased and not checked lately.

    Those with a website domain come first (best score first), then those with only a company name."""
    now = now or datetime.now(timezone.utc)
    erased = set(store.read("suppressed_hashes"))
    ready = []
    for lead in store.read("leads"):
        if (lead.get("stage") not in pipe.EARLY_STAGES or lead.get("sequence") or lead.get("email") or lead["id"] in exclude
                or lead_hashes(lead) & erased or _recently_checked(lead, now)):
            continue
        has_domain = bool(valid_domain(lead.get("domain")))
        if has_domain or (lead.get("company") or "").strip():
            ready.append((not has_domain, -pipe.present_score(lead)["score"], lead))
    ready.sort(key=lambda r: (r[0], r[1]))
    return {"leads": [r[2] for r in ready[:limit]], "with_website": sum(1 for r in ready if not r[0]),
            "name_only": sum(1 for r in ready if r[0]), "available": len(ready)}


def worst_case_credits(leads: list, status: dict) -> int:
    """The most one pass over these leads could spend if every paid step runs."""
    total = 0
    for lead in leads:
        known = bool(valid_domain(lead.get("domain")))
        if status.get("apollo", {}).get("connected"):
            total += (0 if lead.get("apollo_org_id") else ORG_LOOKUP_CREDITS) + (0 if known else ORG_LOOKUP_CREDITS)
            total += PEOPLE_SEARCH_CREDITS + REVEAL_CREDITS
        if known and status.get("hunter", {}).get("connected"):
            total += 1
        if known and status.get("firecrawl", {}).get("connected"):
            total += 3
    return total


# ---------- Apollo steps ----------
def _apollo_headers(config: dict) -> dict:
    return {"x-api-key": config.get("api_key", ""), "Accept": "application/json", "Cache-Control": "no-cache"}


def apollo_org_by_name(company: str, ctx: Context) -> Optional[dict]:
    """The company's own site from Apollo's organisation search, only when the returned name is the same company."""
    reply = ctx.transports["post"](src.APOLLO_COMPANY_SEARCH_URL, _apollo_headers(ctx.status["apollo"]["config"]),
                                   {"q_organization_name": company, "page": 1, "per_page": 3})
    key = company_match_key(company)
    for org in [o for o in (reply.get("organizations") or []) + (reply.get("accounts") or []) if isinstance(o, dict)]:
        row = src.map_apollo_org(org)
        if row["domain"] and company_match_key(row["company"]) == key:
            return row
    return None


def apollo_org_by_domain(domain: str, ctx: Context) -> Optional[dict]:
    reply = ctx.transports["get"](f"{APOLLO_ORG_ENRICH_URL}?{urllib.parse.urlencode({'domain': domain})}",
                                  _apollo_headers(ctx.status["apollo"]["config"]))
    org = reply.get("organization")
    return src.map_apollo_org(org) if isinstance(org, dict) else None


def apollo_people_at(domain: str, ctx: Context) -> list:
    body = {"q_organization_domains_list": [domain], "person_titles": ctx.titles, "page": 1, "per_page": MAX_PEOPLE}
    reply = ctx.transports["post"](src.APOLLO_PEOPLE_SEARCH_URL, _apollo_headers(ctx.status["apollo"]["config"]), body)
    people = [src.map_apollo_person(p) for p in reply.get("people") or [] if isinstance(p, dict)]
    return [p for p in people if p["contact_name"] and p["domain"] in ("", domain)][:MAX_PEOPLE]


def _first(name: str) -> str:
    return (name or "").strip().split(" ")[0].lower()


def choose_primary(lead: dict, people: list) -> Optional[dict]:
    """The person to put on the lead: the best match for the name it already has, else the first one found."""
    if not people:
        return None
    if lead.get("contact_name"):
        return next((p for p in people if _first(p["contact_name"]) == _first(lead["contact_name"])), None)
    return people[0]


# ---------- one lead ----------
class Outcome:
    def __init__(self):
        self.changes, self.notes, self.prov, self.others = {}, [], {}, []
        self.emails = self.phones = self.people = 0
        self.stop = False


def _usable_emails(emails: list, ctx: Context) -> list:
    """Drop emails that are suppressed, erased, or already another lead's."""
    out = []
    for e in emails:
        value = normalise_email(e.get("value"))
        if value and value not in ctx.suppressed and not (hashes_for(value) & ctx.erased) and f"email:{value}" not in ctx.taken:
            out.append({**e, "value": value})
    return out


def _fill_contact(work: dict, out: Outcome, domain: str, emails: list, phones: list, tag: str, ctx: Context) -> None:
    changes, notes = contact_changes(work, domain, _usable_emails(emails, ctx), phones, tag, ctx.taken, ctx.suppressed)
    if not changes:
        return
    chosen = next((e for e in emails if e.get("value") == changes.get("email")), None)
    if chosen and chosen.get("name") and not work.get("contact_name"):
        changes.update(contact_name=chosen["name"], title=work.get("title") or chosen.get("title") or "")
        out.prov["contact_name"] = changes["email_source"]
    if changes.get("email"):
        ctx.taken.add(f"email:{changes['email']}")
        out.emails += 1
        out.prov["email"] = changes["email_source"]
    if changes.get("phone"):
        out.phones += 1
        out.prov["phone"] = changes["phone_source"]
    work.update(changes)
    out.changes.update(changes)
    out.notes += notes


def _crawl(work: dict, out: Outcome, domain: str, ctx: Context) -> None:
    found = find_public_contacts(domain, ctx.transports["fetch"])
    if not found["pages_checked"]:
        ctx.error("website", "Some websites could not be read (not reachable, or they do not allow automatic reading).")
        return
    emails = [{"value": e, "source": url} for e, url in found["emails"].items()]
    phones = [{"value": p, "source": url} for p, url in found["phones"].items()]
    _fill_contact(work, out, domain, emails, phones, "website", ctx)


def _paid(pid: str, cost: int, ctx: Context, call):
    """One paid call within the credit limit. Returns None when it was skipped or failed (the reason is recorded)."""
    if not ctx.usable(pid) or not ctx.can_spend(cost):
        return None
    try:
        ctx.credits += cost
        return call()
    except (PermissionError, src.KeyRejected):
        ctx.reject(pid)
    except NETWORK_ERRORS as exc:
        ctx.error(pid, f"Some lookups failed (last: {str(exc)[:120]}).")
    return None


def _attach_people(work: dict, out: Outcome, people: list) -> Optional[dict]:
    primary = choose_primary(work, people)
    for field in SEARCH_FIELDS:
        if primary and primary.get(field) and not work.get(field):
            work[field] = out.changes[field] = primary[field]
            out.prov[field] = "apollo:people search"
    known = {(c.get("contact_name") or "").lower() for c in work.get("other_contacts") or []}
    known.add((work.get("contact_name") or "").lower())
    for p in people:
        if p is not primary and p["contact_name"].lower() not in known:
            known.add(p["contact_name"].lower())
            out.others.append({"contact_name": p["contact_name"], "title": p["title"], "email": p["email"],
                               "linkedin_url": p["linkedin_url"], "found_at": "apollo:people search", "company": work.get("company", "")})
    out.people = len(people)
    if primary:
        out.notes.append(f"decision maker {primary['contact_name']} ({primary['title'] or 'title not given'})")
    return primary


def _apollo(work: dict, out: Outcome, domain: str, ctx: Context) -> None:
    if not work.get("apollo_org_id"):
        org = _paid("apollo", ORG_LOOKUP_CREDITS, ctx, lambda: apollo_org_by_domain(domain, ctx))
        if org:
            for field in ("apollo_org_id", "company_size", "city"):
                if org.get(field) and not work.get(field):
                    work[field] = out.changes[field] = org[field]
                    out.prov[field] = "apollo:organization lookup"
    people = _paid("apollo", PEOPLE_SEARCH_CREDITS, ctx, lambda: apollo_people_at(domain, ctx))
    if not people:
        return
    primary = _attach_people(work, out, people)
    if not primary or work.get("email"):
        return
    if primary["email"]:                                            # a search result that already shows the work email
        _fill_contact(work, out, domain, [{"value": primary["email"], "source": "api.apollo.io", "name": primary["contact_name"],
                                           "title": primary["title"]}], [], "apollo", ctx)
    elif primary["apollo_person_id"]:
        person = _paid("apollo", REVEAL_CREDITS, ctx, lambda: match_person_by_id(ctx.status["apollo"]["config"].get("api_key", ""),
                                                                                 primary["apollo_person_id"], ctx.transports["post"]) or {})
        email = normalise_email((person or {}).get("email"))
        if email and not email.startswith(src.APOLLO_LOCKED_PREFIX) and business_domain(email) in (None, domain):
            if person.get("name") and " " in person["name"] and (work.get("contact_name") or "").count(" ") == 0:
                work["contact_name"] = out.changes["contact_name"] = person["name"]
            _fill_contact(work, out, domain, [{"value": email, "source": "api.apollo.io", "name": person.get("name") or "",
                                               "title": person.get("title") or ""}], [], "apollo", ctx)


def _other_providers(work: dict, out: Outcome, domain: str, ctx: Context) -> None:
    for pid, cost in (("hunter", 1), ("firecrawl", 3)):
        if work.get("email") or not ctx.usable(pid):
            continue
        found = _paid(pid, cost, ctx, lambda p=pid: PROVIDERS[p]["find"](domain, work.get("company", ""), ctx.status[p]["config"],
                                                                         ctx.transports, work.get("contact_name") or ""))
        if found is None:
            continue
        ctx.credits += found.get("credits_used", cost) - cost            # replace the reserved amount by what it reports
        if found.get("error"):
            ctx.error(pid, found["error"])
        _fill_contact(work, out, domain, found.get("emails") or [], found.get("phones") or [], pid, ctx)


def enrich_lead(lead: dict, ctx: Context) -> Outcome:
    """Look one lead up and return what to fill (nothing is saved here). `out.stop` means the credit limit was reached."""
    out, work = Outcome(), dict(lead)
    domain = valid_domain(work.get("domain"))
    if not domain and ctx.usable("apollo") and (work.get("company") or "").strip():
        org = _paid("apollo", ORG_LOOKUP_CREDITS, ctx, lambda: apollo_org_by_name(work["company"], ctx))
        if org and valid_domain(org["domain"]):
            domain = valid_domain(org["domain"])
            for field in ("website", "domain", "apollo_org_id"):
                if org.get(field) and not work.get(field):
                    work[field] = out.changes[field] = org[field]
                    out.prov[field] = "apollo:organization search"
            out.notes.append(f"website {org['domain']}")
    if not domain:
        return out
    _crawl(work, out, domain, ctx)
    if ctx.usable("apollo") and (not work.get("email") or not work.get("contact_name")):
        _apollo(work, out, domain, ctx)
    _other_providers(work, out, domain, ctx)
    out.stop = ctx.out_of_budget
    return out


# ---------- saving ----------
EMAIL_FIELDS = ("email", "email_is_role", "email_source", "dedupe_key")


def _merge_people(current: list, added: list) -> list:
    known, merged = set(), []
    for c in [*current, *added]:
        key = (c.get("contact_name") or "").lower()
        if key not in known:
            known.add(key)
            merged.append(c)
    return merged[:MAX_OTHER_CONTACTS]


def save_outcome(store, lead_id: str, out: Outcome, label: str) -> bool:
    """Write the outcome onto the latest copy of the lead: empty fields only, early-stage leads outside the sequence only.

    The lead is stamped as checked, so the next click does not look it up (and pay for it) again at once, unless the credit
    limit cut its paid lookups short: then it stays unstamped and the next run picks it up first.
    Returns True when something was added."""
    done = {}
    suppressed = set(store.read("suppressed"))

    def change(lead: dict):
        open_lead = lead.get("stage") in pipe.EARLY_STAGES and not lead.get("sequence")
        filled = {k: v for k, v in out.changes.items() if open_lead and v not in (None, "") and (not lead.get(k) or k == "dedupe_key")
                  and k not in ("provenance", "other_contacts", CHECKED_MARKER)}
        if lead.get("email") or filled.get("email") in suppressed:
            filled = {k: v for k, v in filled.items() if k not in EMAIL_FIELDS}
        updated = {**lead, **filled, **({} if out.stop else {CHECKED_MARKER: now_iso()})}
        others = _merge_people(lead.get("other_contacts") or [], out.others if open_lead else [])
        if others != (lead.get("other_contacts") or []):
            updated["other_contacts"] = others
        prov = {f: v for f, v in out.prov.items() if f in filled}
        if prov:
            updated["provenance"] = {**(lead.get("provenance") or {}), **prov}
        if filled.get("email"):
            updated["email_is_role"] = is_role_address(filled["email"])
        done["changed"] = bool(filled) or "other_contacts" in updated and updated["other_contacts"] != (lead.get("other_contacts") or [])
        events = [event("enrichment", f"{label}: " + ", ".join(out.notes))] if done["changed"] and out.notes else []
        return {**updated, "timeline": [*lead["timeline"], *events]}
    store.update_lead(lead_id, change)
    return bool(done.get("changed"))
