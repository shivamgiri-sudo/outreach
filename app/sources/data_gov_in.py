"""India Open Government Data platform (data.gov.in) resource API, e.g. RoC-wise Company Master Data from the Ministry of
Corporate Affairs. Docs: https://data.gov.in/apis  ; GET https://api.data.gov.in/resource/{resource_id}?api-key=&format=json&limit=&offset=
-> {total, count, offset, limit, records: [{...columns of the dataset...}]}. Licence: Government Open Data Licence - India (GODL).
The resource id is the dataset's own id from its data.gov.in page (you paste it); column names differ by dataset, so a few common
spellings are accepted (company_name / COMPANY_NAME, registered_office_address, company_status, ...). The dataset has no website or
contact details: it gives legal names, status and registered address."""
import re

from .base import SourceSpec, field

BASE = "https://api.data.gov.in/resource/"
ID_RE = re.compile(r"^[0-9a-fA-F-]{8,40}$")
PAGE = 100
MAX_PAGES = 3


def _col(rec: dict, *names) -> str:
    lower = {str(k).lower().replace(" ", "_"): v for k, v in rec.items()}
    for n in names:
        if lower.get(n):
            return str(lower[n]).strip()
    return ""


def map_record(rec: dict) -> dict:
    return {"company": _col(rec, "company_name", "companyname", "name"), "address": _col(rec, "registered_office_address", "registered_address", "address")[:300],
            "country": "IN", "source": "data_gov_in", "source_url": "https://data.gov.in/",
            "_status": _col(rec, "company_status", "status"), "_cin": _col(rec, "corporate_identification_number", "cin")}


def discover(ctx, target, limit):
    rid = str(ctx.values.get("resource_id") or "").strip()
    if not ID_RE.match(rid):
        raise ValueError("Paste the dataset's resource id (from its data.gov.in page) in the source settings.")
    cities = [c.lower() for c in target.get("cities") or []]
    words = [w.lower() for w in (target.get("industries") or []) + (target.get("keywords") or [])]
    rows, offset = [], 0
    for _ in range(MAX_PAGES):
        params = {"api-key": ctx.values.get("api_key", ""), "format": "json", "limit": PAGE, "offset": offset}
        page = ctx.cached(f"{rid}|{offset}", lambda p=params: [map_record(r) for r in ctx.get(BASE + rid, params=p).get("records") or [] if isinstance(r, dict)])
        for r in page:
            text = f"{r['company']} {r['address']}".lower()
            if r["company"] and (not r["_status"] or r["_status"].lower().startswith("active")) \
                    and (not cities or any(c in text for c in cities)) and (not words or any(w in text for w in words)):
                rows.append({k: v for k, v in r.items() if not k.startswith("_")})
        if len(page) < PAGE or len(rows) >= limit:
            break
        offset += PAGE
    return {"rows": rows[:limit], "requests": ctx.requests}


def test(ctx):
    rid = str(ctx.values.get("resource_id") or "").strip()
    if not ID_RE.match(rid):
        return {"ok": False, "message": "Paste the dataset's resource id first."}
    reply = ctx.get(BASE + rid, params={"api-key": ctx.values.get("api_key", ""), "format": "json", "limit": 1})
    return {"ok": True, "message": f"data.gov.in answered; the dataset reports {reply.get('total', 'an unknown number of')} record(s)."}


SPEC = SourceSpec(
    "data_gov_in", "data.gov.in company master data", ("discover_companies",), "Company discovery",
    "Registered companies (name, status, registered address) from an Indian government open dataset such as MCA's RoC-wise Company Master Data.",
    "https://data.gov.in/apis",
    "Government Open Data Licence - India (GODL): reuse allowed with attribution. Public registry data only: no personal contacts are read.",
    ["Register on data.gov.in and create an API key under My Account.", "Open the dataset you want (search 'Company Master Data'), and copy its resource id from the API tab.",
     "Paste the key and the resource id here and save."],
    fields=[field("api_key", "data.gov.in API key", True, True), field("resource_id", "Dataset resource id", True, False, "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx")],
    cost=0, cost_note="Free. Up to 3 pages of 100 records per run.", rate_per_min=20, free_note="Free with your own key.",
    discover=discover, test=test)
