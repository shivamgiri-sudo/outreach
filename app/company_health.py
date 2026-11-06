"""Company health report from free public data (no paid APIs, no keys).

Sources, each optional (a failed source is reported as "not available", never guessed):
  - the company's own website (through the SSRF-guarded, robots-respecting fetcher): reachable, HTTPS, careers page,
    social profiles, copyright year, tools seen on the page;
  - public DNS: email provider (MX), SPF and DMARC records;
  - RDAP (rdap.org, the free successor of WHOIS): domain registration date -> age;
  - Wikidata (free, open): founding year, employee count, headquarters, industry — only when the entity's official
    website matches the company's domain, so a namesake is never used;
  - Google News RSS (free): recent headlines, with growth / risk words counted;
  - this app's own data: open roles and buying signals already on the lead.
The score is explainable: every point comes with a reason. It is a guide for prioritising outreach, not a credit rating.
"""
import json
import re
import time
from datetime import datetime, timezone
from html import unescape
from typing import Callable, Optional
from urllib.parse import quote
from xml.etree import ElementTree

from .hygiene import business_domain

CACHE_HOURS = 24 * 7
MAX_CACHED = 500
MIN_REFRESH_SECONDS = 3600
UA = "MCN-Outreach/1.0 (company health; public data)"
GROWTH_WORDS = re.compile(r"\b(funding|raises?|raised|series [a-e]|expan\w+|hiring|hires|launch\w*|new office|acquires|acquisition|partnership|growth|ipo|record revenue|profit)\b", re.I)
RISK_WORDS = re.compile(r"\b(layoffs?|lays off|laid off|shut ?down|closes|closure|insolven\w+|bankrupt\w*|fraud|raid|probe|penalty|fined|loss widens|default|strike)\b", re.I)
SOCIAL = {"linkedin": r"linkedin\.com/company/[\w%-]+", "twitter": r"(?:twitter|x)\.com/[\w]+", "facebook": r"facebook\.com/[\w.]+",
          "instagram": r"instagram\.com/[\w.]+", "youtube": r"youtube\.com/(?:@|c/|channel/)[\w-]+"}
TOOLS = {"WordPress": r"wp-content|wp-includes", "Shopify": r"cdn\.shopify\.com", "HubSpot": r"js\.hs-scripts\.com|hubspot",
         "Google Analytics": r"googletagmanager\.com|google-analytics\.com", "Zoho": r"zoho\.(?:com|in)/", "Salesforce": r"salesforce|pardot",
         "Freshworks": r"freshworks|freshdesk|freshchat", "Intercom": r"intercom\.io|widget\.intercom", "Darwinbox": r"darwinbox",
         "Keka": r"keka\.com", "Wix": r"wix\.com|wixstatic", "React/Next.js": r"__next|_next/static"}
MX_PROVIDERS = [("Google Workspace", r"google|googlemail"), ("Microsoft 365", r"outlook|protection\.outlook|microsoft"),
                ("Zoho Mail", r"zoho"), ("Rediffmail Pro", r"rediff"), ("GoDaddy", r"secureserver"), ("Hostinger", r"hostinger"),
                ("Amazon SES/WorkMail", r"amazonaws|awsapps")]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ default network helpers (all replaceable in tests)
def http_get(url: str, accept: str = "application/json") -> str:
    from .channels.netguard import guarded_request
    resp = guarded_request(url, "GET", {"User-Agent": UA, "Accept": accept}, timeout=12, max_bytes=1_500_000, max_redirects=3)
    if resp.status >= 400:
        raise OSError(f"HTTP {resp.status}")
    return resp.body.decode("utf-8", errors="replace")


def page_get(url: str) -> str:
    from .scraper import fetch_page
    return fetch_page(url)


def dns_txt(name: str) -> list:
    import dns.resolver
    try:
        return [b"".join(r.strings).decode("utf-8", "replace") for r in dns.resolver.resolve(name, "TXT", lifetime=6)]
    except Exception:  # NXDOMAIN, no answer, timeout: no record
        return []


def dns_mx(name: str) -> list:
    import dns.resolver
    try:
        return sorted(str(r.exchange).rstrip(".").lower() for r in dns.resolver.resolve(name, "MX", lifetime=6))
    except Exception:
        return []


# ------------------------------------------------------------------ sources
def website(domain: str, get_page: Callable) -> dict:
    out = {"ok": False, "url": f"https://{domain}/"}
    started = time.monotonic()
    html = ""
    for url in (f"https://{domain}/", f"https://www.{domain}/"):
        try:
            html = get_page(url)
            out.update(ok=True, url=url)
            break
        except Exception as exc:  # unreachable, robots, blocked
            out["error"] = str(exc)[:120]
    if not out["ok"]:
        return out
    out.pop("error", None)
    out["seconds"] = round(time.monotonic() - started, 1)
    text = html[:600_000]
    title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{0,300})', text, re.I)
    out["title"] = unescape(re.sub(r"\s+", " ", title.group(1)).strip())[:160] if title else ""
    out["description"] = unescape(desc.group(1)).strip() if desc else ""
    out["careers_page"] = bool(re.search(r'href=["\'][^"\']*(career|jobs|join-us|work-with-us|hiring)[^"\']*["\']', text, re.I))
    out["social"] = {k: "https://" + m.group(0) for k, rx in SOCIAL.items() if (m := re.search(rx, text, re.I))}
    years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(20\d\d)", text, re.I)]
    out["copyright_year"] = max(years) if years else None
    out["tools"] = sorted(k for k, rx in TOOLS.items() if re.search(rx, text, re.I))
    return out


def email_setup(domain: str, mx: Callable, txt: Callable) -> dict:
    hosts = mx(domain)
    provider = next((name for name, rx in MX_PROVIDERS if any(re.search(rx, h) for h in hosts)), "own / other" if hosts else "")
    spf = next((t for t in txt(domain) if t.lower().startswith("v=spf1")), "")
    dmarc = next((t for t in txt(f"_dmarc.{domain}") if t.lower().startswith("v=dmarc1")), "")
    policy = (re.search(r"\bp=(\w+)", dmarc, re.I) or [None, ""])[1].lower() if dmarc else ""
    return {"mx": hosts[:4], "provider": provider, "spf": bool(spf), "dmarc": bool(dmarc), "dmarc_policy": policy}


def domain_age(domain: str, get: Callable) -> dict:
    try:
        data = json.loads(get(f"https://rdap.org/domain/{quote(domain)}"))
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}
    reg = next((e.get("eventDate") for e in data.get("events", []) if e.get("eventAction") == "registration"), None)
    if not reg:
        return {"ok": False, "error": "no registration date published"}
    when = datetime.fromisoformat(reg.replace("Z", "+00:00"))
    return {"ok": True, "registered": when.date().isoformat(), "years": round((now_utc() - when).days / 365.25, 1)}


def wikidata(company: str, domain: str, get: Callable) -> dict:
    """Facts from Wikidata, only for an entity whose official website is on the same domain."""
    if not company:
        return {"ok": False, "error": "no company name"}
    try:
        found = json.loads(get("https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&language=en&type=item&limit=5&search="
                               + quote(company[:80])))
        ids = [r["id"] for r in found.get("search", [])][:5]
        if not ids:
            return {"ok": False, "error": "not on Wikidata"}
        ents = json.loads(get("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&props=claims|labels|descriptions&languages=en&ids="
                              + "|".join(ids))).get("entities", {})
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}

    def values(ent, prop):
        return [c.get("mainsnak", {}).get("datavalue", {}).get("value") for c in ent.get("claims", {}).get(prop, [])]
    for eid in ids:
        ent = ents.get(eid) or {}
        sites = [v for v in values(ent, "P856") if isinstance(v, str)]
        if not any(business_domain(s) == domain for s in sites):
            continue
        inception = next((v.get("time", "")[1:5] for v in values(ent, "P571") if isinstance(v, dict)), "")
        employees = next((v.get("amount", "").lstrip("+") for v in values(ent, "P1128") if isinstance(v, dict)), "")
        return {"ok": True, "id": eid, "url": f"https://www.wikidata.org/wiki/{eid}",
                "label": (ent.get("labels", {}).get("en") or {}).get("value", ""),
                "description": (ent.get("descriptions", {}).get("en") or {}).get("value", ""),
                "founded": inception, "employees": employees}
    return {"ok": False, "error": "no Wikidata entry with this website"}


def news(company: str, get: Callable, limit: int = 8) -> dict:
    if not company:
        return {"ok": False, "error": "no company name"}
    q = quote(f'"{company[:80]}" when:180d')
    try:
        raw = get(f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en")
        if re.search(r"<!(DOCTYPE|ENTITY)", raw, re.I):
            raise ValueError("feed declares a DOCTYPE/ENTITY; refused")       # no entity expansion, ever
        root = ElementTree.fromstring(raw)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        if company.split()[0].lower() not in title.lower():
            continue                                  # keep headlines that actually name the company
        items.append({"title": title[:200], "url": (it.findtext("link") or "")[:500], "date": (it.findtext("pubDate") or "")[:40],
                      "source": (it.findtext("source") or "")[:80],
                      "signal": "growth" if GROWTH_WORDS.search(title) else "risk" if RISK_WORDS.search(title) else ""})
    return {"ok": True, "count": len(items), "growth": sum(i["signal"] == "growth" for i in items),
            "risk": sum(i["signal"] == "risk" for i in items), "items": items[:limit]}


# ------------------------------------------------------------------ score
def score(report: dict, lead: dict) -> tuple:
    points, reasons = 0, []

    def add(n, why):
        nonlocal points
        points += n
        reasons.append({"points": n, "why": why})
    web = report["website"]
    if web.get("ok"):
        add(10, "Website is up")
        if web.get("careers_page"):
            add(5, "Has a careers / jobs page")
        if web.get("copyright_year") and web["copyright_year"] >= now_utc().year - 1:
            add(3, f"Website updated recently (© {web['copyright_year']})")
        if len(web.get("social", {})) >= 2:
            add(2, "Active on social media (" + ", ".join(sorted(web["social"])) + ")")
    else:
        add(0, "Website could not be opened")
    mail = report["email"]
    if mail.get("provider") in ("Google Workspace", "Microsoft 365", "Zoho Mail"):
        add(8, f"Pays for business email ({mail['provider']})")
    elif mail.get("mx"):
        add(4, "Has its own email server")
    if mail.get("spf") and mail.get("dmarc"):
        add(4, "Email security set up (SPF + DMARC)")
    age = report["domain_age"]
    if age.get("ok"):
        yrs = age["years"]
        add(15 if yrs >= 10 else 11 if yrs >= 5 else 7 if yrs >= 2 else 3, f"Domain {yrs} years old (since {age['registered']})")
    wiki = report["wikidata"]
    try:
        staff = int(float(wiki.get("employees") or 0))
    except ValueError:
        staff = 0
    if staff >= 1000:
        add(15, f"Large employer (~{staff:,} people)")
    elif staff >= 100:
        add(8, f"Mid-size employer (~{staff:,} people)")
    if wiki.get("ok"):
        add(5, "Listed on Wikidata" + (f", founded {wiki['founded']}" if wiki.get("founded") else "")
            + (f", ~{wiki['employees']} employees" if wiki.get("employees") else ""))
    nw = report["news"]
    if nw.get("ok") and nw["count"]:
        add(min(10, 4 + 2 * nw["growth"]), f"In the news: {nw['count']} headline(s) in 6 months, {nw['growth']} about growth")
        if nw["risk"]:
            add(-min(15, 5 * nw["risk"]), f"{nw['risk']} risk headline(s) (layoffs, closure, penalties…) — read before reaching out")
    roles = int(lead.get("open_roles") or 0)
    if roles:
        add(min(20, 5 + roles), f"{roles} open role(s) known — actively hiring")
    if lead.get("signals"):
        add(5, "Has buying signals on record")
    value = max(0, min(100, points))
    band = "strong" if value >= 65 else "steady" if value >= 40 else "weak / unknown"
    return value, band, reasons


def build(lead: dict, get: Callable = http_get, get_page: Callable = page_get, mx: Callable = dns_mx, txt: Callable = dns_txt) -> dict:
    domain = lead.get("domain") or business_domain(lead.get("website")) or business_domain(lead.get("email"))
    if not domain:
        raise ValueError("This lead has no company website or business email domain to look up.")
    company = re.sub(r"\s*\((test|demo)\)\s*$", "", str(lead.get("company") or "").strip(), flags=re.I)
    report = {"domain": domain, "company": company, "built_at": now_utc().isoformat(timespec="seconds"),
              "website": website(domain, get_page), "email": email_setup(domain, mx, txt), "domain_age": domain_age(domain, get),
              "wikidata": wikidata(company, domain, get), "news": news(company, get)}
    report["score"], report["band"], report["reasons"] = score(report, lead)
    report["sources"] = ["company website", "public DNS", "RDAP (rdap.org)", "Wikidata", "Google News RSS", "this app's lead data"]
    return report


def cached(store, domain: str) -> Optional[dict]:
    row = (store.read("settings").get("company_health") or {}).get(domain)
    return row


def refresh(store, lead: dict, **sources) -> dict:
    domain = lead.get("domain") or business_domain(lead.get("website")) or business_domain(lead.get("email"))
    if not domain:
        raise ValueError("This lead has no company website or business email domain to look up.")
    old = cached(store, domain)
    if old and (now_utc() - datetime.fromisoformat(old["built_at"])).total_seconds() < MIN_REFRESH_SECONDS:
        return {**old, "from_cache": True}
    report = build(lead, **sources)
    def keep(s):
        rows = {k: v for k, v in (s.get("company_health") or {}).items() if k != domain}
        newest = dict(sorted(rows.items(), key=lambda kv: kv[1].get("built_at", ""))[-(MAX_CACHED - 1):])
        return {**s, "company_health": {**newest, domain: report}}
    store.update("settings", keep)
    return report
