"""Restore landlines: put back office numbers that older code dropped from the lead phone.

Owner rule (app/contact_rules.py): a landline with its STD code is kept as the lead phone with phone_type "landline"
(callable, never WhatsApp/SMS). Campaign, Places/OSM and prospect leads used to drop it while keeping the number as
typed in `company_phone`, a `phones` list, a custom phone field or a value-conflict note. This finds leads whose phone
is empty but whose own saved data holds a usable landline, and fills ONLY the empty phone.

  plan(store)                        dry run: what would change (leads in the request's workspace scope)
  apply(store, by)                   fills the phones, saves an undo record, returns the run
  undo(store, run_id, by)            puts the previous (empty) values back where the phone was not changed since
Leads on the do-not-contact list, unsubscribed leads and numbers of erased people are skipped.
"""
import uuid

from . import workspaces
from .lead_model import event, now_iso, source_phone
from .privacy import hashes_for

COLLECTION = "landline_restores"
RUNS_KEPT = 50
SAMPLE = 30
PHONE_WORDS = ("phone", "landline", "tel", "mobile", "contact_no", "contact_number")


def _collection(store) -> list:
    try:
        return store.read(COLLECTION)
    except KeyError:
        store.write(COLLECTION, [])
        return []


def _phone_like_custom(key: str, types: dict) -> bool:
    return types.get(key) == "phone" or any(w in key.lower() for w in PHONE_WORDS)


def candidates(lead: dict, custom_types: dict = None) -> list:
    """[(e164, where, raw)] landlines found in the lead's own saved data, best first."""
    types = custom_types or {}
    raw = [("company_phone", lead.get("company_phone"))]
    raw += [("phones", p) for p in lead.get("phones") or []]
    raw += [(f"custom:{k}", v) for k, v in (lead.get("custom") or {}).items()
            if isinstance(v, (str, int)) and _phone_like_custom(str(k), types)]
    raw += [("value to check", c.get(side)) for c in lead.get("merge_conflicts") or [] if isinstance(c, dict)
            and c.get("field") == "phone" for side in ("incoming", "kept")]
    out, seen = [], set()
    for where, value in raw:
        if value in (None, ""):
            continue
        number, kind = source_phone(str(value))
        if kind == "landline" and number not in seen:
            seen.add(number)
            out.append((number, where, str(value)[:80]))
    return out


def skip_reason(lead: dict, number: str, erased: set):
    if lead.get("do_not_contact"):
        return "do-not-contact"
    if lead.get("stage") == "unsubscribed":
        return "unsubscribed"
    if erased and hashes_for(None, [number]) & erased:
        return "erased"
    return None


def _custom_types(store) -> dict:
    try:
        from .lead_fields import list_fields
        return {f.get("key"): f.get("type") for f in list_fields(store)}
    except Exception:                       # noqa: BLE001 - custom fields are optional for this check
        return {}


def find(leads: list, erased: set, custom_types: dict = None) -> dict:
    """{"changes": [...], "skipped": {reason: n}} for leads in scope with an empty phone and a usable landline."""
    changes, skipped = [], {}
    for lead in leads:
        if lead.get("phone") or not workspaces.visible(lead):
            continue
        found = candidates(lead, custom_types)
        if not found:
            continue
        number, where, raw = found[0]
        why = skip_reason(lead, number, erased)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        changes.append({"lead_id": lead["id"], "company": lead.get("company") or lead.get("domain") or "",
                        "contact_name": lead.get("contact_name") or "", "phone": number, "from": where, "raw": raw,
                        "previous": {k: lead.get(k) for k in ("phone", "phone_type", "phone_source")}})
    return {"changes": changes, "skipped": skipped}


def _summary(found: dict) -> dict:
    return {"would_change": len(found["changes"]), "skipped": found["skipped"], "sample": found["changes"][:SAMPLE]}


def plan(store) -> dict:
    found = find(store.read("leads"), set(store.read("suppressed_hashes")), _custom_types(store))
    return {"dry_run": True, **_summary(found)}


def apply(store, by: str) -> dict:
    """Fill the empty phones (re-checked inside the save, so a phone set meanwhile is never overwritten)."""
    erased, types, done = set(store.read("suppressed_hashes")), _custom_types(store), {}
    run_id, stamp = uuid.uuid4().hex[:10], now_iso()

    def change(leads: list) -> list:
        found = find(leads, erased, types)
        by_id = {c["lead_id"]: c for c in found["changes"]}
        done.update(found=found)
        out = []
        for lead in leads:
            c = by_id.get(lead["id"])
            if c:
                text = f"Landline {c['phone']} restored as the phone from {c['from']} (calls only, no WhatsApp/SMS; by {by})"
                lead = {**lead, "phone": c["phone"], "phone_type": "landline", "phone_source": f"restored:{c['from']}",
                        "timeline": [*(lead.get("timeline") or []), {**event("landline_restored", text), "by": by}]}
            out.append(lead)
        return out
    store.update("leads", change)
    found = done["found"]
    run = {"id": run_id, "at": stamp, "by": by, "changed": [{k: c[k] for k in ("lead_id", "phone", "from", "previous")}
                                                             for c in found["changes"]], "undone_at": None}
    _collection(store)
    store.update(COLLECTION, lambda rows: [*rows, run][-RUNS_KEPT:])
    return {"dry_run": False, "run_id": run_id, "changed": len(found["changes"]), "skipped": found["skipped"],
            "sample": found["changes"][:SAMPLE]}


def last_run(store):
    """The newest run that can still be undone, or None."""
    runs = [r for r in _collection(store) if not r.get("undone_at") and r.get("changed")]
    return {"id": runs[-1]["id"], "at": runs[-1]["at"], "by": runs[-1]["by"], "changed": len(runs[-1]["changed"])} if runs else None


def undo(store, run_id: str, by: str) -> dict:
    """Put back the previous values on leads whose phone is still the restored landline. Raises LookupError / ValueError."""
    run = next((r for r in _collection(store) if r.get("id") == run_id), None)
    if run is None:
        raise LookupError("That restore was not found.")
    if run.get("undone_at"):
        raise ValueError("That restore was already undone.")
    wanted = {c["lead_id"]: c for c in run["changed"]}
    counts = {"reverted": 0, "kept": 0}

    def change(leads: list) -> list:
        out = []
        for lead in leads:
            c = wanted.get(lead["id"])
            if c and lead.get("phone") == c["phone"] and lead.get("phone_type") == "landline":
                prev = c.get("previous") or {}
                lead = {**lead, **{k: prev.get(k) for k in ("phone", "phone_type", "phone_source")},
                        "timeline": [*(lead.get("timeline") or []),
                                     {**event("landline_restore_undone", f"Restored landline {c['phone']} removed again (undo by {by})"), "by": by}]}
                lead = {k: v for k, v in lead.items() if v is not None or k not in ("phone_type", "phone_source")}
                counts["reverted"] += 1
            elif c:
                counts["kept"] += 1                 # changed since (edited, merged): left as it is
            out.append(lead)
        return out
    store.update("leads", change)
    store.update(COLLECTION, lambda rows: [{**r, "undone_at": now_iso(), "undone_by": by, "undo": counts} if r.get("id") == run_id else r
                                           for r in rows])
    return {"run_id": run_id, **counts, "missing": len(wanted) - counts["reverted"] - counts["kept"]}
