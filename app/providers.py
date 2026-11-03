"""Lead-data providers tried side by side on the same leads, plus the contact-scraper JSON import.

Every provider returns the same shape and every found value keeps its source page. A comparison run never writes
contacts to leads; `apply` later copies one provider's stored results into empty email/phone fields only.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Optional

from .apollo import match_person
from .enrich import _same_domain, find_public_contacts, pick_best_email
from .http_util import TIMEOUT_SECONDS
from .hygiene import company_domain, is_role_address, normalise_email, normalise_phone
from .lead_model import event, now_iso

HUNTER_URL = "https://api.hunter.io/v2/domain-search"
HUNTER_LIMIT = 10
HUNTER_SOCIAL_KEYS = ("linkedin", "twitter", "facebook", "instagram", "youtube")
FIRECRAWL_DEFAULT_URL = "https://api.firecrawl.dev"
FIRECRAWL_MAX_PAGES = 3
DEFAULT_SAMPLE = 25
NO_FILL_STAGES = ("unsubscribed", "disqualified")
MAX_RUNS = 20
MAX_EMAILS_PER_ROW = 10
MAX_PHONES_PER_ROW = 5
EXAMPLE_ROWS = 5
NEEDS_NAME = "needs a contact name"
KEY_REJECTED = "the provider rejected the API key"
FIRECRAWL_REFUSED = "Firecrawl refused the request: check the API key, or the site does not allow it"
APOLLO_LOCKED_PREFIX = "email_not_unlocked"
HOST_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
SOCIAL_PLATFORMS = ("linkedin", "x", "twitter", "instagram", "facebook", "youtube", "tiktok", "github", "pinterest", "threads")
VALUE_KEYS = ("value", "email", "phone", "number", "url", "link", "href", "address")
SOURCE_KEYS = ("sources", "source_pages", "source", "pages", "source_url", "found_on")
WRAPPER_KEYS = ("results", "data", "items", "rows")


class RunNotFound(LookupError):
    """No saved comparison run has that id."""


# ---------- transports ----------
def get_json(url: str, headers: Optional[dict] = None) -> dict:
    """GET JSON, return the parsed reply. 401/403 raise PermissionError; other failures raise OSError."""
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise PermissionError(f"HTTP {exc.code}") from exc
        raise OSError(f"HTTP {exc.code}") from exc


# ---------- shared result shape ----------
def _drop_empty(values: dict) -> dict:
    return {k: v for k, v in values.items() if v not in (None, "")}


def _result(emails=(), phones=(), socials=None, technologies=(), credits_used=0, error=None) -> dict:
    out = {"emails": list(emails)[:MAX_EMAILS_PER_ROW], "phones": list(phones)[:MAX_PHONES_PER_ROW],
           "socials": dict(socials or {}), "technologies": list(technologies), "credits_used": credits_used}
    return {**out, "error": error} if error else out


def _from_crawl(found: dict, credits_used: int = 0) -> dict:
    emails = [{"value": e, "source": url} for e, url in found["emails"].items()]
    phones = [{"value": p, "source": url} for p, url in found["phones"].items()]
    return _result(emails, phones, credits_used=credits_used)


# ---------- providers ----------
def find_website(domain, company, config, transports, contact_name="") -> dict:
    """Our own crawler: the company's public pages, robots.txt respected, no cost."""
    return _from_crawl(find_public_contacts(domain, transports["fetch"]))


def parse_hunter(reply: dict, domain: str) -> dict:
    """Hunter Domain Search reply -> result. Shape follows Hunter's docs; CONFIRM with one live call before relying on it."""
    data = reply.get("data") or {}
    emails, phones = [], []
    for item in data.get("emails") or []:
        value = normalise_email(item.get("value"))
        if not value or not _same_domain(value, domain):
            continue
        sources = [s.get("uri") for s in item.get("sources") or [] if isinstance(s, dict) and s.get("uri")]
        source = sources[0] if sources else "hunter.io"
        name = " ".join(p for p in (item.get("first_name"), item.get("last_name")) if p)
        emails.append(_drop_empty({"value": value, "source": source, "confidence": item.get("confidence"),
                                   "name": name, "title": item.get("position")}))
        phone = normalise_phone(item.get("phone_number"))
        if phone:
            phones.append({"value": phone, "source": source})
    socials = {k: data[k] for k in HUNTER_SOCIAL_KEYS if isinstance(data.get(k), str) and data[k]}
    technologies = [t for t in data.get("technologies") or [] if isinstance(t, str)]
    # Counted as one search per call (conservative: Hunter says searches with no result are free; confirm on your plan).
    return _result(emails, phones, socials, technologies, credits_used=1)


def find_hunter(domain, company, config, transports, contact_name="") -> dict:
    query = urllib.parse.urlencode({"domain": domain, "api_key": config.get("api_key", ""), "limit": HUNTER_LIMIT})
    return parse_hunter(transports["get"](f"{HUNTER_URL}?{query}", {}), domain)


def firecrawl_base(config: dict) -> str:
    return (config.get("base_url") or FIRECRAWL_DEFAULT_URL).strip().rstrip("/")


def firecrawl_scrape(url: str, config: dict, post) -> str:
    """One page through Firecrawl's scrape endpoint. The /v1 path must be confirmed against the version you run."""
    key = config.get("api_key")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    reply = post(f"{firecrawl_base(config)}/v1/scrape", headers, {"url": url, "formats": ["html"]})
    data = reply.get("data")
    if reply.get("success") is False or not isinstance(data, dict):
        raise OSError(str(reply.get("error") or "Firecrawl could not read the page"))
    return data.get("html") or data.get("rawHtml") or ""


def firecrawl_fetcher(config: dict, post, max_pages: int = FIRECRAWL_MAX_PAGES):
    """A fetcher(url)->html for find_public_contacts that spends at most max_pages credits and stops after a refusal.

    Firecrawl honours robots.txt by default, so pages a site disallows are not read.
    """
    state = {"calls": 0, "refused": False}

    def fetch(url: str) -> str:
        if state["refused"]:
            raise PermissionError(FIRECRAWL_REFUSED)
        if state["calls"] >= max_pages:
            raise OSError("page budget for this company used up")
        state["calls"] += 1
        try:
            return firecrawl_scrape(url, config, post)
        except PermissionError:
            state["refused"] = True
            raise
    return fetch, state


def find_firecrawl(domain, company, config, transports, contact_name="") -> dict:
    """JS-rendered pages via Firecrawl, then the same contact extraction as our own crawler."""
    fetch, state = firecrawl_fetcher(config, transports["post"])
    found = find_public_contacts(domain, fetch)
    result = _from_crawl(found, credits_used=state["calls"])
    return {**result, "error": FIRECRAWL_REFUSED} if state["refused"] and not found["pages_checked"] else result


def find_apollo(domain, company, config, transports, contact_name="") -> dict:
    """Apollo person match; only useful when the decision maker's name is already known."""
    first, _, last = (contact_name or "").strip().partition(" ")
    if not first:
        return _result(error=NEEDS_NAME)
    person = match_person(config.get("api_key", ""), first, last.strip(), domain, company, transports["post"]) or {}
    email = normalise_email(person.get("email"))
    if email and email.startswith(APOLLO_LOCKED_PREFIX):
        email = None
    found = [_drop_empty({"value": email, "source": "api.apollo.io", "name": person.get("name") or contact_name,
                          "title": person.get("title")})] if email else []
    return _result(found, credits_used=1)


def _provider(pid, title, cost_note, needs, integration, prefixes, find) -> dict:
    return {"id": pid, "title": title, "cost_note": cost_note, "needs": needs, "integration": integration,
            "source_prefixes": prefixes, "find": find}


PROVIDERS = {p["id"]: p for p in (
    _provider("website", "Company website (built in)", "Free. Reads the company's own contact pages and honours robots.txt.",
              [], "", ("website", "enrichment"), find_website),
    _provider("hunter", "Hunter.io", "Free plan has a small monthly search allowance (check hunter.io). One search per company.",
              ["api_key"], "hunter", ("hunter",), find_hunter),
    _provider("firecrawl", "Firecrawl", f"Cloud: up to {FIRECRAWL_MAX_PAGES} page credits per company (check free credits on "
              "firecrawl.dev). Self-hosted: no credits.", [], "firecrawl", ("firecrawl",), find_firecrawl),
    _provider("apollo", "Apollo", "About one credit per lookup. Only leads that already have a contact name.",
              ["api_key"], "apollo", ("apollo",), find_apollo),
)}


def provider_status(saved: dict, pid: str) -> dict:
    """Whether a provider can run now, why not, and its saved settings."""
    provider = PROVIDERS[pid]
    if not provider["integration"]:
        return {"configured": True, "reason": "", "config": {}}
    entry = saved.get(provider["integration"], {})
    values = entry.get("values", {})
    missing = [k for k in provider["needs"] if not values.get(k)]
    if entry.get("mode") != "api":
        reason = "Not set up. Connect it in Setup (direct API mode)."
    elif missing:
        reason = f"Missing {', '.join(missing)}. Add it in Setup."
    elif pid == "firecrawl" and not (values.get("api_key") or values.get("base_url")):
        reason = "Add an API key, or the address of your self-hosted Firecrawl, in Setup."
    else:
        reason = ""
    return {"configured": not reason, "reason": reason, "config": values}


def describe_providers(store) -> list:
    saved = store.read("integrations")
    out = []
    for pid, p in PROVIDERS.items():
        status = provider_status(saved, pid)
        out.append({"id": pid, "title": p["title"], "cost_note": p["cost_note"], "needs": p["needs"],
                    "setup_id": p["integration"], "configured": status["configured"], "reason": status["reason"]})
    return out


# ---------- connection checks used by the Setup page ----------
def check_hunter(entry: dict, get) -> dict:
    """One domain search on hunter.io itself; may use one search from the plan."""
    key = entry.get("values", {}).get("api_key")
    if entry.get("mode") != "api" or not key:
        return {"ok": False, "message": "Connect Hunter first (direct API mode and a key)."}
    try:
        reply = get(f"{HUNTER_URL}?{urllib.parse.urlencode({'domain': 'hunter.io', 'api_key': key, 'limit': 1})}", {})
    except PermissionError:
        return {"ok": False, "message": "Hunter rejected the API key."}
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": f"Could not reach Hunter: {exc}"}
    ok = isinstance(reply.get("data"), dict)
    return {"ok": ok, "message": "Hunter accepted the key. The test may use one search." if ok else "Hunter answered with nothing."}


def check_firecrawl(entry: dict, post) -> dict:
    """One scrape of example.com through the configured Firecrawl."""
    values = entry.get("values", {})
    if entry.get("mode") != "api" or not (values.get("api_key") or values.get("base_url")):
        return {"ok": False, "message": "Add a Firecrawl API key, or the address of your self-hosted Firecrawl, first."}
    try:
        html = firecrawl_scrape("https://example.com", values, post)
    except PermissionError:
        return {"ok": False, "message": "Firecrawl rejected the API key."}
    except (OSError, ValueError) as exc:
        return {"ok": False, "message": f"Could not reach Firecrawl at {firecrawl_base(values)}: {exc}"}
    return {"ok": bool(html), "message": "Firecrawl read a test page. That used one credit on the cloud plan."
            if html else "Firecrawl answered but returned no page."}


# ---------- sampling and comparison ----------
def valid_domain(value) -> Optional[str]:
    """A public-looking host name (no IPs, no localhost), normalised; else None."""
    domain = company_domain(value)
    return domain if domain and HOST_RE.match(domain) else None


def is_indian(lead: dict) -> bool:
    country = (lead.get("country") or "").strip().lower()
    return country in ("in", "india") or (lead.get("phone") or "").startswith("+91") or (lead.get("domain") or "").endswith(".in")


def pick_sample(leads: list, size: int) -> list:
    """Leads with a website domain, open stages only; Indian leads first, then those still missing an email."""
    usable = [lead for lead in leads if valid_domain(lead.get("domain")) and lead.get("stage") not in NO_FILL_STAGES]
    return sorted(usable, key=lambda lead: (not is_indian(lead), bool(lead.get("email"))))[:size]


def run_provider(pid: str, sample: list, config: dict, transports: dict) -> list:
    """Run one provider over the sample. A rejected key stops the provider so credits are not wasted."""
    rows = []
    for lead in sample:
        domain = valid_domain(lead["domain"])
        row = {"lead_id": lead["id"], "company": lead.get("company", ""), "domain": domain}
        try:
            found = PROVIDERS[pid]["find"](domain, lead.get("company", ""), config, transports, lead.get("contact_name") or "")
        except PermissionError:
            return [*rows, {**row, **_result(error=KEY_REJECTED)}]
        except (OSError, ValueError) as exc:
            found = _result(error=f"lookup failed: {exc}")
        rows.append({**row, **found})
    return rows


def choose_email(emails: list, domain: str, suppressed=frozenset()) -> Optional[dict]:
    """Best usable email: hiring mailbox on the company's domain first; else one a provider tied to a person or the site marked official."""
    usable = [e for e in emails if e.get("value") and e["value"] not in suppressed]
    best = pick_best_email([e["value"] for e in usable], domain or "")
    if best:
        return next(e for e in usable if e["value"] == best)
    return next((e for e in usable if e.get("official") or e.get("name")), None)


def _example(row: dict) -> dict:
    email = choose_email(row["emails"], row["domain"])
    return _drop_empty({"company": row["company"], "domain": row["domain"], "email": email and email["value"],
                        "phone": row["phones"][0]["value"] if row["phones"] else None, "error": row.get("error")})


def summarise(pid: str, rows: list) -> dict:
    """Per-provider metrics for one run."""
    checked = len(rows)
    with_email = sum(1 for r in rows if r["emails"])
    confidences = [e["confidence"] for r in rows for e in r["emails"] if isinstance(e.get("confidence"), (int, float))]
    credits = sum(r.get("credits_used", 0) for r in rows)
    errors = [r["error"] for r in rows if r.get("error")]
    found_first = sorted(rows, key=lambda r: not (r["emails"] or r["phones"]))
    return {
        "provider": pid, "title": PROVIDERS[pid]["title"], "skipped": False,
        "leads_checked": checked, "with_email": with_email, "with_phone": sum(1 for r in rows if r["phones"]),
        "emails_found": sum(len(r["emails"]) for r in rows),
        "avg_confidence": round(sum(confidences) / len(confidences), 1) if confidences else None,
        "credits_used": credits, "errors": len(errors), "error_messages": sorted(set(errors))[:3],
        "stopped": KEY_REJECTED if errors and errors[-1] == KEY_REJECTED else "",
        "coverage_pct": round(100 * with_email / checked, 1) if checked else 0.0,
        "cost_per_contact": round(credits / max(1, with_email), 2),
        "examples": [_example(r) for r in found_first[:EXAMPLE_ROWS]],
    }


def _bounced(lead: dict) -> bool:
    return lead.get("stage") == "disqualified" and any("[bounce]" in (e.get("text") or "") for e in lead.get("timeline", []))


def bounced_counts(leads: list) -> dict:
    """Bounced leads per provider, judged by where the lead's email came from. Grows once sending starts."""
    counts = {pid: 0 for pid in PROVIDERS}
    for lead in filter(_bounced, leads):
        source = lead.get("email_source") or ""
        for pid, p in PROVIDERS.items():
            if any(source == pre or source.startswith(pre + ":") for pre in p["source_prefixes"]):
                counts[pid] += 1
    return counts


def read_runs(store) -> list:
    try:
        return store.read("provider_runs")
    except KeyError:
        return []


def _save_run(store, run: dict) -> None:
    try:
        store.read("provider_runs")
    except KeyError:
        store.write("provider_runs", [])
    store.update("provider_runs", lambda runs: [*runs, run][-MAX_RUNS:])


def present_run(run: dict, bounced: dict) -> dict:
    """A run without its heavy raw rows, with the live bounce count per provider."""
    providers = [m if m.get("skipped") else {**m, "bounced": bounced.get(m["provider"], 0)} for m in run["providers"]]
    return {**{k: v for k, v in run.items() if k not in ("rows", "lead_ids")}, "providers": providers}


def list_runs(store) -> list:
    bounced = bounced_counts(store.read("leads"))
    return [present_run(run, bounced) for run in reversed(read_runs(store))]


def compare(store, provider_ids: list, sample_size: int = DEFAULT_SAMPLE, transports: Optional[dict] = None) -> dict:
    """Run each chosen provider on the SAME sample of leads and save the metrics. Never writes contacts to leads."""
    unknown = [p for p in provider_ids if p not in PROVIDERS]
    if unknown:
        raise ValueError(f"Unknown provider: {', '.join(unknown)}")
    if not provider_ids:
        raise ValueError("Pick at least one provider.")
    sample = pick_sample(store.read("leads"), sample_size)
    if not sample:
        raise ValueError("No open leads with a website domain to test on. Import leads with websites first.")
    saved = store.read("integrations")
    metrics, rows = [], {}
    for pid in dict.fromkeys(provider_ids):
        status = provider_status(saved, pid)
        if not status["configured"]:
            metrics.append({"provider": pid, "title": PROVIDERS[pid]["title"], "skipped": True, "reason": status["reason"]})
            continue
        rows[pid] = run_provider(pid, sample, status["config"], transports or {})
        metrics.append(summarise(pid, rows[pid]))
    run = {"id": uuid.uuid4().hex[:10], "at": now_iso(), "sample_size": len(sample), "lead_ids": [lead["id"] for lead in sample],
           "providers": metrics, "rows": rows, "applied": []}
    _save_run(store, run)
    return present_run(run, bounced_counts(store.read("leads")))


# ---------- filling leads (apply and import) ----------
def contact_changes(lead: dict, domain: str, emails: list, phones: list, tag: str, taken: set, suppressed: set) -> tuple:
    """Changes that fill an empty email/phone, each source tagged '<tag>:<page>'. Returns (changes, notes)."""
    changes, notes = {}, []
    email = None if lead.get("email") else choose_email(emails, domain, suppressed)
    if email:
        value = email["value"]
        changes.update(email=value, email_is_role=is_role_address(value), email_source=f"{tag}:{email['source']}")
        if f"email:{value}" not in taken:
            changes["dedupe_key"] = f"email:{value}"
        notes.append(f"email {value}")
    if not lead.get("phone") and phones:
        changes.update(phone=phones[0]["value"], phone_source=f"{tag}:{phones[0]['source']}")
        notes.append(f"phone {phones[0]['value']}")
    return changes, notes


def apply(store, run_id: str, provider_id: str) -> dict:
    """Fill empty emails/phones from one provider's stored results of a run. Closed leads are left alone."""
    run = next((r for r in read_runs(store) if r["id"] == run_id), None)
    if run is None:
        raise RunNotFound("Comparison run not found.")
    if provider_id not in run.get("rows", {}):
        raise ValueError("That provider has no results in this run.")
    leads = {lead["id"]: lead for lead in store.read("leads")}
    taken = {lead.get("dedupe_key") for lead in leads.values()}
    suppressed = set(store.read("suppressed"))
    patches, filled_emails, filled_phones, skipped = {}, 0, 0, 0
    for row in run["rows"][provider_id]:
        lead = leads.get(row["lead_id"])
        if not lead or lead.get("stage") in NO_FILL_STAGES:
            skipped += 1
            continue
        changes, notes = contact_changes(lead, row["domain"], row["emails"], row["phones"], provider_id, taken, suppressed)
        if not changes:
            continue
        taken = taken | {changes.get("dedupe_key")}
        filled_emails += "email" in changes
        filled_phones += "phone" in changes
        text = f"From the {PROVIDERS[provider_id]['title']} comparison: " + ", ".join(notes)
        patches[lead["id"]] = {"set": changes, "events": [event("enrichment", text)]}
    store.patch_leads(patches)
    store.update("provider_runs", lambda runs: [
        {**r, "applied": sorted({*r.get("applied", []), provider_id})} if r["id"] == run_id else r for r in runs])
    return {"filled_emails": filled_emails, "filled_phones": filled_phones, "skipped_closed": skipped,
            "checked": len(run["rows"][provider_id])}


# ---------- omkarcloud website-email-contact-scraper import ----------
def parse_import_payload(payload) -> list:
    """Accept a JSON string, a list, or an object wrapping a list. Raises ValueError for anything else."""
    data = json.loads(payload) if isinstance(payload, str) else payload
    if isinstance(data, dict):
        wrapped = next((data[k] for k in WRAPPER_KEYS if isinstance(data.get(k), list)), None)
        data = wrapped if wrapped is not None else ([data] if ("domain" in data or "website" in data) else None)
    if not isinstance(data, list):
        raise ValueError("Expected a list of results from the contact scraper.")
    return [item for item in data if isinstance(item, dict)]


def _texts(raw) -> list:
    items = raw if isinstance(raw, list) else [raw]
    return [str(i) if not isinstance(i, dict) else str(i.get("url") or i.get("page") or "") for i in items if i]


def _entries(raw) -> list:
    """Normalise a string, an object, or a list of either into {value, sources, official} entries."""
    items = raw if isinstance(raw, list) else ([raw] if raw else [])
    out = []
    for item in items:
        if isinstance(item, str):
            out.append({"value": item.strip(), "sources": [], "official": False})
        elif isinstance(item, dict):
            value = next((str(item[k]).strip() for k in VALUE_KEYS if isinstance(item.get(k), (str, int)) and item[k]), "")
            sources = next((_texts(item[k]) for k in SOURCE_KEYS if item.get(k)), [])
            out.append({"value": value, "sources": [s for s in sources if s], "official": bool(item.get("is_likely_official"))})
    return [e for e in out if e["value"]]


def _contacts(raw, normalise, domain: str) -> list:
    """Normalised contacts, the site's 'likely official' ones first, each with the page it was found on."""
    out, seen = [], set()
    for entry in sorted(_entries(raw), key=lambda e: not e["official"]):
        value = normalise(entry["value"].removeprefix("mailto:").removeprefix("tel:"))
        if value and value not in seen:
            seen.add(value)
            out.append({"value": value, "source": entry["sources"][0] if entry["sources"] else domain, "official": entry["official"]})
    return out


def _social_sources(item: dict) -> dict:
    nested = item.get("socials") or item.get("social_links") or item.get("profiles") or {}
    if isinstance(nested, list):
        nested = {str(n.get("platform") or n.get("type") or n.get("name")): n for n in nested if isinstance(n, dict)}
    return {**{k: item[k] for k in SOCIAL_PLATFORMS if item.get(k)}, **(nested if isinstance(nested, dict) else {})}


def _socials(item: dict) -> dict:
    out = {}
    for platform, raw in _social_sources(item).items():
        entries = sorted(_entries(raw), key=lambda e: not e["official"])
        if entries and platform and platform != "None":
            out[platform.lower()] = entries[0]["value"]
    return out


def _names(raw) -> list:
    items = raw if isinstance(raw, list) else ([raw] if raw else [])
    return [str(i.get("name") if isinstance(i, dict) else i) for i in items if i]


def _technologies(raw) -> list:
    out = []
    for tech in raw if isinstance(raw, list) else []:
        if isinstance(tech, str) and tech:
            out.append({"name": tech, "versions": [], "categories": []})
        elif isinstance(tech, dict) and tech.get("name"):
            out.append({"name": str(tech["name"]), "versions": _names(tech.get("versions") or tech.get("version")),
                        "categories": _names(tech.get("categories"))})
    return out


def parse_scraper_item(item: dict) -> dict:
    domain = company_domain(str(item.get("domain") or item.get("website") or item.get("url") or "")) or ""
    return {"domain": domain, "title": str(item.get("title") or ""),
            "emails": _contacts(item.get("emails"), normalise_email, domain),
            "phones": _contacts(item.get("phones") or item.get("phone_numbers"), normalise_phone, domain),
            "socials": _socials(item), "technologies": _technologies(item.get("technologies")),
            "error": str(item.get("error") or "")}


def _by_domain(items: list) -> dict:
    """One result per domain; a result with contacts beats an empty or failed one."""
    out = {}
    for item in items:
        current = out.get(item["domain"])
        if item["domain"] and (current is None or not (current["emails"] or current["phones"])):
            out[item["domain"]] = item
    return out


def _import_patch(lead: dict, found: dict, taken: set, suppressed: set) -> tuple:
    """Patch for one matched lead: socials and technologies always, contacts only for open leads."""
    changes = {"contact_scraper_at": now_iso()}
    notes = []
    if lead.get("stage") not in NO_FILL_STAGES:
        changes, notes = contact_changes(lead, found["domain"], found["emails"], found["phones"], "contact_scraper", taken, suppressed)
        changes = {**changes, "contact_scraper_at": now_iso()}
    if found["socials"]:
        changes["company_socials"] = {**(lead.get("company_socials") or {}), **found["socials"]}
        notes.append(f"{len(found['socials'])} social links")
    if found["technologies"]:
        changes["technologies"] = found["technologies"]
        notes.append(f"{len(found['technologies'])} technologies")
    events = [event("enrichment", "Contact scraper import: " + ", ".join(notes))] if notes else []
    return {"set": changes, "events": events}


def import_contact_scraper_json(store, payload) -> dict:
    """Match the scraper's results to leads by domain and fill gaps, keeping the source page of every value."""
    found = _by_domain([parse_scraper_item(i) for i in parse_import_payload(payload)])
    if not found:
        raise ValueError("No results with a domain in that file.")
    leads = store.read("leads")
    taken = {lead.get("dedupe_key") for lead in leads}
    suppressed = set(store.read("suppressed"))
    patches, matched_domains, filled_emails, filled_phones = {}, set(), 0, 0
    for lead in leads:
        domain = company_domain(lead.get("domain") or lead.get("website") or "")
        if domain not in found:
            continue
        matched_domains.add(domain)
        patch = _import_patch(lead, found[domain], taken, suppressed)
        taken = taken | {patch["set"].get("dedupe_key")}
        filled_emails += "email" in patch["set"]
        filled_phones += "phone" in patch["set"]
        patches[lead["id"]] = patch
    store.patch_leads(patches)
    return {"results": len(found), "matched": len(patches), "filled_emails": filled_emails, "filled_phones": filled_phones,
            "unmatched_domains": sorted(set(found) - matched_domains),
            "errors": sum(1 for f in found.values() if f["error"] and not (f["emails"] or f["phones"]))}
