"""Integration catalogue: what each connector needs, with easy setup steps.

Each integration supports `manual` mode (CSV/copy-paste, no keys) and `api` mode.
"""

SECRET_TAIL = 4

CATALOGUE = [
    {
        "id": "sending_domain", "title": "Cold-email sending domain", "group": "Email",
        "why": "Cold email must go from a separate domain so your real customer emails stay safe.",
        "modes": ["api"],
        "fields": [
            {"key": "domain", "label": "Sending domain", "required": True, "placeholder": "mail.yourbrand.in"},
            {"key": "from_name", "label": "From name", "required": True, "placeholder": "Priya from BookMyInterview"},
            {"key": "from_email", "label": "From email", "required": True, "placeholder": "hello@mail.yourbrand.in"},
            {"key": "smtp_host", "label": "SMTP host", "required": False, "placeholder": "smtp.resend.com"},
            {"key": "api_key", "label": "Email provider API key", "required": True, "secret": True},
        ],
        "steps": [
            "Buy a look-alike domain (not bookmyinterview.ai).",
            "Add it to your email provider (Resend, Google Workspace or similar).",
            "Add the SPF, DKIM and DMARC records they show you at your domain registrar.",
            "Warm up the mailbox for 2 weeks (start at 20 emails a day).",
            "Paste the details here.",
        ],
    },
    {
        "id": "whatsapp", "title": "WhatsApp Business", "group": "Messaging",
        "why": "Sends the success story after a lead says yes. Only for leads who agreed.",
        "modes": ["api", "manual"],
        "fields": [
            {"key": "provider", "label": "Provider", "required": True, "placeholder": "gupshup / interakt / wati"},
            {"key": "phone_number_id", "label": "WhatsApp number or ID", "required": True},
            {"key": "api_key", "label": "API key", "required": True, "secret": True},
        ],
        "steps": [
            "Pick a provider (Gupshup, Interakt or WATI).",
            "Register your WhatsApp Business number.",
            "Get your first-message template approved.",
            "Paste the number and API key here. Manual mode: copy messages and send from the app.",
        ],
    },
    {
        "id": "apollo", "title": "Apollo", "group": "Lead data",
        "why": "Finds work email and phone for the decision maker.",
        "modes": ["api", "manual"],
        "fields": [{"key": "api_key", "label": "Apollo API key", "required": True, "secret": True}],
        "steps": [
            "Use the Apollo extension on LinkedIn or company sites.",
            "Export the list as CSV and upload it under Leads > Import (manual mode).",
            "Or paste an API key here to enrich leads automatically.",
        ],
    },
    {
        "id": "sales_navigator", "title": "LinkedIn Sales Navigator", "group": "Lead data",
        "why": "Search and save target accounts. Export by hand; no automation.",
        "modes": ["manual"],
        "fields": [],
        "steps": [
            "Build a lead list in Sales Navigator.",
            "Export the list (or use your CSV export tool) and import it under Leads > Import.",
            "Send LinkedIn messages yourself; the app drafts the text.",
        ],
    },
    {
        "id": "llm", "title": "AI writer", "group": "AI",
        "why": "Writes personalised emails, replies and call scripts.",
        "modes": ["api"],
        "fields": [
            {"key": "provider", "label": "Provider", "required": True, "placeholder": "anthropic / openai"},
            {"key": "api_key", "label": "API key", "required": True, "secret": True},
        ],
        "steps": ["Create an API key with your AI provider.", "Paste it here."],
    },
    {
        "id": "email_verifier", "title": "Email verifier", "group": "Data hygiene",
        "why": "Checks emails before sending to protect your sender reputation.",
        "modes": ["api", "manual"],
        "fields": [
            {"key": "provider", "label": "Provider", "required": True, "placeholder": "zerobounce / neverbounce"},
            {"key": "api_key", "label": "API key", "required": True, "secret": True},
        ],
        "steps": ["Create an account with a verifier.", "Paste the API key here, or verify CSVs yourself."],
    },
    {
        "id": "telephony", "title": "Calling (Priya's dialler)", "group": "Messaging",
        "why": "Click-to-call and call outcome logging for the call desk.",
        "modes": ["api", "manual"],
        "fields": [
            {"key": "click_to_call_url", "label": "Click-to-call URL template", "required": True,
             "placeholder": "https://dialler/call?number={phone}"},
        ],
        "steps": ["Ask the dialler team for a click-to-call link.", "Paste it here. Manual mode: Priya dials herself and logs the result."],
    },
    {
        "id": "hunter", "title": "Hunter.io", "group": "Lead data",
        "why": "Finds public work emails for a company's domain. Try its free plan on the Data sources page.",
        "modes": ["api"],
        "fields": [{"key": "api_key", "label": "Hunter API key", "required": True, "secret": True}],
        "steps": [
            "Sign up for the free plan at hunter.io.",
            "Copy your API key from your Hunter account (API page).",
            "Paste it here and save.",
            "Open Data sources and run the comparison to see how many leads it finds.",
        ],
    },
    {
        "id": "firecrawl", "title": "Firecrawl", "group": "Lead data",
        "why": "Reads company websites that need JavaScript, so more contact pages can be checked. It honours robots.txt.",
        "modes": ["api"],
        "fields": [
            {"key": "base_url", "label": "Firecrawl address", "required": False,
             "placeholder": "https://api.firecrawl.dev or your self-hosted URL"},
            {"key": "api_key", "label": "Firecrawl API key (not needed when self-hosted)", "required": False, "secret": True},
        ],
        "steps": [
            "Cloud: sign up at firecrawl.dev and copy your API key. Check their site for how many free credits you get.",
            "Or self-host it with Docker on your own computer or server (e.g. http://localhost:3002) and leave the key empty. "
            "A private address like that is blocked unless the server sets OUTREACH_ALLOW_PRIVATE_HOSTS=localhost:3002.",
            "Self-hosting is under the AGPL-3.0 licence: fine for internal use; if you change it and offer it to others you must share your changes.",
            "Paste the address and/or key here, then run the comparison on the Data sources page.",
        ],
    },
    {
        "id": "contact_scraper", "title": "Website contact scraper (open source)", "group": "Lead data",
        "why": "A free tool you run on your own computer to collect public emails, phones and social links from company websites.",
        "modes": ["manual"],
        "fields": [],
        "steps": [
            "Install the open-source omkarcloud website-email-contact-scraper on your computer (see its GitHub page).",
            "Give it your company websites and use the homepage or key_pages mode only.",
            "Do not switch on bot-wall bypassing or proxies; skip any site that blocks it.",
            "Export the results as JSON and upload the file on the Data sources page. Each value keeps the page it came from.",
        ],
    },
    {
        "id": "google_places", "title": "Google Places (business search)", "group": "Lead data",
        "why": "Lets a campaign find businesses by type and city (name, website, office phone) with your own Google key.",
        "modes": ["api"],
        "fields": [{"key": "api_key", "label": "Places API key", "required": True, "secret": True}],
        "steps": [
            "Open the Google Cloud console and pick (or create) a project with billing switched on.",
            "Enable 'Places API (New)' for that project.",
            "Create an API key under APIs & Services > Credentials and restrict it to the Places API.",
            "Check Google's pricing page for the free monthly allowance and the cost per search.",
            "Paste the key here and save. Campaigns can then search for businesses such as 'BPO companies in Pune'.",
        ],
    },
]


def _mask(value: str) -> str:
    return "••••" + value[-SECRET_TAIL:] if len(value) > SECRET_TAIL else "••••"


SECRET_WORDS = ("password", "secret", "token", "api_key", "apikey", "authkey")


def _hub():
    """The Integrations hub registry, imported late (it imports this module)."""
    from .channels import registry
    return registry


def _status(item: dict, mode: str, values: dict) -> str:
    if not mode:
        return "not_configured"
    if mode == "manual":
        return "configured"
    # sending_domain, whatsapp and telephony may now hold a hub provider (values.provider): use its required fields.
    hub = _hub().hub_provider_for_legacy(item["id"], values)
    fields = hub["fields"] if hub else item["fields"]
    defaults = {f["key"]: f.get("default") for f in fields if f.get("default")}
    missing = [f for f in fields if f.get("required") and not (values.get(f["key"]) or defaults.get(f["key"]))]
    return "incomplete" if missing else "configured"


def _secret(key: str, secret_keys: set) -> bool:
    return key in secret_keys or any(word in key.lower() for word in SECRET_WORDS)


def describe(saved: dict) -> list:
    """Merge the catalogue with saved settings; secrets are masked (including hub provider secrets)."""
    out = []
    for item in CATALOGUE:
        entry = saved.get(item["id"], {})
        mode, values = entry.get("mode", ""), entry.get("values", {})
        secret_keys = {f["key"] for f in item["fields"] if f.get("secret")} | _hub().secret_keys_for_integration(item["id"])
        shown = {k: (_mask(v) if _secret(k, secret_keys) and v and isinstance(v, str) else v) for k, v in values.items()
                 if not isinstance(v, (list, dict))}
        out.append({**item, "mode": mode, "values": shown, "status": _status(item, mode, values)})
    return out


def known_ids() -> set:
    return {i["id"] for i in CATALOGUE}


def _add_source_cards() -> None:
    """Lead-discovery and enrichment sources (app/sources) get their own Lead data cards, built from the source registry."""
    from .sources.registry import catalogue_entries
    CATALOGUE.extend(catalogue_entries())


_add_source_cards()
