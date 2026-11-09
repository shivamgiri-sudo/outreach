"""More ways to reach a lead, and more people at its company, each change logged.

- Extra phone numbers and email addresses on a lead (lead["phones"], lead["emails"]); either can be made the main one.
- Other people at the company (lead["other_contacts"]): name, designation, email, phone, LinkedIn profile.
Every add / change / removal goes to the lead's timeline and to the audit collection (who, when, old, new), the same
rows the Change log shows. Values are validated as hand edits are; an address on the do-not-contact list is refused.
"""
import uuid
from typing import Optional

from .audit import MAX_AUDIT_ROWS, EditRefused, _clean, edit_contact
from .lead_model import event, now_iso

KINDS = {"phone": ("phones", "Extra phone"), "email": ("emails", "Extra email")}
PERSON_FIELDS = {"contact_name": "Name", "title": "Designation", "email": "Email", "phone": "Phone", "linkedin_url": "LinkedIn profile"}
MAX_EXTRA = 10
MAX_PEOPLE = 50


def _lead(store, lead_id: str) -> dict:
    lead = next((l for l in store.read("leads") if l["id"] == lead_id), None)
    if lead is None:
        raise LookupError("Lead not found")
    return lead


def _log(store, lead_id: str, rows: list, actor: dict, text: str) -> dict:
    stamp = now_iso()
    rows = [{**r, "lead_id": lead_id, "by": actor["name"], "by_email": actor["email"], "at": stamp} for r in rows]
    saved = store.update_lead(lead_id, lambda l: {**l, "timeline": [*l["timeline"], {**event("edit", f"{actor['name']} {text}"), "by": actor["name"]}]})
    store.update("audit", lambda old: [*old, *rows][-MAX_AUDIT_ROWS:])
    return saved


def _not_blocked(store, kind: str, value: str) -> None:
    if kind == "email" and value in set(store.read("suppressed")):
        raise EditRefused("That address is on the do-not-contact list.")


def add_point(store, lead_id: str, kind: str, raw: str, actor: dict) -> dict:
    if kind not in KINDS:
        raise EditRefused("Add a phone or an email.")
    value = _clean(kind, raw)
    if not value:
        raise EditRefused(f"Enter the {kind}.")
    _not_blocked(store, kind, value)
    if kind == "email":
        from .email_domain import problem
        bad = problem(value)
        if bad:
            raise EditRefused(f"Not added: {bad}.")
    key, label = KINDS[kind]
    lead = _lead(store, lead_id)
    have = [v for v in (lead.get(key) or []) if v]
    if value == lead.get(kind) or value in have:
        raise EditRefused(f"This lead already has that {kind}.")
    if len(have) >= MAX_EXTRA:
        raise EditRefused(f"At most {MAX_EXTRA} extra {kind}s.")
    store.update_lead(lead_id, lambda l: {**l, key: [*[v for v in (l.get(key) or []) if v], value]})
    return _log(store, lead_id, [{"field": key, "label": label, "old": "", "new": value, "company": lead.get("company") or ""}],
                actor, f"added {label.lower()} {value}.")


def remove_point(store, lead_id: str, kind: str, value: str, actor: dict) -> dict:
    if kind not in KINDS:
        raise EditRefused("Remove a phone or an email.")
    key, label = KINDS[kind]
    lead = _lead(store, lead_id)
    if value not in (lead.get(key) or []) or value == lead.get(kind):
        raise EditRefused(f"That is not one of this lead's extra {kind}s (change the main one with Edit details).")
    store.update_lead(lead_id, lambda l: {**l, key: [v for v in (l.get(key) or []) if v != value] or None})
    return _log(store, lead_id, [{"field": key, "label": label, "old": value, "new": "", "company": lead.get("company") or ""}],
                actor, f"removed {label.lower()} {value}.")


def make_primary(store, lead_id: str, kind: str, value: str, actor: dict) -> dict:
    """The extra number / address becomes the main one; the old main one stays as an extra."""
    if kind not in KINDS:
        raise EditRefused("Pick a phone or an email.")
    key, _label = KINDS[kind]
    lead = _lead(store, lead_id)
    if value not in (lead.get(key) or []):
        raise EditRefused(f"That is not one of this lead's extra {kind}s.")
    old = lead.get(kind)
    result = edit_contact(store, lead_id, {kind: value}, actor, f"made the extra {kind} the main one")
    store.update_lead(lead_id, lambda l: {**l, key: list(dict.fromkeys(v for v in [*(l.get(key) or []), old] if v and v != value)) or None})
    return result


def _person(raw: dict) -> dict:
    out = {}
    for field in PERSON_FIELDS:
        value = raw.get(field)
        if value is None:
            continue
        out[field] = _clean(field, value) if field in ("email", "phone", "linkedin_url") else " ".join(str(value).split())[:200]
    return out


def add_person(store, lead_id: str, raw: dict, actor: dict) -> dict:
    person = _person(raw)
    if not person.get("contact_name"):
        raise EditRefused("Give the person's name.")
    if not any(person.get(f) for f in ("email", "phone", "linkedin_url")):
        raise EditRefused("Give at least an email, a phone or a LinkedIn profile.")
    if person.get("email"):
        _not_blocked(store, "email", person["email"])
    lead = _lead(store, lead_id)
    if len(lead.get("other_contacts") or []) >= MAX_PEOPLE:
        raise EditRefused(f"At most {MAX_PEOPLE} people on one lead.")
    person = {"id": uuid.uuid4().hex[:8], **person, "added_by": actor["name"], "added_at": now_iso(), "source": "hand"}
    store.update_lead(lead_id, lambda l: {**l, "other_contacts": [*(l.get("other_contacts") or []), person]})
    rows = [{"field": f"person.{f}", "label": f"Person: {PERSON_FIELDS[f]}", "old": "", "new": person[f], "company": lead.get("company") or ""}
            for f in PERSON_FIELDS if person.get(f)]
    return _log(store, lead_id, rows, actor, f"added {person['contact_name']}{' (' + person['title'] + ')' if person.get('title') else ''} at this company.")


def _find_person(lead: dict, person_id: str) -> Optional[int]:
    return next((i for i, p in enumerate(lead.get("other_contacts") or []) if str(p.get("id") or i) == person_id), None)


def change_person(store, lead_id: str, person_id: str, raw: dict, actor: dict) -> dict:
    lead = _lead(store, lead_id)
    i = _find_person(lead, person_id)
    if i is None:
        raise LookupError("Person not found")
    before = lead["other_contacts"][i]
    new = _person(raw)
    if new.get("email") and new["email"] != before.get("email"):
        _not_blocked(store, "email", new["email"])
    changed = {f: v for f, v in new.items() if (before.get(f) or "") != (v or "")}
    if not changed:
        return lead
    after = {**before, **changed, "id": before.get("id") or person_id}
    if not after.get("contact_name"):
        raise EditRefused("Give the person's name.")
    store.update_lead(lead_id, lambda l: {**l, "other_contacts": [after if j == i else p for j, p in enumerate(l.get("other_contacts") or [])]})
    rows = [{"field": f"person.{f}", "label": f"Person: {PERSON_FIELDS[f]}", "old": before.get(f) or "", "new": v, "company": lead.get("company") or ""}
            for f, v in changed.items()]
    lines = "; ".join(f"{PERSON_FIELDS[f]}: {before.get(f) or '(empty)'} → {v or '(empty)'}" for f, v in changed.items())
    return _log(store, lead_id, rows, actor, f"edited {after['contact_name']}: {lines}.")


def remove_person(store, lead_id: str, person_id: str, actor: dict) -> dict:
    lead = _lead(store, lead_id)
    i = _find_person(lead, person_id)
    if i is None:
        raise LookupError("Person not found")
    gone = lead["other_contacts"][i]
    store.update_lead(lead_id, lambda l: {**l, "other_contacts": [p for j, p in enumerate(l.get("other_contacts") or []) if j != i] or None})
    return _log(store, lead_id, [{"field": "person", "label": "Person removed", "old": gone.get("contact_name") or "", "new": "",
                                  "company": lead.get("company") or ""}], actor, f"removed {gone.get('contact_name') or 'a person'} from this company.")
