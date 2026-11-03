"""Data hygiene on saved leads: what is missing or wrong, which leads are duplicates (and why), open merge conflicts,
stale leads, and re-applying the import clean-up rules to leads saved before them."""
from datetime import datetime, timedelta, timezone

from .lead_merge import FUZZY_COMPANY_MIN, STAGE_RANK, completeness, lead_domain
from .lead_model import event
from .places import city_key, clean_city, clean_country, clean_state, is_indian_place, size_bucket, state_for_city
from .standardise import (clean_company, clean_email, clean_person_name, clean_title, company_key, key_similarity,
                          department_of, email_alias_key, host_of, is_free_mail, is_role_email, names_compatible,
                          parse_phone, person_key, seniority_of, website_domain)

STALE_DAYS = 90
PROSPECT_STAGES = ("new", "verified", "enriched", "contacted", "not_now")
CLUSTERS_SHOWN = 200


def _phones(lead: dict) -> list:
    raw = [lead.get("phone"), *(lead.get("phones") or [])]
    return list(dict.fromkeys(found[0] for found, _ in (parse_phone(str(p)) for p in raw if p) if found))


def _emails(lead: dict) -> list:
    return list(dict.fromkeys(e for e in [lead.get("email"), *(lead.get("emails") or [])] if e))


class _Clusters:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.why = {}

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def join(self, a: int, b: int, reason: str) -> None:
        if a == b:
            return
        self.why.setdefault((min(a, b), max(a, b)), reason)
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _bucket_join(clusters: _Clusters, buckets: dict, reason: str) -> None:
    for members in buckets.values():
        for other in members[1:]:
            clusters.join(members[0], other, reason)


def duplicate_clusters(leads: list) -> list:
    """Groups of leads that are the same person or company, with the reason each pair matched."""
    clusters = _Clusters(len(leads))
    by = {k: {} for k in ("email", "alias", "phone", "domain_name", "domain", "company")}
    for i, lead in enumerate(leads):
        for email in _emails(lead):
            by["email"].setdefault(email, []).append(i)
            by["alias"].setdefault(email_alias_key(email), []).append(i)
        for phone in _phones(lead):
            by["phone"].setdefault(phone, []).append(i)
        domain, name = lead_domain(lead), person_key(lead.get("contact_name"))
        if domain and name:
            by["domain_name"].setdefault((domain, name), []).append(i)
        if domain:
            by["domain"].setdefault(domain, []).append(i)
        if company_key(lead.get("company")):
            by["company"].setdefault(company_key(lead["company"]), []).append(i)
    _bucket_join(clusters, by["email"], "same email")
    _bucket_join(clusters, by["alias"], "same Gmail inbox (dots or +tag differ)")
    for members in by["phone"].values():
        for other in members[1:]:
            a, b = leads[members[0]], leads[other]
            if names_compatible(a.get("contact_name"), b.get("contact_name")):
                clusters.join(members[0], other, "same phone")
    _bucket_join(clusters, by["domain_name"], "same company domain and person")
    for members in by["domain"].values():
        company_only = [i for i in members if not leads[i].get("contact_name")]
        for i in company_only:
            for other in members:
                clusters.join(i, other, "same company domain (one has no contact person)")
    _company_pairs(leads, clusters, by["company"])
    groups = {}
    for i in range(len(leads)):
        groups.setdefault(clusters.find(i), []).append(i)
    out = []
    for members in (m for m in groups.values() if len(m) > 1):
        reasons = [{"a": leads[a]["id"], "b": leads[b]["id"], "why": why} for (a, b), why in clusters.why.items()
                   if a in members and b in members]
        keep = max(members, key=lambda i: (bool(leads[i].get("sequence")), len(leads[i].get("timeline") or []),
                                           STAGE_RANK.get(leads[i].get("stage"), 11), completeness(leads[i])))
        out.append({"lead_ids": [leads[i]["id"] for i in members], "reasons": reasons,
                    "why": sorted({r["why"] for r in reasons}), "suggested_keep": leads[keep]["id"],
                    "leads": [summary(leads[i]) for i in members]})
    return sorted(out, key=lambda c: -len(c["lead_ids"]))


def _company_pairs(leads: list, clusters: _Clusters, by_company: dict) -> None:
    keys = [company_key(lead.get("company")) for lead in leads]
    domains = [lead_domain(lead) for lead in leads]
    cities = [city_key(lead.get("city")) for lead in leads]
    clash = lambda i, j: domains[i] and domains[j] and domains[i] != domains[j]
    for members in by_company.values():
        for x, i in enumerate(members):
            for j in members[x + 1:]:
                if not clash(i, j) and (not cities[i] or not cities[j] or cities[i] == cities[j]):
                    clusters.join(i, j, "same company name" + (" and city" if cities[i] and cities[j] else ""))
    blocks = {}
    for i in range(len(leads)):
        if keys[i] and cities[i]:
            blocks.setdefault((cities[i], keys[i][:3]), []).append(i)
    for members in blocks.values():
        for x, i in enumerate(members):
            for j in members[x + 1:]:
                if keys[i] == keys[j] or clash(i, j):
                    continue
                score = key_similarity(keys[i], keys[j], FUZZY_COMPANY_MIN)
                if score >= FUZZY_COMPANY_MIN:
                    clusters.join(i, j, f"similar company name in the same city ({round(score * 100)}%)")


SUMMARY_FIELDS = ("id", "company", "contact_name", "title", "email", "phone", "domain", "city", "stage", "source",
                  "created_at", "sector", "company_size", "role")


def summary(lead: dict) -> dict:
    return {**{f: lead.get(f) for f in SUMMARY_FIELDS}, "events": len(lead.get("timeline") or []),
            "other_contacts": len(lead.get("other_contacts") or []), "emailed": bool(lead.get("sequence")),
            "has_consent": bool(lead.get("consent")), "phones": _phones(lead)}


def _last_activity(lead: dict) -> str:
    stamps = [e.get("at") or "" for e in lead.get("timeline") or []] + [lead.get("created_at") or ""]
    return max(stamps)


def report(leads: list, suppressed: set, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    stale_before = (now - timedelta(days=STALE_DAYS)).isoformat(timespec="seconds")
    emails = [lead.get("email") for lead in leads if lead.get("email")]
    conflicts = [{"lead_id": lead["id"], "company": lead.get("company") or lead.get("domain"), **c}
                 for lead in leads for c in lead.get("merge_conflicts") or []]
    clusters = duplicate_clusters(leads)
    needs_clean = sum(1 for lead in leads if standardise_changes(lead))
    return {
        "total": len(leads),
        "missing": {"email": sum(1 for lead in leads if not lead.get("email")),
                    "phone": sum(1 for lead in leads if not lead.get("phone") and not lead.get("phones")),
                    "decision_maker": sum(1 for lead in leads if not lead.get("contact_name")),
                    "website": sum(1 for lead in leads if not lead.get("domain"))},
        "emails": {"invalid": sum(1 for e in emails if clean_email(e)[0] != e),
                   "role": sum(1 for e in emails if is_role_email(e)),
                   "free_mail": sum(1 for e in emails if is_free_mail(e)),
                   "suppressed": sum(1 for e in emails if e in suppressed)},
        "foreign_phones": sum(1 for lead in leads if any(not p.startswith("+91") for p in _phones(lead))),
        "stale": sum(1 for lead in leads if lead.get("stage") in PROSPECT_STAGES and _last_activity(lead) < stale_before),
        "stale_days": STALE_DAYS,
        "conflicts": len(conflicts), "conflict_list": conflicts[:CLUSTERS_SHOWN],
        "duplicate_clusters": len(clusters), "duplicate_leads": sum(len(c["lead_ids"]) for c in clusters),
        "clusters": clusters[:CLUSTERS_SHOWN],
        "needs_standardising": needs_clean,
    }


# ---------------------------------------------------------------- standardise saved leads
def standardise_changes(lead: dict) -> dict:
    """{field: new value} that the import clean-up rules would change on a saved lead (empty when already clean).
    Email, stage, consent and sequence are never touched."""
    changes = {}

    def put(field, value):
        if value != lead.get(field) and not (value in ("", None) and lead.get(field) in ("", None)):
            changes[field] = value
    if lead.get("company"):
        display, legal = clean_company(lead["company"])
        if display:
            put("company", display)
            if legal and not lead.get("company_legal"):
                put("company_legal", legal)
    if lead.get("contact_name"):
        put("contact_name", clean_person_name(lead["contact_name"]) or lead["contact_name"])
    if lead.get("title"):
        title = clean_title(lead["title"])
        put("title", title)
        put("seniority", seniority_of(title) or lead.get("seniority"))
        put("department", department_of(title) or lead.get("department"))
    phones = _phones(lead)
    if lead.get("phone"):
        found, _ = parse_phone(str(lead["phone"]))
        if found:
            put("phone", found[0])
            put("phone_type", found[1])
    if phones and phones != lead.get("phones"):
        put("phones", phones)
    host = host_of(lead.get("domain")) or host_of(lead.get("website"))
    if host:
        domain, _, problem = website_domain(host)
        if domain and not problem:
            put("domain", domain)
    if lead.get("city"):
        put("city", clean_city(lead["city"]))
    country = clean_country(lead.get("country")) if lead.get("country") else ""
    city = changes.get("city", lead.get("city") or "")
    state = clean_state(lead.get("state"), country) if lead.get("state") else state_for_city(city)
    if not country and is_indian_place(city, state):
        country = "IN"
    if country:
        put("country", country)
    if state:
        put("state", state)
    if lead.get("company_size") and not lead.get("company_size_bucket"):     # an import's '1,000+' bucket is kept
        put("company_size_bucket", size_bucket(lead["company_size"]))
    return changes


def apply_standardise(lead: dict, changes: dict) -> dict:
    names = ", ".join(sorted(changes))
    return {**lead, **changes, "timeline": [*(lead.get("timeline") or []), event("standardised", f"Tidied: {names}")]}
