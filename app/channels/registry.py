"""The Integrations hub catalogue: channels, and inside each channel its providers.

Provider entry: id, channel, title, why, docs_url, fields[{key,label,required,secret,placeholder,help,kind}],
steps[], test, capabilities[], plus recommended, cost_note, webhook, oauth, link_based, custom.

Storage stays in the `integrations` collection. Each hub channel maps to one integration id; the three that
existed before keep their ids (sending_domain, whatsapp, telephony), so `/api/integrations` keeps working.
"""
from .. import integrations as legacy
from .catalog_calendar import CALENDAR
from .catalog_email import EVENTS, INBOUND, SENDING
from .catalog_phone import CALLS, SMS, WHATSAPP

# kind: exclusive = one active provider sends; multi = every saved provider is listened to;
# legacy = the older one-card-per-integration connectors (lead data, AI), shown in the hub as they are.
CHANNELS = [
    {"id": "email_send", "title": "Email sending", "integration_id": "sending_domain", "kind": "exclusive",
     "live_flag": "live_sending",
     "about": "The service that delivers your sequence emails. Real sending also needs 'Send for real' on the Outbox page."},
    {"id": "inbound_email", "title": "Reply capture", "integration_id": "inbound_email", "kind": "multi",
     "about": "Brings email replies into the app so the sequence stops and replies are read automatically."},
    {"id": "email_events", "title": "Bounces", "integration_id": "email_events", "kind": "multi",
     "about": "Bounces and spam complaints stop further sending to that address, protecting your domain."},
    {"id": "whatsapp", "title": "WhatsApp", "integration_id": "whatsapp", "kind": "exclusive", "live_flag": "live_whatsapp",
     "about": "Messages to leads who said yes. Stays in practice mode until connected and 'Send for real' is on."},
    {"id": "sms", "title": "SMS", "integration_id": "sms", "kind": "exclusive", "live_flag": "live_sms",
     "about": "Text messages in India need DLT registration first. Practice mode until 'Send for real' is on."},
    {"id": "calls", "title": "Calls", "integration_id": "telephony", "kind": "exclusive",
     "about": "How Priya's call desk places calls."},
    {"id": "calendar", "title": "Calendar & meetings", "integration_id": "calendar", "kind": "exclusive",
     "about": "Book demos from a lead's page, with a video link, and see bookings land on the lead."},
    {"id": "lead_data", "title": "Lead data", "kind": "legacy",
     "legacy_ids": ["apollo", "hunter", "firecrawl", "contact_scraper", "sales_navigator", "email_verifier"],
     "about": "Where contact details come from. Compare them on the Data sources page."},
    {"id": "ai", "title": "AI", "kind": "legacy", "legacy_ids": ["llm"],
     "about": "Writes reply drafts and reads replies the rules cannot place."},
]
HUB_PROVIDERS = {"email_send": SENDING, "inbound_email": INBOUND, "email_events": EVENTS, "whatsapp": WHATSAPP,
                 "sms": SMS, "calls": CALLS, "calendar": CALENDAR}
LEGACY_DOCS = {
    "apollo": "https://docs.apollo.io/", "hunter": "https://hunter.io/api-documentation",
    "firecrawl": "https://docs.firecrawl.dev/", "contact_scraper": "https://github.com/omkarcloud",
    "sales_navigator": "https://business.linkedin.com/sales-solutions", "email_verifier": "https://www.zerobounce.net/docs/",
    "llm": "https://docs.anthropic.com/en/api/getting-started",
}
LEGACY_RECOMMENDED = {"apollo", "llm"}
# Old free-text values of whatsapp.values.provider that now name a hub provider.
LEGACY_PROVIDER_ALIASES = {"gupshup": "gupshup", "interakt": "interakt", "wati": "wati", "aisensy": "aisensy",
                           "meta": "meta_cloud", "meta_cloud": "meta_cloud"}


def _legacy_provider(item: dict, channel_id: str) -> dict:
    return {"id": item["id"], "channel": channel_id, "title": item["title"], "why": item["why"],
            "docs_url": LEGACY_DOCS[item["id"]], "fields": item["fields"], "steps": item["steps"],
            "test": "Uses the connector's own Test button (same as Setup → Connections).",
            "capabilities": ["lead_data"] if channel_id == "lead_data" else ["ai"], "recommended": item["id"] in LEGACY_RECOMMENDED,
            "cost_note": "", "webhook": None, "oauth": False, "link_based": False, "custom": False,
            "legacy": True, "integration_id": item["id"], "modes": item["modes"]}


def _build() -> dict:
    legacy_by_id = {i["id"]: i for i in legacy.CATALOGUE}
    out = {}
    for ch in CHANNELS:
        if ch["kind"] == "legacy":
            out[ch["id"]] = [_legacy_provider(legacy_by_id[i], ch["id"]) for i in ch["legacy_ids"]]
        else:
            out[ch["id"]] = [{**p, "legacy": False, "integration_id": ch["integration_id"]} for p in HUB_PROVIDERS[ch["id"]]]
    return out


PROVIDERS = _build()
CHANNELS_BY_ID = {c["id"]: c for c in CHANNELS}
BY_KEY = {(c, p["id"]): p for c, plist in PROVIDERS.items() for p in plist}


def channel(channel_id: str) -> dict:
    """KeyError for unknown channels."""
    return CHANNELS_BY_ID[channel_id]


def get_provider(channel_id: str, provider_id: str) -> dict:
    """KeyError for unknown channel / provider pairs."""
    return BY_KEY[(channel_id, provider_id)]


def providers_for(channel_id: str) -> list:
    return PROVIDERS[channel_id]


def is_hub_channel(channel_id: str) -> bool:
    return channel_id in CHANNELS_BY_ID and CHANNELS_BY_ID[channel_id]["kind"] != "legacy"


def webhook_pairs() -> set:
    return {key for key, p in BY_KEY.items() if p.get("webhook")}


def channel_for_integration(integration_id: str):
    return next((c["id"] for c in CHANNELS if c.get("integration_id") == integration_id), None)


def secret_keys_for_integration(integration_id: str) -> set:
    """Every secret field any hub provider stores under this integration id (for masking in old endpoints)."""
    ch = channel_for_integration(integration_id)
    if not ch:
        return set()
    return {f["key"] for p in PROVIDERS[ch] for f in p["fields"] if f.get("secret")}


def hub_provider_for_legacy(integration_id: str, values: dict):
    """The hub provider a legacy entry's values.provider names, or None."""
    ch = channel_for_integration(integration_id)
    if not ch:
        return None
    raw = str(values.get("provider") or "").strip().lower()
    pid = raw if (ch, raw) in BY_KEY else LEGACY_PROVIDER_ALIASES.get(raw)
    return BY_KEY.get((ch, pid)) if pid else None


def required_keys(provider: dict) -> list:
    return [f["key"] for f in provider["fields"] if f.get("required")]


def defaults(provider: dict) -> dict:
    return {f["key"]: f["default"] for f in provider["fields"] if f.get("default") is not None}


def parse_template_map(text) -> dict:
    """'ours = theirs' per line → {ours: theirs}. Lines without '=' are ignored."""
    out = {}
    for line in str(text or "").splitlines():
        if "=" in line:
            ours, theirs = (part.strip() for part in line.split("=", 1))
            if ours and theirs:
                out[ours] = theirs
    return out
