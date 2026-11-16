"""Reading the Ministry of Corporate Affairs "Company Master Data" (data.gov.in, RoC-wise CSV files, GODL licence).

The RoC-wise files and the data.gov.in API spell their columns differently (CORPORATE_IDENTIFICATION_NUMBER or CIN,
DATE_OF_REGISTRATION or DATE_OF_INCORPORATION, AUTHORIZED_CAP or AUTHORISED_CAPITAL, ...). `map_record` takes any row
dict and keeps only the columns the register stores; unknown columns are ignored. `read_csv` streams a file row by row
(a BOM, quoted commas and newlines inside quotes are fine) and never loads the whole file. Rows without a CIN or a
company name are skipped and counted.

Assumed column names (from the published RoC-wise files and the API's lowercase keys; any of the spellings works):
  cin           CORPORATE_IDENTIFICATION_NUMBER, CIN, COMPANY_CIN, LLPIN
  name          COMPANY_NAME, NAME, LLP_NAME
  status        COMPANY_STATUS, STATUS, COMPANY_STATUS_FOR_EFILING
  cls           COMPANY_CLASS, CLASS
  category      COMPANY_CATEGORY, CATEGORY      sub_category  COMPANY_SUB_CATEGORY, SUB_CATEGORY
  incorporated  DATE_OF_REGISTRATION, DATE_OF_INCORPORATION, COMPANY_REGISTRATION_DATE, REGISTRATION_DATE
  state         REGISTERED_STATE, STATE, COMPANY_STATE_CODE (or the state code inside the CIN)
  authorised    AUTHORIZED_CAP, AUTHORISED_CAP, AUTHORIZED_CAPITAL, AUTHORISED_CAPITAL
  paid_up       PAIDUP_CAPITAL, PAID_UP_CAPITAL, PAIDUP_CAP, TOTAL_OBLIGATION_OF_CONTRIBUTION (LLP)
  nic           INDUSTRIAL_CLASS, NIC_CODE, COMPANY_INDUSTRIAL_CLASSIFICATION
  activity      PRINCIPAL_BUSINESS_ACTIVITY_AS_PER_CIN, PRINCIPAL_BUSINESS_ACTIVITY, ACTIVITY_DESCRIPTION
  address       REGISTERED_OFFICE_ADDRESS, REGISTERED_ADDRESS, ADDRESS
  roc           REGISTRAR_OF_COMPANIES, ROC, ROC_CODE, COMPANY_ROC_CODE
  email         EMAIL_ADDR, EMAIL, EMAIL_ADDRESS, EMAIL_ID (kept only when the file has it)
  last_return   LATEST_YEAR_ANNUAL_RETURN, LATEST_YEAR_FINANCIAL_STATEMENT
"""
import csv
import io
import re
from datetime import datetime

from .company_match import normalise, state_from_cin, state_key, tok_key

ALIASES = {
    "cin": ("corporate_identification_number", "cin", "company_cin", "corporate_identity_number", "llpin", "llp_identification_number"),
    "name": ("company_name", "name", "companyname", "llp_name", "name_of_company"),
    "status": ("company_status", "status", "company_status_for_efiling", "llp_status"),
    "cls": ("company_class", "class", "class_of_company"),
    "category": ("company_category", "category", "category_of_company"),
    "sub_category": ("company_sub_category", "company_subcategory", "sub_category", "subcategory"),
    "incorporated": ("date_of_registration", "date_of_incorporation", "company_registration_date", "registration_date",
                     "company_registration_date_date", "incorporation_date"),
    "state": ("registered_state", "state", "company_state_code", "state_name", "registered_office_state"),
    "authorised": ("authorized_cap", "authorised_cap", "authorized_capital", "authorised_capital", "authorized_capital_rs",
                   "authorised_capital_rs"),
    "paid_up": ("paidup_capital", "paid_up_capital", "paidup_cap", "paid_up_cap", "paidup_capital_rs",
                "total_obligation_of_contribution"),
    "nic": ("industrial_class", "nic_code", "company_industrial_classification", "industrial_classification"),
    "activity": ("principal_business_activity_as_per_cin", "principal_business_activity", "activity_description",
                 "company_activity", "business_activity", "principal_business_activity_description"),
    "address": ("registered_office_address", "registered_address", "address", "company_registered_office_address",
                "registered_office_address_of_company"),
    "roc": ("registrar_of_companies", "roc", "roc_code", "company_roc_code", "registrar_of_company"),
    "email": ("email_addr", "email", "email_address", "email_id", "company_email", "emailaddress"),
    "last_return": ("latest_year_annual_return", "latest_year_financial_statement", "latest_annual_return"),
}
_LOOKUP = {alias: field for field, names in ALIASES.items() for alias in names}
COLUMNS = ("cin", "name", "name_key", "tok_key", "status", "cls", "category", "sub_category", "incorporated", "authorised",
           "paid_up", "state", "roc", "address", "activity", "nic", "email", "last_return")
LIMITS = {"cin": 25, "name": 255, "status": 60, "cls": 40, "category": 80, "sub_category": 80, "state": 40, "roc": 40,
          "address": 400, "activity": 255, "nic": 12, "email": 120}
DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y", "%Y/%m/%d", "%d.%m.%Y",
                "%d-%m-%y", "%d/%m/%y", "%m/%d/%Y", "%B %d, %Y", "%d %B %Y")
ID_RE = re.compile(r"^[A-Z0-9-]{6,25}$")
EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[a-z]{2,}$", re.I)
_DMY = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$")
_YMD = re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[T ][0-9:.]+(?:Z|[+-]\d{2}:?\d{2})?)?$")
_TIME = re.compile(r"[T ]\d{1,2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
_PLAIN_NUMBER = re.compile(r"^\d+(\.\d+)?$")
_MONEY_JUNK = re.compile(r"(?i)rs\.?|inr|₹|\s")
_SPACES = re.compile(r"\s+")
BLANKS = {"", "na", "n/a", "nan", "null", "none", "-", "--", "not available", "nil"}


def header_key(name) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(name or "").replace("﻿", "").strip().lower()).strip("_")


def field_for(name) -> str:
    """The register column a source column fills ('' when ignored)."""
    return _LOOKUP.get(header_key(name), "")


def _blank(value) -> bool:
    return value is None or str(value).strip().lower() in BLANKS


def parse_date(value) -> str:
    """'2019-01-05' from the common spellings (dd-mm-yyyy first, as the MCA files use); '' when unreadable."""
    if _blank(value):
        return ""
    text = str(value).strip()
    m = _DMY.match(text)                                        # the usual shapes, without strptime
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31 and 1850 <= y <= 2100:
            return _checked(y, mo, d)
    m = _YMD.match(text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31 and 1850 <= y <= 2100:
            return _checked(y, mo, d)
    text = _TIME.sub("", text).strip()
    for fmt in DATE_FORMATS:
        try:
            day = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if 1850 <= day.year <= 2100:
            return day.strftime("%Y-%m-%d")
    if re.fullmatch(r"\d{5}(\.0+)?", text):                    # an Excel day number that slipped into a CSV
        from datetime import timedelta
        day = datetime(1899, 12, 30) + timedelta(days=int(float(text)))
        return day.strftime("%Y-%m-%d") if 1850 <= day.year <= 2100 else ""
    return ""


def _checked(y: int, mo: int, d: int) -> str:
    try:
        return datetime(y, mo, d).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def parse_money(value):
    """Rupees as an int (commas, 'Rs', decimals dropped); None when blank or not a number."""
    if _blank(value):
        return None
    text = str(value).replace(",", "").strip()
    if not _PLAIN_NUMBER.match(text):
        text = _MONEY_JUNK.sub("", text)
    try:
        amount = float(text)
    except ValueError:
        return None
    return int(amount) if 0 <= amount < 1e18 else None


def _title(name: str) -> str:
    return " ".join(w if w in ("and", "of") else w.capitalize() for w in name.split())


def map_record(rec: dict):
    """A register row (dict over COLUMNS) from one source row, or None when it has no CIN or no company name."""
    got = {}
    for key, value in rec.items():
        field = field_for(key)
        if field and field not in got and not _blank(value):
            got[field] = str(value).strip()
    return build(got)


def build(got: dict):
    """A register row from {register column: raw text} (blank values already left out)."""
    cin = _SPACES.sub("", got.get("cin", "")).upper()
    name = _SPACES.sub(" ", got.get("name", "")).strip()
    if not ID_RE.match(cin) or not name:
        return None
    key = normalise(name)
    if not key:
        return None
    row = {c: None for c in COLUMNS}
    row.update(cin=cin, name=name, name_key=key, tok_key=tok_key(key))
    for field in ("status", "cls", "category", "sub_category", "roc", "address", "activity", "nic"):
        if got.get(field):
            row[field] = _SPACES.sub(" ", got[field])
    state = got.get("state", "")
    canon = state_key(state) or state_from_cin(cin)
    row["state"] = _title(canon) if canon else (state.title() if len(state) > 2 else None)
    row["incorporated"] = parse_date(got.get("incorporated")) or None
    row["last_return"] = parse_date(got.get("last_return")) or None
    row["authorised"] = parse_money(got.get("authorised"))
    row["paid_up"] = parse_money(got.get("paid_up"))
    email = got.get("email", "").strip().lower()
    row["email"] = email if EMAIL_RE.match(email) else None
    for field, limit in LIMITS.items():
        if isinstance(row[field], str):
            row[field] = row[field][:limit]
    return row


class Counting(io.RawIOBase):
    """A binary file that counts the bytes read (import progress without a second pass)."""

    def __init__(self, handle):
        self.handle, self.read_bytes = handle, 0

    def readable(self):
        return True

    def readinto(self, buf):
        n = self.handle.readinto(buf)
        self.read_bytes += n or 0
        return n


def _delimiter(first_line: str) -> str:
    counts = {d: first_line.count(d) for d in (",", "\t", ";", "|")}
    return max(counts, key=counts.get) if max(counts.values()) else ","


def read_csv(handle, stats: dict):
    """Yield register rows from a binary CSV file handle, streaming. `stats` gets rows / skipped / columns / unknown."""
    text = io.TextIOWrapper(handle, encoding="utf-8-sig", errors="replace", newline="")
    first = text.readline()
    if not first:
        stats.setdefault("rows", 0)
        return
    delim = _delimiter(first)
    header = next(csv.reader([first], delimiter=delim))
    fields = [field_for(h) for h in header]
    stats["columns"] = sorted({f for f in fields if f})
    stats["unknown"] = [h for h, f in zip(header, fields) if not f and h.strip()][:30]
    if "cin" not in fields or "name" not in fields:
        raise ValueError("This file has no CIN and company name columns, so it does not look like the Company Master Data. "
                         "Columns found: " + ", ".join(h.strip() for h in header[:12]))
    csv.field_size_limit(1 << 20)
    stats.setdefault("rows", 0)
    stats.setdefault("skipped", 0)
    wanted = [(i, f) for i, f in enumerate(fields) if f]
    wanted = [(i, f) for n, (i, f) in enumerate(wanted) if f not in {g for _, g in wanted[:n]}]    # first column of a kind wins
    for values in csv.reader(text, delimiter=delim):
        if not values or not any(values):
            continue
        stats["rows"] += 1
        got = {}
        for i, field in wanted:
            if i < len(values):
                value = values[i].strip()
                if value and value.lower() not in BLANKS:
                    got[field] = value
        row = build(got)
        if row is None:
            stats["skipped"] += 1
            continue
        yield row
