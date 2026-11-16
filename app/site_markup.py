"""Company facts a website publishes about itself as page markup: schema.org Organization / LocalBusiness JSON-LD and the
og: / twitter: meta tags of its home page. Pure parsing (no network): app/hiring_signal.py fetches the page once and
hands the same HTML to this module and to the job readers (app/job_discover.py, app/jobs.py).

facts(html, url) -> {name, logo_url, phone, address, city, company_linkedin_url, social: {x, facebook, instagram, youtube}}
(only what the page states; empty values left out). apply_facts() fills ONLY EMPTY lead fields and records
provenance[field] = "site-markup", the way find & enrich records where each value came from.
"""
import json
import re
from html import unescape
from typing import Optional
from urllib.parse import urljoin, urlparse

from .hygiene import safe_http_url
from .jobs import JSONLD_RE, _walk
from .lead_model import _linkedin, lead_phone

SOURCE = "site-markup"
ORG_TYPES = {"organization", "corporation", "localbusiness", "ngo", "educationalorganization", "professionalservice",
             "store", "medicalorganization", "governmentorganization", "onlinebusiness", "financialservice", "employmentagency"}
SOCIAL_HOSTS = (("x", ("twitter.com", "x.com")), ("facebook", ("facebook.com", "fb.com")), ("instagram", ("instagram.com",)),
                ("youtube", ("youtube.com",)))
_META = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR = re.compile(r'([a-zA-Z:_-]+)\s*=\s*("([^"]*)"|\'([^\']*)\')')
MAX_TEXT = 200


def _types(node: dict) -> set:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return {str(k).lower() for k in kinds if isinstance(k, str)}


def _is_org(node: dict) -> bool:
    kinds = _types(node)
    return bool(kinds & ORG_TYPES) or any(k.endswith(("business", "organization")) for k in kinds)


def _text(value) -> str:
    if isinstance(value, list):
        value = next((v for v in value if isinstance(v, (str, dict))), "")
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl") or value.get("name") or value.get("@id") or ""
    return " ".join(unescape(str(value or "")).split())[:MAX_TEXT]


def jsonld_nodes(html: str) -> list:
    """Every JSON-LD object on the page (lists and @graph opened); a block that is not valid JSON is skipped."""
    out = []
    for block in JSONLD_RE.findall(html or ""):
        try:
            data = json.loads(block, strict=False)
        except ValueError:
            continue
        out.extend(n for n in _walk(data) if isinstance(n, dict))
    return out


def meta_tags(html: str) -> dict:
    """{property-or-name (lower case): content} of the page's <meta> tags (first one wins)."""
    out = {}
    for tag in _META.findall(html or ""):
        attrs = {m.group(1).lower(): unescape(m.group(3) if m.group(3) is not None else m.group(4)) for m in _ATTR.finditer(tag)}
        key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").lower()
        if key and attrs.get("content") and key not in out:
            out[key] = attrs["content"].strip()
    return out


def _address(value) -> tuple:
    """(one-line address, city) from a PostalAddress (or a plain string)."""
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, str):
        return _text(value), ""
    if not isinstance(value, dict):
        return "", ""
    country = value.get("addressCountry")
    country = country.get("name", "") if isinstance(country, dict) else country
    parts = [value.get("streetAddress"), value.get("addressLocality"), value.get("addressRegion"), value.get("postalCode"), country]
    return _text(", ".join(str(p).strip() for p in parts if isinstance(p, str) and p.strip())), _text(value.get("addressLocality"))


def _social(links: list) -> tuple:
    """(LinkedIn company link, {x, facebook, instagram, youtube}) from sameAs links."""
    linkedin, social = "", {}
    for link in links:
        url = safe_http_url(link if "//" in str(link) else "")
        host = (urlparse(url).hostname or "").lower().removeprefix("www.").removeprefix("m.")
        if not url:
            continue
        if not linkedin and _linkedin(url, "company"):
            linkedin = _linkedin(url, "company")
            continue
        for key, hosts in SOCIAL_HOSTS:
            if key not in social and any(host == h or host.endswith("." + h) for h in hosts):
                social[key] = url.split("?")[0]
    return linkedin, social


def facts(html: str, url: str) -> dict:
    org = next((n for n in jsonld_nodes(html) if _is_org(n)), {})
    meta = meta_tags(html)
    same = org.get("sameAs")
    links = [s for s in (same if isinstance(same, list) else [same]) if isinstance(s, str)]
    handle = meta.get("twitter:site", "").lstrip("@")
    if handle and re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle):
        links.append(f"https://x.com/{handle}")
    linkedin, social = _social(links)
    address, city = _address(org.get("address"))
    logo = _text(org.get("logo")) or meta.get("og:logo", "")
    logo = safe_http_url(urljoin(url, logo)) if logo else ""
    phone = _text(org.get("telephone")) or meta.get("telephone", "")
    out = {"name": _text(org.get("name") or org.get("legalName")) or _text(meta.get("og:site_name")), "logo_url": logo,
           "phone": phone, "address": address, "city": city, "company_linkedin_url": linkedin, "social": social}
    return {k: v for k, v in out.items() if v}


FACT_FIELDS = ("company", "logo_url", "phone", "address", "city", "company_linkedin_url")


def changes_for(lead: dict, found: dict, erased: Optional[set] = None) -> tuple:
    """(changes, filled field names) that fill ONLY the lead's empty fields from `found`. A phone that was erased
    (a person asked for their data to be removed) is never put back."""
    changes, filled = {}, []
    wanted = {"company": found.get("name"), "logo_url": found.get("logo_url"), "address": found.get("address"),
              "city": found.get("city"), "company_linkedin_url": found.get("company_linkedin_url")}
    for field, value in wanted.items():
        if value and not lead.get(field):
            changes[field] = value[:120] if field == "city" else value
            filled.append(field)
    if found.get("phone") and not lead.get("phone"):
        phone, kind = lead_phone(found["phone"])
        from .privacy import hashes_for
        if phone and not (hashes_for(None, [phone]) & (erased or set())):
            changes.update(phone=phone, phone_type=kind)
            filled.append("phone")
    social = dict(lead.get("social") or {})
    added = [k for k, v in (found.get("social") or {}).items() if v and not social.get(k)]
    if added:
        changes["social"] = {**social, **{k: found["social"][k] for k in added}}
        filled += [f"social.{k}" for k in added]
    if filled:
        changes["provenance"] = {**(lead.get("provenance") or {}), **{f: SOURCE for f in filled}}
    return changes, filled
