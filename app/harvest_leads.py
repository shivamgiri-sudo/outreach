"""Lead harvest: turn one page of listings into leads, de-duplicated across jobs, with do-not-call numbers flagged.

For each listing, in one atomic update of the leads:
  1. Phones are read with app/phones.py (E.164 + line type); invalid or incomplete numbers are dropped. The best number
     becomes the lead phone, the rest go into `phones`.
  2. A lead already in the job's workspace with the same phone (any of its numbers), the same website domain, or the same
     company name in the same city is the match. The job is linked to it and only its EMPTY fields are filled; nothing
     is overwritten. Counted as a duplicate.
  3. Otherwise a new lead is made in the job's workspace. A lead whose identity key is already held in another workspace
     of the same account is not added (counted as a duplicate, as the importer does).
  4. Numbers on the do-not-contact list ("suppressed": E.164 numbers uploaded as DND, next to unsubscribed emails) mark the
     lead `dnc` and are counted; the lead is kept, never dropped silently. An erased person is never re-created (the
     store's guard drops it); that listing is counted as skipped.
"""
from . import accounts, phones
from .lead_merge import lead_domain
from .lead_model import build_lead, event, now_iso
from .places import city_key
from .standardise import company_key

SOURCE_LABEL = {"google_places": "Google Places", "osm": "OpenStreetMap"}
LEAD_FIELDS = ("website", "domain", "address", "city", "state", "pin", "category", "sector", "rating", "reviews_count", "maps_url",
               "plus_code", "business_status", "google_place_id", "osm_id", "email")
DND_REASON = "On the do-not-call list (uploaded DND numbers)"
MAX_PHONES = 5


def read_phones(row: dict, country: str = "IN") -> list:
    raw = row.get("phone_raw")
    return phones.parse_many([p for p in (raw if isinstance(raw, list) else [raw]) if p], country)[:MAX_PHONES]


def _lead_numbers(lead: dict) -> set:
    return {phones.e164(p) or str(p) for p in [lead.get("phone"), *(lead.get("phones") or [])] if p}


def _name_key(company, city) -> tuple:
    return (company_key(company), city_key(city)) if company_key(company) and city_key(city) else ()


class Index:
    """The workspace's leads by phone, domain and company name + city, kept current as the page adds leads."""

    def __init__(self, leads: list):
        self.phone, self.domain, self.name = {}, {}, {}
        for i, lead in enumerate(leads):
            self.add(i, lead)

    def add(self, i: int, lead: dict) -> None:
        for n in _lead_numbers(lead):
            self.phone.setdefault(n, i)
        d = lead_domain(lead)
        if d:
            self.domain.setdefault(d, i)
        k = _name_key(lead.get("company"), lead.get("city"))
        if k:
            self.name.setdefault(k, i)

    def match(self, numbers: list, domain: str, name: tuple):
        for n in numbers:
            if n in self.phone:
                return self.phone[n], "phone"
        if domain and domain in self.domain:
            return self.domain[domain], "domain"
        if name and name in self.name:
            return self.name[name], "name_city"
        return None, ""


def _fields(row: dict, found: list) -> dict:
    primary = phones.best(found)
    out = {k: row.get(k) for k in LEAD_FIELDS if row.get(k) not in (None, "")}
    if row.get("category"):
        out["sector"] = row["category"][:120]
    if primary:
        out.update(phone=primary["e164"], phone_type=phones.compat_type(primary), phone_line_type=primary["type"])
        rest = [n["e164"] for n in found if n["e164"] != primary["e164"]]
        if rest:
            out["phones"] = rest
    return out


def _fill_empty(lead: dict, fields: dict, job_id: str, dnd: bool, now: str) -> dict:
    """Only empty fields are filled; extra numbers are appended to `phones`; nothing is overwritten."""
    new = dict(lead)
    filled = []
    for k, v in fields.items():
        if k in ("phones", "phone_type", "phone_line_type"):
            continue
        elif new.get(k) in (None, "", 0) and v not in (None, ""):
            new[k] = v
            filled.append(k)
            if k == "phone":
                new["phone_type"], new["phone_line_type"] = fields.get("phone_type", ""), fields.get("phone_line_type", "")
    have = _lead_numbers(new)
    extra = [p for p in [fields.get("phone"), *(fields.get("phones") or [])] if p and p not in have]
    if extra:                                                    # other numbers of the business: added, never replacing
        new["phones"] = [*(new.get("phones") or []), *extra][:10]
        filled.append("phones")
    new["harvest_jobs"] = list(dict.fromkeys([*(new.get("harvest_jobs") or []), job_id]))[-20:]
    if dnd and not new.get("dnc"):
        new.update(dnc=True, dnc_reason=DND_REASON, dnc_at=now)
        filled.append("dnc")
    if filled:
        new["timeline"] = [*(new.get("timeline") or []), {"at": now, "kind": "harvest",
                                                          "text": f"Lead harvest filled empty fields: {', '.join(filled)}"}]
    return new


def _new_lead(row: dict, fields: dict, job: dict, pair: dict, dnd: bool, now: str):
    label = SOURCE_LABEL.get(row["source"], row["source"])
    fallback = f"place:{row['google_place_id']}" if row.get("google_place_id") else f"osm:{row.get('osm_id')}" if row.get("osm_id") else ""
    lead = build_lead({"company": row["company"], "website": row.get("website", ""), "email": row.get("email", ""),
                       "phone": fields.get("phone", ""), "country": row.get("country", "IN"), "city": row.get("city", ""),
                       "state": row.get("state", ""), "sector": fields.get("sector", "")}, row["source"], allow_company_key=True,
                      fallback_key=fallback)
    if lead is None:
        return None
    lead.update({k: v for k, v in fields.items() if v not in (None, "")})
    if not fields.get("phone"):
        lead.update(phone=None)
        lead.pop("phone_type", None)
    lead.update(source_url=row.get("source_url", ""), harvest_jobs=[job["id"]],
                tags=["harvest", *(["to-enrich"] if job["options"].get("deep_crawl") else [])],
                timeline=[event("created", f"Found by lead harvest: {pair['keyword']} in {pair['location']['text']} ({label})")])
    if dnd:
        lead.update(dnc=True, dnc_reason=DND_REASON, dnc_at=now)
    if job["ws"] and job["ws"] != "main":
        lead["workspace"] = job["ws"]
    else:
        lead.pop("workspace", None)
    return lead


def save_rows(store, job: dict, pair_index: int, rows: list) -> dict:
    """Save one page of listings. Returns {counts, results} (results: one row per listing for the job's result table)."""
    pair = job["pairs"][pair_index]
    suppressed = set(store.read("suppressed"))
    amap = accounts.ws_account_map(store)
    account = amap.get(job["ws"], accounts.MCN)
    now = now_iso()
    counts = {"found": 0, "new": 0, "duplicate": 0, "dnd": 0, "skipped": 0, "closed": 0}
    plan, created = [], set()

    def apply(leads: list) -> list:
        mine = [i for i, l in enumerate(leads) if (l.get("workspace") or "main") == job["ws"]]
        index = Index([leads[i] for i in mine])
        taken = {l.get("dedupe_key") for l in leads if accounts.account_of_lead(amap, l) == account and l.get("dedupe_key")}
        for row in rows:
            if not row.get("company"):
                plan.append((row, "skipped", None, False))
                continue
            if row.get("business_status") == "CLOSED_PERMANENTLY":
                plan.append((row, "closed", None, False))
                continue
            found = read_phones(row)
            numbers = [n["e164"] for n in found]
            dnd = any(n in suppressed for n in numbers)
            fields = _fields(row, found)
            at, why = index.match(numbers, lead_domain(row), _name_key(row["company"], row.get("city")))
            if at is not None:
                pos = mine[at]
                leads[pos] = _fill_empty(leads[pos], fields, job["id"], dnd, now)
                plan.append((row, "duplicate", leads[pos]["id"], dnd, why))
                continue
            lead = _new_lead(row, fields, job, pair, dnd, now)
            if lead is None or lead.get("dedupe_key") in taken:
                plan.append((row, "duplicate" if lead else "skipped", None, dnd, "same identity key" if lead else ""))
                continue
            taken.add(lead["dedupe_key"])
            leads.append(lead)
            mine.append(len(leads) - 1)
            index.add(len(mine) - 1, lead)
            created.add(lead["id"])
            plan.append((row, "new", lead["id"], dnd, ""))
        return leads

    saved = {l["id"] for l in store.update("leads", apply) if l.get("id") in created}
    results = []
    for entry in plan:
        row, outcome, lead_id, dnd = entry[:4]
        if outcome == "new" and lead_id not in saved:
            outcome, lead_id = "skipped", None                       # an erased person: the store's guard kept them out
        counts["found"] += 1
        counts[outcome] += 1
        counts["dnd"] += 1 if dnd else 0
        results.append({"job": job["id"], "ws": job["ws"], "pair": pair_index, "lead_id": lead_id, "outcome": outcome, "dnd": bool(dnd),
                        "matched_by": entry[4] if len(entry) > 4 else "", "source": row.get("source", ""), "at": now,
                        **{k: row.get(k) for k in ("company", "category", "website", "city", "pin", "rating", "reviews_count", "maps_url",
                                                   "source_url") if row.get(k) not in (None, "")},
                        **({"phone": p["e164"], "phone_line_type": p["type"]} if (p := phones.best(read_phones(row))) else {})})
    return {"counts": counts, "results": results}
