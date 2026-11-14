"""Duplicate detection that only looks. Nothing here saves, merges or deletes.

preview(): before an import, which rows of the file are already in the app, repeat an earlier row of the same file, or look like
a lead you already have. It uses the same account scoping as the import (one client account never sees another's leads) and says
nothing about a lead the person may not see: such a row is only reported as {kind: "exists"}.

existing_groups(): leads already in the app that share an email, a phone number or a LinkedIn profile (read-only report).

Kinds: exists (same email / phone / LinkedIn profile / company domain + contact name as a lead),
       in_file (same as an earlier row of this file),
       likely (same company and contact name as a lead, but a different email).
"""
from collections import defaultdict
from typing import Callable, Optional

from .hygiene import normalise_email, normalise_phone
from .lead_merge import _li_key, lead_domain
from .standardise import company_key, person_key

PREVIEW_ROWS = 200
GROUPS_MAX = 200
KIND_TEXT = {"exists": "Already in the app", "in_file": "Repeats an earlier row in this file", "likely": "Looks like a lead you already have"}
REASON = {"email": "same email", "phone": "same phone number", "linkedin": "same LinkedIn profile", "domain_name": "same company website and contact name"}


def _emails(p: dict) -> list:
    return [e for e in {normalise_email(x) for x in [p.get("email"), *(p.get("emails") or [])]} if e]


def _phones(p: dict) -> list:
    return [x for x in {normalise_phone(v) for v in [p.get("phone"), *(p.get("phones") or [])]} if x]


def keys_of(p: dict, domain: str = "") -> list:
    """[(kind, value)] identity keys for a lead or an import record (a contact of a lead counts as well)."""
    out = [("email", e) for e in _emails(p)] + [("phone", x) for x in _phones(p)]
    if p.get("linkedin_url"):
        out.append(("linkedin", _li_key(p["linkedin_url"])))
    name = person_key(p.get("contact_name"))
    if domain and name:
        out.append(("domain_name", f"{domain}|{name}"))
    return out


def _people(lead: dict):
    yield lead
    yield from (c for c in lead.get("other_contacts") or [] if isinstance(c, dict))


def _index(leads: list) -> tuple:
    exact, loose = {}, {}
    for lead in leads:
        domain = lead_domain(lead)
        for person in _people(lead):
            for key in keys_of(person, domain):
                exact.setdefault(key, lead)
            name, company = person_key(person.get("contact_name")), company_key(lead.get("company"))
            if name and company:
                loose.setdefault((company, name), lead)
    return exact, loose


def preview(records: list, existing: list, can_see: Callable[[dict], bool], same_account: Optional[Callable[[dict], bool]] = None,
            limit: Optional[int] = PREVIEW_ROWS) -> dict:
    """records: [(row number, import record)] (app/lead_import.extract_record). existing: the account's leads.
    Returns {counts, total, shown, rows}; rows hold {row, kind[, match_lead_id, match_company, owner, matched_by]}."""
    leads = [l for l in existing if same_account is None or same_account(l)]
    exact, loose = _index(leads)
    seen_file: dict = {}
    flagged, counts = [], defaultdict(int)
    for n, rec in records:
        domain = lead_domain(rec)
        keys = keys_of(rec, domain)
        hit = next(((k[0], exact[k]) for k in keys if k in exact), None)
        entry = None
        if hit:
            kind, lead = hit
            entry = {"row": n, "kind": "exists"}
            if can_see(lead):
                entry.update(match_lead_id=lead["id"], match_company=lead.get("company") or "", owner=lead.get("owner") or "",
                             matched_by=REASON[kind])
        else:
            first = next((seen_file[k] for k in keys if k in seen_file), None)
            if first is not None:
                entry = {"row": n, "kind": "in_file", "same_as_row": first, "matched_by": "same as row %d" % first}
            else:
                name, company = person_key(rec.get("contact_name")), company_key(rec.get("company"))
                lead = loose.get((company, name)) if name and company else None
                if lead is not None:
                    entry = {"row": n, "kind": "likely"}
                    if can_see(lead):
                        entry.update(match_lead_id=lead["id"], match_company=lead.get("company") or "", owner=lead.get("owner") or "",
                                     matched_by="same company and contact name, different email")
                    else:
                        entry["kind"] = "exists"
        for k in keys:
            seen_file.setdefault(k, n)
        if entry:
            counts[entry["kind"]] += 1
            flagged.append(entry)
    return {"total": len(records), "counts": {k: counts.get(k, 0) for k in KIND_TEXT}, "flagged": len(flagged),
            "shown": len(flagged) if limit is None else min(len(flagged), limit), "rows": flagged if limit is None else flagged[:limit]}


def existing_groups(leads: list, account_of: Callable[[dict], str], limit: int = GROUPS_MAX) -> dict:
    """Groups of leads that share an email, phone or LinkedIn profile (within one account). `leads` must already be the
    ones the caller may see. Returns {groups, total_groups, shown}."""
    parent: dict = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    owner_of: dict = {}
    why: dict = defaultdict(set)
    for lead in leads:
        acct = account_of(lead)
        find(lead["id"])
        for kind, value in keys_of(lead):
            if kind == "domain_name":
                continue
            key = (acct, kind, value)
            if key in owner_of:
                parent[find(lead["id"])] = find(owner_of[key])
                why[key].add(lead["id"]); why[key].add(owner_of[key])
            else:
                owner_of[key] = lead["id"]
    members = defaultdict(list)
    by_id = {l["id"]: l for l in leads}
    for lead in leads:
        members[find(lead["id"])].append(lead["id"])
    reasons = defaultdict(set)
    for (acct, kind, value), ids in why.items():
        if ids:
            reasons[find(next(iter(ids)))].add(REASON[kind])
    groups = []
    for root, ids in members.items():
        if len(ids) < 2:
            continue
        groups.append({"reasons": sorted(reasons.get(root, [])), "leads": [
            {k: by_id[i].get(k) or "" for k in ("id", "company", "contact_name", "email", "phone", "stage", "owner")}
            | {"history": len(by_id[i].get("timeline") or [])} for i in ids[:20]],
            "size": len(ids)})
    groups.sort(key=lambda g: (-g["size"], g["leads"][0]["company"].lower()))
    return {"total_groups": len(groups), "shown": min(len(groups), limit), "groups": groups[:limit]}
