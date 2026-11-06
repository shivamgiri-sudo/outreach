"""Smart lead import: read any export, map its columns, standardise every row, then dedupe and merge.

`run_import` works on a list of leads it is given. The preview passes a copy and throws it away (a dry run); the
commit passes the stored list inside `store.update`, so the whole import is one atomic change.
"""
import csv
import hashlib
import io
import json
import uuid
from collections import Counter
from datetime import date
from typing import Optional

from .header_map import detect_format, loose_header, map_columns
from .hygiene import dedupe_key as basic_dedupe_key, is_role_address, normalise_phone
from .lead_merge import CLOSED_STAGES, LeadIndex, merge_record
from .lead_model import event, now_iso, to_int
from .source_names import canonical
from .places import (city_key, clean_city, clean_country, clean_state, is_indian_place, job_age_days, parse_address,
                     parse_company_size, parse_count, parse_location, state_for_city)
from .sheet_import import STATUS_STAGE
from .standardise import (best_phone, clean_company, clean_email, clean_text, clean_title, combine_name, company_key,
                          department_of, email_alias_key, email_domain, is_free_mail, is_role_email, linkedin_url,
                          parse_phones, registrable_domain, seniority_of, smart_title, split_emails, website_domain)
from .hygiene import safe_http_url

MAX_ROWS = 20000
SAMPLE_ROWS = 10
LIST_CAP = 500
PROBLEM_TEXT = {
    "invalid_email": "Email '{v}' is not a valid address and was left out.",
    "typo_fixed": "Fixed a typo in the email domain: {v}.",
    "invalid_phone": "Phone '{v}' could not be read and was left out.",
    "junk_phone": "Phone '{v}' looks like a placeholder and was left out.",
    "landline_without_std": "Phone '{v}' has no STD (area) code, so it was left out.",
    "invalid_website": "Website '{v}' is not a valid web address and was left out.",
    "free_mail_domain": "'{v}' is a personal email provider, not a company website, so it was left out.",
    "not_company_site": "'{v}' is a social or listing page, not the company's own website, so it was left out.",
    "role_address": "{v} is a shared inbox (role address), not a person.",
    "role_address_skipped": "{v} is a shared inbox and was left out, as you chose.",
    "free_mail": "{v} is a personal (free-mail) address.",
    "free_mail_skipped": "{v} is a personal address and was left out, as you chose.",
    "foreign_phone": "{v} is not an Indian number; it is kept as an international number.",
    "suppressed": "{v} is on the do-not-contact list, so this row is skipped.",
    "suppressed_extra": "{v} is on the do-not-contact list and was left out.",
    "closed_lead": "Matches a lead marked {v}; new details are added but it stays {v}.",
    "source_opt_out": "The source file says this person opted out of email ({v}), so this row is skipped.",
    "do_not_call": "The source file says do not call ({v}), so the phone numbers were left out.",
    "email_flagged": "The source marked the email {v}; check it before emailing.",
    "custom_unreadable": "{v}: the value could not be read for this custom field and was left out.",
}
# CRM / vendor columns that carry a person's "no" or a vendor's verdict on the email. A "no" is never a "yes".
_OPT_OUT_COLUMNS = {loose_header(h) for h in ("Email Opt Out", "Unsubscribed from all email", "Do Not Email",
                                              "Has Opted Out Of Email", "Opted Out Of Email", "Email Opt-Out")}
_NO_CALL_COLUMNS = {loose_header(h) for h in ("Do Not Call", "DoNotCall", "DNC")}
_EMAIL_STATUS_COLUMNS = {loose_header(h) for h in ("Email Status", "Verification status", "Email Verification Status",
                                                   "Email 1 Validation", "Email Validation", "Email Grade")}
_YES = {"true", "yes", "y", "1", "x", "checked", "opted out", "unsubscribed"}
_BAD_STATUS = ("invalid", "undeliverable", "bounce", "not valid", "risky", "unverifiable", "do not mail", "spamtrap",
               "disposable")


def _source_flags(row: dict) -> tuple:
    """(email opt-out cell, do-not-call cell, bad email-status cell) read straight from the row, '' when absent."""
    found = {"opt": "", "call": "", "status": ""}
    for header, value in row.items():
        loose, text = loose_header(header), clean_text(value)
        low = text.lower()
        if loose in _OPT_OUT_COLUMNS and low in _YES:
            found["opt"] = found["opt"] or f"{header}: {text}"
        elif loose in _NO_CALL_COLUMNS and low in _YES:
            found["call"] = found["call"] or f"{header}: {text}"
        elif loose in _EMAIL_STATUS_COLUMNS and (low in ("d", "f") or any(w in low for w in _BAD_STATUS)):
            found["status"] = found["status"] or f"{header}: {text}"
    return found["opt"], found["call"], found["status"]


def read_table(csv_text: Optional[str] = None, rows: Optional[list] = None) -> tuple:
    """(headers, [(spreadsheet row number, {header: text})]). Comma, semicolon or tab separated; blank rows skipped."""
    if rows is not None:
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError("rows must be a list of objects (column name -> value).")
        headers = list(dict.fromkeys(str(k) for r in rows for k in r if str(k).strip()))
        table = [(n, {h: clean_text(r.get(h)) for h in headers}) for n, r in enumerate(rows, start=2)]
    else:
        text = (csv_text or "").lstrip("﻿")
        if "\x00" in text[:4000]:
            raise ValueError("This file looks like UTF-16 (Excel 'Unicode Text'). Save it as 'CSV UTF-8' and try again.")
        first = text.split("\n", 1)[0]
        delimiter = max((",", ";", "\t"), key=lambda d: (first.count(d), d == ","))
        try:
            reader = csv.reader(io.StringIO(text), delimiter=delimiter)
            header, body = next(reader, []), list(reader)
            blank = [i for i, h in enumerate(header) if not h.strip()]
            start = 2
            if body and len(blank) * 2 >= len(header) and _label_row(body[0], blank):
                header, body, start = body[0], body[1:], 3     # merged group titles above the real column names
            seen, headers = {}, []
            for i, name in enumerate(h.strip().lstrip("﻿") for h in header):
                name = name or f"Column {i + 1}"
                seen[name] = seen.get(name, 0) + 1
                headers.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
            table = [(n, {h: (cells[i] if i < len(cells) else "") for i, h in enumerate(headers)})
                     for n, cells in enumerate(body, start=start)]
        except csv.Error as exc:
            raise ValueError(f"Could not read the CSV ({exc}).") from exc
    table = [(n, r) for n, r in table if any(str(v or "").strip() for v in r.values())]
    if not headers:
        raise ValueError("The file is empty. The first row should hold the column names.")
    if len(table) > MAX_ROWS:
        raise ValueError(f"That file has {len(table)} rows; import at most {MAX_ROWS} at a time.")
    return headers, table


def _label_row(cells: list, blank: list) -> bool:
    """A row of column names: it fills every gap of the row above, with no emails, links or numbers."""
    return all(i < len(cells) and cells[i].strip() for i in blank) and all(
        "@" not in c and "//" not in c and sum(ch.isdigit() for ch in c) <= 2 for c in cells)


def _values(row: dict, columns: list, field: str) -> list:
    picked = [(-c["confidence"], i, clean_text(row.get(c["column"]))) for i, c in enumerate(columns) if c["field"] == field]
    return [v for _, _, v in sorted(picked) if v]


def _first(row: dict, columns: list, field: str) -> str:
    found = _values(row, columns, field)
    return found[0] if found else ""


def _linkedin(row: dict, columns: list, field: str, kind: str) -> str:
    """First column value that is a real LinkedIn link (a Sales Navigator link is skipped), preferring a public
    /in/name slug over an internal ACw… member id so the same person matches across tools."""
    found = [u for u in (linkedin_url(v, kind) for v in _values(row, columns, field)) if u]
    return sorted(found, key=lambda u: u.rsplit("/", 1)[-1].startswith("ACw"))[0] if found else ""


def custom_values(row: dict, columns: list, custom_fields: Optional[list]) -> tuple:
    """({key: stored value}, [(problem, text)]) for columns mapped to the owner's custom lead fields."""
    from .lead_fields import coerce_soft
    by_key = {f["key"]: f for f in custom_fields or []}
    values, problems = {}, []
    for c in columns:
        field = str(c.get("field") or "")
        if not field.startswith("custom:") or field[7:] not in by_key:
            continue
        raw = clean_text(row.get(c["column"]))
        if not raw or field[7:] in values:
            continue
        value = coerce_soft(by_key[field[7:]], raw)
        if value is None:
            problems.append(("custom_unreadable", f"{by_key[field[7:]]['label']} '{raw}'"))
        elif value != "":
            values[field[7:]] = value
    return values, problems


def extract_record(row: dict, columns: list, today: date, custom_fields: Optional[list] = None) -> tuple:
    """One row -> (standardised record, [(problem kind, value)])."""
    get = lambda f: _first(row, columns, f)
    problems = []
    company, legal = clean_company(get("company"))
    name = combine_name(get("first_name"), get("last_name"), get("contact_name"))
    title = clean_title(get("title"))
    emails = []
    for raw in (part for value in _values(row, columns, "email") for part in split_emails(value)):
        email, problem = clean_email(raw)
        if problem:
            problems.append((problem, f"{raw} -> {email}" if problem == "typo_fixed" else raw))
        if email and email not in emails:
            emails.append(email)
    numbers = []
    for value in _values(row, columns, "phone"):
        parsed = parse_phones(value)
        numbers += [n for n in parsed["numbers"] if n not in numbers]
        problems += parsed["problems"]
    phone, phone_kind = best_phone(numbers)
    raw_site = get("website")
    domain, website, site_problem = website_domain(raw_site)
    company_li = _linkedin(row, columns, "company_linkedin_url", "company") or linkedin_url(raw_site, "company")
    if site_problem and not (site_problem == "not_company_site" and company_li):
        problems.append((site_problem, raw_site))
    guessed = bool(not domain and emails and not is_free_mail(emails[0]))
    if guessed:
        domain = registrable_domain(email_domain(emails[0]))
        website = f"https://{domain}" if domain else ""
    person_li = _linkedin(row, columns, "linkedin_url", "in")
    locs = [parse_location(v) for v in _values(row, columns, "location")]  # person "India", then company "Pune, …"
    loc = dict(next((p for p in locs if p["city"]), locs[0] if locs else parse_location("")))
    for other in (p for p in locs if loc["city"] and city_key(p["city"]) == city_key(loc["city"])):
        loc = {k: loc[k] or other[k] for k in loc}  # "Kota" + "Kota, Rajasthan, India"
    addr = parse_address(get("address"))
    city = clean_city(get("city")) or loc["city"] or addr["city"]
    country = clean_country(get("country")) or loc["country"] or addr["country"]
    state = clean_state(get("state"), country) or loc["state"] or addr["state"] or state_for_city(city)
    if not country and (is_indian_place(city, state) or (numbers and all(n[0].startswith("+91") for n in numbers))):
        country = "IN"
    size, bucket = parse_company_size(get("company_size"))
    notes = [f"{c['column']}: {clean_text(row.get(c['column']))}" for c in columns
             if c["field"] == "notes" and clean_text(row.get(c["column"]))]
    rec = {
        "company": company, "company_legal": legal, "contact_name": name, "title": title,
        "seniority": seniority_of(title), "department": department_of(title),
        "email": emails[0] if emails else None, "emails": emails, "phone": phone, "phone_type": phone_kind or "",
        "phones": [n[0] for n in numbers], "website": website, "domain": domain, "website_guess": guessed,
        "linkedin_url": person_li, "company_linkedin_url": company_li, "city": city, "state": state, "country": country,
        "pincode": addr["pincode"], "address": get("address"), "sector": smart_title(get("sector"), keep_small_words=True),
        "company_size": size, "company_size_bucket": bucket, "open_roles": parse_count(get("open_roles")),
        "role": clean_text(get("role")),
        "max_job_age_days": parse_count(get("job_age_days")) or job_age_days(get("posted_on"), today),
        "job_links": [u for u in (safe_http_url(v) for v in _values(row, columns, "job_link")) if u],
        "source_detail": get("source_detail"), "lead_status": get("lead_status"), "notes": notes,
    }
    rec["custom"], custom_problems = custom_values(row, columns, custom_fields)
    return rec, problems + custom_problems


def _set_emails(rec: dict, emails: list) -> None:
    """Emails left after suppression and your choices. A domain read from a dropped address is still the company's."""
    rec["emails"], rec["email"] = emails, (emails[0] if emails else None)


def rejection_reason(rec: dict, problems: list, dropped_by_choice: bool) -> Optional[str]:
    if rec["company"] or rec["domain"] or rec["emails"] or rec["phones"] or rec["linkedin_url"]:
        return None
    bad = [v for kind, v in problems if kind == "invalid_email"]
    if bad:
        return f"Email '{bad[0]}' is not a valid address, and the row has no company or other contact detail."
    if dropped_by_choice:
        return "Its only contact was an email address you chose to leave out."
    if rec["contact_name"]:
        return f"Only a name ({rec['contact_name']}): no company, email, phone or LinkedIn to find them by."
    return "No company name and no contact details."


OPTIONAL_KEYS = ("company_legal", "seniority", "department", "emails", "phone_type", "phones", "website_guess",
                 "linkedin_url", "company_linkedin_url", "city", "state", "pincode", "address", "sector",
                 "company_size_bucket", "role", "job_links", "source_detail")
SOURCED_FIELDS = ("company", "contact_name", "title", "email", "phone", "website", "domain", "linkedin_url",
                  "company_linkedin_url", "city", "state", "country", "sector", "company_size", "open_roles", "role")


def lead_from_record(rec: dict, source: str, import_id: str, at: str, taken: set) -> dict:
    lead_id = uuid.uuid4().hex[:10]
    detail = f" ({rec['source_detail']})" if rec.get("source_detail") else ""
    lead = {
        "id": lead_id, "company": rec["company"], "contact_name": rec["contact_name"], "title": rec["title"],
        "email": rec["email"], "email_is_role": bool(rec["email"] and is_role_address(rec["email"])), "phone": rec["phone"],
        "website": rec["website"], "domain": rec["domain"] or None, "country": rec["country"], "source": source,
        "source_id": canonical(source)[0],
        "stage": STATUS_STAGE.get(rec["lead_status"].lower(), (0, "new"))[1], "consent": None,
        "engagement": {"opens": 0, "clicks": 0, "replies": 0},
        "timeline": [event("created", f"Imported from {source}{detail}"), *[event("note", n) for n in rec["notes"]],
                     *([event("note", f"Lead status in {source}: {rec['lead_status']}")] if rec["lead_status"] else [])],
        "created_at": at, "open_roles": rec["open_roles"], "company_size": rec["company_size"],
        "max_job_age_days": rec["max_job_age_days"], "repost_count": 0,
        **{k: rec[k] for k in OPTIONAL_KEYS if rec.get(k)},
        "field_sources": {f: source for f in SOURCED_FIELDS if rec.get(f)},
        **({"custom": dict(rec["custom"])} if rec.get("custom") else {}),
    }
    if import_id:
        lead["import_id"] = import_id
    key = basic_dedupe_key(lead) or (f"linkedin:{rec['linkedin_url']}" if rec["linkedin_url"] else
                                     f"company:{company_key(rec['company'])}|{city_key(rec['city'])}")
    lead["dedupe_key"] = key if key not in taken else f"{key}#{lead_id}"
    taken.add(lead["dedupe_key"])
    return lead


SAMPLE_FIELDS = ("company", "contact_name", "title", "seniority", "department", "email", "phone", "phone_type", "domain",
                 "city", "state", "country", "sector", "company_size_bucket", "role", "open_roles")


def run_import(leads: list, table: list, columns: list, source: str, suppressed: set, options: dict,
               import_id: str = "", today: Optional[date] = None, at: Optional[str] = None, custom_fields: Optional[list] = None) -> dict:
    """Dedupe and merge `table` rows into `leads` (changed in place). Returns what happened to every row."""
    today, at = today or date.today(), at or now_iso()
    index = LeadIndex(leads)
    created, touched = {}, {}
    blocked_aliases = {email_alias_key(e) for e in suppressed}
    out = {k: [] for k in ("created_ids", "merged_ids", "merges", "duplicates", "unchanged", "rejected", "skipped",
                           "warnings", "sample", "source_opt_outs")}
    counts = Counter()

    for n, row in table:
        rec, problems = extract_record(row, columns, today, custom_fields)
        warn = lambda kind, value, n=n: out["warnings"].append(
            {"row": n, "kind": kind, "text": PROBLEM_TEXT[kind].format(v=value)})
        for kind, value in problems:
            warn(kind, value)
        action = None
        opted_out, no_call, flagged = _source_flags(row)
        if no_call and rec["phones"]:
            warn("do_not_call", no_call)
            rec.update(phone=None, phone_type="", phones=[])
        if flagged and rec["emails"]:
            warn("email_flagged", f"{rec['emails'][0]} ({flagged})")
            rec["notes"].append(f"{rec['emails'][0]} flagged by source - {flagged}")
        blocked = [e for e in rec["emails"] if e in suppressed or email_alias_key(e) in blocked_aliases]
        if opted_out:
            warn("source_opt_out", opted_out)
            out["source_opt_outs"] += [e for e in rec["emails"] if e not in out["source_opt_outs"]]
            out["skipped"].append({"row": n, "reason": PROBLEM_TEXT["source_opt_out"].format(v=opted_out), "values": row})
            action = "skipped"
        elif blocked and blocked[0] == rec["emails"][0]:
            warn("suppressed", blocked[0])
            out["skipped"].append({"row": n, "reason": PROBLEM_TEXT["suppressed"].format(v=blocked[0]), "values": row})
            action = "skipped"
        else:
            for email in blocked:
                warn("suppressed_extra", email)
            kept, dropped = [], False
            for email in (e for e in rec["emails"] if e not in blocked):
                kind = "role_address" if is_role_email(email) else "free_mail" if is_free_mail(email) else None
                if kind and options.get(f"skip_{'role_addresses' if kind == 'role_address' else 'free_mail'}"):
                    warn(f"{kind}_skipped", email)
                    dropped = True
                    continue
                if kind:
                    warn(kind, email)
                kept.append(email)
            _set_emails(rec, kept)
            for phone in rec["phones"]:
                if not phone.startswith("+91"):
                    warn("foreign_phone", phone)
            reason = rejection_reason(rec, problems, dropped)
            if reason:
                out["rejected"].append({"row": n, "reason": reason, "values": row})
                action = "rejected"
        if action is None:
            action = _place(leads, index, rec, n, source, import_id, at, created, touched, out, warn)
        counts[action] += 1
        if len(out["sample"]) < SAMPLE_ROWS:
            out["sample"].append({"row": n, "action": action, **{f: rec.get(f) for f in SAMPLE_FIELDS}})
    out["summary"] = {
        "rows": len(table), "create": counts["create"], "merge": counts["merge"], "unchanged": counts["unchanged"],
        "duplicates_in_file": counts["duplicate"], "rejected": counts["rejected"], "skipped": counts["skipped"],
        "conflicts": sum(len(m["conflicts"]) for m in out["merges"] + out["duplicates"]),
        "contacts_added": sum(1 for m in out["merges"] + out["duplicates"] if m.get("added_contact")),
        "fields_filled": sum(len(m["filled"]) for m in out["merges"]),
        "warnings": dict(Counter(w["kind"] for w in out["warnings"])),
    }
    return out


def _place(leads, index, rec, n, source, import_id, at, created, touched, out, warn) -> str:
    hit = index.match(rec)
    if hit is None:
        leads.append(lead_from_record(rec, source, import_id, at, index.keys))
        i = len(leads) - 1
        index.add(i)
        created[i] = n
        out["created_ids"].append(leads[i]["id"])
        return "create"
    i, who, reason = hit
    before = leads[i]
    merged, info = merge_record(before, rec, who, source, import_id, at, index.keys)
    extra = {k: v for k, v in (rec.get("custom") or {}).items() if (merged.get("custom") or {}).get(k) in (None, "")}
    if extra:                                  # custom values fill gaps only; a value already on the lead is kept
        merged = {**merged, "custom": {**(merged.get("custom") or {}), **extra}}
        info = {**info, "filled": [*info["filled"], *(f"custom.{k}" for k in extra)], "changed": True}
    status = [f"Lead status in {source}: {rec['lead_status']}"] if rec.get("lead_status") else []   # stage is never changed
    notes = [t for t in [*rec["notes"], *status] if t not in {e.get("text") for e in merged["timeline"]}]
    if notes:
        merged = {**merged, "timeline": [*merged["timeline"], *[event("note", t) for t in notes]]}
        info["changed"] = True
    first_row = created.get(i) or touched.get(i)
    entry = {"row": n, "lead_id": before["id"], "company": before.get("company") or merged.get("company"),
             "matched_by": reason, "filled": info["filled"], "conflicts": info["conflicts"],
             "added_contact": info["added_contact"]}
    if before.get("stage") in CLOSED_STAGES:
        warn("closed_lead", before["stage"])
    if info["changed"] or info.get("touched"):
        leads[i] = merged
        index.add(i)
    if first_row:
        out["duplicates"].append({**entry, "same_as_row": first_row})
        return "duplicate"
    if not info["changed"]:
        out["unchanged"].append(entry)
        return "unchanged"
    touched[i] = n
    out["merges"].append(entry)
    out["merged_ids"].append(before["id"])
    return "merge"


def fingerprint(lead: dict) -> str:
    return hashlib.sha1(json.dumps(lead, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def trim_lists(result: dict) -> dict:
    """Keep responses small: long lists are cut (rejected and skipped rows are kept for the download)."""
    return {k: (v[:LIST_CAP] if isinstance(v, list) and k in ("merges", "duplicates", "unchanged", "warnings") else v)
            for k, v in result.items()}


# ---------------------------------------------------------------- quick paste (POST /api/leads/import)
def quick_rows(csv_text: str, custom_fields: Optional[list] = None) -> list:
    """Rows for the simple importer: the original columns, plus known fields found under other column names. A column
    whose header matches one of the owner's custom lead fields (label or key) is kept as "custom:<key>"."""
    from .lead_fields import coerce_soft, match_header
    headers, table = read_table(csv_text)
    custom_cols = [(h, f) for h in headers for f in [match_header(custom_fields or [], h)] if f]
    columns = map_columns(headers, [r for _, r in table[:25]], detect_format(headers))
    rows = []
    for _, row in table:
        base = {k.strip().lower(): str(v or "").strip() for k, v in row.items() if k}
        get = lambda f: _first(row, columns, f)
        emails = [e for e in (clean_email(p)[0] for v in _values(row, columns, "email") for p in split_emails(v)) if e]
        numbers = [n for v in _values(row, columns, "phone") for n in parse_phones(v)["numbers"]]
        site = get("website")
        fill = {
            "company": get("company"), "contact_name": combine_name(get("first_name"), get("last_name"), get("contact_name")),
            "title": get("title"), "email": emails[0] if emails else "", "phone": best_phone(numbers)[0] or "",
            "website": site if "//" in site or not site else f"https://{site}", "country": clean_country(get("country")),
            "open_roles": parse_count(get("open_roles")) or "", "company_size": parse_company_size(get("company_size"))[0] or "",
            "linkedin_url": get("linkedin_url"), "company_linkedin_url": get("company_linkedin_url"), "city": get("city"),
            "state": get("state"), "sector": get("sector"), "role": get("role"),
        }
        if base.get("email") and clean_email(base["email"])[0] is None:
            base["email"] = ""                     # e.g. Apollo's email_not_unlocked@domain.com placeholder
        for key, value in fill.items():
            if value and not base.get(key):
                base[key] = str(value)
        if base.get("company_size") and to_int(base["company_size"]) == 0:
            base["company_size"] = str(parse_company_size(base["company_size"])[0])
        if base.get("phone") and not normalise_phone(base["phone"]):
            mobiles = [e for e, kind in parse_phones(base["phone"])["numbers"] if kind == "mobile"]
            base["phone"] = mobiles[0] if mobiles else base["phone"]
        for header, field in custom_cols:
            value = coerce_soft(field, row.get(header))
            if value not in (None, "") and f"custom:{field['key']}" not in base:
                base[f"custom:{field['key']}"] = value
        rows.append(base)
    return rows
