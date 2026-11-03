"""Free website-level enrichment for one company domain, through the polite fetcher (robots.txt respected, SSRF guarded,
same host only). It does not change the old crawler in app/enrich.py; it adds:
  * sitemap.xml discovery (https://www.sitemaps.org/protocol.html) of contact / about / team / leadership / careers pages,
  * schema.org JSON-LD Organization (email, telephone, ContactPoint, sameAs), Person and employee/founder entries
    (https://schema.org/Organization, https://schema.org/ContactPoint, https://schema.org/Person),
  * mailto: and tel: links,
  * the company's LinkedIn PAGE link from sameAs (only recorded as text; LinkedIn itself is never fetched).
Every value keeps the page it came from. WHOIS is not used."""
import json
import re
from urllib.parse import urlparse
from xml.etree import ElementTree

from ..hygiene import normalise_email, normalise_phone, safe_http_url
from ..jobs import JSONLD_RE
from ..scraper import extract_contacts
from .base import SourceSpec, person, valid_host

PAGE_WORDS = ("contact", "about", "team", "leadership", "management", "people", "careers", "career", "jobs", "who-we-are", "our-story")
FIXED_PATHS = ("/", "/contact", "/contact-us", "/about", "/about-us", "/team", "/leadership", "/careers")
MAILTO_RE = re.compile(r'href=["\']mailto:([^"\'?>\s]+)', re.I)
TEL_RE = re.compile(r'href=["\']tel:([^"\'>\s]+)', re.I)
MAX_PAGES = 8
MAX_SITEMAPS = 3


def sitemap_urls(ctx, host: str) -> list:
    """Same-host page URLs from /sitemap.xml (and up to two child sitemaps of an index) whose path looks relevant."""
    urls, queue, seen = [], [f"https://{host}/sitemap.xml"], 0
    while queue and seen < MAX_SITEMAPS:
        url = queue.pop(0)
        seen += 1
        try:
            root = ElementTree.fromstring(ctx.fetch(url))
        except (OSError, ValueError, ElementTree.ParseError):
            continue
        for loc in root.iter():
            if not str(loc.tag).endswith("loc") or not loc.text:
                continue
            link = loc.text.strip()
            parsed = urlparse(link)
            if (parsed.hostname or "").lower().removeprefix("www.") != host:
                continue
            if str(root.tag).endswith("sitemapindex"):
                queue.append(link)
            elif any(w in parsed.path.lower() for w in PAGE_WORDS):
                urls.append(link)
    return urls


def _walk(node):
    if isinstance(node, list):
        for n in node:
            yield from _walk(n)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "employee", "founder", "member", "contactPoint", "author"):
            yield from _walk(node.get(key, []))


def _types(node) -> list:
    t = node.get("@type")
    return t if isinstance(t, list) else [t]


def parse_page(html: str, url: str) -> dict:
    """Everything useful on one page: emails, phones, people, contact points and social links, each tied to `url`."""
    found = {"emails": [], "phones": [], "people": [], "socials": [], "company_linkedin": ""}
    generic = extract_contacts(html, url)
    for e in [*MAILTO_RE.findall(html), *generic["emails"]]:
        mail = normalise_email(e.replace("%40", "@"))
        if mail:
            found["emails"].append({"value": mail, "name": "", "title": "", "source": url})
    for t in TEL_RE.findall(html) + generic["phones"]:
        phone = normalise_phone(t.replace("%20", " "))
        if phone:
            found["phones"].append({"value": phone, "source": url})
    for block in JSONLD_RE.findall(html):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for node in _walk(data):
            kinds = _types(node)
            same = node.get("sameAs")
            for link in same if isinstance(same, list) else [same]:
                link = safe_http_url(link)
                if not link:
                    continue
                found["socials"].append(link)
                if "linkedin.com/company/" in link.lower() and not found["company_linkedin"]:
                    found["company_linkedin"] = link          # recorded as text only; never fetched
            mail = normalise_email(node.get("email"))
            phone = normalise_phone(node.get("telephone"))
            if "Person" in kinds and (node.get("name") or mail):
                found["people"].append({"name": str(node.get("name") or ""), "title": str(node.get("jobTitle") or ""), "email": mail or "",
                                        "linkedin_url": next((s for s in (same if isinstance(same, list) else [same]) if "linkedin.com/in/" in str(s)), ""), "source": url})
            elif mail:
                found["emails"].append({"value": mail, "name": "", "title": str(node.get("contactType") or ""), "source": url})
            if phone:
                found["phones"].append({"value": phone, "source": url})
    return found


def crawl(ctx, domain: str, extra_pages=()) -> dict:
    """One company domain -> merged findings. Cached 7 days. Pages: sitemap picks first, then the usual contact paths."""
    host = valid_host(domain)
    if not host or ctx.fetch is None:
        return {"emails": [], "phones": [], "people": [], "socials": [], "company_linkedin": "", "pages": []}

    def run():
        urls = []
        for u in [*extra_pages, *sitemap_urls(ctx, host), *(f"https://{host}{p}" for p in FIXED_PATHS)]:
            if u not in urls and (urlparse(u).hostname or "").lower().removeprefix("www.") == host:
                urls.append(u)
        out = {"emails": [], "phones": [], "people": [], "socials": [], "company_linkedin": "", "pages": []}
        for url in urls[:MAX_PAGES]:
            try:
                html = ctx.fetch(url)
            except (OSError, ValueError):
                continue
            out["pages"].append(url)
            page = parse_page(html, url)
            for key in ("emails", "phones", "people", "socials"):
                out[key] += page[key]
            out["company_linkedin"] = out["company_linkedin"] or page["company_linkedin"]
        out["emails"] = [e for e in {e["value"]: e for e in reversed(out["emails"])}.values() if e["value"].split("@", 1)[1].removeprefix("www.").endswith(host)]
        out["phones"] = list({p["value"]: p for p in reversed(out["phones"])}.values())
        out["socials"] = list(dict.fromkeys(out["socials"]))
        return out
    return ctx.cached(f"site:{host}", run)


def find_people(ctx, domain, company, titles, limit):
    data = crawl(ctx, domain)
    named = [person("website_deep", p["email"], name=p["name"], title=p["title"], linkedin_url=p["linkedin_url"], source_url=p["source"]) for p in data["people"]]
    plain = [person("website_deep", e["value"], title=e["title"], source_url=e["source"]) for e in data["emails"]]
    words = [t.lower() for t in titles or []]
    named.sort(key=lambda p: not any(w in p["title"].lower() for w in words))
    return [p for p in [*named, *plain] if p["email"]][:limit]


def find_email(ctx, first, last, domain, company):
    data = crawl(ctx, domain)
    f, l = first.lower(), last.lower()
    hits = []
    for e in data["emails"]:
        local = e["value"].split("@", 1)[0]
        if f and (f in local) and (not l or l in local or local.startswith(f[0] + l) or local.startswith(f)):
            hits.append(person("website_deep", e["value"], name=f"{first} {last}".strip(), source_url=e["source"]))
    for p in data["people"]:
        if p["email"] and f and f in p["name"].lower() and (not l or l in p["name"].lower()):
            hits.append(person("website_deep", p["email"], name=p["name"], title=p["title"], linkedin_url=p["linkedin_url"], source_url=p["source"]))
    return hits


def test(ctx):
    return {"ok": True, "message": "Built in: reads a company's own public pages and sitemap, honouring robots.txt. Nothing to connect."}


SPEC = SourceSpec(
    "website_deep", "Company website deep read (free)", ("discover_people", "find_email"), "People and email",
    "Reads sitemap.xml, contact/about/team/careers pages, schema.org Organization/ContactPoint/Person data, mailto: and tel: links, and records the company's LinkedIn page link.",
    "https://www.sitemaps.org/protocol.html",
    "Company websites only, through the polite fetcher: robots.txt respected, same host only, at most 8 pages per company, public same-domain addresses only. LinkedIn is never fetched.",
    ["Nothing to set up."], keyless=True, rate_per_min=60, cost_note="Free; at most 8 page fetches per company.",
    free_note="Free.", honest_status="built in; tested with fake pages only", find_people=find_people, find_email=find_email, test=test, needs_domain=True)
