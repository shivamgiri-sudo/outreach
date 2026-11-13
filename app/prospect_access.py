"""Ownership in the prospect database: contacts of people who are other members' CRM leads stay hidden from you.

A prospect linked (pdb_crm_links) to CRM leads is hidden for `user` when ownership is on in those leads' workspaces and every
one of those leads is hidden from them (lead_access.Hider). A hidden person keeps name, title and company (so the pool still
reads as a pool) but loses every way to contact them, and cannot be added, exported or acted on.
Prospects with no CRM link, or with at least one visible lead, are untouched."""
from __future__ import annotations

from typing import Optional

from . import lead_access
from .prospect_repo import repo_for

CONTACT_KEYS = ("crm_status", "email", "email_status", "email_confidence", "linkedin_url", "phones", "source_url", "provenance", "crm_leads")


def hidden_people(store, user: Optional[dict], person_ids) -> set:
    """Ids among `person_ids` whose contacts `user` may not see."""
    ids = [str(i) for i in dict.fromkeys(person_ids) if i]
    hider = lead_access.hidden_test(store, user)
    if not ids or hider is None:
        return set()
    repo = repo_for(store)
    links: dict = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for r in repo.q(f"SELECT person_id, lead_id FROM pdb_crm_links WHERE person_id IN ({', '.join('?' for _ in part)})", part):
            links.setdefault(r["person_id"], []).append(r["lead_id"])
    if not links:
        return set()
    leads = {l["id"]: l for l in store.read("leads")}
    out = set()
    for pid, lead_ids in links.items():
        rows = [leads[l] for l in lead_ids if l in leads]
        if rows and all(hider(row) for row in rows):
            out.add(pid)
    return out


def redact(person: dict) -> dict:
    """The person without any contact detail, marked `contact_hidden`."""
    out = {k: v for k, v in person.items() if k not in CONTACT_KEYS}
    out.update({"has_phone": False, "contact_hidden": True, "in_crm": True})
    if "crm_leads" in person:
        out["crm_leads"] = []
    return out


def apply(store, user: Optional[dict], people: list) -> list:
    """`people` (dicts with an id) with the hidden ones redacted."""
    hidden = hidden_people(store, user, [p.get("id") for p in people])
    return [redact(p) if p.get("id") in hidden else p for p in people]


def one(store, user: Optional[dict], person: Optional[dict]) -> Optional[dict]:
    return None if person is None else apply(store, user, [person])[0]
