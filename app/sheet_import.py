"""Import the team's BMI Google Sheet (Leads tab and Demo Shared tab) into leads."""
import csv
import io
import re
from typing import Optional

from .hygiene import business_domain, normalise_phone, safe_http_url
from .lead_model import build_lead, event, to_int

PHONE_SPLIT_RE = re.compile(r"[/,;]")
MOBILE_LEN = 10
LANDLINE_STD_MAX_LEADING = "5"
FAKE_NUMBERS = ("1234567890", "0123456789")

# sheet "Lead Status" -> (rank used to pick the furthest row, our stage)
STATUS_STAGE = {
    "lost": (-2, "disqualified"), "not now": (-1, "not_now"), "new": (0, "new"), "contacted": (1, "contacted"),
    "interested": (2, "engaged"), "demo scheduled": (3, "engaged"), "demo done": (4, "call_done"),
    "proposal sent": (5, "call_done"), "won": (6, "customer"),
}


def read_rows(csv_text: str) -> list:
    """CSV rows as dicts; a repeated header (the sheet has two 'Remarks') gets ' (2)' so nothing is lost."""
    reader = csv.reader(io.StringIO(csv_text))
    header = next(reader, [])
    seen, names = {}, []
    for name in (h.strip() for h in header):
        seen[name] = seen.get(name, 0) + 1
        names.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return [dict(zip(names, row)) for row in reader if any(cell.strip() for cell in row)]


def parse_phones(raw: str) -> dict:
    mobiles, landlines, junk = [], [], False
    for part in PHONE_SPLIT_RE.split(raw or ""):
        digits = re.sub(r"\D", "", part)
        if len(digits) == MOBILE_LEN + 2 and digits.startswith("91"):
            digits = digits[2:]
        elif len(digits) == MOBILE_LEN + 1 and digits.startswith("0"):
            digits = digits[1:]
        if len(digits) != MOBILE_LEN:
            continue
        if digits in FAKE_NUMBERS or len(set(digits)) == 1:
            junk = True
        elif normalise_phone(digits):
            mobiles.append(normalise_phone(digits))
        elif digits[0] <= LANDLINE_STD_MAX_LEADING:
            landlines.append(f"+91{digits}")
    return {"mobiles": list(dict.fromkeys(mobiles)), "landlines": list(dict.fromkeys(landlines)), "junk": junk}


def _first(rows: list, key: str) -> str:
    return next((r.get(key, "").strip() for r in rows if r.get(key, "").strip()), "")


def _status_of(row: dict) -> tuple:
    return STATUS_STAGE.get(row.get("Lead Status", "").strip().lower(), STATUS_STAGE["new"])


def _row_note(row: dict) -> Optional[str]:
    bits = [
        row.get("Open Position", "").strip(),
        f"call: {row['Call Outcome'].strip()}" if row.get("Call Outcome", "").strip() else "",
        f"LinkedIn: {row['Status'].strip()}" if row.get("Status", "").strip() else "",
        row.get("Remarks (2)", "").strip(), row.get("Remarks", "").strip(),
    ]
    text = " — ".join(b for b in bits if b)
    has_signal = any(row.get(k, "").strip() for k in ("Call Outcome", "Status", "Remarks (2)", "Remarks"))
    return text if has_signal else None


def _company_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


def sheet_leads(rows: list) -> list:
    groups = {}
    for row in rows:
        name = row.get("Company Name", "").strip()
        if name:
            groups.setdefault(_company_key(name), []).append(row)
    leads = []
    for group in groups.values():
        phones = [parse_phones(r.get("Contact Number", "")) for r in group]
        mobiles = list(dict.fromkeys(m for p in phones for m in p["mobiles"]))
        email = _first(group, "Email ID")
        website_from_email = business_domain(email)
        lead = build_lead({
            "company": _first(group, "Company Name"), "contact_name": _first(group, "Contact Person"),
            "title": _first(group, "Designation"), "email": email, "phone": mobiles[0] if mobiles else "",
            "website": f"https://{website_from_email}" if website_from_email else "",
            "country": "IN",
        }, "bmi_sheet", allow_company_key=True)
        if lead is None:
            continue
        titles = list(dict.fromkeys(r.get("Open Position", "").strip() for r in group if r.get("Open Position", "").strip()))
        rank, stage = max((_status_of(r) for r in group), key=lambda s: s[0])
        notes = [n for n in (_row_note(r) for r in group) if n]
        leads.append({
            **lead,
            "stage": stage,
            "phones": mobiles,
            "landlines": list(dict.fromkeys(l for p in phones for l in p["landlines"])),
            "phone_junk": any(p["junk"] for p in phones),
            "website_guess": bool(website_from_email),
            "role": " and ".join(titles[:2]),
            "open_roles": sum(max(1, to_int(r.get("No. of Openings"))) for r in group),
            "max_job_age_days": max((to_int(r.get("Days Live")) for r in group), default=0),
            "company_size": to_int(re.sub(r"[^0-9]", "", _first(group, "Company Size (employees)")[:6])) if _first(group, "Company Size (employees)")[:1].isdigit() else 0,
            "city": _first(group, "City"), "sector": _first(group, "Company Sector"),
            "job_links": [r["Job Posting Link"].strip() for r in group if r.get("Job Posting Link", "").strip()],
            "linkedin_url": safe_http_url(_first(group, "LinkedIn Profile URL")),
            "contact_confidence": _first(group, "Contact Confidence"),
            "timeline": [*lead["timeline"], *[event("sheet", n) for n in notes]],
        })
    return leads


def demo_leads(rows: list) -> list:
    leads = []
    for row in rows:
        phones = parse_phones(row.get("Contact Number", ""))
        email = row.get("Email ID", "").strip()
        domain = business_domain(email)
        lead = build_lead({
            "company": row.get("Company Name", "").strip(), "contact_name": row.get("Contact Person", "").strip(),
            "title": row.get("Designation", "").strip(), "email": email,
            "phone": phones["mobiles"][0] if phones["mobiles"] else "",
            "website": f"https://{domain}" if domain else "", "country": "IN",
        }, "bmi_sheet:demo", allow_company_key=True)
        if lead is None:
            continue
        status = row.get("Status", "").strip()
        leads.append({
            **lead,
            "stage": "customer" if "onboarded" in status.lower() else "call_done",
            "phones": phones["mobiles"], "website_guess": bool(domain),
            "demo_given": row.get("Demo Given Date", "").strip(), "demo_status": status,
            "timeline": [*lead["timeline"], event("demo", f"Demo given {row.get('Demo Given Date', '').strip()} — {status}")],
        })
    return leads


DM_SKIP_NAMES = ("", "(company page)", "—", "-")


def decision_makers(rows: list) -> list:
    """People found by hand on the team sheet's 'Decision Makers' tab (company pages and notes are skipped)."""
    people = []
    for row in rows:
        company = row.get("Company", "").strip()
        name = row.get("Name", "").strip()
        if not company or company.lower() == "notes" or name.lower() in DM_SKIP_NAMES:
            continue
        title = next((v.strip() for k, v in row.items() if k.startswith("Designation") and v), "")
        linkedin = next((v.strip() for k, v in row.items() if k.startswith("LinkedIn") and v.strip().startswith("http")), "")
        people.append({"company": company, "contact_name": name, "title": "" if title in DM_SKIP_NAMES else title,
                       "linkedin_url": linkedin, "found_at": row.get("Where I found it", "").strip(),
                       "confidence": row.get("Confidence", "").strip(), "fits_size": next((v.strip() for k, v in row.items() if k.startswith("Fits")), "")})
    return people


_LEGAL = re.compile(r"\b(pvt|private|ltd|limited|llp|inc|india|co)\b\.?")


def company_match_key(name: str) -> str:
    """'Cai Store (footwear)' and 'Make Your Own Perfume Pvt Ltd' match 'Cai Store' and 'Make Your Own Perfume'."""
    base = re.sub(r"\(.*?\)", " ", (name or "").lower())
    return _company_key(_LEGAL.sub(" ", base))
