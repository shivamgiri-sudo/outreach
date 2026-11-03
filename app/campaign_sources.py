"""Where a campaign finds companies and people. Every adapter takes its transport as an argument (tests use fakes).

Only compliant sources: provider APIs the owner connects with their own keys (Apollo, Google Places), the company's
own careers page (schema.org JobPosting, robots.txt respected by the fetcher), and lists the owner imported. Job
boards and LinkedIn pages are never fetched. Each row keeps `source` and `source_url`, so every field's origin is
stored on the lead.

Apollo and Google request/response shapes are coded from their public docs and tested with fakes only; confirm
each with one small live call before relying on it.
"""
import urllib.parse
from datetime import datetime
from typing import Optional

from .apollo import APOLLO_BASE
from .hygiene import business_domain, company_domain, normalise_email, normalise_phone, safe_http_url
from .jobs import is_job_board, job_age_days, parse_job_postings
from .lead_model import to_int

APOLLO_PEOPLE_SEARCH_URL = f"{APOLLO_BASE}/mixed_people/api_search"
APOLLO_COMPANY_SEARCH_URL = f"{APOLLO_BASE}/mixed_companies/search"
APOLLO_JOB_POSTINGS_URL = APOLLO_BASE + "/organizations/{org_id}/job_postings"
APOLLO_PER_PAGE_MAX = 100
APOLLO_LOCKED_PREFIX = "email_not_unlocked"
PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_FIELD_MASK = ("places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,"
                     "places.internationalPhoneNumber,places.websiteUri,places.businessStatus,places.googleMapsUri")
PLACES_PAGE_MAX = 20
MAX_QUERIES = 10
MAX_ROLE_TITLES = 2
# Decision makers for an AI screening + interview-booking product.
DEFAULT_PERSON_TITLES = ["HR Head", "Head of HR", "HR Manager", "Talent Acquisition", "Talent Acquisition Manager",
                         "Recruitment Manager", "Founder", "Co-Founder"]
# Bucket shown to the owner -> Apollo's "min,max" employee range.
SIZE_BUCKETS = {"1-10": "1,10", "11-50": "11,50", "51-200": "51,200", "201-500": "201,500", "501-1000": "501,1000",
                "1001-5000": "1001,5000", "5001+": "5001,1000000"}
# A Places "website" on these hosts is not the company's own site, so it must not become the company domain.
NOT_COMPANY_SITES = ("facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com", "wa.me",
                     "whatsapp.com", "google.com", "business.site", "linktr.ee", "justdial.com", "indiamart.com",
                     "sulekha.com", "blogspot.com")


class SourceError(Exception):
    """A source could not be used; the message is shown to the owner as is."""


class KeyRejected(SourceError):
    """The provider refused the API key; stop calling it this run."""


def _country(value) -> str:
    text = str(value or "").strip()
    return "IN" if text.lower() in ("india", "in", "bharat") else text


def own_site(url) -> tuple:
    """(website, domain) when the link is the company's own http(s) site; ('', '') for social pages, job boards."""
    site = safe_http_url(url)
    domain = business_domain(site) if site else None
    if not domain or is_job_board(domain) or any(domain == d or domain.endswith("." + d) for d in NOT_COMPANY_SITES):
        return "", ""
    return site, domain


def _clean_list(values) -> list:
    return [str(v).strip() for v in values or [] if str(v).strip()]


# ---------- Apollo ----------
def _apollo_headers(key: str) -> dict:
    return {"x-api-key": key, "Accept": "application/json", "Cache-Control": "no-cache"}


def _apollo_filters(target: dict) -> dict:
    """Shared organisation filters. Industries and keywords become keyword tags (Apollo's free-text tag filter)."""
    sizes = [SIZE_BUCKETS[b] for b in target.get("company_size_buckets") or [] if b in SIZE_BUCKETS]
    filters = {"organization_locations": _clean_list(target.get("cities")) or ["India"],
               "organization_num_employees_ranges": sizes,
               "q_organization_keyword_tags": _clean_list(target.get("industries")) + _clean_list(target.get("keywords"))}
    return {k: v for k, v in filters.items() if v}


def map_apollo_person(person: dict) -> dict:
    """One Apollo person -> a raw campaign row. Locked emails (email_not_unlocked@…) and obfuscated last names are dropped."""
    org = person.get("organization") if isinstance(person.get("organization"), dict) else {}
    first = str(person.get("first_name") or "").strip()
    last = str(person.get("last_name") or "").strip()
    name = str(person.get("name") or "").strip() or " ".join(p for p in (first, last) if p)
    email = normalise_email(person.get("email"))
    if email and email.startswith(APOLLO_LOCKED_PREFIX):
        email = None
    website, domain = own_site(org.get("website_url") or (f"https://{org['primary_domain']}" if org.get("primary_domain") else ""))
    return {"company": str(org.get("name") or "").strip(), "contact_name": name, "title": str(person.get("title") or "").strip(),
            "email": email or "", "website": website, "domain": domain,
            "linkedin_url": safe_http_url(person.get("linkedin_url")),
            "city": str(person.get("city") or org.get("city") or "").strip(),
            "country": _country(person.get("country") or org.get("country")),
            "company_size": to_int(org.get("estimated_num_employees")),
            "apollo_person_id": str(person.get("id") or ""), "apollo_org_id": str(org.get("id") or person.get("organization_id") or ""),
            "source": "apollo_search", "source_url": "https://app.apollo.io"}


def apollo_people(target: dict, key: str, post, max_results: int) -> dict:
    """Apollo People API Search for HR heads, talent leads and founders at matching companies."""
    titles = _clean_list(target.get("person_titles")) or DEFAULT_PERSON_TITLES
    per_page = max(1, min(APOLLO_PER_PAGE_MAX, max_results))
    rows, requests, page = [], 0, 1
    while len(rows) < max_results:
        body = {"person_titles": titles, **_apollo_filters(target), "page": page, "per_page": per_page}
        try:
            reply = post(APOLLO_PEOPLE_SEARCH_URL, _apollo_headers(key), body)
        except PermissionError as exc:
            raise KeyRejected("Apollo rejected the API key. Check it under Integrations > Lead data.") from exc
        requests += 1
        people = [p for p in reply.get("people") or [] if isinstance(p, dict)]
        rows += [map_apollo_person(p) for p in people]
        if len(people) < per_page:
            break
        page += 1
    return {"rows": [r for r in rows if r["company"] or r["domain"]][:max_results], "requests": requests}


def map_apollo_org(org: dict) -> dict:
    phone_raw = (org.get("primary_phone") or {}).get("number") if isinstance(org.get("primary_phone"), dict) else None
    phone_raw = phone_raw or org.get("sanitized_phone") or org.get("phone") or ""
    website, domain = own_site(org.get("website_url") or (f"https://{org['primary_domain']}" if org.get("primary_domain") else ""))
    return {"company": str(org.get("name") or "").strip(), "website": website, "domain": domain,
            "phone": normalise_phone(phone_raw) or "", "company_phone": str(phone_raw or ""), "phone_type": "business" if phone_raw else "",
            "city": str(org.get("city") or "").strip(), "country": _country(org.get("country")),
            "company_size": to_int(org.get("estimated_num_employees")), "apollo_org_id": str(org.get("id") or ""),
            "source": "apollo_hiring", "source_url": "https://app.apollo.io"}


def hiring_signals(postings: list, wanted: list, now: datetime) -> dict:
    """Open roles, the matching role titles and the oldest posting age from a list of job postings."""
    titles = [str(p.get("title") or "").strip() for p in postings if isinstance(p, dict) and p.get("title")]
    wanted_l = [w.lower() for w in wanted]
    matching = [t for t in titles if any(w in t.lower() for w in wanted_l)] or titles
    ages = [job_age_days(str(p.get("posted_at") or p.get("date_posted") or ""), now) for p in postings if isinstance(p, dict)]
    return {"open_roles": len(titles), "role": " and ".join(list(dict.fromkeys(matching))[:MAX_ROLE_TITLES]),
            "max_job_age_days": max(ages, default=0)}


def apollo_hiring(target: dict, key: str, post, get, max_results: int, now: datetime) -> dict:
    """Companies with open jobs for the roles they hire for (Organization Search job-title filter), then each
    company's job postings for open roles and job age. Uses one request per company for the postings."""
    roles = _clean_list(target.get("roles_hiring"))
    if not roles:
        raise SourceError("Add at least one role they hire for (for example 'customer support') to find hiring companies.")
    body = {"q_organization_job_titles": roles, **_apollo_filters(target), "page": 1,
            "per_page": max(1, min(APOLLO_PER_PAGE_MAX, max_results))}
    try:
        reply = post(APOLLO_COMPANY_SEARCH_URL, _apollo_headers(key), body)
    except PermissionError as exc:
        raise KeyRejected("Apollo rejected the API key. Check it under Integrations > Lead data.") from exc
    orgs = [o for o in (reply.get("organizations") or []) + (reply.get("accounts") or []) if isinstance(o, dict)][:max_results]
    rows, requests, failed, refused = [], 1, 0, False
    for org in orgs:
        row = map_apollo_org(org)
        if not (row["company"] or row["domain"]):
            continue
        if row["apollo_org_id"] and refused:
            failed += 1
        elif row["apollo_org_id"]:
            try:
                requests += 1
                listed = get(APOLLO_JOB_POSTINGS_URL.format(org_id=urllib.parse.quote(row["apollo_org_id"], safe="")), _apollo_headers(key))
                postings = listed.get("organization_job_postings") or listed.get("job_postings") or []
                if postings:
                    row.update(hiring_signals(postings, roles, now))
            except PermissionError:         # job postings not in the plan: keep the companies already paid for
                refused, failed = True, failed + 1
            except (OSError, ValueError):
                failed += 1
        rows.append(row)
    return {"rows": rows, "requests": requests, "failed_lookups": failed}


# ---------- careers pages ----------
def careers_postings(urls: list, pasted: list, fetch) -> dict:
    """JobPosting entries from each company's own careers page (fetched or pasted). Returns pages, postings and errors."""
    pages, errors = [], []
    for url in urls:
        host = company_domain(url)
        if is_job_board(host):
            errors.append(f"{url}: job boards are not read. Add the company's own careers page instead.")
            continue
        try:
            pages.append((url, fetch(url)))
        except PermissionError as exc:
            errors.append(f"{url}: not read ({exc}).")
        except (OSError, ValueError) as exc:
            errors.append(f"{url}: could not read it ({exc}). Paste the page's HTML instead.")
    for item in pasted:
        url, html = str(item.get("url") or "").strip(), str(item.get("html") or "")
        if url and is_job_board(company_domain(url)):
            errors.append(f"{url}: job board pages are not used. Paste the company's own careers page instead.")
            continue
        if html.strip():
            pages.append((url, html))
        else:
            errors.append(f"{url or 'Pasted page'}: no job listings found in the pasted HTML (no JobPosting data).")
    found = []
    for url, html in pages:
        postings = parse_job_postings(html, url)
        if not postings:
            errors.append(f"{url or 'Pasted page'}: no job listings found (the page has no JobPosting data).")
        found += [(p, url) for p in postings]
    return {"postings": found, "errors": errors, "pages": len(pages)}


# ---------- Google Places ----------
def map_place(place: dict, city: str) -> dict:
    phone_raw = place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber") or ""
    website, domain = own_site(place.get("websiteUri"))
    place_id = str(place.get("id") or "")
    maps_url = safe_http_url(place.get("googleMapsUri")) or (f"https://www.google.com/maps/place/?q=place_id:{place_id}" if place_id else "")
    return {"company": str((place.get("displayName") or {}).get("text") or "").strip(), "website": website, "domain": domain,
            "phone": normalise_phone(phone_raw) or "", "company_phone": str(phone_raw), "phone_type": "business" if phone_raw else "",
            "address": str(place.get("formattedAddress") or "")[:300], "city": city, "country": "IN",
            "google_place_id": place_id, "source": "google_places", "source_url": maps_url}


def places_queries(target: dict) -> list:
    kinds = _clean_list(target.get("industries")) or _clean_list(target.get("keywords")) or ["companies"]
    cities = _clean_list(target.get("cities")) or ["India"]
    return [(f"{kind} in {city}", city if city != "India" else "") for kind in kinds for city in cities][:MAX_QUERIES]


def _places_key_problem(exc: Exception) -> bool:
    text = str(exc)
    return "API_KEY_INVALID" in text or "API key not valid" in text


def google_places(target: dict, key: str, post, max_results: int) -> dict:
    """Places Text Search (New): '<industry> in <city>' -> businesses with website and office phone."""
    rows, requests = [], 0
    headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": PLACES_FIELD_MASK}
    for query, city in places_queries(target):
        if len(rows) >= max_results:
            break
        body = {"textQuery": query, "pageSize": min(PLACES_PAGE_MAX, max_results - len(rows)), "regionCode": "IN", "languageCode": "en"}
        try:
            reply = post(PLACES_SEARCH_URL, headers, body)
        except PermissionError as exc:
            raise KeyRejected("Google rejected the Places API key (or the Places API is not enabled for it).") from exc
        except OSError as exc:
            if _places_key_problem(exc):
                raise KeyRejected("Google says the Places API key is not valid.") from exc
            raise
        requests += 1
        for place in reply.get("places") or []:
            if isinstance(place, dict) and place.get("businessStatus") != "CLOSED_PERMANENTLY":
                rows.append(map_place(place, city))
    return {"rows": [r for r in rows if r["company"]][:max_results], "requests": requests}


def check_places(entry: dict, post) -> dict:
    """Setup test: one search asking for place IDs only (the smallest field mask)."""
    key = (entry.get("values") or {}).get("api_key")
    if entry.get("mode") != "api" or not key:
        return {"ok": False, "message": "Add a Places API key first."}
    try:
        post(PLACES_SEARCH_URL, {"X-Goog-Api-Key": key, "X-Goog-FieldMask": "places.id"},
             {"textQuery": "software company in Pune", "pageSize": 1})
    except PermissionError:
        return {"ok": False, "message": "Google rejected the key, or Places API (New) is not enabled for it."}
    except (OSError, ValueError) as exc:
        if _places_key_problem(exc):
            return {"ok": False, "message": "Google says the key is not valid."}
        return {"ok": False, "message": f"Could not reach Google Places: {exc}"}
    return {"ok": True, "message": "Google accepted the key. The test asked for place IDs only (see Google's pricing page)."}


# ---------- email verification ----------
ZEROBOUNCE_URL = "https://api.zerobounce.net/v2/validate"
NEVERBOUNCE_URL = "https://api.neverbounce.com/v4/single/check"
VERIFIER_BAD = {"invalid", "spamtrap", "abuse", "do_not_mail", "disposable"}
VERIFIER_RISKY = {"catch-all", "catchall", "unknown", "accept_all"}


def verifier_config(saved_integrations: dict) -> Optional[dict]:
    """The connected email verifier ({provider, api_key}) when it is one we can call, else None."""
    entry = saved_integrations.get("email_verifier") or {}
    values = entry.get("values") or {}
    provider = str(values.get("provider") or "").strip().lower().replace(" ", "")
    if entry.get("mode") != "api" or not values.get("api_key"):
        return None
    return {"provider": provider, "api_key": values["api_key"], "supported": provider in ("zerobounce", "neverbounce")}


def verify_email(email: str, config: dict, get) -> str:
    """valid / invalid / risky from ZeroBounce or NeverBounce. Raises KeyRejected or OSError."""
    if config["provider"] == "zerobounce":
        reply = get(f"{ZEROBOUNCE_URL}?{urllib.parse.urlencode({'api_key': config['api_key'], 'email': email, 'ip_address': ''})}", {})
        if reply.get("error"):
            raise KeyRejected(f"ZeroBounce: {str(reply['error'])[:120]}")
        status = str(reply.get("status") or "").lower()
    else:
        reply = get(f"{NEVERBOUNCE_URL}?{urllib.parse.urlencode({'key': config['api_key'], 'email': email})}", {})
        if reply.get("status") in ("auth_failure", "general_failure", "throttle_triggered"):
            raise KeyRejected(f"NeverBounce: {str(reply.get('message') or reply['status'])[:120]}")
        status = str(reply.get("result") or "").lower()
    if not status:                      # outage (NeverBounce temp_unavail) or empty reply: no verdict on the address
        raise OSError(f"{config['provider']}: no result ({str(reply.get('message') or reply.get('status') or 'empty reply')[:120]})")
    if status == "valid":
        return "valid"
    return "invalid" if status in VERIFIER_BAD else "risky"
