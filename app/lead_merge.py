"""Find the existing lead a new row belongs to, and merge without ever overwriting.

Match keys, strongest first: same email; same Gmail inbox (dots and +tags ignored); same phone; same company domain
and same person; same company domain (company-level); similar company name (token-set similarity >= 0.92) in the
same city, or the exact same name when a city is unknown. A lead is a company: a different person at a matched
company is kept in other_contacts. Different non-empty values are logged in merge_conflicts for a person to decide.
"""
import uuid
from typing import Optional

from .hygiene import dedupe_key as basic_dedupe_key, is_role_address
from .lead_model import event
from .places import city_key, clean_city, size_bucket
from .standardise import (company_key, company_similarity, email_alias_key, email_domain, host_of, is_free_mail,
                          is_role_email, key_similarity, names_compatible, parse_phone, person_key, registrable_domain)

FUZZY_COMPANY_MIN = 0.92
COMPANY_FIELDS = ("company", "company_legal", "website", "domain", "company_linkedin_url", "sector",
                  "company_size", "company_size_bucket", "city", "state", "country", "pincode", "address", "open_roles",
                  "role", "max_job_age_days", "source_detail")
PERSON_FIELDS = ("contact_name", "title", "seniority", "department", "email", "linkedin_url")
CONTACT_FIELDS = ("contact_name", "title", "seniority", "department", "email", "phone", "linkedin_url")
CLOSED_STAGES = ("unsubscribed", "disqualified")


def _empty(value) -> bool:
    return value in (None, "", 0, [], {}, False)


def _same(field: str, a, b) -> bool:
    """Whether two non-empty values say the same thing (so no conflict)."""
    if field == "company":
        return company_similarity(a, b) >= FUZZY_COMPANY_MIN
    if field == "contact_name":
        return names_compatible(a, b)
    if field == "email":
        return email_alias_key(a) == email_alias_key(b)
    if field == "city":
        return city_key(a) == city_key(b)
    if field in ("title", "sector", "state", "country", "linkedin_url", "company_linkedin_url"):
        return str(a).strip().lower().rstrip("/") == str(b).strip().lower().rstrip("/")
    return a == b


CONFLICT_FIELDS = frozenset({"company", "domain", "sector", "company_size_bucket", "city", "state", "country",
                             "contact_name", "title", "email", "linkedin_url", "company_linkedin_url"})


def lead_domain(lead: dict) -> str:
    """Registrable company domain of a lead: its domain, else its website, else a business email."""
    for value in (lead.get("domain"), lead.get("website")):
        host = host_of(value)
        if host:
            return registrable_domain(host)
    email = lead.get("email") or ""
    return "" if not email or is_free_mail(email) else registrable_domain(email_domain(email))


def _li_key(url: str) -> str:
    return str(url).strip().lower().rstrip("/")


def _phone_key(value) -> str:
    found, _ = parse_phone(str(value or ""))
    return found[0] if found else ""


def has_person(rec: dict) -> bool:
    email = rec.get("email") or ""
    return bool(rec.get("contact_name") or rec.get("linkedin_url") or (email and not is_role_email(email)))


class LeadIndex:
    """Lookup tables over a list of leads (the list is changed in place as rows are merged or added)."""

    def __init__(self, leads: list):
        self.leads = leads
        self.email, self.alias, self.phone, self.domain_name, self.domain = {}, {}, {}, {}, {}
        self.company, self.blocks, self.keys = {}, {}, set()
        self.ckey, self.ldomain, self.linkedin = {}, {}, {}
        for i in range(len(leads)):
            self.add(i)

    def _people(self, lead: dict):
        yield "primary", lead
        for j, person in enumerate(lead.get("other_contacts") or []):
            yield ("other", j), person

    def add(self, i: int) -> None:
        lead = self.leads[i]
        domain = self.ldomain[i] = lead_domain(lead)
        key = self.ckey[i] = company_key(lead.get("company"))
        self.keys.add(lead.get("dedupe_key"))
        for who, person in self._people(lead):
            emails = [person.get("email")] + (list(lead.get("emails") or []) if who == "primary" else [])
            for email in filter(None, emails):
                self.email.setdefault(email, (i, who))
                self.alias.setdefault(email_alias_key(email), (i, who))
            phones = [person.get("phone")] + list(person.get("phones") or [])
            for phone in filter(None, map(_phone_key, phones)):
                self.phone.setdefault(phone, (i, who))
            if person.get("linkedin_url"):
                self.linkedin.setdefault(_li_key(person["linkedin_url"]), (i, who))
            name = person_key(person.get("contact_name"))
            if domain and name:
                self.domain_name.setdefault((domain, name), (i, who))
        if domain:
            self.domain.setdefault(domain, i)
        if key:
            self.company.setdefault(key, [])
            if i not in self.company[key]:
                self.company[key].append(i)
            block = (city_key(lead.get("city")), key[:3])
            if block[0]:
                self.blocks.setdefault(block, [])
                if i not in self.blocks[block]:
                    self.blocks[block].append(i)

    def _person_of(self, i: int, who):
        lead = self.leads[i]
        return lead if who == "primary" else (lead.get("other_contacts") or [])[who[1]]

    def _check_person(self, i: int, who, rec: dict, reason: str, strong: bool) -> tuple:
        """A shared phone or a role inbox with a different name is the same company, not the same person."""
        person = self._person_of(i, who)
        names_differ = not names_compatible(rec.get("contact_name"), person.get("contact_name"))
        if names_differ and not strong:
            return i, "company", f"{reason} (different person)"
        return i, who, reason

    def match(self, rec: dict) -> Optional[tuple]:
        """(lead index, who, reason) where who is 'primary', ('other', j) or 'company'; None for a new lead."""
        emails = rec.get("emails") or ([rec["email"]] if rec.get("email") else [])
        for email in emails:
            if email in self.email:
                i, who = self.email[email]
                return self._check_person(i, who, rec, "same email", strong=not is_role_email(email))
        for email in emails:
            alias = email_alias_key(email)
            if alias in self.alias:
                i, who = self.alias[alias]
                return self._check_person(i, who, rec, "same Gmail inbox (dots or +tag differ)", strong=True)
        if rec.get("linkedin_url") and _li_key(rec["linkedin_url"]) in self.linkedin:
            i, who = self.linkedin[_li_key(rec["linkedin_url"])]
            return i, who, "same LinkedIn profile"
        for phone in rec.get("phones") or []:
            if phone in self.phone:
                i, who = self.phone[phone]
                key, other = company_key(rec.get("company")), self.ckey[i]
                if key and other and key_similarity(key, other) < 0.5 and lead_domain(rec) != self.ldomain[i]:
                    continue                     # a shared board line at two unrelated companies
                return self._check_person(i, who, rec, "same phone", strong=False)
        domain, name = rec.get("domain") or "", person_key(rec.get("contact_name"))
        if domain and name and (domain, name) in self.domain_name:
            i, who = self.domain_name[(domain, name)]
            return i, who, "same company domain and person"
        if domain and domain in self.domain:
            return self.domain[domain], "company", "same company domain"
        return self._company_match(rec, domain)

    def _company_match(self, rec: dict, domain: str) -> Optional[tuple]:
        key, city = company_key(rec.get("company")), city_key(rec.get("city"))
        if not key:
            return None
        clash = lambda i: domain and self.ldomain[i] and self.ldomain[i] != domain
        for i in self.company.get(key, []):
            other_city = city_key(self.leads[i].get("city"))
            if clash(i) or (city and other_city and city != other_city):
                continue
            return i, "company", "same company name and city" if city and other_city else "same company name"
        if city:
            for i in self.blocks.get((city, key[:3]), []):
                score = key_similarity(key, self.ckey[i], FUZZY_COMPANY_MIN)
                if score >= FUZZY_COMPANY_MIN and not clash(i):
                    return i, "company", f"similar company name in the same city ({round(score * 100)}%)"
        return None


def _new_conflict(field: str, kept, incoming, source: str, import_id: str, at: str) -> dict:
    return {"id": uuid.uuid4().hex[:8], "field": field, "kept": kept, "incoming": incoming, "source": source,
            "import_id": import_id, "at": at}


def _fill(target: dict, rec: dict, fields, source: str, info: dict, record_conflicts: bool, import_id: str, at: str):
    for field in fields:
        incoming = rec.get(field)
        if _empty(incoming):
            continue
        current = target.get(field)
        if _empty(current):
            target[field] = incoming
            if record_conflicts is not None:
                target.setdefault("field_sources", {})[field] = source
            info["filled"].append(field)
        elif record_conflicts and field in CONFLICT_FIELDS and not _same(field, current, incoming):
            existing = target.get("merge_conflicts") or []
            if not any(c["field"] == field and c["incoming"] == incoming for c in existing):
                target["merge_conflicts"] = [*existing, _new_conflict(field, current, incoming, source, import_id, at)]
                info["conflicts"].append({"field": field, "kept": current, "incoming": incoming})


def _union(*lists) -> list:
    return list(dict.fromkeys(v for items in lists for v in (items or []) if v))


def _put_lists(lead: dict, rec: dict) -> None:
    """Add the row's phones and emails to the lead's lists (lists are only written when there is something in them)."""
    for key, single in (("phones", "phone"), ("emails", "email")):
        values = _union(lead.get(key) or ([lead[single]] if lead.get(single) else []), rec.get(key))
        if values and values != lead.get(key):
            lead[key] = values


def _without_timeline(lead: dict) -> dict:
    return {k: v for k, v in lead.items() if k != "timeline"}


def contact_entry(rec: dict, source: str, at: str) -> dict:
    entry = {f: rec.get(f) for f in CONTACT_FIELDS if not _empty(rec.get(f))}
    if rec.get("phones"):
        entry["phones"] = list(rec["phones"])
    return {**entry, "source": source, "added_at": at}


def merge_record(lead: dict, rec: dict, who, source: str, import_id: str, at: str, taken_keys: set) -> tuple:
    """Fill the lead's gaps from a standardised row. Returns (new lead, info). Stage and consent are never touched."""
    original = lead
    lead = {**lead, "timeline": list(lead.get("timeline") or [])}
    info = {"filled": [], "conflicts": [], "added_contact": None, "changed": False}
    person_less = not lead.get("contact_name") and not lead.get("email")
    if who == "company" and has_person(rec) and person_less:
        who = "primary"
    _fill(lead, rec, COMPANY_FIELDS, source, info, True, import_id, at)
    if "domain" in info["filled"] and rec.get("website_guess"):
        lead["website_guess"] = True
    if who == "primary":
        _fill(lead, rec, PERSON_FIELDS, source, info, True, import_id, at)
        if not lead.get("phone") and rec.get("phone"):
            lead["phone"], lead["phone_type"] = rec["phone"], rec.get("phone_type", "")
            lead.setdefault("field_sources", {})["phone"] = source
            info["filled"].append("phone")
        _put_lists(lead, rec)
    elif isinstance(who, tuple):
        contacts = [dict(c) for c in lead.get("other_contacts") or []]
        _fill(contacts[who[1]], {**rec, "phone": rec.get("phone")}, CONTACT_FIELDS, source, info, None, import_id, at)
        phones = _union(contacts[who[1]].get("phones"), rec.get("phones"))
        if phones:
            contacts[who[1]]["phones"] = phones
        lead["other_contacts"] = contacts
    elif has_person(rec):
        contacts = list(lead.get("other_contacts") or [])
        people = [c.get("contact_name") for c in contacts] + [lead.get("contact_name")]
        if not rec.get("contact_name") or not any(p and names_compatible(rec["contact_name"], p) for p in people):
            contacts.append(contact_entry(rec, source, at))
            info["added_contact"] = rec.get("contact_name") or rec.get("email") or "a contact"
        lead["other_contacts"] = contacts
    else:
        _put_lists(lead, rec)
        if not lead.get("phone") and rec.get("phone"):
            lead["phone"], lead["phone_type"] = rec["phone"], rec.get("phone_type", "")
            info["filled"].append("phone")
        if not lead.get("email") and rec.get("email"):
            lead["email"] = rec["email"]
            info["filled"].append("email")
    if "email" in info["filled"] and lead.get("email"):
        lead["email_is_role"] = is_role_address(lead["email"])
    links = _union(lead.get("job_links"), rec.get("job_links"))
    if links:
        lead["job_links"] = links
    new_key = basic_dedupe_key(lead)
    if new_key and new_key != lead.get("dedupe_key") and new_key not in taken_keys and (
            not str(lead.get("dedupe_key") or "").startswith("email:")):
        taken_keys.discard(lead.get("dedupe_key"))
        lead["dedupe_key"] = new_key
        taken_keys.add(new_key)
    if _without_timeline(lead) == _without_timeline(original):
        return original, info
    info["changed"] = True
    n = len(info["filled"])
    text = f"Merged from {source} ({n} field{'' if n == 1 else 's'} filled)"
    if info["added_contact"]:
        text += f"; added {info['added_contact']} as another contact"
    if info["conflicts"]:
        text += f"; {len(info['conflicts'])} different value(s) kept for review"
    lead["timeline"].append(event("merged", text))
    return lead, info


# ---------------------------------------------------------------- merging two existing leads
STAGE_RANK = {"new": 0, "verified": 1, "enriched": 2, "not_now": 3, "contacted": 3, "engaged": 4, "interested": 5,
              "whatsapp_active": 6, "call_done": 7, "trial": 8, "customer": 9, "advocate": 10}


def merged_stage(leads: list) -> str:
    """Unsubscribed beats everything, then disqualified; otherwise the most advanced stage."""
    stages = [lead.get("stage") or "new" for lead in leads]
    for closed in ("unsubscribed", "disqualified"):
        if closed in stages:
            return closed
    return max(stages, key=lambda s: STAGE_RANK.get(s, 0))


def completeness(lead: dict) -> int:
    return sum(1 for f in ("company", "contact_name", "title", "email", "phone", "domain", "city", "linkedin_url")
               if lead.get(f))


def merge_leads(keep: dict, others: list, at: str, taken: Optional[set] = None) -> dict:
    """Merge duplicate leads into `keep`: fill gaps, keep every person, phone and event, keep the strictest status."""
    lead = {**keep, "timeline": list(keep.get("timeline") or [])}
    all_leads = [keep, *others]
    taken = set(taken or ())
    for other in others:
        source = f"{other.get('source') or 'lead'} (lead {other['id']})"
        rec = {**other, "emails": other.get("emails") or ([other["email"]] if other.get("email") else []),
               "phones": other.get("phones") or ([other["phone"]] if other.get("phone") else [])}
        same_person = names_compatible(other.get("contact_name"), lead.get("contact_name"))
        lead, info = merge_record(lead, rec, "primary" if same_person else "company", source, "", at, taken)
        if info["changed"]:
            lead = {**lead, "timeline": lead["timeline"][:-1]}   # replaced by one summary event below
        for person in other.get("other_contacts") or []:
            known = {person_key(c.get("contact_name")) for c in lead.get("other_contacts") or []}
            if person_key(person.get("contact_name")) not in known | {person_key(lead.get("contact_name"))}:
                lead["other_contacts"] = [*(lead.get("other_contacts") or []), person]
        lead["timeline"] += list(other.get("timeline") or [])
        phones = _union(lead.get("phones") or ([lead["phone"]] if lead.get("phone") else []), rec["phones"])
        if phones:
            lead["phones"] = phones
        carried = [c for c in other.get("merge_conflicts") or [] if c not in (lead.get("merge_conflicts") or [])]
        if carried:
            lead["merge_conflicts"] = [*(lead.get("merge_conflicts") or []), *carried]
        for field, origin in (other.get("field_sources") or {}).items():
            if (lead.get("field_sources") or {}).get(field, "").endswith(f"(lead {other['id']})"):
                lead["field_sources"][field] = origin
    lead["timeline"] = sorted(lead["timeline"], key=lambda e: e.get("at") or "")
    lead["stage"] = merged_stage(all_leads)
    withdrawn = sorted(filter(None, (x.get("consent_withdrawn_at") for x in all_leads)))
    if lead["stage"] == "unsubscribed" or withdrawn:
        lead["consent"] = None
        lead["consent_withdrawn_at"] = withdrawn[-1] if withdrawn else lead.get("consent_withdrawn_at") or at
    else:
        lead["consent"] = keep.get("consent") or next((x["consent"] for x in others if x.get("consent")), None)
    if not keep.get("sequence"):
        lead["sequence"] = next((x["sequence"] for x in others if x.get("sequence")), None)
        if lead["sequence"] is None:
            lead.pop("sequence", None)
    for key in ("activation", "demo_token", "referral_code"):
        if not lead.get(key):
            found = next((x[key] for x in others if x.get(key)), None)
            if found:
                lead[key] = found
    engagement = dict(keep.get("engagement") or {})
    for other in others:
        for k, v in (other.get("engagement") or {}).items():
            engagement[k] = (engagement.get(k) or 0) + (v or 0) if isinstance(v, (int, float)) else engagement.get(k, v)
    lead["engagement"] = engagement
    lead["merged_ids"] = _union(keep.get("merged_ids"), *[[x["id"], *(x.get("merged_ids") or [])] for x in others])
    lead["company_size_bucket"] = lead.get("company_size_bucket") or size_bucket(lead.get("company_size") or 0)
    if lead.get("city"):
        lead["city"] = clean_city(lead["city"])
    names = ", ".join(x.get("company") or x.get("contact_name") or x["id"] for x in others)
    lead["timeline"].append(event("merged", f"Merged {len(others)} duplicate lead(s) into this one: {names}"))
    return lead
