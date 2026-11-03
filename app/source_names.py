"""One list of lead sources: a stable id, a plain-English label and a kind for every place a lead can come from.

Stored leads keep their original free-text `source` ("apollo_search", "web_scraper:https://...", "bmi_sheet:demo").
`canonical(raw)` maps any of them to a registry id and splits the `:detail` suffix off, and `backfill(store)` adds
`source_id` next to `source` (never rewriting it), so older data keeps working and the step can be repeated safely.
The web app reads this list from GET /api/lead-sources, so labels live in exactly one place.
"""
from datetime import datetime, timezone

KINDS = ("inbound", "outbound", "import", "referral", "manual")

# (id, label, kind)
SOURCES = (
    # inbound: the person came to us
    ("meta_ads", "Facebook / Instagram ads", "inbound"),
    ("google_ads", "Google Ads", "inbound"),
    ("website_form", "Website form", "inbound"),
    ("whatsapp_click", "WhatsApp message", "inbound"),
    ("linkedin_leadgen", "LinkedIn lead form", "inbound"),
    ("indiamart", "IndiaMART", "inbound"),
    ("justdial", "JustDial", "inbound"),
    ("tradeindia", "TradeIndia", "inbound"),
    ("tiktok_ads", "TikTok ads", "inbound"),
    ("zapier_webhook", "Webhook (Zapier / Make / n8n)", "inbound"),
    ("sheet_sync", "Google Sheet sync", "inbound"),
    ("other_inbound", "Other inbound", "inbound"),
    # outbound: we found them
    ("apollo", "Apollo", "outbound"),
    ("google_maps", "Google Maps", "outbound"),
    ("lusha", "Lusha", "outbound"),
    ("zoominfo", "ZoomInfo", "outbound"),
    ("hunter", "Hunter", "outbound"),
    ("snov", "Snov", "outbound"),
    ("directory", "Business directory", "outbound"),
    ("sales_navigator", "LinkedIn Sales Navigator", "outbound"),
    ("job_board", "Job board", "outbound"),
    ("careers_page", "Careers page", "outbound"),
    ("web_scraper", "Website contact finder", "outbound"),
    ("enrichment", "Website lookup", "outbound"),
    # imports and hand entry
    ("bmi_sheet", "Team sheet", "import"),
    ("sheet", "Spreadsheet import", "import"),
    ("referral", "Referral", "referral"),
    ("manual", "Added by hand", "manual"),
    ("unknown", "Unknown", "manual"),
)
# Old spellings and near-duplicates -> the id they mean.
ALIASES = {
    "apollo_search": "apollo", "apollo_hiring": "apollo", "contact_scraper": "web_scraper",
    "google_places": "google_maps", "places": "google_maps", "google_maps": "google_maps",
    "bmi_demo_sheet": "bmi_sheet", "bmi": "bmi_sheet", "csv": "sheet", "import": "sheet", "upload": "sheet",
    "apollo.io": "apollo", "linkedin_sales_navigator": "sales_navigator", "salesnav": "sales_navigator",
    "job_page": "job_board", "jobs": "job_board", "scraper": "web_scraper", "website": "web_scraper",
    "facebook": "meta_ads", "instagram": "meta_ads", "meta": "meta_ads", "facebook_ads": "meta_ads",
    "google": "google_ads", "linkedin": "linkedin_leadgen", "tiktok": "tiktok_ads",
    "webhook": "zapier_webhook", "zapier": "zapier_webhook", "generic": "zapier_webhook", "make": "zapier_webhook",
    "n8n": "zapier_webhook", "whatsapp": "whatsapp_click", "web_form": "website_form", "form": "website_form",
    "landing_page": "website_form", "google_sheet": "sheet_sync", "inbound": "other_inbound",
    "dnb": "directory", "justdial.com": "justdial", "indiamart.com": "indiamart",
}
BY_ID = {sid: {"id": sid, "label": label, "kind": kind} for sid, label, kind in SOURCES}
INBOUND_IDS = tuple(sid for sid, _, kind in SOURCES if kind == "inbound")
PICKABLE = tuple(s for s in BY_ID if s != "unknown")


def _norm(text: str) -> str:
    return "".join(c if c.isalnum() or c in "._" else "_" for c in str(text or "").strip().lower()).strip("_")


def canonical(raw) -> tuple:
    """(registry id, detail) for any stored or typed source value. Unknown text becomes ("other", text) -> "unknown"."""
    text = str(raw or "").strip()
    if not text:
        return "unknown", ""
    head, _, detail = text.partition(":")
    key = _norm(head)
    if key in BY_ID:
        return key, detail.strip()
    if key in ALIASES:
        return ALIASES[key], detail.strip()
    for prefix, target in (("bmi_sheet", "bmi_sheet"), ("web_scraper", "web_scraper"), ("referral", "referral"),
                           ("apollo", "apollo"), ("google_maps", "google_maps"), ("google_places", "google_maps")):
        if key.startswith(prefix):
            return target, (detail or text[len(prefix):].lstrip("_ -")).strip()
    return "unknown", text[:200]


def source_id(raw) -> str:
    return canonical(raw)[0]


def label_of(sid_or_raw) -> str:
    sid = sid_or_raw if sid_or_raw in BY_ID else canonical(sid_or_raw)[0]
    if sid == "unknown" and sid_or_raw not in (None, "", "unknown"):
        return str(sid_or_raw).split(":")[0].replace("_", " ").strip().capitalize() or "Unknown"
    return BY_ID[sid]["label"]


def kind_of(sid: str) -> str:
    return BY_ID.get(sid, BY_ID["unknown"])["kind"]


def is_inbound_id(sid: str) -> bool:
    return sid in INBOUND_IDS


def registry() -> dict:
    """What GET /api/lead-sources returns."""
    return {"sources": [BY_ID[s] for s in PICKABLE], "kinds": list(KINDS), "inbound": list(INBOUND_IDS),
            "aliases": {k: v for k, v in ALIASES.items()}}


def lead_source_id(lead: dict) -> str:
    return lead.get("source_id") or canonical(lead.get("source"))[0]


def touches(lead: dict) -> list:
    """The lead's `sources[]` history as dicts, including the first source even when it was never written down."""
    out = []
    for item in lead.get("sources") or []:
        if isinstance(item, dict) and item.get("id"):
            out.append(item)
        elif isinstance(item, str):
            out.append({"id": canonical(item)[0], "at": lead.get("created_at") or "", "detail": canonical(item)[1]})
    if not out:
        sid, detail = canonical(lead.get("source"))
        out.append({"id": lead.get("source_id") or sid, "at": lead.get("created_at") or "", "detail": detail})
    return out


def add_touch(lead: dict, raw_source, detail: str = "", attribution: dict = None, at: str = None, every: bool = False) -> bool:
    """Append a `sources[]` entry when the lead is seen again from a source it has not been seen from. Changes `lead`
    in place and returns whether it added one. A source id is added once, unless `every` is set (inbound captures:
    each capture is its own touch)."""
    sid, parsed = canonical(raw_source)
    detail = (detail or parsed or "")[:200]
    current = touches(lead)
    if not every and any(t["id"] == sid for t in current):
        return False
    entry = {"id": sid, "at": at or datetime.now(timezone.utc).isoformat(timespec="seconds"), "detail": detail}
    if attribution:
        entry["attribution"] = attribution
    lead["sources"] = [*current, entry]
    return True


def with_source_id(lead: dict) -> dict:
    """The lead with `source_id` filled in (never changes `source`)."""
    if lead.get("source_id"):
        return lead
    return {**lead, "source_id": canonical(lead.get("source"))[0]}


def backfill(store) -> int:
    """Write `source_id` on every lead that lacks it. Idempotent: a second run changes nothing and does not save."""
    if all(lead.get("source_id") for lead in store.read("leads")):
        return 0
    count = {"n": 0}

    def apply(leads: list) -> list:
        out = []
        for lead in leads:
            if not lead.get("source_id"):
                count["n"] += 1
                lead = {**lead, "source_id": canonical(lead.get("source"))[0]}
            out.append(lead)
        return out
    store.update("leads", apply)
    return count["n"]
